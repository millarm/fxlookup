"""Typer CLI for the fx_rates pipeline."""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="fx-rates",
    help="Fetch BoE FX rates and generate Oracle FBDI CSV/zip artefacts.",
    add_completion=False,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@app.command("run")
def cmd_run(
    week: Optional[str] = typer.Option(
        None,
        "--week",
        metavar="YYYY-MM-DD",
        help="Run date (defaults to today). Must be a date within the target week.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Override variance HOLD errors (>5%). Does not override BLOCK (>10%).",
    ),
    dry_run_flag: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and preview rates without writing ledger or evidence.",
    ),
    evidence_dir: Optional[Path] = typer.Option(
        None,
        "--evidence-dir",
        help="Directory for evidence packs (default: ./evidence).",
    ),
    ledger: Optional[Path] = typer.Option(
        None,
        "--ledger",
        help="Path to SQLite ledger file (default: ./ledger.db).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch rates and generate FBDI artefacts for the week containing WEEK."""
    _setup_logging(verbose)

    run_date: date | None = None
    if week:
        try:
            run_date = date.fromisoformat(week)
        except ValueError:
            typer.echo(f"[error] Invalid date format: {week!r} – use YYYY-MM-DD", err=True)
            raise typer.Exit(1)

    if dry_run_flag:
        _do_dry_run(run_date)
    else:
        _do_run(run_date, force=force, evidence_dir=evidence_dir, ledger_path=ledger)


# ---------------------------------------------------------------------------
# Internal command handlers
# ---------------------------------------------------------------------------


def _do_run(
    run_date: date | None,
    *,
    force: bool,
    evidence_dir: Path | None,
    ledger_path: Path | None,
) -> None:
    from .agent_api import run_weekly

    result = run_weekly(
        run_date=run_date,
        force=force,
        evidence_dir=evidence_dir,
        ledger_path=ledger_path,
    )

    if result.exit_code == 0:
        typer.echo("=" * 60)
        typer.echo("FX RATES RUN COMPLETE")
        typer.echo("=" * 60)
        typer.echo(f"  Run ID         : {result.run_id}")
        typer.echo(f"  Run date       : {result.run_date}")
        typer.echo(f"  Source date    : {result.source_date}"
                   + (" [EXCEPTION – bank holiday fallback]" if result.source_date_exception else ""))
        typer.echo(f"  Applied window : {result.applied_from} – {result.applied_to}")
        typer.echo(f"  Evidence path  : {result.evidence_path}")
        typer.echo(f"  Manifest SHA256: {result.manifest_sha256}")
        typer.echo("")
        typer.echo("  Rates:")
        for (from_ccy, to_ccy), rate in sorted(result.rates.items()):
            typer.echo(f"    {from_ccy}/{to_ccy}  {rate}")
        raise typer.Exit(0)

    elif result.exit_code == 10:
        typer.echo(f"[already-run] {result.error}", err=True)
        raise typer.Exit(10)

    elif result.exit_code == 20:
        typer.echo("[variance-hold] The following pairs moved >5%:", err=True)
        for b in result.variance_breaches:
            if isinstance(b, dict):
                typer.echo(
                    f"  {b['pair']}: {b['pct_change']}%  (prior={b['prior']} current={b['current']})",
                    err=True,
                )
            else:
                typer.echo(
                    f"  {b.from_ccy}/{b.to_ccy}: {b.pct_change:.2f}%  "
                    f"(prior={b.prior_rate} current={b.current_rate})",
                    err=True,
                )
        typer.echo("Hint: use --force to override.", err=True)
        raise typer.Exit(20)

    elif result.exit_code == 21:
        typer.echo("[variance-block] Hard block – the following pairs moved >10%:", err=True)
        for b in result.variance_breaches:
            if isinstance(b, dict):
                typer.echo(
                    f"  {b['pair']}: {b['pct_change']}%  (prior={b['prior']} current={b['current']})",
                    err=True,
                )
            else:
                typer.echo(
                    f"  {b.from_ccy}/{b.to_ccy}: {b.pct_change:.2f}%  "
                    f"(prior={b.prior_rate} current={b.current_rate})",
                    err=True,
                )
        typer.echo("No override available – investigate the source data.", err=True)
        raise typer.Exit(21)

    elif result.exit_code == 30:
        typer.echo(f"[source-unavailable] {result.error}", err=True)
        raise typer.Exit(30)

    else:
        typer.echo(f"[error] exit_code={result.exit_code}: {result.error}", err=True)
        raise typer.Exit(result.exit_code)


def _do_dry_run(run_date: date | None) -> None:
    from .agent_api import dry_run

    result = dry_run(run_date=run_date)

    if result.exit_code == 0:
        typer.echo("=" * 60)
        typer.echo("DRY RUN (no ledger/evidence written)")
        typer.echo("=" * 60)
        typer.echo(f"  Run date       : {result.run_date}")
        typer.echo(f"  Source date    : {result.source_date}"
                   + (" [EXCEPTION – bank holiday fallback]" if result.source_date_exception else ""))
        typer.echo(f"  Applied window : {result.applied_from} – {result.applied_to}")
        typer.echo("")
        typer.echo("  Rates:")
        for (from_ccy, to_ccy), rate in sorted(result.rates.items()):
            typer.echo(f"    {from_ccy}/{to_ccy}  {rate}")
        typer.echo("")
        typer.echo("  FBDI CSV preview:")
        if result.csv_preview:
            for line in result.csv_preview.splitlines()[:10]:
                typer.echo(f"    {line}")
        raise typer.Exit(0)
    else:
        typer.echo(f"[error] {result.error}", err=True)
        raise typer.Exit(result.exit_code)


@app.command("status")
def cmd_status(
    ledger: Optional[Path] = typer.Option(
        None,
        "--ledger",
        help="Path to SQLite ledger file (default: ./ledger.db).",
    ),
    limit: int = typer.Option(20, "--limit", help="Maximum rows to show."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show recent runs from the ledger."""
    _setup_logging(verbose)

    from .agent_api import get_status

    records = get_status(ledger_path=ledger, limit=limit)

    if not records:
        typer.echo("No runs found in ledger.")
        return

    typer.echo(
        f"{'RUN ID':<28} {'DATE':<12} {'APPLIED':<23} {'SOURCE':<12} {'STATUS':<12} {'EXCEPTION'}"
    )
    typer.echo("-" * 100)
    for r in records:
        typer.echo(
            f"{r.run_id:<28} {str(r.run_date):<12} "
            f"{str(r.applied_from)}–{str(r.applied_to):<12} "
            f"{str(r.source_date):<12} {r.status:<12} "
            f"{'YES' if r.source_date_exception else ''}"
        )


@app.command("replay")
def cmd_replay(
    week: str = typer.Argument(..., help="Week date YYYY-MM-DD to replay."),
    force: bool = typer.Option(False, "--force"),
    evidence_dir: Optional[Path] = typer.Option(None, "--evidence-dir"),
    ledger: Optional[Path] = typer.Option(None, "--ledger"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Replay a run for a specific historical week date."""
    _setup_logging(verbose)
    try:
        run_date = date.fromisoformat(week)
    except ValueError:
        typer.echo(f"[error] Invalid date: {week!r} – use YYYY-MM-DD", err=True)
        raise typer.Exit(1)

    _do_run(run_date, force=force, evidence_dir=evidence_dir, ledger_path=ledger)


if __name__ == "__main__":
    app()
