"""Foreign-exchange rates for cross-currency rebate comparison.

CRITICAL: Producers compare across currencies and a 5% FX swing changes
decisions. Every estimate rendered in a currency other than the program's
local currency MUST carry the FX rate it used, the as-of date, and a hard
staleness flag when the rate is older than 24 hours. The breakdown
contract enforces this — see api/breakdown.py.

This module is a hand-maintained stub. The rates table below is illustrative;
do not use the numbers in production decisions until this module is wired
to a real FX feed. The shape of `lookup()` is what downstream consumers
depend on, and the migration to a real feed leaves that shape unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final

from api.breakdown import FxInfo


# Units of USD per 1 unit of the source currency. Illustrative — replace
# with a real feed before relying on these. Updated 2026-05-12 by hand from
# a public reference; intent is to keep the framework testable, not to be
# market-correct.
_RATES_TO_USD: Final[dict[str, float]] = {
    "USD": 1.00,
    "GBP": 1.27,
    "NZD": 0.60,
    "AUD": 0.67,
    "EUR": 1.09,
    "CAD": 0.73,
}
_RATES_AS_OF: Final[date] = date(2026, 5, 12)
_STALE_AFTER_HOURS: Final[int] = 24


@dataclass(frozen=True)
class _Rate:
    """Internal representation: one currency-pair rate with provenance."""

    rate: float
    as_of: date


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def is_stale(as_of: date, *, now: datetime | None = None) -> bool:
    """Return True if the rate is older than the staleness threshold."""
    now = now or _now()
    age_hours = (now - datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)).total_seconds() / 3600.0
    return age_hours > _STALE_AFTER_HOURS


def lookup(source_currency: str, target_currency: str = "USD") -> FxInfo:
    """Get FX rate from `source_currency` to `target_currency`.

    Returns an FxInfo with the rate, as-of date, and a `fx_stale` flag set
    when the rate is older than 24 hours. Raises KeyError when either
    currency is unknown — the caller is expected to handle the missing-rate
    case explicitly (typically by skipping the USD conversion rather than
    silently passing zero).
    """
    src = source_currency.upper()
    tgt = target_currency.upper()
    if src == tgt:
        return FxInfo(fx_rate=1.0, fx_as_of=_RATES_AS_OF, fx_stale=is_stale(_RATES_AS_OF))
    if src not in _RATES_TO_USD:
        raise KeyError(f"No FX rate registered for source currency {src!r}")
    if tgt not in _RATES_TO_USD:
        raise KeyError(f"No FX rate registered for target currency {tgt!r}")
    # Triangulate through USD: src_to_usd / tgt_to_usd = src_to_tgt
    src_to_usd = _RATES_TO_USD[src]
    tgt_to_usd = _RATES_TO_USD[tgt]
    rate = src_to_usd / tgt_to_usd
    return FxInfo(fx_rate=rate, fx_as_of=_RATES_AS_OF, fx_stale=is_stale(_RATES_AS_OF))
