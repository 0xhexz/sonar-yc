from app.classifier import (
    clean_json,
    parse_batch,
    pre_filter,
    classify_batch,
    FOUNDER_ANNOUNCEMENT,
    THIRD_PARTY,
)
from app.config import Settings


def test_pre_filter_drops_short_and_spam():
    assert pre_filter("ok") is True                 # too short
    assert pre_filter("") is True
    assert pre_filter("Check my link in bio!") is True   # spam pattern
    assert pre_filter("We got into YC S26! excited to move to SF") is False  # real signal


def test_clean_json_strips_fences():
    raw = '```json\n[{"id": "1", "label": "founder_announcement"}]\n```'
    out = clean_json(raw)
    assert isinstance(out, list) and out[0]["label"] == "founder_announcement"


def test_clean_json_finds_array_in_prose():
    raw = 'Here you go: [{"id": "2", "label": "third_party", "confidence": 0.9}] hope it helps!'
    out = clean_json(raw)
    assert out[0]["id"] == "2"


def test_parse_batch_maps_and_ignores_unknown_ids():
    text = '[{"id": "1", "label": "founder_announcement", "company_name": "Acme", "batch": "S26", "confidence": 0.9, "reasoning": "founder says got in"}, {"id": "999", "label": "x", "confidence": 0.1}]'
    res = parse_batch(text, ["1", "2"])
    assert "1" in res
    assert res["1"].label == FOUNDER_ANNOUNCEMENT
    assert res["1"].company_name == "Acme"
    assert "999" not in res  # unknown id ignored


def test_classify_batch_returns_empty_without_key(monkeypatch):
    s = Settings(llm_api_key=None, classify_enabled=True)
    async def boom(*a, **k):
        raise AssertionError("must not call LLM")
    monkeypatch.setattr("app.classifier._chat", boom)
    import asyncio
    out = asyncio.run(classify_batch(s, [{"id": "0", "text": "hello world there"}]))
    assert out == {}


def test_classify_batch_happy_path(monkeypatch):
    s = Settings(llm_api_key="k", llm_base_url="https://x/v1", llm_model="m", classify_enabled=True)

    async def fake_chat(settings, messages):
        return json_ok()
    import app.classifier as cl
    monkeypatch.setattr(cl, "_chat", fake_chat)
    import asyncio
    items = [{"id": "0", "text": "i got into YC S26! excited", "author": "janedoe"}]
    out = asyncio.run(classify_batch(s, items))
    assert "0" in out
    assert out["0"].is_founder_announcement


def json_ok():
    return '[{"id": "0", "label": "founder_announcement", "company_name": "Acme", "batch": "S26", "confidence": 0.95, "reasoning": "founder says got in"}]'


def test_classify_batch_llm_error_graceful(monkeypatch):
    s = Settings(llm_api_key="k", classify_enabled=True)

    async def fake_chat(settings, messages):
        raise RuntimeError("boom")
    import app.classifier as cl
    monkeypatch.setattr(cl, "_chat", fake_chat)
    import asyncio
    out = asyncio.run(classify_batch(s, [{"id": "0", "text": "i got into YC S26!"}]))
    assert out == {}
