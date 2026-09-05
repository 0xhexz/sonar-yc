"""Live X test (reads key from .env via config — never prints the key).

Run AFTER putting X_PROVIDER_API_KEY in .env. Makes a real advanced_search for
our YC keyword and reports whether tweets came back (proves the provider/credit
works regardless of any balance badge on the dashboard).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.sources.x_twitter import XSource


async def main():
    st = get_settings()
    if not st.is_x_ready:
        print("X provider NOT configured — put X_PROVIDER_API_KEY in .env first.")
        return
    src = XSource(st)
    signals = await src.fetch()
    print(f"X source returned {len(signals)} signal(s)")
    for s in signals[:6]:
        handle = s.founders[0].handle if s.founders else "?"
        print(f"  - @{handle} | {s.description[:80]}")
        print(f"      link: {s.url}")


asyncio.run(main())
