"""The source stations, against recorded responses.

The tests worth reading here are the failure ones. Any station can parse a good response;
what determines whether this tool is trustworthy is what happens when a government server
times out, returns a shape nobody documented, or answers a question that was never asked.
"""

from __future__ import annotations

import pytest

from analyzer.core.profile import load_profile
from analyzer.sources import http
from analyzer.sources.base import Context, Station, StationResult
from analyzer.sources.broadband import BroadbandStation
from analyzer.sources.commute import CommuteStation
from analyzer.sources.flood import FloodStation, is_sfha
from analyzer.sources.geocode import GeocodeStation
from analyzer.sources.parcel import (
    ParcelStation,
    assessment_ratio,
    pick_parcel,
    read_baths,
    read_utilities,
    street_number,
)

from support import load_response


@pytest.fixture
def profile():
    return load_profile()


@pytest.fixture
def located() -> Context:
    """A context as it looks after geocoding succeeded."""
    return Context(
        address="606 Andre Ct, Spartanburg, SC 29301",
        price=268_000,
        lat=34.943051,
        lon=-81.97665,
        census_block_geoid="450830206012004",
        county_fips="45083",
    )


# =============================================================================
# The station contract itself
# =============================================================================


class TestStationContract:
    def test_a_station_never_raises_on_an_unreachable_source(self, located):
        class Exploding(Station):
            name = "exploding"

            def fetch(self, ctx):
                raise http.SourceUnavailable("connection reset")

        result = Exploding().run(located)
        assert result.degradation is not None
        assert "unreachable" in result.degradation.reason

    def test_a_rejection_is_reported_differently_from_an_outage(self, located):
        class Refused(Station):
            name = "refused"

            def fetch(self, ctx):
                raise http.SourceRejected("HTTP 403 Forbidden")

        reason = Refused().run(located).degradation.reason
        assert "declined" in reason and "403" in reason

    def test_an_unexpected_response_shape_degrades_rather_than_crashes(self, located):
        class Confused(Station):
            name = "confused"

            def fetch(self, ctx):
                return {"totally": "different"}["features"]

        result = Confused().run(located)
        assert result.degradation is not None
        assert "KeyError" in result.degradation.reason

    def test_a_station_cannot_write_facts_it_did_not_declare(self, located):
        class Sneaky(Station):
            name = "sneaky"
            provides = ("beds",)

            def fetch(self, ctx):
                return StationResult(station="sneaky", facts={"beds": 3, "price": 1})

        with pytest.raises(AssertionError, match="undeclared facts"):
            Sneaky().run(located)

    def test_non_fatal_stations_skip_without_coordinates(self):
        empty = Context(address="somewhere", price=1.0)
        result = FloodStation().run(empty)
        assert result.degradation is not None
        assert "no coordinates" in result.degradation.reason

    def test_skipping_costs_no_request(self, fake_http):
        FloodStation().run(Context(address="x", price=1.0))
        assert fake_http.calls == []


# =============================================================================
# G — geocode
# =============================================================================


