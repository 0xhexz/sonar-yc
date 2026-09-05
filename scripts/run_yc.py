"""Dev helper: run a real YC directory scan (Playwright) — slow path."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import run_scan
from app.slack_notifier import SlackNotifier
from app.store import Store

DB = Path("/tmp/ycradar_yc.db")


async def main():
    if DB.exists():
        DB.unlink()
    store = Store(DB)
    settings = get_settings()
    notifier = SlackNotifier(None, None, "#yc-radar")
    result = await run_scan(settings, store, notifier, only=["yc"])
    print("=== YC SCAN ===")
    print("signals:", result.counts.get("signals"))
    print("alerts:", len(result.alerts), "errors:", result.errors or "none")
    if result.alerts:
        for a in result.alerts[:5]:
            print("  -", a.classification, a.company_name, a.batch)
    store.close()


asyncio.run(main())
