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
  table { width: 100%; border-collapse: collapse; font-size: .88rem;
          background: #131826; border: 1px solid #232a3d; border-radius: 10px;
          overflow: hidden; }
  th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #1d2334; }
  th { color: #8b93a7; font-weight: 600; font-size: .75rem; text-transform: uppercase;
       letter-spacing: .05em; }
  tr:last-child td { border-bottom: 0; }
  .ok { color: #4ade80; } .warn { color: #fbbf24; } .err { color: #f87171; }
  .muted { color: #8b93a7; }
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

    counts = store.directory_counts()
    seen_n = store.seen_count()
    pending = store.list_pending()
    last_scan = _kv(store, "last_scan_at")
    sources_rows = ""
    for name, n in counts.items():
        cls = "ok" if n else "warn"
        status = "healthy" if n else "no data — coverage gap"
        sources_rows += (
            f"<tr><td><b>{_esc(name)}</b></td>"
            f"<td class='{cls}'>{status}</td>"
            f"<td>{n} tracked</td></tr>"
        )

    recent = store.recent_directory(limit=8)
    recent_rows = ""
    for r in recent:
        payload = json.loads(r["payload"] or "{}")
        recent_rows += (
            f"<tr><td><b>{_esc(payload.get('name') or r['slug'])}</b></td>"
            f"<td>{_esc(payload.get('batch') or '—')}</td>"
            f"<td class='muted'>{_esc((payload.get('description') or payload.get('one_liner') or '')[:70])}</td>"
            f"<td>{_esc(r['source'])}</td></tr>"
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
        pend_rows += (
            f"<tr><td><b>{_esc(p['key'])}</b></td>"
            f"<td class='muted'>{_esc(source_label)}</td>"
            f"<td class='time'>{_esc(p['first_seen_at'])} UTC</td></tr>"
        )
    if not pend_rows:
        pend_rows = "<tr><td colspan='3' class='muted'>No pending early signals — all caught-up.</td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>yc-radar · dashboard</title><style>{_CSS}</style></head><body>
<h1>🛰️ yc-radar</h1>
<p class="sub">Last scan: <span class="time">{_esc(last_scan)} UTC</span> ·
seen {_esc(seen_n)} identities · {len(pending)} pending early</p>

<section><div class="grid">
  <div class="card"><div class="n">{counts.get('yc', 0)}</div><div class="l">YC tracked</div></div>
  <div class="card"><div class="n">{counts.get('speedrun', 0)}</div><div class="l">Speedrun tracked</div></div>
  <div class="card early"><div class="n">{len(pending)}</div><div class="l">Pending early</div></div>
  <div class="card"><div class="n">{seen_n}</div><div class="l">Reported total</div></div>
</div></section>

<section><h2>Source health — gaps render amber</h2>
<table><tr><th>Source</th><th>Status</th><th>Coverage</th></tr>{sources_rows}</table></section>

<section><h2>Newest directory members</h2>
<table><tr><th>Company</th><th>Batch</th><th>One-liner</th><th>Source</th></tr>{recent_rows or '<tr><td colspan="4" class="muted">—</td></tr>'}</table></section>

<section><h2>Pending early signals (awaiting directory confirmation)</h2>
<table><tr><th>Identity</th><th>First seen via</th><th>Since</th></tr>{pend_rows}</table></section>
</body></html>"""
