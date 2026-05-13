# Integrating the tax engine into another project

The `dist/` directory at the repo root is a **vendored snapshot** of the
tax incentive engine — the minimum set of files needed to call
`estimate_rebate` against the verified incentive database from another
project (e.g., First Look). It is built from source and committed at
release tags; consumers copy it in by hand.

It is intentionally **not a pip package**. There is no `pyproject.toml`,
no PyPI release, no version-pinning ceremony. The contract is simpler:
copy the directory in, freeze its version, update by re-copying.

## Current version

`v0.1-phase1` — the seven verified programs (UK AVEC, UK Enhanced AVEC
/ IFTC, UK AVEC VFX Additional Credit; NZ International Live Action
Production Rebate + 5% Uplift; NZ International PDV Rebate + 5%
Uplift), Hells Angels calibration at 0.0000% drift, and the four
Phase-1 additions to `estimate_rebate`: monetization discount, filing
fees, multi-currency qualifying spend, and producer view.

The version this snapshot was built from is also recorded inside the
package at `dist/VERSION`.

## How to consume

### 1. Copy the directory in

Copy `dist/` from this repo into your project and rename it to
`tax_engine`:

```bash
# from your project root
cp -r ../path/to/Tax-Incentives/dist ./tax_engine
```

You can rename `tax_engine` to anything — `incentives`, `vendor/tax`,
`thirdparty/film_tax`. The in-package imports are relative, so the
package name does not matter.

The resulting layout is:

```
your_project/
├── your_code.py
└── tax_engine/
    ├── __init__.py
    ├── VERSION
    ├── api/
    │   ├── __init__.py
    │   ├── breakdown.py        # RebateEstimate, Money, ProducerSummary, ...
    │   ├── fx.py               # FX lookup + 24h staleness flag
    │   ├── rate_rules.py       # per-program rate-rule registry
    │   └── query.py            # estimate_rebate, get_program, find_programs, ...
    ├── data/
    │   ├── incentives.db       # pre-built SQLite database
    │   └── processed/          # source JSONs that built the DB (reference only)
    ├── schema/
    │   └── schema.sql          # DB schema (reference only)
    └── scripts/
        ├── __init__.py
        └── models.py           # Pydantic models for the write path (read path
                                 # does NOT depend on this; included for round-
                                 # trip tooling)
```

### 2. Install runtime dependencies

The engine depends on:

- **Python 3.11+**
- **`pydantic` ≥ 2.6** — the breakdown contract is Pydantic-validated.

That's it. `click` is required only if you also import the CLI in
`api/query.py`; it can be omitted if you use the Python API directly.

### 3. The import path

```python
from tax_engine.api.query import estimate_rebate, get_program, find_programs
from tax_engine.api.breakdown import Money, RebateEstimate
```

If you renamed `tax_engine` to something else, substitute that name.

### 4. Minimal working example

```python
from tax_engine.api.query import estimate_rebate
est = estimate_rebate(program_id=1, qualifying_spend=10_000_000, fx_target="USD", output_view="producer")
print(est.producer_summary.top_sheet_line)
```

(`program_id=1` is the NZ Live Action Production Rebate in `v0.1-phase1`.
See "Stable program IDs" below for why you should look programs up by
natural key in production code.)

A more complete example matching the kind of call First Look will make:

```python
from tax_engine.api.query import estimate_rebate
from tax_engine.api.breakdown import Money

est = estimate_rebate(
    program_id=1,                                          # NZ Live Action Rebate
    qualifying_spend_by_currency={"USD": 39_393_672},      # multi-currency input
    monetization_discount_pct=3.0,                         # Cashet 3%
    filing_fees=Money(amount=75_000, currency="NZD"),      # combined filing + audit
    output_view="producer",
    fx_target="USD",
)
print(est.producer_summary.top_sheet_line)
# → "New Zealand 20% Incentive Rebate (less 3% monetization discount,
#    less NZD 75,000 filing fees): -USD 4,611,000.00"

print(f"gross  = USD {est.estimate_usd:,.2f}")     # the 20% × qualifying figure
print(f"today  = {est.cash_today:,.2f} {est.currency}")  # post-discount, post-fees
for caveat in est.caveats:
    print(f"  ⚠ {caveat}")
```

