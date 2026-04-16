"""Pure date-logic functions for the fx_rates policy layer.

All functions are side-effect free - they accept only dates and holiday sets,
never perform I/O.
"""

from __future__ import annotations

from datetime import date, timedelta


def resolve_applied_window(run_date: date) -> tuple[date, date]:
    """Return the Oracle applied rate window for a given run date.

    The window is always a full Mon–Sun week:
      - applied_from = most recent Monday on or before *run_date*
      - applied_to   = applied_from + 6 days (Sunday)

    Args:
        run_date: The date the process is executed (today or a replay date).

    Returns:
        ``(applied_from, applied_to)`` as a tuple of dates.

    Examples:
        >>> resolve_applied_window(date(2025, 4, 7))   # Monday
        (date(2025, 4, 7), date(2025, 4, 13))
        >>> resolve_applied_window(date(2025, 4, 9))   # Wednesday
        (date(2025, 4, 7), date(2025, 4, 13))
        >>> resolve_applied_window(date(2025, 4, 13))  # Sunday
        (date(2025, 4, 7), date(2025, 4, 13))
    """
    # weekday(): Monday=0, Sunday=6
    days_since_monday = run_date.weekday()
    applied_from = run_date - timedelta(days=days_since_monday)
    applied_to = applied_from + timedelta(days=6)
    return applied_from, applied_to


def resolve_source_date(
    applied_from: date,
    uk_holidays: frozenset[date],
) -> tuple[date, bool]:
    """Return the BoE source date for the given applied_from date.

    The source date is the most recent BoE *publication day* strictly before
    *applied_from* (i.e. the Friday before the Monday window, or earlier if
    that Friday is a bank holiday).

    BoE publishes on Mon–Fri excluding England/Wales bank holidays.

    Args:
        applied_from: The Monday start of the Oracle applied window.
        uk_holidays:  Frozenset of England/Wales bank holidays (any relevant years).

    Returns:
        ``(source_date, exception)`` where *exception* is True when the
        algorithm had to go back past the immediately preceding Friday (i.e.
        a bank-holiday fallback triggered).

    Raises:
        ValueError: If no publication day is found within 14 days (safeguard).
    """
    # Start from the day before applied_from (the Sunday) and walk back
    candidate = applied_from - timedelta(days=1)
    first_friday = applied_from - timedelta(days=3)  # Friday before Monday

    for _ in range(14):
        # A BoE publication day: weekday Mon(0)–Fri(4) and not a bank holiday
        if candidate.weekday() < 5 and candidate not in uk_holidays:
            exception = candidate < first_friday
            return candidate, exception
        candidate -= timedelta(days=1)

    raise ValueError(
        f"No BoE publication day found within 14 days before {applied_from}. "
        "Check holiday calendar."
    )
