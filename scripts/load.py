"""Load processed JSON files into the SQLite database.

Pipeline:
    1. Drop and recreate the database from schema/schema.sql (idempotent rebuild).
    2. Validate every data/processed/*.json file (fail fast on errors).
    3. Insert jurisdictions, programs, and sources.
    4. Re-validate the loaded database.

Re-running this script is the canonical way to refresh the DB from source JSON.

Usage:
    python -m scripts.load
    python -m scripts.load --skip-validation         # not recommended
    python -m scripts.load --db path/to/other.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import click

from scripts.models import IncentiveProgram, Jurisdiction, JurisdictionFile, Source
from scripts.validate import validate_all_files, validate_db, _print_report

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "schema.sql"
PROCESSED_DIR = ROOT / "data" / "processed"
DEFAULT_DB = ROOT / "data" / "incentives.db"


def _rebuild_schema(con: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS change_log;
        DROP TABLE IF EXISTS sources;
        DROP TABLE IF EXISTS incentive_programs;
        DROP TABLE IF EXISTS jurisdictions;
        """
    )
    con.executescript(schema_sql)


def _insert_jurisdiction(cur: sqlite3.Cursor, j: Jurisdiction) -> int:
    cur.execute(
        """
        INSERT INTO jurisdictions (country, region, city, display_name, currency)
        VALUES (?, ?, ?, ?, ?)
        """,
        (j.country, j.region, j.city, j.display_name, j.currency),
    )
    return cur.lastrowid


def _insert_program(cur: sqlite3.Cursor, jurisdiction_id: int, p: IncentiveProgram) -> int:
    cur.execute(
        """
        INSERT INTO incentive_programs (
            jurisdiction_id, program_name, incentive_type, headline_rate_pct,
            rate_details, minimum_spend, minimum_spend_notes, cap_per_project,
            annual_program_cap, atl_eligible, atl_cap_notes,
            qualifying_spend_summary, non_qualifying_spend, application_process,
            payment_timing, sunset_date, last_verified_date, verification_method,
            qualifying_budget_ceiling, qualifying_budget_ceiling_notes, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            jurisdiction_id,
            p.program_name,
            p.incentive_type,
            p.headline_rate_pct,
            p.rate_details,
            p.minimum_spend,
            p.minimum_spend_notes,
            p.cap_per_project,
            p.annual_program_cap,
            1 if p.atl_eligible else 0,
            p.atl_cap_notes,
            p.qualifying_spend_summary,
            p.non_qualifying_spend,
            p.application_process,
            p.payment_timing,
            p.sunset_date.isoformat() if p.sunset_date else None,
            p.last_verified_date.isoformat(),
            p.verification_method,
            p.qualifying_budget_ceiling,
            p.qualifying_budget_ceiling_notes,
            p.notes,
        ),
    )
    return cur.lastrowid


def _insert_source(cur: sqlite3.Cursor, program_id: int, s: Source) -> None:
    cur.execute(
        """
        INSERT INTO sources (incentive_program_id, url, source_title, source_type, accessed_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            program_id,
            str(s.url),
            s.source_title,
            s.source_type,
            s.accessed_date.isoformat(),
        ),
    )


def load(db_path: Path = DEFAULT_DB, skip_validation: bool = False) -> int:
    if not skip_validation:
        click.echo("→ Validating processed files…")
        r = validate_all_files()
        _print_report(r)
        if not r.ok:
            click.echo(click.style("Aborting load — fix errors above.", fg="red"))
            return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")

    click.echo(f"→ Rebuilding schema at {db_path}")
    _rebuild_schema(con)

    cur = con.cursor()
    files = sorted(PROCESSED_DIR.glob("*.json"))
    n_jurisdictions = n_programs = n_sources = 0

    for path in files:
        click.echo(f"→ Loading {path.name}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        jf = JurisdictionFile.model_validate(raw)
        jid = _insert_jurisdiction(cur, jf.jurisdiction)
        n_jurisdictions += 1
        for prog in jf.programs:
            pid = _insert_program(cur, jid, prog)
            n_programs += 1
            for src in prog.sources:
                _insert_source(cur, pid, src)
                n_sources += 1

    con.commit()
    con.close()

    click.echo(
        click.style(
            f"✓ Loaded {n_jurisdictions} jurisdictions, "
            f"{n_programs} programs, {n_sources} sources",
            fg="green",
        )
    )

    if not skip_validation:
        click.echo("→ Validating loaded database…")
        r = validate_db(db_path)
        _print_report(r)
        if not r.ok:
            return 1

    return 0


@click.command()
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=DEFAULT_DB,
              help="Output SQLite database path.")
@click.option("--skip-validation", is_flag=True, help="Skip pre- and post-load validation.")
def cli(db_path: Path, skip_validation: bool):
    """Rebuild the incentives database from data/processed/*.json."""
    sys.exit(load(db_path, skip_validation))


if __name__ == "__main__":
    cli()
