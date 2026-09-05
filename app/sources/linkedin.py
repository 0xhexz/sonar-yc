"""LinkedIn source via third-party data access.

LinkedIn has no public API for monitoring *arbitrary* company pages/posts (its
official API only reads your own org and needs Partner approval), and scraping
it directly violates LinkedIn's ToS. The compliant-enough route is a third-party
provider — the default being an **Apify LinkedIn Post Search actor**
(``apimaestro/linkedin-posts-search-scraper-no-cookies``), which searches posts
by keyword and returns author/text/URL/timestamp without needing your session.

The adapter is labelled ToS-aware / best-effort: it depends on a third-party
scraper whose shared session pool is out of our control (the vendor logs
"No available users" when LinkedIn blocks its pool). If no provider is
configured, it falls back to a realistic MOCK signal so the pipeline still runs.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from ..config import Settings
from ..models import CompanySignal, Founder, utcnow
from ._provider_base import ProviderSource, extract_items, get_json, item_id, text_of

logger = logging.getLogger("ycradar.linkedin")


def _is_apify(base_url: str | None) -> bool:
    return bool(base_url and "apify" in base_url.lower())


class LinkedInSource(ProviderSource):
    name = "linkedin"
    base_url_key = "linkedin_provider_base_url"
    api_key_key = "linkedin_provider_api_key"

    mock_signals: list[CompanySignal] = [
        CompanySignal(
            source="linkedin",
            name="Unannounced YC Startup",
            description="Thrilled to share we're building with Y Combinator this summer!",
            founders=[Founder(name="Founder", url="https://www.linkedin.com/in/founder")],
            linkedin_url="https://www.linkedin.com/company/example",
            url="https://www.linkedin.com/posts/example",
        )
    ]

    async def fetch(self) -> list[CompanySignal]:
        if not self.ready:
            logger.info("LinkedIn provider not configured; returning mock signal(s)")
            return self._mock()

        if _is_apify(self.settings.linkedin_provider_base_url):
            return await self._fetch_apify()

        # Generic provider path (documented shape).
        base = self.settings.linkedin_provider_base_url.rstrip("/")
        path = self.settings.linkedin_provider_search_path
        api_key = self.settings.linkedin_provider_api_key
        out: list[CompanySignal] = []
        for kw in self.settings.linkedin_keyword_list:
            if not kw:
                continue
            url = urljoin(base + "/", path.lstrip("/"))
            payload = await get_json(
                url, api_key, timeout=self.settings.http_timeout,
                params={"query": kw, "sort": "date", "count": 30},
            )
            for item in extract_items(payload):
                out.append(self._map_generic(item))
        logger.info("LinkedIn provider returned %d raw signal(s)", len(out))
        return out

    async def _fetch_apify(self) -> list[CompanySignal]:
        """Apify path: run a post-search actor per keyword, pull its dataset.

        Supports two actors with different input shapes:
        * ``apimaestro~linkedin-posts-search-scraper-no-cookies`` — ``keyword`` (string)
        * ``datadoping~linkedin-posts-search-scraper`` — ``keywords`` (array), 4× cheaper
        Both return post dicts with author/text/URL we map via ``_map_apify_item``.
        """
        base = self.settings.linkedin_provider_base_url.rstrip("/")
        token = self.settings.linkedin_provider_api_key
        actor = self.settings.linkedin_provider_actor
        cheap_array_mode = "datadoping" in actor.lower()
        out: list[CompanySignal] = []
        try:
            async with httpx.AsyncClient(timeout=150, follow_redirects=True) as client:
                for kw in self.settings.linkedin_keyword_list:
                    if not kw:
                        continue
                    payload = (
                        {
                            "keywords": [kw],
                            "maxPosts": self.settings.linkedin_max_posts,
                            "sortBy": "date_posted",
                            "maxTotalChargeUsd": 0.05,
                        }
                        if cheap_array_mode
                        else {"keyword": kw, "limit": self.settings.linkedin_max_posts}
                    )
                    run = await client.post(
                        f"{base}/v2/actors/{actor}/runs",
                        params={"token": token, "waitForFinish": 90},
                        json=payload,
                    )
                    run.raise_for_status()
                    rdata = run.json().get("data") or {}
                    if rdata.get("status") != "SUCCEEDED":
                        logger.warning("apify run not succeeded: %s", rdata.get("statusMessage"))
                        continue
                    dsid = rdata.get("defaultDatasetId")
                    if not dsid:
                        continue
                    items_resp = await client.get(
                        f"{base}/v2/datasets/{dsid}/items",
                        params={"token": token, "clean": True},
                    )
                    items_resp.raise_for_status()
                    for it in items_resp.json():
                        out.append(self._map_apify_item(it))
                    await asyncio.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            logger.error("apify linkedin fetch failed: %s", exc)
        logger.info("apify linkedin returned %d raw signal(s)", len(out))
        return out

    def _map_generic(self, item: dict) -> CompanySignal:
        text = text_of(item)
        handle = ""
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        if isinstance(author, dict):
            handle = author.get("username") or author.get("screen_name") or ""
        pid = item_id(item)
        li_url = f"https://www.linkedin.com/posts/{handle}/{pid}" if handle and pid else ""
        return CompanySignal(
            source="linkedin",
            name=item.get("company") or item.get("company_name") or "",
            description=text,
            founders=[Founder(handle=handle, url=f"https://www.linkedin.com/in/{handle}")] if handle else [],
            linkedin_url=item.get("linkedin_url") or li_url,
            url=li_url or item.get("url") or "",
        )

    def _map_apify_item(self, it: dict) -> CompanySignal:
        author = it.get("author") or {}
        name = (author.get("name") if isinstance(author, dict) else None) or ""
        profile_url = (author.get("profile_url") if isinstance(author, dict) else None) or ""
        posted_at = it.get("posted_at") or {}
        ts_iso = posted_at.get("date") if isinstance(posted_at, dict) else None
        try:
            detected = datetime.strptime(ts_iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if ts_iso else None
        except Exception:
            detected = None
        return CompanySignal(
            source="linkedin",
            name=it.get("company_name") or "",
            description=(it.get("text") or it.get("content") or "").strip(),
            founders=[Founder(name=name, url=profile_url)] if name else [],
            linkedin_url=profile_url or "",
            url=it.get("post_url") or "",
            detected_at=detected or utcnow(),
        )
