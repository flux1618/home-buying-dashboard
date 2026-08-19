"""Monthly cost of ownership. PITI, DTI, cash to close, and full TCO range.

PITI and front-end DTI intentionally match the formulas already in `app.js` so the
Python and JavaScript paths cannot drift. Maintenance reserve is deliberately EXCLUDED
from the DTI figure (lenders don't count it) but INCLUDED in the true monthly range,
because the household still has to pay it.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import maintenance, tax
from .profile import BuyerProfile

PMMS = "https://www.freddiemac.com/pmms"
INSURANCE_SRC = "https://www.lendingtree.com/insurance/state-of-home-insurance/"
DEED_FEE_SRC = "https://dor.sc.gov/tax/deed"

SC_DEED_FEE_PER_500 = 1.85
BUYER_CLOSING_PCT = 0.03


def monthly_payment(principal: float, annual_rate: float, term_months: int) -> float:
    """Standard amortized payment. Handles the 0% edge case."""
    if principal <= 0:
        return 0.0
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    r = annual_rate / 12.0
    if r == 0:
        return principal / term_months
    factor = (1 + r) ** term_months
    return principal * (r * factor) / (factor - 1)


def deed_recording_fee(price: float) -> float:
    """SC deed recording fee, $1.85 per $500 of consideration."""
    return (price / 500.0) * SC_DEED_FEE_PER_500


@dataclass(frozen=True)
class CostBreakdown:
    price: float
    loan_amount: float
    principal_interest: float
    monthly_tax: float
    monthly_insurance: float
    monthly_hoa: float
    piti: float
    front_end_dti: float
    dti_within_target: bool
    reserve_low: float
    reserve_high: float
    true_monthly_low: float
    true_monthly_high: float
    cash_to_close: float

    def to_dict(self) -> dict[str, object]:
        return {
            "loan_amount": round(self.loan_amount, 2),
            "principal_interest": round(self.principal_interest, 2),
            "monthly_tax": round(self.monthly_tax, 2),
            "monthly_insurance": round(self.monthly_insurance, 2),
            "monthly_hoa": round(self.monthly_hoa, 2),
            "piti": round(self.piti, 2),
            "front_end_dti": round(self.front_end_dti, 4),
            "dti_within_target": self.dti_within_target,
            "maintenance_reserve_low": round(self.reserve_low, 2),
            "maintenance_reserve_high": round(self.reserve_high, 2),
            "true_monthly_low": round(self.true_monthly_low, 2),
            "true_monthly_high": round(self.true_monthly_high, 2),
            "cash_to_close": round(self.cash_to_close, 2),
            "sources": {
                "rate": PMMS,
                "insurance": INSURANCE_SRC,
                "deed_fee": DEED_FEE_SRC,
            },
            "note": (
                "PITI and DTI exclude the maintenance reserve, matching how a lender "
                "underwrites. true_monthly_* includes it, because the household still "
                "pays it."
            ),
        }


def compute(
    profile: BuyerProfile,
    price: float,
    sqft: float | None,
    year_built: int | None,
    hoa_monthly: float,
    current_year: int,
    owner_occupied: bool = True,
) -> CostBreakdown:
    loan = max(0.0, price - profile.down_payment)
    pi = monthly_payment(loan, profile.mortgage_rate, profile.loan_term_months)

    primary, non_primary, _ = tax.both_scenarios(price, profile.millage_district)
    monthly_tax = (primary if owner_occupied else non_primary).monthly_tax

    monthly_ins = profile.annual_insurance / 12.0
    piti = pi + monthly_tax + monthly_ins + hoa_monthly

    dti = piti / profile.monthly_income if profile.monthly_income else 0.0

    reserves = maintenance.all_methods(price, sqft, year_built, current_year)
    monthlies = [r.monthly for r in reserves]
    low, high = min(monthlies), max(monthlies)

    closing = price * BUYER_CLOSING_PCT + deed_recording_fee(price)

    return CostBreakdown(
        price=price,
        loan_amount=loan,
        principal_interest=pi,
        monthly_tax=monthly_tax,
        monthly_insurance=monthly_ins,
        monthly_hoa=hoa_monthly,
        piti=piti,
        front_end_dti=dti,
        dti_within_target=dti <= profile.target_front_end_dti,
        reserve_low=low,
        reserve_high=high,
        true_monthly_low=piti + low,
        true_monthly_high=piti + high,
        cash_to_close=profile.down_payment + closing,
    )
