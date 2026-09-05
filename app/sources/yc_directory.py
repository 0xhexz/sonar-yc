"""YC Directory source.

Two paths, same output (CompanySignal list):

1. **Algolia path (primary, stable):** YC\'s own website is powered by a public
   Algolia search index (``YCCompany_By_Launch_Date_production``). The per-page
   API key is published in the page HTML (``window.AlgoliaOpts``). We fetch it,
   query the index sorted by launch date, and get clean structured records —
   no headless browser, no DOM scraping, nothing to break on a redesign.

2. **Playwright path (fallback):** if Algolia fails, render the SPA in headless
   Chrome and parse the cards. Kept from v1; selectors are isolated.

Ethics: the index is YC\'s own public search infrastructure (same one the
website calls); we poll a small number of queries per cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone as _tz
from urllib.parse import urlencode

import httpx

from ..config import Settings
from ..models import CompanySignal
from .base import BaseSource

logger = logging.getLogger("ycradar.ycdir")

YCDIR = "https://www.ycombinator.com/companies"
PAGE_DELAY = 2.0  # politeness for the Playwright path
MAX_PAGES_PER_BATCH = 4


_BATCH_RE = re.compile(r"(SPRING|SUMMER|FALL|WINTER)\s+\d{4}", re.I)

_EXTRACT_JS = r"""
() => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/companies/"]'))
    .filter(a => /\/companies\/[a-z0-9-]+$/.test(a.getAttribute('href') || ''));
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    const slug = (a.getAttribute('href') || '').split('/').pop();
    if (!slug || seen.has(slug)) continue;
    seen.add(slug);
    const q = s => { const n = a.querySelector(s); return n ? (n.innerText || '').trim() : ''; };
    const name = q('[class*="_coName_"]');
    const loc = q('[class*="_coLocation_"]');
    const pills = Array.from(a.querySelectorAll('[class*="pill"]'))
      .map(x => (x.innerText || '').trim()).filter(Boolean);
    const text = (a.innerText || '');
    const bm = text.match(/(SPRING|SUMMER|FALL|WINTER)\s+\d{4}/);
    const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
    const tagline = lines.find(l =>
      !l.endsWith(loc || '__none__') &&
      !/^(SPRING|SUMMER|FALL|WINTER)\s+\d{4}$/i.test(l) &&
      !pills.includes(l)
    ) || '';
    out.push({slug, name, location: loc, batch: bm ? bm[0].toUpperCase() : '', tagline, industries: pills});
  }
  return out;
}
"""


class YC_DirectorySource(BaseSource):
    name = "yc"

    # ---- Algolia primary path -------------------------------------------
    async def _get_algolia(self) -> tuple[str, str] | None:
        """Pull (app_id, api_key) from the YC companies page HTML."""
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout,
                                         follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 yc-radar/1.0"}) as c:
                r = await c.get(YCDIR)
                r.raise_for_status()
                m = re.search(r"window\.AlgoliaOpts\s*=\s*({[^<]+})", r.text)
                if not m:
                    return None
                opts = json.loads(m.group(1))
                if opts.get("app") and opts.get("key"):
                    return opts["app"], opts["key"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Algolia key fetch failed: %s", exc)
        return None

    async def _algolia_query(self, app: str, key: str, body: dict) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout) as c:
                r = await c.post(
                    f"https://{app}-dsn.algolia.net/1/indexes/YCCompany_By_Launch_Date_production/query",
                    json=body,
                    headers={"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Algolia query failed: %s", exc)
        return None

    async def _fetch_algolia(self) -> list[CompanySignal]:
        got = await self._get_algolia()
        if not got:
            return []
        app, key = got
        per_page = 50
        page = 0
        signals: list[CompanySignal] = []
        while page < 3:  # newest 150 is plenty for incremental diffing
            data = await self._algolia_query(app, key, {
                "hitsPerPage": per_page, "page": page,
            })
            if not data:
                break
            hits = data.get("hits") or []
            for h in hits:
                signals.append(self._algolia_to_signal(h))
            nb = data.get("nbPages") or 0
            page += 1
            if page >= nb or not hits:
                break
            await asyncio.sleep(0.3)
        logger.info("yc via Algolia: %d companies", len(signals))
        return signals

    @staticmethod
    def _algolia_to_signal(h: dict) -> CompanySignal:
        batch_raw = (h.get("batch") or "").strip()
        launched = h.get("launched_at") or 0
        ts = time.gmtime(launched) if launched else None
        detected = datetime.fromtimestamp(launched, _tz.utc) if launched else None
        slug = h.get("slug") or ""
        name = h.get("name") or slug or "Unknown"
        return CompanySignal(
            source="yc",
            name=name,
            slug=slug.lower(),
            batch=batch_raw,
            description=h.get("one_liner") or "",
            city=h.get("all_locations") or "",
            industries=[h.get("industry")] if h.get("industry") else (h.get("industries") or []),
            url=f"https://www.ycombinator.com/companies/{slug}",
            detected_at=detected or datetime.now(_tz.utc),
        )

    # ---- Playwright fallback --------------------------------------------
    async def _maybe_goto(self, page, url: str, tries: int = 3) -> None:
        last = None
        for _ in range(tries):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_selector('a[href*="/companies/"]', timeout=60000)
                await page.wait_for_timeout(800)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                await asyncio.sleep(2)
        if last:
            raise last

    async def _current_batches(self, page) -> list[str]:
        batches = [b.strip() for b in (self.settings.yc_batches or "").split(",") if b.strip()]
        if batches:
            return batches
        current = await page.evaluate(
            r"""() => {
              const el = document.querySelector('#app') || document.querySelector('[data-page]');
              if (el && el.dataset && (el.dataset.page || el.getAttribute('data-page'))) {
                try {
                  const p = JSON.parse(el.dataset.page || el.getAttribute('data-page'));
                  if (p && p.props && p.props.currentBatch) return [p.props.currentBatch];
                } catch (e) {}
              }
              return [];
            }"""
        )
        if current:
            return current
        labels = await page.evaluate(
            r"""() => {
              const out = [];
              document.querySelectorAll('label').forEach(l => {
                const t = (l.innerText || '').trim();
                if (/(SPRING|SUMMER|FALL|WINTER)\s+\d{4}/i.test(t)) {
                  out.push(t.replace(/\s*\d+\s*$/, '').trim());
                }
              });
              return out;
            }"""
        )
        seen: list[str] = []
        for l in labels:
            if l and l not in seen:
                seen.append(l)
            if len(seen) >= 2:
                break
        return seen

    async def fetch(self) -> list[CompanySignal]:
        # Primary: Algolia (no browser).
        try:
            sigs = await self._fetch_algolia()
            if sigs:
                return sigs
            logger.warning("Algolia path returned 0; falling back to Playwright")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Algolia path crashed (%s); falling back to Playwright", exc)

        # Fallback: Playwright render (v1 behavior).
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright not installed; YC source disabled")
            return []
        signals: list[CompanySignal] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="chrome")
                page = await browser.new_page(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) yc-radar/1.0"
                )
                try:
                    await self._maybe_goto(page, YCDIR)
                    batches = await self._current_batches(page)
                    if not batches:
                        batches = ["Spring 2026"]
                    for batch in batches:
                        try:
                            signals.extend(await self._fetch_batch(page, batch))
                            await asyncio.sleep(PAGE_DELAY)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("yc batch %s failed: %s", batch, exc)
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("yc directory fetch failed: %s", exc)
            return []
        logger.info("yc directory (playwright) fetched %d companies", len(signals))
        return signals

    async def _fetch_batch(self, page, batch: str) -> list[CompanySignal]:
        signals: list[CompanySignal] = []
        for page_no in range(1, MAX_PAGES_PER_BATCH + 1):
            url = f"{YCDIR}?{urlencode({'batch': batch})}&page={page_no}"
            await self._maybe_goto(page, url)
            cards = await page.evaluate(_EXTRACT_JS)
            if not cards:
                break
            for c in cards:
                signals.append(
                    CompanySignal(
                        source="yc",
                        name=c.get("name") or c.get("slug") or "Unknown",
                        slug=(c.get("slug") or "").strip().lower(),
                        batch=self._norm_batch(c.get("batch") or batch),
                        description=c.get("tagline") or "",
                        city=c.get("location") or "",
                        industries=c.get("industries") or [],
                        url=f"https://www.ycombinator.com/companies/{c.get('slug', '')}",
                    )
                )
            if len(cards) < 10:
                break
        return signals

    @staticmethod
    def _norm_batch(raw: str) -> str:
        m = _BATCH_RE.search(raw or "")
        if not m:
            return raw or ""
        season = m.group(1).title()
        year = m.group(0).split()[-1]
        return f"{season} {year}"
