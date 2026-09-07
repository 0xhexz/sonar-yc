"""Pond Protocol V1 manifest shape (public, no auth).

IMPORTANT: disable auto-scan before importing the app so TestClient's lifespan
does not kick off a real (network/Playwright) monitor scan.
"""
import os

os.environ.setdefault("RUN_ON_START", "false")
os.environ.setdefault("SOURCES_ENABLED", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _client():
    return TestClient(app)


def test_manifest_shape_and_required_fields():
    with _client() as c:
        r = c.get("/manifest")
        assert r.status_code == 200
        data = r.json()
        assert data["protocol"] == "marketplace-agent"
        assert data["protocol_version"] == "1.0"
        assert data["agent_version"]
        assert data["capabilities"]["sync"] is True
        assert data["capabilities"]["cancellation"] is False
        assert data["limits"]["max_request_bytes"] > 0
        assert "scan_now" in [a["id"] for a in data["actions"]]


def test_manifest_works_without_auth():
    # GET /manifest must succeed with no access key / version header
    with _client() as c:
        assert c.get("/manifest").status_code == 200


def test_health_endpoint():
    with _client() as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_root_endpoint():
    with _client() as c:
        assert c.head("/").status_code == 200
        assert c.get("/").status_code == 200


def test_slack_channel_alias():
    from app.config import Settings

    s = Settings(slack_channel_id="C12345", slack_dm_user_id="U67890")
    assert s.slack_channel == "C12345"
    assert s.slack_dm_user == "U67890"


def test_dashboard_and_trailing_slashes():
    with _client() as c:
        r = c.get("/dashboard")
        assert r.status_code == 200
        assert "X (Twitter)" in r.text
        assert "LinkedIn" in r.text
        assert "Recent Social Signals" in r.text
        assert c.get("/dashboard/").status_code == 200
        assert c.get("/health/").status_code == 200
        assert c.get("/favicon.ico").status_code == 204


def test_cadence_duration_strings():
    import pytest
    from app.config import Settings, format_duration, parse_duration_seconds

    # 1. Parsing duration strings and raw numbers
    assert parse_duration_seconds(8, default_unit="h") == 28800
    assert parse_duration_seconds(15, default_unit="m") == 900
    assert parse_duration_seconds("30m") == 1800
    assert parse_duration_seconds("30 min") == 1800
    assert parse_duration_seconds("0.5h") == 1800
    assert parse_duration_seconds("1h30m") == 5400
    assert parse_duration_seconds("1d") == 86400
    assert parse_duration_seconds("45s") == 45
    with pytest.raises(ValueError):
        parse_duration_seconds("invalid_duration")

    # 2. Formatting durations
    assert format_duration(1800) == "30m"
    assert format_duration(28800) == "8h"
    assert format_duration(86400) == "1d"
    assert format_duration(5400) == "1h30m"
    assert format_duration(45) == "45s"

    # 3. Settings aliases with duration strings
    s = Settings(
        yc_interval="30m",
        speedrun_interval="0.5h",
        x_interval="15m",
        linkedin_interval="1d",
    )
    assert s.yc_interval_seconds == 1800
    assert s.speedrun_interval_seconds == 1800
    assert s.x_interval_seconds == 900
    assert s.linkedin_interval_seconds == 86400
    assert s.yc_cadence_label == "30m"
    assert s.speedrun_cadence_label == "30m"
    assert s.x_cadence_label == "15m"
    assert s.linkedin_cadence_label == "1d"

    # 4. Backward compatibility with legacy inputs
    s_legacy = Settings(
        yc_interval_hours=2,
        speedrun_interval_hours=4,
        x_interval_minutes=15,
        linkedin_interval_hours=48,
    )
    assert s_legacy.yc_interval_hours == 2
    assert s_legacy.speedrun_interval_hours == 4
    assert s_legacy.x_interval_minutes == 15
    assert s_legacy.linkedin_interval_hours == 48
    assert s_legacy.yc_interval_seconds == 7200
    assert s_legacy.speedrun_interval_seconds == 14400
    assert s_legacy.x_interval_seconds == 900
    assert s_legacy.linkedin_interval_seconds == 172800
    assert s_legacy.yc_cadence_label == "2h"
    assert s_legacy.speedrun_cadence_label == "4h"
    assert s_legacy.x_cadence_label == "15m"
    assert s_legacy.linkedin_cadence_label == "2d"



