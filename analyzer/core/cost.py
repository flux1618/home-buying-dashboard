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


@dataclass(frozen=True)
class MaxPriceSolution:
    """The answer to "what can I actually offer," which is two numbers, not one."""

    dti_ceiling: float
    feasible: bool
    lender_max_price: float | None
    household_max_price: float | None
    floor_price: float
    floor_dti: float
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "dti_ceiling": round(self.dti_ceiling, 4),
            "feasible": self.feasible,
            "lender_max_price": (
                None if self.lender_max_price is None else round(self.lender_max_price)
            ),
            "household_max_price": (
                None
                if self.household_max_price is None
                else round(self.household_max_price)
            ),
            "floor_price": round(self.floor_price),
            "floor_dti": round(self.floor_dti, 4),
            "notes": list(self.notes),
            "note": (
                "lender_max_price uses PITI, which is what a lender underwrites. "
                "household_max_price adds the maintenance reserve, which the household "
                "pays and the lender ignores. The gap between them is not an error."
            ),
        }


# A dollar is finer than any input this is built from, and it makes the bisection
# terminate in about 25 iterations for any price a house in this county will have.
_SOLVER_TOLERANCE = 1.0
_SOLVER_CEILING = 5_000_000.0


def solve_max_price(
    profile: BuyerProfile,
    *,
    dti_ceiling: float | None = None,
    sqft: float | None = None,
    year_built: int | None = None,
    hoa_monthly: float = 0.0,
    current_year: int,
    owner_occupied: bool = True,
) -> MaxPriceSolution:
    """Invert `compute`: the highest price whose DTI stays at or under a ceiling.

    `compute` answers "given this house, can I afford it." Shopping asks the reverse:
    "given what I can afford, how much house is that." Doing that by hand means guessing a
    price, reading the DTI, and adjusting -- a person running bisection badly.

    Solved numerically rather than algebraically, on purpose. PITI is monotonic in price
    but not linear in it: the SC deed fee steps per $500, the assessment ratio and millage
    live in `tax`, and the maintenance reserve has three methods with different price
    sensitivities. A closed form would have to restate all of that, and would go stale the
    first time a millage rate or a reserve method changed. Bisecting over the real
    `compute` cannot drift from it, because it *is* it.

    Two answers come back because there are two honest ones. A lender underwrites PITI, so
    `lender_max_price` is the number an approval letter shows. The household also pays a
    maintenance reserve, so `household_max_price` is the price that keeps the same share of
    gross income going out the door. Reporting only the first would repeat the mistake this
    project exists to avoid: quote the optimistic figure, let the difference surface later.
    """
    ceiling = profile.target_front_end_dti if dti_ceiling is None else dti_ceiling
    if ceiling <= 0:
        raise ValueError("dti_ceiling must be positive")
    if not profile.monthly_income:
        raise ValueError("cannot solve for a DTI ceiling without an income")

    def at(price: float) -> CostBreakdown:
        return compute(
            profile,
            price,
            sqft,
            year_built,
            hoa_monthly,
            current_year,
            owner_occupied=owner_occupied,
        )

    # The cheapest house you could buy outright still owes taxes, insurance and any HOA.
    # If that alone breaks the ceiling then no price works, and the reason is a fixed cost
    # rather than something a smaller loan fixes. Saying which beats returning zero.
    floor_price = max(profile.down_payment, 1.0)
    floor = at(floor_price)
    if floor.front_end_dti > ceiling:
        return MaxPriceSolution(
            dti_ceiling=ceiling,
            feasible=False,
            lender_max_price=None,
            household_max_price=None,
            floor_price=floor_price,
            floor_dti=floor.front_end_dti,
            notes=(
                f"Even at ${floor_price:,.0f}, a loan of $0, taxes and insurance and HOA "
                f"put front-end DTI at {floor.front_end_dti:.1%} -- above the "
                f"{ceiling:.1%} ceiling. Fixed costs are the binding constraint, so a "
                f"cheaper house does not solve it. Raise the ceiling or revisit the "
                f"insurance estimate.",
            ),
        )

    def bisect(dti_of) -> float:
        lo, hi = floor_price, _SOLVER_CEILING
        # Guard the bracket rather than assume it. If even the ceiling price fits, DTI is
        # not the constraint, and the caller gets told that instead of a bogus root.
        if dti_of(at(hi)) <= ceiling:
            return hi
        while hi - lo > _SOLVER_TOLERANCE:
            mid = (lo + hi) / 2.0
            if dti_of(at(mid)) <= ceiling:
                lo = mid
            else:
                hi = mid
        # Return the low edge: it satisfies the ceiling. The high edge does not, and
        # rounding a house price up into a DTI breach is the one error that matters here.
        return lo

    lender = bisect(lambda c: c.front_end_dti)
    # The reserve is a range across three methods, so the conservative edge is the high
    # one. Averaging them here would hide the spread `maintenance` deliberately reports.
    household = bisect(lambda c: (c.piti + c.reserve_high) / profile.monthly_income)

    solved = at(lender)
    notes: list[str] = [
        f"At ${lender:,.0f} the payment is ${solved.piti:,.0f}/mo and front-end DTI is "
        f"{solved.front_end_dti:.1%}. True monthly including the reserve runs "
        f"${solved.true_monthly_low:,.0f} to ${solved.true_monthly_high:,.0f}.",
        f"Holding the same {ceiling:.0%} of gross while also funding the maintenance "
        f"reserve caps the price at ${household:,.0f}, ${lender - household:,.0f} lower.",
    ]

    down_pct = profile.down_payment / lender if lender else 0.0
    if down_pct < 0.20:
        # Not modeled anywhere in this codebase, and it is real money. The solver raising
        # the price is exactly what drives the down-payment percentage down, so this
        # warning is a property of the answer rather than of the inputs.
        notes.append(
            f"A fixed ${profile.down_payment:,.0f} down on ${lender:,.0f} is "
            f"{down_pct:.1%}, under 20%. Mortgage insurance would apply and is NOT in "
            f"this figure -- typically 0.3-1.5% of the loan per year, which would pull "
            f"the real answer down. Treat this as an upper bound."
        )
    if sqft is None:
        notes.append(
            "No square footage given, so the maintenance reserve used its unknown-sqft "
            "band. Supplying sqft narrows the household number."
        )
    if lender >= _SOLVER_CEILING:
        notes.append(
            f"The search ceiling of ${_SOLVER_CEILING:,.0f} was reached: at this income "
            f"DTI is not the binding constraint. Cash to close and appraisal are."
        )

    return MaxPriceSolution(
        dti_ceiling=ceiling,
        feasible=True,
        lender_max_price=lender,
        household_max_price=household,
        floor_price=floor_price,
        floor_dti=floor.front_end_dti,
        notes=tuple(notes),
    )


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
