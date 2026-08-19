"""SC property tax. Both assessment scenarios, always.

Two facts drive every calculation here:

1. SC assesses owner-occupied legal residences at 4% and other property at 6%.
2. Assessed value resets to the SALE PRICE at purchase.

Together those mean the seller's current tax bill is not predictive of yours in either
direction, which is exactly what listing sites display. Computing both scenarios and
showing them side by side is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass

from .millage import SC_DOR, MillageSchedule, get_schedule
from .provenance import Value, estimated

PRIMARY_RATIO = 0.04
NON_PRIMARY_RATIO = 0.06


@dataclass(frozen=True)
class TaxScenario:
    label: str
    ratio: float
    assessed_value: float
    applicable_mills: float
    annual_tax: float
    monthly_tax: float

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "assessment_ratio": self.ratio,
            "assessed_value": round(self.assessed_value, 2),
            "applicable_mills": self.applicable_mills,
            "annual_tax": round(self.annual_tax, 2),
            "monthly_tax": round(self.monthly_tax, 2),
        }


def assessed_value(price: float, ratio: float) -> float:
    """Assessed value = sale price x assessment ratio. Resets at purchase."""
    if price < 0:
        raise ValueError("price cannot be negative")
    return price * ratio


def annual_tax(price: float, ratio: float, mills: float) -> float:
    """annual tax = assessed value x (mills / 1000)."""
    if mills < 0:
        raise ValueError("mills cannot be negative")
    return assessed_value(price, ratio) * (mills / 1000.0)


def owner_occupied(price: float, schedule: MillageSchedule) -> TaxScenario:
    """4% ratio, school operating millage removed (school bonds still apply)."""
    mills = schedule.primary_mills()
    annual = annual_tax(price, PRIMARY_RATIO, mills)
    return TaxScenario(
        label="Owner-occupied legal residence (4%)",
        ratio=PRIMARY_RATIO,
        assessed_value=assessed_value(price, PRIMARY_RATIO),
        applicable_mills=mills,
        annual_tax=annual,
        monthly_tax=annual / 12.0,
    )


def non_owner_occupied(price: float, schedule: MillageSchedule) -> TaxScenario:
    """6% ratio, full millage including school operating."""
    mills = schedule.total_mills()
    annual = annual_tax(price, NON_PRIMARY_RATIO, mills)
    return TaxScenario(
        label="Non-owner-occupied (6%)",
        ratio=NON_PRIMARY_RATIO,
        assessed_value=assessed_value(price, NON_PRIMARY_RATIO),
        applicable_mills=mills,
        annual_tax=annual,
        monthly_tax=annual / 12.0,
    )


def both_scenarios(
    price: float, district_key: str | None = None
) -> tuple[TaxScenario, TaxScenario, MillageSchedule]:
    schedule = get_schedule(district_key)
    return owner_occupied(price, schedule), non_owner_occupied(price, schedule), schedule


def tax_block(price: float, district_key: str | None = None) -> dict[str, object]:
    """Provenance-wrapped tax section for the output document."""
    primary, non_primary, schedule = both_scenarios(price, district_key)

    def wrap(scenario: TaxScenario) -> dict[str, object]:
        body = scenario.to_dict()
        body["monthly_tax_value"] = estimated(
            round(scenario.monthly_tax, 2),
            source_url=schedule.source_url,
            note=schedule.note,
        ).to_dict()
        return body

    return {
        "district": schedule.district,
        "millage": {
            "total_mills": schedule.total_mills(),
            "primary_mills": schedule.primary_mills(),
            "exempt_mills": schedule.exempt_mills(),
            "breakdown": schedule.breakdown(),
            "source_url": schedule.source_url,
        },
        "scenario_owner_occupied": wrap(primary),
        "scenario_non_owner_occupied": wrap(non_primary),
        "delta_annual": round(non_primary.annual_tax - primary.annual_tax, 2),
        "rules_source": SC_DOR,
        "caveat": (
            "Assessed value resets to the sale price at purchase. The seller's current "
            "bill is not your future bill. File the legal-residence classification with "
            "the County Auditor by Jan 15 of the tax year after closing."
        ),
    }


def ratio_reset_note(current_ratio: float | None) -> Value | None:
    """Flag the direction a listing's tax line will move for an owner-occupier."""
    if current_ratio is None:
        return None
    if abs(current_ratio - NON_PRIMARY_RATIO) < 1e-9:
        return estimated(
            "down",
            source_url=SC_DOR,
            note=(
                "Currently taxed at 6% (rental or second home). The listed tax figure "
                "is inflated relative to what an owner-occupier would pay."
            ),
        )
    return estimated(
        "flat",
        source_url=SC_DOR,
        note="Already assessed at the 4% owner-occupied ratio.",
    )
