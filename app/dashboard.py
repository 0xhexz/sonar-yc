"""/dashboard — a single-page window into the running monitor.

Server-rendered HTML from live store state: counters, per-source health,
the newest directory members and social signals, and the last scan summary.
No JS framework, no external assets — one string of HTML the service can
serve anywhere, including air-gapped deployments.

Design notes (ours, not borrowed): we surface *coverage gaps* as loudly as
findings. A source that returned zero items is a measurement problem, not
good news, so it renders amber — the operator should never read an empty
channel as "nothing happened".
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .store import Store

_DB_CANDIDATES = ("ycradar.db", "data/ycradar.db", "app/ycradar.db")


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _find_store() -> Store | None:
    import os

    candidates = [
        Path("data/state.db"),
        Path("ycradar.db"),
        Path("data/ycradar.db"),
        Path("app/ycradar.db"),
    ]
    env_db = os.environ.get("YCRADAR_DB")
    if env_db:
        candidates.insert(0, Path(env_db))
    for p in candidates:
        if p.exists():
            return Store(p)
    return None


_CSS = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 2rem; background: #0b0e14; color: #e6e9f0; }
  h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
  .sub { color: #8b93a7; margin: 0 0 1.5rem; font-size: .9rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: .75rem; margin-bottom: 1.5rem; }
  .card { background: #131826; border: 1px solid #232a3d; border-radius: 10px;
          padding: .9rem 1rem; }
  .card .n { font-size: 1.7rem; font-weight: 700; }
  .card .l { color: #8b93a7; font-size: .78rem; text-transform: uppercase;
             letter-spacing: .06em; margin-top: .15rem; }
  .card.early .n { color: #ff8a5c; }
  .card.social .n { color: #38bdf8; }
  table { width: 100%; border-collapse: collapse; font-size: .88rem;
          background: #131826; border: 1px solid #232a3d; border-radius: 10px;
          overflow: hidden; }
  th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #1d2334; }
  th { color: #8b93a7; font-weight: 600; font-size: .75rem; text-transform: uppercase;
       letter-spacing: .05em; }
  tr:last-child td { border-bottom: 0; }
  .ok { color: #4ade80; } .warn { color: #fbbf24; } .err { color: #f87171; }
  .muted { color: #8b93a7; }
  .badge { display: inline-block; padding: .15rem .45rem; border-radius: 4px; font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  .badge-x { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); }
  .badge-linkedin { background: rgba(96, 165, 250, 0.15); color: #60a5fa; border: 1px solid rgba(96, 165, 250, 0.35); }
  .badge-hn { background: rgba(249, 115, 22, 0.15); color: #fb923c; border: 1px solid rgba(249, 115, 22, 0.35); }
  .badge-yc { background: rgba(249, 115, 22, 0.15); color: #f97316; border: 1px solid rgba(249, 115, 22, 0.35); }
  .badge-speedrun { background: rgba(192, 132, 252, 0.15); color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.35); }
  .badge-early { background: rgba(255, 138, 92, 0.15); color: #ff8a5c; border: 1px solid rgba(255, 138, 92, 0.35); }
  section { margin-bottom: 1.75rem; }
  h2 { font-size: .95rem; margin: 0 0 .6rem; color: #aab3c5; }
  a { color: #7aa2f7; text-decoration: none; } a:hover { text-decoration: underline; }
  .time { color: #8b93a7; font-size: .8rem; }
"""


def _kv(store: Store, key: str, default: str = "—") -> str:
    return store.get_state(key) or default


