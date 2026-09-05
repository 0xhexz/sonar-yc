"""Dev helper: run a real scan over the fast sources (Speedrun + mock X/LinkedIn)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import run_scan
from app.slack_notifier import SlackNotifier
from app.store import Store

DB = Path("/tmp/ycradar_test.db")


async def main():
    if DB.exists():
        DB.unlink()
    store = Store(DB)
    settings = get_settings()
    notifier = SlackNotifier(None, None, "#yc-radar")  # dry run
    result = await run_scan(settings, store, notifier, only=["speedrun", "x", "linkedin"])
    print("=== SCAN RESULT ===")
    print("counts:", result.counts)
    print("errors:", result.errors or "none")
    print("alerts:", len(result.alerts))
    for a in result.alerts:
        print("  -", a.classification, "|", a.company_name, "|", a.batch, "| src:", a.source, "| link:", a.link[:60])
    store.close()


asyncio.run(main())
