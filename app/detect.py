"""Early-detection classification.

This is pure logic (no I/O). It decides whether an observed company/founder
signal is an EARLY signal (founder announced on social *before* the
accelerator confirmed it), a CONFIRMED (already in a directory), or a
SPEEDRUN company, or neither.

The directory "key sets" passed in are built from the *fresh* directory
listing for the current cycle (see ``app.loop``). Each set contains slugs,
normalised names, and founder handles so a social post can be matched even
when it mentions only a founder handle or a company name.
"""
from __future__ import annotations

from .models import CompanySignal, slugify, EARLY, CONFIRMED, SPEEDRUN


def signal_identities(signal: CompanySignal) -> set[str]:
    """All comparable identifiers for a signal: slug, name, founder handles."""
    ids: set[str] = set()
    if signal.slug:
        ids.add(signal.slug.strip().lower())
    if signal.name:
        ids.add(slugify(signal.name))
        ids.add(signal.name.strip().lower())
    for f in signal.founders:
        k = f.lookup_key
        if k:
            ids.add(k)
    return ids


def _match(identities: set[str], key_set: set[str]) -> str | None:
    for ident in identities:
        if ident in key_set:
            return ident
    return None


def _batch_is_current(batch: str | None) -> bool:
    """Is this cohort one the accelerator is announcing around now?

    An old batch means the company was accepted years ago, so its absence
    from the directory says nothing about the accelerator being slow to
    publish — a 2011 announcement must never fire a 2026 early alert.
    Unrecognisable strings count as current: guessing wrong here silences
    real signals.
    """
    import datetime as _dt
    import re as _re

    text = (batch or "").upper()
    now = _dt.date.today().year
    m = _re.search(r"(20\d{2})", text)
    if m:
        return abs(int(m.group(1)) - now) <= 1
    m = _re.search(r"\b[WSFX]\s?(\d{2})\b", text)
    if m:
        return abs((2000 + int(m.group(1))) - now) <= 1
    m = _re.search(r"\bSR\s?0*?(\d{1,3})\b", text)
    if m:
        return True  # Speedrun cohorts are all recent
    return True


def classify(
    signal: CompanySignal,
    yc_keys: set[str],
    speedrun_keys: set[str],
) -> str:
    """Classify a signal into EARLY / CONFIRMED / SPEEDRUN.

    * A signal sourced from the YC directory itself is always CONFIRMED.
    * A signal sourced from the Speedrun directory is always SPEEDRUN.
    * A *social* signal (X/LinkedIn/HN) is CONFIRMED/SPEEDRUN if its identity
      is already present in the relevant directory, otherwise EARLY — i.e.
      the founder announced it before the accelerator did.
    * An announcement about a long-closed batch is downgraded to CONFIRMED
      (it was published long ago; the directory just no longer highlights it).
    """
    identities = signal_identities(signal)

    if signal.source == "yc":
        return CONFIRMED
    if signal.source == "speedrun":
        return SPEEDRUN

    # Social sources:
    if _match(identities, yc_keys):
        return CONFIRMED
    if _match(identities, speedrun_keys):
        return SPEEDRUN
    batch = getattr(signal, "batch", None)
    if batch and not _batch_is_current(batch):
        # Old-cohort announcement: real, but not news about "before YC".
        return CONFIRMED
    return EARLY
