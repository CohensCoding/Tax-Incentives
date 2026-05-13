# Demo: UK vs NZ at GBP/NZD 40,000,000 qualifying spend

A pinned reference snapshot for the marquee scenario the project answers:
*"For a production with ~$40M of qualifying spend, what is each available
program likely to pay, and what do I need to verify before relying on the
number?"*

Programs compared (current IDs in parentheses, looked up by the natural
key in the test):

* AVEC for Film (5) — UK, flat 34%
* Enhanced AVEC / IFTC (6) — UK, capped_base 53% on first £12M qualifying
* NZSPR International — Live Action Production Rebate (1) — NZ, flat 20%
* NZSPR International — Production Rebate 5% Uplift (2) — NZ, stacking +5%

Generated with:

```
python -m api.query compare 5 6 1 2 --spend 40000000 --full
```

`tests/test_demo_snapshot.py` invokes that exact command and diffs against
the fenced output below. If a future change shifts any of the numbers,
caveats, sources, or rule attributions for this scenario, the test fails
loudly. To accept a change deliberately, regenerate this file and commit
the new snapshot alongside the code change.

> **Note on FX staleness.** The `(STALE)` flag and trailing caveat appear
> because `api/fx.py` is a hand-maintained stub whose rates carry an
> explicit `as_of` date; once that date is more than 24 hours in the past,
> every cross-currency estimate carries a stale-rate flag. This is the
> framework's load-bearing reminder that USD figures are illustrative
> until the FX module is wired to a real feed.

