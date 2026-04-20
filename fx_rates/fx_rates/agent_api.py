"""Typed functions for agent invocation of the fx_rates pipeline.

All functions catch internal exceptions and surface them through typed return
values – they never raise directly to the caller.

Exit code semantics
-------------------
 0  success (Phase 1 terminal: generated)
10  already-run  (idempotency guard)
20  variance-hold  (>5%, overridable with force=True)
21  variance-block (>10%, hard block)
30  source-unavailable (BoE fetch failed)
40  oracle-failure (Phase 2 – reserved)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ulid import ULID

from .evidence.writer import EvidenceWriter
from .oracle.csv_builder import (
    build_csv_bytes,
    build_fbdi_rows,
    build_zip_bytes,
)
from .policy.dates import resolve_applied_window, resolve_source_date
from .policy.holidays import get_uk_holidays_for_range
from .policy.validation import (
    VarianceBlockError,
    VarianceHoldError,
    check_completeness,
    check_variance,
)
from .providers.base import SourceUnavailableError
from .providers.boe import SERIES_TO_PAIR, BoEProvider
from .state.ledger import AlreadyRunError, LedgerError, RunLedger
from .state.models import RateRecord, RunRecord

logger = logging.getLogger(__name__)

_REQUIRED_SERIES = frozenset(SERIES_TO_PAIR.keys())


# ---------------------------------------------------------------------------
# Return-type dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Result returned by :func:`run_weekly`."""

    exit_code: int
    run_id: str | None = None
    run_date: date | None = None
    source_date: date | None = None
    applied_from: date | None = None
    applied_to: date | None = None
    rates: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    evidence_path: str | None = None
    manifest_sha256: str | None = None
    source_date_exception: bool = False
    error: str | None = None
    variance_breaches: list[Any] = field(default_factory=list)


