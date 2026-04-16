"""Build Oracle FBDI GlDailyRatesInterface CSV and zip artefacts."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from ..providers.boe import SERIES_TO_PAIR

# FBDI column headers (exact Oracle field names)
_COLUMNS = [
    "From Currency",
    "To Currency",
    "User Conversion Type",
    "From Conversion Date",
    "To Conversion Date",
    "Conversion Rate",
    "Inverse Conversion Rate",
    "Mode Flag",
]

_CONVERSION_TYPE = "Corporate"
_MODE_FLAG = "Insert"
_SIX_DP = Decimal("0.000001")

CSV_FILENAME = "GlDailyRatesInterface.csv"
ZIP_FILENAME = "GlDailyRatesInterface.zip"


def _pad_to_6dp(rate: Decimal) -> str:
    """Right-pad a rate to exactly 6 decimal places without rounding.

    E.g. Decimal('1.3449') → '1.344900'
         Decimal('1.234567890') → '1.234567'   (truncate, not round)
    """
    # Truncate to 6dp using ROUND_DOWN (floor towards zero)
    truncated = rate.quantize(_SIX_DP, rounding=ROUND_DOWN)
    # Format with exactly 6dp
    return format(truncated, "f")


def _fmt_date(d: date) -> str:
    """Format date as DD/MM/YYYY for FBDI."""
    return d.strftime("%d/%m/%Y")


def build_fbdi_rows(
    rates: dict[str, Decimal],
    applied_from: date,
    applied_to: date,
) -> list[dict[str, str]]:
    """Build FBDI data rows from rate dict and applied window.

    Args:
        rates:         Series-code → Decimal rate mapping (e.g. XUDLUSS → 1.25).
        applied_from:  Monday of the applied window (From Conversion Date).
        applied_to:    Sunday of the applied window (To Conversion Date).

    Returns:
        List of row dicts keyed by FBDI column name, one per currency pair.
    """
    rows = []
    from_date_str = _fmt_date(applied_from)
    to_date_str = _fmt_date(applied_to)

    for series_code, (from_ccy, to_ccy) in sorted(SERIES_TO_PAIR.items()):
        rate = rates[series_code]
        rows.append(
            {
                "From Currency": from_ccy,
                "To Currency": to_ccy,
                "User Conversion Type": _CONVERSION_TYPE,
                "From Conversion Date": from_date_str,
                "To Conversion Date": to_date_str,
                "Conversion Rate": _pad_to_6dp(rate),
                "Inverse Conversion Rate": "",
                "Mode Flag": _MODE_FLAG,
            }
        )
    return rows


def build_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    """Serialise FBDI rows to CSV bytes (UTF-8, CRLF line endings).

    Args:
        rows: List of row dicts as returned by :func:`build_fbdi_rows`.

    Returns:
        Raw bytes of the CSV file.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_COLUMNS,
        lineterminator="\r\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_zip_bytes(csv_bytes: bytes) -> bytes:
    """Wrap *csv_bytes* in an Oracle FBDI-compatible zip archive.

    The zip contains a single member named :data:`CSV_FILENAME`.

    Args:
        csv_bytes: Raw CSV bytes as returned by :func:`build_csv_bytes`.

    Returns:
        Raw zip bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(CSV_FILENAME, csv_bytes)
    return buf.getvalue()


def write_fbdi_artefacts(
    output_dir: Path,
    rates: dict[str, Decimal],
    applied_from: date,
    applied_to: date,
) -> tuple[Path, Path]:
    """Generate and write CSV + zip artefacts to *output_dir*.

    Args:
        output_dir:   Directory in which to write the files (must exist).
        rates:        Series-code → Decimal rate mapping.
        applied_from: Monday of the applied window.
        applied_to:   Sunday of the applied window.

    Returns:
        ``(csv_path, zip_path)`` absolute Path objects.

    Raises:
        OSError: If writing fails.
    """
    rows = build_fbdi_rows(rates, applied_from, applied_to)
    csv_bytes = build_csv_bytes(rows)
    zip_bytes = build_zip_bytes(csv_bytes)

    csv_path = output_dir / CSV_FILENAME
    zip_path = output_dir / ZIP_FILENAME

    try:
        csv_path.write_bytes(csv_bytes)
        zip_path.write_bytes(zip_bytes)
    except OSError as exc:
        raise OSError(
            f"Failed to write FBDI artefacts to {output_dir}: {exc}"
        ) from exc

    return csv_path, zip_path
