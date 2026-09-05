"""LLM intent classification for social signals (ChirpSieve-inspired).

The brief asks for *early* detection: a founder personally announcing their own
acceptance into YC/Speedrun BEFORE the accelerator announced them. Raw keyword
search returns a lot of noise (news, criticism, job posts, friends discussing
YC). This module classifies each social signal into:

* ``founder_announcement`` — the author says THEY / their own company got into
  YC or a16z Speedrun → the 🔥 EARLY signal we chase.
* ``founder_applied`` — the author applied / is waiting (not acceptance yet).
* ``third_party`` — news, press, jobs, politics, or commentary about YC.
* ``unrelated`` — not about YC/Speedrun acceptance at all.

It mirrors ChirpSieve's two-step pattern: a cheap heuristic pre-filter, then one
provider-agnostic LLM call (OpenAI-compatible: OpenAI / Groq / OpenRouter /
Ollama) that returns strict JSON. JSON extraction is tolerant (marks only
plain-text output) so it works with providers that don't support Structured
Outputs, and failures fall back gracefully to no-classification.

The output fields are exactly what the brief's Example 1 alert needs:
company name, founder handle, batch, confidence, and a one-line "why".
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from .config import Settings

logger = logging.getLogger("ycradar.classifier")

# ---- Label constants --------------------------------------------------------
FOUNDER_ANNOUNCEMENT = "founder_announcement"
FOUNDER_APPLIED = "founder_applied"
THIRD_PARTY = "third_party"
UNRELATED = "unrelated"

# Cheap heuristic drop rules (no LLM call).
_MIN_CONTENT = 20
SPAM_PATTERNS = [
    r"^(rt\s+)", r"\b(link in bio|promo code|subscribe now|dm me for price|only today)\b",
]


def looks_like_founder_signal(text: str) -> bool:
    """Cheap, high-precision gate — True only if the text plausibly reports a
    founder's own acceptance (avoids paying an LLM call for news/criticism).

    Requires BOTH an acceptance verb AND a YC/Speedrun mention within ~60 chars
    of each other — so "got into a fight" or "we're in the US and YC is great"
    don't match, but "i got into YC" / "we're in YC S26" / "backed by Speedrun" do.
    """
    if not text:
        return False
    t = text.lower()
    yc_positions = [m.start() for m in re.finditer(r"y\s?c\b|y combinator|speedrun|a16z", t)]
    verb_positions = [
        m.start()
        for m in re.finditer(
            r"got (into|in|accepted)|accepted|admitted|invited|selected|backed by|made it|we\s*are\s*in|we'?re\s*in|joined\s+the",
            t,
        )
    ]
    if not yc_positions or not verb_positions:
        return False
    return any(abs(y - v) <= 60 for y in yc_positions for v in verb_positions)

CLASSIFY_SYSTEM_PROMPT = """\
You are a precise analyst for a YC/Speedrun early-detection bot. You read
Twitter/X or LinkedIn posts and decide whether the AUTHOR is personally
announcing that THEY or their own company just got accepted into Y Combinator
or a16z Speedrun — i.e. the founder's own announcement, before the accelerator
officially announced them.

Classify each post into EXACTLY ONE of:
- "founder_announcement": the author says they/their own company GOT IN (e.g.
  "i got into YC!", "we're in YC S26", "so proud to say we're backed by Y
  Combinator", "we got into the Speedrun batch"). Gold signal.
- "founder_applied": author applied, made an application, is waiting/aspiring
  ("just applied", "application video", "hoping to get in").
- "third_party": news, press, job posts, criticism, politics, or comments ABOUT
  YC by someone who is not announcing their own acceptance (e.g. "Y Combinator
  is ...", "YC W19's X just...", "hiring at a YC startup").
- "unrelated": does not concern YC/Speedrun acceptance at all.

Return ONLY a JSON ARRAY with EXACTLY these keys per object:
{"id":"<id>","label":"founder_announcement|founder_applied|third_party|unrelated","company_name":"<if identifiable else "">","batch":"<S26 or SR005 if mentioned else "">","confidence":0.0,"reasoning":"<1 sentence>"}

Example: [{"id":"1","text":"big news: i got into Y Combinator!","author":"bek"}] ->
[{"id":"1","label":"founder_announcement","company_name":"","batch":"","confidence":0.98,"reasoning":"author announces own acceptance"}]
"""


@dataclass
class FounderAnalysis:
    id: str = ""
    label: str = UNRELATED
    company_name: str = ""
    batch: str = ""
    confidence: float = 0.0
    reasoning: str = ""

    @property
    def is_founder_announcement(self) -> bool:
        return self.label == FOUNDER_ANNOUNCEMENT

    @property
    def acceptable(self) -> bool:
        return self.label in (FOUNDER_ANNOUNCEMENT, FOUNDER_APPLIED)


# ---- tolerant JSON helpers --------------------------------------------------
def clean_json(text: str) -> any:
    """Strip code fences / surrounding prose and parse the first JSON value."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    # Find the outermost JSON array or object.
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start = t.find(open_c)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start : i + 1])
                    except Exception:
                        return None
    return None


