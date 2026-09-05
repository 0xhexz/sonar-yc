"""X fuel gauge — provider health ledger with automatic selection.

Instead of asking the user to pick an X data provider (a decision requiring
research they shouldn't have to do), the bot keeps a small ledger of how each
configured provider behaved last time and picks by itself:

    cost_rank:  free < sorsa < twitterapi.io < twtapi   (cheaper first)
    health:     ok / exhausted / broken / untried

Selection rule (deterministic):
    1. cheapest provider whose health is `ok`
    2. else any `untried` provider
    3. else the free discovery chain — always available, cost $0

After every fetch the caller records the outcome, so the ledger heals itself:
a provider that starts failing gets demoted, one that recovers gets promoted
back at its next opportunity.
"""
from __future__ import annotations

import logging

from .config import Settings
from .store import Store

logger = logging.getLogger("ycradar.xfuel")

# Cheapest first. The free chain is implicit and always last-resort available.
COST_ORDER: list[str] = ["sorsa", "twitterapi_io", "twtapi"]

_PROBE_KW = 1  # attempts before a provider is considered exhausted
_MIN_SIGNALS_OK = 5


def detect_provider(base_url: str | None) -> str | None:
    b = (base_url or "").lower()
    if "sorsa" in b:
        return "sorsa"
    if "twitterapi.io" in b:
        return "twitterapi_io"
    if "twtapi" in b:
        return "twtapi"
    return None


class FuelLedger:
    """Persisted per-provider outcomes, stored in the shared SQLite store."""

    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings

        def _get(attr: str) -> str | None:
            return getattr(settings, attr, None)

        base = _get("x_provider_base_url") or ""
        provider = detect_provider(base)
        self.providers: dict[str, str | None] = {
            "sorsa": _get("sorsa_api_key") if provider == "sorsa" else None,
            "twitterapi_io": _get("x_provider_api_key") if provider == "twitterapi_io" else None,
            "twtapi": _get("x_provider_api_key") if provider == "twtapi" else None,
        }

    # -- ledger I/O -----------------------------------------------------------
    def _state(self) -> dict[str, str]:
        import json

        raw = self.store.get_state("x_fuel_ledger") or "{}"
        try:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, state: dict[str, str]) -> None:
        import json

        self.store.set_state("x_fuel_ledger", json.dumps(state))

    # -- selection ------------------------------------------------------------
    def pick(self) -> str:
        """Return 'free' or the name of the provider to use this scan."""
        state = self._state()
        # cheapest healthy first
        for name in COST_ORDER:
            if not self.providers.get(name):
                continue
            health = state.get(name, "untried")
            if health == "ok":
                return name
        # nothing healthy? try anything untried
        for name in COST_ORDER:
            if self.providers.get(name) and state.get(name, "untried") == "untried":
                return name
        return "free"

    def record(self, name: str, signal_count: int) -> None:
        state = self._state()
        if name == "free":
            return
        if signal_count >= _MIN_SIGNALS_OK:
            state[name] = "ok"
        elif signal_count == 0:
            # zero could be a quiet day; only demote on repeat zero
            if state.get(name) == "zero":
                state[name] = "exhausted"
            else:
                state[name] = "zero"
        else:
            state[name] = "ok"
        self._save(state)
        logger.info("fuel ledger: %s -> %s", name, state[name])
