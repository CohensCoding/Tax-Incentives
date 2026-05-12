"""Export the incentives database to consumer-friendly formats.

Formats:

* `llm-context` (markdown) — one summary per program, designed to be stuffed
  into an LLM context window so a downstream budgeting tool can answer
  questions like "where can I shoot a $40M movie with $15M in star
  salaries and get the best rebate?" The markdown is structured so each
  program section is independently parseable.

* `csv` — flat tabular view of programs for spreadsheet inspection.

* `json` — same shape as the loaded DB rows; useful for diffing across
  database versions.

Every export passes the strict export gate first
(`scripts.validate.validate_all_files(export_mode=True)` plus a DB check).
A program with `verification_method='model_knowledge_unverified'` or with
a verified-method claim that has no on-disk artifact under `data/raw/` is
refused — the export gate is the load-bearing safety boundary between
in-development data and consumer output.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click

from api.query import (
    DEFAULT_DB,
    IncentiveProgram,
    get_program,
    list_jurisdictions,
)
from scripts.validate import validate_all_files, validate_db, _print_report


def _gate(db_path: Path) -> bool:
    """Run the strict export gate. Returns True only if clean."""
    r = validate_all_files(export_mode=True)
    r.extend(validate_db(db_path, export_mode=True))
    if not r.ok:
        click.echo(click.style(
            "✗ Export gate refused — fix errors before exporting.", fg="red"
        ), err=True)
        _print_report(r)
        return False
    return True


def _load_all_programs(db_path: Path) -> list[IncentiveProgram]:
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    ids = [r["id"] for r in con.execute("SELECT id FROM incentive_programs ORDER BY id")]
    con.close()
    return [get_program(pid, db_path=db_path) for pid in ids]


# ─── llm-context (markdown) ─────────────────────────────────────────────

def _md_program(p: IncentiveProgram) -> str:
    """Render one program as a self-contained markdown section."""
    lines: list[str] = []
    lines.append(f"### {p.program_name}")
    lines.append("")
    lines.append(f"- **Jurisdiction:** {p.jurisdiction_display}")
    lines.append(f"- **Program ID:** {p.id}")
    lines.append(f"- **Type:** {p.incentive_type}")
    lines.append(f"- **Headline rate:** {p.headline_rate_pct}%")
    lines.append(f"- **Currency:** {p.currency}")
    lines.append(f"- **ATL eligible:** {p.atl_eligible}")
    if p.minimum_spend is not None:
        lines.append(f"- **Minimum spend:** {p.currency} {p.minimum_spend:,.0f}")
    if p.qualifying_budget_ceiling is not None:
        lines.append(f"- **Budget ceiling:** {p.currency} {p.qualifying_budget_ceiling:,.0f}")
    if p.cap_per_project is not None:
        lines.append(f"- **Cap per project:** {p.currency} {p.cap_per_project:,.0f}")
    if p.annual_program_cap is not None:
        lines.append(f"- **Annual program cap:** {p.currency} {p.annual_program_cap:,.0f}")
    lines.append(f"- **Verification:** {p.verification_method} (last verified {p.last_verified_date})")
    if p.sunset_date:
        lines.append(f"- **Sunset date:** {p.sunset_date}")
    lines.append("")

    def _para(label: str, text: str | None) -> None:
        if text:
            lines.append(f"**{label}.** {text}")
            lines.append("")

    _para("Rate details", p.rate_details)
    _para("Minimum spend notes", p.minimum_spend_notes)
    _para("Budget ceiling notes", p.qualifying_budget_ceiling_notes)
    _para("ATL cap notes", p.atl_cap_notes)
    _para("Qualifying spend", p.qualifying_spend_summary)
    _para("Non-qualifying spend", p.non_qualifying_spend)
    _para("Application process", p.application_process)
    _para("Payment timing", p.payment_timing)
    _para("Notes", p.notes)

    if p.source_urls:
        lines.append("**Sources:**")
        for u in p.source_urls:
            lines.append(f"- {u}")
        lines.append("")
    return "\n".join(lines)


def export_llm_context(db_path: Path = DEFAULT_DB) -> str:
    if not _gate(db_path):
        raise click.ClickException("Export gate refused.")
    programs = _load_all_programs(db_path)
    juris = {j.id: j for j in list_jurisdictions(db_path=db_path)}

    out: list[str] = []
    out.append("# Film & TV Production Tax Incentive Database — LLM Context Pack")
    out.append("")
    out.append("This document is a structured summary of every program that has "
               "passed the export gate (i.e., has been verified against a live or "
               "archived official source, with the source file present in "
               "`data/raw/`). It is suitable for inclusion in an LLM context "
               "window. Numeric fields are in each program's local currency; "
               "USD comparisons require an FX conversion at query time.")
    out.append("")
    out.append("**Important:** This data is a planning aid only. Tax incentive "
               "programs change. Producers must verify against the live official "
               "source before any binding decision. Sources are listed under each "
               "program.")
    out.append("")

    # Group by jurisdiction for readability
    by_juris: dict[int, list[IncentiveProgram]] = {}
    for p in programs:
        by_juris.setdefault(p.jurisdiction_id, []).append(p)
    for jid in sorted(by_juris):
        j = juris[jid]
        out.append(f"## {j.display_name}")
        out.append("")
        for p in by_juris[jid]:
            out.append(_md_program(p))
    return "\n".join(out)


# ─── CSV ────────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "id", "jurisdiction_display", "currency", "program_name", "incentive_type",
    "headline_rate_pct", "atl_eligible", "minimum_spend", "qualifying_budget_ceiling",
    "cap_per_project", "annual_program_cap", "verification_method",
    "last_verified_date", "source_urls",
]


def export_csv(db_path: Path = DEFAULT_DB) -> str:
    if not _gate(db_path):
        raise click.ClickException("Export gate refused.")
    programs = _load_all_programs(db_path)
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLUMNS)
    for p in programs:
        w.writerow([
            p.id, p.jurisdiction_display, p.currency, p.program_name, p.incentive_type,
            p.headline_rate_pct, p.atl_eligible, p.minimum_spend, p.qualifying_budget_ceiling,
            p.cap_per_project, p.annual_program_cap, p.verification_method,
            p.last_verified_date, "; ".join(p.source_urls),
        ])
    return buf.getvalue()


# ─── JSON ───────────────────────────────────────────────────────────────

def export_json(db_path: Path = DEFAULT_DB) -> str:
    if not _gate(db_path):
        raise click.ClickException("Export gate refused.")
    programs = _load_all_programs(db_path)
    return json.dumps(
        [
            {
                **{k: getattr(p, k) for k in (
                    "id", "jurisdiction_id", "jurisdiction_display", "currency",
                    "program_name", "incentive_type", "headline_rate_pct",
                    "rate_details", "minimum_spend", "minimum_spend_notes",
                    "cap_per_project", "annual_program_cap", "atl_eligible",
                    "atl_cap_notes", "qualifying_spend_summary",
                    "non_qualifying_spend", "application_process", "payment_timing",
                    "sunset_date", "last_verified_date", "verification_method",
                    "qualifying_budget_ceiling", "qualifying_budget_ceiling_notes",
                    "notes",
                )},
                "source_urls": p.source_urls,
            }
            for p in programs
        ],
        indent=2,
        ensure_ascii=False,
    )


# ─── CLI ────────────────────────────────────────────────────────────────

@click.command()
@click.option("--format", "fmt", type=click.Choice(["llm-context", "csv", "json"]),
              default="llm-context")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Write to file instead of stdout.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), default=DEFAULT_DB)
def cli(fmt: str, out_path: Path | None, db_path: Path):
    """Export the database (must pass the strict export gate)."""
    if fmt == "llm-context":
        content = export_llm_context(db_path)
    elif fmt == "csv":
        content = export_csv(db_path)
    else:
        content = export_json(db_path)
    if out_path:
        out_path.write_text(content, encoding="utf-8")
        click.echo(f"✓ Wrote {len(content):,} chars to {out_path}", err=True)
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    cli()
