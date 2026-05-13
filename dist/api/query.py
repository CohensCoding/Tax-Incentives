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
from typing import Callable, Literal, Optional

# NOTE: `click` is intentionally NOT imported at module top level. It is
# imported lazily inside `_cli()` below so consumers using only the Python
# API surface (estimate_rebate, get_program, find_programs, ...) can
# `from api.query import ...` without click installed. The CLI dispatch
# path still requires click; importing it inside `_cli()` makes that
# dependency explicit and confined.

from . import fx, rate_rules
from .breakdown import (
    EstimateStep,
    FxInfo,
    Money,
    ProducerSummary,
    RebateEstimate,
    RulePattern,
)
from .rate_rules import ProgramKey, RateRule


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
    qualifying_spend: Optional[float] = None,
    atl_spend: float = 0.0,
    *,
    qualifying_spend_by_currency: Optional[dict[str, float]] = None,
    monetization_discount_pct: Optional[float] = None,
    filing_fees: Optional[Money] = None,
    output_view: Literal["engineering", "producer"] = "engineering",
    fx_target: Optional[str] = None,
    db_path: Path = DEFAULT_DB,
) -> RebateEstimate:
    """Compute a gross rebate / credit estimate for one program.

    Qualifying spend can be supplied via EITHER path, never both:

    * `qualifying_spend` (float, in program-local currency) — the original
      path. Assumes the caller has pre-aggregated.

    * `qualifying_spend_by_currency` (dict[str, float]) — mixed-currency
      qualifying spend, e.g., ``{"USD": 12_000_000, "NZD": 28_000_000}``
      for a production whose DP is paid in USD and B-camera op is paid
      in NZD but both work in NZ and qualify under the 'used or consumed'
      test. Components convert through api.fx to the program's local
      currency before the rate rule runs; the breakdown records each
      conversion as a step.

    `atl_spend` is echoed in `inputs` but does not alter the math — none
    of the currently-loaded programs apply an ATL-specific cap.

    Optional post-rate adjustments (both additive to the breakdown, both
    factored into `cash_today`):

    * `monetization_discount_pct` — e.g., ``3`` for Cashet's 3% discount
      to monetize the rebate before it pays out. This is the producer's
      cash-today value, the number that goes on a budget top sheet.

    * `filing_fees` — a `Money` (amount + currency) covering tax-credit
      filing and audit fees. Converted to program-local currency at
      time of estimation and subtracted from the post-discount value.

    Set `fx_target` to additionally render the estimate (gross and
    cash-today) in another currency, with FX provenance attached.

    `output_view="producer"` adds a `ProducerSummary` to the returned
    estimate — same math, top-sheet-ready presentation. The engineering
    view (steps, caveats, sources) is always present regardless.
    """
    if (qualifying_spend is None) == (qualifying_spend_by_currency is None):
        raise ValueError(
            "Pass exactly one of `qualifying_spend` or "
            "`qualifying_spend_by_currency`."
        )
    if qualifying_spend is not None and qualifying_spend < 0:
        raise ValueError("qualifying_spend must be non-negative")
    if qualifying_spend_by_currency is not None:
        for ccy, amt in qualifying_spend_by_currency.items():
            if amt < 0:
                raise ValueError(
                    f"qualifying_spend_by_currency[{ccy!r}] must be non-negative"
                )
    if atl_spend < 0:
        raise ValueError("atl_spend must be non-negative")
    if monetization_discount_pct is not None and not (0 <= monetization_discount_pct <= 100):
        raise ValueError("monetization_discount_pct must be in [0, 100]")

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

    # ── 1. Resolve qualifying_spend to program-local currency ────────────
    pre_steps: list[EstimateStep] = []
    extra_caveats: list[str] = []
    if qualifying_spend is None:
        resolved_spend, pre_steps, extra_caveats = _resolve_multi_currency_spend(
            qualifying_spend_by_currency, program.currency,
        )
    else:
        resolved_spend = qualifying_spend

    # ── 2. Run the rate-rule handler on the resolved spend ───────────────
    estimate = handler(program, rule, resolved_spend, atl_spend)

    # ── 3. Apply monetization discount and filing fees (post-rate) ───────
    cash_today = estimate.gross_estimate
    post_steps: list[EstimateStep] = []
    if monetization_discount_pct is not None:
        discount_amt = cash_today * (monetization_discount_pct / 100.0)
        cash_today -= discount_amt
        post_steps.append(EstimateStep(
            description=f"Less {monetization_discount_pct:g}% monetization discount",
            value=-discount_amt,
        ))
    if filing_fees is not None:
        fees_in_program_ccy = _convert_money_to(filing_fees, program.currency, extra_caveats)
        cash_today -= fees_in_program_ccy
        if filing_fees.currency.upper() == program.currency.upper():
            fees_desc = f"Less filing/audit fees ({program.currency} {filing_fees.amount:,.2f})"
        else:
            fees_desc = (
                f"Less filing/audit fees ({filing_fees.currency} {filing_fees.amount:,.2f}"
                f" → {program.currency} {fees_in_program_ccy:,.2f})"
            )
        post_steps.append(EstimateStep(description=fees_desc, value=-fees_in_program_ccy))
        if cash_today < 0:
            extra_caveats.append(
                "Filing fees exceed the post-discount rebate value — cash-today "
                "is negative. Verify the fees figure."
            )

    has_post_adjustments = monetization_discount_pct is not None or filing_fees is not None
    if has_post_adjustments:
        post_steps.append(EstimateStep(
            description="Cash-today (gross less discount less filing fees)",
            value=cash_today,
        ))

    # ── 4. Splice pre + rate + post steps; carry over extra caveats ──────
    updated_steps = pre_steps + estimate.steps + post_steps
    updated_caveats = estimate.caveats + extra_caveats
    # Only echo Phase-1 inputs into `inputs` when they were actually used,
    # so the breakdown shape stays backward-compatible for callers that
    # don't touch the new parameters.
    extra_inputs: dict[str, object] = {}
    if qualifying_spend_by_currency is not None:
        extra_inputs["qualifying_spend_by_currency"] = qualifying_spend_by_currency
    if monetization_discount_pct is not None:
        extra_inputs["monetization_discount_pct"] = monetization_discount_pct
    if filing_fees is not None:
        extra_inputs["filing_fees"] = {
            "amount": filing_fees.amount,
            "currency": filing_fees.currency,
        }
    estimate = estimate.model_copy(update={
        "steps": updated_steps,
        "caveats": updated_caveats,
        "cash_today": cash_today if has_post_adjustments else None,
        "inputs": {**estimate.inputs, **extra_inputs},
    })

    # ── 5. fx_target cross-conversion ────────────────────────────────────
    if fx_target and fx_target.upper() != program.currency.upper():
        info = fx.lookup(program.currency, fx_target.upper())
        gross_in_target = estimate.gross_estimate * info.fx_rate
        cash_today_in_target = (
            estimate.cash_today * info.fx_rate if estimate.cash_today is not None else None
        )
        estimate = estimate.model_copy(update={
            "estimate_usd": gross_in_target if fx_target.upper() == "USD" else None,
            "fx": info,
        })
        if fx_target.upper() != "USD":
            estimate.steps.append(EstimateStep(
                description=f"Gross converted to {fx_target.upper()} at "
                            f"{info.fx_rate:.6f} ({program.currency}→{fx_target.upper()}, "
                            f"as of {info.fx_as_of})",
                value=gross_in_target,
            ))
            if cash_today_in_target is not None:
                estimate.steps.append(EstimateStep(
                    description=f"Cash-today converted to {fx_target.upper()}",
                    value=cash_today_in_target,
                ))
        if info.fx_stale:
            estimate.caveats.append(
                f"FX rate {program.currency}→{fx_target.upper()} is stale "
                f"(as of {info.fx_as_of}, threshold 24h). Re-fetch before binding."
            )

    # ── 6. Producer view (additive — engineering view always present) ────
    if output_view == "producer":
        summary = _build_producer_summary(
            program, rule, estimate,
            monetization_discount_pct=monetization_discount_pct,
            filing_fees=filing_fees,
            fx_target=fx_target,
        )
        estimate = estimate.model_copy(update={"producer_summary": summary})

    return estimate


