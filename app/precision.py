"""Precision gate — our own weighted scoring for founder-announcement posts.

Design (radar.yaml-driven, never hardcoded):

  * DISQUALIFIERS  — if any matches, the post dies immediately. These catch
    posts that contain a perfect acceptance phrase but are spoken by someone
    OTHER than the founder: congratulations, referrals, investor notes,
    anniversaries, advice threads. Scoring alone cannot catch these, because
    "Congrats to my friend who got into YC" scores beautifully on every
    positive signal.
  * WEIGHTED CUES  — announcement phrases carry weights (0.5-1.0). We take the
    STRONGEST match, not the sum: a post repeating the same idea three times
    must not out-score a clearer one.
  * CORROBORATION  — an explicit batch code or a founder voice (I/we/our, or
    an announcement opener like "excited to announce") adds on top.
  * PENALTIES      — no company name identifiable, or third-party framing.

Output: a score in [0, 1] plus the matched evidence, so the loop can route
posts: >= alert_floor -> alert, >= recap_floor -> recap, else drop.

This is deliberately independent of the LLM: it runs first (free), and the LLM
only sees posts that already cleared this gate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS_PATH = Path(__file__).resolve().parents[1] / "radar.yaml"

# Shipped defaults — radar.yaml overrides any of these sections.
DEFAULT_DISQUALIFIERS = [
    r"congratulations to",
    r"congrats\b",                        # someone else's good news (any form)
    r"proud of (my|our) (friend|brother|sister|cofounder|buddy)",
    r"shoutout to",
    r"\bhow (i|to) got? into y[cc]\b",
    r"how to get into y[cc]",
    r"tips for (yc|y combinator|applying)",
    r"application tips",
    r"apply(ing)? to (yc|y combinator)",
    r"\byc (application|resume|cv)\b",
    r"\b(ycombinator|yc)\s+alum",
    r"i invested (early )?in",
    r"\breferred\b",
    r"we (referred|introduced) .* to y[cc]",
    r"got referred",
    r"anniversary",
    r"one year ago",
    r"\b\d+\s+years? ago\b",
    r"years? ago (today|we|our)",
    r"thread:",
    r"recap of",
]
DEFAULT_CUES = [
    (r"got (into|in)(to)? y( ?c| combinator)", 1.0),
    (r"accepted (into|to|by) (yc|y combinator)", 1.0),
    (r"accepted into", 0.9),
    (r"backed by (yc|y combinator)", 0.9),
    (r"joining (the )?(yc|y combinator|speedrun)", 0.9),
    (r"(our|my) (startup|company) joined (yc|y combinator)", 0.95),
    (r"we'?re (now )?(a|in) (yc|y combinator)", 0.85),
    (r"funded by (yc|y combinator)", 0.85),
    (r"part of (the )?(yc|speedrun)", 0.7),
    (r"got into speedrun", 1.0),
    (r"a16z speedrun", 0.75),
    (r"speedrun batch", 0.8),
    (r"launch hn", 0.8),
]
FOUNDER_VOICE = re.compile(
    r"\b(i|we|my|our|me|mine|us)\b|\bi'?m\b|\bwe'?re\b|\bi'?ve\b"
    r"|excited to (announce|share)|thrilled to (announce|share)"
    r"|proud to (announce|share)|happy to share|big news|humbled",
    re.I,
)
SELF_ANNOUNCE = re.compile(
    # third-person company self-announcement: "Nebula Security is now backed by YC"
    r"^[^a-z]*[A-Z][\w.&'-]{1,24}(?:\s+[A-Z][\w.&'-]{1,20}){0,2}\s+"
    r"(?:is|has been|was)\s+(?:now\s+)?(?:backed|accepted|funded|part of)\b"
)
BATCH_RE = re.compile(r"\b(?:YC\s*)?([SWFX]\s?\d{2}|Speedrun\s*SR\d{3})\b", re.I)


@dataclass
class GateResult:
    score: float
    disqualified: bool = False
    reason: str = ""
    cues: list[str] = field(default_factory=list)
    batch: str | None = None
    company_hint: str | None = None


def _rules() -> dict:
    """Load radar.yaml overrides if present; else defaults.

    Merge semantics: yaml lists ADD to the shipped defaults (deduped), so the
    file tunes without accidentally amputating a protection. Scalar values
    (floors) replace outright.
    """
    try:  # yaml is optional — defaults already cover it
        import yaml  # type: ignore

        if DEFAULTS_PATH.exists():
            cfg = yaml.safe_load(DEFAULTS_PATH.read_text()) or {}
            gate = cfg.get("precision_gate") or {}
            merged: dict = {}
            if gate.get("disqualifiers"):
                seen = set()
                merged["disqualifiers"] = [
                    d
                    for d in (DEFAULT_DISQUALIFIERS + gate["disqualifiers"])
                    if not (d in seen or seen.add(d))
                ]
            if gate.get("cues"):
                base = {p: w for p, w in DEFAULT_CUES}
                base.update({p: float(w) for p, w in gate["cues"]})
                merged["cues"] = list(base.items())
            floors = gate.get("floors") or {}
            if floors:
                merged["floors"] = floors
            return {"precision_gate": merged} if merged else {}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def run_gate(text: str) -> GateResult:
    rules = _rules().get("precision_gate", {})
    dis_patterns = [re.compile(p, re.I) for p in rules.get("disqualifiers", DEFAULT_DISQUALIFIERS)]
    cue_list = [
        (re.compile(p, re.I), w) for p, w in rules.get("cues", DEFAULT_CUES)
    ]
    floors = rules.get("floors", {})
    alert_floor = float(floors.get("alert", 0.55))
    # recap floor is informational here; the loop uses it to route

    t = (text or "").strip()
    if not t:
        return GateResult(0.0, True, "empty")
    low = t.lower()

    # 1) Hard disqualifiers — someone else speaking.
    for p in dis_patterns:
        m = p.search(low)
        if m:
            return GateResult(0.0, True, f"disqualified by /{p.pattern[:24]}/")

    # 2) Strongest cue wins (not the sum).
    best, cues = 0.0, []
    for p, w in cue_list:
        if p.search(low):
            cues.append(p.pattern[:26])
            best = max(best, w)
    if best == 0.0:
        return GateResult(0.0, False, "no announcement cue", cues)

    score = best

    # 3) Corroboration.
    m = BATCH_RE.search(t)
    if m:
        score += 0.12
        gate_batch = m.group(1).upper().replace(" ", "")
    else:
        gate_batch = None

    has_voice = bool(FOUNDER_VOICE.search(low)) or bool(SELF_ANNOUNCE.match(t))
    if has_voice:
        score += 0.08
    else:
        # No founder voice at all = someone reporting on a company. This is a
        # rejection, not a penalty — news write-ups pass every phrase check.
        return GateResult(min(score, 0.4), True, "no founder voice (third-party report)", cues, gate_batch)

    # 4) Penalty: no identifiable company name (conservative heuristic).
    if not _company_hint(t):
        score -= 0.15

    return GateResult(min(round(score, 3), 1.0), False, "", cues, gate_batch, _company_hint(t))


def _company_hint(text: str) -> str | None:
    """Conservative company-name hint: 'X (YC S26)' or 'X is/has been backed'."""
    m = re.search(r"\b([A-Z][\w.&'-]{1,24}(?:\s+[A-Z][\w.&'-]{1,20}){0,2})\s*\(\s*(?:YC|a16z)", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z][\w.&'-]{1,24}(?:\s+[A-Z][\w.&'-]{1,20}){0,2})\s+(?:is|has been|was)\s+(?:now\s+)?(?:backed|accepted|funded)", text)
    return m.group(1) if m else None


def route(score: float, disqualified: bool, floors: dict | None = None) -> str:
    """Route a gated post: 'alert' | 'recap' | 'drop'."""
    rules = floors or _rules().get("precision_gate", {}).get("floors", {})
    alert_floor = float(rules.get("alert", 0.55))
    recap_floor = float(rules.get("recap", 0.35))
    if disqualified:
        return "drop"
    if score >= alert_floor:
        return "alert"
    if score >= recap_floor:
        return "recap"
    return "drop"
