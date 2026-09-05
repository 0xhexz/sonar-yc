"""Live probe of the free-X discovery+hydration chain ($0, no paid quota used)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.sources.x_free import fetch_free_x, hydrate


async def main() -> None:
    st = get_settings()

    # 1. Syndication hydration on the brief's reference post.
    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        post = await hydrate(http, "2061493360150601738")
    if post:
        print(f"HYDRATION OK  @{post['handle']}: {post['text'][:60]}... (likes={post['likes']})")
    else:
        print("HYDRATION FAILED — syndication endpoint returned nothing")

    # 2. Full free chain over our configured keywords.
    sigs = await fetch_free_x(st)
    print(f"FREE CHAIN: {len(sigs)} hydrated YC-related signal(s)")
    for s in sigs[:5]:
        print(f"  @{s.founders[0].handle if s.founders else '?'} — {s.description[:70]!r}")


if __name__ == "__main__":
    asyncio.run(main())
