"""Scoring rules. The behaviour that matters most is what happens when a source fails."""

from __future__ import annotations

import pytest

from analyzer.core import scoring
from analyzer.core.profile import load_profile
from analyzer.core.scoring import PropertyFacts


@pytest.fixture
def profile():
    return load_profile()


def clean(**overrides) -> PropertyFacts:
    """A property that passes everything, so each test changes exactly one thing."""
    base = dict(
        price=312_000,
        sqft=1780,
        beds=3,
        baths=3,
        garage_spaces=2,
        hoa_monthly=0.0,
        year_built=2016,
        roof_age_years=6,
        hvac_age_years=6,
        flood_zone="X",
        water_sewer="public",
        commute_min=16.4,
        fiber_available=True,
    )
    base.update(overrides)
    return PropertyFacts(**base)


class TestBaseline:
    def test_clean_property_scores_100_and_takes(self, profile):
        r = scoring.score(clean(), profile, 2026)
        assert r.value == 100
        assert r.verdict == scoring.VERDICT_TAKE
        assert r.deductions == []
        assert r.hard_fails == []


class TestHardFails:
    @pytest.mark.parametrize("zone", ["A", "AE", "AO", "VE", "ae"])
    def test_sfha_zones_fail(self, profile, zone):
        r = scoring.score(clean(flood_zone=zone), profile, 2026)
        assert r.value == 0
        assert r.verdict == scoring.VERDICT_PASS
        assert len(r.hard_fails) == 1

    @pytest.mark.parametrize("zone", ["X", "X500", "C"])
    def test_non_sfha_zones_pass(self, profile, zone):
        assert scoring.score(clean(flood_zone=zone), profile, 2026).hard_fails == []

    @pytest.mark.parametrize("util", ["well", "septic", "well/septic", "SEPTIC"])
    def test_non_public_utilities_fail(self, profile, util):
        r = scoring.score(clean(water_sewer=util), profile, 2026)
        assert r.value == 0 and r.verdict == scoring.VERDICT_PASS

    @pytest.mark.parametrize("util", ["public", "City", "MUNICIPAL"])
    def test_public_utilities_pass(self, profile, util):
        assert scoring.score(clean(water_sewer=util), profile, 2026).hard_fails == []

    def test_commute_over_limit_fails(self, profile):
        r = scoring.score(clean(commute_min=26.3), profile, 2026)
        assert r.value == 0 and r.verdict == scoring.VERDICT_PASS

    def test_commute_exactly_at_limit_passes(self, profile):
        """20.0 is not "over 20"."""
        assert scoring.score(clean(commute_min=20.0), profile, 2026).hard_fails == []

    def test_commute_just_over_fails(self, profile):
        assert scoring.score(clean(commute_min=20.1), profile, 2026).hard_fails

    def test_hard_fail_short_circuits_deductions(self, profile):
        """Score is 0, not 0-minus-deductions. No partial credit either way."""
        r = scoring.score(clean(flood_zone="AE", sqft=900, beds=1), profile, 2026)
        assert r.value == 0
        assert r.deductions == []

    def test_multiple_hard_fails_all_reported(self, profile):
        r = scoring.score(
            clean(flood_zone="AE", water_sewer="septic", commute_min=30),
            profile,
            2026,
        )
        assert len(r.hard_fails) == 3


