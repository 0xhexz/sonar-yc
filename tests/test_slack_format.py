from datetime import datetime, timezone

from app.slack_notifier import build_blocks, render_text, classification_meta
from app.models import Alert, Founder, EARLY, CONFIRMED, SPEEDRUN


def _alert(cls=EARLY):
    return Alert(
        classification=cls,
        company_name="Acme AI",
        batch="YC S26",
        source="x",
        description="We got into YC S26! Excited to move to SF.",
        founder=Founder(handle="janedoe"),
        link="https://x.com/janedoe/status/123456",
        detected_at=datetime(2026, 8, 28, 13, 14, tzinfo=timezone.utc),
    )


def test_meta_early():
    h, s = classification_meta(EARLY)
    assert "EARLY YC SIGNAL" in h
    assert "not yet officially" in s


def test_blocks_contains_required_fields():
    blocks = build_blocks(_alert())
    texts = []
    for b in blocks:
        if b.get("text", {}).get("type") == "mrkdwn":
            texts.append(b["text"]["text"])
        for f in b.get("fields", []):
            texts.append(f["text"])
    joined = " ".join(texts)
    for expected in ("Acme AI", "YC S26", "janedoe", "x", "Link"):
        assert expected in joined, f"missing {expected}"


def test_render_text_includes_link():
    text = render_text(_alert())
    assert "https://x.com/janedoe/status/123456" in text
    assert "EARLY YC SIGNAL" in text