class TestGeocode:
    def test_reads_coordinates_and_census_block(self, fake_http):
        fake_http.route("geocoding.geo.census.gov", load_response("census_geocode"))
        ctx = Context(address="606 Andre Ct, Spartanburg, SC 29301", price=268_000)
        result = GeocodeStation().run(ctx)

        assert result.ok
        assert result.context_updates["lat"] == pytest.approx(34.943051, abs=1e-5)
        assert result.context_updates["census_block_geoid"].startswith("45083")
        assert result.context_updates["county_fips"] == "45083"

    def test_census_values_are_measured_and_cited(self, fake_http):
        fake_http.route("geocoding.geo.census.gov", load_response("census_geocode"))
        result = GeocodeStation().run(Context(address="606 Andre Ct", price=1.0))
        for value in result.values.values():
            assert value.confidence == "measured"
            assert value.source_url

    def test_falls_back_to_nominatim_when_census_finds_nothing(self, fake_http):
        fake_http.route("geocoding.geo.census.gov", {"result": {"addressMatches": []}})
        fake_http.route(
            "nominatim", [{"lat": "34.94", "lon": "-81.97", "display_name": "somewhere, SC"}]
        )
        result = GeocodeStation().run(Context(address="unmatched road", price=1.0))

        assert result.ok
        assert fake_http.called_with("nominatim")

    def test_the_fallback_is_labelled_an_estimate_not_a_measurement(self, fake_http):
        fake_http.route("geocoding.geo.census.gov", {"result": {"addressMatches": []}})
        fake_http.route("nominatim", [{"lat": "34.94", "lon": "-81.97", "display_name": "x"}])
        result = GeocodeStation().run(Context(address="unmatched road", price=1.0))

        assert result.values["latitude"].confidence == "estimated"
        assert "centroid" in result.values["latitude"].note
        assert any("Census geocoder did not match" in t["task"] for t in result.tasks)

    def test_both_geocoders_failing_is_fatal(self, fake_http):
        fake_http.route("geocoding.geo.census.gov", {"result": {"addressMatches": []}})
        fake_http.route("nominatim", [])
        result = GeocodeStation().run(Context(address="nowhere at all", price=1.0))
        assert result.degradation is not None

    def test_an_empty_address_is_refused_before_any_request(self, fake_http):
        result = GeocodeStation().run(Context(address="   ", price=1.0))
        assert "no address" in result.degradation.reason
        assert fake_http.calls == []


# =============================================================================
# F — flood
# =============================================================================


class TestFlood:
    def test_reads_the_zone_from_a_real_response(self, fake_http, located):
        fake_http.route("NFHL", load_response("nfhl_flood_zone"))
        result = FloodStation().run(located)

        assert result.ok
        assert result.facts["flood_zone"] == "X"
        assert result.values["in_special_flood_hazard_area"].value is False

    def test_an_sfha_zone_produces_a_blocking_task(self, fake_http, located):
        fake_http.route(
            "NFHL",
            {"features": [{"attributes": {"FLD_ZONE": "AE", "SFHA_TF": "T", "STATIC_BFE": 612.0}}]},
        )
        result = FloodStation().run(located)

        assert result.facts["flood_zone"] == "AE"
        assert result.values["in_special_flood_hazard_area"].value is True
        assert any(t["blocking"] and "mandatory" in t["task"] for t in result.tasks)

    def test_unmapped_is_unknown_not_safe(self, fake_http, located):
        """The most dangerous bug this station could have."""
        fake_http.route("NFHL", {"features": []})
        result = FloodStation().run(located)

        assert result.facts["flood_zone"] is None
        assert result.values["flood_zone"].confidence == "unavailable"
        assert "NOT that it is outside" in result.values["flood_zone"].note
        assert any(t["blocking"] for t in result.tasks)

    def test_an_arcgis_error_payload_degrades(self, fake_http, located):
        fake_http.route("NFHL", {"error": {"code": 500, "message": "backend down"}})
        result = FloodStation().run(located)
        assert result.degradation is not None
        assert "backend down" in result.degradation.reason

    @pytest.mark.parametrize(
        "zone,expected",
        [("A", True), ("AE", True), ("AO", True), ("VE", True), ("X", False),
         ("X500", False), ("D", False), ("", False), (None, False)],
    )
    def test_sfha_classification(self, zone, expected):
        assert is_sfha(zone) is expected


# =============================================================================
# C — commute
# =============================================================================


class TestCommute:
    def test_applies_the_congestion_multiplier(self, fake_http, profile, located):
        fake_http.route("router.project-osrm.org", load_response("osrm_route"))
        result = CommuteStation(profile.primary_anchor).run(located)

        assert result.ok
        free_flow = result.values["free_flow_minutes"].value
        assert result.facts["commute_min"] == pytest.approx(free_flow * 1.25, abs=0.1)

    def test_the_free_flow_number_is_measured_and_the_rush_number_is_not(
        self, fake_http, profile, located
    ):
        """The distinction the whole station exists to preserve."""
        fake_http.route("router.project-osrm.org", load_response("osrm_route"))
        result = CommuteStation(profile.primary_anchor).run(located)

        assert result.values["free_flow_minutes"].confidence == "measured"
        assert result.values["rush_hour_minutes"].confidence == "estimated"
        assert "assumption, not a measurement" in result.values["rush_hour_minutes"].note

    def test_a_route_failure_degrades(self, fake_http, profile, located):
        fake_http.route("router.project-osrm.org", {"code": "NoRoute", "routes": []})
        result = CommuteStation(profile.primary_anchor).run(located)
        assert "NoRoute" in result.degradation.reason

    def test_it_always_asks_you_to_actually_drive_it(self, fake_http, profile, located):
        fake_http.route("router.project-osrm.org", load_response("osrm_route"))
        result = CommuteStation(profile.primary_anchor).run(located)
        assert any("Drive the route" in t["task"] for t in result.tasks)


