"""UK bank holiday cache backed by python-holidays (England and Wales)."""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

import holidays

logger = logging.getLogger(__name__)


@lru_cache(maxsize=20)
def get_uk_holidays(year: int) -> frozenset[date]:
    """Return England and Wales bank holidays for *year* as a frozenset.

    Results are cached per year so repeated calls within a run are free.
    """
    eng_wales = holidays.country_holidays("GB", subdiv="ENG", years=year)
    result = frozenset(eng_wales.keys())
    logger.debug("Loaded %d UK bank holidays for %d", len(result), year)
    return result


def get_uk_holidays_for_range(start: date, end: date) -> frozenset[date]:
    """Return UK bank holidays covering the date range [start, end].

    Fetches per-year caches and unions them.
    """
    combined: set[date] = set()
    for year in range(start.year, end.year + 1):
        combined |= get_uk_holidays(year)
    return frozenset(combined)


def is_boe_publication_day(d: date, holidays: frozenset[date]) -> bool:
    """Return True if *d* is a day BoE would publish rates (Mon-Fri, not a bank holiday)."""
    return d.weekday() < 5 and d not in holidays
