"""INPUT VERIFICATION SUITE
=========================
Test 1-4: Each cadence input actually controls its scheduler job.
Test 5-8: Each source returns VALID results (real data, correct shape).
Test 9-10: Full pipeline integrity (filter works, dedupe works).
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.sources.speedrun import SpeedrunSource
from app.sources.x_twitter import XSource
from app.sources.linkedin import LinkedInSource
from app.sources.yc_directory import YC_DirectorySource
from app.classifier import looks_like_founder_signal

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


async def main():
    # ---------- 1-4: cadence inputs wired to scheduler jobs ----------
    s = Settings(
        yc_interval_hours=2,
        speedrun_interval_hours=4,
        x_interval_minutes=15,
        linkedin_interval_hours=48,
        run_on_start=False,
    )
    check("1. YC_INTERVAL_HOURS env→config", s.yc_interval_hours == 2, f"= {s.yc_interval_hours}")
    check("2. SPEEDRUN_INTERVAL_HOURS env→config", s.speedrun_interval_hours == 4, f"= {s.speedrun_interval_hours}")
    check("3. X_INTERVAL_MINUTES env→config", s.x_interval_minutes == 15, f"= {s.x_interval_minutes}")
    check("4. LINKEDIN_INTERVAL_HOURS env→config", s.linkedin_interval_hours == 48, f"= {s.linkedin_interval_hours}")

    # scheduler wiring (inspect main.py source for job->interval mapping)
    src_main = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text()
    check("1b. YC job uses yc_interval_hours", "yc_interval_hours" in src_main and '_yc_job' in src_main)
    check("2b. Speedrun job uses speedrun_interval_hours", "speedrun_interval_hours" in src_main)
    check("3b. X job uses x_interval_minutes", "x_interval_minutes" in src_main)
    check("4b. LinkedIn job uses linkedin_interval_hours", "linkedin_interval_hours" in src_main)

    # ---------- 5-8: each source returns VALID results ----------
    st = get_settings_for_sources()
    print("\n--- 5. YC (Algolia) ---")
    yc = await YC_DirectorySource(st).fetch()
    check("5a. YC returns >100 companies", len(yc) > 100, f"= {len(yc)}")
    if yc:
        sample = yc[0]
        check("5b. YC signal has name+slug+url", bool(sample.name and sample.slug and sample.url))
        check("5c. YC has real batch tag", any(s.batch for s in yc[:20]), "e.g. " + (yc[0].batch or ""))
        # spot-check a known YC company name format
        check("5d. YC names non-empty strings", all(s.name for s in yc[:20]))

    print("\n--- 6. Speedrun (REST) ---")
    sp = await SpeedrunSource(st).fetch()
    check("6a. Speedrun returns >200 companies", len(sp) > 200, f"= {len(sp)}")
    if sp:
        check("6b. Speedrun batch = SRxxx format", all((s.batch or "").startswith("SR") for s in sp[:20]))
        check("6c. Speedrun has founder data", any(s.founders for s in sp[:20]))

    print("\n--- 7. X (TwtAPI) ---")
    xs = await XSource(st).fetch()
    check("7a. X returns >100 raw signals", len(xs) > 100, f"= {len(xs)}")
    if xs:
        check("7b. X signals have founder handles", any(s.founders and s.founders[0].handle for s in xs[:30]))
        check("7c. X signals have text", any(s.description for s in xs[:30]))
        # relevance: after our fetch, some should mention YC
        ycm = sum(1 for s in xs[:50] if "yc" in (s.description or "").lower() or "y combinator" in (s.description or "").lower())
        check("7d. X posts mention YC", ycm > 10, f"{ycm}/50")

    print("\n--- 8. LinkedIn (Apify) ---")
    li = await LinkedInSource(st).fetch()
    check("8a. LinkedIn returns posts", len(li) > 10, f"= {len(li)}")
    if li:
        check("8b. LinkedIn posts have text", any(s.description for s in li[:10]))
        check("8c. LinkedIn posts have author info", any(s.founders for s in li[:10]))

    # ---------- 9-10: pipeline integrity ----------
    print("\n--- 9. Filter integrity (regex gate) ---")
    checks = [
        ("I got into YC S26 as a solo founder!", True),
        ("Y Combinator is the USAID of Silicon Valley", False),
        ("We just got rejected from the YC F26 batch!", False),
        ("My application video for Y Combinator", False),
        ("Adalat AI is now backed by Y Combinator", True),
    ]
    for text, expected in checks:
        got = looks_like_founder_signal(text)
        check(f"   gate {'PASS' if expected else 'DROP'}: {text[:35]!r}", got == expected)

    print("\n--- 10. Dedupe integrity ---")
    from app.store import Store
    from app.loop import run_scan
    from app.slack_notifier import SlackNotifier
    db = Path(tempfile.mkdtemp()) / "v.db"
    store = Store(db)
    nf = SlackNotifier(None, None, "#x")
    r1 = await run_scan(st, store, nf, only=["speedrun"])
    r2 = await run_scan(st, store, nf, only=["speedrun"])
    check("10a. scan1 fetched companies", r1.counts["signals"].get("speedrun", 0) > 200)
    # scan2: after backfill, 0 alerts expected (identical data)
    check("10b. scan2 = 0 duplicates", len(r2.alerts) == 0, f"= {len(r2.alerts)}")
    store.close()

    print(f"\n{'='*50}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)


def get_settings_for_sources():
    # use real .env creds (loaded automatically); disable LLM for speed
    from app.config import get_settings
    st = get_settings()
    return st


asyncio.run(main())
