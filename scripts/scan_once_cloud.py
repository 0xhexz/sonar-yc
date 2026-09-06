"""One-shot scan for cloud runners (GitHub Actions cron).

Runs a single scan against the configured STATE_DB (Postgres on Actions) and
delivers alerts to Slack, then exits. No scheduler — the cron IS the scheduler.
Exit code 0 on success (even with 0 alerts), 1 on scan failure.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    from app.config import get_settings
    from app.store import open_store
    from app.slack_notifier import SlackNotifier
    from app.loop import run_scan

    st = get_settings()
    store = open_store()
    notifier = SlackNotifier(
        st.slack_bot_token, st.slack_webhook_url, st.slack_channel, st.slack_dm_user
    )
    target = st.slack_channel or st.slack_dm_user
    print(f"SONAR one-shot scan | slack: {notifier.ready} ({target})")

    result = await run_scan(st, store, notifier, only=None)
    print("signals:", result.counts.get("signals"))
    print(f"alerts: {result.counts.get('alerts')} | delivered: {result.counts.get('delivered')}")
    if result.errors:
        print("errors:", list(result.errors.items())[:5])
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
