"""Query interface for the incentives database.

Exposes both a Python API (consumed by downstream tools, the LLM exporter,
test suites) and a Click CLI for human inspection.

Design rules:

* The estimator NEVER returns a number without a breakdown. Every call
  produces a fully populated `RebateEstimate` per the contract in
  api/breakdown.py.
* The math NEVER branches on jurisdiction name — it dispatches on the
  RateRule pattern through a small handler table.
* Caveats are emitted from the rate-rule registry (program-specific
  preconditions) plus pattern-specific notes. The function is a
  caveats-pass-through, not a caveats-author.
* USD conversion is opt-in via the `fx_target` argument and is rendered
  through api.fx with full provenance — never silently baked in.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import click

from api import fx, rate_rules
from api.breakdown import EstimateStep, FxInfo, RebateEstimate, RulePattern
from api.rate_rules import ProgramKey, RateRule


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "incentives.db"


# ─── Lightweight read-only DTOs ─────────────────────────────────────────
# We deliberately don't reuse the Pydantic models from scripts/models.py:
# those validate the *write* path (JSON → SQLite). At read time we want
# small dataclasses that match the DB row shape and don't impose the same
# strictness (e.g., HttpUrl) on every read.

@dataclass(frozen=True)
class Jurisdiction:
    id: int
    country: str
    region: Optional[str]
    city: Optional[str]
    display_name: str
    currency: str


@dataclass(frozen=True)
class IncentiveProgram:
    id: int
    jurisdiction_id: int
    jurisdiction_display: str
    currency: str
    program_name: str
    incentive_type: str
    headline_rate_pct: float
    rate_details: Optional[str]
    minimum_spend: Optional[float]
    minimum_spend_notes: Optional[str]
    cap_per_project: Optional[float]
    annual_program_cap: Optional[float]
    atl_eligible: bool
    atl_cap_notes: Optional[str]
    qualifying_spend_summary: Optional[str]
    non_qualifying_spend: Optional[str]
    application_process: Optional[str]
    payment_timing: Optional[str]
    sunset_date: Optional[str]
    last_verified_date: str
    verification_method: str
    qualifying_budget_ceiling: Optional[float]
    qualifying_budget_ceiling_notes: Optional[str]
    notes: Optional[str]
    source_urls: list[str]


class ProgramNotFound(Exception):
    pass


class NoRateRule(Exception):
    """Raised when a program has no entry in api.rate_rules.RULES.

    estimate_rebate refuses to guess. Add the rule to the registry first.
    """


# ─── DB plumbing ────────────────────────────────────────────────────────

def _connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run `python -m scripts.load` first."
        )
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


_PROGRAM_SELECT = """
SELECT p.id, p.jurisdiction_id, j.display_name AS jurisdiction_display, j.currency,
       p.program_name, p.incentive_type, p.headline_rate_pct, p.rate_details,
       p.minimum_spend, p.minimum_spend_notes, p.cap_per_project,
       p.annual_program_cap, p.atl_eligible, p.atl_cap_notes,
       p.qualifying_spend_summary, p.non_qualifying_spend, p.application_process,
       p.payment_timing, p.sunset_date, p.last_verified_date, p.verification_method,
       p.qualifying_budget_ceiling, p.qualifying_budget_ceiling_notes, p.notes
