"""Quick TwtAPI key check — is the new key alive and what does one call return?"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.sources._provider_base import get_json


async def main() -> None:
    st = get_settings()
    print("provider:", st.x_provider_base_url[:60])
    payload = await get_json(
        st.x_provider_base_url,
        st.x_provider_api_key,
        timeout=30,
        params={
            "q": '"got into YC"',
            "count": 20,
            "sort": "recency",
        },
    )
    if payload is None:
        print("call FAILED (auth/limit/network) — see log line above")
        return
    if isinstance(payload, dict) and payload.get("code"):
        print("API error payload:", str(payload)[:200])
        return
    # recursive tweet count
    def count_tweets(node):
        n = 0
        if isinstance(node, dict):
            if "full_text" in node or ("text" in node and "id_str" in node):
                n += 1
            for v in node.values():
                n += count_tweets(v)
        elif isinstance(node, list):
            for v in node:
                n += count_tweets(v)
        return n

    print("one call OK — tweets found:", count_tweets(payload))


if __name__ == "__main__":
    asyncio.run(main())
