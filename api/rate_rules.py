"""Hand-maintained rate-rule parameters and program-specific caveats.

This module is the seam between the data-as-code processed JSON and the
math the `estimate_rebate` function runs. It exists in this shape
deliberately:

* No schema change is needed for milestone 3 — programs stay defined by
  `data/processed/*.json` and the headline_rate_pct field there continues
  to be the single source for the rate itself.
* The auxiliary parameters needed to do the math (the IFTC £15M base cap,
  the 80% qualifying ceiling, the stacking parent for NZ uplifts) live
  here as small structured fields rather than getting parsed out of prose
  on every call.
* Per-program caveats — the load-bearing 'this estimate assumes X' list —
  live here too. When the data model gains a structured RateRule table
  (planned for milestone 4 or 5 once a third compositional-rate program
  shows up), the rows in RULES below move into that table verbatim and
  this file becomes a thin reader. The migration is data movement, not
  a rewrite.

Three patterns. Never branch on jurisdiction name — branch on `pattern`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProgramKey = tuple[str, str]  # (jurisdiction.display_name, program.program_name)

RulePattern = Literal["flat", "capped_base", "stacking"]


@dataclass(frozen=True)
class RateRule:
    """Per-program rate-rule parameters.

    The headline rate comes from the program row (`headline_rate_pct`);
    this dataclass holds only the auxiliary parameters and caveats.
    """

    pattern: RulePattern

    # capped_base: maximum qualifying spend (in program-local currency) to
    # which the headline rate applies. None when not applicable. For IFTC
    # the value here is the qualifying-spend equivalent of the £15M total-
    # core base cap — namely 80% × £15M = £12M, because the AVEC 80%-cap
    # rule already shrinks the £15M core figure to £12M when computed in
    # qualifying terms (UK-core costs may bind tighter, captured in caveats).
    base_cap_qualifying: float | None = None

    # stacking: natural key of the parent program this uplift attaches to.
    # estimate_rebate emits a caveat referencing the parent and returns
    # only the uplift's gross figure; caller sums.
    stacks_on: ProgramKey | None = None

    # Ordered prose flags. The function emits these verbatim. Order matters
    # for human readability — put eligibility-disqualifying conditions
    # first, then transitional rules, then post-tax interpretation notes.
    caveats: tuple[str, ...] = field(default_factory=tuple)


# Universal caveats appended to every estimate — things the function
# fundamentally cannot verify (the qualifying_spend input itself).
_UNIVERSAL_CAVEATS: tuple[str, ...] = (
    "The qualifying_spend input is taken at face value. The tool does NOT verify it "
    "against the program's qualifying-cost definition; producers must compute it correctly "
    "for the target jurisdiction (e.g., HMRC 'used or consumed' test for UK; QNZPE rules "
    "for NZ).",
)


_UK_AVEC_CAVEATS = (
    "Assumes the production has a BFI British cultural certificate (interim or final) or "
    "qualifies as an official co-production. Not verified by this tool.",
    "Assumes at least 10% of total core expenditure is UK expenditure. Not verified by this tool.",
    "AVEC qualifying expenditure is capped at the lower of 80% of total core costs or actual UK "
    "core costs. The qualifying_spend input must already reflect this rule; the tool does not "
    "re-apply it.",
    "Gross expenditure credit reported here is taxable at the UK main Corporation Tax rate "
    "(currently 25%); net benefit after CT is approximately 0.75 × the gross figure shown.",
)


RULES: dict[ProgramKey, RateRule] = {
    # ─── United Kingdom ────────────────────────────────────────────────
    ("United Kingdom", "Audio-Visual Expenditure Credit (AVEC) — Film"): RateRule(
        pattern="flat",
        caveats=_UK_AVEC_CAVEATS + (
            "The AVEC VFX Additional Credit (39%) applies separately to qualifying VFX costs "
            "from 1 January 2025. To estimate it, call estimate_rebate on the VFX program with "
            "VFX-only qualifying spend; sum with this estimate. The 80% qualifying cap does NOT "
            "apply to VFX costs.",
        ),
    ),
    ("United Kingdom", "Enhanced AVEC for Independent Film (IFTC)"): RateRule(
        pattern="capped_base",
        base_cap_qualifying=12_000_000.0,  # 80% × £15M total-core base cap
        caveats=_UK_AVEC_CAVEATS + (
            "Eligibility ceiling: total core expenditure must not exceed £23.5M. The tool "
            "cannot verify total core expenditure from a qualifying_spend input — productions "
            "with total core > £23.5M are ineligible for IFTC and must claim standard AVEC "
            "at 34% instead.",
            "Base-rate cap: the 53% rate is paid on at most £15M of core expenditure (equivalent "
            "to £12M of qualifying spend after the 80% rule). For productions with total core "
            "between £15M and £23.5M, the portion above £15M is not eligible under IFTC.",
            "Creative connection: requires UK lead director, UK lead writer, or qualification "
            "as an official co-production. Not verified by this tool.",
            "Principal photography must have begun on or after 1 April 2024 for Enhanced AVEC "
            "eligibility. Claims are payable from 1 April 2025.",
            "The VFX Additional Credit is NOT available alongside Enhanced AVEC.",
        ),
    ),
    ("United Kingdom", "AVEC VFX Additional Credit (Film & HETV)"): RateRule(
        pattern="flat",
        caveats=(
            "qualifying_spend for this program is VFX costs only, not total UK core costs. "
            "The caller is responsible for partitioning a production's spend into VFX and non-VFX "
            "buckets before calling.",
            "Available only for film and high-end TV productions claiming the standard 34% rate of "
            "AVEC. NOT available for productions claiming Enhanced AVEC (IFTC) or for animation / "
            "children's TV.",
            "VFX costs are exempt from the 80% cap on total core costs that applies to the parent "
            "AVEC claim.",
            "VFX costs incurred on or after 1 January 2025 are eligible; earlier costs do not "
            "qualify. The tool does not verify the incurred date of input spend.",
            "Gross expenditure credit reported here is taxable at the UK main Corporation Tax rate "
            "(currently 25%); net benefit after CT is approximately 0.75 × the gross figure shown.",
        ),
    ),

    # ─── New Zealand ───────────────────────────────────────────────────
    ("New Zealand", "NZSPR International — Live Action Production Rebate (20%)"): RateRule(
        pattern="flat",
        caveats=(
            "Assumes the production is eligible: applicant is a NZ entity (typically an SPV); "
            "production is intended for theatrical release, TV broadcast, or commercial online "
            "distribution; pre-registered with NZFC before principal photography; QNZPE ≥ NZD 4M. "
            "Productions that started principal photography before 1 January 2026 require the "
            "legacy NZD 15M threshold for theatrical features (see change_log).",
            "ATL is fully eligible without cap (post-7 November 2025 reform). Pre-1-January-2026 "
            "productions are assessed under earlier Criteria with ATL cap provisions — see "
            "change_log and data/raw/new_zealand/CRITERIA_FETCH_STATUS.md.",
            "QNZPE for non-resident non-cast personnel requires they work on the production for "
            "≥14 days total; cast members are exempt. The tool does not validate this against "
            "the supplied qualifying_spend.",
            "A separate 'Production Rebate 5% Uplift' program stacks additively for productions "
            "meeting points (40/85) and minimum-spend (NZD 20M) criteria. Call estimate_rebate "
            "on that program separately to compute the combined 25% expected rebate.",
            "Cash rebate paid ~3 months after the final audited application is accepted, then ~10 "
            "business days from invoice. Interim payment available once QNZPE ≥ NZD 50M.",
        ),
    ),
    ("New Zealand", "NZSPR International — Production Rebate 5% Uplift"): RateRule(
        pattern="stacking",
        stacks_on=("New Zealand", "NZSPR International — Live Action Production Rebate (20%)"),
        caveats=(
            "This is the uplift only. Stacks additively with the parent Live Action Production "
            "Rebate (20%). Call estimate_rebate on the parent program with the same qualifying_spend "
            "and sum the two gross_estimates to get the combined 25% expected rebate.",
            "Awarded on points basis (minimum 40 of 85 across sustainability, NZ production "
            "activity, NZ personnel, skills/talent, innovation/infrastructure, marketing). NOT "
            "automatic for spend-qualifying productions. The tool does not validate the points "
            "claim.",
            "Minimum QNZPE NZD 20M for productions starting principal photography on or after "
            "1 January 2026 (NZD 30M for earlier productions).",
            "Provisional Certification is mandatory and must be submitted before principal "
            "photography begins.",
        ),
    ),
    ("New Zealand", "NZSPR International — PDV Rebate (20%)"): RateRule(
        pattern="flat",
        caveats=(
            "qualifying_spend for this program is QNZPE on PDV (Post / Digital / Visual Effects) "
            "activity only — fees and expenses for PDV personnel, studio/office hire for PDV, "
            "PDV equipment, depreciation of PDV-assigned assets.",
            "Assumes the production is eligible: registered with NZFC within 20 working days of "
            "accepting a qualifying bid for PDV work; QNZPE ≥ NZD 250,000.",
            "ATL fully eligible without cap (post-7 November 2025 reform).",
            "A separate 'PDV Rebate 5% Uplift' program stacks additively from 1 January 2026 "
            "for productions meeting the points test. Call estimate_rebate on that program "
            "separately.",
        ),
    ),
    ("New Zealand", "NZSPR International — PDV Rebate 5% Uplift"): RateRule(
        pattern="stacking",
        stacks_on=("New Zealand", "NZSPR International — PDV Rebate (20%)"),
        caveats=(
            "This is the uplift only. Stacks additively with the parent PDV Rebate (20%). Call "
            "estimate_rebate on the parent program with the same qualifying_spend and sum the "
            "two gross_estimates to get the combined 25% expected rebate.",
            "Available only for PDV Activity starting on or after 1 January 2026.",
            "Points-based award: criteria are Promoting NZ PDV Activity, On-the-job training, "
            "Industry seminars, Education sector seminars. Minimum points threshold varies by "
            "QNZPE and is not published on the public overview page; consult NZFC for current "
            "values. The tool does not validate the points claim.",
            "Provisional Certification is OPTIONAL for the PDV Rebate 5% Uplift (unlike the "
            "Production Rebate Uplift, where it is mandatory).",
        ),
    ),
}


def get_rule(jurisdiction_display: str, program_name: str) -> RateRule | None:
    """Look up the rate rule for a program by natural key."""
    return RULES.get((jurisdiction_display, program_name))


def universal_caveats() -> tuple[str, ...]:
    return _UNIVERSAL_CAVEATS
