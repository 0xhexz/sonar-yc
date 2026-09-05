import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.loop import run_scan
from app.models import CompanySignal, Founder, EARLY, CONFIRMED
from app.slack_notifier import SlackNotifier
from app.store import Store
from app import loop as loop_mod


class FakeSource:
    name = "fake"
    signals = []

    def __init__(self, settings=None):
        self.settings = settings

    @property
    def enabled(self):
        return True

    async def fetch(self):
        return list(self.signals)


def _patch_sources(monkeypatch, mapping):
    # return a FakeSource per source name with the given signals
    def _factory(name, settings):
        src = FakeSource()
        src.signals = mapping.get(name, [])
        return src

    monkeypatch.setattr(loop_mod, "get_source", _factory)


def _settings():
    return Settings(sources_enabled="yc,speedrun,x,linkedin", run_on_start=False)


def _store(tmp_path):
    return Store(tmp_path / "t.db")


async def _scan(s, nm, only, st):
    notifier = SlackNotifier(None, None, "#x")
    return await run_scan(s, st, notifier, only=only)


def test_first_directory_scan_backfills_silently(tmp_path, monkeypatch):
    yc = [CompanySignal(source="yc", name="Acme", slug="acme"),
          CompanySignal(source="yc", name="Bead", slug="bead")]
    _patch_sources(monkeypatch, {"yc": yc})
    st = _store(tmp_path)
    res = asyncio.run(_scan(_settings(), "stub", ["yc"], st))
    assert res.counts["signals"].get("yc") == 2
    assert len(res.alerts) == 0  # backfill, no alerts
    assert st.directory_slugs("yc") == {"acme", "bead"}
    st.close()


def test_new_directory_company_alerts_once(tmp_path, monkeypatch):
    _patch_sources(monkeypatch, {"yc": [CompanySignal(source="yc", name="Bead", slug="bead")]})
    st = _store(tmp_path)
    asyncio.run(_scan(_settings(), "stub", ["yc"], st))  # backfill bead
    # next scan adds a new company
    _patch_sources(monkeypatch, {"yc": [
        CompanySignal(source="yc", name="Bead", slug="bead"),
        CompanySignal(source="yc", name="Acme", slug="acme"),
    ]})
    res = asyncio.run(_scan(_settings(), "stub", ["yc"], st))
    assert len(res.alerts) == 1
    assert res.alerts[0].company_name == "Acme"
    assert res.alerts[0].classification == CONFIRMED
    st.close()


def test_social_early_alert_dedupes(tmp_path, monkeypatch):
    social = [CompanySignal(source="x", name="", founders=[Founder(handle="beknabdik")])]
    _patch_sources(monkeypatch, {"x": social})
    st = _store(tmp_path)
    res1 = asyncio.run(_scan(_settings(), "stub", ["x"], st))
    assert len(res1.alerts) == 1 and res1.alerts[0].classification == EARLY
    res2 = asyncio.run(_scan(_settings(), "stub", ["x"], st))
    assert len(res2.alerts) == 0  # no duplicate
    st.close()


def test_upgrade_pending_to_confirmed(tmp_path, monkeypatch):
    # social EARLY (identity = slugify('Acme') because no founder)
    social = [CompanySignal(source="x", name="Acme")]
    _patch_sources(monkeypatch, {"x": social})
    st = _store(tmp_path)
    r1 = asyncio.run(_scan(_settings(), "stub", ["x"], st))
    assert r1.alerts[0].classification == EARLY
    # dry-run ledger: decided == delivered when no Slack is configured
    assert st.is_seen("acme")
    # now YC directory confirms the same company (slug 'acme')
    _patch_sources(monkeypatch, {"yc": [CompanySignal(source="yc", name="Acme", slug="acme")]})
    r2 = asyncio.run(_scan(_settings(), "stub", ["yc"], st))
    # re-scan of the same identity must NOT re-alert (already reported)
    assert len(r2.alerts) == 0
    assert st.is_pending("acme") is False
    st.close()


def test_source_error_does_not_crash(tmp_path, monkeypatch):
    def boom(name, settings):
        class Bad:
            enabled = True
            async def fetch(self):
                raise RuntimeError("network")
        return Bad()
    monkeypatch.setattr(loop_mod, "get_source", boom)
    st = _store(tmp_path)
    res = asyncio.run(_scan(_settings(), "stub", ["yc"], st))
    assert res is not None
    assert res.counts["signals"].get("yc") == 0
    st.close()
