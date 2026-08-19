"""Buyer profile: constraints as configuration, not as constants in code.

Parameterizing the household's rules is what turns "a tool for one person" into a
decision engine that can be pointed at a different buyer. Adding a persona is a TOML
file, not a code change.

TOML is read with stdlib `tomllib` (3.11+), so `core/` stays dependency-free per ADR 0002.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Two ways to find the rulebook, in priority order.
#
# `HBA_PROFILE` exists because the path-relative default only works when the code is run
# from a source checkout. Once the package is pip-installed — which is exactly what the
# container does — `parents[2]` resolves into site-packages, where no profile lives. The
# env var lets a deployment say where the config is instead of inferring it from where the
# code happens to be sitting.
#
# Note this is stdlib `os` only, so ADR 0002's purity rule still holds.
DEFAULT_PROFILE_PATH = Path(
    os.environ.get("HBA_PROFILE")
    or Path(__file__).resolve().parents[2] / "buyer_profile.toml"
)


@dataclass(frozen=True)
class Anchor:
    """A commute destination. A list of these, so a second campus is config."""

    label: str
    address: str
    lat: float
    lon: float
    arrival_window: str
    source_url: str


@dataclass(frozen=True)
class BuyerProfile:
    name: str
    # finance
    gross_annual_income: float
    monthly_non_housing: float
    down_payment: float
    target_front_end_dti: float
    mortgage_rate: float
    loan_term_months: int
    annual_insurance: float
    target_price: float
    # hard fails
    max_commute_min: float
    require_public_water_sewer: bool
    exclude_flood_zones: tuple[str, ...]
    # scored preferences -> penalty points
    min_beds: int
    min_baths: int
    min_sqft: int
    min_garage_spaces: int
    require_fiber: bool
    max_hoa_monthly: float
    penalties: dict[str, int]
    # near-term capital expenses — age thresholds and their deductions
    capex_thresholds: dict[str, int]
    capex_penalties: dict[str, int]
    # caveats
    preferred_year_built_min: int
    max_price_over_target_pct: float
    max_price_per_sqft: float
    # verdict bands
    verdict_take_min: int
    verdict_watch_min: int
    # score assigned when a hard fail could not be evaluated
    unevaluated_score: int
    anchors: tuple[Anchor, ...] = field(default_factory=tuple)
    millage_district: str | None = None

    @property
    def monthly_income(self) -> float:
        return self.gross_annual_income / 12.0

    @property
    def primary_anchor(self) -> Anchor:
        if not self.anchors:
            raise ValueError("profile has no commute anchors")
        return self.anchors[0]


def load_profile(path: Path | str | None = None) -> BuyerProfile:
    path = Path(path) if path else DEFAULT_PROFILE_PATH
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    fin = raw["finance"]
    hard = raw["hard_fails"]
    pref = raw["preferences"]
    capex = raw["capital_expenses"]
    cav = raw["caveats"]
    verdict = raw["verdict"]

    anchors = tuple(
        Anchor(
            label=a["label"],
            address=a["address"],
            lat=a["lat"],
            lon=a["lon"],
            arrival_window=a["arrival_window"],
            source_url=a["source_url"],
        )
        for a in raw.get("anchor", [])
    )

    return BuyerProfile(
        name=raw["name"],
        gross_annual_income=fin["gross_annual_income"],
        monthly_non_housing=fin["monthly_non_housing"],
        down_payment=fin["down_payment"],
        target_front_end_dti=fin["target_front_end_dti"],
        mortgage_rate=fin["mortgage_rate"],
        loan_term_months=fin["loan_term_months"],
        annual_insurance=fin["annual_insurance"],
        target_price=fin["target_price"],
        max_commute_min=hard["max_commute_min"],
        require_public_water_sewer=hard["require_public_water_sewer"],
        exclude_flood_zones=tuple(hard["exclude_flood_zones"]),
        min_beds=pref["min_beds"],
        min_baths=pref["min_baths"],
        min_sqft=pref["min_sqft"],
        min_garage_spaces=pref["min_garage_spaces"],
        require_fiber=pref["require_fiber"],
        max_hoa_monthly=pref["max_hoa_monthly"],
        penalties=dict(pref["penalties"]),
        capex_thresholds={k: v for k, v in capex.items() if k.endswith("_age")},
        capex_penalties=dict(capex["penalties"]),
        preferred_year_built_min=cav["preferred_year_built_min"],
        max_price_over_target_pct=cav["max_price_over_target_pct"],
        max_price_per_sqft=cav["max_price_per_sqft"],
        verdict_take_min=verdict["take_min"],
        verdict_watch_min=verdict["watch_min"],
        unevaluated_score=verdict["unevaluated_score"],
        anchors=anchors,
        millage_district=raw.get("tax", {}).get("millage_district"),
    )
