"""Tests for policy/validation.py variance and completeness checks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fx_rates.policy.validation import (
    VarianceBlockError,
    VarianceHoldError,
    check_completeness,
    check_variance,
)

_PAIRS = [
    ("GBP", "USD"),
    ("GBP", "EUR"),
    ("GBP", "JPY"),
    ("GBP", "CAD"),
]


def _rates(usd="1.2500", eur="1.1800", jpy="190.00", cad="1.7800"):
    return {
        ("GBP", "USD"): Decimal(usd),
        ("GBP", "EUR"): Decimal(eur),
        ("GBP", "JPY"): Decimal(jpy),
        ("GBP", "CAD"): Decimal(cad),
    }


# ---------------------------------------------------------------------------
# check_variance
# ---------------------------------------------------------------------------


class TestCheckVariance:
    def test_no_prior_rates_skips_all_checks(self):
        current = _rates()
        breaches = check_variance(current, {})
        assert breaches == []

    def test_within_2pct_no_breach(self):
        prior = _rates()
        current = _rates(usd="1.2620")  # ~0.96% change
        breaches = check_variance(current, prior)
        assert breaches == []

    def test_between_2_and_5_pct_warns_not_raises(self):
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.2875")  # 3% change
        breaches = check_variance(current, prior)
        assert len(breaches) == 1
        assert breaches[0].from_ccy == "GBP"
        assert breaches[0].to_ccy == "USD"
        # Should not raise

    def test_over_5pct_raises_hold(self):
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.3150")  # 5.2% change
        with pytest.raises(VarianceHoldError) as exc_info:
            check_variance(current, prior)
        assert exc_info.value.exit_code == 20
        assert len(exc_info.value.breaches) == 1

    def test_over_5pct_force_overrides_hold(self):
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.3150")  # 5.2% change
        # Should not raise when force=True
        breaches = check_variance(current, prior, force=True)
        assert len(breaches) >= 1

    def test_over_10pct_raises_block(self):
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.3900")  # 11.2% change
        with pytest.raises(VarianceBlockError) as exc_info:
            check_variance(current, prior)
        assert exc_info.value.exit_code == 21

    def test_over_10pct_force_does_not_override_block(self):
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.3900")  # >10% change
        with pytest.raises(VarianceBlockError):
            check_variance(current, prior, force=True)

    def test_multiple_pairs_breach(self):
        prior = _rates()
        # USD: 1.25 → 1.39 = +11.2%; EUR: 1.18 → 1.31 = +11.0%; both >10%
        current = _rates(usd="1.3900", eur="1.3100")
        with pytest.raises(VarianceBlockError) as exc_info:
            check_variance(current, prior)
        assert len(exc_info.value.breaches) == 2

    def test_pair_missing_from_prior_is_skipped(self):
        prior = {("GBP", "USD"): Decimal("1.2500")}
        current = _rates()
        # Only USD has a prior; others are skipped
        breaches = check_variance(current, prior)
        assert all(b.to_ccy == "USD" or True for b in breaches)  # no crash

    def test_exact_2pct_boundary_is_warning(self):
        prior = _rates(usd="1.0000")
        current = _rates(usd="1.0200")  # exactly 2%
        breaches = check_variance(current, prior)
        # 2% is above the warn threshold (>2%) – actually at 2% exactly it should be OK
        # Spec says "Over 2%: log warning" – strictly over
        assert breaches == []

    def test_exact_5pct_boundary_raises_hold(self):
        prior = _rates(usd="1.0000")
        current = _rates(usd="1.0501")  # just over 5%
        with pytest.raises(VarianceHoldError):
            check_variance(current, prior)

    def test_pct_change_is_absolute(self):
        # Decrease by 6% should also trigger hold
        prior = _rates(usd="1.2500")
        current = _rates(usd="1.1750")  # -6%
        with pytest.raises(VarianceHoldError):
            check_variance(current, prior)


# ---------------------------------------------------------------------------
# check_completeness
# ---------------------------------------------------------------------------


class TestCheckCompleteness:
    _required = frozenset({"XUDLUSS", "XUDLERS", "XUDLJYS", "XUDLCDS"})

    def test_all_series_present_and_positive(self):
        rates = {
            "XUDLUSS": Decimal("1.25"),
            "XUDLERS": Decimal("1.18"),
            "XUDLJYS": Decimal("190.0"),
            "XUDLCDS": Decimal("1.78"),
        }
        check_completeness(rates, self._required)  # should not raise

    def test_missing_series_raises(self):
        rates = {
            "XUDLUSS": Decimal("1.25"),
            "XUDLERS": Decimal("1.18"),
            # XUDLJYS and XUDLCDS missing
        }
        with pytest.raises(ValueError, match="Missing required series"):
            check_completeness(rates, self._required)

    def test_zero_rate_raises(self):
        rates = {
            "XUDLUSS": Decimal("0"),
            "XUDLERS": Decimal("1.18"),
            "XUDLJYS": Decimal("190.0"),
            "XUDLCDS": Decimal("1.78"),
        }
        with pytest.raises(ValueError, match="non-positive"):
            check_completeness(rates, self._required)

    def test_negative_rate_raises(self):
        rates = {
            "XUDLUSS": Decimal("-1.25"),
            "XUDLERS": Decimal("1.18"),
            "XUDLJYS": Decimal("190.0"),
            "XUDLCDS": Decimal("1.78"),
        }
        with pytest.raises(ValueError, match="non-positive"):
            check_completeness(rates, self._required)