```
Qualifying spend: 40,000,000 (fx_target=USD)

  id  jurisdiction      program                                                       rule                       gross             usd   caveats
   5  United Kingdom    Audio-Visual Expenditure Credit (AVEC) — Film                 flat           GBP 13,600,000.00  USD 17,272,000         7
   6  United Kingdom    Enhanced AVEC for Independent Film (IFTC)                     capped_base     GBP 6,360,000.00   USD 8,077,200        11
   1  New Zealand       NZSPR International — Live Action Production Rebate (20%)     flat            NZD 8,000,000.00   USD 4,800,000         7
   2  New Zealand       NZSPR International — Production Rebate 5% Uplift             stacking        NZD 2,000,000.00   USD 1,200,000         6

────────────────────────────────────────────────────────────────────────────────
#5  Audio-Visual Expenditure Credit (AVEC) — Film  [United Kingdom]
  rule_applied:   flat
  gross_estimate: GBP 13,600,000.00
  estimate_usd:   USD 17,272,000.00  @ 1.270000  as_of=2026-05-12 (STALE)
  steps:
    - Headline rate (34.0%): 0.3400
    - Qualifying spend submitted: 40,000,000.0000
    - Gross estimate = 0.3400 × 40,000,000.00: 13,600,000.0000
  caveats:
    - Assumes the production has a BFI British cultural certificate (interim or final) or qualifies as an official co-production. Not verified by this tool.
    - Assumes at least 10% of total core expenditure is UK expenditure. Not verified by this tool.
    - AVEC qualifying expenditure is capped at the lower of 80% of total core costs or actual UK core costs. The qualifying_spend input must already reflect this rule; the tool does not re-apply it.
    - Gross expenditure credit reported here is taxable at the UK main Corporation Tax rate (currently 25%); net benefit after CT is approximately 0.75 × the gross figure shown.
    - The AVEC VFX Additional Credit (39%) applies separately to qualifying VFX costs from 1 January 2025. To estimate it, call estimate_rebate on the VFX program with VFX-only qualifying spend; sum with this estimate. The 80% qualifying cap does NOT apply to VFX costs.
    - The qualifying_spend input is taken at face value. The tool does NOT verify it against the program's qualifying-cost definition; producers must compute it correctly for the target jurisdiction (e.g., HMRC 'used or consumed' test for UK; QNZPE rules for NZ).
    - FX rate GBP→USD is stale (as of 2026-05-12, threshold 24h). Re-fetch before binding.
  sources:
    - [5] https://www.gov.uk/guidance/claim-audio-visual-expenditure-credits-for-corporation-tax
    - [5] https://www.bfi.org.uk/apply-british-certification-expenditure-credits/about-uk-creative-industry-expenditure-credits
────────────────────────────────────────────────────────────────────────────────
#6  Enhanced AVEC for Independent Film (IFTC)  [United Kingdom]
  rule_applied:   capped_base
  gross_estimate: GBP 6,360,000.00
  estimate_usd:   USD 8,077,200.00  @ 1.270000  as_of=2026-05-12 (STALE)
  steps:
    - Headline rate (53.0%): 0.5300
    - Qualifying spend submitted: 40,000,000.0000
    - Base cap on qualifying spend at headline rate (GBP 12,000,000): 12,000,000.0000
    - Effective qualifying spend = min(submitted, cap) [cap binds]: 12,000,000.0000
    - Gross estimate = 0.5300 × 12,000,000.00: 6,360,000.0000
    - Note: qualifying spend above GBP 12,000,000 is not eligible at this rate under the capped_base pattern.
  caveats:
    - Assumes the production has a BFI British cultural certificate (interim or final) or qualifies as an official co-production. Not verified by this tool.
    - Assumes at least 10% of total core expenditure is UK expenditure. Not verified by this tool.
    - AVEC qualifying expenditure is capped at the lower of 80% of total core costs or actual UK core costs. The qualifying_spend input must already reflect this rule; the tool does not re-apply it.
    - Gross expenditure credit reported here is taxable at the UK main Corporation Tax rate (currently 25%); net benefit after CT is approximately 0.75 × the gross figure shown.
    - Eligibility ceiling: total core expenditure must not exceed £23.5M. The tool cannot verify total core expenditure from a qualifying_spend input — productions with total core > £23.5M are ineligible for IFTC and must claim standard AVEC at 34% instead.
    - Base-rate cap: the 53% rate is paid on at most £15M of core expenditure (equivalent to £12M of qualifying spend after the 80% rule). For productions with total core between £15M and £23.5M, the portion above £15M is not eligible under IFTC.
    - Creative connection: requires UK lead director, UK lead writer, or qualification as an official co-production. Not verified by this tool.
    - Principal photography must have begun on or after 1 April 2024 for Enhanced AVEC eligibility. Claims are payable from 1 April 2025.
    - The VFX Additional Credit is NOT available alongside Enhanced AVEC.
    - The qualifying_spend input is taken at face value. The tool does NOT verify it against the program's qualifying-cost definition; producers must compute it correctly for the target jurisdiction (e.g., HMRC 'used or consumed' test for UK; QNZPE rules for NZ).
    - FX rate GBP→USD is stale (as of 2026-05-12, threshold 24h). Re-fetch before binding.
  sources:
    - [6] https://www.gov.uk/guidance/claim-audio-visual-expenditure-credits-for-corporation-tax
    - [6] https://www.bfi.org.uk/apply-british-certification-expenditure-credits/about-uk-creative-industry-expenditure-credits
────────────────────────────────────────────────────────────────────────────────
#1  NZSPR International — Live Action Production Rebate (20%)  [New Zealand]
  rule_applied:   flat
  gross_estimate: NZD 8,000,000.00
  estimate_usd:   USD 4,800,000.00  @ 0.600000  as_of=2026-05-12 (STALE)
  steps:
    - Headline rate (20.0%): 0.2000
    - Qualifying spend submitted: 40,000,000.0000
    - Gross estimate = 0.2000 × 40,000,000.00: 8,000,000.0000
  caveats:
    - Assumes the production is eligible: applicant is a NZ entity (typically an SPV); production is intended for theatrical release, TV broadcast, or commercial online distribution; pre-registered with NZFC before principal photography; QNZPE ≥ NZD 4M. Productions that started principal photography before 1 January 2026 require the legacy NZD 15M threshold for theatrical features (see change_log).
    - ATL is fully eligible without cap (post-7 November 2025 reform). Pre-1-January-2026 productions are assessed under earlier Criteria with ATL cap provisions — see change_log and data/raw/new_zealand/CRITERIA_FETCH_STATUS.md.
    - QNZPE for non-resident non-cast personnel requires they work on the production for ≥14 days total; cast members are exempt. The tool does not validate this against the supplied qualifying_spend.
    - A separate 'Production Rebate 5% Uplift' program stacks additively for productions meeting points (40/85) and minimum-spend (NZD 20M) criteria. Call estimate_rebate on that program separately to compute the combined 25% expected rebate.
    - Cash rebate paid ~3 months after the final audited application is accepted, then ~10 business days from invoice. Interim payment available once QNZPE ≥ NZD 50M.
    - The qualifying_spend input is taken at face value. The tool does NOT verify it against the program's qualifying-cost definition; producers must compute it correctly for the target jurisdiction (e.g., HMRC 'used or consumed' test for UK; QNZPE rules for NZ).
    - FX rate NZD→USD is stale (as of 2026-05-12, threshold 24h). Re-fetch before binding.
  sources:
    - [1] https://www.nzfilm.co.nz/incentives/rebate-international-nzspr
    - [1] https://www.nzfilm.co.nz/news/updated-criteria-for-changes-to-international-rebate
────────────────────────────────────────────────────────────────────────────────
#2  NZSPR International — Production Rebate 5% Uplift  [New Zealand]
  rule_applied:   stacking
  gross_estimate: NZD 2,000,000.00
  estimate_usd:   USD 1,200,000.00  @ 0.600000  as_of=2026-05-12 (STALE)
  steps:
    - Stacking uplift rate (5.0%): 0.0500
    - Qualifying spend submitted: 40,000,000.0000
    - Gross uplift = 0.0500 × 40,000,000.00 (additive on top of parent 'NZSPR International — Live Action Production Rebate (20%)'): 2,000,000.0000
  caveats:
    - This is the uplift only. Stacks additively with the parent Live Action Production Rebate (20%). Call estimate_rebate on the parent program with the same qualifying_spend and sum the two gross_estimates to get the combined 25% expected rebate.
    - Awarded on points basis (minimum 40 of 85 across sustainability, NZ production activity, NZ personnel, skills/talent, innovation/infrastructure, marketing). NOT automatic for spend-qualifying productions. The tool does not validate the points claim.
    - Minimum QNZPE NZD 20M for productions starting principal photography on or after 1 January 2026 (NZD 30M for earlier productions).
    - Provisional Certification is mandatory and must be submitted before principal photography begins.
    - The qualifying_spend input is taken at face value. The tool does NOT verify it against the program's qualifying-cost definition; producers must compute it correctly for the target jurisdiction (e.g., HMRC 'used or consumed' test for UK; QNZPE rules for NZ).
    - FX rate NZD→USD is stale (as of 2026-05-12, threshold 24h). Re-fetch before binding.
  sources:
    - [2] https://www.nzfilm.co.nz/incentives/rebate-international-nzspr-5-percent
    - [2] https://www.nzfilm.co.nz/incentives/rebate-international-nzspr
    - [2] https://www.nzfilm.co.nz/news/updated-criteria-for-changes-to-international-rebate
```
