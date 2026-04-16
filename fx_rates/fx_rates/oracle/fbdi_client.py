"""Oracle Fusion FBDI upload client – Phase 2 stub.

This module is intentionally unimplemented in Phase 1.  The interface is
defined here so that type-checkers and callers can import it without error,
but every method raises :exc:`NotImplementedError` until Phase 2 is built.

Phase 2 will implement:
  - Authenticating to Oracle Fusion via OAuth2 / basic auth.
  - Uploading the FBDI zip via the Oracle UCM (Universal Content Manager)
    REST endpoint or SOAP web service.
  - Polling for import completion and retrieving error reports.
  - Updating the run ledger with status=uploaded / failed.
"""

from __future__ import annotations

from pathlib import Path


class FbdiClient:
    """Oracle Fusion FBDI upload client (Phase 2 – not yet implemented).

    Args:
        oracle_base_url: Base URL of the Oracle Fusion instance.
        username:        Oracle Fusion username.
        password:        Oracle Fusion password.
    """

    def __init__(
        self,
        oracle_base_url: str,
        username: str,
        password: str,
    ) -> None:
        raise NotImplementedError(
            "FbdiClient is a Phase 2 feature and has not been implemented yet. "
            "Phase 1 ends at FBDI CSV/zip generation (status=generated)."
        )

    def upload(self, zip_path: Path, run_id: str) -> str:
        """Upload the FBDI zip to Oracle Fusion UCM.

        Args:
            zip_path: Path to the GlDailyRatesInterface.zip file.
            run_id:   ULID run identifier for audit logging.

        Returns:
            Oracle UCM document ID for the uploaded file.

        Raises:
            NotImplementedError: Always – Phase 2 stub.
        """
        raise NotImplementedError("Phase 2 stub")

    def trigger_import(self, ucm_doc_id: str) -> str:
        """Trigger the Oracle GL Daily Rates import process.

        Args:
            ucm_doc_id: UCM document ID returned by :meth:`upload`.

        Returns:
            Oracle ESS job ID for tracking import completion.

        Raises:
            NotImplementedError: Always – Phase 2 stub.
        """
        raise NotImplementedError("Phase 2 stub")

    def poll_import_status(self, job_id: str, timeout_seconds: int = 300) -> str:
        """Poll until the import job completes or times out.

        Args:
            job_id:          Oracle ESS job ID from :meth:`trigger_import`.
            timeout_seconds: Maximum seconds to wait before raising.

        Returns:
            Final job status string (e.g. ``'SUCCEEDED'``).

        Raises:
            NotImplementedError: Always – Phase 2 stub.
        """
        raise NotImplementedError("Phase 2 stub")