FROM incentive_programs p
JOIN jurisdictions j ON j.id = p.jurisdiction_id
"""


def _row_to_program(con: sqlite3.Connection, row: sqlite3.Row) -> IncentiveProgram:
    src_urls = [
        r["url"] for r in con.execute(
            "SELECT url FROM sources WHERE incentive_program_id = ? ORDER BY id",
            (row["id"],),
        )
    ]
    return IncentiveProgram(
        id=row["id"],
        jurisdiction_id=row["jurisdiction_id"],
        jurisdiction_display=row["jurisdiction_display"],
        currency=row["currency"],
        program_name=row["program_name"],
        incentive_type=row["incentive_type"],
        headline_rate_pct=row["headline_rate_pct"],
        rate_details=row["rate_details"],
        minimum_spend=row["minimum_spend"],
        minimum_spend_notes=row["minimum_spend_notes"],
        cap_per_project=row["cap_per_project"],
        annual_program_cap=row["annual_program_cap"],
        atl_eligible=bool(row["atl_eligible"]),
        atl_cap_notes=row["atl_cap_notes"],
        qualifying_spend_summary=row["qualifying_spend_summary"],
        non_qualifying_spend=row["non_qualifying_spend"],
        application_process=row["application_process"],
        payment_timing=row["payment_timing"],
        sunset_date=row["sunset_date"],
        last_verified_date=row["last_verified_date"],
        verification_method=row["verification_method"],
        qualifying_budget_ceiling=row["qualifying_budget_ceiling"],
        qualifying_budget_ceiling_notes=row["qualifying_budget_ceiling_notes"],
        notes=row["notes"],
        source_urls=src_urls,
    )


# ─── Public API ─────────────────────────────────────────────────────────

def list_jurisdictions(
    country: Optional[str] = None,
    *,
    db_path: Path = DEFAULT_DB,
) -> list[Jurisdiction]:
    """Return all jurisdictions, optionally filtered by country."""
    con = _connect(db_path)
    try:
        if country:
            rows = con.execute(
                "SELECT * FROM jurisdictions WHERE country = ? ORDER BY display_name",
                (country,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM jurisdictions ORDER BY country, display_name"
            ).fetchall()
        return [
            Jurisdiction(
                id=r["id"], country=r["country"], region=r["region"], city=r["city"],
                display_name=r["display_name"], currency=r["currency"],
            )
            for r in rows
        ]
    finally:
        con.close()


def get_program(program_id: int, *, db_path: Path = DEFAULT_DB) -> IncentiveProgram:
    """Fetch one program by primary key. Raises ProgramNotFound."""
    con = _connect(db_path)
    try:
        row = con.execute(_PROGRAM_SELECT + " WHERE p.id = ?", (program_id,)).fetchone()
        if row is None:
            raise ProgramNotFound(f"No program with id={program_id}")
        return _row_to_program(con, row)
    finally:
        con.close()


def find_programs(
    *,
    atl_eligible: Optional[bool] = None,
    min_rate_pct: Optional[float] = None,
    max_minimum_spend_usd: Optional[float] = None,
    country: Optional[str] = None,
    db_path: Path = DEFAULT_DB,
) -> list[IncentiveProgram]:
    """Filter programs by common producer-facing criteria.

    `max_minimum_spend_usd` converts each program's minimum_spend to USD
    via api.fx for comparison. Programs without a minimum_spend pass this
    filter (no floor to clear).
    """
    con = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list = []
        if atl_eligible is not None:
            clauses.append("p.atl_eligible = ?")
            params.append(1 if atl_eligible else 0)
        if min_rate_pct is not None:
            clauses.append("p.headline_rate_pct >= ?")
            params.append(min_rate_pct)
        if country is not None:
            clauses.append("j.country = ?")
            params.append(country)
        sql = _PROGRAM_SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "")
        sql += " ORDER BY j.display_name, p.program_name"
        rows = con.execute(sql, params).fetchall()
        results = [_row_to_program(con, r) for r in rows]
    finally:
        con.close()

    if max_minimum_spend_usd is not None:
        results = [
            p for p in results
            if p.minimum_spend is None
            or _to_usd(p.minimum_spend, p.currency) <= max_minimum_spend_usd
        ]
    return results


def _to_usd(amount: float, currency: str) -> float:
    info = fx.lookup(currency, "USD")
    return amount * info.fx_rate


# ─── Estimator ──────────────────────────────────────────────────────────

def estimate_rebate(
    program_id: int,
    qualifying_spend: float,
    atl_spend: float = 0.0,
    *,
    fx_target: Optional[str] = None,
    db_path: Path = DEFAULT_DB,
) -> RebateEstimate:
    """Compute a gross rebate / credit estimate for one program.

    `qualifying_spend` is in the program's local currency and is assumed
    to already reflect the program's qualifying-cost definition (the
    caveats explain what the function did NOT verify).

    `atl_spend` is reserved for future ATL-cap programs. None of the seven
    currently-loaded programs apply an ATL-specific cap; the value is
    echoed back in `inputs` but does not alter the math.

    Set `fx_target='USD'` (or any registered currency) to additionally
    render the estimate in another currency, with FX provenance attached.
    """
    if qualifying_spend < 0:
        raise ValueError("qualifying_spend must be non-negative")
    if atl_spend < 0:
        raise ValueError("atl_spend must be non-negative")

    program = get_program(program_id, db_path=db_path)
    rule = rate_rules.get_rule(program.jurisdiction_display, program.program_name)
    if rule is None:
        raise NoRateRule(
            f"No rate rule registered for ({program.jurisdiction_display!r}, "
            f"{program.program_name!r}). Add an entry to api.rate_rules.RULES."
        )

    handler = _HANDLERS.get(rule.pattern)
    if handler is None:
        raise NoRateRule(f"Unknown rate-rule pattern {rule.pattern!r}")

    estimate = handler(program, rule, qualifying_spend, atl_spend)

    if fx_target and fx_target.upper() != program.currency.upper():
        info = fx.lookup(program.currency, fx_target.upper())
        estimate = estimate.model_copy(update={
            "estimate_usd": estimate.gross_estimate * info.fx_rate if fx_target.upper() == "USD" else None,
            "fx": info,
        })
        # For non-USD targets we don't populate estimate_usd, but the steps
        # still record the converted figure for transparency.
        if fx_target.upper() != "USD":
            converted = estimate.gross_estimate * info.fx_rate
            estimate.steps.append(EstimateStep(
                description=f"Converted to {fx_target.upper()} at {info.fx_rate:.6f} "
                            f"({program.currency}→{fx_target.upper()}, as of {info.fx_as_of})",
                value=converted,
            ))
        if info.fx_stale:
            estimate.caveats.append(
                f"FX rate {program.currency}→{fx_target.upper()} is stale "
                f"(as of {info.fx_as_of}, threshold 24h). Re-fetch before binding."
            )

    return estimate


# ─── Pattern handlers ───────────────────────────────────────────────────

def _common_caveats(program: IncentiveProgram, rule: RateRule) -> list[str]:
    out = list(rule.caveats) + list(rate_rules.universal_caveats())
    if program.verification_method != "official_source_archived" and program.verification_method != "official_source_live":
        out.append(
            f"verification_method={program.verification_method!r}: this program's data is not "
            "verified against an official source. Estimate is unreliable."
        )
    return out


def _estimate_flat(
    program: IncentiveProgram,
    rule: RateRule,
    qualifying_spend: float,
    atl_spend: float,
) -> RebateEstimate:
    rate = program.headline_rate_pct / 100.0
    gross = rate * qualifying_spend
    steps = [
        EstimateStep(description=f"Headline rate ({program.headline_rate_pct}%)", value=rate),
        EstimateStep(description="Qualifying spend submitted", value=qualifying_spend),
        EstimateStep(
            description=f"Gross estimate = {rate:.4f} × {qualifying_spend:,.2f}",
            value=gross,
        ),
    ]
    return RebateEstimate(
        program_id=program.id,
        program_name=program.program_name,
        jurisdiction_display=program.jurisdiction_display,
        inputs={
            "qualifying_spend": qualifying_spend,
            "atl_spend": atl_spend,
            "currency": program.currency,
        },
        gross_estimate=gross,
        currency=program.currency,
        rule_applied="flat",
        steps=steps,
        caveats=_common_caveats(program, rule),
        sources={program.id: list(program.source_urls)},
    )


def _estimate_capped_base(
    program: IncentiveProgram,
    rule: RateRule,
    qualifying_spend: float,
    atl_spend: float,
) -> RebateEstimate:
    if rule.base_cap_qualifying is None:
        raise NoRateRule(
            f"capped_base pattern for program {program.id} ({program.program_name}) "
            "requires base_cap_qualifying. Update api.rate_rules.RULES."
        )
    rate = program.headline_rate_pct / 100.0
    cap = rule.base_cap_qualifying
    effective = min(qualifying_spend, cap)
    gross = rate * effective
    cap_binding = effective < qualifying_spend

    steps = [
        EstimateStep(description=f"Headline rate ({program.headline_rate_pct}%)", value=rate),
        EstimateStep(description="Qualifying spend submitted", value=qualifying_spend),
        EstimateStep(
            description=f"Base cap on qualifying spend at headline rate "
                        f"({program.currency} {cap:,.0f})",
            value=cap,
        ),
        EstimateStep(
            description=f"Effective qualifying spend = min(submitted, cap) "
                        f"[{'cap binds' if cap_binding else 'submitted binds'}]",
            value=effective,
        ),
        EstimateStep(
            description=f"Gross estimate = {rate:.4f} × {effective:,.2f}",
            value=gross,
        ),
    ]
    if cap_binding:
        steps.append(EstimateStep(
            description=f"Note: qualifying spend above {program.currency} {cap:,.0f} is not "
                        "eligible at this rate under the capped_base pattern.",
            value=None,
        ))
    return RebateEstimate(
        program_id=program.id,
        program_name=program.program_name,
        jurisdiction_display=program.jurisdiction_display,
        inputs={
            "qualifying_spend": qualifying_spend,
            "atl_spend": atl_spend,
            "currency": program.currency,
        },
        gross_estimate=gross,
        currency=program.currency,
        rule_applied="capped_base",
        steps=steps,
        caveats=_common_caveats(program, rule),
        sources={program.id: list(program.source_urls)},
    )


def _estimate_stacking(
    program: IncentiveProgram,
    rule: RateRule,
    qualifying_spend: float,
    atl_spend: float,
) -> RebateEstimate:
    if rule.stacks_on is None:
        raise NoRateRule(
            f"stacking pattern for program {program.id} ({program.program_name}) "
            "requires stacks_on. Update api.rate_rules.RULES."
        )
    rate = program.headline_rate_pct / 100.0
    gross = rate * qualifying_spend
    parent_juris, parent_name = rule.stacks_on
    steps = [
        EstimateStep(
            description=f"Stacking uplift rate ({program.headline_rate_pct}%)",
            value=rate,
        ),
        EstimateStep(description="Qualifying spend submitted", value=qualifying_spend),
        EstimateStep(
            description=f"Gross uplift = {rate:.4f} × {qualifying_spend:,.2f} "
                        f"(additive on top of parent '{parent_name}')",
            value=gross,
        ),
    ]
    return RebateEstimate(
        program_id=program.id,
        program_name=program.program_name,
        jurisdiction_display=program.jurisdiction_display,
        inputs={
            "qualifying_spend": qualifying_spend,
            "atl_spend": atl_spend,
            "currency": program.currency,
        },
        gross_estimate=gross,
        currency=program.currency,
        rule_applied="stacking",
        steps=steps,
        caveats=_common_caveats(program, rule),
        sources={program.id: list(program.source_urls)},
    )


# Pattern dispatch table — adding a fourth pattern is one new entry here
# plus one new handler. No edits to estimate_rebate.
_HANDLERS: dict[RulePattern, Callable[..., RebateEstimate]] = {
    "flat": _estimate_flat,
    "capped_base": _estimate_capped_base,
    "stacking": _estimate_stacking,
}


# ─── compare_programs ───────────────────────────────────────────────────

@dataclass
class ComparisonRow:
    program_id: int
    program_name: str
    jurisdiction_display: str
    currency: str
    gross_estimate: float
    estimate_usd: Optional[float]
    rule_applied: RulePattern
    caveats_count: int


@dataclass
class ComparisonTable:
    """Side-by-side comparison of estimates for a single qualifying-spend
    assumption across multiple programs. Each estimate retains its full
    breakdown; the summary fields are for tabular rendering.
    """

    qualifying_spend: float
    fx_target: Optional[str]
    rows: list[ComparisonRow]
    estimates: list[RebateEstimate]


def compare_programs(
    program_ids: list[int],
    qualifying_spend: float,
    atl_spend: float = 0.0,
    *,
    fx_target: Optional[str] = "USD",
    db_path: Path = DEFAULT_DB,
) -> ComparisonTable:
    """Run estimate_rebate against each program with the same inputs and
    return both the summary table and the full breakdowns.

    Programs without a registered rate rule are skipped with a clear
    message — caller can log/display the omission.
    """
    estimates: list[RebateEstimate] = []
    for pid in program_ids:
        try:
            estimates.append(estimate_rebate(
                pid, qualifying_spend, atl_spend,
                fx_target=fx_target, db_path=db_path,
            ))
        except (ProgramNotFound, NoRateRule) as e:
            click.echo(click.style(f"  skipped program {pid}: {e}", fg="yellow"), err=True)

    rows = [
        ComparisonRow(
            program_id=e.program_id,
            program_name=e.program_name,
            jurisdiction_display=e.jurisdiction_display,
            currency=e.currency,
            gross_estimate=e.gross_estimate,
            estimate_usd=e.estimate_usd,
            rule_applied=e.rule_applied,
            caveats_count=len(e.caveats),
        )
        for e in estimates
    ]
    rows.sort(key=lambda r: (r.estimate_usd or 0.0, r.gross_estimate), reverse=True)
    return ComparisonTable(
        qualifying_spend=qualifying_spend,
        fx_target=fx_target,
        rows=rows,
        estimates=estimates,
    )


# ─── CLI ────────────────────────────────────────────────────────────────

def _render_money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


@click.group()
def cli():
    """Query the incentives database."""


@cli.command(name="list")
@click.option("--country", default=None, help="Filter by country (exact match).")
def list_cmd(country: Optional[str]):
    """List jurisdictions, optionally filtered by country."""
    for j in list_jurisdictions(country=country):
        click.echo(f"  {j.id:>3}  {j.display_name}  ({j.currency})")


@cli.command()
@click.argument("program_id", type=int)
def show(program_id: int):
    """Show a single program's full record."""
    try:
        p = get_program(program_id)
    except ProgramNotFound as e:
        raise click.ClickException(str(e))
    click.echo(f"#{p.id}  {p.program_name}")
    click.echo(f"  jurisdiction:        {p.jurisdiction_display}  ({p.currency})")
    click.echo(f"  type:                {p.incentive_type}")
    click.echo(f"  headline rate:       {p.headline_rate_pct}%")
    click.echo(f"  atl eligible:        {p.atl_eligible}")
    click.echo(f"  minimum spend:       {p.minimum_spend}")
    click.echo(f"  budget ceiling:      {p.qualifying_budget_ceiling}")
    click.echo(f"  verification:        {p.verification_method}")
    click.echo(f"  last verified:       {p.last_verified_date}")
    click.echo(f"  sources:")
    for u in p.source_urls:
        click.echo(f"    - {u}")


