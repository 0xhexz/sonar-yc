"""Direct live probe: what X and LinkedIn actually return right now (current .env)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.sources.x_twitter import XSource
from app.sources.linkedin import LinkedInSource


async def main():
    st = get_settings()
    print("X_PROVIDER_BASE_URL:", st.x_provider_base_url)
    print("X is_x_ready:", st.is_x_ready)
    print("LinkedIn is_linkedin_ready:", st.is_linkedin_ready)
    print("=" * 50)
    print("▶ X SOURCE (real/current)")
    x = await XSource(st).fetch()
    print(f"   returned {len(x)} signals")
    for s in x[:6]:
        h = s.founders[0].handle if s.founders else "?"
        print(f"   - @{h} | {s.description[:70]}")
    print("=" * 50)
    print("▶ LINKEDIN SOURCE (current)")
    li = await LinkedInSource(st).fetch()
    print(f"   returned {len(li)} signals")
    for s in li[:6]:
        h = s.founders[0].handle if s.founders else "?"
        print(f"   - {h} | {s.description[:70]} | url:{s.url[:50]}")


asyncio.run(main())
