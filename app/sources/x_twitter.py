"""X / Twitter source via a third-party data provider.

Read path only. The adapter queries a provider for recent tweets matching each
configured keyword. Supports three provider shapes:

* **TwtAPI** (twtapi.com) — GET + ``X-API-Key`` header + ``q/count/sort=recency``
  (recommended; your ChirpSieve already uses it).
* **Sorsa** — POST ``/v3/search-tweets`` with ``ApiKey`` header.
* **Generic** (twitterapi.io-style) — GET ``path`` with ``query`` params.

If no provider is configured it returns realistic MOCK samples (including the
beknabdik founder who pre-announced YC) so the pipeline can run before any key.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

from ..config import Settings
from ..models import CompanySignal, Founder
from ._provider_base import (
    ProviderSource,
    author_handle,
    extract_items,
    get_json,
    item_id,
    post_json,
    text_of,
)

logger = logging.getLogger("ycradar.x")


def _is_sorsa(base_url: str | None) -> bool:
    return bool(base_url and "sorsa" in base_url.lower())


def _is_twtapi(base_url: str | None) -> bool:
    return bool(base_url and "twtapi" in base_url.lower())


def _is_twitterapi_io(base_url: str | None) -> bool:
    return bool(base_url and "twitterapi.io" in base_url.lower())


class XSource(ProviderSource):
    name = "x"
    base_url_key = "x_provider_base_url"
    api_key_key = "x_provider_api_key"

    # A realistic early-signal example (founder pre-announced on X).
    mock_signals: list[CompanySignal] = [
        CompanySignal(
            source="x",
            name="Unannounced YC Startup",
            description=(
                "big news: i got into Y Combinator. solo founder, on my 4th attempt. "
                "Stay tuned for our launch"
            ),
            founders=[Founder(handle="beknabdik", name="Bek", url="https://x.com/beknabdik")],
            x_url="https://x.com/beknabdik",
            url="https://x.com/beknabdik/status/2061493360150601738",
        )
    ]

    async def fetch(self) -> list[CompanySignal]:
        if not self.ready:
            # No paid provider configured at all — go straight to the free chain.
            from .x_free import fetch_free_x

            try:
                free = await fetch_free_x(self.settings)
            except Exception as exc:  # noqa: BLE001
                logger.error("free-X path failed: %s", exc)
                free = []
            if free:
                return free
            logger.info("no X provider configured and free path empty; mock sample(s)")
            return self._mock()

        out: list[CompanySignal] = []
        if _is_sorsa(self.settings.x_provider_base_url):
            out = await self._fetch_sorsa()
        elif _is_twtapi(self.settings.x_provider_base_url):
            out = await self._fetch_twtapi()
        elif _is_twitterapi_io(self.settings.x_provider_base_url):
            out = await self._fetch_twitterapi_io()
        else:
            out = await self._fetch_generic()

        # Paid provider returned nothing usable (quota exhausted, empty page):
        # degrade to the free discovery chain instead of going silent.
        if not out:
            logger.warning(
                "paid provider %s returned 0 signals — trying free chain",
                self.settings.x_provider_base_url,
            )
            from .x_free import fetch_free_x

            try:
                free = await fetch_free_x(self.settings)
            except Exception as exc:  # noqa: BLE001
                logger.error("free-X path failed: %s", exc)
                free = []
            if free:
                return free
        return out

    async def _fetch_generic(self) -> list[CompanySignal]:

        base = getattr(self.settings, self.base_url_key).rstrip("/")
        path = self.settings.x_provider_search_path
        api_key = getattr(self.settings, self.api_key_key)
        out: list[CompanySignal] = []

        for kw in self.settings.x_keyword_list:
            if not kw:
                continue
            url = urljoin(base + "/", path.lstrip("/"))
            payload = await get_json(
                url,
                api_key,
                timeout=self.settings.http_timeout,
                params={
                    "query": kw,
                    "queryType": "Latest",
                    "lang": self.settings.x_lang,
                    "count": 30,
                },
            )
            out.extend(self._map_items(payload))
        logger.info("X provider returned %d raw signal(s)", len(out))
        return out

    def _map_items(self, payload) -> list[CompanySignal]:
        """Map provider tweet/post items into X signals (shared by both paths)."""
        import re

        relevance = re.compile(r"(y\s?combinator|\bYC\b|speedrun)", re.I)
        out: list[CompanySignal] = []
        for item in extract_items(payload):
            text = text_of(item)
            if not text:
                continue
            if text.strip().startswith("RT "):
                continue  # drop reposts
            # Relevance guard: the preview sample can be truncated; require an
            # actual YC/Speedrun token somewhere in the full text.
            if not relevance.search(text):
                continue
            lang = item.get("lang") or item.get("language") or self.settings.x_lang
            if self.settings.x_lang and lang and str(lang) != self.settings.x_lang:
                continue
            handle = author_handle(item)
            tid = item_id(item)
            x_url = f"https://x.com/{handle}/status/{tid}" if handle and tid else f"https://x.com/{handle}"
            out.append(
                CompanySignal(
                    source="x",
                    name="",  # unknown from a raw tweet; detection matches by founder
                    description=text,
                    founders=[Founder(handle=handle, url=f"https://x.com/{handle}")],
                    x_url=x_url,
                    url=x_url,
                )
            )
        return out

    async def _fetch_sorsa(self) -> list[CompanySignal]:
        """Sorsa API path: POST /v3/search-tweets with an 'ApiKey' header."""
        base = self.settings.x_provider_base_url.rstrip("/")
        key = self.settings.x_provider_api_key
        out: list[CompanySignal] = []
        for kw in self.settings.x_keyword_list:
            if not kw:
                continue
            # Multi-word keywords must be sent as an exact phrase, otherwise the
            # provider matches the words independently and returns noise.
            phrase = f'"{kw}"' if " " in kw else kw
            query = f"{phrase} lang:{self.settings.x_lang}" if self.settings.x_lang else phrase
            payload = await post_json(
                f"{base}/v3/search-tweets",
                key,
                body={"query": query, "order": "latest", "limit": 30},
                timeout=self.settings.http_timeout,
                auth_header="ApiKey",
            )
            out.extend(self._map_items(payload))
        logger.info("Sorsa returned %d raw signal(s)", len(out))
        return out

    async def _fetch_twitterapi_io(self) -> list[CompanySignal]:
        """twitterapi.io path (docs: GET /twitter/tweet/advanced_search).

        Spec (from docs.twitterapi.io, not tested live to conserve credits):
        * header ``X-API-Key``
        * params: ``query`` (required), ``queryType`` (Latest|Top), ``cursor`` (page 1 = "")
        * response: ``{tweets: [{id, text, author.userName, lang, createdAt, url}],
          has_next_page, next_cursor}``
        Billing is per returned tweet, so we do exactly one page per keyword.
        """
        import httpx

        base = (self.settings.x_provider_base_url or "").rstrip("/")
        key = self.settings.x_provider_api_key
        out: list[CompanySignal] = []
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout,
                                         follow_redirects=True) as client:
                for kw in self.settings.x_keyword_list:
                    if not kw:
                        continue
                    phrase = f'"{kw}"' if " " in kw else kw
                    query = f"{phrase} lang:{self.settings.x_lang}" if self.settings.x_lang else phrase
                    resp = await client.get(
                        f"{base}/twitter/tweet/advanced_search",
                        headers={"X-API-Key": key},
                        params={"query": query, "queryType": "Latest"},
                    )
                    if resp.status_code in (401, 403, 429):
                        logger.warning("twitterapi.io aborted: HTTP %s (key/quota)", resp.status_code)
                        break
                    resp.raise_for_status()
                    data = resp.json()
                    tweets = data.get("tweets") or []
                    items = [
                        {
                            "text": t.get("text") or "",
                            "id": t.get("id") or "",
                            "username": (t.get("author") or {}).get("userName") or "",
                            "lang": t.get("lang") or "",
                            "url": t.get("url") or "",
                        }
                        for t in tweets
                        if isinstance(t, dict)
                    ]
                    out.extend(self._map_items(items))
                    await asyncio.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            logger.error("twitterapi.io fetch failed: %s", exc)
        logger.info("twitterapi.io returned %d raw signal(s)", len(out))
        return out

    async def _fetch_twtapi(self) -> list[CompanySignal]:
        """TwtAPI path: GET {base} with X-API-Key header, params q/count/sort=recency."""
        import httpx

        base = self.settings.x_provider_base_url.rstrip("/")
        key = self.settings.x_provider_api_key
        headers = {"X-API-Key": key}
        out: list[CompanySignal] = []
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout,
                                         follow_redirects=True) as client:
                for kw in self.settings.x_keyword_list:
                    if not kw:
                        continue
                    phrase = f'"{kw}"' if " " in kw else kw
                    query = f"{phrase} lang:{self.settings.x_lang}" if self.settings.x_lang else phrase
                    resp = await client.get(
                        base,
                        headers=headers,
                        params={"q": query, "count": 30, "max_results": 30, "sort": "recency"},
                    )
                    if resp.status_code in (401, 403, 429):
                        logger.warning("TwtAPI aborted: HTTP %s (check key/quota)", resp.status_code)
                        break
                    resp.raise_for_status()
                    items = _twtapi_items(resp.json())
                    out.extend(self._map_items(items))
                await asyncio.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            logger.error("TwtAPI fetch failed: %s", exc)
        logger.info("TwtAPI returned %d raw signal(s)", len(out))
        return out


def _find_screen_name(node, depth: int = 0) -> str:
    """Find the first 'screen_name' string anywhere in a tweet/user node."""
    if node is None or depth > 8:
        return ""
    if isinstance(node, dict):
        sn = node.get("screen_name")
        if isinstance(sn, str) and sn:
            return sn
        for v in node.values():
            r = _find_screen_name(v, depth + 1)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_screen_name(v, depth + 1)
            if r:
                return r
    return ""


def _twtapi_items(payload) -> list[dict]:
    """Recursively pull tweet dicts out of TwtAPI's GraphQL response.

    TwtAPI returns a deep ``search_timeline`` tree (``data.search_by_raw_query
    .search_timeline.timeline.instructions[].entries[].content.itemContent
    .tweet_results.result.legacy``). We walk the entire JSON tree (depth-capped;
    JSON has no cycles) and collect every node that carries a tweet ``legacy`` or
    ``full_text``. Normalises each to ``{text, id, username, lang}`` so the shared
    ``_map_items`` pipeline can consume them directly.
    """
    items: list[dict] = []
    seen: set[str] = set()

    def add_box(text: str, author: str, tid: str, lang: str = ""):
        if not text or not tid:
            return
        key = str(tid)
        if key in seen:
            return
        seen.add(key)
        items.append({"text": text, "id": tid, "username": author, "lang": lang})

    def walk(x, depth: int = 0):
        if depth > 40 or x is None:
            return
        if isinstance(x, dict):
            leg = x.get("legacy")
            if isinstance(leg, dict) and (leg.get("full_text") or leg.get("text")):
                author = _find_screen_name(x)
                add_box(leg.get("full_text") or leg.get("text"), author,
                        x.get("rest_id") or leg.get("id_str") or leg.get("id") or "",
                        leg.get("lang") or "")
            elif x.get("full_text") or x.get("text"):
                author = _find_screen_name(x)
                add_box(x.get("full_text") or x.get("text"), author,
                        x.get("id_str") or x.get("rest_id") or x.get("id") or "",
                        x.get("lang") or "")
            for v in x.values():
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)

    walk(payload)
    return items