@cli.command()
@click.option("--program", "program_id", type=int, required=True)
@click.option("--spend", "qualifying_spend", type=float, required=True,
              help="Qualifying spend in program-local currency.")
@click.option("--atl-spend", "atl_spend", type=float, default=0.0)
@click.option("--fx-target", default="USD", help="Optional cross-currency conversion target.")
def estimate(program_id: int, qualifying_spend: float, atl_spend: float, fx_target: str):
    """Estimate the rebate / credit for one program and show the breakdown."""
    try:
        est = estimate_rebate(
            program_id, qualifying_spend, atl_spend, fx_target=fx_target,
        )
    except (ProgramNotFound, NoRateRule) as e:
        raise click.ClickException(str(e))
    _print_estimate(est)


def _print_estimate(est: RebateEstimate) -> None:
    click.echo(f"#{est.program_id}  {est.program_name}  [{est.jurisdiction_display}]")
    click.echo(f"  rule_applied:   {est.rule_applied}")
    click.echo(f"  gross_estimate: {_render_money(est.gross_estimate, est.currency)}")
    if est.estimate_usd is not None and est.fx is not None:
        stale = " (STALE)" if est.fx.fx_stale else ""
        click.echo(
            f"  estimate_usd:   USD {est.estimate_usd:,.2f}"
            f"  @ {est.fx.fx_rate:.6f}  as_of={est.fx.fx_as_of}{stale}"
        )
    click.echo("  steps:")
    for s in est.steps:
        if s.value is None:
            click.echo(f"    - {s.description}")
        else:
            click.echo(f"    - {s.description}: {s.value:,.4f}")
    click.echo("  caveats:")
    for c in est.caveats:
        click.echo(f"    - {c}")
    click.echo("  sources:")
    for pid, urls in est.sources.items():
        for u in urls:
            click.echo(f"    - [{pid}] {u}")


