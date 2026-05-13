"""Regression guard for the marquee demo scenario.

The committed file `docs/demo_uk_vs_nz_40m.md` contains a fenced code block
holding the verbatim output of:

    python -m api.query compare 5 6 1 2 --spend 40000000 --full

This test runs that exact command and compares its output to the snapshot.
Any change that alters the UK vs NZ numbers, caveats, sources, or rule
attributions at GBP/NZD 40,000,000 qualifying spend fails the test loudly.

When a change is intentional, regenerate the demo file with the same
command and commit the new snapshot alongside the code change. Pytest's
default string-diff output makes the offending lines easy to find.

The test deliberately runs the CLI through subprocess rather than calling
the Python API directly, so it catches regressions in argument parsing,
the dispatch glue, and the CLI rendering — not just the math.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_FILE = ROOT / "docs" / "demo_uk_vs_nz_40m.md"
DB_PATH = ROOT / "data" / "incentives.db"

# The exact arguments the demo file was generated with. If these change,
# the snapshot must change too — keep them identical. The Phase 1
# regeneration moved from `--full` (engineering breakdown default) to
# the new producer-view default, with monetization discount and filing
# fees applied to match a real budget line.
COMPARE_ARGS = [
    "compare", "5", "6", "1", "2",
    "--spend", "40000000",
    "--monetization-discount", "3",
    "--filing-fees", "75000",
    "--filing-fees-currency", "NZD",
]


def _extract_fenced_block(markdown: str) -> str:
    """Return the contents of the LAST fenced ``` block in the file.

    The demo file has one short fenced block early on (the command line
    that generated it) and the canonical output block at the end. The
    canonical output is what we pin, so we take the last fence.
    """
    matches = re.findall(r"```\n(.*?)\n```", markdown, flags=re.DOTALL)
    if not matches:
        raise AssertionError(
            f"No fenced code block found in {DEMO_FILE}. The snapshot file "
            "must include the canonical CLI output inside a ``` fence."
        )
    return matches[-1]


def test_demo_uk_vs_nz_40m_snapshot():
    if not DB_PATH.exists():
        pytest.skip(f"Database not present at {DB_PATH}; run scripts.load first.")
    if not DEMO_FILE.exists():
        pytest.fail(f"Snapshot file missing: {DEMO_FILE}")

    expected = _extract_fenced_block(DEMO_FILE.read_text(encoding="utf-8"))

    result = subprocess.run(
        [sys.executable, "-m", "api.query", *COMPARE_ARGS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
        check=False,
    )
    assert result.returncode == 0, (
        f"CLI exited non-zero (rc={result.returncode}).\n"
        f"stderr:\n{result.stderr}"
    )
    actual = result.stdout.rstrip("\n")

    if actual != expected:
        # Surface a short, human-readable summary along with pytest's diff.
        actual_lines = actual.splitlines()
        expected_lines = expected.splitlines()
        first_diff = next(
            (i for i, (a, e) in enumerate(zip(actual_lines, expected_lines)) if a != e),
            min(len(actual_lines), len(expected_lines)),
        )
        raise AssertionError(
            "Demo snapshot drift: the canonical UK-vs-NZ £40M comparison output "
            f"changed.\n\nFirst diverging line ({first_diff + 1}):\n"
            f"  expected: {expected_lines[first_diff] if first_diff < len(expected_lines) else '<eof>'!r}\n"
            f"  actual:   {actual_lines[first_diff] if first_diff < len(actual_lines) else '<eof>'!r}\n\n"
            "If the change is intentional, regenerate docs/demo_uk_vs_nz_40m.md "
            "with:\n"
            f"  python -m api.query {' '.join(COMPARE_ARGS)} > /tmp/demo.txt\n"
            "and commit the updated snapshot alongside this code change."
        )
