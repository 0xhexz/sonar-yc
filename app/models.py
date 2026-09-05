"""Domain models for YC Radar.

Keep these pure dataclasses (no I/O). They are the contract between the
source adapters, the detection pipeline, and the Slack formatter.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import re


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    """Normalise an arbitrary company name into a comparable slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@dataclass
class Founder:
    """A founder referenced by a company or a social signal."""
    handle: str | None = None  # @x handle or LinkedIn handle
    name: str | None = None
    url: str | None = None

    @property
    def lookup_key(self) -> str:
        return (self.handle or self.name or "").lower().replace("@", "").strip()


@dataclass
class CompanySignal:
    """A normalised company/founder observation from one source."""
    source: str  # 'yc' | 'speedrun' | 'x' | 'linkedin'
    name: str
    slug: str = ""
    batch: str | None = None  # e.g. 'YC S26' or 'SR005'
    description: str = ""
    founders: list[Founder] = field(default_factory=list)
    website_url: str = ""
    x_url: str = ""
    linkedin_url: str = ""
    city: str = ""
    country: str = ""
    industries: list[str] = field(default_factory=list)
    url: str = ""  # canonical profile URL for this company
    detected_at: datetime = field(default_factory=utcnow)

    @property
    def dedup_key(self) -> str:
        """Stable identity used for de-duplication.

        Uses slug when available, otherwise a normalised name. Prefix with the
        source so the same company seen on X vs YC isn't cross-collapsed unless
        we want it to be — for alert purposes we DO want to collapse by company
        identity, but we keep a source element to allow "confirmed" vs "early"
        upgrades. The detection layer handles that; this key is just raw identity.
        """
        base = self.slug or slugify(self.name)
        return f"{self.source}:{base}"


@dataclass
class Alert:
    """A formatted alert destined for Slack."""
    classification: str  # 'EARLY' | 'CONFIRMED' | 'SPEEDRUN' | 'DIRECTORY'
    company_name: str
    batch: str | None
    source: str
    description: str = ""
    founder: Founder | None = None
    link: str = ""
    detected_at: datetime = field(default_factory=utcnow)
    #: Slack message ts of the original post — set after delivery so a later
    #: CONFIRMED upgrade can reply in the same thread.
    thread_ts: str | None = None
    upgraded: bool = False

    @property
    def detected_display(self, tz: str | None = None) -> str:
        return self.detected_at.strftime("%b %-d, %Y, %-I:%M %p")

    def to_dict(self) -> dict:
        return asdict(self)


# Classification constants
EARLY = "EARLY"
CONFIRMED = "CONFIRMED"
SPEEDRUN = "SPEEDRUN"
DIRECTORY = "DIRECTORY"
DUPLICATE = "DUPLICATE"
