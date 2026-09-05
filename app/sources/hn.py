"""Hacker News source — free founder-signal feed (no key, no bot wall).

The rival yc-launch-monitor's only real early signal (Mireye, "Launch HN:
Mireye (YC S26)") came from exactly this kind of post. HN is the place founders
legitimately announce YC launches ("Launch HN: X (YC S26)") — the Algolia HN
API is free, stable JSON, no auth.

    GET https://hn.algolia.com/api/v1/search_by_date?query=...&tags=story

We treat "Launch HN ... (YC Sxx)" as a founder announcement signal and let the
shared classifier + directory cross-reference decide EARLY vs CONFIRMED.
"""
from __future__ import annotations

import logging
import re

from ..config import Settings
from ..models import CompanySignal, Founder
from ._provider_base import get_json

logger = logging.getLogger("ycradar.hn")

LAUNCH_RE = re.compile(
    r"launch\s+hn:\s*(?P<name>[^()]{2,60}?)\s*\(\s*YC\s*(?P<batch>[SWFX]\d{2}|[A-Za-z]+\s*\d{4})\s*\)",
    re.I,
)
# Accept "YC W26", "YC Summer 2026", "YC S26" style batches inside the parens.


class HNSource:
    name = "hn"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch(self) -> list[CompanySignal]:
        if not self.settings.hn_enabled:
            return []
        out: list[CompanySignal] = []
        for kw in ("Launch HN YC", "Show HN Y Combinator", "Launch HN Speedrun"):
            try:
                payload = await get_json(
                    "https://hn.algolia.com/api/v1/search_by_date",
                    None,
                    timeout=self.settings.http_timeout,
                    params={"query": kw, "tags": "story", "hitsPerPage": 30},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("HN query %r failed: %s", kw, exc)
                continue
            hits = payload.get("hits") or []
            for h in hits:
                title = h.get("title") or ""
                m = LAUNCH_RE.search(title)
                if not m:
                    continue
                name = (m.group("name") or "").strip()
                batch = (m.group("batch") or "").strip().upper()
                out.append(
                    CompanySignal(
                        source="hn",
                        name=name,
                        description=f"Launch HN: {name} (YC {batch}) — {title}",
                        founders=[
                            Founder(
                                handle=h.get("author") or "",
                                name=h.get("author") or "",
                                url=f"https://news.ycombinator.com/user?id={h.get('author')}",
                            )
                        ],
                        url=f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    )
                )
        logger.info("HN returned %d launch signal(s)", len(out))
        return out
