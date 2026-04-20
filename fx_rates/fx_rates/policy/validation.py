"""Variance and completeness validation for FX rates."""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Variance thresholds (as fractions, e.g. 0.02 = 2%)
_WARN_THRESHOLD = Decimal("0.02")
_HOLD_THRESHOLD = Decimal("0.05")
_BLOCK_THRESHOLD = Decimal("0.10")

_100 = Decimal("100")


class VarianceHoldError(Exception):
    """Rate variance exceeded 5% – soft hold, can be overridden with --force.

    Exit code: 20.
    """

    exit_code: int = 20

    def __init__(self, breaches: list[VarianceBreach]) -> None:
        lines = [
            f"  {b.from_ccy}/{b.to_ccy}: {b.pct_change:.2f}% "
            f"(prior={b.prior_rate}, current={b.current_rate})"
            for b in breaches
        ]
        super().__init__(
            "Variance HOLD – the following pairs moved >5%:\n"
            + "\n".join(lines)
            + "\nUse --force to override."
        )
        self.breaches = breaches


class VarianceBlockError(Exception):
    """Rate variance exceeded 10% – hard block, no override allowed.

    Exit code: 21.
    """

    exit_code: int = 21

    def __init__(self, breaches: list[VarianceBreach]) -> None:
        lines = [
            f"  {b.from_ccy}/{b.to_ccy}: {b.pct_change:.2f}% "
            f"(prior={b.prior_rate}, current={b.current_rate})"
            for b in breaches
        ]
        super().__init__(
            "Variance BLOCK – the following pairs moved >10% (no override):\n"
            + "\n".join(lines)
        )
        self.breaches = breaches


class VarianceBreach:
    """Details of a single variance breach."""

    def __init__(
        self,
        from_ccy: str,
        to_ccy: str,
        prior_rate: Decimal,
        current_rate: Decimal,
        pct_change: Decimal,
    ) -> None:
        self.from_ccy = from_ccy
        self.to_ccy = to_ccy
        self.prior_rate = prior_rate
        self.current_rate = current_rate
        self.pct_change = pct_change

    def __repr__(self) -> str:
        return (
            f"VarianceBreach({self.from_ccy}/{self.to_ccy} "
            f"{self.pct_change:.2f}%)"
        )


def check_variance(
    current_rates: dict[tuple[str, str], Decimal],
    prior_rates: dict[tuple[str, str], Decimal],
    *,
    force: bool = False,
) -> list[VarianceBreach]:
    """Check current rates against prior rates for variance breaches.

    Args:
        current_rates: Mapping of ``(from_ccy, to_ccy)`` → current rate.
        prior_rates:   Mapping of ``(from_ccy, to_ccy)`` → prior rate.
                       Pairs absent from prior_rates are skipped.
        force:         If True, HOLD and BLOCK errors are demoted to warnings.

    Returns:
        List of all ``VarianceBreach`` objects (warning level and above).

    Raises:
        VarianceBlockError: If any pair exceeds 10% (hard block).
        VarianceHoldError:  If any pair exceeds 5% and *force* is False.
    """
    if not prior_rates:
        logger.info("No prior run found – skipping variance checks.")
        return []

    warn_breaches: list[VarianceBreach] = []
    hold_breaches: list[VarianceBreach] = []
    block_breaches: list[VarianceBreach] = []

    for pair, current in sorted(current_rates.items()):
        prior = prior_rates.get(pair)
        if prior is None:
            logger.debug("No prior rate for %s/%s – skipping variance check.", *pair)
            continue

        if prior == 0:
            logger.warning("Prior rate for %s/%s is zero – skipping variance check.", *pair)
            continue

        pct_change = abs((current - prior) / prior * _100)
        breach = VarianceBreach(*pair, prior, current, pct_change)

        if pct_change > _BLOCK_THRESHOLD * _100:
            block_breaches.append(breach)
        elif pct_change > _HOLD_THRESHOLD * _100:
            hold_breaches.append(breach)
        elif pct_change > _WARN_THRESHOLD * _100:
            warn_breaches.append(breach)
        else:
            logger.debug(
                "%s/%s variance %.4f%% – OK", *pair, pct_change
            )

    for b in warn_breaches:
        logger.warning(
            "Variance WARNING %s/%s: %.2f%% (prior=%s current=%s)",
            b.from_ccy, b.to_ccy, b.pct_change, b.prior_rate, b.current_rate,
        )

    if block_breaches:
        if force:
            for b in block_breaches:
                logger.warning(
                    "Variance BLOCK overridden (--force) %s/%s: %.2f%%",
                    b.from_ccy, b.to_ccy, b.pct_change,
                )
        else:
            raise VarianceBlockError(block_breaches)

    if hold_breaches:
        all_hold = warn_breaches + hold_breaches
        if force:
            for b in hold_breaches:
                logger.warning(
                    "Variance HOLD overridden (--force) %s/%s: %.2f%%",
                    b.from_ccy, b.to_ccy, b.pct_change,
                )
        else:
            raise VarianceHoldError(hold_breaches)

    return warn_breaches + hold_breaches


def check_completeness(
    rates: dict[str, Decimal],
    required_series: frozenset[str],
) -> None:
    """Assert all required series are present with positive values.

    Raises:
        ValueError: If any series is missing or non-positive.
    """
    missing = required_series - set(rates.keys())
    if missing:
        raise ValueError(f"Missing required series: {sorted(missing)}")

    for series, rate in rates.items():
        if series in required_series and rate <= 0:
            raise ValueError(
                f"Series {series} has non-positive rate: {rate}"
            )
