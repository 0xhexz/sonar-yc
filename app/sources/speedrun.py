"""a16z Speedrun directory source.

Speedrun is a16z's accelerator (NOT YC — see research). Its company
directory is exposed as a clean public REST API:

    GET https://speedrun-api.a16z.com/api/companies/companies/?limit=100&offset=N

Response is Django REST Framework pagination:
    {"count": 258, "next": "...", "previous": null, "results": [...]}

Each result carries name, slug, cohort (batch), description, industries,
location, website, founder_set (with per-founder X/LinkedIn URLs), etc.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx

from ..config import Settings
from ..models import CompanySignal, Founder, slugify
from .base import BaseSource

logger = logging.getLogger("ycradar.speedrun")

BASE_URL = "https://speedrun-api.a16z.com/api/companies/companies/"
PAGE_SIZE = 100


class SpeedrunSource(BaseSource):
    name = "speedrun"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def _parse_founders(self, founder_set: list[dict]) -> list[Founder]:
        out: list[Founder] = []
        for f in founder_set or []:
            first = f.get("first_name", "")
            last = f.get("last_name", "")
            full = f"{first} {last}".strip()
            # founder_set often includes a slug/url; stitch a handle from the
            # linked_in / twitter fields if present.
            handle = f.get("twitter") or f.get("x") or f.get("linkedin")
            out.append(
                Founder(
                    name=full or None,
                    handle=handle or None,
                    url=f.get("url") or f.get("linkedin_url"),
                )
            )
        return out

    def _to_signal(self, r: dict) -> CompanySignal:
        founders = self._parse_founders(r.get("founder_set") or [])
        return CompanySignal(
            source="speedrun",
            name=r.get("name") or r.get("slug") or "Unknown",
            slug=(r.get("slug") or "").strip().lower(),
            batch=r.get("cohort") or None,  # e.g. 'SR005'
            description=r.get("description") or r.get("preamble") or "",
            founders=founders,
            website_url=r.get("website_url") or "",
            x_url=r.get("x_url") or "",
            linkedin_url=r.get("linkedin_url") or "",
            city=r.get("city") or "",
            country=r.get("country") or "",
            industries=r.get("industries") or [],
            url=f"https://speedrun.a16z.com/companies/{r.get('slug', '')}",
        )

    async def fetch(self) -> list[CompanySignal]:
        """Retrieve all companies (paginated) and return them as signals."""
        signals: list[CompanySignal] = []
        url: str | None = f"{BASE_URL}?limit={PAGE_SIZE}&offset=0"
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout,
            follow_redirects=True,
            headers={"User-Agent": "yc-radar-monitor/1.0"},
        ) as client:
            try:
                while url:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    for r in data.get("results", []):
                        signals.append(self._to_signal(r))
                    url = data.get("next")
                    if not url:
                        break
                    parsed = urlparse(url)
                    if parsed.scheme not in ("http", "https"):
                        break
            except Exception as exc:  # noqa: BLE001
                logger.error("speedrun fetch failed: %s", exc)
                return []
        logger.info("speedrun fetched %d companies", len(signals))
        return signals
