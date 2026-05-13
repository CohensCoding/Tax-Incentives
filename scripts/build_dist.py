"""Assemble dist/ from source for downstream vendoring.

dist/ is a vendored snapshot — the minimum set of files a consuming
project (e.g., First Look) needs to call `estimate_rebate` against the
verified incentive database. It is committed at release tags. The
consumer copies it into their project as `tax_engine/` (or any other
package name); the in-package imports are rewritten to relative form
here so the consumer can pick any name.

To regenerate after a code change:

    python -m scripts.load             # rebuild data/incentives.db
    python -m scripts.build_dist \\
        --version v0.X-phaseY          # write the version stamp into dist/VERSION

Files included (per the Phase 1 vendoring spec):

  * api/{breakdown,fx,rate_rules,query}.py — with in-package imports
    rewritten to relative form so dist/ can be vendored under any name
  * schema/schema.sql — schema reference (the DB ships pre-built)
  * scripts/models.py — Pydantic models for the write path (not used by
    the read path, included as a courtesy for round-trip tooling)
  * data/processed/*.json — verified jurisdiction files
  * data/incentives.db — pre-built SQLite database

Files deliberately NOT included:

  * tests/, scripts/scrape/, scripts/load.py, scripts/validate.py,
    scripts/export.py — development tooling that stays in source
  * data/raw/ — audit trail for parsers; not needed by consumers
  * README, INTEGRATION.md, docs/ — repo-level documentation
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Files copied verbatim into dist/api/ after the import rewrite below.
API_FILES = ("breakdown.py", "fx.py", "rate_rules.py", "query.py")


def _transform_imports(text: str) -> str:
    """Rewrite in-package absolute imports to relative form.

    Source files use absolute imports like `from api.breakdown import ...`
    because they run from the repo root during development and tests. When
    vendored under any other package name, those absolute imports would
    not resolve. Rewriting to relative form makes the snapshot
    package-name-agnostic.

      from api import X        →  from . import X
      from api.X import Y      →  from .X import Y
      from scripts.X import Y  →  from ..scripts.X import Y
    """
    text = re.sub(r"^from api import ", "from . import ", text, flags=re.MULTILINE)
    text = re.sub(
        r"^from api\.([a-zA-Z_][a-zA-Z_0-9]*) import ",
        r"from .\1 import ",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^from scripts\.([a-zA-Z_][a-zA-Z_0-9]*) import ",
        r"from ..scripts.\1 import ",
        text,
        flags=re.MULTILINE,
    )
    return text


def _detect_version() -> str:
    """Best-effort: latest annotated tag pointing at HEAD; else short SHA."""
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", "--exact-match"],
            cwd=ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if tag:
            return tag
    except subprocess.CalledProcessError:
        pass
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        return f"untagged-{sha}"
    except subprocess.CalledProcessError:
        return "untagged-unknown"


def build(version: str | None = None) -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    # Top-level __init__.py — makes the vendored directory importable as
    # a package regardless of what the consumer renames it to.
    (DIST / "__init__.py").write_text(
        '"""Vendored snapshot of the tax incentive engine.\n\n'
        'See INTEGRATION.md in the source repo for usage. The version this\n'
        'snapshot was built from is recorded in VERSION.\n"""\n',
        encoding="utf-8",
    )

    # api/
    api_dir = DIST / "api"
    api_dir.mkdir()
    (api_dir / "__init__.py").write_text(
        '"""Public API. The primary entry point is `query.estimate_rebate`."""\n',
        encoding="utf-8",
    )
    for name in API_FILES:
        src = ROOT / "api" / name
        dst = api_dir / name
        dst.write_text(_transform_imports(src.read_text(encoding="utf-8")), encoding="utf-8")

    # scripts/models.py — courtesy include for consumers who want to
    # validate/round-trip data; the read path does not depend on it.
    scripts_dir = DIST / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(ROOT / "scripts" / "models.py", scripts_dir / "models.py")

    # schema/schema.sql — reference; the DB is shipped pre-built.
    schema_dir = DIST / "schema"
    schema_dir.mkdir()
    shutil.copy2(ROOT / "schema" / "schema.sql", schema_dir / "schema.sql")

    # data/processed/*.json and data/incentives.db
    data_dir = DIST / "data"
    data_dir.mkdir()
    (data_dir / "processed").mkdir()
    n_processed = 0
    for jf in sorted((ROOT / "data" / "processed").glob("*.json")):
        shutil.copy2(jf, data_dir / "processed" / jf.name)
        n_processed += 1
    db_src = ROOT / "data" / "incentives.db"
    if not db_src.exists():
        print(
            f"ERROR: {db_src} not found. Run `python -m scripts.load` first.",
            file=sys.stderr,
        )
        return 1
    shutil.copy2(db_src, data_dir / "incentives.db")

    # VERSION
    resolved_version = version or _detect_version()
    (DIST / "VERSION").write_text(resolved_version + "\n", encoding="utf-8")

    print(
        f"✓ Built dist/ at version {resolved_version} "
        f"({n_processed} processed JSON file(s); DB included)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble dist/ for vendoring.")
    parser.add_argument(
        "--version",
        default=None,
        help="Version stamp to write into dist/VERSION. Defaults to the "
             "current annotated git tag, or 'untagged-<sha>' if unavailable.",
    )
    args = parser.parse_args(argv)
    return build(args.version)


if __name__ == "__main__":
    sys.exit(main())
