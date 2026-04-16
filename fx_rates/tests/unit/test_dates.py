"""Exhaustive tests for policy/dates.py date logic.

Covers:
  - Normal weekday runs
  - DST boundary weeks
  - UK bank holiday fallbacks (Good Friday, Easter Monday, etc.)
  - Specific 2026 and 2027 Easter cases from the spec
  - All UK bank holidays 2025-2030 (no crash guarantee)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fx_rates.policy.dates import resolve_applied_window, resolve_source_date
from fx_rates.policy.holidays import get_uk_holidays, get_uk_holidays_for_range


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def holidays_for(applied_from: date) -> frozenset[date]:
    """Return holiday set covering the week before applied_from."""
    return get_uk_holidays_for_range(
        applied_from - timedelta(days=14),
        applied_from,
    )


# ---------------------------------------------------------------------------
# resolve_applied_window tests
# ---------------------------------------------------------------------------


class TestResolveAppliedWindow:
    def test_monday_returns_same_monday(self):
        monday = date(2025, 4, 7)
        af, at = resolve_applied_window(monday)
        assert af == monday
        assert at == date(2025, 4, 13)

    def test_tuesday_returns_preceding_monday(self):
        tuesday = date(2025, 4, 8)
        af, at = resolve_applied_window(tuesday)
        assert af == date(2025, 4, 7)
        assert at == date(2025, 4, 13)

    def test_wednesday_returns_preceding_monday(self):
        wednesday = date(2025, 4, 9)
        af, at = resolve_applied_window(wednesday)
        assert af == date(2025, 4, 7)
        assert at == date(2025, 4, 13)

    def test_friday_returns_preceding_monday(self):
        friday = date(2025, 4, 11)
        af, at = resolve_applied_window(friday)
        assert af == date(2025, 4, 7)
        assert at == date(2025, 4, 13)

    def test_sunday_returns_preceding_monday(self):
        sunday = date(2025, 4, 13)
        af, at = resolve_applied_window(sunday)
        assert af == date(2025, 4, 7)
        assert at == date(2025, 4, 13)

    def test_applied_to_is_always_sunday(self):
        for offset in range(7):
            d = date(2025, 6, 2) + timedelta(days=offset)  # week of 2 Jun
            af, at = resolve_applied_window(d)
            assert at.weekday() == 6, f"applied_to should be Sunday for run_date={d}"

    def test_applied_from_is_always_monday(self):
        for offset in range(7):
            d = date(2025, 6, 2) + timedelta(days=offset)
            af, at = resolve_applied_window(d)
            assert af.weekday() == 0, f"applied_from should be Monday for run_date={d}"

    def test_applied_window_span_is_6_days(self):
        for offset in range(7):
            d = date(2025, 1, 6) + timedelta(days=offset)
            af, at = resolve_applied_window(d)
            assert (at - af).days == 6

    def test_dst_spring_forward_week_uk_2025(self):
        # UK clocks go forward last Sunday in March; week of 31 Mar 2025
        run_date = date(2025, 3, 31)  # Monday (spring forward day)
        af, at = resolve_applied_window(run_date)
        assert af == date(2025, 3, 31)
        assert at == date(2025, 4, 6)

    def test_dst_autumn_back_week_uk_2025(self):
        # UK clocks go back last Sunday in Oct; week of 27 Oct 2025
        run_date = date(2025, 10, 27)  # Monday
        af, at = resolve_applied_window(run_date)
        assert af == date(2025, 10, 27)
        assert at == date(2025, 11, 2)

    def test_year_boundary_week(self):
        # Week spanning 29 Dec 2025 – 4 Jan 2026
        run_date = date(2025, 12, 29)
        af, at = resolve_applied_window(run_date)
        assert af == date(2025, 12, 29)
        assert at == date(2026, 1, 4)


# ---------------------------------------------------------------------------
# resolve_source_date tests
# ---------------------------------------------------------------------------


class TestResolveSourceDate:
    def test_normal_monday_gives_friday(self):
        # Week of 7 Apr 2025 (no bank holidays)
        applied_from = date(2025, 4, 7)
        h = holidays_for(applied_from)
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2025, 4, 4)  # Friday
        assert exc is False

    def test_tuesday_late_run_still_gives_correct_monday_window(self):
        # Late run on Tuesday – window still Mon 7 Apr; source still Fri 4 Apr
        applied_from = date(2025, 4, 7)  # resolve_applied_window(tuesday) → this
        h = holidays_for(applied_from)
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2025, 4, 4)  # same Friday
        assert exc is False

    def test_good_friday_2025_falls_back_to_thursday(self):
        # Good Friday 2025 = 18 Apr; week of 21 Apr uses Thursday 17 Apr
        applied_from = date(2025, 4, 21)
        h = holidays_for(applied_from)
        assert date(2025, 4, 18) in h, "Good Friday 18 Apr 2025 should be a holiday"
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2025, 4, 17)  # Thursday
        assert exc is True  # had to go back past Friday

    def test_easter_monday_2025_week_source(self):
        # Easter Monday 2025 = 21 Apr.
        # Week of 22 Apr: source = most recent pub day before Mon 22 Apr
        # Fri 18 Apr = Good Friday (holiday), Mon 21 Apr = Easter Monday (holiday)
        # → Thursday 17 Apr
        applied_from = date(2025, 4, 22)
        h = holidays_for(applied_from)
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2025, 4, 17)  # Thursday (Good Friday & Easter Mon both off)
        assert exc is True

    def test_2026_easter_week_of_7_apr(self):
        # 2026 Easter: Good Friday = 3 Apr, Easter Monday = 6 Apr
        # Week of 7 Apr 2026: source should be Wednesday 2 Apr
        # (Thu 2 Apr is not a holiday, Fri 3 Apr = Good Friday)
        applied_from = date(2026, 4, 7)
        h = holidays_for(applied_from)
        assert date(2026, 4, 3) in h, "Good Friday 3 Apr 2026 should be a holiday"
        assert date(2026, 4, 6) in h, "Easter Monday 6 Apr 2026 should be a holiday"
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2026, 4, 2)  # Wednesday
        assert exc is True

    def test_2027_easter_week_of_30_mar(self):
        # 2027 Easter: Good Friday = 26 Mar, Easter Monday = 29 Mar
        # Week of 30 Mar 2027: source should be Thursday 25 Mar
        # (Friday 26 Mar = Good Friday, Thursday 25 Mar is not a holiday)
        applied_from = date(2027, 3, 30)
        h = holidays_for(applied_from)
        assert date(2027, 3, 26) in h, "Good Friday 26 Mar 2027 should be a holiday"
        assert date(2027, 3, 29) in h, "Easter Monday 29 Mar 2027 should be a holiday"
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2027, 3, 25)  # Thursday
        assert exc is True  # had to go back past (would-be) Friday

    def test_source_date_is_weekday(self):
        """Source date should never fall on a weekend."""
        for year in range(2025, 2031):
            for week_offset in range(52):
                d = date(year, 1, 6) + timedelta(weeks=week_offset)
                # Ensure d is a Monday
                if d.weekday() != 0:
                    continue
                h = get_uk_holidays_for_range(d - timedelta(days=14), d)
                src, _ = resolve_source_date(d, h)
                assert src.weekday() < 5, (
                    f"source_date {src} is a weekend for applied_from={d}"
                )

    def test_source_date_not_a_bank_holiday(self):
        """Source date should never be a UK bank holiday."""
        for year in range(2025, 2031):
            for week_offset in range(52):
                d = date(year, 1, 6) + timedelta(weeks=week_offset)
                if d.weekday() != 0:
                    continue
                h = get_uk_holidays_for_range(d - timedelta(days=14), d)
                src, _ = resolve_source_date(d, h)
                assert src not in h, (
                    f"source_date {src} is a bank holiday for applied_from={d}"
                )

    def test_no_exceptions_for_all_bank_holiday_weeks_2025_2030(self):
        """Weeks immediately after any UK bank holiday must not raise."""
        errors = []
        for year in range(2025, 2031):
            h_year = get_uk_holidays(year)
            for bh in h_year:
                # Week that follows a bank holiday
                bh_week_monday = bh - timedelta(days=bh.weekday())
                next_monday = bh_week_monday + timedelta(weeks=1)
                h_range = get_uk_holidays_for_range(
                    next_monday - timedelta(days=14), next_monday
                )
                try:
                    src, exc = resolve_source_date(next_monday, h_range)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"applied_from={next_monday} (after bh={bh}): {e}")
        assert not errors, "\n".join(errors)

    def test_exception_flag_false_for_normal_week(self):
        # No bank holidays around week of 9 Jun 2025
        applied_from = date(2025, 6, 9)
        h = holidays_for(applied_from)
        _, exc = resolve_source_date(applied_from, h)
        assert exc is False

    def test_source_strictly_before_applied_from(self):
        """source_date must always be strictly before applied_from."""
        for offset_weeks in range(0, 200, 7):
            applied_from = date(2025, 1, 6) + timedelta(weeks=offset_weeks)
            h = get_uk_holidays_for_range(
                applied_from - timedelta(days=14), applied_from
            )
            src, _ = resolve_source_date(applied_from, h)
            assert src < applied_from, (
                f"source_date {src} is not before applied_from {applied_from}"
            )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_christmas_2025_week(self):
        # Christmas Day = 25 Dec 2025 (Thursday), Boxing Day = 26 Dec (Friday)
        # Both are bank holidays → week of 29 Dec should fall back to Wed 24 Dec
        applied_from = date(2025, 12, 29)
        h = holidays_for(applied_from)
        src, exc = resolve_source_date(applied_from, h)
        assert src == date(2025, 12, 24)  # Wednesday
        assert exc is True

    def test_new_year_2026_week(self):
        # 1 Jan 2026 = Thursday (New Year's Day bank holiday)
        # Week of 5 Jan 2026: source = Fri 2 Jan (not a bank holiday)
        applied_from = date(2026, 1, 5)
        h = holidays_for(applied_from)
        src, exc = resolve_source_date(applied_from, h)
        # 1 Jan is a Thursday bank holiday, 2 Jan is Friday – should be fine
        assert src == date(2026, 1, 2)
        assert exc is False
