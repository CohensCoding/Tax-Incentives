"""Tests for the estimate_rebate function and the breakdown contract.

Programs are looked up by natural key (jurisdiction + program_name) so the
tests are robust to the auto-incrementing program_id changing across loads.
The tests assume `python -m scripts.load` has run; they do not run the load
themselves so a stale DB will fail loudly rather than masking a bug.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from api.breakdown import RebateEstimate
from api.query import (
    DEFAULT_DB,
    ProgramNotFound,
    compare_programs,
    estimate_rebate,
    find_programs,
    get_program,
)


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


def _id(program_ids: dict[str, int], name: str) -> int:
    if name not in program_ids:
        raise KeyError(f"Program {name!r} not loaded; check data/processed/.")
    return program_ids[name]


# ─── Flat pattern ───────────────────────────────────────────────────────

def test_avec_film_flat(program_ids):
    """AVEC Film: 34% × £40M = £13.6M, no cap."""
    pid = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    est = estimate_rebate(pid, 40_000_000)
    assert est.rule_applied == "flat"
    assert est.currency == "GBP"
    assert est.gross_estimate == pytest.approx(13_600_000.0)
    assert est.inputs == {
        "qualifying_spend": 40_000_000.0,
        "atl_spend": 0.0,
        "currency": "GBP",
    }
    assert est.fx is None
    assert est.estimate_usd is None
    assert len(est.caveats) > 0  # never empty for a non-trivial program


def test_nz_live_action_flat(program_ids):
    """NZ Live Action: 20% × NZD 40M = NZD 8M."""
    pid = _id(program_ids, "NZSPR International — Live Action Production Rebate (20%)")
    est = estimate_rebate(pid, 40_000_000)
    assert est.rule_applied == "flat"
    assert est.currency == "NZD"
    assert est.gross_estimate == pytest.approx(8_000_000.0)


def test_vfx_flat(program_ids):
    """VFX additional credit: 39% × £5M = £1.95M; partitioning is caller's job."""
    pid = _id(program_ids, "AVEC VFX Additional Credit (Film & HETV)")
    est = estimate_rebate(pid, 5_000_000)
    assert est.rule_applied == "flat"
    assert est.gross_estimate == pytest.approx(1_950_000.0)
    # The partitioning caveat must be first in the list — producers reading
    # only the first caveat must understand they need to split spend.
    assert "VFX costs only" in est.caveats[0]


# ─── Capped-base pattern ────────────────────────────────────────────────

def test_iftc_cap_binds(program_ids):
    """IFTC at £20M qualifying: 53% × min(£20M, £12M) = 53% × £12M = £6.36M."""
    pid = _id(program_ids, "Enhanced AVEC for Independent Film (IFTC)")
    est = estimate_rebate(pid, 20_000_000)
    assert est.rule_applied == "capped_base"
    assert est.gross_estimate == pytest.approx(6_360_000.0)
    # The cap-binds note must be present so producers don't assume the rate
    # applies to the full £20M.
    assert any("cap binds" in s.description for s in est.steps)
    # Eligibility-ceiling caveat (£23.5M total core) must always fire for IFTC,
    # since the function can't verify it from a qualifying_spend input.
    assert any("£23.5M" in c for c in est.caveats)


def test_iftc_submitted_binds(program_ids):
    """IFTC at £8M qualifying: cap doesn't bind; 53% × £8M = £4.24M."""
    pid = _id(program_ids, "Enhanced AVEC for Independent Film (IFTC)")
    est = estimate_rebate(pid, 8_000_000)
    assert est.rule_applied == "capped_base"
    assert est.gross_estimate == pytest.approx(4_240_000.0)
    assert any("submitted binds" in s.description for s in est.steps)


def test_iftc_exact_cap(program_ids):
    """IFTC at exactly the £12M cap: edge case — neither described as 'binding'
    is wrong, but the math must produce 53% × £12M = £6.36M either way."""
    pid = _id(program_ids, "Enhanced AVEC for Independent Film (IFTC)")
    est = estimate_rebate(pid, 12_000_000)
    assert est.gross_estimate == pytest.approx(6_360_000.0)


# ─── Stacking pattern ───────────────────────────────────────────────────

def test_nz_production_uplift_stacks(program_ids):
    """Production 5% Uplift returns the uplift only; caveat names the parent."""
    pid = _id(program_ids, "NZSPR International — Production Rebate 5% Uplift")
    est = estimate_rebate(pid, 40_000_000)
    assert est.rule_applied == "stacking"
    assert est.gross_estimate == pytest.approx(2_000_000.0)
    # First caveat must explicitly name the parent so callers can find it.
    assert "Live Action Production Rebate" in est.caveats[0]


def test_nz_pdv_uplift_stacks(program_ids):
    pid = _id(program_ids, "NZSPR International — PDV Rebate 5% Uplift")
    est = estimate_rebate(pid, 1_000_000)
    assert est.rule_applied == "stacking"
    assert est.gross_estimate == pytest.approx(50_000.0)
    assert "PDV Rebate (20%)" in est.caveats[0]