# ─── Multi-currency resolution + Money conversion helpers ───────────────

def _resolve_multi_currency_spend(
    by_currency: dict[str, float],
    program_currency: str,
) -> tuple[float, list[EstimateStep], list[str]]:
    """Sum a mixed-currency qualifying-spend dict into program-local
    currency. Returns (total, conversion_steps, extra_caveats)."""
    program_ccy = program_currency.upper()
    if not by_currency:
        raise ValueError("qualifying_spend_by_currency must be non-empty")
    steps: list[EstimateStep] = []
    extra_caveats: list[str] = []
    total = 0.0
    for raw_ccy, amount in by_currency.items():
        ccy = raw_ccy.upper()
        if ccy == program_ccy:
            total += amount
            steps.append(EstimateStep(
                description=f"Qualifying spend component: {ccy} {amount:,.2f} "
                            f"(no conversion needed — program currency)",
                value=amount,
            ))
            continue
        info = fx.lookup(ccy, program_ccy)
        converted = amount * info.fx_rate
        total += converted
        steps.append(EstimateStep(
            description=f"Qualifying spend component: {ccy} {amount:,.2f} → "
                        f"{program_ccy} {converted:,.2f} at {info.fx_rate:.6f} "
                        f"(as of {info.fx_as_of})",
            value=converted,
        ))
        if info.fx_stale:
            extra_caveats.append(
                f"FX rate {ccy}→{program_ccy} for qualifying-spend conversion "
                f"is stale (as of {info.fx_as_of}, threshold 24h). Re-fetch "
                "before binding."
            )
    steps.append(EstimateStep(
        description=f"Total qualifying spend in program currency ({program_ccy})",
        value=total,
    ))
    return total, steps, extra_caveats


