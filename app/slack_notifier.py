"""Slack delivery — Block Kit formatting + post.

Supports two delivery paths:

* **Bot token** (recommended): ``POST /api/chat.postMessage`` with
  ``Authorization: Bearer xoxb-…`` — works for channels AND DMs.
* **Incoming webhook** (simplest): ``POST`` the webhook URL.

Each alert carries the company name, source, batch, description, link and a
status line, matching the brief's examples.
"""
from __future__ import annotations

import logging

import httpx

from .models import Alert, EARLY, CONFIRMED, SPEEDRUN, DIRECTORY

logger = logging.getLogger("ycradar.slack")

SLACK_API = "https://slack.com/api/chat.postMessage"


def classification_meta(cls: str) -> tuple[str, str]:
    """Return (header_text, status_line) for a classification."""
    if cls == EARLY:
        return (
            "🔥 EARLY YC SIGNAL — Founder Announced Before YC",
            "⚡ Founder announced / not yet officially announced by YC",
        )
    if cls == CONFIRMED:
        return (
            "⚡ NEW YC COMPANY",
            "✅ Confirmed by YC",
        )
    if cls == SPEEDRUN:
        return (
            "🚀 NEW SPEEDRUN COMPANY",
            "✅ Confirmed — a16z Speedrun",
        )
    if cls == "HN_LAUNCH":
        return (
            "🟠 LAUNCH HN — Founder launch post",
            "ℹ️ Founder launched on Hacker News (free signal feed)",
        )
    return ("📡 NEW YC/SPEEDRUN SIGNAL", "ℹ️ New listing")


def build_blocks(alert: Alert) -> list[dict]:
    header, status = classification_meta(alert.classification)
    founder_link = ""
    if alert.founder and (alert.founder.handle or alert.founder.name):
        handle = alert.founder.handle or alert.founder.name
        tag = f"@{handle}" if alert.founder.handle else handle
        founder_link = f"{tag}"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Company:* {alert.company_name or '—'}"},
                {"type": "mrkdwn", "text": f"*Batch:* {alert.batch or '—'}"},
                {"type": "mrkdwn", "text": f"*Founder:* {founder_link or '—'}"},
                {"type": "mrkdwn", "text": f"*Source:* {alert.source.upper()}"},
                {"type": "mrkdwn", "text": f"*Status:* {status}"},
            ],
        },
    ]
    if alert.description:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Description:*\n{alert.description[:400]}"}}
        )
    if alert.link:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"🔗 *Link:* <{alert.link}>"}}
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Detected: {alert.detected_at.strftime('%b %-d, %Y, %-I:%M %p')} PT",
                }
            ],
        }
    )
    return blocks


def render_text(alert: Alert) -> str:
    """Plain-text fallback (used for webhooks without blocks and for demos)."""
    header, status = classification_meta(alert.classification)
    lines = [header, ""]
    lines.append(f"Company: {alert.company_name or '—'}")
    lines.append(f"Batch: {alert.batch or '—'}")
    if alert.founder:
        f = alert.founder.handle or alert.founder.name
        if f:
            lines.append(f"Founder: {f}")
    lines.append(f"Source: {alert.source.upper()}")
    lines.append(f"Status: {status}")
    if alert.description:
        lines.append(f"Description: {alert.description}")
    if alert.link:
        lines.append(f"Link: {alert.link}")
    lines.append(f"Detected: {alert.detected_at.strftime('%b %-d, %Y, %-I:%M %p')} PT")
    return "\n".join(lines)


class SlackNotifier:
    def __init__(self, bot_token: str | None, webhook_url: str | None, channel: str, dm_user: str | None = None):
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.channel = channel
        self.dm_user = dm_user

    @property
    def ready(self) -> bool:
        return bool(self.bot_token or self.webhook_url)

    def _text(self, alert: Alert) -> str:
        return render_text(alert)

    async def send_health(self, failures: dict[str, str]) -> bool:
        """Report degraded sources in-channel (Foxy's rule: silent failure is
        the only unrecoverable bug in a monitoring product)."""
        if not self.ready or not failures:
            return False
        lines = "\n".join(f"• *{src}* — {err[:120]}" for src, err in failures.items())
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *yc-radar · source degraded*\n"
                        f"{lines}\n\n_Other sources are still running normally._"
                    ),
                },
            }
        ]
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                if self.bot_token:
                    target = self.dm_user if self.dm_user else self.channel
                    resp = await client.post(
                        SLACK_API,
                        json={"channel": target, "text": "yc-radar source degraded", "blocks": blocks},
                        headers={"Authorization": f"Bearer {self.bot_token}"},
                    )
                    return resp.status_code == 200 and resp.json().get("ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("health post failed: %s", exc)
        return False

    async def send_thread_reply(self, identity: str, channel: str, ts: str, company: str, days: int | None) -> bool:
        """Reply in the original early-signal thread when YC confirms the company.

        This is the bot proving its own value in place, on the original alert.
        """
        if not self.bot_token:
            return False
        lead = (
            f"*{company}* is now listed in the YC directory."
            if days is None
            else f"*{company}* is now listed in the YC directory · *{days} day(s)* after yc-radar flagged it."
        )
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":white_check_mark: {lead}"},
            }
        ]
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.post(
                    SLACK_API,
                    json={"channel": channel, "thread_ts": ts, "text": lead, "blocks": blocks},
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                )
                ok = resp.status_code == 200 and resp.json().get("ok")
                if ok:
                    logger.info("thread reply posted for %s", identity)
                return ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("thread reply failed for %s: %s", identity, exc)
            return False

    async def send(self, alert: Alert) -> bool:
        """Deliver an alert. Returns True if a provider accepted it, False for
        a dry-run (no Slack credentials configured).

        On success the message ``ts`` is stored on the alert so the loop can
        later thread a confirmation reply onto the original early signal.
        """
        if not self.ready:
            logger.info("Slack not configured — dry-run only (would send for %s)", alert.company_name)
            return False

        blocks = build_blocks(alert)
        text = self._text(alert)

        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                if self.bot_token:
                    target = self.dm_user if self.dm_user else self.channel
                    payload = {"channel": target, "text": text, "blocks": blocks}
                    if getattr(alert, "thread_ts", None):
                        payload["thread_ts"] = alert.thread_ts
                    resp = await client.post(
                        SLACK_API,
                        json=payload,
                        headers={"Authorization": f"Bearer {self.bot_token}"},
                    )
                    data = resp.json()
                    if resp.status_code == 200 and data.get("ok"):
                        logger.info("Slack alert delivered to %s", target)
                        alert.thread_ts = data.get("ts")  # enable future thread replies
                        return True
                    logger.error("Slack API error: %s", data)
                    return False
                if self.webhook_url:
                    resp = await client.post(self.webhook_url, json={"text": text, "blocks": blocks})
                    if resp.status_code in (200, 201, 202):
                        logger.info("Slack webhook alert delivered")
                        return True
                    logger.error("Slack webhook error %s: %s", resp.status_code, resp.text[:300])
                    return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Slack delivery failed: %s", exc)
        return False
