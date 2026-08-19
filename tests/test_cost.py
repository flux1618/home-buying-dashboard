"""PITI, DTI, and cash to close, including parity with the JS dashboard."""

from __future__ import annotations

import pytest

from analyzer.core import cost
from analyzer.core.profile import load_profile


@pytest.fixture
def profile():
    return load_profile()


class TestAmortization:
    def test_known_payment(self):
        """$232,000 at 6.67% over 360 months."""
        pmt = cost.monthly_payment(232_000, 0.0667, 360)
        assert pmt == pytest.approx(1492.0, abs=1.0)

    def test_zero_rate_is_straight_division(self):
        assert cost.monthly_payment(360_000, 0.0, 360) == pytest.approx(1000.0)

    def test_zero_principal_is_zero(self):
        assert cost.monthly_payment(0, 0.0667, 360) == 0.0

    def test_cash_purchase_has_no_payment(self):
        assert cost.monthly_payment(-5000, 0.0667, 360) == 0.0

    def test_higher_rate_costs_more(self):
        assert cost.monthly_payment(232_000, 0.08, 360) > cost.monthly_payment(
            232_000, 0.0667, 360
        )

    def test_invalid_term_rejected(self):
        with pytest.raises(ValueError):
            cost.monthly_payment(232_000, 0.0667, 0)


class TestJSParity:
    """These must not drift from app.js or the site and the CLI disagree."""

    def test_piti_matches_dashboard_formula(self, profile):
        price = 312_000
        b = cost.compute(profile, price, 1780, 2016, 0.0, 2026)

        loan = price - 80_000
        pi = cost.monthly_payment(loan, 0.0667, 360)
        tax_mo = price * 0.04 * (280 / 1000) / 12
        ins_mo = 3102 / 12

        assert b.piti == pytest.approx(pi + tax_mo + ins_mo, abs=0.01)

    def test_tax_component_equals_hand_calculation(self, profile):
        b = cost.compute(profile, 312_000, 1780, 2016, 0.0, 2026)
        assert b.monthly_tax == pytest.approx(291.2, abs=0.1)

    def test_dti_is_piti_over_gross_monthly(self, profile):
        b = cost.compute(profile, 312_000, 1780, 2016, 0.0, 2026)
        assert b.front_end_dti == pytest.approx(b.piti / (406_480 / 12), abs=1e-6)


class TestCostStructure:
    def test_hoa_raises_piti_dollar_for_dollar(self, profile):
        no_hoa = cost.compute(profile, 300_000, 1600, 2015, 0.0, 2026)
        with_hoa = cost.compute(profile, 300_000, 1600, 2015, 150.0, 2026)
        assert with_hoa.piti - no_hoa.piti == pytest.approx(150.0)

    def test_maintenance_excluded_from_piti_included_in_true_monthly(self, profile):
        b = cost.compute(profile, 300_000, 1600, 2015, 0.0, 2026)
        assert b.true_monthly_low == pytest.approx(b.piti + b.reserve_low)
        assert b.true_monthly_high == pytest.approx(b.piti + b.reserve_high)
        assert b.reserve_low > 0

    def test_true_monthly_is_a_range_not_a_point(self, profile):
        b = cost.compute(profile, 300_000, 1600, 1985, 0.0, 2026)
        assert b.true_monthly_high > b.true_monthly_low

    def test_dti_flag_tracks_the_target(self, profile):
        cheap = cost.compute(profile, 250_000, 1600, 2015, 0.0, 2026)
        assert cheap.dti_within_target is True

    def test_non_owner_occupied_costs_more_monthly(self, profile):
        oo = cost.compute(profile, 300_000, 1600, 2015, 0.0, 2026, owner_occupied=True)
        noo = cost.compute(profile, 300_000, 1600, 2015, 0.0, 2026, owner_occupied=False)
        assert noo.monthly_tax > oo.monthly_tax


class TestClosing:
    def test_deed_fee_is_185_per_500(self):
        assert cost.deed_recording_fee(300_000) == pytest.approx(1110.0)

    def test_cash_to_close_is_down_plus_closing(self, profile):
        b = cost.compute(profile, 300_000, 1600, 2015, 0.0, 2026)
        expected = 80_000 + 300_000 * 0.03 + cost.deed_recording_fee(300_000)
        assert b.cash_to_close == pytest.approx(expected)