# =============================================================================
# A — parcel
# =============================================================================


class TestUtilityParsing:
    @pytest.mark.parametrize(
        "slots,expected",
        [
            (("ALL PUBLIC", " ", " "), "public"),
            (("PUBLIC WATER", "PUBLIC SEWER", " "), "public"),
            (("WELL", "SEPTIC", " "), "well_septic"),
            (("PUBLIC WATER", "SEPTIC", " "), "well_septic"),
            (("ALL PUBLIC", "GAS", " "), "public"),
        ],
    )
    def test_recognised_combinations(self, slots, expected):
        attrs = {f"Utility{i}": v for i, v in enumerate(slots, 1)}
        assert read_utilities(attrs)[0] == expected

    def test_half_of_public_service_is_unknown_not_public(self):
        """Public water alone says nothing about sewer, which is the hard fail."""
        water_sewer, summary = read_utilities({"Utility1": "PUBLIC WATER"})
        assert water_sewer is None
        assert "only one half" in summary

    def test_blank_utilities_are_unknown_not_public(self):
        water_sewer, summary = read_utilities({"Utility1": " ", "Utility2": " "})
        assert water_sewer is None
        assert "no utility fields" in summary

    def test_an_unrecognised_code_is_unknown_and_says_so(self):
        water_sewer, summary = read_utilities({"Utility1": "CISTERN"})
        assert water_sewer is None
        assert "not recognised" in summary


class TestParcelFieldReading:
    def test_half_baths_count_as_a_half(self):
        assert read_baths({"FullBaths": 2, "HalfBaths": 1}) == 2.5

    def test_no_full_baths_recorded_is_unknown_not_zero(self):
        assert read_baths({"FullBaths": 0, "HalfBaths": 1}) is None

    @pytest.mark.parametrize(
        "code,ratio", [("4OOR", 0.04), ("6RGR", 0.06), ("9XXX", None), (None, None)]
    )
    def test_the_property_type_code_reveals_the_assessment_ratio(self, code, ratio):
        assert assessment_ratio(code)[0] == ratio

    @pytest.mark.parametrize(
        "text,expected",
        [("606 ANDRE CT", "606"), ("606 Andre Ct, Spartanburg", "606"),
         ("ANDRE CT", None), ("", None), (None, None)],
    )
    def test_street_number_extraction(self, text, expected):
        assert street_number(text) == expected


class TestParcelDisambiguation:
    """A 40 m buffer catches neighbours. Picking wrong attaches their house to yours."""

    def test_a_single_candidate_is_returned_unchanged(self):
        one = {"PropertyLo": "606 ANDRE CT"}
        assert pick_parcel([one], "606 Andre Ct") is one

    def test_the_street_number_breaks_the_tie(self):
        candidates = [
            {"PropertyLo": "604 ANDRE CT", "BedRooms": 2},
            {"PropertyLo": "606 ANDRE CT", "BedRooms": 4},
        ]
        assert pick_parcel(candidates, "606 Andre Ct, Spartanburg SC")["BedRooms"] == 4

    def test_no_match_is_flagged_rather_than_guessed_silently(self):
        candidates = [{"PropertyLo": "604 ANDRE CT"}, {"PropertyLo": "608 ANDRE CT"}]
        chosen = pick_parcel(candidates, "606 Andre Ct")
        assert "_ambiguous" in chosen

    def test_ambiguity_becomes_a_blocking_task(self, fake_http, located):
        fake_http.route("maps.spartanburgcounty.org", http.SourceUnavailable("timeout"))
        fake_http.route(
            "services9.arcgis.com",
            {
                "features": [
                    {"attributes": {"PropertyLo": "604 ANDRE CT", "BedRooms": 3,
                                    "FullBaths": 2, "YearBuilt": 1970, "PropertyTy": "4OOR",
                                    "Utility1": "ALL PUBLIC"}},
                    {"attributes": {"PropertyLo": "608 ANDRE CT", "BedRooms": 4,
                                    "FullBaths": 2, "YearBuilt": 1972, "PropertyTy": "4OOR",
                                    "Utility1": "ALL PUBLIC"}},
                ]
            },
        )
        result = ParcelStation().run(located)
        assert any("Confirm which parcel" in t["task"] and t["blocking"] for t in result.tasks)


