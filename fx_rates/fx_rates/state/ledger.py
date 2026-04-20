"""SQLite run ledger for idempotency tracking and audit trail."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    TERMINAL_SUCCESS_STATUSES,
    RateRecord,
    RunRecord,
    RunStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path("ledger.db")

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    run_date            TEXT NOT NULL,
    source_date         TEXT NOT NULL,
    applied_from        TEXT NOT NULL,
    applied_to          TEXT NOT NULL,
    status              TEXT NOT NULL,
    evidence_path       TEXT,
    manifest_sha256     TEXT,
    source_date_exception INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rates (
    run_id          TEXT NOT NULL,
    from_ccy        TEXT NOT NULL,
    to_ccy          TEXT NOT NULL,
    rate            TEXT NOT NULL,
    source_precision INTEGER NOT NULL,
    PRIMARY KEY (run_id, from_ccy, to_ccy),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


class AlreadyRunError(Exception):
    """Raised when a successful run already exists for the given applied window.

    Exit code: 10.
    """

    exit_code: int = 10

    def __init__(self, applied_from: date, applied_to: date, existing_run_id: str) -> None:
        super().__init__(
            f"Run already completed for window {applied_from} – {applied_to} "
            f"(run_id={existing_run_id}). Use --force to override."
        )
        self.applied_from = applied_from
        self.applied_to = applied_to
        self.existing_run_id = existing_run_id


class LedgerError(Exception):
    """Raised on unexpected ledger I/O failures."""


class RunLedger:
    """SQLite-backed run ledger.

    Args:
        path: Path to the SQLite database file.  Created if absent.
    """

    def __init__(self, path: Path | str = DEFAULT_LEDGER_PATH) -> None:
        self._path = Path(path)
        self._connect()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        try:
            self._conn = sqlite3.connect(
                str(self._path),
                isolation_level=None,  # autocommit
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_DDL)
        except sqlite3.Error as exc:
            raise LedgerError(
                f"Cannot open ledger at {self._path}: {exc}"
            ) from exc

    def close(self) -> None:
        """Close the underlying database connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        run_date: date,
        source_date: date,
        applied_from: date,
        applied_to: date,
        source_date_exception: bool = False,
    ) -> RunRecord:
        """Insert a new run row with status=pending.

        Raises:
            AlreadyRunError: If a terminal-success run exists for the same
                applied window.
            LedgerError: On database failure.
        """
        self._check_idempotency(applied_from, applied_to)

        now = _now_iso()
        try:
            self._conn.execute(
                """
                INSERT INTO runs
                  (run_id, run_date, source_date, applied_from, applied_to,
                   status, source_date_exception, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    run_id,
                    run_date.isoformat(),
                    source_date.isoformat(),
                    applied_from.isoformat(),
                    applied_to.isoformat(),
                    int(source_date_exception),
                    now,
                    now,
                ),
            )
        except sqlite3.Error as exc:
            raise LedgerError(f"Failed to create run {run_id}: {exc}") from exc

        return self.get_run(run_id)  # type: ignore[return-value]

    def update_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        evidence_path: str | None = None,
        manifest_sha256: str | None = None,
    ) -> None:
        """Update the status (and optionally evidence_path / manifest_sha256)."""
        now = _now_iso()
        try:
            self._conn.execute(
                """
                UPDATE runs
                   SET status = ?,
                       evidence_path = COALESCE(?, evidence_path),
                       manifest_sha256 = COALESCE(?, manifest_sha256),
                       updated_at = ?
                 WHERE run_id = ?
                """,
                (status, evidence_path, manifest_sha256, now, run_id),
            )
        except sqlite3.Error as exc:
            raise LedgerError(
                f"Failed to update status for run {run_id}: {exc}"
            ) from exc

    def save_rates(self, run_id: str, rates: list[RateRecord]) -> None:
        """Persist rate records for a run (replace on conflict)."""
        try:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO rates
                  (run_id, from_ccy, to_ccy, rate, source_precision)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (r.run_id, r.from_ccy, r.to_ccy, str(r.rate), r.source_precision)
                    for r in rates
                ],
            )
        except sqlite3.Error as exc:
            raise LedgerError(
                f"Failed to save rates for run {run_id}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return a RunRecord by run_id, or None if not found."""
        try:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(f"Failed to fetch run {run_id}: {exc}") from exc

        if row is None:
            return None
        return self._row_to_run(dict(row))

    def list_runs(self, limit: int = 100) -> list[RunRecord]:
        """Return most-recent runs ordered by created_at desc."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(f"Failed to list runs: {exc}") from exc

        return [self._row_to_run(dict(r)) for r in rows]

    def get_latest_successful_run_for_pair(
        self, from_ccy: str, to_ccy: str, before_run_date: date | None = None
    ) -> RateRecord | None:
        """Return the most recent rate for a currency pair from a successful run."""
        try:
            if before_run_date is not None:
                row = self._conn.execute(
                    """
                    SELECT r.*
                      FROM rates r
                      JOIN runs rn ON rn.run_id = r.run_id
                     WHERE r.from_ccy = ? AND r.to_ccy = ?
                       AND rn.status IN ('generated','uploaded','reconciled')
                       AND rn.run_date < ?
                     ORDER BY rn.run_date DESC
                     LIMIT 1
                    """,
                    (from_ccy, to_ccy, before_run_date.isoformat()),
                ).fetchone()
            else:
                row = self._conn.execute(
                """
                SELECT r.*
                  FROM rates r
                  JOIN runs rn ON rn.run_id = r.run_id
                 WHERE r.from_ccy = ? AND r.to_ccy = ?
                   AND rn.status IN ('generated','uploaded','reconciled')
                 ORDER BY rn.run_date DESC
                 LIMIT 1
                """,
                (from_ccy, to_ccy),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(
                f"Failed to fetch prior rate for {from_ccy}/{to_ccy}: {exc}"
            ) from exc

        if row is None:
            return None
        d = dict(row)
        return RateRecord(
            run_id=d["run_id"],
            from_ccy=d["from_ccy"],
            to_ccy=d["to_ccy"],
            rate=Decimal(d["rate"]),
            source_precision=d["source_precision"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_idempotency(self, applied_from: date, applied_to: date) -> None:
        """Raise AlreadyRunError if a terminal-success run exists for this window."""
        placeholders = ",".join("?" * len(TERMINAL_SUCCESS_STATUSES))
        try:
            row = self._conn.execute(
                f"""
                SELECT run_id FROM runs
                 WHERE applied_from = ?
                   AND applied_to   = ?
                   AND status IN ({placeholders})
                 LIMIT 1
                """,
                (
                    applied_from.isoformat(),
                    applied_to.isoformat(),
                    *sorted(TERMINAL_SUCCESS_STATUSES),
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(
                f"Idempotency check failed: {exc}"
            ) from exc

        if row is not None:
            raise AlreadyRunError(applied_from, applied_to, row["run_id"])

    def _row_to_run(self, d: dict[str, Any]) -> RunRecord:
        rates = self._load_rates(d["run_id"])
        return RunRecord(
            run_id=d["run_id"],
            run_date=date.fromisoformat(d["run_date"]),
            source_date=date.fromisoformat(d["source_date"]),
            applied_from=date.fromisoformat(d["applied_from"]),
            applied_to=date.fromisoformat(d["applied_to"]),
            status=d["status"],
            evidence_path=d.get("evidence_path"),
            manifest_sha256=d.get("manifest_sha256"),
            source_date_exception=bool(d.get("source_date_exception", 0)),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            rates=rates,
        )

    def _load_rates(self, run_id: str) -> list[RateRecord]:
        rows = self._conn.execute(
            "SELECT * FROM rates WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [
            RateRecord(
                run_id=r["run_id"],
                from_ccy=r["from_ccy"],
                to_ccy=r["to_ccy"],
                rate=Decimal(r["rate"]),
                source_precision=r["source_precision"],
            )
            for r in rows
        ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