### Stable program IDs

The integer `program_id` is assigned by the database at load time. It is
stable across rebuilds **of the same data**, but if a future version
adds a new jurisdiction whose name sorts before existing ones, IDs may
shift. The safe pattern is to look programs up by natural key on first
use and cache the ID:

```python
import sqlite3, pathlib
db = pathlib.Path(__file__).parent / "tax_engine" / "data" / "incentives.db"
con = sqlite3.connect(db)
ids = {row[1]: row[0] for row in con.execute(
    "SELECT id, program_name FROM incentive_programs"
)}
con.close()

nz_live_action = ids["NZSPR International — Live Action Production Rebate (20%)"]
```

### Where the database lives

`api/query.py` resolves the database path relative to its own file:

```python
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "incentives.db"
```

So as long as you keep the `data/incentives.db` file in the vendored
directory (next to `api/`), every API function that takes an optional
`db_path` defaults to it correctly. You can pass `db_path=...`
explicitly if you store the DB elsewhere.

## Programs included in v0.1-phase1

| Program | Jurisdiction | Rate | Rule pattern |
|---|---|---|---|
| Audio-Visual Expenditure Credit (AVEC) — Film | United Kingdom | 34% | flat |
| Enhanced AVEC for Independent Film (IFTC) | United Kingdom | 53% | capped_base (£12M qualifying base) |
| AVEC VFX Additional Credit (Film & HETV) | United Kingdom | 39% | flat |
| NZSPR International — Live Action Production Rebate (20%) | New Zealand | 20% | flat |
| NZSPR International — Production Rebate 5% Uplift | New Zealand | 5% | stacking on the 20% base |
| NZSPR International — PDV Rebate (20%) | New Zealand | 20% | flat |
| NZSPR International — PDV Rebate 5% Uplift | New Zealand | 5% | stacking on the PDV base |

All seven entries are `verification_method = official_source_archived`
with source URLs and on-disk audit-trail HTML.

## Updating to a future version

When a future release tag is cut on the source repo (e.g., `v0.2`,
`v0.3-phase2`):

1. Pull the new tag in the source repo.
2. Confirm `dist/VERSION` matches the tag you want.
3. Replace your vendored directory wholesale:

   ```bash
   rm -rf your_project/tax_engine
   cp -r ../path/to/Tax-Incentives/dist your_project/tax_engine
   ```

4. Re-run your project's test suite — the engine's public API is
   intended to be additive across versions, but verifying calibration
   against your own scenarios is cheap insurance.

Do not edit files inside `tax_engine/` after vendoring. Local edits
will be silently overwritten on the next update. If you need behavior
the engine doesn't provide, raise it in the source repo and cut a
release.

## What is and is not in `dist/`

| Included | Excluded |
|---|---|
| `api/` — public Python API + CLI dispatch | `tests/` — pytest suite |
| `data/incentives.db` — pre-built DB | `data/raw/` — parser audit trail |
| `data/processed/*.json` — source JSONs | `scripts/scrape/`, `scripts/parse/` |
| `schema/schema.sql` — schema reference | `scripts/load.py`, `scripts/validate.py`, `scripts/export.py` |
| `scripts/models.py` — Pydantic write-path models | `scripts/build_dist.py` — the build tool itself |
| `VERSION` — release tag | Repo README, `INTEGRATION.md`, `docs/` |

The exclusions are deliberate: anything development-related (tests,
loaders, scrapers, build tools, repo docs) stays in the source repo
and never ships to consumers.

## Regenerating `dist/` from source

Maintainers of the source repo, not consumers. From the repo root:

```bash
python -m scripts.load                                  # rebuild data/incentives.db
python -m scripts.build_dist --version vX.Y-phaseZ      # write the version stamp
```

The build script rewrites in-package imports from absolute (`from
api.breakdown import ...`) to relative (`from .breakdown import ...`)
inside `dist/` so the snapshot works under any package name. The
source files are unchanged.