def test_stacking_sum_matches_combined_rate(program_ids):
    """Producer should be able to sum parent + uplift and get a 25% effective
    rate. This is the canonical use case for the stacking pattern."""
    parent_id = _id(program_ids, "NZSPR International — Live Action Production Rebate (20%)")
    uplift_id = _id(program_ids, "NZSPR International — Production Rebate 5% Uplift")
    parent = estimate_rebate(parent_id, 40_000_000)
    uplift = estimate_rebate(uplift_id, 40_000_000)
    assert parent.gross_estimate + uplift.gross_estimate == pytest.approx(10_000_000.0)


# ─── FX provenance ─────────────────────────────────────────────────────

def test_fx_provenance_attached_when_crossing(program_ids):
    pid = _id(program_ids, "NZSPR International — Live Action Production Rebate (20%)")
    est = estimate_rebate(pid, 40_000_000, fx_target="USD")
    # NZD 8M @ 0.60 = USD 4.8M
    assert est.estimate_usd == pytest.approx(4_800_000.0)
    assert est.fx is not None
    assert est.fx.fx_rate == pytest.approx(0.60)
    # The stub rates were last updated 2026-05-12; today is 2026-05-12, so
    # not stale. Just assert the flag exists with the right type.
    assert isinstance(est.fx.fx_stale, bool)


def test_fx_not_attached_when_same_currency(program_ids):
    pid = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    est = estimate_rebate(pid, 10_000_000, fx_target="GBP")
    # Asking for GBP→GBP is a no-op; the function leaves fx unset.
    assert est.fx is None
    assert est.estimate_usd is None


# ─── Contract enforcement ──────────────────────────────────────────────

def test_breakdown_is_rebate_estimate(program_ids):
    """Whatever else changes, the return type must match the locked contract."""
    pid = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    est = estimate_rebate(pid, 10_000_000)
    assert isinstance(est, RebateEstimate)
    # Required fields present
    for field in ("program_id", "program_name", "jurisdiction_display", "inputs",
                  "gross_estimate", "currency", "rule_applied", "steps",
                  "caveats", "sources"):
        assert hasattr(est, field), f"breakdown missing required field {field!r}"


def test_caveats_never_empty_for_real_programs(program_ids):
    """The load-bearing rule: no production-relevant estimate ships without
    caveats. If this test fails, a program was added without preconditions
    declared in api.rate_rules.RULES."""
    for name, pid in program_ids.items():
        est = estimate_rebate(pid, 1_000_000)
        assert len(est.caveats) > 0, f"{name!r} produced an estimate with zero caveats"


def test_sources_keyed_by_program_id(program_ids):
    pid = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    est = estimate_rebate(pid, 1_000_000)
    assert list(est.sources.keys()) == [pid]
    assert len(est.sources[pid]) >= 1


# ─── Input validation ──────────────────────────────────────────────────

def test_negative_qualifying_spend_rejected(program_ids):
    pid = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    with pytest.raises(ValueError):
        estimate_rebate(pid, -1.0)


def test_unknown_program_rejected():
    with pytest.raises(ProgramNotFound):
        estimate_rebate(99999, 1_000_000)


# ─── Comparison ────────────────────────────────────────────────────────

def test_compare_uk_vs_nz_for_40m(program_ids):
    """The canonical use case: UK AVEC vs NZ Live Action for a $40M-equivalent
    project. Tests that compare_programs runs, sorts by USD descending, and
    returns one estimate per input."""
    uk = _id(program_ids, "Audio-Visual Expenditure Credit (AVEC) — Film")
    nz = _id(program_ids, "NZSPR International — Live Action Production Rebate (20%)")
    table = compare_programs([uk, nz], 40_000_000, fx_target="USD")
    assert len(table.rows) == 2
    assert len(table.estimates) == 2
    # rows sorted descending by estimate_usd
    assert table.rows[0].estimate_usd >= table.rows[1].estimate_usd


# ─── find_programs ─────────────────────────────────────────────────────

def test_find_atl_eligible_filters(program_ids):
    """VFX has atl_eligible=False; everything else is True. The filter must
    keep the VFX program out of the True set and only return it in the False set."""
    atl_true = find_programs(atl_eligible=True)
    atl_false = find_programs(atl_eligible=False)
    names_true = {p.program_name for p in atl_true}
    names_false = {p.program_name for p in atl_false}
    assert "AVEC VFX Additional Credit (Film & HETV)" in names_false
    assert "AVEC VFX Additional Credit (Film & HETV)" not in names_true


def test_find_min_rate_filters(program_ids):
    """min_rate=40 should return IFTC (53%) only — everything else is ≤39."""
    result = find_programs(min_rate_pct=40.0)
    assert len(result) == 1
    assert result[0].program_name == "Enhanced AVEC for Independent Film (IFTC)"


def test_find_country_filters(program_ids):
    nz = find_programs(country="New Zealand")
    assert len(nz) == 4
    assert all(p.jurisdiction_display == "New Zealand" for p in nz)
