"""Pydantic data models for the run ledger."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal[
    "pending",
    "fetched",
    "validated",
    "generated",
    "uploaded",
    "reconciled",
    "failed",
]

# Terminal success statuses - used for idempotency checks
TERMINAL_SUCCESS_STATUSES: frozenset[RunStatus] = frozenset(
    {"generated", "uploaded", "reconciled"}
)


class RateRecord(BaseModel):
    """A single FX rate line within a run."""

    run_id: str
    from_ccy: str
    to_ccy: str
    rate: Decimal
    source_precision: int

    model_config = {"frozen": True}


class RunRecord(BaseModel):
    """A single run entry in the ledger."""

    run_id: str
    run_date: date
    source_date: date
    applied_from: date
    applied_to: date
    status: RunStatus
    evidence_path: str | None = None
    manifest_sha256: str | None = None
    source_date_exception: bool = False
    created_at: datetime
    updated_at: datetime
    rates: list[RateRecord] = Field(default_factory=list)

    model_config = {"frozen": False}
