"""Near-term capital expenses — aging systems that will need money soon.

Why this module exists: a caveat that reads "roof age 17 yrs" and deducts nothing
lets a house with a five-figure bill attached to it score a perfect 100. That is
the tool lying by omission. An aging roof is not a note, it is a price adjustment.

So component age now does two things:
  1. produces a DOLLAR RANGE, labeled by magnitude (four figure / five figure)
  2. deducts points, scaled by how overdue the component is

The dollar ranges are ESTIMATES and always will be. Only an inspector's quote on a
specific house is real. The point is to know the order of magnitude before writing
an offer, not to predict the invoice.

Sources:
  Roof, SC-specific:  https://www.thisoldhouse.com/roofing/roof-replacement-cost-south-carolina
                      https://modernize.com/roof/cost-calculator/south-carolina
  HVAC, by home size: https://www.usatoday.com/story/money/home-services/hvac-replacement-cost/90313725007/
"""

from __future__ import annotations

from dataclasses import dataclass

ROOF_SRC = (
    "https://www.thisoldhouse.com/roofing/roof-replacement-cost-south-carolina"
)
ROOF_SRC_ALT = "https://modernize.com/roof/cost-calculator/south-carolina"
HVAC_SRC = (
    "https://www.usatoday.com/story/money/home-services/hvac-replacement-cost/"
    "90313725007/"
)

# Any single expense at or above this is called out as five-figure.
FIVE_FIGURE = 10_000
# Total near-term capex at or above this triggers a blocking task to get quotes.
QUOTE_THRESHOLD = 5_000

# Roof replacement, South Carolina, architectural asphalt, banded by living area.
# Statewide range is $6,064-$19,016 (This Old House); Modernize SC puts a standard
# 1,800-2,200 sqft home at $9,000-$22,500. Bands straddle both.
_ROOF_BANDS: tuple[tuple[float | None, float, float], ...] = (
    (1500, 6_000, 14_000),
    (2500, 8_000, 18_000),
    (None, 12_000, 24_000),
)
_ROOF_UNKNOWN_SQFT = (6_000, 19_000)

# Full HVAC replacement by home size, from the USA Today size table.
_HVAC_BANDS: tuple[tuple[float | None, float, float], ...] = (
    (1750, 6_000, 10_000),
    (2250, 7_000, 12_000),
    (2750, 8_000, 14_000),
    (None, 9_000, 16_000),
)
_HVAC_UNKNOWN_SQFT = (7_500, 14_500)


@dataclass(frozen=True)
class CapitalExpense:
    component: str
    reason: str
    low: float
    high: float
    points: int
    urgency: str  # "due" | "overdue"
    source_url: str

    @property
    def magnitude(self) -> str:
        return "five_figure" if self.high >= FIVE_FIGURE else "four_figure"

    @property
    def headline(self) -> str:
        return (
            f"{self.component}: ${self.low:,.0f}-${self.high:,.0f} "
            f"({self.magnitude.replace('_', ' ')})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "reason": self.reason,
            "estimate_low": self.low,
            "estimate_high": self.high,
            "magnitude": self.magnitude,
            "urgency": self.urgency,
            "points_deducted": self.points,
            "source_url": self.source_url,
            "confidence": "estimated",
        }


def _band(sqft: float | None, bands, fallback: tuple[float, float]) -> tuple[float, float]:
    if not sqft:
        return fallback
    for ceiling, low, high in bands:
        if ceiling is None or sqft < ceiling:
            return low, high
    return fallback


def roof_cost(sqft: float | None) -> tuple[float, float]:
    return _band(sqft, _ROOF_BANDS, _ROOF_UNKNOWN_SQFT)


def hvac_cost(sqft: float | None) -> tuple[float, float]:
    return _band(sqft, _HVAC_BANDS, _HVAC_UNKNOWN_SQFT)


def assess(
    roof_age_years: int | None,
    hvac_age_years: int | None,
    sqft: float | None,
    thresholds: dict[str, int],
    penalties: dict[str, int],
) -> list[CapitalExpense]:
    """Every component past its service life, with a cost range and a deduction."""
    found: list[CapitalExpense] = []

    if roof_age_years is not None:
        low, high = roof_cost(sqft)
        if roof_age_years >= thresholds["roof_overdue_age"]:
            found.append(
                CapitalExpense(
                    "Roof replacement",
                    f"Roof age {roof_age_years} yrs is past the "
                    f"{thresholds['roof_overdue_age']}-yr overdue mark — assume "
                    f"replacement is immediate, not eventual",
                    low, high, penalties["roof_overdue"], "overdue", ROOF_SRC,
                )
            )
        elif roof_age_years >= thresholds["roof_due_age"]:
            found.append(
                CapitalExpense(
                    "Roof replacement",
                    f"Roof age {roof_age_years} yrs is at or past the "
                    f"{thresholds['roof_due_age']}-yr mark — budget replacement "
                    f"within a few years, or negotiate a credit",
                    low, high, penalties["roof_due"], "due", ROOF_SRC,
                )
            )

    if hvac_age_years is not None:
        low, high = hvac_cost(sqft)
        if hvac_age_years >= thresholds["hvac_overdue_age"]:
            found.append(
                CapitalExpense(
                    "HVAC replacement",
                    f"HVAC age {hvac_age_years} yrs is past the "
                    f"{thresholds['hvac_overdue_age']}-yr overdue mark — expect "
                    f"failure, not service life",
                    low, high, penalties["hvac_overdue"], "overdue", HVAC_SRC,
                )
            )
        elif hvac_age_years >= thresholds["hvac_due_age"]:
            found.append(
                CapitalExpense(
                    "HVAC replacement",
                    f"HVAC age {hvac_age_years} yrs is at or past the "
                    f"{thresholds['hvac_due_age']}-yr mark — inspect and budget "
                    f"replacement",
                    low, high, penalties["hvac_due"], "due", HVAC_SRC,
                )
            )

    return found


def block(expenses: list[CapitalExpense]) -> dict[str, object]:
    """Capital-expense section for the output document."""
    total_low = sum(e.low for e in expenses)
    total_high = sum(e.high for e in expenses)
    return {
        "items": [e.to_dict() for e in expenses],
        "total_low": total_low,
        "total_high": total_high,
        "total_points_deducted": sum(e.points for e in expenses),
        "has_five_figure_item": any(e.magnitude == "five_figure" for e in expenses),
        "needs_contractor_quotes": total_high >= QUOTE_THRESHOLD,
        "sources": {"roof": ROOF_SRC, "roof_alt": ROOF_SRC_ALT, "hvac": HVAC_SRC},
        "note": (
            "Planning ranges only, banded by home size. An inspector's quote on the "
            "specific house is the real number. Use these to size an offer credit, "
            "not to predict an invoice."
        ),
    }
