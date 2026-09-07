"""10-Minute Deep Research Test Runner for SONAR.

Runs 10 cycles (1 cycle every 60 seconds) testing live TwtAPI (X) and Apify (LinkedIn).
Records comprehensive telemetry, raw signals, filter results, and alerts to
`data/research_10m_run.json` for in-depth analysis.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.store import open_store
from app.slack_notifier import SlackNotifier
from app.loop import run_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("research_runner")

OUTPUT_FILE = Path("data/research_10m_run.json")


async def main():
    settings = get_settings()
    store = open_store()
    notifier = SlackNotifier(
        bot_token=settings.slack_bot_token,
        webhook_url=settings.slack_webhook_url,
        channel=settings.slack_channel,
        dm_user=settings.slack_dm_user,
    )

    print("=================================================================")
    print("🚀 STARTING 10-MINUTE RESEARCH TEST RUNNER")
    print(f"X Provider: TwtAPI ({settings.x_provider_base_url})")
    print(f"LinkedIn Provider: Apify ({settings.linkedin_provider_actor})")
    print(f"Keywords X: {settings.x_keyword_list}")
    print(f"Keywords LinkedIn: {settings.linkedin_keyword_list}")
    print("Cadence: 1 scan every 60 seconds for 10 minutes (10 cycles)")
    print("=================================================================\n")

    telemetry = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total_cycles": 10,
        "cycle_interval_sec": 60,
        "rounds": [],
        "all_raw_signals": {"x": [], "linkedin": []},
        "all_alerts": [],
    }

    start_time = time.time()
    for cycle in range(1, 11):
        cycle_start = time.time()
        print(f"\n--- [Cycle {cycle}/10] Scan started at {datetime.now().strftime('%H:%M:%S')} ---")

        # Run full scan cycle (single call fetches X & LinkedIn without duplicates)
        t0 = time.time()
        try:
            scan_res = await run_scan(settings, store, notifier, only=["x", "linkedin"])
            scan_duration = round(time.time() - t0, 2)
            raw_x = scan_res.fetched.get("x", [])
            raw_li = scan_res.fetched.get("linkedin", [])
            new_alerts = scan_res.alerts
            errors = scan_res.errors
        except Exception as exc:
            scan_duration = round(time.time() - t0, 2)
            raw_x = []
            raw_li = []
            new_alerts = []
            errors = {"scan_exception": str(exc)}
            logger.error("Scan exception: %s", exc)

        print(f"  Fetch completed in {scan_duration}s")
        print(f"  Raw X signals fetched: {len(raw_x)}")
        print(f"  Raw LinkedIn signals fetched: {len(raw_li)}")
        print(f"  New alerts emitted: {len(new_alerts)}")
        for a in new_alerts:
            print(f"    ⭐ [{a.classification}] {a.company_name} ({a.batch or 'No Batch'}) | {a.source.upper()} | {a.link}")
        if errors:
            print(f"  Errors: {errors}")

        # Record cycle metrics
        round_data = {
            "cycle": cycle,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scan_duration_sec": scan_duration,
            "raw_x_count": len(raw_x),
            "raw_li_count": len(raw_li),
            "alerts_count": len(new_alerts),
            "alerts": [
                {
                    "classification": a.classification,
                    "company": a.company_name,
                    "batch": a.batch,
                    "source": a.source,
                    "founder": a.founder.handle if a.founder else "",
                    "link": a.link,
                    "description": a.description[:250] if a.description else "",
                }
                for a in new_alerts
            ],
            "errors": errors,
        }
        telemetry["rounds"].append(round_data)

        # Save raw signals for deeper research
        for s in raw_x:
            telemetry["all_raw_signals"]["x"].append({
                "source": "x",
                "handle": s.founders[0].handle if s.founders else "",
                "url": s.url,
                "text": s.description,
                "cycle": cycle,
            })
        for s in raw_li:
            telemetry["all_raw_signals"]["linkedin"].append({
                "source": "linkedin",
                "name": s.name,
                "author": s.founders[0].name if s.founders else "",
                "url": s.url,
                "text": s.description,
                "cycle": cycle,
            })
        telemetry["all_alerts"].extend(round_data["alerts"])

        # Flush telemetry to file after each cycle
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(telemetry, indent=2))

        # Sleep remaining time until next 60-second boundary if not the last cycle
        elapsed_in_cycle = time.time() - cycle_start
        sleep_time = max(0.0, 60.0 - elapsed_in_cycle)
        if cycle < 10:
            print(f"  Cycle {cycle} finished in {round(elapsed_in_cycle, 1)}s. Waiting {round(sleep_time, 1)}s until next round...")
            await asyncio.sleep(sleep_time)

    total_elapsed = round(time.time() - start_time, 1)
    telemetry["finished_at"] = datetime.now(timezone.utc).isoformat()
    telemetry["total_elapsed_sec"] = total_elapsed
    OUTPUT_FILE.write_text(json.dumps(telemetry, indent=2))

    print("\n=================================================================")
    print(f"🏁 10-MINUTE TEST RUNNER COMPLETE! Total elapsed: {total_elapsed}s")
    print(f"Total raw X signals collected: {len(telemetry['all_raw_signals']['x'])}")
    print(f"Total raw LinkedIn signals collected: {len(telemetry['all_raw_signals']['linkedin'])}")
    print(f"Total unique alerts emitted: {len(telemetry['all_alerts'])}")
    print(f"Detailed research log saved to: {OUTPUT_FILE}")
    print("=================================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
