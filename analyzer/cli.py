"""Command line entry point. `python -m analyzer.cli "<address>" <price>`.

Prints the human-readable analysis, or the whole result document with --json.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core.profile import load_profile
from .pipeline import PipelineAborted, run

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"
GREEN, GOLD, RED = "\033[32m", "\033[33m", "\033[31m"

COLOURS = {"TAKE": GREEN, "WATCH": GOLD, "PASS": RED}


def render(doc: dict) -> None:
    score = doc["score"]
    verdict = score["verdict"]
    colour = COLOURS.get(verdict, "")

    location = doc.get("location", {})
    print(f"\n{BOLD}{location.get('matched_address') or doc['input']['address']}{OFF}")
    if location:
        print(
            f"{DIM}{location['latitude']}, {location['longitude']}"
            f"{'  ·  block ' + location['census_block_geoid'] if location.get('census_block_geoid') else ''}{OFF}"
        )

    pinned = "  (pinned — unresolved inputs)" if score.get("score_pinned") else ""
    print(f"\n  {BOLD}Score {colour}{score['value']}{OFF}  →  {colour}{verdict}{OFF}{pinned}")

    stations = doc.get("stations", {})
    if stations.get("degraded"):
        print(f"\n  {GOLD}Degraded stations:{OFF} {', '.join(stations['degraded'])}")
        for entry in doc.get("degraded_sources", []):
            print(f"    {DIM}{entry['station']}: {entry['reason']}{OFF}")

    for label, key in (("Hard fails", "hard_fails"), ("Unresolved", "unevaluated_hard_fails")):
        if score.get(key):
            print(f"\n  {BOLD}{label}{OFF}")
            for item in score[key]:
                print(f"    {RED}·{OFF} {item}")

    if score.get("deductions"):
        print(f"\n  {BOLD}Deductions{OFF} {DIM}(-{score['total_deducted']}){OFF}")
        for entry in score["deductions"]:
            print(f"    {GOLD}-{entry['points']:<3}{OFF} {entry['reason']}")

    if score.get("capital_expenses"):
        print(f"\n  {BOLD}Near-term capital expenses{OFF} {DIM}(-{score['capex_deducted']}){OFF}")
        for entry in score["capital_expenses"]:
            print(
                f"    {GOLD}-{entry['points_deducted']:<3}{OFF} {entry['component']}  "
                f"${entry['estimate_low']:,.0f}-${entry['estimate_high']:,.0f}"
            )

    if score.get("caveats"):
        print(f"\n  {BOLD}Caveats{OFF} {DIM}(no points){OFF}")
        for caveat in score["caveats"]:
            print(f"    {DIM}·{OFF} {caveat}")

    cost = doc["cost"]
    print(f"\n  {BOLD}Monthly{OFF}")
    print(f"    PITI            ${cost['piti']:>10,.0f}   front-end DTI {cost['front_end_dti']*100:.1f}%")
    print(f"    True monthly    ${cost['true_monthly_low']:>10,.0f} - ${cost['true_monthly_high']:,.0f}")
    print(f"    Cash to close   ${cost['cash_to_close']:>10,.0f}")

    blocking = [t for t in doc["verification_tasks"] if t.get("blocking")]
    advisory = [t for t in doc["verification_tasks"] if not t.get("blocking")]
    print(f"\n  {BOLD}Before an offer{OFF} {DIM}({len(blocking)} blocking){OFF}")
    for task in blocking:
        print(f"    {RED}!{OFF} {task['task']}")
        if task.get("reason"):
            print(f"      {DIM}{task['reason']}{OFF}")
    if advisory:
        print(f"\n  {BOLD}Worth doing{OFF} {DIM}({len(advisory)}){OFF}")
        for task in advisory:
            print(f"    {DIM}·{OFF} {task['task']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse one property end to end.")
    parser.add_argument("address")
    parser.add_argument("price", type=float)
    parser.add_argument("--hoa", type=float, default=0.0, help="monthly HOA dues")
    parser.add_argument("--roof-age", type=int, default=None)
    parser.add_argument("--hvac-age", type=int, default=None)
    parser.add_argument("--garage", type=int, default=None, help="garage bay count")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    profile = load_profile(args.profile) if args.profile else load_profile()

    try:
        result = run(
            args.address,
            args.price,
            profile=profile,
            hoa_monthly=args.hoa,
            roof_age_years=args.roof_age,
            hvac_age_years=args.hvac_age,
            garage_spaces=args.garage,
        )
    except PipelineAborted as exc:
        print(f"{RED}Could not analyse this address:{OFF} {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.document, indent=2))
    else:
        render(result.document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