class TestParcelStation:
    @pytest.fixture
    def stubbed(self, fake_http):
        """The real situation: county server down, stale mirror answering."""
        fake_http.route(
            "maps.spartanburgcounty.org",
            http.SourceUnavailable("SSL: CERTIFICATE_VERIFY_FAILED"),
        )
        fake_http.route("services9.arcgis.com", load_response("parcel_cama"))
        return fake_http

    def test_the_authoritative_source_is_always_tried_first(self, stubbed, located):
        ParcelStation().run(located)
        assert stubbed.calls[0].startswith("https://maps.spartanburgcounty.org")

    def test_it_reads_the_house_from_a_real_record(self, stubbed, located):
        result = ParcelStation().run(located)
        assert result.ok
        assert result.facts["year_built"] == 1970
        assert result.facts["beds"] == 4
        assert result.facts["baths"] == 2.5
        assert result.facts["water_sewer"] == "public"

    def test_mirror_values_are_estimates_carrying_their_vintage(self, stubbed, located):
        """A five-year-old snapshot of a current field is an estimate, however precise."""
        result = ParcelStation().run(located)
        year = result.values["year_built"]
        assert year.confidence == "estimated"
        assert "2021" in year.note

    def test_falling_back_produces_a_blocking_task_naming_the_county(self, stubbed, located):
        result = ParcelStation().run(located)
        first = result.tasks[0]
        assert first["blocking"]
        assert "Assessor" in first["task"]
        assert "CERTIFICATE_VERIFY_FAILED" in first["reason"]

    def test_garage_bay_count_is_never_invented_from_a_type_string(self, stubbed, located):
        """'CARPORT ATT' does not contain a number, so no number is produced."""
        result = ParcelStation().run(located)
        assert "garage_spaces" not in result.facts
        assert "bay count is not in this dataset" in result.values["garage_type"].note
        assert any("garage bay count" in t["task"] for t in result.tasks)

    def test_blank_living_area_is_unknown_and_asks_for_the_number(self, stubbed, located):
        result = ParcelStation().run(located)
        assert result.facts["sqft"] is None
        assert any("square footage" in t["task"] for t in result.tasks)

    def test_a_four_percent_record_warns_that_the_ratio_must_be_refiled(self, stubbed, located):
        result = ParcelStation().run(located)
        assert result.values["current_assessment_ratio"].value == 0.04
        assert any("re-filed after closing" in t["task"] and t["blocking"] for t in result.tasks)

    def test_both_sources_failing_degrades(self, fake_http, located):
        fake_http.route("maps.spartanburgcounty.org", http.SourceUnavailable("timeout"))
        fake_http.route("services9.arcgis.com", {"features": []})
        result = ParcelStation().run(located)
        assert result.degradation is not None
        assert "no parcel found" in result.degradation.reason


# =============================================================================
# B — broadband
# =============================================================================


