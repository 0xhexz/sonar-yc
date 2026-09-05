"""Precision harness — measure the gate, don't just claim it.

Runs our precision gate (regex/disqualifier layer, no LLM) against a
hand-labelled case pack of REAL announcement shapes and the traps that fool
keyword matching. Prints precision / recall / F1 and exits non-zero below a
floor, so it can gate a release.

    .venv/bin/python scripts/measure_precision.py [--min-f1 0.85]

Labels are OURS: each case states what a GTM operator would want routed
where. Add cases as we meet them in the wild — the pack is the spec.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.precision import route, run_gate  # noqa: E402

# (text, expected_route)  — expected is 'alert' | 'drop'
# 'recap' counts as correct for both alert (usable signal) and drop (borderline)
CASES: list[tuple[str, str]] = [
    # --- genuine founder announcements (want: alert) ---
    ("big news: i got into Y Combinator. solo founder, on my 4th attempt.", "alert"),
    ("We got into YC S26! Excited to move to SF and start building.", "alert"),
    ("Adalat AI is now backed by Y Combinator. We're the first nonprofit in the batch.", "alert"),
    ("We're in YC S26 as a solo founder — come build with us!", "alert"),
    ("Nebula Security is now backed by Y Combinator", "alert"),
    ("thrilled to announce we got into Y Combinator S26!", "alert"),
    ("after 3 applications, we finally got into the Speedrun batch (SR006)", "alert"),
    ("humbled to share our startup joined Y Combinator this summer", "alert"),
    # --- traps that fool keyword search (want: drop) ---
    ("8 startups I referred got into YC", "drop"),
    ("exactly one year ago today, we got into Y Combinator", "drop"),
    ("I invested early into a startup that was recently accepted into YC", "drop"),
    ("Congrats to my friend who got into YC!", "drop"),
    ("Congratulations to the founders of Acme on getting into YC", "drop"),
    ("How I got into YC: my application tips and timeline", "drop"),
    ("YC application opens soon — here's how to get into YC", "drop"),
    ("Y Combinator is the USAID of Silicon Valley", "drop"),
    ("My application video for Y Combinator (feedback welcome)", "drop"),
    ("YC alumni are founding the next wave of AI companies", "drop"),
    ("TechCrunch: two YC startups raised Series A this week", "drop"),
    ("Shoutout to my cofounder — we got rejected from YC but shipped anyway", "drop"),
]


def main() -> int:
    tp = fp = tn = fn = 0
    failures: list[str] = []
    for text, want in CASES:
        g = run_gate(text)
        got = route(g.score, g.disqualified)
        # treat 'recap' as alert-side (it reached a human), drop as drop-side
        got_side = "drop" if got == "drop" else "alert"
        if want == "alert" and got_side == "alert":
            tp += 1
        elif want == "alert":
            fn += 1
            failures.append(f"MISSED  (recall): {text[:64]!r} -> {got} ({g.score:.2f})")
        elif got_side == "alert":
            fp += 1
            failures.append(f"FALSE+ (precision): {text[:64]!r} -> {got} ({g.score:.2f})")
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print(f"cases: {len(CASES)}  (positives {tp + fn}, negatives {fp + tn})")
    print(f"precision: {precision:.0%}   recall: {recall:.0%}   f1: {f1:.0%}")
    for f in failures:
        print(" ", f)

    min_f1 = 0.85
    if "--min-f1" in sys.argv:
        min_f1 = float(sys.argv[sys.argv.index("--min-f1") + 1])
    if f1 < min_f1:
        print(f"\nFAIL: f1 {f1:.0%} below floor {min_f1:.0%}")
        return 1
    print(f"\nPASS: f1 {f1:.0%} >= floor {min_f1:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
