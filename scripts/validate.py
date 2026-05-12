"""Validation layer.

Two modes:

* File mode: validate processed JSON files in data/processed/ before they are
  loaded into the database. Catches structural problems and policy violations
  (missing official source, stale verification date, currency mismatch, ATL
  field set but null, etc.).

* DB mode: validate the loaded SQLite database. Catches orphan records and
  cross-table integrity issues that wouldn't show up in per-file checks.

Run as a module:

    python -m scripts.validate files
    python -m scripts.validate db
    python -m scripts.validate all
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import click
from pydantic import ValidationError

from scripts.models import JurisdictionFile

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "incentives.db"

VERIFY_WARN_DAYS = 180
VERIFY_FAIL_DAYS = 365
HEADLINE_RATE_SANITY_MAX = 50.0  # over this → manual review

# Currency expected per country. ISO 4217 codes.
# A jurisdiction's currency must match its country.
EXPECTED_CURRENCY = {
    "United Kingdom": "GBP",
    "Ireland": "EUR",
    "Australia": "AUD",
    "New Zealand": "NZD",
    "Canada": "CAD",
    "United States": "USD",
    "Hungary": "HUF",
    "Czech Republic": "CZK",
    "Spain": "EUR",
    "Iceland": "ISK",
    "South Africa": "ZAR",
    "Malta": "EUR",
    "Dominican Republic": "DOP",
    "Thailand": "THB",
    "Colombia": "COP",
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "Report") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _validate_program(program, ctx: str, today: date, expected_currency: str | None) -> Report:
    r = Report()

    # Source: at least one official_government or film_office
    if not any(s.source_type in ("official_government", "film_office") for s in program.sources):
        r.err(f"{ctx}: no official_government or film_office source")

    # last_verified_date freshness
    age = (today - program.last_verified_date).days
    if age > VERIFY_FAIL_DAYS:
        r.err(
            f"{ctx}: last_verified_date is {age} days old "
            f"(>{VERIFY_FAIL_DAYS} fail threshold)"
        )
    elif age > VERIFY_WARN_DAYS:
        r.warn(
            f"{ctx}: last_verified_date is {age} days old "
            f"(>{VERIFY_WARN_DAYS} warn threshold)"
        )
    elif age < 0:
        r.err(f"{ctx}: last_verified_date is in the future")

    # Headline rate sanity
    if program.headline_rate_pct > HEADLINE_RATE_SANITY_MAX:
        r.warn(
            f"{ctx}: headline_rate_pct={program.headline_rate_pct} exceeds "
            f"sanity threshold {HEADLINE_RATE_SANITY_MAX} — review required"
        )

    # minimum_spend ↔ minimum_spend_notes co-presence (also enforced by Pydantic model)
    if program.minimum_spend is not None and not program.minimum_spend_notes:
        r.err(f"{ctx}: minimum_spend set but minimum_spend_notes missing")

    # Currency / country match is enforced at jurisdiction level via expected_currency.
    # (No-op here; left as hook in case program-level overrides are added later.)
    _ = expected_currency

    # Sunset date sanity
    if program.sunset_date and program.sunset_date < today:
        r.warn(f"{ctx}: program sunset_date {program.sunset_date} is in the past")

    return r


def validate_file(path: Path, today: date | None = None) -> Report:
    today = today or date.today()
    r = Report()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        r.err(f"{path.name}: invalid JSON: {e}")
        return r

    try:
        jf = JurisdictionFile.model_validate(raw)
    except ValidationError as e:
        r.err(f"{path.name}: schema validation failed:\n{e}")
        return r

    j = jf.jurisdiction
    expected = EXPECTED_CURRENCY.get(j.country)
    if expected and j.currency != expected:
        r.err(
            f"{path.name}: currency {j.currency} does not match expected "
            f"{expected} for country {j.country}"
        )

    for prog in jf.programs:
        ctx = f"{path.name} :: {j.display_name} :: {prog.program_name}"
        r.extend(_validate_program(prog, ctx, today, expected))

    return r


def validate_all_files(today: date | None = None) -> Report:
    r = Report()
    files = sorted(PROCESSED_DIR.glob("*.json"))
    if not files:
        r.warn(f"No processed files found in {PROCESSED_DIR}")
        return r
    for f in files:
        r.extend(validate_file(f, today=today))
    return r


def validate_db(db_path: Path = DB_PATH) -> Report:
    r = Report()
    if not db_path.exists():
        r.err(f"Database not found at {db_path}")
        return r

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    # Programs without any source
    rows = cur.execute(
        """
        SELECT p.id, p.program_name FROM incentive_programs p
        LEFT JOIN sources s ON s.incentive_program_id = p.id
        WHERE s.id IS NULL
        """
    ).fetchall()
    for pid, name in rows:
        r.err(f"program {pid} ({name}) has no sources")

    # Programs without an official source
    rows = cur.execute(
        """
        SELECT p.id, p.program_name FROM incentive_programs p
        WHERE NOT EXISTS (
            SELECT 1 FROM sources s
            WHERE s.incentive_program_id = p.id
              AND s.source_type IN ('official_government', 'film_office')
        )
        """
    ).fetchall()
    for pid, name in rows:
        r.err(f"program {pid} ({name}) has no official source")

    # Orphan sources / change_log (FKs should prevent these, but check anyway
    # in case foreign_keys was disabled during a load)
    orphans = cur.execute(
        """
        SELECT s.id FROM sources s
        LEFT JOIN incentive_programs p ON p.id = s.incentive_program_id
        WHERE p.id IS NULL
        """
    ).fetchall()
    for (sid,) in orphans:
        r.err(f"orphan source id={sid}")

    orphans = cur.execute(
        """
        SELECT c.id FROM change_log c
        LEFT JOIN incentive_programs p ON p.id = c.incentive_program_id
        WHERE p.id IS NULL
        """
    ).fetchall()
    for (cid,) in orphans:
        r.err(f"orphan change_log id={cid}")

    # Currency / country mismatch
    rows = cur.execute(
        "SELECT id, country, currency, display_name FROM jurisdictions"
    ).fetchall()
    for jid, country, currency, name in rows:
        expected = EXPECTED_CURRENCY.get(country)
        if expected and currency != expected:
            r.err(
                f"jurisdiction {jid} ({name}): currency {currency} != expected {expected}"
            )

    # last_verified_date freshness on loaded rows
    today = date.today()
    rows = cur.execute(
        "SELECT id, program_name, last_verified_date FROM incentive_programs"
    ).fetchall()
    for pid, name, lvd in rows:
        try:
            d = date.fromisoformat(lvd)
        except (TypeError, ValueError):
            r.err(f"program {pid} ({name}): last_verified_date '{lvd}' not ISO-formatted")
            continue
        age = (today - d).days
        if age > VERIFY_FAIL_DAYS:
            r.err(f"program {pid} ({name}): last_verified_date {age} days old")
        elif age > VERIFY_WARN_DAYS:
            r.warn(f"program {pid} ({name}): last_verified_date {age} days old")

    con.close()
    return r


def _print_report(r: Report) -> None:
    for w in r.warnings:
        click.echo(click.style(f"WARN  {w}", fg="yellow"))
    for e in r.errors:
        click.echo(click.style(f"ERROR {e}", fg="red"))
    summary = f"{len(r.errors)} error(s), {len(r.warnings)} warning(s)"
    click.echo(click.style(summary, fg="red" if r.errors else "green"))


@click.group()
def cli():
    """Validate processed data files and/or the loaded database."""


@cli.command()
def files():
    """Validate every JSON file in data/processed/."""
    r = validate_all_files()
    _print_report(r)
    sys.exit(0 if r.ok else 1)


@cli.command()
def db():
    """Validate the loaded SQLite database."""
    r = validate_db()
    _print_report(r)
    sys.exit(0 if r.ok else 1)


@cli.command(name="all")
def all_():
    """Validate files, then database."""
    r = validate_all_files()
    r.extend(validate_db())
    _print_report(r)
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    cli()