def _convert_money_to(
    money: Money,
    target_currency: str,
    caveats_accumulator: list[str],
) -> float:
    """Convert a Money to target_currency. Records a stale-rate caveat
    into `caveats_accumulator` when applicable."""
    src = money.currency.upper()
    tgt = target_currency.upper()
    if src == tgt:
        return money.amount
    info = fx.lookup(src, tgt)
    if info.fx_stale:
        caveats_accumulator.append(
            f"FX rate {src}→{tgt} for filing-fees conversion is stale "
            f"(as of {info.fx_as_of}, threshold 24h). Re-fetch before binding."
        )
    return money.amount * info.fx_rate


# ─── Producer summary ───────────────────────────────────────────────────

def _build_producer_summary(
    program: IncentiveProgram,
    rule: RateRule,
    estimate: RebateEstimate,
    *,
    monetization_discount_pct: Optional[float],
    filing_fees: Optional[Money],
    fx_target: Optional[str],
) -> ProducerSummary:
    label = rule.producer_label or program.program_name

    # Display values: in fx_target if cross-currency was requested AND a
    # rate is available; otherwise in program currency. This matches how
    # producers actually paste numbers — the budget is in one currency.
    if fx_target and fx_target.upper() != program.currency.upper() and estimate.fx is not None:
        display_currency = fx_target.upper()
        gross_display = estimate.gross_estimate * estimate.fx.fx_rate
        cash_today_display = (
            (estimate.cash_today * estimate.fx.fx_rate)
            if estimate.cash_today is not None else gross_display
        )
    else:
        display_currency = program.currency
        gross_display = estimate.gross_estimate
        cash_today_display = (
            estimate.cash_today if estimate.cash_today is not None else gross_display
        )

    annotations: list[str] = []
    if monetization_discount_pct is not None:
        annotations.append(f"less {monetization_discount_pct:g}% monetization discount")
    if filing_fees is not None:
        annotations.append(
            f"less {filing_fees.currency} {filing_fees.amount:,.0f} filing fees"
        )
    suffix = f" ({', '.join(annotations)})" if annotations else ""
    top_sheet_line = (
        f"{label}{suffix}: -{display_currency} {cash_today_display:,.2f}"
    )

    headline_caveat = (
        estimate.caveats[0]
        if estimate.caveats
        else "No specific caveats recorded for this program."
    )

    engineering_view_ref = (
        f"Engineering view available via the same call without "
        f"output_view='producer' — includes {len(estimate.caveats)} caveats, "
        f"{len(estimate.steps)} worked-solution steps, and {sum(len(v) for v in estimate.sources.values())} source URLs."
    )

    return ProducerSummary(
        top_sheet_line=top_sheet_line,
        gross_rebate=Money(amount=gross_display, currency=display_currency),
        cash_today=Money(amount=max(cash_today_display, 0.0), currency=display_currency),
        headline_caveat=headline_caveat,
        engineering_view_ref=engineering_view_ref,
    )


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
            # Library function — use stdlib stderr so callers without click
            # installed still get the skip warning.
            print(f"  skipped program {pid}: {e}", file=sys.stderr)

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


# ─── CLI (deferred so library imports don't require click) ─────────────
#
# Everything click-related — decorators, click.echo helpers, click.option
# annotations, click.BadParameter / click.ClickException — lives inside
# `_cli()`. The function imports click on its first call and returns the
# Click `cli` group, fully wired. Library imports of api.query do not
# trigger this function, so consumers without click installed can use
# the Python API freely.

