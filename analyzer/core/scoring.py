"""The score. Pure function, no network, no model, fully reproducible.

Three tiers, and the distinction matters:

  hard fails  -> score 0, verdict PASS. Disqualifying at any price.
  deductions  -> points off 100. A house can fail these and still be a candidate.
  caveats     -> zero points. Attention, not penalty.

A hard fail that could not be EVALUATED (missing input, dead source) never silently
becomes a pass. The verdict is capped at WATCH and the reason is stated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .profile import BuyerProfile

VERDICT_TAKE = "TAKE"
VERDICT_WATCH = "WATCH"
VERDICT_PASS = "PASS"


@dataclass
class PropertyFacts:
    """Everything scoring needs. Sources populate this; scoring never fetches.

    `None` means unknown, which is different from a failing value and is treated
    differently.
    """

    price: float
    sqft: float | None = None
    beds: int | None = None
    baths: float | None = None
    garage_spaces: int | None = None
    hoa_monthly: float | None = 0.0
    year_built: int | None = None
    roof_age_years: int | None = None
    hvac_age_years: int | None = None
    # hard-fail inputs — None means "could not evaluate"
    flood_zone: str | None = None
    water_sewer: str | None = None
    commute_min: float | None = None
    fiber_available: bool | None = None


@dataclass
class ScoreResult:
    value: int
    verdict: str
    hard_fails: list[str] = field(default_factory=list)
    unevaluated_hard_fails: list[str] = field(default_factory=list)
    deductions: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def total_deducted(self) -> int:
        return sum(d["points"] for d in self.deductions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "verdict": self.verdict,
            "hard_fails": self.hard_fails,
            "unevaluated_hard_fails": self.unevaluated_hard_fails,
            "deductions": self.deductions,
            "total_deducted": self.total_deducted,
            "caveats": self.caveats,
        }


def _is_flood_zone(zone: str | None, excluded: tuple[str, ...]) -> bool:
    if not zone:
        return False
    return zone.strip().upper() in {z.upper() for z in excluded}


def score(facts: PropertyFacts, profile: BuyerProfile, current_year: int) -> ScoreResult:
    result = ScoreResult(value=100, verdict=VERDICT_TAKE)

    # -- hard fails ----------------------------------------------------------

    if facts.flood_zone is None:
        result.unevaluated_hard_fails.append(
            "Flood zone unknown — cannot confirm the property is outside a Special "
            "Flood Hazard Area"
        )
    elif _is_flood_zone(facts.flood_zone, profile.exclude_flood_zones):
        result.hard_fails.append(
            f"FEMA flood zone {facts.flood_zone} is a Special Flood Hazard Area"
        )

    if profile.require_public_water_sewer:
        if facts.water_sewer is None:
            result.unevaluated_hard_fails.append(
                "Water/sewer type unknown — cannot confirm public utilities"
            )
        elif facts.water_sewer.strip().lower() not in ("public", "city", "municipal"):
            result.hard_fails.append(
                f"Water/sewer is '{facts.water_sewer}' — public water and sewer required"
            )

    if facts.commute_min is None:
        result.unevaluated_hard_fails.append(
            "Commute unknown — cannot evaluate the 20-minute limit"
        )
    elif facts.commute_min > profile.max_commute_min:
        result.hard_fails.append(
            f"Rush-hour commute {facts.commute_min:.1f} min exceeds the "
            f"{profile.max_commute_min:.0f}-min limit"
        )

    if result.hard_fails:
        result.value = 0
        result.verdict = VERDICT_PASS
        _add_caveats(result, facts, profile, current_year)
        return result

    # -- deductions ----------------------------------------------------------

    p = profile.penalties

    def deduct(key: str, reason: str) -> None:
        points = p.get(key, 0)
        if points:
            result.deductions.append({"reason": reason, "points": points, "rule": key})

    hoa = facts.hoa_monthly or 0.0
    if hoa > profile.max_hoa_monthly:
        deduct(
            "hoa_over_max",
            f"HOA ${hoa:,.0f}/mo exceeds the ${profile.max_hoa_monthly:,.0f}/mo "
            f"preference — still a candidate, but it is a permanent cost",
        )

    if facts.beds is not None and facts.beds < profile.min_beds:
        deduct("beds_under", f"{facts.beds} bedrooms, want {profile.min_beds}")
    if facts.sqft is not None and facts.sqft < profile.min_sqft:
        deduct(
            "sqft_under",
            f"{facts.sqft:,.0f} sqft below the {profile.min_sqft:,} floor",
        )
    if profile.require_fiber and facts.fiber_available is False:
        deduct("no_fiber", "No fiber reported — remote work depends on it")
    if (
        facts.garage_spaces is not None
        and facts.garage_spaces < profile.min_garage_spaces
    ):
        deduct(
            "garage_under",
            f"{facts.garage_spaces}-car garage, want {profile.min_garage_spaces}",
        )
    if facts.baths is not None and facts.baths < profile.min_baths:
        deduct("baths_under", f"{facts.baths:g} baths, want {profile.min_baths}")

    result.value = max(0, min(100, 100 - result.total_deducted))

    # -- verdict -------------------------------------------------------------

    if result.value >= profile.verdict_take_min:
        result.verdict = VERDICT_TAKE
    elif result.value >= profile.verdict_watch_min:
        result.verdict = VERDICT_WATCH
    else:
        result.verdict = VERDICT_PASS

    # An unverified hard fail can never present as a clean TAKE.
    if result.unevaluated_hard_fails and result.verdict == VERDICT_TAKE:
        result.verdict = VERDICT_WATCH

    _add_caveats(result, facts, profile, current_year)
    return result


def _add_caveats(
    result: ScoreResult,
    facts: PropertyFacts,
    profile: BuyerProfile,
    current_year: int,
) -> None:
    """Flags that inform but never deduct."""
    if facts.year_built and facts.year_built < profile.preferred_year_built_min:
        result.caveats.append(
            f"Built {facts.year_built}, before {profile.preferred_year_built_min} — "
            f"noted as a caveat only, not a deduction. Expect older systems, wiring, "
            f"and insulation."
        )
    if (
        facts.roof_age_years is not None
        and facts.roof_age_years >= profile.roof_age_caveat_years
    ):
        result.caveats.append(
            f"Roof age {facts.roof_age_years} yrs — expect replacement; escrow or credit"
        )
    if (
        facts.hvac_age_years is not None
        and facts.hvac_age_years >= profile.hvac_age_caveat_years
    ):
        result.caveats.append(
            f"HVAC age {facts.hvac_age_years} yrs — inspect and budget replacement"
        )

    over = profile.target_price * (1 + profile.max_price_over_target_pct)
    if facts.price > over:
        pct = (facts.price / profile.target_price - 1) * 100
        result.caveats.append(
            f"Price ${facts.price:,.0f} is {pct:.0f}% above the "
            f"${profile.target_price:,.0f} target — negotiate"
        )

    if facts.sqft:
        ppsf = facts.price / facts.sqft
        if ppsf > profile.max_price_per_sqft:
            result.caveats.append(
                f"${ppsf:,.0f}/sqft is above ${profile.max_price_per_sqft:,.0f} — "
                f"check comps in the same ZIP"
            )

    if facts.fiber_available is None:
        result.caveats.append(
            "Broadband unknown — FCC data is census-block precision at best; "
            "call the ISP with the exact address"
        )