@cli.command()
@click.argument("program_ids", nargs=-1, type=int, required=True)
@click.option("--spend", "qualifying_spend", type=float, required=True)
@click.option("--atl-spend", "atl_spend", type=float, default=0.0)
@click.option("--fx-target", default="USD")
@click.option("--full", is_flag=True, help="Print full breakdown per program (not just the table).")
def compare(program_ids: tuple[int, ...], qualifying_spend: float, atl_spend: float,
            fx_target: str, full: bool):
    """Compare estimates across multiple programs at the same qualifying_spend."""
    table = compare_programs(
        list(program_ids), qualifying_spend, atl_spend,
        fx_target=fx_target,
    )
    click.echo(
        f"Qualifying spend: {qualifying_spend:,.0f} (fx_target={table.fx_target})\n"
    )
    click.echo(f"{'id':>4}  {'jurisdiction':<16}  {'program':<60}  "
               f"{'rule':<12}  {'gross':>18}  {'usd':>14}  {'caveats':>8}")
    for r in table.rows:
        gross = _render_money(r.gross_estimate, r.currency)
        usd = f"USD {r.estimate_usd:,.0f}" if r.estimate_usd is not None else "—"
        click.echo(
            f"{r.program_id:>4}  {r.jurisdiction_display:<16}  {r.program_name[:60]:<60}  "
            f"{r.rule_applied:<12}  {gross:>18}  {usd:>14}  {r.caveats_count:>8}"
        )
    if full:
        click.echo()
        for est in table.estimates:
            click.echo("─" * 80)
            _print_estimate(est)


@cli.command()
@click.option("--atl-eligible/--no-atl", default=None)
@click.option("--min-rate", "min_rate_pct", type=float, default=None)
@click.option("--max-min-spend-usd", "max_minimum_spend_usd", type=float, default=None)
@click.option("--country", default=None)
def find(atl_eligible: Optional[bool], min_rate_pct: Optional[float],
         max_minimum_spend_usd: Optional[float], country: Optional[str]):
    """Filter programs by common producer criteria."""
    matches = find_programs(
        atl_eligible=atl_eligible,
        min_rate_pct=min_rate_pct,
        max_minimum_spend_usd=max_minimum_spend_usd,
        country=country,
    )
    for p in matches:
        click.echo(f"  {p.id:>3}  {p.jurisdiction_display:<16}  {p.program_name[:60]:<60}  "
                   f"{p.headline_rate_pct:>5}%  atl={p.atl_eligible}")


if __name__ == "__main__":
    cli()