class TestUnevaluatedHardFails:
    """The safety property: missing data must never look like a clean pass."""

    @pytest.mark.parametrize(
        "field", ["flood_zone", "water_sewer", "commute_min"]
    )
    def test_unknown_hard_fail_input_caps_verdict_at_watch(self, profile, field):
        r = scoring.score(clean(**{field: None}), profile, 2026)
        assert r.verdict == scoring.VERDICT_WATCH
        assert len(r.unevaluated_hard_fails) == 1

    def test_unknown_pins_the_score_to_fifty(self, profile):
        """Unknown is not a failure and not a pass — it is an unresolved question.

        Pinning to 50 lands it squarely in WATCH: worth following up, not worth an
        offer, and visibly distinct from a house that earned its score.
        """
        r = scoring.score(clean(flood_zone=None), profile, 2026)
        assert r.value == 50
        assert r.score_pinned is True

    def test_pin_only_lowers_never_raises(self, profile):
        """A dead source must not flatter a genuinely weak house up to 50."""
        r = scoring.score(
            clean(flood_zone=None, sqft=1200, beds=2, baths=2, garage_spaces=1),
            profile,
            2026,
        )
        assert r.total_deducted == 58
        assert r.value == 42
        assert r.verdict == scoring.VERDICT_PASS

    def test_fully_evaluated_property_is_not_pinned(self, profile):
        assert scoring.score(clean(), profile, 2026).score_pinned is False

    def test_unknown_is_not_recorded_as_a_hard_fail(self, profile):
        r = scoring.score(clean(water_sewer=None), profile, 2026)
        assert r.hard_fails == []

    def test_all_unknown_still_watch_never_take(self, profile):
        r = scoring.score(
            clean(flood_zone=None, water_sewer=None, commute_min=None), profile, 2026
        )
        assert r.verdict == scoring.VERDICT_WATCH
        assert len(r.unevaluated_hard_fails) == 3

    def test_a_real_hard_fail_still_wins_over_an_unknown(self, profile):
        r = scoring.score(clean(flood_zone="AE", commute_min=None), profile, 2026)
        assert r.verdict == scoring.VERDICT_PASS


class TestDeductions:
    @pytest.mark.parametrize(
        "overrides,points",
        [
            ({"hoa_monthly": 150.0}, 25),
            ({"beds": 2}, 20),
            ({"sqft": 1200}, 20),
            ({"fiber_available": False}, 15),
            ({"garage_spaces": 1}, 10),
            ({"baths": 2}, 8),
        ],
    )
    def test_each_rule_deducts_its_weight(self, profile, overrides, points):
        r = scoring.score(clean(**overrides), profile, 2026)
        assert r.value == 100 - points
        assert r.total_deducted == points

    def test_hoa_at_the_ceiling_is_not_penalised(self, profile):
        """The rule is "over $100", so $100 exactly is fine."""
        assert scoring.score(clean(hoa_monthly=100.0), profile, 2026).value == 100

    def test_hoa_is_demoted_from_hard_fail_to_penalty(self, profile):
        """An expensive HOA is a cost, not a disqualification."""
        r = scoring.score(clean(hoa_monthly=400.0), profile, 2026)
        assert r.hard_fails == []
        assert r.value == 75

    def test_sqft_at_the_floor_is_not_penalised(self, profile):
        assert scoring.score(clean(sqft=1400), profile, 2026).value == 100

    def test_deductions_stack(self, profile):
        r = scoring.score(
            clean(sqft=1280, baths=2, garage_spaces=1, fiber_available=False),
            profile,
            2026,
        )
        assert r.total_deducted == 20 + 8 + 10 + 15
        assert r.value == 47

    def test_zero_is_reserved_for_hard_fails(self, profile):
        """Every deduction at once totals 98, so deductions alone cannot reach 0.

        That is deliberate: a score of 0 means "disqualified", not "scored badly".
        A reader seeing 0 can trust that a hard fail fired.
        """
        r = scoring.score(
            clean(
                hoa_monthly=500.0,
                beds=1,
                sqft=600,
                fiber_available=False,
                garage_spaces=0,
                baths=1,
            ),
            profile,
            2026,
        )
        assert r.total_deducted == 98
        assert r.value == 2
        assert r.verdict == scoring.VERDICT_PASS
        assert r.hard_fails == []

    def test_score_clamps_and_never_goes_negative(self, profile):
        """Guard for future weight changes that could sum past 100."""
        inflated = dict(profile.penalties)
        inflated["sqft_under"] = 500
        object.__setattr__(profile, "penalties", inflated)
        r = scoring.score(clean(sqft=600), profile, 2026)
        assert r.value == 0

    def test_unknown_optional_field_is_not_a_deduction(self, profile):
        """Missing sqft is unknown, not "small"."""
        assert scoring.score(clean(sqft=None), profile, 2026).value == 100

    def test_unknown_fiber_does_not_deduct(self, profile):
        r = scoring.score(clean(fiber_available=None), profile, 2026)
        assert r.total_deducted == 0

    def test_every_deduction_names_its_rule(self, profile):
        r = scoring.score(clean(sqft=1200, baths=2), profile, 2026)
        assert all(d["rule"] and d["reason"] for d in r.deductions)