@dataclass
class DryRunResult:
    """Result returned by :func:`dry_run`."""

    exit_code: int
    run_date: date | None = None
    source_date: date | None = None
    applied_from: date | None = None
    applied_to: date | None = None
    rates: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    source_date_exception: bool = False
    error: str | None = None
    csv_preview: str | None = None  # first few lines of the FBDI CSV


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_weekly(
    *,
    run_date: date | None = None,
    force: bool = False,
    evidence_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> RunResult:
    """Execute the full weekly FX rate pipeline.

    Steps:
      1. Resolve dates (applied window, BoE source date).
      2. Idempotency check via ledger.
      3. Fetch rates from BoE IADB.
      4. Validate completeness and variance against prior run.
      5. Generate FBDI CSV + zip.
      6. Write evidence pack.
      7. Persist run record to ledger.

    Args:
        run_date:     Date to treat as "today" (default: actual today).
        force:        Override variance HOLD errors (not BLOCK).
        evidence_dir: Directory for evidence packs (default: ``./evidence``).
        ledger_path:  Path to SQLite ledger (default: ``./ledger.db``).

    Returns:
        :class:`RunResult` with exit_code and run details.
    """
    run_date = run_date or date.today()
    evidence_base = Path(evidence_dir) if evidence_dir else Path("evidence")
    ledger_p = Path(ledger_path) if ledger_path else Path("ledger.db")

    # --- 1. Resolve dates ---
    applied_from, applied_to = resolve_applied_window(run_date)
    holidays = get_uk_holidays_for_range(
        applied_from - __import__("datetime").timedelta(days=14),
        applied_from,
    )
    source_date, source_date_exception = resolve_source_date(applied_from, holidays)

    logger.info(
        "run_weekly: run_date=%s applied=%s–%s source=%s exception=%s",
        run_date,
        applied_from,
        applied_to,
        source_date,
        source_date_exception,
    )

    # --- 2. Open ledger + idempotency check ---
    try:
        ledger = RunLedger(ledger_p)
    except LedgerError as exc:
        return RunResult(exit_code=30, error=str(exc))

    run_id = str(ULID())

    try:
        ledger.create_run(
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            source_date_exception=source_date_exception,
        )
    except AlreadyRunError as exc:
        logger.info("Already-run guard triggered: %s", exc)
        return RunResult(
            exit_code=exc.exit_code,
            run_date=run_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
        )
    except LedgerError as exc:
        return RunResult(exit_code=30, error=str(exc))

    # --- 3. Fetch from BoE ---
    provider = BoEProvider()
    try:
        raw_rates = provider.fetch(source_date)
        ledger.update_status(run_id, "fetched")
    except SourceUnavailableError as exc:
        ledger.update_status(run_id, "failed")
        return RunResult(
            exit_code=30,
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
        )

    # --- 4. Validate ---
    try:
        check_completeness(raw_rates, _REQUIRED_SERIES)
    except ValueError as exc:
        ledger.update_status(run_id, "failed")
        return RunResult(
            exit_code=30,
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
        )

    # Build pair-keyed current rates for variance
    pair_rates: dict[tuple[str, str], Decimal] = {
        SERIES_TO_PAIR[s]: r for s, r in raw_rates.items()
    }

    # Load prior rates for each pair
    prior_pair_rates: dict[tuple[str, str], Decimal] = {}
    for pair in pair_rates:
        rec = ledger.get_latest_successful_run_for_pair(*pair, before_run_date=run_date)
        if rec is not None:
            prior_pair_rates[pair] = rec.rate

    variance_info: dict[str, Any] = {}
    try:
        breaches = check_variance(pair_rates, prior_pair_rates, force=force)
        variance_info["breaches"] = [
            {
                "pair": f"{b.from_ccy}/{b.to_ccy}",
                "pct_change": str(b.pct_change),
                "prior": str(b.prior_rate),
                "current": str(b.current_rate),
            }
            for b in breaches
        ]
        ledger.update_status(run_id, "validated")
    except VarianceBlockError as exc:
        ledger.update_status(run_id, "failed")
        return RunResult(
            exit_code=21,
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
            variance_breaches=exc.breaches,
        )
    except VarianceHoldError as exc:
        ledger.update_status(run_id, "failed")
        return RunResult(
            exit_code=20,
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
            variance_breaches=exc.breaches,
        )

    # --- 5. Generate FBDI artefacts ---
    rows = build_fbdi_rows(raw_rates, applied_from, applied_to)
    fbdi_csv_bytes = build_csv_bytes(rows)
    fbdi_zip_bytes = build_zip_bytes(fbdi_csv_bytes)

    # --- 6. Write evidence pack ---
    evidence_base.mkdir(parents=True, exist_ok=True)
    writer = EvidenceWriter(evidence_base)

    rates_derived_payload = {
        series: {
            "pair": f"{SERIES_TO_PAIR[series][0]}/{SERIES_TO_PAIR[series][1]}",
            "rate": str(rate),
            "source_precision": len(str(rate).rstrip("0").rstrip(".").split(".")[-1])
            if "." in str(rate)
            else 0,
        }
        for series, rate in raw_rates.items()
    }

    boe_request_meta = {
        "url": provider.last_url,
        "timestamp": provider.last_timestamp,
        "response_headers": provider.last_response_headers,
        "http_status": provider.last_http_status,
    }

    try:
        run_dir, manifest_sha256 = writer.write(
            run_id=run_id,
            boe_raw_csv=provider.last_raw_csv,
            boe_request_meta=boe_request_meta,
            rates_derived=rates_derived_payload,
            fbdi_csv_bytes=fbdi_csv_bytes,
            fbdi_zip_bytes=fbdi_zip_bytes,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            variance_info=variance_info,
        )
    except Exception as exc:
        ledger.update_status(run_id, "failed")
        return RunResult(
            exit_code=30,
            run_id=run_id,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=f"Evidence write failed: {exc}",
        )

    # --- 7. Persist rates + finalise ledger ---
    rate_records = [
        RateRecord(
            run_id=run_id,
            from_ccy=SERIES_TO_PAIR[series][0],
            to_ccy=SERIES_TO_PAIR[series][1],
            rate=rate,
            source_precision=len(str(rate).rstrip("0").rstrip(".").split(".")[-1])
            if "." in str(rate)
            else 0,
        )
        for series, rate in raw_rates.items()
    ]
    ledger.save_rates(run_id, rate_records)
    ledger.update_status(
        run_id,
        "generated",
        evidence_path=str(run_dir),
        manifest_sha256=manifest_sha256,
    )

    logger.info("run_weekly complete: run_id=%s exit_code=0", run_id)
    return RunResult(
        exit_code=0,
        run_id=run_id,
        run_date=run_date,
        source_date=source_date,
        applied_from=applied_from,
        applied_to=applied_to,
        rates=pair_rates,
        evidence_path=str(run_dir),
        manifest_sha256=manifest_sha256,
        source_date_exception=source_date_exception,
        variance_breaches=variance_info.get("breaches", []),
    )


def dry_run(
    *,
    run_date: date | None = None,
) -> DryRunResult:
    """Fetch and generate FBDI files without writing to ledger or evidence.

    Useful for verifying rates before committing a run.

    Args:
        run_date: Date to treat as "today" (default: actual today).

    Returns:
        :class:`DryRunResult` with rates and CSV preview.
    """
    import datetime as dt

    run_date = run_date or date.today()

    applied_from, applied_to = resolve_applied_window(run_date)
    holidays = get_uk_holidays_for_range(
        applied_from - dt.timedelta(days=14),
        applied_from,
    )
    source_date, source_date_exception = resolve_source_date(applied_from, holidays)

    provider = BoEProvider()
    try:
        raw_rates = provider.fetch(source_date)
    except SourceUnavailableError as exc:
        return DryRunResult(
            exit_code=30,
            run_date=run_date,
            source_date=source_date,
            applied_from=applied_from,
            applied_to=applied_to,
            error=str(exc),
        )

    pair_rates: dict[tuple[str, str], Decimal] = {
        SERIES_TO_PAIR[s]: r for s, r in raw_rates.items()
    }

    rows = build_fbdi_rows(raw_rates, applied_from, applied_to)
    csv_bytes = build_csv_bytes(rows)
    csv_preview = csv_bytes.decode("utf-8")

    return DryRunResult(
        exit_code=0,
        run_date=run_date,
        source_date=source_date,
        applied_from=applied_from,
        applied_to=applied_to,
        rates=pair_rates,
        source_date_exception=source_date_exception,
        csv_preview=csv_preview,
    )


def get_status(
    *,
    ledger_path: Path | str | None = None,
    limit: int = 20,
) -> list[RunRecord]:
    """Return recent run records from the ledger.

    Args:
        ledger_path: Path to SQLite ledger (default: ``./ledger.db``).
        limit:       Maximum number of records to return.

    Returns:
        List of :class:`~fx_rates.state.models.RunRecord` ordered newest-first.
    """
    ledger_p = Path(ledger_path) if ledger_path else Path("ledger.db")
    try:
        ledger = RunLedger(ledger_p)
        return ledger.list_runs(limit=limit)
    except LedgerError as exc:
        logger.error("Cannot read ledger: %s", exc)
        return []
