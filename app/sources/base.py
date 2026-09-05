"""Base source abstraction."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings
    from ..models import CompanySignal


class BaseSource(ABC):
    """A pluggable monitor source.

    Subclasses implement ``fetch()`` returning a list of normalised
    ``CompanySignal`` objects. New platforms (Reddit, ProductHunt, News…)
    only need a new subclass plus registration — no core changes.
    """

    name: str = "base"

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        """Whether this source can run right now (e.g. required key present)."""
        return True

    @abstractmethod
    async def fetch(self) -> list["CompanySignal"]:
        """Return fresh signals. Should never raise — on failure return [] and
        let the caller log a coverage gap (see ``app.loop``)."""
        raise NotImplementedError
