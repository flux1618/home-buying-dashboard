"""Station A — assessor and CAMA record: the physical facts about the house.

This station is the honest centrepiece of the whole project, because it is where the
data is worst and the temptation to paper over that is strongest.

**What happened when this was built.** Spartanburg County runs its own ArcGIS server at
`maps.spartanburgcounty.org`. It did not respond from the build environment — not a 404,
just silence until timeout. What *is* reachable is a county extract published to ArcGIS
Online, dated February 2021, containing roughly 29,400 parcels rather than the county's
full set.

That is a genuinely bad source. It is also the source that exists. The response is not to
pretend otherwise:

  - The authoritative county server is tried first, every time.
  - The 2021 mirror is a labelled fallback, never the primary.
  - Every value from the mirror is `estimated`, never `measured`, with the vintage in
    the note — because a five-year-old snapshot of a *current* field is an estimate, no
    matter how precisely it is stored.
  - A blocking verification task names the county's own record as the thing to check.

**What is deliberately NOT inferred.** The `Garage` field records type — `GARAGE ATT`,
`CARPORT DET` — and never a bay count. Turning "GARAGE ATT" into "2 spaces" would produce
a number that scores, deducts, and looks measured, from a field that does not contain it.
So `garage_spaces` stays unknown and a task asks for the count. Unknown that says so beats
a plausible fabrication.

**A useful accident.** The `PropertyTy` code starts with 4 for owner-occupied legal
residence and 6 for everything else, which is the assessment ratio itself. That confirms
the current owner's ratio directly — and therefore whether the tax figure on the listing
is about to change under a new owner.
"""

from __future__ import annotations

from typing import Any

from ..core.provenance import derived, estimated, measured
from . import http
from .base import Context, Station, StationResult

# Authoritative, and unreachable from some networks. Tried first regardless.
COUNTY_PRIMARY = (
    "https://maps.spartanburgcounty.org/server/rest/services/GIS/CAMA_Parcels/FeatureServer/0/query"
)
COUNTY_DOC = "https://www.spartanburgcounty.org/172/Assessor"

# County extract published to ArcGIS Online, February 2021. Stale and partial.
MIRROR = (
    "https://services9.arcgis.com/HoRra3ATPLGmyjn6/arcgis/rest/services/"
    "Parcel_and_CAMA_Feb_1_2021/FeatureServer/0/query"
)
MIRROR_DOC = (
    "https://www.arcgis.com/home/item.html?id=&title=Parcel_and_CAMA_Feb_1_2021"
)
MIRROR_VINTAGE = "February 2021 county extract, approx. 29,400 parcels — not the full county"

FIELDS = ",".join([
    "TAXPIN", "District", "PropertyLo", "City", "Zip", "YearBuilt", "LivingArea",
    "TotalArea", "FullBaths", "HalfBaths", "BedRooms", "Garage", "Utility1",
    "Utility2", "Utility3", "PropertyTy", "Acreage", "SaleDate", "SaleAmount",
    "CurrentAss", "OwnerName", "RoofCover", "HeatType",
])

# The Census geocoder returns a point interpolated along the street centreline, which
# routinely lands a metre or two OUTSIDE the parcel it belongs to. A strict point-in-
# polygon query therefore misses real parcels. Buffering the query by 40 m fixes that;
# picking the right candidate out of the buffer is handled by street-number matching.
SEARCH_RADIUS_M = 40

PUBLIC_WATER = {"ALL PUBLIC", "PUBLIC WATER"}
PUBLIC_SEWER = {"ALL PUBLIC", "PUBLIC SEWER"}
PRIVATE = {"WELL", "SEPTIC"}


