"""Dev helper: full end-to-end scan over ALL sources (real YC + Speedrun, mock X/LinkedIn)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import run_scan
from app.slack_notifier import SlackNotifier
from app.store import Store

DB = Path("/tmp/ycradar_full.db")


async def main():
    if DB.exists():
        DB.unlink()
    store = Store(DB)
    settings = get_settings()
    notifier = SlackNotifier(None, None, "#yc-radar")
    # First scan = backfill (silent) for directories; social mocks still alert.
    result = await run_scan(settings, store, notifier)
    print("=== FULL SCAN #1 (backfill) ===")
    print("counts:", result.counts)
    print("errors:", result.errors or "none")
    print("alerts:", len(result.alerts))
    # Second scan = should be all duplicates (0 alerts) -> dedupe proof.
    result2 = await run_scan(settings, store, notifier)
    print("=== FULL SCAN #2 (dedupe) ===")
    print("alerts:", len(result2.alerts), "(expect 0)")
    store.close()


asyncio.run(main())
