"""Shared helper for third-party data-provider sources (X & LinkedIn).

A provider is just a base URL + API key. The adapter maps whatever JSON the
provider returns into our normalised ``CompanySignal``. To keep the bot usable
before any key is configured (and to power the demo), each provider source
falls back to realistic MOCK signals when its key/env is missing.

Response parsing is deliberately tolerant: it looks for tweet/post arrays under
several common keys (``data``, ``tweets``, ``results``, ``data.data``) and
per-item fields we care about (text, author username/handle, id, timestamp).
"""
from __future__ import annotations

import logging

import httpx

from ..config import Settings
from ..models import CompanySignal, Founder
from .base import BaseSource

logger = logging.getLogger("ycradar.provider")


def deep_find(obj, keys: tuple[str, ...]):
    """Return first list found by walking common keys (for tweet/post arrays)."""
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, list):
                return v
        for k in ("data", "body", "payload", "result"):
            if k in obj:
                r = deep_find(obj[k], keys)
                if r is not None:
                    return r
    elif isinstance(obj, list):
        for item in obj:
            r = deep_find(item, keys)
            if r is not None:
                return r
    return None


async def get_json(
    url: str,
    api_key: str | None,
    timeout: int = 30,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict | list | None:
    """GET a provider endpoint with the API key; return parsed JSON."""
    h = {"User-Agent": "yc-radar-monitor/1.0"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
        h["X-API-Key"] = api_key
    if headers:
        h.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=h)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("provider GET failed for %s: %s", url, exc)
        return None


async def post_json(
    url: str,
    api_key: str | None,
    body: dict | None = None,
    timeout: int = 30,
    headers: dict | None = None,
    auth_header: str | None = None,
) -> dict | list | None:
    """POST JSON to a provider endpoint (e.g. Sorsa's /v3/search-tweets)."""
    h = {"User-Agent": "yc-radar-monitor/1.0", "Content-Type": "application/json"}
    if api_key:
        # Sorsa uses an 'ApiKey' header; default auth_header can override.
        h[auth_header or "ApiKey"] = api_key
        h.setdefault("Authorization", f"Bearer {api_key}")
        h.setdefault("X-API-Key", api_key)
    if headers:
        h.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(url, json=body or {}, headers=h)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("provider POST failed for %s: %s", url, exc)
        return None


def extract_items(payload) -> list[dict]:
    """Return a list of post/tweet dicts from an unknown provider response."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    arr = deep_find(payload, ("tweets", "posts", "results", "items", "data"))
    return arr if isinstance(arr, list) else []


def text_of(item: dict) -> str:
    for k in ("text", "full_text", "content", "tweet", "description", "post"):
        v = item.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if t:
                return str(t)
    return ""


def author_handle(item: dict) -> str:
    for key in ("author", "user"):
        u = item.get(key)
        if isinstance(u, dict):
            for h in ("username", "screen_name", "handle", "name"):
                if u.get(h):
                    return str(u[h]).replace("@", "")
    for h in ("username", "screen_name", "handle", "user_handle", "author_handle"):
        if item.get(h):
            return str(item[h]).replace("@", "")
    return ""


def item_id(item: dict) -> str:
    for k in ("id", "tweet_id", "post_id", "status_id"):
        if item.get(k):
            return str(item[k])
    return ""


class ProviderSource(BaseSource):
    """Base for X/LinkedIn provider sources with a mock fallback."""

    name = "provider"
    base_url_key = ""
    api_key_key = ""
    mock_signals: list[CompanySignal] = []

    @property
    def ready(self) -> bool:
        base = getattr(self.settings, self.base_url_key, None)
        key = getattr(self.settings, self.api_key_key, None)
        return bool(base and key)

    @property
    def enabled(self) -> bool:
        # Always "enabled" via mock so the bot runs pre-key; the loop drops
        # provider results only if not ready AND mock disabled.
        return True

    def _mock(self) -> list[CompanySignal]:
        return list(self.mock_signals)

    async def fetch(self) -> list[CompanySignal]:
        # Subclasses override the real fetch; this default returns mock.
        return self._mock()
