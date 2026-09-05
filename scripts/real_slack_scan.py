"""REAL Slack delivery proof: run an actual X scan and deliver alerts to #yc-radar."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import run_scan
from app.slack_notifier import SlackNotifier
from app.store import Store


async def main():
    db = Path(__file__).resolve().parents[1] / "data" / "state.db"  # real persistent store
    st = get_settings()
    st = st.model_copy(update={"social_max_alerts_per_scan": 3})  # avoid flooding
    store = Store(db)
    nf = SlackNotifier(st.slack_bot_token, None, "#yc-radar")  # REAL token!
    print("Slack ready:", nf.ready, "| target: #yc-radar")
    r = await run_scan(st, store, nf, only=["x"])
    print(f"scan: {r.counts['signals']} | alerts: {len(r.alerts)} | DELIVERED to Slack: {r.counts['delivered']}")
    for a in r.alerts[:5]:
        h = a.founder.handle if a.founder else "?"
        print(f"  🔥 @{h} | {a.description[:60]}")
    store.close()


asyncio.run(main())