def _cli():
    """Build and return the Click CLI group.

    Imports click lazily and defines all CLI commands inside this
    function. Decorators only execute when `_cli()` is invoked (from
    `python -m api.query` via the `__main__` block below). This keeps
    the module's library surface independent of click.
    """
    import click

    def _render_money(amount: float, currency: str) -> str:
        return f"{currency} {amount:,.2f}"

    def _parse_spend_currency_args(args: tuple[str, ...]) -> dict[str, float]:
        """Parse repeated --spend-currency CCY=AMOUNT flags into a dict."""
        out: dict[str, float] = {}
        for raw in args:
            if "=" not in raw:
                raise click.BadParameter(
                    f"--spend-currency expects CCY=AMOUNT, got {raw!r}"
                )
            ccy, amt = raw.split("=", 1)
            try:
                out[ccy.strip().upper()] = float(amt)
            except ValueError as e:
                raise click.BadParameter(
                    f"--spend-currency amount for {ccy!r} must be a number: {amt!r}"
                ) from e
        return out

    def _print_producer_summary(est: RebateEstimate) -> None:
        """Render a single estimate's producer-view summary."""
        ps = est.producer_summary
        if ps is None:
            # Defensive: caller should only invoke this when summary is present.
            _print_estimate(est)
            return
        click.echo(ps.top_sheet_line)
        click.echo(f"  gross rebate:   {ps.gross_rebate.currency} {ps.gross_rebate.amount:,.2f}")
        if abs(ps.cash_today.amount - ps.gross_rebate.amount) > 0.01:
            click.echo(f"  cash today:     {ps.cash_today.currency} {ps.cash_today.amount:,.2f}")
        click.echo(f"  headline caveat: {ps.headline_caveat}")
        click.echo(f"  ({ps.engineering_view_ref})")

    def _print_estimate(est: RebateEstimate) -> None:
        click.echo(f"#{est.program_id}  {est.program_name}  [{est.jurisdiction_display}]")
        click.echo(f"  rule_applied:   {est.rule_applied}")
        click.echo(f"  gross_estimate: {_render_money(est.gross_estimate, est.currency)}")
        if est.cash_today is not None:
            click.echo(f"  cash_today:     {_render_money(est.cash_today, est.currency)}")
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
    @click.option("--spend", "qualifying_spend", type=float, default=None,
                  help="Qualifying spend in program-local currency.")
    @click.option("--spend-currency", "spend_currency_args", multiple=True,
                  help="Mixed-currency qualifying-spend component: CCY=AMOUNT. "
                       "Repeatable. Mutually exclusive with --spend.")
    @click.option("--atl-spend", "atl_spend", type=float, default=0.0)
    @click.option("--fx-target", default="USD", help="Cross-currency conversion target.")
    @click.option("--monetization-discount", "monetization_discount_pct", type=float,
                  default=None,
                  help="Monetization discount as a percentage (e.g., 3 for 3%).")
    @click.option("--filing-fees", "filing_fees_amount", type=float, default=None,
                  help="Filing/audit fees amount.")
    @click.option("--filing-fees-currency", default=None,
                  help="Currency for --filing-fees (default: program currency).")
    @click.option("--producer-view", "producer_view_flag", is_flag=True, default=False,
                  help="Output the top-sheet producer summary; engineering view is default.")
    def estimate(
        program_id: int,
        qualifying_spend: Optional[float],
        spend_currency_args: tuple[str, ...],
        atl_spend: float,
        fx_target: str,
        monetization_discount_pct: Optional[float],
        filing_fees_amount: Optional[float],
        filing_fees_currency: Optional[str],
        producer_view_flag: bool,
    ):
        """Estimate the rebate / credit for one program and show the breakdown."""
        by_currency: Optional[dict[str, float]] = (
            _parse_spend_currency_args(spend_currency_args) if spend_currency_args else None
        )
        fees: Optional[Money] = None
        if filing_fees_amount is not None:
            # Default fees currency to program currency if not specified.
            if filing_fees_currency is None:
                try:
                    prog_currency = get_program(program_id).currency
                except ProgramNotFound as e:
                    raise click.ClickException(str(e))
                filing_fees_currency = prog_currency
            fees = Money(amount=filing_fees_amount, currency=filing_fees_currency.upper())

        try:
            est = estimate_rebate(
                program_id,
                qualifying_spend=qualifying_spend,
                atl_spend=atl_spend,
                qualifying_spend_by_currency=by_currency,
                monetization_discount_pct=monetization_discount_pct,
                filing_fees=fees,
                output_view="producer" if producer_view_flag else "engineering",
                fx_target=fx_target,
            )
        except (ProgramNotFound, NoRateRule, ValueError) as e:
            raise click.ClickException(str(e))

        if producer_view_flag and est.producer_summary is not None:
            _print_producer_summary(est)
        else:
            _print_estimate(est)

    @cli.command()
    @click.argument("program_ids", nargs=-1, type=int, required=True)
    @click.option("--spend", "qualifying_spend", type=float, required=True)
    @click.option("--atl-spend", "atl_spend", type=float, default=0.0)
    @click.option("--fx-target", default="USD")
    @click.option("--monetization-discount", "monetization_discount_pct", type=float,
                  default=None,
                  help="Monetization discount as a percentage; applied to every program.")
    @click.option("--filing-fees", "filing_fees_amount", type=float, default=None,
                  help="Filing/audit fees applied to every program (display currency).")
    @click.option("--filing-fees-currency", default=None,
                  help="Currency for --filing-fees (default: --fx-target).")
    @click.option("--full", is_flag=True,
                  help="Output the full engineering breakdown. Default is the "
                       "producer-view top-sheet summary table.")
    def compare(
        program_ids: tuple[int, ...],
        qualifying_spend: float,
        atl_spend: float,
        fx_target: str,
        monetization_discount_pct: Optional[float],
        filing_fees_amount: Optional[float],
        filing_fees_currency: Optional[str],
        full: bool,
    ):
        """Compare estimates across multiple programs at the same qualifying_spend.

        Default output is the producer-view summary (one top-sheet line per
        program plus headline caveats). Pass --full to get the engineering
        breakdown per program.
        """
        fees: Optional[Money] = None
        if filing_fees_amount is not None:
            # For compare, the same Money is applied to every program, so its
            # currency must be supplied explicitly. Default to fx_target.
            fees = Money(
                amount=filing_fees_amount,
                currency=(filing_fees_currency or fx_target).upper(),
            )

        # Build each estimate with the appropriate view.
        view: Literal["engineering", "producer"] = "engineering" if full else "producer"
        estimates: list[RebateEstimate] = []
        for pid in program_ids:
            try:
                estimates.append(estimate_rebate(
                    pid,
                    qualifying_spend=qualifying_spend,
                    atl_spend=atl_spend,
                    monetization_discount_pct=monetization_discount_pct,
                    filing_fees=fees,
                    output_view=view,
                    fx_target=fx_target,
                ))
            except (ProgramNotFound, NoRateRule, ValueError) as e:
                click.echo(
                    click.style(f"  skipped program {pid}: {e}", fg="yellow"),
                    err=True,
                )

        click.echo(f"Qualifying spend: {qualifying_spend:,.0f} (fx_target={fx_target})")
        if monetization_discount_pct is not None:
            click.echo(f"Applied monetization discount: {monetization_discount_pct:g}%")
        if fees is not None:
            click.echo(f"Applied filing fees: {fees.currency} {fees.amount:,.2f}")
        click.echo()

        if not full:
            # Producer view: top-sheet lines per program, sorted by cash-today USD desc.
            ranked = sorted(
                estimates,
                key=lambda e: (e.estimate_usd or 0.0, e.gross_estimate),
                reverse=True,
            )
            for est in ranked:
                if est.producer_summary is None:
                    _print_estimate(est)
                    continue
                ps = est.producer_summary
                click.echo("  " + ps.top_sheet_line)
                click.echo(f"      gross:           {ps.gross_rebate.currency} "
                           f"{ps.gross_rebate.amount:,.2f}")
                # Only show cash-today as a separate line when it differs from
                # gross (i.e., discount or fees were applied). Same number twice
                # is noise.
                if abs(ps.cash_today.amount - ps.gross_rebate.amount) > 0.01:
                    click.echo(f"      cash today:      {ps.cash_today.currency} "
                               f"{ps.cash_today.amount:,.2f}")
                click.echo(f"      headline caveat: {ps.headline_caveat}")
                click.echo()
        else:
            # Engineering view: same compact table the original CLI showed,
            # followed by full breakdowns per program.
            ranked = sorted(
                estimates,
                key=lambda e: (e.estimate_usd or 0.0, e.gross_estimate),
                reverse=True,
            )
            click.echo(f"{'id':>4}  {'jurisdiction':<16}  {'program':<60}  "
                       f"{'rule':<12}  {'gross':>18}  {'usd':>14}  {'caveats':>8}")
            for est in ranked:
                gross = _render_money(est.gross_estimate, est.currency)
                usd = f"USD {est.estimate_usd:,.0f}" if est.estimate_usd is not None else "—"
                click.echo(
                    f"{est.program_id:>4}  {est.jurisdiction_display:<16}  "
                    f"{est.program_name[:60]:<60}  {est.rule_applied:<12}  "
                    f"{gross:>18}  {usd:>14}  {len(est.caveats):>8}"
                )
            click.echo()
            for est in ranked:
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

    return cli


if __name__ == "__main__":
    _cli()()
