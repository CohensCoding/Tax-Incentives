"""Calibration tests against real-world producer budgets.

Each test pins the engine's output against a line item from a real
production budget. If the math drifts, the test fails loudly with the
delta vs ground truth — that's the producer-credibility check.

When a calibration assumption needs updating (FX rates change, fee
estimates change, source budget revises), update the constants at the
top of the test and document the reason in the commit message.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from api.breakdown import Money
from api.query import DEFAULT_DB, estimate_rebate


# ─── Calibration inputs and ground truth ────────────────────────────────

# Jeff's "Hells Angels" NZ International budget line (account 6817-area).
# The line shows the combined 25% rebate (20% base + 5% uplift) on
# qualifying spend, with Cashet's 3% monetization discount, and the
# combined filing + audit fees. Numbers below are derived from the
# documented line totals; the test allows a 2% tolerance to absorb FX
# precision and fee-estimate looseness.
HELLS_ANGELS_QUALIFYING_USD = 39_393_672.0   # back-derived: $9,848,418 / 0.25
HELLS_ANGELS_GROSS_USD_TARGET = 9_848_418.0  # the budget line we calibrate to
HELLS_ANGELS_MONETIZATION_DISCOUNT_PCT = 3.0  # Cashet standard
HELLS_ANGELS_FILING_FEES_NZD = 75_000.0       # combined filing + audit; tunable
HELLS_ANGELS_TOLERANCE = 0.02                 # ±2%


# ─── Helpers ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def program_ids() -> dict[str, int]:
    if not Path(DEFAULT_DB).exists():
        pytest.skip(f"Database not present at {DEFAULT_DB}; run scripts.load first.")
    con = sqlite3.connect(DEFAULT_DB)
    ids = {row[1]: row[0] for row in con.execute(
        "SELECT id, program_name FROM incentive_programs"
    )}
    con.close()
    return ids


# ─── Hells Angels: NZ International 25% (20% base + 5% uplift) ──────────

def test_hells_angels_nz_25pct_gross_within_tolerance(program_ids):
    """The sum of base + uplift gross rebates (in USD) at the documented
    qualifying spend must land within ±2% of Jeff's $9,848,418 line.

    Run mode: multi-currency input (`qualifying_spend_by_currency={"USD": ...}`)
    so the FX conversion goes through the same path producers will use
    when their budget is in USD but the rebate program is NZD.
    """
    base_id = program_ids[
        "NZSPR International — Live Action Production Rebate (20%)"
    ]
    uplift_id = program_ids[
        "NZSPR International — Production Rebate 5% Uplift"
    ]

    base = estimate_rebate(
        base_id,
        qualifying_spend_by_currency={"USD": HELLS_ANGELS_QUALIFYING_USD},
        fx_target="USD",
    )
    uplift = estimate_rebate(
        uplift_id,
        qualifying_spend_by_currency={"USD": HELLS_ANGELS_QUALIFYING_USD},
        fx_target="USD",
    )

    combined_usd = (base.estimate_usd or 0.0) + (uplift.estimate_usd or 0.0)
    delta = abs(combined_usd - HELLS_ANGELS_GROSS_USD_TARGET) / HELLS_ANGELS_GROSS_USD_TARGET
    assert delta < HELLS_ANGELS_TOLERANCE, (
        f"NZ 25% gross rebate calibration drift: "
        f"got USD {combined_usd:,.2f}, target USD {HELLS_ANGELS_GROSS_USD_TARGET:,.2f}, "
        f"delta {delta * 100:+.2f}% (tolerance ±{HELLS_ANGELS_TOLERANCE * 100:.1f}%). "
        "If FX rates or qualifying-spend inputs are intentionally changed, "
        "update the constants at the top of tests/test_calibration.py and note "
        "the reason in the commit message."
    )


def test_hells_angels_cash_today_with_discount_and_fees(program_ids):
    """Layer the Cashet 3% monetization discount and NZ$75K combined
    filing/audit fees on top of the gross. Verifies that the post-rate
    adjustments compose correctly with the cross-currency conversion.

    Cash-today should be approximately:
      gross × (1 - 0.03) - filing_fees_in_NZD_converted_to_USD
    """
    base_id = program_ids[
        "NZSPR International — Live Action Production Rebate (20%)"
    ]
    uplift_id = program_ids[
        "NZSPR International — Production Rebate 5% Uplift"
    ]
    # Split the NZ$75K combined fees evenly across the two programs so
    # both estimates carry their share (in a real budget the fee is a
    # single line — splitting here is a modelling convenience).
    half_fees = Money(amount=HELLS_ANGELS_FILING_FEES_NZD / 2.0, currency="NZD")

    estimates = []
    for pid in (base_id, uplift_id):
        estimates.append(estimate_rebate(
            pid,
            qualifying_spend_by_currency={"USD": HELLS_ANGELS_QUALIFYING_USD},
            monetization_discount_pct=HELLS_ANGELS_MONETIZATION_DISCOUNT_PCT,
            filing_fees=half_fees,
            fx_target="USD",
        ))

    # Each estimate's cash_today is in program currency (NZD). Convert
    # via the estimate's own FX info to keep provenance consistent.
    combined_cash_usd = 0.0
    for est in estimates:
        assert est.cash_today is not None, "cash_today must be populated when discount/fees set"
        assert est.fx is not None
        combined_cash_usd += est.cash_today * est.fx.fx_rate

    # Expected: 9,848,418 × 0.97 - $45K (NZ$75K @ 0.60) ≈ $9,508,166
    gross_usd_target = HELLS_ANGELS_GROSS_USD_TARGET
    discount_factor = 1.0 - (HELLS_ANGELS_MONETIZATION_DISCOUNT_PCT / 100.0)
    # FX-convert the NZD fees through the same stub rate the function uses.
    from api import fx as fx_mod
    fees_usd = HELLS_ANGELS_FILING_FEES_NZD * fx_mod.lookup("NZD", "USD").fx_rate
    expected_cash_usd = gross_usd_target * discount_factor - fees_usd

    delta = abs(combined_cash_usd - expected_cash_usd) / expected_cash_usd
    assert delta < HELLS_ANGELS_TOLERANCE, (
        f"NZ 25% cash-today calibration drift: "
        f"got USD {combined_cash_usd:,.2f}, expected USD {expected_cash_usd:,.2f}, "
        f"delta {delta * 100:+.2f}% (tolerance ±{HELLS_ANGELS_TOLERANCE * 100:.1f}%)."
    )


def test_hells_angels_producer_summary_format(program_ids):
    """The producer view's top-sheet line for the combined NZ 25% rebate
    must be paste-ready for a budget line. Verifies label, modifiers,
    and currency formatting against the documented Jeff-budget style."""
    base_id = program_ids[
        "NZSPR International — Live Action Production Rebate (20%)"
    ]
    est = estimate_rebate(
        base_id,
        qualifying_spend_by_currency={"USD": HELLS_ANGELS_QUALIFYING_USD},
        monetization_discount_pct=HELLS_ANGELS_MONETIZATION_DISCOUNT_PCT,
        filing_fees=Money(amount=HELLS_ANGELS_FILING_FEES_NZD, currency="NZD"),
        output_view="producer",
        fx_target="USD",
    )
    assert est.producer_summary is not None
    line = est.producer_summary.top_sheet_line
    # Producer-friendly label, not the technical program name.
    assert "New Zealand 20% Incentive Rebate" in line
    # Modifiers visible.
    assert "less 3% monetization discount" in line
    assert "less NZD 75,000 filing fees" in line
    # Cash-today value in display currency (USD) with negative sign.
    assert line.startswith("New Zealand 20% Incentive Rebate")
    assert "-USD " in line
