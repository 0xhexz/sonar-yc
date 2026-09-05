"""Prove dedupe behavior per source type: YC/Speedrun (identical data) vs X (sliding window)."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import run_scan
from app.slack_notifier import SlackNotifier
from app.store import Store


async def main():
    db = Path(tempfile.mkdtemp()) / "t.db"
    st = get_settings()
    store = Store(db)
    nf = SlackNotifier(None, None, "#x")

    # --- YC + Speedrun: two scans on identical data ---
    r1 = await run_scan(st, store, nf, only=["yc", "speedrun"])
    r2 = await run_scan(st, store, nf, only=["yc", "speedrun"])
    print("=== YC + Speedrun (directory) ===")
    print(f"scan1: signals={r1.counts['signals']} alerts={len(r1.alerts)}")
    print(f"scan2: signals={r2.counts['signals']} alerts={len(r2.alerts)}  <- same data, 0 new")

    # --- X: two scans, check duplicate HANDLES between alert sets ---
    rx1 = await run_scan(st, store, nf, only=["x"])
    h1 = {a.founder.handle for a in rx1.alerts if a.founder}
    rx2 = await run_scan(st, store, nf, only=["x"])
    h2 = {a.founder.handle for a in rx2.alerts if a.founder}
    overlap = h1 & h2
    print("\n=== X (social, sliding window) ===")
    print(f"scan1 alerts: {len(rx1.alerts)} (handles: {len(h1)})")
    print(f"scan2 alerts: {len(rx2.alerts)} (handles: {len(h2)})")
    print(f"DUPLICATE handles across scans: {len(overlap)} {overlap if overlap else ''}")
    print("=> scan2-এর alert-গুলো কি সব নতুন founder?",
          "হ্যাঁ ✅" if not overlap else "কিছু রিপিট আছে ❌")
    store.close()


asyncio.run(main())
