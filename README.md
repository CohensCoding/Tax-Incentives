# Film & TV Production Tax Incentive Database

A structured, queryable database of global film and television production tax
incentives. It exists to support producers, line producers, and the planning
tools they use — when you need to compare jurisdictions on rebate rate, what
qualifies, minimum spend, caps, above-the-line treatment, and how the money
actually gets paid, this is where you look first.

This database is the foundation for a larger AI-powered budgeting and
scenario-planning tool. The data is designed to be consumed by both humans (via
the CLI and a markdown export) and language models (via structured queries and
an LLM-context export).

---

## ⚠️ Disclaimer — read this first

**This database is a planning aid, not a binding source of truth.** Tax
incentive programs change frequently. The fine print on what qualifies, what
caps apply, and how above-the-line spend is treated has nuances that require
local legal and accounting expertise.

**Do not rely on this data for binding financial decisions** without verifying
against current official sources and consulting a qualified production
accountant in the target jurisdiction. Every program entry links back to its
official source — use those links.

Every program entry carries a structured `verification_method` field
(`official_source_live`, `official_source_archived`, `secondary_source`,
`model_knowledge_unverified`). The export gate
(`python -m scripts.validate export`) hard-fails on any program that has not
been verified against a live or archived official source, AND on any
verified program whose source `local_path` doesn't point to a real file
under `data/raw/`. Downstream consumers (the budgeting AI, LLM exports,
public APIs) must run that gate before producing output. Unverified
entries are allowed inside the DB during development; they must not leak
out.

The current Tier-1 entries (UK AVEC + IFTC + VFX credit; NZ Production /
PDV Rebates ± 5% Uplifts) are `official_source_archived` — parsed from
captures of the canonical gov.uk, BFI, and NZFC pages held under
`data/raw/`.

---

## What's in the database right now

| Jurisdiction   | Program                                              | Headline rate | ATL eligible | Verification              | Verified |
| -------------- | ---------------------------------------------------- | ------------- | ------------ | ------------------------- | -------- |
| United Kingdom | Audio-Visual Expenditure Credit (AVEC) — Film        | 34%           | Yes          | official_source_archived  | 2026-02-19 |
| United Kingdom | Enhanced AVEC for Independent Film (IFTC)            | 53%           | Yes          | official_source_archived  | 2026-02-19 |
| United Kingdom | AVEC VFX Additional Credit (Film & HETV)             | 39%           | No (VFX only)| official_source_archived  | 2026-02-19 |
| New Zealand    | NZSPR International — Live Action Production Rebate  | 20%           | Yes          | official_source_archived  | 2026-05-12 |
| New Zealand    | NZSPR International — Production Rebate 5% Uplift    | +5%           | Yes          | official_source_archived  | 2026-05-12 |
| New Zealand    | NZSPR International — PDV Rebate                     | 20%           | Yes          | official_source_archived  | 2026-05-12 |
| New Zealand    | NZSPR International — PDV Rebate 5% Uplift           | +5%           | Yes          | official_source_archived  | 2026-05-12 |

All entries are parsed from archived captures of the canonical sources held
under `data/raw/`. The export gate passes — downstream consumers may proceed.

Full Tier-1 / Tier-2 / Tier-3 jurisdiction roadmap lives in the project brief
and will be checked off as entries are added.

---

## Project layout

```
film-tax-incentives/
├── README.md
├── requirements.txt
├── schema/schema.sql              # SQLite schema
├── data/
│   ├── raw/                       # raw fetched HTML/PDF, timestamped (audit trail)
│   ├── processed/                 # human-reviewed structured JSON per jurisdiction
│   └── incentives.db              # generated SQLite database (gitignored)
├── scripts/
│   ├── models.py                  # Pydantic models shared by validate + load
│   ├── validate.py                # validation CLI
│   ├── load.py                    # rebuild DB from data/processed/*.json
│   ├── scrape/                    # per-jurisdiction fetchers (one module each)
│   └── parse/                     # per-jurisdiction parsers
├── api/
│   ├── breakdown.py               # locked RebateEstimate contract
│   ├── fx.py                      # FX lookup with as-of date and staleness flag
│   ├── rate_rules.py              # per-program rate-rule params + caveats (table-driven)
│   └── query.py                   # public API + CLI (estimate, compare, find, list)
└── tests/                         # pytest suite (run with `pytest tests/`)
```

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Common operations

