"""Slack live proof: find channel, join if possible, post a REAL alert, read back verify."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import get_settings
from app.slack_notifier import build_blocks, render_text
from app.models import Alert, Founder, EARLY

API = "https://slack.com/api"


async def main():
    st = get_settings()
    tok = st.slack_bot_token
    assert tok, "no token"
    h = {"Authorization": f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=20) as c:
        # 1) find bot's own user id
        ra = await c.post(f"{API}/auth.test", headers=h)
        bot_uid = ra.json().get("user_id")
        print("bot uid:", bot_uid)
        # 2) open DM with the bot itself -> Slack delivers to the bot's app DM
        #    (for a human recipient we'd need their member id; channel-first below)
        # 3) list channels anyway (maybe #yc-radar exists with invite)
        r = await c.get(f"{API}/conversations.list", headers=h, params={"limit": 100})
        chans = r.json().get("channels", [])
        print("channels visible:", [(ch.get("name"), ch.get("is_member")) for ch in chans])
        target = next((ch for ch in chans if ch.get("name") == "yc-radar"), None) or (chans[0] if chans else None)
        if target:
            cid = target["id"]
            if not target.get("is_member"):
                rj = await c.post(f"{API}/conversations.join", headers=h, json={"channel": cid})
                print("join:", rj.json().get("ok"), rj.json().get("error", ""))
        else:
            # DM route: conversations.open with the bot's own uid (app DM)
            ro = await c.post(f"{API}/conversations.open", headers=h, json={"users": bot_uid})
            print("conversations.open:", ro.json().get("ok"), ro.json().get("error", ""))
            cid = (ro.json().get("channel") or {}).get("id")
            print("DM channel:", cid)
        if not cid:
            print("FAILED to open any target")
            return
        alert = Alert(
            classification=EARLY,
            company_name="Unannounced YC Startup (live scan)",
            batch="YC S26",
            source="x",
            description="I got into YC S26 as a solo founder! The last 15 months looked something like this…",
            founder=Founder(handle="mynameisyahia", url="https://x.com/mynameisyahia"),
            link="https://x.com/mynameisyahia",
        )
        rp = await c.post(f"{API}/chat.postMessage", headers=h, json={
            "channel": cid, "text": render_text(alert), "blocks": build_blocks(alert),
        })
        pd = rp.json()
        print("postMessage ok:", pd.get("ok"), "| error:", pd.get("error"), "| ts:", pd.get("ts"))
        if pd.get("ok") and pd.get("ts"):
            rh = await c.get(f"{API}/conversations.history", headers=h, params={"channel": cid, "limit": 5})
            msgs = rh.json().get("messages", [])
            hit = next((m for m in msgs if m.get("ts") == pd["ts"]), None)
            print("READ-BACK:", "VERIFIED ✅" if hit else "NOT FOUND ❌", "| text head:", (hit or {}).get("text", "")[:80])


asyncio.run(main())
