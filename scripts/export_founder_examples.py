"""Export real founder examples + the exact Slack payloads the bot delivered.

Pulls identities, delivery receipts and the precise Block Kit output for each
named founder from the live state DB, so the submission can attach authentic
bot output rather than mock-ups.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Alert, CompanySignal, Founder  # noqa: E402
from app.slack_notifier import build_blocks, render_text  # noqa: E402

DB = "data/state.db"
HIGHLIGHT = ["adalat_ai", "mynameisyahia", "zmrishh", "georgejeffersn", "madebythomasai", "willowvoiceai"]

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def payload_of(row):
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else (p or {})


print("loading seen identities with payloads...")
seen = {}
for r in c.execute("SELECT dedup_key, payload FROM seen"):
    seen[r["dedup_key"]] = payload_of(r)

print("building example output for:", ", ".join(HIGHLIGHT))
out = []
for handle in HIGHLIGHT:
    key = f"x:{handle}"
    info = seen.get(key) or seen.get(handle) or {}
    ts_row = c.execute("SELECT ts FROM message_ts WHERE identity=?", (handle,)).fetchone()
    pend = c.execute("SELECT first_seen_at FROM pending_early WHERE key=?", (handle,)).fetchone()
    sig = CompanySignal(
        source="x",
        name=(info.get("name") or handle),
        description=(info.get("description") or info.get("text") or ""),
        founders=[Founder(handle=handle, url=f"https://x.com/{handle}")],
        url=f"https://x.com/{handle}",
    )
    alert = Alert(
        classification="EARLY",
        company_name=(info.get("company") or handle),
        batch=(info.get("batch") or "YC S26"),
        source="X",
        description=sig.description[:400],
        founder=sig.founders[0],
        link=f"https://x.com/{handle}",
    )
    out.append(
        {
            "handle": handle,
            "slack_ts": ts_row["ts"] if ts_row else None,
            "first_seen": pend["first_seen_at"] if pend else None,
            "blocks": build_blocks(alert),
            "text": render_text(alert),
        }
    )

dest = Path("docs/founder-examples.json")
dest.write_text(json.dumps(out, indent=2, default=str))
print(f"wrote {dest} ({len(out)} examples)")
for o in out:
    line = (o["text"].splitlines()[0],)
    print(" -", o["handle"], "| ts:", o["slack_ts"], "|", line[0])