def clean(raw: Any) -> str | None:
    """ArcGIS pads unset strings with spaces. Treat those as absent."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def positive_int(raw: Any) -> int | None:
    """Zero in this dataset means 'not recorded', not 'zero of them'."""
    try:
        number = int(float(raw))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def read_utilities(attrs: dict[str, Any]) -> tuple[str | None, str]:
    """Collapse three free-text utility slots into the hard-fail input.

    Returns (water_sewer, human-readable summary). `None` means the record did not say,
    which must not be read as public service.
    """
    slots = {clean(attrs.get(f"Utility{i}")) for i in (1, 2, 3)}
    slots.discard(None)
    upper = {s.upper() for s in slots if s}

    if not upper:
        return None, "no utility fields recorded"
    if upper & PRIVATE:
        found = sorted(upper & PRIVATE)
        return "well_septic", f"private service recorded: {', '.join(found).lower()}"
    if upper & PUBLIC_WATER and upper & PUBLIC_SEWER:
        return "public", f"public water and sewer ({', '.join(sorted(upper)).lower()})"
    if upper & (PUBLIC_WATER | PUBLIC_SEWER):
        half = ", ".join(sorted(upper)).lower()
        return None, f"only one half of public service recorded ({half}) — the other is unknown"
    return None, f"utility codes not recognised: {', '.join(sorted(upper)).lower()}"


def read_baths(attrs: dict[str, Any]) -> float | None:
    full = positive_int(attrs.get("FullBaths"))
    half = positive_int(attrs.get("HalfBaths")) or 0
    if full is None:
        return None
    return float(full) + 0.5 * half


def assessment_ratio(property_type: str | None) -> tuple[float | None, str]:
    """The leading digit of the CAMA property-type code is the assessment ratio."""
    if not property_type:
        return None, "property type not recorded"
    lead = property_type[0]
    if lead == "4":
        return 0.04, "coded 4% — current owner claims legal residence"
    if lead == "6":
        return 0.06, "coded 6% — not owner-occupied for the current owner"
    return None, f"unrecognised property-type code {property_type!r}"


def street_number(text: str | None) -> str | None:
    """Leading house number, or None. Used to disambiguate buffered matches."""
    if not text:
        return None
    first = text.strip().split()[0] if text.strip().split() else ""
    return first if first.isdigit() else None


def pick_parcel(candidates: list[dict[str, Any]], address: str) -> dict[str, Any]:
    """Choose among parcels inside the search buffer.

    A 40 m buffer on a suburban street can return three or four neighbouring lots, and
    silently taking the first one would attach a neighbour's bedroom count to this house.
    Matching the street number is a cheap, checkable tiebreak. If nothing matches, the
    caller gets the first candidate but the ambiguity is recorded in the result.
    """
    if len(candidates) == 1:
        return candidates[0]

    wanted = street_number(address)
    if wanted:
        for candidate in candidates:
            if street_number(clean(candidate.get("PropertyLo"))) == wanted:
                return candidate

    chosen = dict(candidates[0])
    chosen["_ambiguous"] = [clean(c.get("PropertyLo")) for c in candidates]
    return chosen


class ParcelStation(Station):
    name = "parcel"
    provides = ("sqft", "beds", "baths", "year_built", "water_sewer")
    description = "County assessor / CAMA record, with a stale ArcGIS mirror as fallback"

    def fetch(self, ctx: Context) -> StationResult:
        try:
            attrs = self._query(COUNTY_PRIMARY, ctx)

            return self._build(attrs, source=COUNTY_DOC, stale=False, note=None)
        except (http.SourceUnavailable, http.SourceRejected, LookupError) as exc:
            attrs = self._query(MIRROR, ctx)
            result = self._build(
                attrs, source=MIRROR_DOC, stale=True, note=MIRROR_VINTAGE
            )
            result.tasks.insert(
                0,
                self.task(
                    "Pull the current parcel card from the Spartanburg County Assessor — "
                    "these figures came from a February 2021 extract",
                    blocking=True,
                    reason=f"county GIS server did not respond ({exc})",
                ),
            )
            return result

    # -- fetching ------------------------------------------------------------

    def _query(self, endpoint: str, ctx: Context) -> dict[str, Any]:
        url = http.build_url(
            endpoint,
            {
                "geometry": f'{{"x":{ctx.lon},"y":{ctx.lat},"spatialReference":{{"wkid":4326}}}}',
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "distance": SEARCH_RADIUS_M,
                "units": "esriSRUnit_Meter",
                "outFields": FIELDS,
                "returnGeometry": "false",
                "f": "json",
            },
        )
        payload = http.get_json(url).data
        if "error" in payload:
            raise http.SourceUnavailable(str(payload["error"].get("message", "ArcGIS error")))
        features = payload.get("features") or []
        if not features:
            raise LookupError(
                f"no parcel found within {SEARCH_RADIUS_M} m of the geocoded point"
            )
        return pick_parcel([f.get("attributes", {}) for f in features], ctx.address)

    # -- shaping -------------------------------------------------------------

    def _build(
        self, attrs: dict[str, Any], *, source: str, stale: bool, note: str | None
    ) -> StationResult:
        def wrap(value: Any, **kw: Any):
            """Stale sources produce estimates, current sources produce measurements."""
            extra = dict(kw)
            if stale:
                extra["note"] = "; ".join(filter(None, [note, extra.get("note")])) or None
                return estimated(value, source, **extra)
            return measured(value, source, **extra)

        sqft = positive_int(attrs.get("LivingArea"))
        beds = positive_int(attrs.get("BedRooms"))
        baths = read_baths(attrs)
        year_built = positive_int(attrs.get("YearBuilt"))
        water_sewer, utility_summary = read_utilities(attrs)
        ratio, ratio_note = assessment_ratio(clean(attrs.get("PropertyTy")))
        garage_type = clean(attrs.get("Garage"))
        district = clean(attrs.get("District"))

        values: dict[str, Any] = {}
        for key, value in (
            ("parcel_id", clean(attrs.get("TAXPIN"))),
            ("situs_address", clean(attrs.get("PropertyLo"))),
            ("tax_district", district),
            ("year_built", year_built),
            ("living_sqft", sqft),
            ("beds", beds),
            ("baths", baths),
            ("acreage", attrs.get("Acreage")),
            ("roof_cover", clean(attrs.get("RoofCover"))),
            ("heat_type", clean(attrs.get("HeatType"))),
        ):
            if value is not None:
                values[key] = wrap(value)

        values["water_sewer"] = (
            wrap(water_sewer, note=utility_summary)
            if water_sewer
            else derived(None, note=f"water/sewer not determined — {utility_summary}")
        )
        if ratio is not None:
            values["current_assessment_ratio"] = wrap(ratio, note=ratio_note)
        if garage_type:
            values["garage_type"] = wrap(
                garage_type,
                note="the assessor records garage TYPE only; bay count is not in this dataset",
            )

        tasks = self._tasks(
            sqft=sqft, beds=beds, garage_type=garage_type, ratio=ratio, ratio_note=ratio_note
        )

        ambiguous = attrs.get("_ambiguous")
        if ambiguous:
            neighbours = ", ".join(a for a in ambiguous if a)
            values["parcel_match_ambiguous"] = derived(
                True,
                note=f"street number did not match any parcel in the search radius; "
                     f"candidates were: {neighbours}",
            )
            tasks.insert(
                0,
                self.task(
                    f"Confirm which parcel this is — the search radius returned several "
                    f"and none matched the street number ({neighbours})",
                    blocking=True,
                    reason="physical facts may belong to a neighbouring lot",
                ),
            )

        return StationResult(
            station=self.name,
            facts={
                "sqft": sqft,
                "beds": beds,
                "baths": baths,
                "year_built": year_built,
                "water_sewer": water_sewer,
            },
            values=values,
            context_updates={"tax_district": district} if district else {},
            tasks=tasks,
        )

    def _tasks(self, *, sqft, beds, garage_type, ratio, ratio_note) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        if garage_type and garage_type.upper() != "NONE":
            tasks.append(
                self.task(
                    f"Confirm the garage bay count — the assessor records only "
                    f"{garage_type!r}, so no count was scored",
                    blocking=False,
                    reason="a bay count inferred from a type string would be a guess",
                )
            )
        if sqft is None:
            tasks.append(
                self.task(
                    "Get heated square footage from the listing or an appraisal — the "
                    "assessor record left LivingArea blank",
                    blocking=False,
                    reason="square footage drives both the score and the reserve estimate",
                )
            )
        if beds is None:
            tasks.append(
                self.task("Confirm bedroom count from the listing", blocking=False))
        if ratio == 0.06:
            tasks.append(
                self.task(
                    "Listing tax figures are on the 6% non-owner-occupied ratio and will "
                    "drop for an owner-occupier — file for legal residence with the County "
                    "Auditor by January 15 after closing",
                    blocking=False,
                    reason=ratio_note,
                )
            )
        elif ratio == 0.04:
            tasks.append(
                self.task(
                    "The 4% ratio belongs to the current owner, not the house — it must be "
                    "re-filed after closing or the bill resets to 6%",
                    blocking=True,
                    reason=ratio_note,
                )
            )
        return tasks
