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
been verified against a live or archived official source. Downstream
consumers (the budgeting AI, LLM exports, public APIs) must run that gate
before producing output. Unverified entries are allowed inside the DB during
development; they must not leak out.

---

## What's in the database right now

| Jurisdiction   | Program                                              | Headline rate | ATL eligible | Verification              |
| -------------- | ---------------------------------------------------- | ------------- | ------------ | ------------------------- |
| United Kingdom | Audio-Visual Expenditure Credit (AVEC) — Film        | 34%           | Yes          | model_knowledge_unverified |
| United Kingdom | Independent Film Tax Credit (IFTC)                   | 53%           | Yes          | model_knowledge_unverified |
| New Zealand    | NZ Screen Production Rebate — International (NZSPR)  | 20% (+5%)     | Yes (capped) | model_knowledge_unverified |

All three current entries are blocked by the export gate until they are
re-verified against a live or archived official source. See **Fetch access
status** below for why.

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
python -m scripts.validate files     # check JSON files (warns on unverified)
python -m scripts.validate db        # check loaded DB   (warns on unverified)
python -m scripts.validate all       # both
python -m scripts.validate export    # strict gate: fails on unverified entries
```

`export` is the gate any downstream consumer (LLM context export, JSON/CSV
dump, public API) MUST pass before producing output. It refuses to let
`model_knowledge_unverified` entries leak to producers.

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
USD or any other cross-currency comparison happens **at query time**, and the
downstream `estimate_rebate` function (planned for milestone 3) must record:

1. The FX rate it used.
2. The `as_of` date of that rate.
3. A staleness flag if the rate is older than 24 hours.

A 5% FX swing changes producer decisions, so every cross-currency comparison
must show its FX provenance the same way it shows its incentive provenance.

## Fetch access status

The Tier-1 seed entries (UK AVEC, UK IFTC, NZ NZSPR) currently carry
`verification_method = "model_knowledge_unverified"` because the canonical
sources could not be fetched during the initial build:

* `gov.uk`, `bfi.org.uk`, `nzfilm.co.nz`, and the Wayback Machine all
  returned an identical 21-byte `"Host not in allowlist"` response from the
  sandbox egress proxy.
* This is **not** a CDN-level User-Agent block — the proxy refuses the
  connection regardless of headers, content-API endpoint, or archive
  fallback. The only confirmed reachable host from the sandbox is PyPI.

Until at least one of UK or NZ is verified end-to-end from a real source, no
new jurisdictions will be added and the query interface
(`api/query.py`, `estimate_rebate`) will not be built. The pipeline must be
proven against a real source before it is scaled.

**Options to unblock**, in rough order of preference:

1. Expand the sandbox egress allowlist to include `gov.uk`, `bfi.org.uk`,
   `nzfilm.co.nz`, and `web.archive.org`. Then run the per-jurisdiction
   scrapers under `scripts/scrape/` (to be written).
2. Run the scraping pipeline in an unrestricted environment and commit the
   resulting `data/raw/{jurisdiction}/...` files and updated
   `data/processed/*.json`.
3. Manually download the canonical PDFs/HTML from the official sites and drop
   them into `data/raw/{jurisdiction}/{YYYY-MM-DD}_{source_name}.{ext}`. A
   parser then reads from `data/raw/` only, no network required, and flips
   the entry to `verification_method = "official_source_archived"`.

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
