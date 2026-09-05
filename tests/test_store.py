from app.store import Store
from app.models import CompanySignal, Founder
from pathlib import Path
import tempfile


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "t.db")


def test_seen_dedup(tmp_path):
    st = _store(tmp_path)
    assert st.is_seen("x:bek") is False
    st.mark_seen("x:bek")
    st.mark_seen("x:bek")  # idempotent
    assert st.is_seen("x:bek") is True
    st.close()


def test_pending(tmp_path):
    st = _store(tmp_path)
    st.add_pending("bek", {"source": "x", "handle": "bek"})
    assert st.is_pending("bek") is True
    assert len(st.list_pending()) == 1
    st.remove_pending("bek")
    assert st.is_pending("bek") is False
    st.close()


def test_kv(tmp_path):
    st = _store(tmp_path)
    st.set_state("last_scan_at", "2026-09-02T00:00:00")
    # number should round-trip through json
    st.set_state("n", 5)
    assert st.get_state("n") == 5
    st.close()


def test_directory_set_dedups(tmp_path):
    st = _store(tmp_path)
    payload = [
        {"slug": "bead-ai", "name": "Bead AI"},
        {"slug": "bead-ai", "name": "Bead AI"},  # duplicate
        {"slug": "amdahl", "name": "Amdahl"},
    ]
    saved = st.save_directory_set("speedrun", payload)
    assert saved == 2
    assert st.directory_slugs("speedrun") == {"bead-ai", "amdahl"}
    st.close()


def test_store_reuse_across_scans(tmp_path):
    st = _store(tmp_path)
    st.mark_seen("speedrun:bead-ai")
    # new store on same db must still see it (persistence)
    st.close()
    st2 = Store(tmp_path / "t.db")
    assert st2.is_seen("speedrun:bead-ai") is True
    st2.close()
