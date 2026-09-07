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