def pre_filter(text: str) -> bool:
    """Cheap heuristics — True = drop before calling the LLM."""
    if not text or len(text.strip()) < _MIN_CONTENT:
        return True
    for pat in SPAM_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False


async def _chat(settings: Settings, messages: list[dict]) -> str:
    """One chat-completions call to any OpenAI-compatible endpoint, with a
    small retry/backoff for transient 429/5xx provider throttling."""
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key or 'none'}"}
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=settings.classify_timeout,
                                         follow_redirects=True) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"provider throttling ({resp.status_code})", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code == 400:
                # 400 = request rejected; retrying won't help
                break
            await asyncio.sleep(2 * (attempt + 1))
    raise last_exc or RuntimeError("LLM call failed")


def parse_batch(text: str, batch_ids: list[str]) -> dict[str, FounderAnalysis]:
    """Parse an LLM JSON-array response into id -> FounderAnalysis.

    Tolerates schema drift from schema-lax models: accepts ``label`` or alias
    field names (``classification`` / ``type`` / ``category``), ``company`` /
    ``company_name``, ``confidence`` / ``score``, and ``reasoning`` / ``reason``.
    Values like ``startup_related`` / ``achievement`` map to ``third_party``
    (they mention YC but are not a founder's own announcement).
    """
    result: dict[str, FounderAnalysis] = {}
    parsed = clean_json(text) or []
    if not isinstance(parsed, list):
        return result
    by_id = {str(b): b for b in batch_ids}
    label_aliases = {
        "founder_announcement": FOUNDER_ANNOUNCEMENT,
        "announcement": FOUNDER_ANNOUNCEMENT,
        "founder": FOUNDER_ANNOUNCEMENT,
        "founder_applied": FOUNDER_APPLIED,
        "applied": FOUNDER_APPLIED,
        "application": FOUNDER_APPLIED,
        "third_party": THIRD_PARTY,
        "news": THIRD_PARTY,
        "commentary": THIRD_PARTY,
        "unrelated": UNRELATED,
        "irrelevant": UNRELATED,
        # Schema-lax models often return these for YC-mention posts:
        "startup_related": THIRD_PARTY,
        "achievement": THIRD_PARTY,
        "positive": UNRELATED,
    }
    for item in parsed:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id", ""))
        if iid not in by_id:
            continue
        raw_label = (
            item.get("label")
            or item.get("classification")
            or item.get("type")
            or item.get("category")
            or UNRELATED
        )
        label = label_aliases.get(str(raw_label).lower().strip(), UNRELATED)
        try:
            confidence = float(item.get("confidence", item.get("score", 0.0)) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        result[iid] = FounderAnalysis(
            id=iid,
            label=label,
            company_name=item.get("company_name") or item.get("company") or "",
            batch=item.get("batch") or item.get("cohort") or "",
            confidence=confidence,
            reasoning=item.get("reasoning") or item.get("reason") or item.get("explanation") or "",
        )
    return result


async def classify_batch(settings: Settings, items: list[dict]) -> dict[str, FounderAnalysis]:
    """Classify a batch of social items (each: {'id', 'text', 'author'}).

    Micro-batches by ``classify_batch_size`` (matching ChirpSieve's approach) so a
    single request never swamps the model. Returns id -> FounderAnalysis for
    everything that survived the pre-filter. If the LLM call fails, returns {} —
    the caller falls back (never crashes the scan).
    """
    analysis: dict[str, FounderAnalysis] = {}
    if not settings.llm_api_key or not settings.classify_enabled:
        return analysis

    to_ask = [
        it
        for it in items
        if not pre_filter(it.get("text", "")) and looks_like_founder_signal(it.get("text", ""))
    ]
    if not to_ask:
        return analysis

    batch_size = max(1, settings.classify_batch_size)
    failed_once = False
    for start in range(0, len(to_ask), batch_size):
        if failed_once:
            # Provider rejected/failed a chunk — give up for THIS scan (the
            # caller's regex gate covers precision); next scan will retry.
            logger.warning("skipping remaining classification chunks this scan")
            break
        chunk = to_ask[start : start + batch_size]
        payload = [
            {"id": it.get("id", ""), "text": it.get("text", ""), "author": it.get("author", "")}
            for it in chunk
        ]
        user = "Analyze these posts:\n" + json.dumps(payload, ensure_ascii=False)
        try:
            content = await _chat(
                settings,
                [
                    {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
            )
            chunk_analysis = parse_batch(content, [it["id"] for it in chunk])
            analysis.update(chunk_analysis)
        except Exception as exc:  # noqa: BLE001
            failed_once = True
            logger.warning("LLM classification failed for chunk %d (falling back to regex): %s", start // batch_size, exc)
        await asyncio.sleep(0.4)
    return analysis