class TestVerdictBands:
    @pytest.mark.parametrize(
        "overrides,verdict",
        [
            ({}, scoring.VERDICT_TAKE),                       # 100
            ({"hoa_monthly": 150.0}, scoring.VERDICT_TAKE),   # 75, at the boundary
            ({"sqft": 1200, "beds": 2}, scoring.VERDICT_WATCH),  # 60
            ({"sqft": 1200, "beds": 2, "baths": 2}, scoring.VERDICT_WATCH),  # 52
            ({"sqft": 1200, "beds": 2, "fiber_available": False}, scoring.VERDICT_WATCH),  # 45, floor
            ({"sqft": 1200, "beds": 2, "fiber_available": False, "baths": 2}, scoring.VERDICT_PASS),  # 37
        ],
    )
    def test_bands(self, profile, overrides, verdict):
        assert scoring.score(clean(**overrides), profile, 2026).verdict == verdict

    def test_watch_floor_is_forty_five(self, profile):
        """Lowered from 50 so a stack of soft misses still earns a showing."""
        assert profile.verdict_watch_min == 45
        r = scoring.score(
            clean(sqft=1280, baths=2, garage_spaces=1, fiber_available=False),
            profile,
            2026,
        )
        assert r.value == 47
        assert r.verdict == scoring.VERDICT_WATCH


class TestCaveats:
    def test_old_house_is_a_caveat_not_a_deduction(self, profile):
        """Explicit decision: pre-2000 is noted, never excluded."""
        r = scoring.score(clean(year_built=1978), profile, 2026)
        assert r.value == 100
        assert r.verdict == scoring.VERDICT_TAKE
        assert any("1978" in c for c in r.caveats)

    def test_unknown_component_ages_on_an_old_house_are_flagged(self, profile):
        """Missing ages mean the capex tier could not run, so the score is optimistic."""
        r = scoring.score(
            clean(year_built=1985, roof_age_years=None, hvac_age_years=None),
            profile,
            2026,
        )
        assert any("optimistic" in c for c in r.caveats)

    def test_unknown_ages_on_a_new_house_are_not_flagged(self, profile):
        r = scoring.score(
            clean(year_built=2024, roof_age_years=None, hvac_age_years=None),
            profile,
            2026,
        )
        assert not any("optimistic" in c for c in r.caveats)

    def test_price_over_target_flags(self, profile):
        r = scoring.score(clean(price=400_000), profile, 2026)
        assert r.value == 100
        assert any("above the" in c for c in r.caveats)

    def test_price_within_ten_percent_does_not_flag(self, profile):
        r = scoring.score(clean(price=330_000), profile, 2026)
        assert not any("above the" in c for c in r.caveats)

    def test_price_per_sqft_flags(self, profile):
        r = scoring.score(clean(price=400_000, sqft=1500), profile, 2026)
        assert any("/sqft" in c for c in r.caveats)

    def test_unknown_broadband_produces_a_caveat(self, profile):
        r = scoring.score(clean(fiber_available=None), profile, 2026)
        assert any("Broadband unknown" in c for c in r.caveats)

    def test_caveats_are_attached_even_to_hard_fails(self, profile):
        """A rejected house should still explain itself fully."""
        r = scoring.score(clean(flood_zone="AE", year_built=1965), profile, 2026)
        assert r.value == 0
        assert r.caveats
