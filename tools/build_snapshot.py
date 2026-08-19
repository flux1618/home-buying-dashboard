"""Compile the buyer profile into the static snapshot's rule block.

## Why this exists

The public page had its own copy of the scoring rules, hand-written in JavaScript. It had
drifted from the Python engine badly enough to give opposite answers:

| Rule | page said | engine says |
|---|---|---|
| HOA above $100/mo | hard fail, score 0, PASS | -25 deduction |
| Roof at 17 years | note, no points | -25 |
| HVAC at 14 years | note, no points | -15 |
| WATCH floor | 50 | 45 |
| Facts unknown | silently scored as perfect | capped below TAKE |

On 606 Andre Ct that is a 40-point gap: the page showed a confident TAKE on the exact house
the engine scores 52 WATCH. Nobody had done anything wrong — two implementations of the same
rules simply drifted, which is what two implementations of anything always do.

ADR 0007 made the CLI, the batch runner, and the API share one implementation. The browser
was the door that got missed, and it cannot import Python. So the rules are *compiled* out of
`buyer_profile.toml` into `data.json`, and `app.js` evaluates that data rather than
restating it. The page can still be wrong about how it renders a rule; it can no longer
disagree about what the rule is.

## What it does not do

It does not port the engine to JavaScript. Tax millage, amortisation, insurance proration,
maintenance-reserve methods, and the source stations all stay in Python. The browser gets
the parts that are genuinely declarative -- threshold comparisons and verdict bands -- and
the page links to the local engine for everything else. Extending this into a full
reimplementation would recreate the exact problem it was written to fix.

## Usage

    python tools/build_snapshot.py            # update data.json in place
    python tools/build_snapshot.py --check    # exit 1 if stale, used by CI

It *augments* rather than regenerates: market data, GeoJSON, and drive-time matrices in
`data.json` are left untouched. Only the `rules` key is rewritten, so running this is safe
and idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PROFILE = REPO / "buyer_profile.toml"
SNAPSHOT = REPO / "data.json"

# Mirrors analyzer/core/capex.py. Duplicated deliberately and narrowly: importing the
# analyzer here would make the build script depend on the package being installed, and
# these five numbers are covered by a parity test in tests/test_snapshot_rules.py.
ROOF_BANDS = [
    {"max_sqft": 1500, "low": 6000, "high": 14000},
    {"max_sqft": 2500, "low": 8000, "high": 18000},
    {"max_sqft": None, "low": 12000, "high": 24000},
]
ROOF_UNKNOWN = {"low": 6000, "high": 19000}
HVAC_BANDS = [
    {"max_sqft": 1750, "low": 6000, "high": 10000},
    {"max_sqft": 2250, "low": 7000, "high": 12000},
    {"max_sqft": 2750, "low": 8000, "high": 14000},
    {"max_sqft": None, "low": 9000, "high": 16000},
]
HVAC_UNKNOWN = {"low": 7500, "high": 14500}

ROOF_SRC = "https://www.thisoldhouse.com/roofing/roof-replacement-cost-south-carolina"
HVAC_SRC = (
    "https://www.usatoday.com/story/money/home-services/hvac-replacement-cost/90313725007/"
)


def build_rules(profile: dict[str, Any]) -> dict[str, Any]:
    """Turn the profile into something a browser can evaluate without interpreting TOML."""
    hard = profile["hard_fails"]
    pref = profile["preferences"]
    pen = pref["penalties"]
    capex = profile["capital_expenses"]
    cpen = capex["penalties"]
    cav = profile["caveats"]
    verdict = profile["verdict"]

    return {
        "profile_name": profile["name"],
        # Stamped so a stale page is self-evident rather than something you have to diff.
        "generated_from": "buyer_profile.toml",
        "engine_parity_note": (
            "Thresholds and weights are compiled from buyer_profile.toml by "
            "tools/build_snapshot.py. They are not retyped here."
        ),
        "verdict": {
            "take_min": verdict["take_min"],
            "watch_min": verdict["watch_min"],
            "unevaluated_score": verdict["unevaluated_score"],
        },
        # Disqualifying. Ordered so the rendered list reads the way the engine evaluates.
        "hard_fails": [
            {
                "id": "commute",
                "label": f"Commute over {hard['max_commute_min']} minutes",
                "threshold": hard["max_commute_min"],
            },
            {
                "id": "water_sewer",
                "label": "Well or septic rather than public water and sewer",
                "threshold": None,
            },
            {
                "id": "flood",
                "label": "FEMA special flood hazard area "
                + f"({', '.join(hard['exclude_flood_zones'])})",
                "threshold": None,
            },
        ],
        # Scored but survivable. `points` is what the engine actually deducts.
        "deductions": [
            {
                "id": "hoa",
                "compare": "greater_than",
                "threshold": pref["max_hoa_monthly"],
                "points": pen["hoa_over_max"],
                "label": f"HOA above ${pref['max_hoa_monthly']}/mo",
                # Spelled out because the page previously treated this as a hard fail, and
                # the distinction is the whole reason this file exists.
                "note": "A deduction, not a deal-breaker.",
            },
            {
                "id": "beds",
                "compare": "less_than",
                "threshold": pref["min_beds"],
                "points": pen["beds_under"],
                "label": f"Fewer than {pref['min_beds']} bedrooms",
            },
            {
                "id": "sqft",
                "compare": "less_than",
                "threshold": pref["min_sqft"],
                "points": pen["sqft_under"],
                "label": f"Under {pref['min_sqft']:,} heated sqft",
            },
            {
                "id": "fiber",
                "compare": "is_false",
                "threshold": None,
                "points": pen["no_fiber"],
                "label": "No fiber available",
            },
            {
                "id": "garage",
                "compare": "less_than",
                "threshold": pref["min_garage_spaces"],
                "points": pen["garage_under"],
                "label": f"Fewer than {pref['min_garage_spaces']} garage bays",
            },
            {
                "id": "baths",
                "compare": "less_than",
                "threshold": pref["min_baths"],
                "points": pen["baths_under"],
                "label": f"Fewer than {pref['min_baths']} bathrooms",
            },
        ],
        # Aging systems with a bill attached. Two tiers each: due, then overdue.
        "capital_expenses": [
            {
                "id": "roof",
                "component": "Roof replacement",
                "due_age": capex["roof_due_age"],
                "overdue_age": capex["roof_overdue_age"],
                "due_points": cpen["roof_due"],
                "overdue_points": cpen["roof_overdue"],
                "bands": ROOF_BANDS,
                "unknown_sqft": ROOF_UNKNOWN,
                "source_url": ROOF_SRC,
            },
            {
                "id": "hvac",
                "component": "HVAC replacement",
                "due_age": capex["hvac_due_age"],
                "overdue_age": capex["hvac_overdue_age"],
                "due_points": cpen["hvac_due"],
                "overdue_points": cpen["hvac_overdue"],
                "bands": HVAC_BANDS,
                "unknown_sqft": HVAC_UNKNOWN,
                "source_url": HVAC_SRC,
            },
        ],
        # Flagged, never scored. Build year lives here on purpose: an older house is a
        # prompt to look closer, not evidence of a worse house.
        "caveats": {
            "preferred_year_built_min": cav["preferred_year_built_min"],
            "max_price_over_target_pct": cav["max_price_over_target_pct"],
            "max_price_per_sqft": cav["max_price_per_sqft"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if data.json is stale instead of rewriting it",
    )
    args = parser.parse_args()

    with open(PROFILE, "rb") as fh:
        profile = tomllib.load(fh)
    rules = build_rules(profile)

    snapshot = json.loads(SNAPSHOT.read_text())

    if args.check:
        if snapshot.get("rules") != rules:
            print(
                "data.json is stale: buyer_profile.toml has changed since it was built.\n"
                "Run `python tools/build_snapshot.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("data.json rules match buyer_profile.toml")
        return 0

    snapshot["rules"] = rules
    # Trailing newline and stable key order so the diff is readable and re-running with no
    # profile change produces no diff at all.
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    print(f"wrote rules for {rules['profile_name']!r} into {SNAPSHOT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