### Rebuild the database

```bash
python -m scripts.load
```

This drops any existing `data/incentives.db`, recreates the schema, validates
every file in `data/processed/`, loads the data, and re-validates the loaded
database. The whole pipeline runs in seconds and is the canonical way to
refresh the database from source JSON.

### Validate without loading

```bash
python -m scripts.validate files     # check JSON files (warns on unverified)
python -m scripts.validate db        # check loaded DB   (warns on unverified)
python -m scripts.validate all       # both
python -m scripts.validate export    # strict gate: fails on unverified entries
```

`export` is the gate any downstream consumer (LLM context export, JSON/CSV
dump, public API) MUST pass before producing output. It refuses to let
`model_knowledge_unverified` entries leak to producers.

### Query

The query interface lives at `api/query.py` and offers both a Python API
and a CLI.

```bash
python -m api.query list --country "United Kingdom"
python -m api.query show 5
python -m api.query find --atl-eligible --min-rate 30 --country "New Zealand"
python -m api.query estimate --program 5 --spend 40000000 --fx-target USD
python -m api.query compare 5 6 1 2 --spend 40000000 --full
```

Every estimate returns a structured `RebateEstimate` (see `api/breakdown.py`)
with the program identification, raw inputs, gross figure in local
currency, an optional USD conversion with FX provenance, the rate-rule
pattern used (`flat`, `capped_base`, or `stacking`), an ordered worked
solution in `steps`, an ordered `caveats` list, and the source URLs for
the program. The function never returns a number without `caveats`
populated — the caveats list is the load-bearing place where eligibility
preconditions, transitional rules, and post-tax interpretation notes go
that the math cannot reduce to a number.

### Export for downstream consumers

```bash
python -m scripts.export --format llm-context --out exports/llm_context.md
python -m scripts.export --format csv     --out exports/programs.csv
python -m scripts.export --format json    --out exports/programs.json
```

All three formats run the strict export gate first — they refuse to
write output if any program is `model_knowledge_unverified` or if a
verified program's source `local_path` doesn't point to a real file
under `data/raw/`.

---

## How to add or update a jurisdiction

1. **Fetch the official source.** Save raw HTML/PDF under
   `data/raw/{jurisdiction_slug}/{YYYY-MM-DD}_{source_name}.{ext}` so there's
   an audit trail of exactly what was parsed.
2. **Write or update a per-jurisdiction scraper/parser** under
   `scripts/scrape/` and `scripts/parse/`. Do not build a generic scraper —
   every official site is shaped differently and heuristic parsers produce
   the kind of subtle errors that compound downstream.
3. **Produce a `data/processed/{slug}.json` file.** Follow the Pydantic model
   in `scripts/models.py`. Set unknown fields to `null` and explain in the
   `notes` field; never guess.
4. **Reference the archived artifact.** When you set `verification_method` to
   `official_source_live` or `official_source_archived`, at least one entry
   in `sources` must include a `local_path` field pointing at the saved file
   under `data/raw/`. The validator hard-fails if the file doesn't exist —
   you can't claim verification without leaving the artifact behind.
5. **Run `python -m scripts.validate files`** and fix every error.
6. **Run `python -m scripts.load`** to rebuild the database. Material changes
   to tracked fields (`headline_rate_pct`, `incentive_type`,
   `verification_method`, `last_verified_date`, `atl_eligible`,
   `minimum_spend`, `cap_per_project`, `annual_program_cap`,
   `qualifying_budget_ceiling`, `sunset_date`) are automatically recorded in
   `change_log` by diffing against the previous DB state. Older `change_log`
   entries survive the rebuild.

