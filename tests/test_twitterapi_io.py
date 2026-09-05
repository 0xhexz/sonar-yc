"""Unit-test twitterapi.io path with a mocked docs-shaped response (no credits burned)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.sources.x_twitter import XSource, _is_twitterapi_io

DOCS_SHAPE = {
    "tweets": [
        {
            "type": "tweet",
            "id": "2061493360150601738",
            "url": "https://x.com/beknabdik/status/2061493360150601738",
            "text": "big news: i got into Y Combinator. solo founder, on my 4th attempt.",
            "lang": "en",
            "author": {"type": "user", "userName": "beknabdik", "name": "Bek", "followers": 357},
        },
        {
            "type": "tweet",
            "id": "999",
            "url": "https://x.com/newsy/status/999",
            "text": "Y Combinator is the USAID of Silicon Valley",
            "lang": "en",
            "author": {"userName": "newsy"},
        },
    ],
    "has_next_page": True,
    "next_cursor": "DAACCgACG",
}


def test_twitterapi_io_detection():
    assert _is_twitterapi_io("https://api.twitterapi.io")
    assert not _is_twitterapi_io("https://api.sorsa.io")
    assert not _is_twitterapi_io("https://www.twtapi.com/api-proxy/api/v1/twitter/Search")


def test_twitterapi_io_docs_shape_mapping(monkeypatch):
    st = Settings(
        x_provider_base_url="https://api.twitterapi.io",
        x_provider_api_key="testkey",
        x_keywords='"got into YC","Y Combinator"',
        x_lang="en",
    )
    src = XSource(st)
    assert src.ready

    calls = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            calls.append(1)
            return DOCS_SHAPE

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            assert "twitter/tweet/advanced_search" in url
            assert headers.get("X-API-Key") == "testkey"
            assert params.get("queryType") == "Latest"
            return FakeResp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", FakeClient)

    sigs = asyncio.run(src.fetch())
    # 2 keywords × 2 tweets = 4 mapped; retweets/relevance filter applies downstream
    assert calls, "fetch never called"
    assert len(sigs) == 4
    s0 = sigs[0]
    assert s0.source == "x"
    assert s0.founders[0].handle == "beknabdik"
    assert "got into Y Combinator" in s0.description
    assert s0.url == "https://x.com/beknabdik/status/2061493360150601738"
