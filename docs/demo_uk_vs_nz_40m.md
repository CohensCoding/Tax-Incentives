# Demo: UK vs NZ at GBP/NZD 40,000,000 qualifying spend

A pinned reference snapshot for the marquee scenario the project answers:
*"For a production with ~$40M of qualifying spend, what does each available
program actually pay producers in cash-today terms — after the lender's
monetization discount and the accountant's filing fees — and what do I
need to verify before relying on the number?"*

The four UK + NZ programs compared (current IDs in parentheses, looked up
by natural key in the test):

* **UK AVEC for Film (5)** — flat 34%
* **UK Enhanced AVEC / IFTC (6)** — capped_base 53% on first £12M qualifying
* **NZ Live Action Production Rebate (1)** — flat 20%
* **NZ Production Rebate 5% Uplift (2)** — stacking +5%

The demo applies a 3% Cashet-style monetization discount and a NZ$75K
combined filing + audit fees provision — matching the structure of a real
producer budget line.

Generated with:

```
python -m api.query compare 5 6 1 2 --spend 40000000 \
  --monetization-discount 3 --filing-fees 75000 --filing-fees-currency NZD
```

Pass `--full` instead of the default to see the engineering breakdown
(steps, all caveats, sources) for every program.

`tests/test_demo_snapshot.py` runs the same command and diffs against the
fenced output below. Numbers, caveats, sources, or rule attributions
shifting on this scenario fails the test loudly with the regeneration
command.

> **Note on FX staleness.** `api/fx.py` is a hand-maintained stub whose
> rates carry an explicit `as_of` date. Once that date is more than 24h in
> the past, every cross-currency value carries a stale-rate flag and a
> trailing caveat. USD figures here are illustrative until the FX module
> is wired to a real feed (parked follow-up per the project plan).

```
Qualifying spend: 40,000,000 (fx_target=USD)
Applied monetization discount: 3%
Applied filing fees: NZD 75,000.00

  UK AVEC Film Credit (34%) (less 3% monetization discount, less NZD 75,000 filing fees): -USD 16,708,840.00
      gross:           USD 17,272,000.00
      cash today:      USD 16,708,840.00
      headline caveat: Assumes the production has a BFI British cultural certificate (interim or final) or qualifies as an official co-production. Not verified by this tool.

  UK Enhanced AVEC / IFTC (53%) (less 3% monetization discount, less NZD 75,000 filing fees): -USD 7,789,884.00
      gross:           USD 8,077,200.00
      cash today:      USD 7,789,884.00
      headline caveat: Assumes the production has a BFI British cultural certificate (interim or final) or qualifies as an official co-production. Not verified by this tool.

  New Zealand 20% Incentive Rebate (less 3% monetization discount, less NZD 75,000 filing fees): -USD 4,611,000.00
      gross:           USD 4,800,000.00
      cash today:      USD 4,611,000.00
      headline caveat: Assumes the production is eligible: applicant is a NZ entity (typically an SPV); production is intended for theatrical release, TV broadcast, or commercial online distribution; pre-registered with NZFC before principal photography; QNZPE ≥ NZD 4M. Productions that started principal photography before 1 January 2026 require the legacy NZD 15M threshold for theatrical features (see change_log).

  New Zealand 5% Production Uplift (less 3% monetization discount, less NZD 75,000 filing fees): -USD 1,119,000.00
      gross:           USD 1,200,000.00
      cash today:      USD 1,119,000.00
      headline caveat: This is the uplift only. Stacks additively with the parent Live Action Production Rebate (20%). Call estimate_rebate on the parent program with the same qualifying_spend and sum the two gross_estimates to get the combined 25% expected rebate.
```
