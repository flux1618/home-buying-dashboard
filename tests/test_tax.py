"""Assessment-ratio math. Highest-priority tests: this is where the real money bug was.

The dashboard originally hardcoded the 4% owner-occupied path, which silently
mispriced any listing currently taxed at 6%.
"""

from __future__ import annotations

import pytest

from analyzer.core import millage, tax


class TestAssessedValue:
    def test_primary_ratio_is_four_percent(self):
        assert tax.assessed_value(300_000, tax.PRIMARY_RATIO) == 12_000

    def test_non_primary_ratio_is_six_percent(self):
        assert tax.assessed_value(300_000, tax.NON_PRIMARY_RATIO) == 18_000

    def test_resets_to_sale_price_not_prior_assessment(self):
        """The whole point: assessed value follows the price you pay."""
        assert tax.assessed_value(400_000, 0.04) == 16_000
        assert tax.assessed_value(200_000, 0.04) == 8_000

    def test_negative_price_rejected(self):
        with pytest.raises(ValueError):
            tax.assessed_value(-1, 0.04)


class TestAnnualTax:
    def test_mills_are_thousandths(self):
        # 300k at 4% = 12,000 assessed. 280 mills = 0.280. -> 3,360
        assert tax.annual_tax(300_000, 0.04, 280) == pytest.approx(3_360.0)

    def test_zero_mills_is_zero_tax(self):
        assert tax.annual_tax(300_000, 0.04, 0) == 0.0

    def test_negative_mills_rejected(self):
        with pytest.raises(ValueError):
            tax.annual_tax(300_000, 0.04, -5)


class TestMillageSchedule:
    def test_typical_fallback_nets_to_dashboard_figure(self):
        """Parity guard: the JS dashboard uses 280 mills owner-occupied."""
        assert millage.TYPICAL_UNINCORPORATED.primary_mills() == 280.0

    def test_unknown_district_falls_back_not_raises(self):
        assert millage.get_schedule("nonexistent") is millage.TYPICAL_UNINCORPORATED
        assert millage.get_schedule(None) is millage.TYPICAL_UNINCORPORATED

    def test_district_1_total_matches_scac_report(self):
        """County base 85.6 + district 289.2 = 374.8 mills."""
        assert millage.SPARTANBURG_1.total_mills() == pytest.approx(374.8)

    def test_school_operating_is_exempt_school_bonds_are_not(self):
        by_name = {c.name: c for c in millage.SPARTANBURG_1.components}
        assert by_name["Current School (operating)"].exempt_for_primary is True
        assert by_name["School Bonds"].exempt_for_primary is False

    def test_exemption_removes_only_operating_millage(self):
        s = millage.SPARTANBURG_1
        assert s.exempt_mills() == pytest.approx(175.8)
        assert s.primary_mills() == pytest.approx(199.0)


class TestScenarios:
    def test_six_percent_always_costs_more_than_four(self):
        primary, non_primary, _ = tax.both_scenarios(300_000, "spartanburg_1")
        assert non_primary.annual_tax > primary.annual_tax

    def test_six_percent_penalty_compounds_ratio_and_millage(self):
        """1.5x the ratio AND more mills, so the gap is wider than 1.5x."""
        primary, non_primary, _ = tax.both_scenarios(300_000, "spartanburg_1")
        assert non_primary.annual_tax / primary.annual_tax > 1.5

    def test_monthly_is_annual_over_twelve(self):
        primary, _, _ = tax.both_scenarios(300_000)
        assert primary.monthly_tax == pytest.approx(primary.annual_tax / 12)

    def test_block_reports_both_and_the_delta(self):
        block = tax.tax_block(300_000, "spartanburg_1")
        oo = block["scenario_owner_occupied"]["annual_tax"]
        noo = block["scenario_non_owner_occupied"]["annual_tax"]
        assert block["delta_annual"] == pytest.approx(noo - oo, abs=0.01)

    def test_block_carries_provenance_and_a_caveat(self):
        block = tax.tax_block(300_000)
        wrapped = block["scenario_owner_occupied"]["monthly_tax_value"]
        assert wrapped["confidence"] == "estimated"
        assert wrapped["source_url"]
        assert "resets to the sale price" in block["caveat"]


class TestRatioResetNote:
    def test_six_percent_listing_moves_down_for_an_owner_occupier(self):
        note = tax.ratio_reset_note(0.06)
        assert note is not None and note.value == "down"

    def test_four_percent_listing_stays_flat(self):
        note = tax.ratio_reset_note(0.04)
        assert note is not None and note.value == "flat"

    def test_unknown_current_ratio_yields_no_claim(self):
        assert tax.ratio_reset_note(None) is None
