"""Tests for oracle/csv_builder.py FBDI CSV and zip generation."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from fx_rates.oracle.csv_builder import (
    CSV_FILENAME,
    ZIP_FILENAME,
    _pad_to_6dp,
    build_csv_bytes,
    build_fbdi_rows,
    build_zip_bytes,
)


# ---------------------------------------------------------------------------
# _pad_to_6dp
# ---------------------------------------------------------------------------


class TestPadTo6dp:
    def test_integer_rate_gets_six_zeros(self):
        assert _pad_to_6dp(Decimal("1")) == "1.000000"

    def test_two_dp_padded(self):
        assert _pad_to_6dp(Decimal("1.25")) == "1.250000"

    def test_four_dp_padded(self):
        assert _pad_to_6dp(Decimal("1.3449")) == "1.344900"

    def test_exactly_six_dp_unchanged(self):
        assert _pad_to_6dp(Decimal("1.234567")) == "1.234567"

    def test_more_than_six_dp_truncated_not_rounded(self):
        # 1.234567890 truncated to 6dp = 1.234567 (not 1.234568)
        assert _pad_to_6dp(Decimal("1.234567890")) == "1.234567"

    def test_jpy_style_large_number(self):
        assert _pad_to_6dp(Decimal("193.45")) == "193.450000"

    def test_high_precision_truncation(self):
        # Verify ROUND_DOWN (floor) semantics: 1.9999999 → 1.999999
        assert _pad_to_6dp(Decimal("1.9999999")) == "1.999999"


# ---------------------------------------------------------------------------
# build_fbdi_rows
# ---------------------------------------------------------------------------


class TestBuildFbdiRows:
    _applied_from = date(2025, 4, 7)   # Monday
    _applied_to = date(2025, 4, 13)    # Sunday

    _rates = {
        "XUDLUSS": Decimal("1.254300"),
        "XUDLERS": Decimal("1.180100"),
        "XUDLJYS": Decimal("193.450000"),
        "XUDLCDS": Decimal("1.782300"),
    }

    def _get_rows(self):
        return build_fbdi_rows(self._rates, self._applied_from, self._applied_to)

    def test_returns_four_rows(self):
        rows = self._get_rows()
        assert len(rows) == 4

    def test_from_currency_is_gbp(self):
        rows = self._get_rows()
        assert all(r["From Currency"] == "GBP" for r in rows)

    def test_to_currencies_present(self):
        rows = self._get_rows()
        to_ccys = {r["To Currency"] for r in rows}
        assert to_ccys == {"USD", "EUR", "JPY", "CAD"}

    def test_conversion_type_is_corporate(self):
        rows = self._get_rows()
        assert all(r["User Conversion Type"] == "Corporate" for r in rows)

    def test_mode_flag_is_insert(self):
        rows = self._get_rows()
        assert all(r["Mode Flag"] == "Insert" for r in rows)

    def test_from_date_is_monday_dd_mm_yyyy(self):
        rows = self._get_rows()
        assert all(r["From Conversion Date"] == "07/04/2025" for r in rows)

    def test_to_date_is_sunday_dd_mm_yyyy(self):
        rows = self._get_rows()
        assert all(r["To Conversion Date"] == "13/04/2025" for r in rows)

    def test_inverse_conversion_rate_blank(self):
        rows = self._get_rows()
        assert all(r["Inverse Conversion Rate"] == "" for r in rows)

    def test_usd_rate_formatted_6dp(self):
        rows = self._get_rows()
        usd_row = next(r for r in rows if r["To Currency"] == "USD")
        assert usd_row["Conversion Rate"] == "1.254300"

    def test_jpy_rate_formatted_6dp(self):
        rows = self._get_rows()
        jpy_row = next(r for r in rows if r["To Currency"] == "JPY")
        assert jpy_row["Conversion Rate"] == "193.450000"

    def test_rate_truncated_not_rounded(self):
        rates = dict(self._rates)
        rates["XUDLUSS"] = Decimal("1.2549999")
        rows = build_fbdi_rows(rates, self._applied_from, self._applied_to)
        usd_row = next(r for r in rows if r["To Currency"] == "USD")
        assert usd_row["Conversion Rate"] == "1.254999"


# ---------------------------------------------------------------------------
# build_csv_bytes
# ---------------------------------------------------------------------------


class TestBuildCsvBytes:
    def test_is_valid_csv(self):
        rows = build_fbdi_rows(
            {
                "XUDLUSS": Decimal("1.25"),
                "XUDLERS": Decimal("1.18"),
                "XUDLJYS": Decimal("193.0"),
                "XUDLCDS": Decimal("1.78"),
            },
            date(2025, 4, 7),
            date(2025, 4, 13),
        )
        csv_bytes = build_csv_bytes(rows)
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        parsed = list(reader)
        assert len(parsed) == 4

    def test_headers_present(self):
        rows = build_fbdi_rows(
            {
                "XUDLUSS": Decimal("1.25"),
                "XUDLERS": Decimal("1.18"),
                "XUDLJYS": Decimal("193.0"),
                "XUDLCDS": Decimal("1.78"),
            },
            date(2025, 4, 7),
            date(2025, 4, 13),
        )
        csv_bytes = build_csv_bytes(rows)
        first_line = csv_bytes.decode("utf-8").splitlines()[0]
        assert "From Currency" in first_line
        assert "Conversion Rate" in first_line

    def test_crlf_line_endings(self):
        rows = build_fbdi_rows(
            {
                "XUDLUSS": Decimal("1.25"),
                "XUDLERS": Decimal("1.18"),
                "XUDLJYS": Decimal("193.0"),
                "XUDLCDS": Decimal("1.78"),
            },
            date(2025, 4, 7),
            date(2025, 4, 13),
        )
        csv_bytes = build_csv_bytes(rows)
        assert b"\r\n" in csv_bytes


# ---------------------------------------------------------------------------
# build_zip_bytes
# ---------------------------------------------------------------------------


class TestBuildZipBytes:
    def _make_zip(self):
        rows = build_fbdi_rows(
            {
                "XUDLUSS": Decimal("1.25"),
                "XUDLERS": Decimal("1.18"),
                "XUDLJYS": Decimal("193.0"),
                "XUDLCDS": Decimal("1.78"),
            },
            date(2025, 4, 7),
            date(2025, 4, 13),
        )
        csv_bytes = build_csv_bytes(rows)
        return build_zip_bytes(csv_bytes), csv_bytes

    def test_is_valid_zip(self):
        zip_bytes, _ = self._make_zip()
        assert zipfile.is_zipfile(io.BytesIO(zip_bytes))

    def test_contains_single_member_with_correct_name(self):
        zip_bytes, _ = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        assert names == [CSV_FILENAME]

    def test_csv_content_in_zip_matches_original(self):
        zip_bytes, csv_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            extracted = zf.read(CSV_FILENAME)
        assert extracted == csv_bytes