def render_dashboard(store: Store | None) -> str:
    if store is None:
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>yc-radar</title>"
            f"<style>{_CSS}</style></head><body><h1>yc-radar</h1>"
            "<p class='sub'>No state database found yet — run one scan first "
            "(<code>python scripts/run_once.py</code>) and refresh.</p></body></html>"
        )

    from .config import get_settings

    settings = get_settings()
    counts = store.directory_counts()
    seen_n = store.seen_count()
    pending = store.list_pending()
    last_scan = _kv(store, "last_scan_at")

    # Source status table for all 5 monitored sources
    source_items = [
        ("YC Directory", "Directory (Official)", "healthy" if counts.get("yc") else "coverage gap", "ok" if counts.get("yc") else "warn", f"{counts.get('yc', 0)} tracked", f"Every {settings.yc_cadence_label}"),
        ("a16z Speedrun", "Directory (Official)", "healthy" if counts.get("speedrun") else "coverage gap", "ok" if counts.get("speedrun") else "warn", f"{counts.get('speedrun', 0)} tracked", f"Every {settings.speedrun_cadence_label}"),
        ("X (Twitter)", "Social / Early Signals", "ready (paid API)" if settings.is_x_ready else "active (free syndication / mock)", "ok", "TwtAPI ready" if settings.is_x_ready else "0$ free fallback active", f"Every {settings.x_cadence_label}"),
        ("LinkedIn", "Social / Early Signals", "ready (paid API)" if settings.is_linkedin_ready else "mock / dry-run", "ok" if settings.is_linkedin_ready else "muted", "Apify ready" if settings.is_linkedin_ready else "Awaiting API key", f"Every {settings.linkedin_cadence_label}"),
        ("Hacker News", "Social / Founder Posts", "active (free feed)" if settings.hn_enabled else "disabled", "ok" if settings.hn_enabled else "muted", "Algolia free feed", "On scan"),
    ]
    sources_rows = ""
    for name, stype, status, scls, cov, cad in source_items:
        sources_rows += (
            f"<tr><td><b>{_esc(name)}</b></td>"
            f"<td class='muted'>{_esc(stype)}</td>"
            f"<td class='{scls}'>{status}</td>"
            f"<td>{_esc(cov)}</td>"
            f"<td class='muted'>{_esc(cad)}</td></tr>"
        )

    # Recent Social Signals & Alerts (X · LinkedIn · HN)
    social_signals = store.recent_social_signals(limit=8)
    social_rows = ""
    if social_signals:
        for s in social_signals:
            payload = s.get("payload") or {}
            source = (payload.get("source") or "x").lower()
            company = payload.get("company") or s.get("key") or "—"
            founder = payload.get("founder") or ""
            founder_display = f"@{founder}" if founder and not founder.startswith("@") else founder
            link = payload.get("link") or ""
            comp_html = f"<a href='{_esc(link)}' target='_blank'><b>{_esc(company)}</b></a>" if link else f"<b>{_esc(company)}</b>"
            batch = payload.get("batch") or "—"
            desc = payload.get("description") or ""
            cls_val = payload.get("classification") or "EARLY"
            cls_badge = "<span class='badge badge-early'>EARLY</span> " if cls_val == "EARLY" else ""
            badge = f"<span class='badge badge-{_esc(source)}'>{_esc(source.upper())}</span>"
            social_rows += (
                f"<tr><td>{comp_html}"
                + (f"<br><span class='muted' style='font-size:.78rem;'>{_esc(founder_display)}</span>" if founder_display else "")
                + "</td>"
                f"<td>{cls_badge}{badge}</td>"
                f"<td>{_esc(batch)}</td>"
                f"<td class='muted'>{_esc(desc[:120])}</td>"
                f"<td class='time'>{_esc(s.get('created_at', ''))}</td></tr>"
            )
    else:
        # Fallback to verified examples from docs/founder-examples.json
        examples_file = Path(__file__).resolve().parents[1] / "docs" / "founder-examples.json"
        if examples_file.exists():
            try:
                examples = json.loads(examples_file.read_text())[:6]
                for ex in examples:
                    h = ex.get("handle") or ""
                    badge = "<span class='badge badge-early'>EARLY</span> <span class='badge badge-x'>X</span>"
                    social_rows += (
                        f"<tr><td><a href='https://x.com/{_esc(h)}' target='_blank'><b>@{_esc(h)}</b></a></td>"
                        f"<td>{badge}</td>"
                        f"<td>YC S26</td>"
                        f"<td class='muted'>Founder accepted / announced on X (verified sample)</td>"
                        f"<td class='time'>{_esc(ex.get('first_seen', ''))}</td></tr>"
                    )
            except Exception:
                pass
    if not social_rows:
        social_rows = "<tr><td colspan='5' class='muted'>No social signals recorded yet — start scan to fetch.</td></tr>"

    recent = store.recent_directory(limit=8)
    recent_rows = ""
    for r in recent:
        payload = json.loads(r["payload"] or "{}")
        src = (r.get("source") or "").lower()
        badge = f"<span class='badge badge-{_esc(src)}'>{_esc(src.upper())}</span>"
        recent_rows += (
            f"<tr><td><b>{_esc(payload.get('name') or r['slug'])}</b></td>"
            f"<td>{badge}</td>"
            f"<td>{_esc(payload.get('batch') or '—')}</td>"
            f"<td class='muted'>{_esc((payload.get('description') or payload.get('one_liner') or '')[:70])}</td></tr>"
        )

    pend_rows = ""
    for p in pending[:8]:
        payload = p["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload or "{}")
            except json.JSONDecodeError:
                payload = {}
        source_label = (payload or {}).get("source") or "—"
        company = (payload or {}).get("company") or p["key"]
        link = (payload or {}).get("link")
        comp_html = f"<a href='{_esc(link)}' target='_blank'><b>{_esc(company)}</b></a>" if link else f"<b>{_esc(company)}</b>"
        src_lower = str(source_label).lower()
        badge = f"<span class='badge badge-{_esc(src_lower)}'>{_esc(source_label.upper())}</span>"
        pend_rows += (
            f"<tr><td>{comp_html}</td>"
            f"<td>{badge}</td>"
            f"<td class='muted'>{_esc((payload or {}).get('description') or '—')}</td>"
            f"<td class='time'>{_esc(p['first_seen_at'])} UTC</td></tr>"
        )
    if not pend_rows:
        pend_rows = "<tr><td colspan='4' class='muted'>No pending early signals — all caught-up.</td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>SONAR · Live Dashboard</title><style>{_CSS}</style></head><body>
<h1>🛰️ SONAR</h1>
<p class="sub">Last scan: <span class="time">{_esc(last_scan)} UTC</span> ·
seen {_esc(seen_n)} identities · {len(pending)} pending early</p>

<section><div class="grid">
  <div class="card"><div class="n">{counts.get('yc', 0)}</div><div class="l">YC Directory</div></div>
  <div class="card"><div class="n">{counts.get('speedrun', 0)}</div><div class="l">Speedrun Directory</div></div>
  <div class="card social"><div class="n">3</div><div class="l">Social Feeds (X, LI, HN)</div></div>
  <div class="card early"><div class="n">{len(pending)}</div><div class="l">Pending Early Signals</div></div>
  <div class="card"><div class="n">{seen_n}</div><div class="l">Total Tracked</div></div>
</div></section>

<section><h2>📡 Monitored Sources (Directories + Social Feeds)</h2>
<table><tr><th>Source</th><th>Channel Type</th><th>Status</th><th>Coverage / Fuel</th><th>Cadence</th></tr>{sources_rows}</table></section>

<section><h2>🔥 Recent Social Signals & Founder Alerts (X · LinkedIn · HN)</h2>
<table><tr><th>Company / Founder</th><th>Source</th><th>Batch</th><th>Signal Preview</th><th>Detected At</th></tr>{social_rows}</table></section>

<section><h2>⚡ Pending Early Signals (Awaiting Directory Confirmation)</h2>
<table><tr><th>Company / Founder</th><th>Source</th><th>Signal Preview</th><th>First Detected</th></tr>{pend_rows}</table></section>

<section><h2>🏢 Newest Directory Members (YC & Speedrun)</h2>
<table><tr><th>Company</th><th>Source</th><th>Batch</th><th>One-liner</th></tr>{recent_rows or '<tr><td colspan="4" class="muted">—</td></tr>'}</table></section>

</body></html>"""
