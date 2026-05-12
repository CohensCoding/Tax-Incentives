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

The current Tier-1 seed entries (UK and New Zealand) were compiled during the
initial build of the database and explicitly flagged as `SEED ENTRY` in their
`notes` field. They require live re-verification against the cited sources
before being used in a production budget.

---

## What's in the database right now

| Jurisdiction   | Program                                              | Headline rate | ATL eligible | Last verified |
| -------------- | ---------------------------------------------------- | ------------- | ------------ | ------------- |
| United Kingdom | Audio-Visual Expenditure Credit (AVEC) — Film        | 34%           | Yes          | 2026-05-12*   |
| United Kingdom | Independent Film Tax Credit (IFTC)                   | 53%           | Yes          | 2026-05-12*   |
| New Zealand    | NZ Screen Production Rebate — International (NZSPR)  | 20% (+5%)     | Yes (capped) | 2026-05-12*   |

\* Seed entries — see Disclaimer above.

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
│   └── query.py                   # (next milestone) query interface for downstream tools
└── tests/
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
python -m scripts.validate files     # check JSON files
python -m scripts.validate db        # check loaded DB
python -m scripts.validate all       # both
```

### Query (next milestone)

The query interface (`api/query.py`) is the next planned milestone. It will
expose both a Python API (`find_programs`, `compare_programs`, `estimate_rebate`,
etc.) and a CLI (`python -m api.query list --country "Australia"`).

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
4. **Run `python -m scripts.validate files`** and fix every error.
5. **Run `python -m scripts.load`** to rebuild the database.
6. **Log material changes in the `change_log` table** when updating an existing
   program (rate changes, sunset extensions, etc.).

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
