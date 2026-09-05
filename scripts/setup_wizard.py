"""Interactive setup wizard — `python scripts/setup_wizard.py`.

Walks a non-technical user from zero to a running bot:

  1. Validates the Slack bot token (auth.test) and shows the workspace name.
  2. Lists channels the bot can post to; you pick one by number (no channel IDs).
  3. Joins the chosen channel (conversations.join) if needed.
  4. Sends a test message and asks you to confirm it arrived.
  5. Writes every answer to .env — nothing else to edit.

Inspired by Foxy's `init` (the best setup UX of the three projects).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLACK = "https://slack.com/api"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def main() -> int:
    env_path = ROOT / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    print("\n=== yc-radar setup wizard ===\n")

    # 1. Slack token
    token = _ask("Slack bot token (xoxb-…)", existing.get("SLACK_BOT_TOKEN", ""))
    if not token.startswith("xoxb-"):
        print("  ⚠️  That doesn't look like a bot token (should start with xoxb-).")
        if _ask("Use it anyway?", "n").lower() not in ("y", "yes"):
            return 1

    async def _flow() -> tuple[list[dict], dict, bool]:
        channels: list[dict] = []
        result_env: dict[str, str] = {}
        ok = True
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{SLACK}/auth.test", headers={"Authorization": f"Bearer {token}"}
            )
            info = r.json()
            if not info.get("ok"):
                print(f"  ❌ Token rejected: {info.get('error')}")
                return channels, result_env, False
            print(f"  ✅ Connected to {info.get('team')} as @{info.get('user')}")

            resp = (
                await client.post(
                    f"{SLACK}/conversations.list",
                    json={"limit": 200, "types": "public_channel,private_channel"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            ).json()
            channels = resp.get("channels") or []
            print("\n  Where should alerts go?")
            for i, c in enumerate(channels, 1):
                mark = " (already in)" if c.get("is_member") else ""
                print(f"    {i}. #{c['name']}{mark}")
            dm_idx = len(channels) + 1
            print(f"    {dm_idx}. DM me instead")
            choice = int(_ask(f"Pick 1-{dm_idx}", "1") or "1")
            if choice == dm_idx:
                member = _ask("Your Slack member ID (U…)")
                dm = (
                    await client.post(
                        f"{SLACK}/conversations.open",
                        json={"users": member},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                ).json()
                result_env["SLACK_CHANNEL_ID"] = ""
                result_env["SLACK_DM_USER_ID"] = (dm.get("channel") or {}).get("id", "")
            else:
                ch = channels[choice - 1]
                result_env["SLACK_CHANNEL_ID"] = ch["id"]
                result_env["SLACK_DM_USER_ID"] = ""
                if not ch.get("is_member"):
                    join = (
                        await client.post(
                            f"{SLACK}/conversations.join",
                            json={"channel": ch["id"]},
                            headers={"Authorization": f"Bearer {token}"},
                        )
                    ).json()
                    if join.get("ok"):
                        print(f"  ✅ Joined #{ch['name']}")
                    else:
                        print(f"  ⚠️ join failed: {join.get('error')}")

            test = (
                await client.post(
                    f"{SLACK}/chat.postMessage",
                    json={
                        "channel": result_env["SLACK_CHANNEL_ID"] or result_env["SLACK_DM_USER_ID"],
                        "text": "✅ yc-radar setup test — if you can read this, alerts will arrive here.",
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            ).json()
            if test.get("ok"):
                print("  ✅ Test message sent — go and check Slack.")
            else:
                print(f"  ❌ Test message failed: {test.get('error')} — fix this before continuing.")
                if _ask("Continue anyway?", "n").lower() not in ("y", "yes"):
                    ok = False
        return channels, result_env, ok

    channels, env_updates, ok = _run(_flow())
    if not ok and not _ask("Save settings anyway?", "n").lower() in ("y", "yes"):
        return 1

    # 5. Write .env
    existing.update(env_updates)
    existing["SLACK_BOT_TOKEN"] = token
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n")
    print(f"\n  Saved to {env_path}. Setup is done — start the bot with:")
    print("    .venv/bin/python -m app.main   (or: uvicorn app.main:app)")
    print("\n  Confirm memory works: run a scan twice; the 2nd must report 0 new.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