class TestBroadband:
    def test_no_api_key_degrades_with_an_instruction(self, fake_http, located):
        result = BroadbandStation(api_key=None).run(located)
        assert result.degradation is not None
        assert "FCC_API_KEY" in result.degradation.reason
        assert fake_http.calls == []

    def test_a_missing_key_never_reports_no_fiber(self, fake_http, located):
        """The bug that would silently deduct 15 points from every house in the county."""
        result = BroadbandStation(api_key=None).run(located)
        assert result.facts == {}

    def test_no_census_block_degrades(self, fake_http, located):
        located.census_block_geoid = None
        result = BroadbandStation(api_key="key").run(located)
        assert "census block" in result.degradation.reason

    def test_fiber_found(self, fake_http, located):
        fake_http.route(
            "broadbandmap.fcc.gov",
            {"data": [{"brand_name": "Spartanburg Fiber", "technology_code": 50,
                       "max_advertised_download_speed": 1000}]},
        )
        result = BroadbandStation(api_key="key").run(located)
        assert result.facts["fiber_available"] is True
        assert "Spartanburg Fiber" in result.values["reporting_providers"].value

    def test_cable_alone_is_not_fiber(self, fake_http, located):
        fake_http.route(
            "broadbandmap.fcc.gov",
            {"data": [{"brand_name": "Cable Co", "technology_code": 40,
                       "max_advertised_download_speed": 940}]},
        )
        result = BroadbandStation(api_key="key").run(located)
        assert result.facts["fiber_available"] is False

    def test_slow_fiber_does_not_count(self, fake_http, located):
        fake_http.route(
            "broadbandmap.fcc.gov",
            {"data": [{"brand_name": "Slow Fiber", "technology_code": 50,
                       "max_advertised_download_speed": 25}]},
        )
        result = BroadbandStation(api_key="key").run(located)
        assert result.facts["fiber_available"] is False

    def test_every_value_is_block_precision_and_says_availability_is_a_claim(
        self, fake_http, located
    ):
        fake_http.route(
            "broadbandmap.fcc.gov",
            {"data": [{"brand_name": "F", "technology_code": 50,
                       "max_advertised_download_speed": 1000}]},
        )
        result = BroadbandStation(api_key="key").run(located)
        value = result.values["fiber_available"]
        assert value.precision == "census_block"
        assert "10 business days" in value.note
        assert "not address level" in value.note

    def test_the_call_the_isp_task_is_permanent_and_blocking(self, fake_http, located):
        fake_http.route(
            "broadbandmap.fcc.gov",
            {"data": [{"brand_name": "Acme Fiber", "technology_code": 50,
                       "max_advertised_download_speed": 1000}]},
        )
        result = BroadbandStation(api_key="key").run(located)
        call = [t for t in result.tasks if "Call" in t["task"]]
        assert len(call) == 1
        assert call[0]["blocking"]
        assert "Acme Fiber" in call[0]["task"]

    def test_no_filing_is_unknown_not_unserved(self, fake_http, located):
        fake_http.route("broadbandmap.fcc.gov", {"data": []})
        result = BroadbandStation(api_key="key").run(located)
        assert result.facts["fiber_available"] is None
        assert "not evidence of no service" in result.values["fiber_available"].note

    def test_a_rejected_key_degrades_rather_than_guessing(self, fake_http, located):
        fake_http.route("broadbandmap.fcc.gov", http.SourceRejected("HTTP 401 Unauthorized"))
        result = BroadbandStation(api_key="stale-key").run(located)
        assert "declined" in result.degradation.reason
        assert result.facts == {}

class TestCurrentCountyParcelStation:
    """The live service has a different schema from the retained 2021 mirror."""

    def test_current_county_fields_are_used_and_wrapped_as_measurements(self, fake_http, located):
        fake_http.route(
            "maps.spartanburgcounty.org",
            {
                "features": [
                    {
                        "attributes": {
                            "MAPNUMBER": "7-10-00-001.00",
                            "PropertyLocation": "606 ANDRE CT SPARTANBURG",
                            "District": "5800",
                            "YearBuilt": 2004,
                            "LivingArea": 1800,
                            "FullBaths": 2,
                            "HalfBaths": 1,
                            "BedRooms": 4,
                            "Garage": "GARAGE ATT",
                            "Utility1": "ALL PUBLIC",
                            "PropertyType": "4OOR",
                            "Acreage": 0.33,
                        }
                    }
                ]
            },
        )

        result = ParcelStation().run(located)

        assert result.facts["year_built"] == 2004
        assert result.values["parcel_id"].value == "7-10-00-001.00"
        assert result.values["year_built"].confidence == "measured"
        assert result.values["situs_address"].confidence == "measured"
        assert not fake_http.called_with("services9.arcgis.com")
        # This catches the dangerous failure mode: asking the primary for only stale
        # mirror names makes ArcGIS reject the entire request and silently selects 2021.
        assert "MAPNUMBER" in fake_http.calls[0]
        assert "PropertyType" in fake_http.calls[0]
