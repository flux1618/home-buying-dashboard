"""Assemble the output document. Pure — takes facts, returns the result dict.

Network stations (geocode, county GIS, FEMA, OSRM, FCC) populate `PropertyFacts` and
`Degradation` objects and hand them here. This module never fetches anything, which is
why the whole analysis is unit-testable without a single HTTP mock.
"""

from __future__ import annotations

from typing import Any

from . import cost, maintenance, scoring, tax
from .profile import BuyerProfile
from .provenance import Degradation, now_iso
from .scoring import PropertyFacts

ENGINE_VERSION = "0.1.0"


def verification_tasks(
    facts: PropertyFacts, result: scoring.ScoreResult, profile: BuyerProfile
) -> list[dict[str, Any]]:
    """Tasks a human must do. Blocking ones gate an offer."""
    tasks: list[dict[str, Any]] = []

    def add(task: str, blocking: bool, reason: str | None = None) -> None:
        entry: dict[str, Any] = {"task": task, "blocking": blocking}
        if reason:
            entry["reason"] = reason
        tasks.append(entry)

    # Broadband verification is permanent, not conditional. FCC "available" means the
    # provider claims it can install within 10 business days, not that service exists.
    add(
        "Call the ISP with the exact street address to confirm fiber serviceability",
        blocking=True,
        reason="FCC data is census-block precision and provider-reported",
    )

    if facts.commute_min is not None:
        margin = abs(facts.commute_min - profile.max_commute_min)
        if margin <= 2:
            add(
                f"Drive the route at {profile.primary_anchor.arrival_window} before "
                f"trusting the {facts.commute_min:.1f} min estimate",
                blocking=True,
                reason=f"within {margin:.1f} min of the hard limit",
            )

    for item in result.unevaluated_hard_fails:
        add(f"Resolve before offer: {item}", blocking=True)

    add("Get an actual insurance quote before the offer", blocking=False,
        reason="SC statewide average is a placeholder")
    add("Pull parcel tax history and exact tax district at Spartanburg County GIS",
        blocking=False, reason="millage is a typical estimate until resolved")
    add("Confirm the legal-residence 4% classification filing deadline with the "
        "County Auditor (Jan 15 of the tax year after closing)", blocking=False)

    if facts.roof_age_years is None or facts.hvac_age_years is None:
        add("Get roof and HVAC ages from the seller's disclosure", blocking=False)

    return tasks


def analyze(
    facts: PropertyFacts,
    profile: BuyerProfile,
    current_year: int,
    address: str = "",
    degradations: list[Degradation] | None = None,
) -> dict[str, Any]:
    """Full result document. Deterministic given the same inputs."""
    degradations = degradations or []

    costs = cost.compute(
        profile=profile,
        price=facts.price,
        sqft=facts.sqft,
        year_built=facts.year_built,
        hoa_monthly=facts.hoa_monthly or 0.0,
        current_year=current_year,
    )
    result = scoring.score(facts, profile, current_year)

    return {
        "engine_version": ENGINE_VERSION,
        "analyzed_at": now_iso(),
        "profile": profile.name,
        "input": {
            "address": address,
            "price": facts.price,
            "sqft": facts.sqft,
            "beds": facts.beds,
            "baths": facts.baths,
            "year_built": facts.year_built,
            "hoa_monthly": facts.hoa_monthly,
            "water_sewer": facts.water_sewer,
            "flood_zone": facts.flood_zone,
            "commute_min": facts.commute_min,
            "fiber_available": facts.fiber_available,
        },
        "tax": tax.tax_block(facts.price, profile.millage_district),
        "maintenance_reserve": maintenance.reserve_block(
            facts.price, facts.sqft, facts.year_built, current_year
        ),
        "cost": costs.to_dict(),
        "commute": {
            "anchor": profile.primary_anchor.label,
            "anchor_address": profile.primary_anchor.address,
            "arrival_window": profile.primary_anchor.arrival_window,
            "rush_hour_min": facts.commute_min,
            "limit_min": profile.max_commute_min,
            "source_url": profile.primary_anchor.source_url,
        },
        "score": result.to_dict(),
        "verification_tasks": verification_tasks(facts, result, profile),
        "degraded_sources": [d.to_dict() for d in degradations],
    }
