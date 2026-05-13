"""Regression guard for the library-vs-CLI dependency boundary.

`INTEGRATION.md` documents that downstream consumers using only the
Python API (`from tax_engine.api.query import estimate_rebate` and
related) do not need click installed — click is required only for the
CLI dispatch path. Phase 1 v0.1-phase1 shipped with click imported at
the top of `api/query.py`, which silently broke that contract: any
library import triggered ModuleNotFoundError in an environment without
click.

This test runs in a subprocess that installs a meta-path finder
rejecting every `click` import — simulating an environment where click
is genuinely unavailable. It then imports the library surface and runs
a basic call. The subprocess pattern is used so the test runner's own
process state (where click may or may not already be imported) does not
contaminate the check.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "incentives.db"


_SUBPROCESS_SCRIPT = r"""
import sys
import importlib.abc
import importlib.machinery


class _BlockClick(importlib.abc.MetaPathFinder):
    '''Reject every `click` import. Simulates "click not installed".'''
    def find_spec(self, name, path, target=None):
        if name == "click" or name.startswith("click."):
            raise ModuleNotFoundError(
                f"No module named {name!r} (blocked by test)"
            )
        return None


# Defensively drop any already-imported click modules so the finder
# above is the only source of truth. (Python's import system consults
# sys.modules before meta_path.)
for key in [k for k in sys.modules if k == "click" or k.startswith("click.")]:
    del sys.modules[key]

sys.meta_path.insert(0, _BlockClick())

# A confirming negative check: importing click directly must fail now.
try:
    import click  # noqa: F401
except ModuleNotFoundError:
    pass
else:
    print("FAIL: click was not blocked by the meta-path finder", file=sys.stderr)
    sys.exit(2)

# Library surface: every public symbol consumers might pull in.
from api.query import (
    estimate_rebate,
    get_program,
    find_programs,
    list_jurisdictions,
    compare_programs,
    ProgramNotFound,
    NoRateRule,
)
from api.breakdown import Money, RebateEstimate, ProducerSummary

# Run an actual estimate to confirm the dispatch path doesn't itself
# reach for click. Use a natural-key lookup so the test is stable across
# any future ID reshuffles.
import sqlite3
con = sqlite3.connect(sys.argv[1])
ids = dict((r[1], r[0]) for r in con.execute(
    "SELECT id, program_name FROM incentive_programs"
))
con.close()

pid = ids["NZSPR International — Live Action Production Rebate (20%)"]
est = estimate_rebate(
    pid,
    qualifying_spend_by_currency={"USD": 39_393_672},
    monetization_discount_pct=3.0,
    filing_fees=Money(amount=37_500, currency="NZD"),
    output_view="producer",
    fx_target="USD",
)

# Sanity-check the result is well-formed and matches calibration.
assert isinstance(est, RebateEstimate)
assert est.producer_summary is not None
assert isinstance(est.producer_summary, ProducerSummary)
assert abs(est.estimate_usd - 7_878_734.4) < 1.0, (
    f"gross USD diverged: {est.estimate_usd}"
)
print("OK estimate_usd=", est.estimate_usd, "rule_applied=", est.rule_applied)
"""


def test_library_imports_without_click():
    if not DB_PATH.exists():
        pytest.skip(f"Database not present at {DB_PATH}; run scripts.load first.")

    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(DB_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Library import / call failed with click blocked. This means "
            "api/query.py (or one of its dependencies) still references "
            "click in the library surface. Move the offending click usage "
            "into the _cli() function and re-run.\n\n"
            f"subprocess returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert "OK" in result.stdout, f"unexpected stdout:\n{result.stdout}"
