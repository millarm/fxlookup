"""Bank of England IADB rate provider (Phase 1 primary source)."""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
import requests

from .base import SourceUnavailableError

logger = logging.getLogger(__name__)

# BoE IADB endpoint
_BOE_URL = (
    "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
)

# Series codes and their Oracle currency-pair labels
SERIES_TO_PAIR: dict[str, tuple[str, str]] = {
    "XUDLUSS": ("GBP", "USD"),
    "XUDLERS": ("GBP", "EUR"),
    "XUDLJYS": ("GBP", "JPY"),
    "XUDLCDS": ("GBP", "CAD"),
}

REQUIRED_SERIES: frozenset[str] = frozenset(SERIES_TO_PAIR.keys())

# Fetch window: source_date ± 3 calendar days (tolerates holiday shifts)
_WINDOW_DAYS = 3


def _fmt_date(d: date) -> str:
    """Format a date as DD/Mmm/YYYY for the BoE IADB query string."""
    return d.strftime("%d/%b/%Y")


class BoEProvider:
    """Fetches FX rates from the Bank of England IADB API.

    Args:
        session: Optional requests.Session for dependency injection / testing.
        timeout: HTTP timeout in seconds (default 30).
    """

    # BoE blocks headless requests without a plausible browser User-Agent
    _DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = 30,
    ) -> None:
        if session is None:
            session = requests.Session()
            session.headers.update(self._DEFAULT_HEADERS)
        self._session = session
        self._timeout = timeout

        # Populated after fetch() for evidence pack
        self.last_response_headers: dict[str, str] = {}
        self.last_http_status: int = 0
        self.last_url: str = ""
        self.last_timestamp: str = ""
        self.last_raw_csv: bytes = b""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, source_date: date) -> dict[str, Decimal]:
        """Fetch rates for *source_date* from BoE IADB.

        A ±3-day window around *source_date* is requested; the row for the
        exact *source_date* must be present in the response.

        Returns:
            ``{series_code: Decimal(rate), ...}`` for all 4 required series.

        Raises:
            SourceUnavailableError: On any HTTP, parse or validation failure.
        """
        date_from = source_date - timedelta(days=_WINDOW_DAYS)
        date_to = source_date + timedelta(days=_WINDOW_DAYS)

        params = self._build_params(date_from, date_to)
        raw_bytes, headers, status, url, ts = self._do_request(params)

        self.last_raw_csv = raw_bytes
        self.last_response_headers = dict(headers)
        self.last_http_status = status
        self.last_url = url
        self.last_timestamp = ts

        rates = self._parse_and_validate(raw_bytes, source_date)
        return rates

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_params(self, date_from: date, date_to: date) -> dict[str, str]:
        return {
            "csv.x": "yes",
            "Datefrom": _fmt_date(date_from),
            "Dateto": _fmt_date(date_to),
            "SeriesCodes": ",".join(sorted(REQUIRED_SERIES)),
            "CSVF": "TN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N",
        }

    def _do_request(
        self, params: dict[str, str]
    ) -> tuple[bytes, Any, int, str, str]:
        """Execute the HTTP GET and return (body, headers, status, url, timestamp)."""
        import datetime as dt

        try:
            resp = self._session.get(
                _BOE_URL, params=params, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise SourceUnavailableError(
                f"BoE HTTP request failed: {exc}", cause=exc
            ) from exc

        ts = dt.datetime.now(dt.timezone.utc).isoformat()

        if resp.status_code != 200:
            raise SourceUnavailableError(
                f"BoE returned HTTP {resp.status_code} (expected 200). "
                f"URL: {resp.url}"
            )

        return resp.content, resp.headers, resp.status_code, resp.url, ts

    def _parse_and_validate(
        self, raw_bytes: bytes, source_date: date
    ) -> dict[str, Decimal]:
        """Parse the CSV response and validate all requirements.

        Raises:
            SourceUnavailableError: On any parse or validation failure.
        """
        try:
            df = pd.read_csv(
                io.BytesIO(raw_bytes),
                header=0,
                dtype=str,
            )
        except Exception as exc:
            raise SourceUnavailableError(
                f"BoE response is not parseable CSV: {exc}", cause=exc
            ) from exc

        # Normalise column names (strip whitespace)
        df.columns = [c.strip() for c in df.columns]

        # The first column is the date column (label varies)
        date_col = df.columns[0]
        series_cols = set(df.columns[1:])

        # Check all 4 series are present
        missing = REQUIRED_SERIES - series_cols
        if missing:
            raise SourceUnavailableError(
                f"BoE response missing required series: {sorted(missing)}"
            )

        # Parse dates - BoE uses DD Mmm YYYY format
        try:
            df[date_col] = pd.to_datetime(df[date_col], dayfirst=True, format="mixed")
        except Exception as exc:
            raise SourceUnavailableError(
                f"BoE response date column unparseable: {exc}", cause=exc
            ) from exc

        # Find row for exact source_date
        mask = df[date_col].dt.date == source_date
        matching = df[mask]
        if matching.empty:
            raise SourceUnavailableError(
                f"BoE response contains no row for source_date {source_date}. "
                f"Available dates: {sorted(df[date_col].dt.date.tolist())}"
            )

        row = matching.iloc[0]
        rates: dict[str, Decimal] = {}

        for series in sorted(REQUIRED_SERIES):
            raw_val = str(row[series]).strip()

            # Check for NaN / null
            if raw_val.lower() in ("nan", "none", "", "n/a", "null"):
                raise SourceUnavailableError(
                    f"BoE series {series} has null/NaN value for {source_date}"
                )

            try:
                d = Decimal(raw_val)
            except InvalidOperation as exc:
                raise SourceUnavailableError(
                    f"BoE series {series} value {raw_val!r} is not a valid decimal",
                    cause=exc,
                ) from exc

            if d <= 0:
                raise SourceUnavailableError(
                    f"BoE series {series} value {d} is not positive for {source_date}"
                )

            rates[series] = d
            logger.debug("BoE %s on %s = %s", series, source_date, d)

        logger.info(
            "BoE fetch OK for %s: %s",
            source_date,
            {k: str(v) for k, v in rates.items()},
        )
        return rates
