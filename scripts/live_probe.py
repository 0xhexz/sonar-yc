"""Live probe: real fetch from both directory sources (YC + a16z Speedrun)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.sources.speedrun import SpeedrunSource
from app.sources.yc_directory import YC_DirectorySource


async def main():
    st = Settings()
    print("=" * 50)
    print("▶ SPEEDRUN (a16z) — public REST API")
    sr = await SpeedrunSource(st).fetch()
    print(f"   fetched {len(sr)} companies")
    for s in sr[:5]:
        print(f"   - {s.name} | {s.batch} | {s.city} | x:{s.x_url}")
    print("=" * 50)
    print("▶ YC DIRECTORY — Playwright render (retry-সহ)")
    yc = await YC_DirectorySource(st).fetch()
    print(f"   fetched {len(yc)} companies")
    for s in yc[:6]:
        print(f"   - {s.name} | {s.batch} | {s.city} | url:{s.url}")
    print("=" * 50)
    # Cross-check: does the yc batch look like the CURRENT batch?
    if yc:
        batches = sorted({s.batch for s in yc}, key=lambda b: b or "")
        print("   YC batch(es) detected:", batches)


asyncio.run(main())