### What counts as a valid source

Every program must have at least one `official_government` or `film_office`
source — no exceptions. Industry publications and legal summaries are allowed
as supplementary sources but cannot stand alone.

---

## Data freshness policy

* **180 days**: the validator warns when an entry's `last_verified_date` is
  older than 180 days. Re-verify against the official source.
* **365 days**: the validator hard-fails. Stale data is worse than no data.
* **On every update**: bump `last_verified_date` only after confirming the
  current source. Don't bump it as a convenience to silence the warning.
* **Verification method is structured**, not a free-text note. When a program
  moves from `model_knowledge_unverified` to `official_source_live`, the
  change is recorded in `change_log` and must be backed by a real, dated
  source fetch saved under `data/raw/`.

### FX / currency policy

Monetary fields (minimum spends, caps, ceilings) are stored in the
jurisdiction's local currency — **never** converted to USD inside the DB.
USD or any other cross-currency comparison happens **at query time** via
`api/fx.py`, and `estimate_rebate` always records:

1. The FX rate it used.
2. The `as_of` date of that rate.
3. A staleness flag set when the rate is older than 24 hours.

A 5% FX swing changes producer decisions, so every cross-currency
comparison shows its FX provenance the same way it shows its incentive
provenance. `api/fx.py` is currently a hand-maintained stub with
illustrative rates — replace its rates table with a real feed before
relying on USD figures in binding decisions.

## Fetch access status

The sandbox in which this database is being built blocks outbound HTTP to
every official jurisdiction source — `gov.uk`, `bfi.org.uk`,
`nzfilm.co.nz`, and `web.archive.org` all return a fixed 21-byte
`"Host not in allowlist"` from the egress proxy regardless of User-Agent,
content-API endpoint, or archive fallback. Only PyPI is reachable.

The Tier-1 entries were therefore verified via **manually downloaded
captures** (option 3 below) saved under `data/raw/{slug}/`. Their
`verification_method` is `official_source_archived` and every source row
points at the saved file. The same approach is the supported path for
adding any new jurisdiction from inside the sandbox.

**Available unblock paths**, in rough order of preference:

1. Expand the sandbox egress allowlist to include `gov.uk`, `bfi.org.uk`,
   `nzfilm.co.nz`, and `web.archive.org`. Then run per-jurisdiction
   scrapers under `scripts/scrape/` (to be written) that fetch live and
   flip entries to `official_source_live`.
2. Run the scraping pipeline in an unrestricted environment and commit the
   resulting `data/raw/{jurisdiction}/...` files plus updated
   `data/processed/*.json`.
3. **Manually capture the canonical HTML/PDFs and drop them into
   `data/raw/{jurisdiction}/{YYYY-MM-DD}_{source_name}.{ext}`** (the
   current Tier-1 approach). A parser then reads from `data/raw/` only,
   no network required, and the entry is flipped to
   `official_source_archived`. The validator's archive-file existence
   check requires the saved file to exist before the verification claim
   is accepted.

---

## Design principles (carried into every entry)

* **Accuracy over coverage.** A small, correct database is more useful than
  a large, sloppy one.
* **Show your work.** Every program links to its sources; every rebate
  estimate (once the query layer ships) will show its calculation breakdown.
* **Preserve raw sources.** Always save the timestamped HTML/PDF you parsed
  from. When a program changes, you need to be able to prove what it said
  when you parsed it.
* **Don't invent fields.** If a program doesn't publish its annual cap, leave
  the field null. Don't infer.
* **Flag uncertainty.** Use the `notes` field liberally.
* **Currency stays local.** Minimum spends and caps are stored in the
  jurisdiction's currency. USD conversion happens at query time with the
  conversion date logged — never baked into the database.
