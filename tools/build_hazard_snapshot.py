"""Compile FEMA National Risk Index hazard data into the static snapshot.

## Why this exists

`analyzer/sources/risk.py` gave the CLI and the API an 18-hazard risk layer. The static
page got nothing, which recreated in one afternoon exactly the drift that ADR 0008 was
written to end: the published snapshot and the local engine disagreed about what the tool
even measures. The page is the fourth door, and a door that opens onto a different building
is worse than no door.

The page cannot call FEMA. ADR 0001 makes it a snapshot — it scores what you type and looks
nothing up. So the hazard data is fetched here, at build time, and committed. Same pattern
as the market medians and the drive-time matrix already in `data.json`.

## What it fetches, and why in two shapes

**County distribution** — one query for all 87 Spartanburg County tracts, reduced to
min/median/max per hazard. This is the shape that answers "is wildfire a thing here," which
is the question somebody browsing the page actually has. A single tract cannot answer it.

**Per-ZIP centroid tract** — one point query per ZIP polygon already in `data.json`. This
answers "and what about where I'm looking," at the resolution FEMA publishes.

The two disagree on purpose and the page says so. A ZIP centroid lands in exactly one
tract; ZIP 29651 is 91.7 square miles and contains many. The centroid figure is a sample,
not an average, and labelling it otherwise would invent precision FEMA does not sell.

## Three things measured on 2026-08-19 that the output is shaped around

**1. Drought is modeled in 20 of 87 Spartanburg tracts, and where it is modeled it runs
71.0 to 94.7.** The tract containing 606 Andre Ct is one of the 67 where it is not modeled,
so a single-address view reports drought as unknown and moves on. County-wide, drought is
the highest-percentile hazard in the county. Both statements are true. This is the
"a zero is not good news" case from `risk.py` with local numbers attached, and it is the
strongest argument for shipping the distribution alongside the point.

**2. Community resilience is a county-level number wearing a tract-level costume.** All 87
Spartanburg tracts return exactly 44.5694. So do all 123 Greenville tracts (43.8739), all
54 Butte County CA tracts (48.2715), and all 81 Cumberland County NC tracts (22.4215).
Social vulnerability, by contrast, takes 74 distinct values across those same 87 tracts.
So RESL is published per county and joined onto the tract table. Presenting it next to a
tract-varying field without saying so would imply a neighbourhood signal that does not
exist in the data. It is labelled county-level everywhere it appears.

**3. Percentiles are the value; FEMA's five rating labels are binned per hazard.** Carried
over from `risk.py`, and the reason nothing here renders a label without its number.

## What it does not do

It does not score. Nothing this writes into `data.json` is read by the rule evaluator in
`app.js` — there is a test asserting that. Hazard risk is a caveat (ADR 0009), and it stays
one on the page for the same reason it does in the engine: adding deductions would silently
change scores already recorded.

It does not regenerate the file. Only the `hazards` key is rewritten.

## Usage

    python tools/build_hazard_snapshot.py            # fetch and update data.json
    python tools/build_hazard_snapshot.py --check     # exit 1 if the key is missing/stale

`--check` deliberately does *not* re-fetch. FEMA data changing is not a build failure, and
a CI job that fails when a federal dataset is republished trains people to ignore CI. It
verifies the key exists, is internally consistent, and covers the hazards the profile asks
for. Refreshing is a decision a person makes, and the retrieval date is stamped so staleness
is visible rather than inferred.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from analyzer.sources import http, risk  # noqa: E402  (needs the path above)

PROFILE = REPO / "buyer_profile.toml"
SNAPSHOT = REPO / "data.json"

COUNTY = "Spartanburg"
STATE_ABBRV = "SC"

# Fields whose value is a county-level figure joined onto the tract table. Measured, not
# assumed -- see the module docstring. Anything listed here gets labelled in the UI.
COUNTY_RESOLUTION_FIELDS = ("community_resilience",)


def _hazards_from_profile() -> tuple[tuple[str, ...], float]:
    with open(PROFILE, "rb") as fh:
        profile = tomllib.load(fh)
    block = profile.get("risk", {})
    return tuple(block.get("hazards", ())), float(block.get("caveat_percentile", 90.0))


def _fields(hazards: tuple[str, ...]) -> list[str]:
    return list(risk.BASE_FIELDS) + [
        f"{code}_{suffix}" for code in hazards for suffix in risk.HAZARD_SUFFIXES
    ]


def _query(params: dict[str, Any]) -> list[dict[str, Any]]:
    payload = http.get_json(http.build_url(risk.NRI_QUERY, params)).data
    if "error" in payload:
        raise http.SourceUnavailable(str(payload["error"].get("message", "ArcGIS error")))
    return [f.get("attributes", {}) for f in payload.get("features", [])]


def _spread(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 1),
        "median": round(statistics.median(values), 1),
        "max": round(max(values), 1),
    }


def fetch_county(hazards: tuple[str, ...]) -> dict[str, Any]:
    """Every tract in the county, reduced to a spread per hazard."""
    rows = _query(
        {
            "where": f"STATEABBRV='{STATE_ABBRV}' AND COUNTY='{COUNTY}'",
            "outFields": ",".join(_fields(hazards)),
            "returnGeometry": "false",
            "f": "json",
        }
    )
    if not rows:
        raise http.SourceUnavailable(f"no NRI tracts returned for {COUNTY} County")

    total = len(rows)
    out: dict[str, Any] = {"tract_count": total, "hazards": {}}

    for code in hazards:
        modeled = [
            float(r[f"{code}_RISKS"])
            for r in rows
            if r.get(f"{code}_RISKS") is not None
            and risk.is_modeled(r.get(f"{code}_RISKR"))
        ]
        entry: dict[str, Any] = {
            "label": risk.HAZARD_LABELS[code],
            "modeled_tracts": len(modeled),
            "total_tracts": total,
        }
        # A hazard modeled in a minority of tracts is the trap: the address you looked at
        # reports "unknown" while the county median is high. Carrying both counts lets the
        # page say that out loud instead of showing a reassuring blank.
        if modeled:
            entry.update(_spread(modeled))
        out["hazards"][code] = entry

    for key, field in (
        ("nri_composite_risk", "RISK_SCORE"),
        ("social_vulnerability", "SOVI_SCORE"),
        ("community_resilience", "RESL_SCORE"),
    ):
        vals = [float(r[field]) for r in rows if r.get(field) is not None]
        if vals:
            spread = _spread(vals)
            # Proof rather than assertion: if a future NRI release starts varying
            # resilience by tract, this flag flips on its own and the label follows.
            spread["varies_by_tract"] = len({round(v, 4) for v in vals}) > 1
            out[key] = spread

    versions = {(r.get("NRI_VER") or "").strip() for r in rows} - {""}
    out["nri_version"] = sorted(versions)[0] if len(versions) == 1 else None
    return out


def fetch_zip_centroids(hazards: tuple[str, ...], zips: list[dict[str, Any]]) -> dict:
    """The one tract each ZIP centroid falls in. A sample of the ZIP, not a summary."""
    fields = ",".join(_fields(hazards))
    out: dict[str, Any] = {}

    for feature in zips:
        props = feature.get("properties", {})
        code, centroid = props.get("zip"), props.get("centroid")
        if not code or not centroid:
            continue
        lat, lon = centroid[0], centroid[1]

        rows = _query(
            {
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": fields,
                "returnGeometry": "false",
                "f": "json",
            }
        )
        if not rows:
            continue
        attrs = rows[0]

        entry: dict[str, Any] = {
            "name": props.get("name"),
            "tract_fips": (attrs.get("TRACTFIPS") or "").strip() or None,
            "hazards": {},
        }
        for key, score_f, rating_f in (
            ("nri_composite_risk", "RISK_SCORE", "RISK_RATNG"),
            ("social_vulnerability", "SOVI_SCORE", "SOVI_RATNG"),
            ("community_resilience", "RESL_SCORE", "RESL_RATNG"),
        ):
            score, rating = attrs.get(score_f), attrs.get(rating_f)
            if score is None or not risk.is_modeled(rating):
                continue
            entry[key] = {"percentile": round(float(score), 1), "rating": rating}

        for hcode in hazards:
            score = attrs.get(f"{hcode}_RISKS")
            rating = attrs.get(f"{hcode}_RISKR")
            label = risk.HAZARD_LABELS[hcode]
            if score is None or not risk.is_modeled(rating):
                # Mirrors the station: absent is unknown, and unknown is not low.
                entry["hazards"][hcode] = {"label": label, "modeled": False}
                continue
            entry["hazards"][hcode] = {
                "label": label,
                "modeled": True,
                "percentile": round(float(score), 1),
                "rating": rating,
            }
        out[code] = entry

    return out


def build(snapshot: dict[str, Any]) -> dict[str, Any]:
    hazards, caveat = _hazards_from_profile()
    if not hazards:
        raise SystemExit("buyer_profile.toml has no [risk] hazards to compile")

    zips = snapshot.get("geojson", {}).get("zips", {}).get("features", [])
    county = fetch_county(hazards)

    return {
        "generated_from": "FEMA National Risk Index, census tracts",
        "source_url": risk.NRI_DOC,
        "query_url": risk.NRI_QUERY,
        "nri_version": county.get("nri_version"),
        "retrieved": date.today().isoformat(),
        "county_name": f"{COUNTY} County, {STATE_ABBRV}",
        "caveat_percentile": caveat,
        "hazard_codes": list(hazards),
        "hazard_labels": {c: risk.HAZARD_LABELS[c] for c in hazards},
        "county_resolution_fields": list(COUNTY_RESOLUTION_FIELDS),
        # Carried as data so the page cannot render the numbers without the caveats. Every
        # one of these was measured; none is boilerplate.
        "caveats": [
            "Percentiles are the value. FEMA's five rating labels are binned separately "
            "per hazard, so the same label means different things for different hazards.",
            "A hazard with no rating is not modeled for that tract. That is unknown, not "
            "low risk.",
            "The per-ZIP figure is the single census tract containing the ZIP centroid. "
            "It is a sample of the ZIP, not an average over it.",
            "Community resilience is published per county and joined onto the tract "
            "table, so it does not vary between tracts within the county.",
            "Nothing on this page scores hazard risk. It is reported as a caveat, the "
            "same treatment a pre-2000 build year gets.",
        ],
        "county": county,
        "zips": fetch_zip_centroids(hazards, zips),
    }


def check(snapshot: dict[str, Any]) -> int:
    """Structural, not freshness. See the module docstring for why."""
    block = snapshot.get("hazards")
    if not block:
        print("data.json has no `hazards` key: run tools/build_hazard_snapshot.py", file=sys.stderr)
        return 1

    wanted, _ = _hazards_from_profile()
    have = tuple(block.get("hazard_codes", ()))
    if have != wanted:
        print(
            f"data.json hazards {have} do not match buyer_profile.toml {wanted}.\n"
            "Run `python tools/build_hazard_snapshot.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    missing = [c for c in wanted if c not in block.get("county", {}).get("hazards", {})]
    if missing:
        print(f"county distribution is missing hazards {missing}", file=sys.stderr)
        return 1

    print(
        f"data.json hazards cover {len(have)} codes across "
        f"{block['county']['tract_count']} tracts, NRI {block.get('nri_version')}, "
        f"retrieved {block.get('retrieved')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the hazards key without re-fetching from FEMA",
    )
    args = parser.parse_args()

    snapshot = json.loads(SNAPSHOT.read_text())
    if args.check:
        return check(snapshot)

    snapshot["hazards"] = build(snapshot)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    block = snapshot["hazards"]
    print(
        f"wrote hazards for {len(block['hazard_codes'])} codes: "
        f"{block['county']['tract_count']} county tracts, "
        f"{len(block['zips'])} ZIP centroids, NRI {block['nri_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
