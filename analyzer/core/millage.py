"""South Carolina millage schedules.

The owner-occupied exemption is narrower than "no school tax". Per the SC Association
of Counties, owner-occupied properties qualifying for the 4% ratio are exempt from
school property taxes *with the exception of mills imposed for school bonded
indebtedness*. So the exemption has to be modeled per component, not as one flag.

Sources:
  https://dor.sc.gov/lgs/property-tax-basics
  https://www.sccounties.org/sites/default/files/uploads/resources/propertytaxreport2024_final.pdf

Every schedule here is ESTIMATED until the Spartanburg County GIS parcel lookup
resolves the actual tax district for a specific TMS. See docs/KNOWN_LIMITATIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

SC_DOR = "https://dor.sc.gov/lgs/property-tax-basics"
SCAC_2024 = (
    "https://www.sccounties.org/sites/default/files/uploads/resources/"
    "propertytaxreport2024_final.pdf"
)
COUNTY_AUDITOR = "https://www.spartanburgcounty.gov/171/Auditor"


@dataclass(frozen=True)
class MillageComponent:
    """One line item on a tax bill.

    exempt_for_primary: True when an owner-occupied legal residence does not pay it.
    School *operating* millage is exempt. School *bond* millage is not.
    """

    name: str
    mills: float
    exempt_for_primary: bool = False


@dataclass(frozen=True)
class MillageSchedule:
    district: str
    components: tuple[MillageComponent, ...]
    source_url: str
    note: str

    def total_mills(self) -> float:
        """Full millage — what a non-owner-occupied property pays."""
        return round(sum(c.mills for c in self.components), 4)

    def primary_mills(self) -> float:
        """Millage after the owner-occupied legal-residence exemption."""
        return round(
            sum(c.mills for c in self.components if not c.exempt_for_primary), 4
        )

    def exempt_mills(self) -> float:
        return round(self.total_mills() - self.primary_mills(), 4)

    def breakdown(self) -> list[dict[str, object]]:
        return [
            {
                "name": c.name,
                "mills": c.mills,
                "exempt_for_primary": c.exempt_for_primary,
            }
            for c in self.components
        ]


# Spartanburg County base millage, 2025 schedule.
_COUNTY_BASE = (MillageComponent("Spartanburg County base", 85.6),)

# Spartanburg School District 1, from the 2024 SCAC report. District total 289.2 mills.
# "Current School" is the operating levy and is the exempt portion.
SPARTANBURG_1 = MillageSchedule(
    district="Spartanburg School District 1",
    components=_COUNTY_BASE
    + (
        MillageComponent("Current School (operating)", 175.8, exempt_for_primary=True),
        MillageComponent("School Bonds", 74.0),
        MillageComponent("General School", 13.0),
        MillageComponent("McCarthy/Teszler", 10.9),
        MillageComponent("Swofford Vocational School", 7.8),
        MillageComponent("Teacher Equalization Fund", 4.0),
        MillageComponent("University School", 3.7),
    ),
    source_url=SCAC_2024,
    note=(
        "ESTIMATE - Spartanburg District 1 schedule from the 2024 SCAC report plus "
        "county base. Excludes municipal and special-purpose district mills. Verify "
        "the exact district for a specific TMS with the County Auditor."
    ),
)

# Fallback when the district is unknown. Calibrated so primary_mills() == 280, matching
# the figure the dashboard has used, so the Python and JS paths agree.
TYPICAL_UNINCORPORATED = MillageSchedule(
    district="Typical unincorporated Spartanburg County (fallback)",
    components=(
        MillageComponent("Typical owner-occupied net millage", 280.0),
        MillageComponent("Typical school operating", 175.8, exempt_for_primary=True),
    ),
    source_url=COUNTY_AUDITOR,
    note=(
        "ESTIMATE - blended county + district + fire + other for unincorporated "
        "Spartanburg County. Not parcel-specific. Verify with the County Auditor "
        "before relying on the tax figure for an offer."
    ),
)

SCHEDULES: dict[str, MillageSchedule] = {
    "spartanburg_1": SPARTANBURG_1,
    "typical": TYPICAL_UNINCORPORATED,
}


def get_schedule(key: str | None) -> MillageSchedule:
    """Resolve a schedule, falling back to the typical estimate."""
    if not key:
        return TYPICAL_UNINCORPORATED
    return SCHEDULES.get(key, TYPICAL_UNINCORPORATED)
