"""Full live scan with the fresh TwtAPI key: all 5 sources -> Slack delivery.

Delivers to the configured Slack channel. Proves the whole pipeline end-to-end
with real fuel: X (paid, fresh key), LinkedIn (Apify), HN, YC, Speedrun.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    from app.config import get_settings
    from app.store import Store
    from app.slack_notifier import SlackNotifier
    from app.loop import run_scan

    st = get_settings()
    store = Store(st.state_db_path)
    notifier = SlackNotifier(
        st.slack_bot_token, st.slack_webhook_url, st.slack_channel, st.slack_dm_user
    )
    print(f"Slack ready: {notifier.ready} | target: {st.slack_channel or st.slack_dm_user}")

    result = await run_scan(st, store, notifier, only=None)
    print(f"scan: sources={result.counts.get('sources')}")
    print(f"signals: {result.counts.get('signals')}")
    print(f"alerts: {result.counts.get('alerts')} | DELIVERED to Slack: {result.counts.get('delivered')}")
    if result.errors:
        print("errors:", list(result.errors.items())[:3])


if __name__ == "__main__":
    asyncio.run(main())
