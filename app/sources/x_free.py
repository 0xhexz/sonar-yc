"""Free X discovery path — $0, no key, no quota.

Chain (inspired by Foxy's providers, cross-validated with our docs-driven
twitterapi.io adapter):

1. DISCOVERY — ask a public search engine for indexed X posts matching each
   announcement keyword (``site:x.com <keyword>``). Engines tried in order:
   Serper (if SERPER_API_KEY set; 2,500 free one-off credits) → DuckDuckGo
   HTML → Bing HTML. Each degrades quietly.
2. HYDRATION — X's own public syndication endpoint returns the full tweet
   (text, author, likes, created_at) for any tweet ID, free, forever:
       https://cdn.syndication.twimg.com/tweet-result?id={id}&lang=en&token=a
   Deleted/protected posts return nothing — which doubles as a pre-send check.

Free mode has real but partial recall (engines index X imperfectly). It is the
fallback when every paid provider is unconfigured or exhausted, so the source
NEVER goes silent.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, unquote, parse_qs, urlparse

from ..config import Settings
from ..models import CompanySignal, Founder
from ._provider_base import get_json

logger = logging.getLogger("ycradar.x_free")

STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)", re.I
)
SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
_BLOCK_MARKERS = ("anomaly", "unusual traffic", "captcha", "are you a robot")


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#x27;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _looks_blocked(body: str, status: int) -> bool:
    if status == 202:
        return True
    low = (body or "")[:6000].lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _unwrap(href: str) -> str:
    """Undo DuckDuckGo's click-tracking redirect."""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


async def _serper_search(http, query: str, key: str, limit: int) -> list[str]:
    r = await http.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": 10},
    )
    if r.status_code != 200:
        raise RuntimeError(f"serper HTTP {r.status_code}")
    out = [item.get("link") or "" for item in (r.json().get("organic") or [])]
    return [u for u in out if u][:limit]


async def _duckduckgo_search(http, query: str, limit: int) -> list[str]:
    r = await http.get(
        "https://html.duckduckgo.com/html/",
        params={"q": quote_plus(query)},
    )
    if _looks_blocked(r.text, r.status_code):
        raise RuntimeError("duckduckgo throttled")
    hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', r.text)
    return [_unwrap(h) for h in hrefs][:limit]


async def _bing_search(http, query: str, limit: int) -> list[str]:
    r = await http.get(
        "https://www.bing.com/search",
        params={"q": quote_plus(query), "count": limit},
    )
    if _looks_blocked(r.text, r.status_code):
        raise RuntimeError("bing throttled")
    hrefs = re.findall(r'<h2><a href="(https?://[^"]+)"', r.text)
    return hrefs[:limit]


async def hydrate(http, post_id: str) -> dict | None:
    """Free full-tweet fetch via X's syndication endpoint. None = gone/private."""
    try:
        r = await http.get(
            SYNDICATION,
            params={"id": post_id, "lang": "en", "token": "a"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("hydrate %s failed: %s", post_id, exc)
        return None
    if not data or not data.get("text"):
        return None
    user = data.get("user") or {}
    return {
        "id": str(data.get("id_str") or post_id),
        "text": data.get("text") or "",
        "handle": user.get("screen_name") or "",
        "lang": data.get("lang") or "en",
        "url": f"https://x.com/{user.get('screen_name') or 'i'}/status/{post_id}",
        "likes": int(data.get("favorite_count") or 0),
    }


async def fetch_free_x(settings: Settings) -> list[CompanySignal]:
    """Discovery via search engines + hydration via syndication. $0 total."""
    import httpx

    out: list[CompanySignal] = []
    seen_ids: set[str] = set()
    serper_key = getattr(settings, "serper_api_key", None)
    per_query = 10

    async with httpx.AsyncClient(
        timeout=settings.http_timeout, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
    ) as http:
        for kw in settings.x_keyword_list:
            if not kw:
                continue
            query = f"site:x.com {kw}"
            urls: list[str] = []
            engines: list = []
            if serper_key:
                engines.append(lambda q, l: _serper_search(http, q, serper_key, l))
            engines.append(lambda q, l: _duckduckgo_search(http, q, l))
            engines.append(lambda q, l: _bing_search(http, q, l))

            for engine in engines:
                try:
                    urls = await engine(query, per_query)
                    if urls:
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.info("search engine unavailable (%s); trying next", exc)
            if not urls:
                continue

            for url in urls:
                m = STATUS_RE.search(url)
                if not m:
                    continue
                _, post_id = m.group(1), m.group(2)
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                post = await hydrate(http, post_id)
                if not post:
                    continue  # deleted/protected — pre-send check failed
                handle = post["handle"] or m.group(1)
                text = post["text"]
                relevance = re.compile(r"(y\s?combinator|\bYC\b|speedrun)", re.I)
                if not relevance.search(text):
                    continue
                if settings.x_lang and post.get("lang") and str(post["lang"]) != settings.x_lang:
                    continue
                out.append(
                    CompanySignal(
                        source="x",
                        name="",
                        description=text,
                        founders=[Founder(handle=handle, url=f"https://x.com/{handle}")],
                        x_url=post["url"],
                        url=post["url"],
                    )
                )
                await __import__("asyncio").sleep(0.4)

    logger.info("free-X path returned %d hydrated signal(s)", len(out))
    return out
