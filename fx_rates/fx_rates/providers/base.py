"""RateProvider protocol - base interface for FX rate providers."""

from datetime import date
from decimal import Decimal
from typing import Protocol


class RateProvider(Protocol):
    """Protocol that all FX rate providers must satisfy."""

    def fetch(
        self,
        source_date: date,
    ) -> dict[str, Decimal]:
        """Fetch rates for the given source date.

        Args:
            source_date: The date for which to fetch rates.

        Returns:
            Mapping of series code (e.g. ``XUDLUSS``) to rate as Decimal.

        Raises:
            SourceUnavailableError: If the provider cannot supply rates.
        """
        ...


class SourceUnavailableError(Exception):
    """Raised when a rate provider cannot supply the requested rates."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause
