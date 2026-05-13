"""The breakdown contract — the shape of every value `estimate_rebate` returns.

Once this ships, downstream consumers (scenario comparison UI, LLM agents,
the budgeting tool) start depending on it. Add fields conservatively;
removing or renaming fields after the first release breaks consumers.

The contract is deliberately verbose because the function's first job is
showing its work, not producing a single number. `caveats` is load-bearing —
it's where everything that can't be reduced to math goes (cultural test
status, eligibility ceilings the tool can't verify, transitional rules,
post-CT net benefit explanations). A producer seeing only `gross_estimate`
without `caveats` will assume the number is binding; the caveats are how the
function refuses to let that assumption stand.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


RulePattern = Literal["flat", "capped_base", "stacking"]


class Money(BaseModel):
    """Currency-tagged amount.

    Used for inputs that carry their own currency, distinct from the
    program's local currency — e.g., filing fees that a producer pays in
    USD or AUD to an accountant working on a UK production. The estimator
    converts to program-local currency via api.fx when applying the value.
    """

    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class FxInfo(BaseModel):
    """Currency conversion provenance. Present only when the estimate is
    rendered in a currency other than the program's local currency."""

    model_config = ConfigDict(extra="forbid")

    fx_rate: float = Field(gt=0, description="Units of target currency per 1 unit of source currency")
    fx_as_of: date
    fx_stale: bool = Field(description="True when the rate is older than 24 hours")


class EstimateStep(BaseModel):
    """One row of the estimator's worked solution. `value` may be null for
    purely qualitative steps (e.g., 'Applied: capped_base rule')."""

    model_config = ConfigDict(extra="forbid")

    description: str
    value: Optional[float] = None


class ProducerSummary(BaseModel):
    """Top-sheet-ready summary of an estimate, derived from the same math
    as the engineering breakdown but presented for the producer who needs
    a single line they can paste into a budget.

    `top_sheet_line` matches the format real production budgets use:
    `"<jurisdiction label> (<modifiers>): -<currency> <amount>"`. The
    `gross_rebate` and `cash_today` figures are also exposed separately
    so downstream tools can format them differently if needed.

    `headline_caveat` is deliberately a single string, not a list — the
    producer view's job is to surface the ONE most important thing the
    producer must verify, not the full list. The engineering view (the
    parent `RebateEstimate.caveats` list) is always still available; the
    `engineering_view_ref` field tells the caller how to get it.
    """

    model_config = ConfigDict(extra="forbid")

    top_sheet_line: str
    gross_rebate: Money
    cash_today: Money
    headline_caveat: str
    engineering_view_ref: str


class RebateEstimate(BaseModel):
    """Result of `estimate_rebate(program_id, qualifying_spend, atl_spend, ...)`.

    Fields:
      program_id, program_name, jurisdiction_display
          Identifies the program the estimate is for.
      inputs
          The raw inputs as received. Mirroring inputs into the breakdown
          makes downstream artifacts (scenario logs, LLM context) self-
          describing without a separate request log.
      gross_estimate, currency
          The headline number, in the program's local currency. Always
          'gross' — the function never silently nets for corporation tax
          or withholding; net-of-tax interpretation belongs in caveats.
      cash_today
          Optional. The gross less monetization discount less filing fees,
          in the program's local currency. Populated only when at least
          one of `monetization_discount_pct` or `filing_fees` was passed
          to `estimate_rebate`. This is the number that goes on a budget
          top sheet.
      estimate_usd, fx
          Populated only when the caller asks for a USD cross-conversion.
          fx carries the rate, as-of date, and a hard 24h staleness flag.
      rule_applied
          Which of the three patterns produced this estimate. Useful for
          downstream consumers that want to render different layouts per
          pattern.
      steps
          Ordered worked solution. Read top-to-bottom, the math should be
          reproducible by hand. May include pre-rate currency-conversion
          steps and post-rate discount / filing-fee steps.
      caveats
          Ordered prose flags for things the estimator cannot verify.
          Eligibility tests, transitional rules, post-tax-net adjustments,
          cap interactions the math abstracts over. NEVER empty for a
          program that has any non-trivial structure.
      sources
          program_id -> list of source URLs. For a single-program estimate
          this dict has one key; reserved as a dict because compare-style
          renderings stitch many estimates' sources together.
      producer_summary
          Optional. Populated when `output_view='producer'` is passed to
          `estimate_rebate`. Both views are layered on top of the same
          underlying calculation — same numbers, different presentation.
    """

    model_config = ConfigDict(extra="forbid")

    program_id: int
    program_name: str
    jurisdiction_display: str

    inputs: dict[str, Any]

    gross_estimate: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    cash_today: Optional[float] = Field(default=None, ge=0)

    estimate_usd: Optional[float] = Field(default=None, ge=0)
    fx: Optional[FxInfo] = None

    rule_applied: RulePattern
    steps: list[EstimateStep]
    caveats: list[str]
    sources: dict[int, list[str]]

    producer_summary: Optional[ProducerSummary] = None
