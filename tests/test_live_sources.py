"""Contract tests against the real endpoints. Excluded from the default run.

    pytest -m live

The offline suite proves the parsers handle the responses we recorded. It cannot tell you
that a county changed a field name last Tuesday. That is what these are for: run them
before trusting a report, and on a schedule in CI. A failure here is news about the world,
not a bug in the code, so it must never break the normal suite.
"""

from __future__ import annotations

import pytest

from analyzer.core.profile import load_profile
from analyzer.pipeline import run
from analyzer.sources import http, parcel
from analyzer.sources.base import Context
from analyzer.sources.commute import CommuteStation
from analyzer.sources.flood import FloodStation
from analyzer.sources.geocode import GeocodeStation

pytestmark = pytest.mark.live

ADDRESS = "606 Andre Ct, Spartanburg, SC 29301"


@pytest.fixture(scope="module")
def located() -> Context:
    ctx = Context(address=ADDRESS, price=268_000)
    result = GeocodeStation().run(ctx)
    if not result.ok:
        pytest.skip(f"census geocoder unavailable: {result.degradation.reason}")
    for key, value in result.context_updates.items():
        setattr(ctx, key, value)
    return ctx


def test_the_census_geocoder_still_returns_a_census_block(located):
    assert located.lat and located.lon
    assert len(located.census_block_geoid) == 15
    assert located.county_fips == "45083", "Spartanburg County FIPS"


def test_fema_still_answers_a_point_query(located):
    result = FloodStation().run(located)
    if not result.ok:
        pytest.skip(f"FEMA NFHL unavailable: {result.degradation.reason}")
    assert result.facts["flood_zone"]


def test_osrm_still_routes_to_the_hospital(located):
    result = CommuteStation(load_profile().primary_anchor).run(located)
    if not result.ok:
        pytest.skip(f"OSRM unavailable: {result.degradation.reason}")
    assert 0 < result.facts["commute_min"] < 120


def test_the_parcel_mirror_still_has_the_fields_the_parser_reads(located):
    """The named fields are the ones a rename would silently break."""
    try:
        attrs = ParcelProbe()._query(parcel.MIRROR, located)
    except (http.SourceUnavailable, http.SourceRejected, LookupError) as exc:
        pytest.skip(f"parcel mirror unavailable: {exc}")

    for field in ("PropertyLo", "YearBuilt", "BedRooms", "FullBaths", "PropertyTy", "Utility1"):
        assert field in attrs, f"the mirror stopped returning {field}"
    assert attrs["PropertyTy"][0] in "46", "assessment-ratio code shape changed"


class ParcelProbe(parcel.ParcelStation):
    """Exists only so the live test can query the mirror directly, skipping the county."""


def test_the_authoritative_county_server_is_still_unreachable():
    """Documents a known limitation, and tells us the day it is fixed.

    The county's ArcGIS host serves an incomplete certificate chain. If this test starts
    failing, that is good news: drop the stale-mirror fallback note from the docs.
    """
    ctx = Context(address=ADDRESS, price=1.0, lat=34.943051, lon=-81.97665)
    with pytest.raises((http.SourceUnavailable, http.SourceRejected, LookupError)):
        parcel.ParcelStation()._query(parcel.COUNTY_PRIMARY, ctx)


def test_a_full_run_produces_a_scored_report(located):
    result = run(ADDRESS, 268_000, roof_age_years=17, hvac_age_years=14)
    assert result.document["score"]["verdict"] in {"TAKE", "WATCH", "PASS"}
    assert result.document["location"]["census_block_geoid"]
    for entry in result.document["degraded_sources"]:
        assert entry["reason"], "a degradation must always explain itself"
        assert "missing" in entry
