"""Evidence artefact writer – produces a tamper-evident audit pack per run."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EvidenceWriteError(Exception):
    """Raised when evidence artefacts cannot be written."""


class EvidenceWriter:
    """Writes an evidence pack to ``{evidence_dir}/{run_id}/``.

    Artefacts are written in a defined order; the manifest is always last.
    The SHA-256 of the manifest itself is returned by :meth:`write`.

    Args:
        evidence_dir: Parent directory; the run sub-directory is created here.
    """

    def __init__(self, evidence_dir: Path | str) -> None:
        self._base = Path(evidence_dir)

    def write(
        self,
        *,
        run_id: str,
        boe_raw_csv: bytes,
        boe_request_meta: dict[str, Any],
        rates_derived: dict[str, Any],
        fbdi_csv_bytes: bytes,
        fbdi_zip_bytes: bytes,
        source_date: date,
        applied_from: date,
        applied_to: date,
        variance_info: dict[str, Any] | None = None,
    ) -> tuple[Path, str]:
        """Write all evidence artefacts and return (run_dir, manifest_sha256).

        Files written in order:
          1. boe_raw.csv
          2. boe_request.json
          3. rates_derived.json
          4. fbdi_upload.csv
          5. GlDailyRatesInterface.zip
          6. manifest.json

        Args:
            run_id:           ULID run identifier (used as sub-directory name).
            boe_raw_csv:      Exact bytes received from BoE IADB.
            boe_request_meta: Dict with url, timestamp, response_headers, http_status.
            rates_derived:    Parsed rate dict with precision info.
            fbdi_csv_bytes:   The generated FBDI CSV bytes.
            fbdi_zip_bytes:   The generated FBDI zip bytes.
            source_date:      BoE source date for this run.
            applied_from:     Monday of the Oracle applied window.
            applied_to:       Sunday of the Oracle applied window.
            variance_info:    Optional dict of variance check results.

        Returns:
            ``(run_dir_path, manifest_sha256_hex)``

        Raises:
            EvidenceWriteError: If any file write fails.
        """
        run_dir = self._base / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EvidenceWriteError(
                f"Cannot create evidence directory {run_dir}: {exc}"
            ) from exc

        file_hashes: dict[str, str] = {}

        # 1. boe_raw.csv
        boe_raw_path = run_dir / "boe_raw.csv"
        file_hashes["boe_raw.csv"] = self._write_bytes(boe_raw_path, boe_raw_csv)

        # 2. boe_request.json
        boe_req_path = run_dir / "boe_request.json"
        file_hashes["boe_request.json"] = self._write_json(
            boe_req_path, boe_request_meta
        )

        # 3. rates_derived.json
        rates_path = run_dir / "rates_derived.json"
        file_hashes["rates_derived.json"] = self._write_json(
            rates_path, rates_derived
        )

        # 4. fbdi_upload.csv
        fbdi_csv_path = run_dir / "fbdi_upload.csv"
        file_hashes["fbdi_upload.csv"] = self._write_bytes(
            fbdi_csv_path, fbdi_csv_bytes
        )

        # 5. GlDailyRatesInterface.zip
        zip_path = run_dir / "GlDailyRatesInterface.zip"
        file_hashes["GlDailyRatesInterface.zip"] = self._write_bytes(
            zip_path, fbdi_zip_bytes
        )

        # 6. manifest.json  (always last)
        manifest = {
            "run_id": run_id,
            "source_date": source_date.isoformat(),
            "applied_from": applied_from.isoformat(),
            "applied_to": applied_to.isoformat(),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "variance_info": variance_info or {},
            "files": file_hashes,
        }
        manifest_path = run_dir / "manifest.json"
        manifest_sha256 = self._write_json(manifest_path, manifest)

        logger.info(
            "Evidence pack written to %s  manifest_sha256=%s",
            run_dir,
            manifest_sha256,
        )
        return run_dir, manifest_sha256

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sha256_of_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _write_bytes(self, path: Path, data: bytes) -> str:
        """Write *data* to *path* and return its SHA-256 hex digest."""
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise EvidenceWriteError(
                f"Failed to write evidence file {path}: {exc}"
            ) from exc
        return self._sha256_of_bytes(data)

    def _write_json(self, path: Path, obj: Any) -> str:
        """Serialise *obj* as pretty-printed JSON, write to *path*, return SHA-256."""
        try:
            encoded = json.dumps(obj, indent=2, default=_json_default).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise EvidenceWriteError(
                f"Cannot serialise evidence object for {path}: {exc}"
            ) from exc
        return self._write_bytes(path, encoded)


def _json_default(obj: Any) -> Any:
    """Custom JSON serialiser for types not handled by the stdlib encoder."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")
