"""Per-keyword X scan: runs all 7 keywords one by one, shows counts + sample hits."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.sources._provider_base import post_json, extract_items, text_of
from app.sources.x_twitter import XSource, _is_sorsa


async def main():
    st = get_settings()
    assert _is_sorsa(st.x_provider_base_url), "expected Sorsa provider"
    base = st.x_provider_base_url.rstrip("/")
    total = 0
    for i, kw in enumerate(st.x_keyword_list, 1):
        phrase = f'"{kw}"' if " " in kw else kw
        query = f"{phrase} lang:{st.x_lang}"
        payload = await post_json(
            f"{base}/v3/search-tweets",
            st.x_provider_api_key,
            body={"query": query, "order": "latest", "limit": 30},
            timeout=st.http_timeout,
            auth_header="ApiKey",
        )
        items = extract_items(payload)
        texts = [text_of(it) for it in items if text_of(it)]
        non_rt = [t for t in texts if not t.strip().startswith("RT ")]
        total += len(non_rt)
        sample = (non_rt[0][:70].replace("\n", " ") if non_rt else "—")
        print(f"{i}/7 | {kw!r:28} -> {len(items):2} raw | {len(non_rt):2} kept | e.g. {sample}")
        await asyncio.sleep(0.4)
    print(f"TOTAL kept: {total}")

asyncio.run(main())
