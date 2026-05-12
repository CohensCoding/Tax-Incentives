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
from datetime import date
from pathlib import Path
from typing import Any

import click

from scripts.models import IncentiveProgram, Jurisdiction, JurisdictionFile, Source
from scripts.validate import validate_all_files, validate_db, _print_report

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "schema.sql"
PROCESSED_DIR = ROOT / "data" / "processed"
DEFAULT_DB = ROOT / "data" / "incentives.db"

# Fields whose changes are recorded in change_log. These are the ones a
# producer actually cares about — numeric, enum, and date fields. Text-only
# fields (notes, rate_details, summaries) are excluded; their drift is too
# noisy to make a useful audit trail.
TRACKED_FIELDS: tuple[str, ...] = (
    "headline_rate_pct",
    "incentive_type",
    "verification_method",
    "last_verified_date",
    "atl_eligible",
    "minimum_spend",
    "cap_per_project",
    "annual_program_cap",
    "qualifying_budget_ceiling",
    "sunset_date",
)

# Natural key for a program — survives an ID-reassigning schema rebuild.
ProgramKey = tuple[str, str]  # (jurisdiction.display_name, program_name)


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
        INSERT INTO sources (
            incentive_program_id, url, source_title, source_type, accessed_date, local_path
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            program_id,
            str(s.url),
            s.source_title,
            s.source_type,
            s.accessed_date.isoformat(),
            s.local_path,
        ),
    )


def record_change(
    cur: sqlite3.Cursor,
    program_id: int,
    field: str,
    old_value: Any,
    new_value: Any,
    *,
    changed_date: date | None = None,
    reason: str | None = None,
) -> None:
    """Append a change_log entry for `program_id`.

    Values are coerced to strings (or NULL) for storage; the reason field is
    free-form. Callers outside the loader (a future `verify` subcommand that
    flips verification_method, a manual rate correction script, etc.) should
    use this helper rather than poking the table directly.
    """
    cur.execute(
        """
        INSERT INTO change_log (
            incentive_program_id, changed_date, field_changed,
            old_value, new_value, change_reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            program_id,
            (changed_date or date.today()).isoformat(),
            field,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            reason,
        ),
    )


def _snapshot_existing(con: sqlite3.Connection) -> tuple[
    dict[ProgramKey, dict[str, Any]],
    list[tuple],
]:
    """Capture (program tracked-field values, change_log entries) keyed by
    natural key, so we can diff after the rebuild.

    Returns ({}, []) if the schema isn't present yet (first-ever run).
    """
    try:
        select = ", ".join(f"p.{f}" for f in TRACKED_FIELDS)
        rows = con.execute(
            f"""
            SELECT j.display_name, p.program_name, p.id, {select}
            FROM incentive_programs p
            JOIN jurisdictions j ON j.id = p.jurisdiction_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}, []

    programs: dict[ProgramKey, dict[str, Any]] = {}
    old_id_to_key: dict[int, ProgramKey] = {}
    for row in rows:
        jname, pname, old_pid, *field_values = row
        key: ProgramKey = (jname, pname)
        programs[key] = dict(zip(TRACKED_FIELDS, field_values))
        old_id_to_key[old_pid] = key

    cl_rows = con.execute(
        """
        SELECT incentive_program_id, changed_date, field_changed,
               old_value, new_value, change_reason
        FROM change_log
        ORDER BY id
        """
    ).fetchall()

    # Re-key change_log rows by natural key so they survive an ID-reassigning
    # rebuild. Entries whose program no longer exists are dropped on the
    # floor (the program was deleted from the source JSON).
    keyed_log = [
        (old_id_to_key[old_pid], cd, field, old_v, new_v, reason)
        for old_pid, cd, field, old_v, new_v, reason in cl_rows
        if old_pid in old_id_to_key
    ]
    return programs, keyed_log


def _replay_and_diff_change_log(
    cur: sqlite3.Cursor,
    snapshot: dict[ProgramKey, dict[str, Any]],
    preserved_log: list[tuple],
    new_id_by_key: dict[ProgramKey, int],
    today: date,
) -> tuple[int, int]:
    """Carry forward old change_log entries and append diff entries for any
    tracked field that changed since the previous load.

    Returns (n_preserved, n_diff_entries).
    """
    n_preserved = 0
    for key, changed_date_s, field, old_v, new_v, reason in preserved_log:
        new_pid = new_id_by_key.get(key)
        if new_pid is None:
            continue
        cur.execute(
            """
            INSERT INTO change_log (
                incentive_program_id, changed_date, field_changed,
                old_value, new_value, change_reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_pid, changed_date_s, field, old_v, new_v, reason),
        )
        n_preserved += 1

    n_diff = 0
    select = ", ".join(TRACKED_FIELDS)
    for key, old_fields in snapshot.items():
        new_pid = new_id_by_key.get(key)
        if new_pid is None:
            continue
        row = cur.execute(
            f"SELECT {select} FROM incentive_programs WHERE id = ?",
            (new_pid,),
        ).fetchone()
        new_fields = dict(zip(TRACKED_FIELDS, row))
        for f in TRACKED_FIELDS:
            if old_fields[f] != new_fields[f]:
                record_change(
                    cur,
                    new_pid,
                    f,
                    old_fields[f],
                    new_fields[f],
                    changed_date=today,
                    reason="Auto-detected during scripts.load",
                )
                n_diff += 1
    return n_preserved, n_diff


def load(db_path: Path = DEFAULT_DB, skip_validation: bool = False) -> int:
    if not skip_validation:
        click.echo("→ Validating processed files…")
        r = validate_all_files()
        _print_report(r)
        if not r.ok:
            click.echo(click.style("Aborting load — fix errors above.", fg="red"))
            return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Snapshot the existing DB (if any) BEFORE rebuilding schema, so we have
    # something to diff against and so the change_log audit trail survives.
    snapshot: dict[ProgramKey, dict[str, Any]] = {}
    preserved_log: list[tuple] = []
    if db_path.exists():
        con_pre = sqlite3.connect(db_path)
        con_pre.execute("PRAGMA foreign_keys = ON")
        snapshot, preserved_log = _snapshot_existing(con_pre)
        con_pre.close()

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")

    click.echo(f"→ Rebuilding schema at {db_path}")
    _rebuild_schema(con)

    cur = con.cursor()
    files = sorted(PROCESSED_DIR.glob("*.json"))
    n_jurisdictions = n_programs = n_sources = 0
    new_id_by_key: dict[ProgramKey, int] = {}

    for path in files:
        click.echo(f"→ Loading {path.name}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        jf = JurisdictionFile.model_validate(raw)
        jid = _insert_jurisdiction(cur, jf.jurisdiction)
        n_jurisdictions += 1
        for prog in jf.programs:
            pid = _insert_program(cur, jid, prog)
            new_id_by_key[(jf.jurisdiction.display_name, prog.program_name)] = pid
            n_programs += 1
            for src in prog.sources:
                _insert_source(cur, pid, src)
                n_sources += 1

    n_preserved, n_diff = _replay_and_diff_change_log(
        cur, snapshot, preserved_log, new_id_by_key, date.today()
    )

    con.commit()
    con.close()

    click.echo(
        click.style(
            f"✓ Loaded {n_jurisdictions} jurisdictions, "
            f"{n_programs} programs, {n_sources} sources",
            fg="green",
        )
    )
    if n_preserved or n_diff:
        click.echo(
            click.style(
                f"  change_log: {n_preserved} preserved, {n_diff} new diff entr"
                f"{'y' if n_diff == 1 else 'ies'}",
                fg="cyan",
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
