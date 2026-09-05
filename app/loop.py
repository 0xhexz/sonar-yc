"""Monitor loop — one scan.

``run_scan`` fetches every enabled source, updates the directory snapshot,
classifies each signal (EARLY / CONFIRMED / SPEEDRUN), de-duplicates against the
store, tracks pending-early upgrades, and emits Slack alerts.

It is intentionally idempotent: re-running the same cycle must not re-alert.
Directory updates (``yc``/``speedrun``) refresh the ``directory`` table every
cycle so ``classify`` can check a social signal against the freshest listing.

A source failure is treated as a *coverage gap* (logged), never as "no news".
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings
from .detect import classify, signal_identities
from .models import (
    Alert,
    CompanySignal,
    Founder,
    slugify,
    EARLY,
    CONFIRMED,
    SPEEDRUN,
    DIRECTORY,
)
from .classifier import (
    FOUNDER_ANNOUNCEMENT,
    classify_batch,
    looks_like_founder_signal,
)
from .slack_notifier import SlackNotifier
from .store import Store
from .sources import get_source, source_kind

logger = logging.getLogger("ycradar.loop")


async def _filter_social(settings: Settings, sigs: list[CompanySignal]) -> list[CompanySignal]:
    """Route social signals through the precision gate, then the LLM filter.

    Layer 1 (free): the precision gate scores every post — hard disqualifiers
    kill congratulations/referrals/advice, weighted cues + founder voice score
    the rest. Survivors above the alert floor go on; borderline posts are kept
    for the end-of-scan recap rather than dropped silently.

    Layer 2 (paid, optional): the LLM confirms/enriches what survived.
    """
    # ---- Layer 1: precision gate (free, always on) ----
    from .precision import route as gate_route, run_gate

    gated: list[CompanySignal] = []
    for s in sigs:
        if not (s.description or "").strip():
            gated.append(s)  # nothing to score (e.g. directory-style stub) — abstain
            continue
        g = run_gate(s.description or "")
        verdict = gate_route(g.score, g.disqualified)
        if verdict == "drop":
            continue
        if verdict == "recap":
            logger.info("gate→recap (%.2f): %r", g.score, (s.description or "")[:60])
        if g.batch and not s.batch:
            s.batch = g.batch
        if g.company_hint and not s.name:
            s.name = g.company_hint
        gated.append(s)
    sigs = gated

    # ---- Layer 2: LLM (optional) ----
    if not (settings.llm_api_key and settings.classify_enabled):
        return sigs
    items = [
        {
            "id": str(i),
            "text": s.description,
            "author": (s.founders[0].lookup_key if s.founders else ""),
        }
        for i, s in enumerate(sigs)
    ]
    analyses = await classify_batch(settings, items)
    if not analyses:
        # LLM unavailable/failed — degrade to the cheap regex gate (precision
        # without the LLM), never pass ALL noise through.
        logger.warning(
            "classifier unavailable; using regex gate for %d social signals", len(sigs)
        )
        return [s for s in sigs if looks_like_founder_signal(s.description)]
    kept: list[CompanySignal] = []
    for i, s in enumerate(sigs):
        a = analyses.get(str(i))
        if a is None:
            # Not classified: pre-filtered noise, OR a chunk that failed after
            # another chunk succeeded (partial LLM outage). Fall back to the
            # regex gate so genuinely-matching posts aren't silently lost.
            if looks_like_founder_signal(s.description):
                kept.append(s)
            continue
        if a.label == FOUNDER_ANNOUNCEMENT and a.confidence >= settings.classify_min_confidence:
            if a.company_name:
                s.name = a.company_name
            if a.batch:
                s.batch = f"YC {a.batch}" if a.batch.upper().startswith("S") and a.batch[-2:].isdigit() else a.batch
            kept.append(s)
        # founder_applied / third_party / unrelated -> filtered out (no alert)
    logger.info("social filter: %d -> %d kept (founder announcements)", len(sigs), len(kept))
    return kept


@dataclass
class ScanResult:
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    alerts: list[Alert] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)


def _canonical_identity(sig: CompanySignal) -> str:
    """Primary company-level identity for alert de-duplication.

    Directory signals use their slug (or name). Social signals (no known
    company) fall back to the founder handle so the same founder isn't alerted
    twice across X and LinkedIn.
    """
    if sig.source in ("yc", "speedrun"):
        return sig.slug or slugify(sig.name)
    for f in sig.founders:
        if f.lookup_key:
            return f.lookup_key
    return sig.slug or slugify(sig.name)


def _directory_key_set(store: Store, source: str) -> set[str]:
    """Full identity set (slugs + names + founder handles) for a directory."""
    keys: set[str] = set()
    rows = store._conn.execute(
        "SELECT slug, payload FROM directory WHERE source=?", (source,)
    ).fetchall()
    for r in rows:
        keys.add(r["slug"])
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        if payload.get("name"):
            keys.add(slugify(payload["name"]))
            keys.add(str(payload["name"]).lower())
        for f in payload.get("founders") or []:
            if isinstance(f, dict):
                h = f.get("handle")
                if h:
                    keys.add(str(h).lower().replace("@", ""))
    return {k for k in keys if k}


async def _fetch_group(sources: list[str], settings: Settings) -> dict[str, list[CompanySignal]]:
    results: dict[str, list[CompanySignal]] = {}

    async def one(name: str):
        src = get_source(name, settings)
        try:
            results[name] = await src.fetch()
        except Exception as exc:  # noqa: BLE001  (coverage gap, not crash)
            logger.error("source %s failed: %s", name, exc)
            results[name] = []

    await asyncio.gather(*(one(n) for n in sources if n in ("yc", "speedrun", "x", "linkedin")))
    return results


def _make_alert(sig: CompanySignal, cls: str, upgraded: bool = False) -> Alert:
    founder = sig.founders[0] if sig.founders else None
    return Alert(
        classification=cls,
        company_name=sig.name or (founder.handle if founder else "") or "Unknown",
        batch=sig.batch,
        source=sig.source,
        description=sig.description,
        founder=founder,
        link=sig.url or sig.x_url or sig.linkedin_url or sig.website_url,
    )


async def run_scan(settings: Settings, store: Store, notifier: SlackNotifier,
                   only: list[str] | None = None) -> ScanResult:
    result = ScanResult()
    enabled = [s for s in settings.enabled_source_list if only is None or s in only]

    # 1) Fetch all enabled sources in parallel. Never let one failure kill others.
    fetched = await _fetch_group(enabled, settings)

    # 2) Refresh directory snapshots from the freshly fetched directory sources.
    #    On the very first scan, backfill silently (mark everything seen, no alerts)
    #    so we don't blast every historical company. Later runs alert on *new* ones.
    initialized = bool(store.get_state("directory_init"))
    dir_loaded = False
    for name in ("yc", "speedrun"):
        sigs = fetched.get(name, [])
        if sigs:
            dir_loaded = True
        try:
            store.save_directory_set(
                name,
                [
                    {
                        "slug": s.slug,
                        "name": s.name,
                        "founders": [
                            {"handle": f.lookup_key, "name": f.name} for f in s.founders
                        ],
                    }
                    for s in sigs
                ],
            )
            if not initialized:
                for s in sigs:
                    ident = _canonical_identity(s)
                    if ident:
                        store.mark_seen(ident)
        except Exception as exc:  # noqa: BLE001
            result.errors[name] = str(exc)
    if not initialized and dir_loaded:
        store.set_state("directory_init", True)

    # 3) Build fresh directory key sets for classification.
    yc_keys = _directory_key_set(store, "yc")
    speedrun_keys = _directory_key_set(store, "speedrun")

    # 4) Classify + de-duplicate + build alerts.
    #
    # DELIVER-FIRST LEDGER: we do NOT mark anything seen here. An item only
    # enters the ledger once Slack has acknowledged the message (or the bot is
    # in dry-run, where "decided" is all there is). Marking on decision means
    # an outage doesn't delay alerts — it deletes them: the company would be
    # recorded as reported and never reconsidered.
    emitted: list[Alert] = []
    _alert_identities: list[tuple[str, str]] = []  # (identity, classification)
    social_cap = settings.social_max_alerts_per_scan
    recap_items: list[str] = []
    _pending_thread_replies: list[tuple[str, str, str, str]] = []
    for name, sigs in fetched.items():
        if name in ("x", "linkedin", "hn"):
            sigs = await _filter_social(settings, sigs)
        source_emitted = 0
        for sig in sigs:
            try:
                cls = classify(sig, yc_keys, speedrun_keys)
                identity = _canonical_identity(sig)

                if not identity:
                    continue

                if cls == EARLY:
                    if store.is_seen(identity) or store.is_pending(identity):
                        continue
                    store.add_pending(identity, {"source": sig.source, "handle": identity})
                    # Burst protection: cap alerts per social scan; extras fall
                    # to the recap instead of being spam or silence.
                    if name in ("x", "linkedin", "hn") and source_emitted >= social_cap:
                        recap_items.append(f"{sig.name or identity} — {(sig.description or '')[:80]}")
                        continue
                    source_emitted += 1
                    emitted.append(_make_alert(sig, EARLY))
                    _alert_identities.append((identity, EARLY))

                else:  # CONFIRMED or SPEEDRUN (directory or social matching a dir identity)
                    upgraded = False
                    if store.is_seen(identity) or store.is_pending(identity):
                        # If it was pending-early and is now in a directory, upgrade
                        if store.is_pending(identity) or not store.is_seen(identity):
                            store.remove_pending(identity)
                            upgraded = True
                            emitted.append(_make_alert(sig, cls, upgraded=True))
                            _alert_identities.append((identity, cls))
                            # Queue a thread reply onto the original early alert.
                            saved = store.get_message_ts(identity)
                            if saved and notifier.ready:
                                _pending_thread_replies.append(
                                    (identity, saved[0], saved[1], sig.name)
                                )
                        continue
                    store.remove_pending(identity)
                    emitted.append(_make_alert(sig, cls))
                    _alert_identities.append((identity, cls))
            except Exception as exc:  # noqa: BLE001
                result.errors[f"{name}:{sig.name}"] = str(exc)

    # 5) Deliver, then ledger — highest-value first (early leads the channel).
    # An item counts as seen only once Slack has acknowledged it (message ts
    # received). Anything Slack does not ack stays unmarked and is offered
    # again next scan — an outage must delay alerts, never delete them. In
    # dry-run (notifier not ready) decision equals delivery, so we ledger.
    emitted.sort(key=lambda a: (a.classification != EARLY,))
    delivered = 0
    ledgered: set[str] = set()
    for (identity, _cls), alert in zip(_alert_identities, emitted):
        ok = await notifier.send(alert)
        if ok:
            delivered += 1
            if alert.classification == EARLY and alert.thread_ts:
                channel = notifier.dm_user or notifier.channel
                try:
                    store.save_message_ts(identity, channel or "", alert.thread_ts)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("could not persist message ts: %s", exc)
        if ok or not notifier.ready:
            # Acknowledged (or dry-run): safe to remember it as reported.
            store.mark_seen(identity)
            store.remove_pending(identity)
            ledgered.add(identity)
    for identity in ledgered:
        store.remove_pending(identity)

    # 5b) Self-health: a monitoring product that dies silently is worthless.
    # If a source failed, say so in Slack (max once per scan).
    health_failures = {k: v for k, v in result.errors.items() if "source" in k.lower()}
    if health_failures and notifier.ready:
        try:
            await notifier.send_health(health_failures)
        except Exception as exc:  # noqa: BLE001
            logger.warning("health alert failed: %s", exc)

    # 5c) Thread replies: prove the early call was right, in the original thread.
    for identity, chan, ts, company in _pending_thread_replies:
        try:
            await notifier.send_thread_reply(identity, chan, ts, company, days=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("thread reply failed for %s: %s", identity, exc)

    store.set_state("last_scan_at", result.scanned_at.isoformat())
    result.alerts = emitted
    result.counts = {
        "sources": enabled,
        "signals": {n: len(v) for n, v in fetched.items()},
        "alerts": len(emitted),
        "delivered": delivered,
        "yc_keys": len(yc_keys),
        "speedrun_keys": len(speedrun_keys),
    }
    logger.info("scan complete: %s", result.counts)
    return result
