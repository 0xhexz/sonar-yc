"""Source registry.

New sources register here via a small amount of boilerplate. The cadence
(directory vs social) is encoded by a ``kind`` attribute so the loop can run
each source at an appropriate interval.
"""
from __future__ import annotations

from ..config import Settings
from .base import BaseSource
from .speedrun import SpeedrunSource
from .yc_directory import YC_DirectorySource
from .x_twitter import XSource
from .linkedin import LinkedInSource
from .hn import HNSource

# name -> (class, kind). kind in {'directory', 'social'}.
REGISTRY: dict[str, tuple[type[BaseSource], str]] = {
    "yc": (YC_DirectorySource, "directory"),
    "speedrun": (SpeedrunSource, "directory"),
    "x": (XSource, "social"),
    "linkedin": (LinkedInSource, "social"),
    "hn": (HNSource, "social"),
}


def get_source(name: str, settings: Settings) -> BaseSource:
    cls, _kind = REGISTRY[name]
    return cls(settings)


def source_kind(name: str) -> str:
    return REGISTRY[name][1]
