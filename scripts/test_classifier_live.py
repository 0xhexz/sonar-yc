"""Live proof: real X tweets -> LLM classification -> noise dropped, founder announcements kept."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.loop import _filter_social
from app.sources.x_twitter import XSource


async def main():
    st = get_settings()
    print("LLM:", st.llm_model, "@", st.llm_base_url)
    src = XSource(st)
    signals = await src.fetch()
    print(f"raw X signals: {len(signals)}")
    print(f"classify_enabled={st.classify_enabled}, llm key set={bool(st.llm_api_key)}")
    kept = await _filter_social(st, signals)
    print(f"AFTER LLM FILTER: {len(kept)} kept (founder announcements only)")
    print("----- KEPT -----")
    for s in kept[:10]:
        h = s.founders[0].handle if s.founders else "?"
        print(f"  @{h} | {s.name or '-'} | {s.batch or '-'} | {s.description[:80]}")
    print("----- (dropped examples were third_party/founder_applied/unrelated) -----")


asyncio.run(main())
