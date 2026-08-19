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

    render_hazards(doc.get("hazard_profile"))

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


def render_hazards(profile: dict | None) -> None:
    """FEMA National Risk Index for the census tract, printed after the money.

    Deliberately below Monthly and above the task list, because that is what this data
    is: it does not change the score, it changes what you go ask an insurance agent.

    Percentiles are shown and FEMA's rating labels are not, with one exception. The
    labels are binned separately for each hazard, so a "Relatively Moderate" wildfire
    rating and a "Relatively Moderate" heat rating are nowhere near the same national
    position — printing them side by side in a column would invite exactly the wrong
    comparison. The number is the comparable thing.
    """
    if not profile:
        return

    hazards = profile.get("hazards") or {}
    modeled = {c: h for c, h in hazards.items() if h.get("modeled")}
    unmodeled = [h["label"] for h in hazards.values() if not h.get("modeled")]
    if not modeled and not unmodeled and not profile.get("nri_composite_risk"):
        return

    tract = profile.get("tract_fips") or "unknown tract"
    print(f"\n  {BOLD}Hazard risk{OFF} {DIM}(FEMA NRI · tract {tract} · no score effect){OFF}")

    for label, key in (
        ("Social vulnerability", "social_vulnerability"),
        ("Community resilience", "community_resilience"),
    ):
        entry = profile.get(key)
        if entry and entry.get("percentile") is not None:
            print(
                f"    {label:<22} {entry['percentile']:>5.1f}"
                f"  {DIM}{entry.get('rating', '')}{OFF}"
            )

    # Sorted worst-first: the reason to read this section is to find the one hazard that
    # is out of line, and an alphabetical list buries it.
    ranked = sorted(modeled.values(), key=lambda h: h["percentile"], reverse=True)
    for hazard in ranked:
        pct = hazard["percentile"]
        colour = RED if pct >= 90 else GOLD if pct >= 75 else ""
        # OFF only when a colour was actually opened. Emitting a reset unconditionally
        # leaves a visible escape sequence in any terminal or log that is not
        # interpreting ANSI, which is most places this output gets pasted.
        suffix = OFF if colour else ""
        print(f"    {hazard['label']:<22} {colour}{pct:>5.1f}{suffix}")

    if unmodeled:
        print(
            f"    {DIM}not modeled here: {', '.join(sorted(unmodeled))} "
            f"— unknown, not low{OFF}"
        )

    # FEMA's composite risk index is deliberately not printed as a headline number. It
    # averages all 18 hazards, and most hazards do not apply to any given place, so a
    # tract can be extreme in the one hazard that will actually happen to it and still
    # rate low overall. The tract containing Paradise, California scores 32nd percentile
    # composite and 95th for wildfire. The composite is only worth surfacing when it
    # disagrees with the worst hazard, and then only to say so out loud.
    #
    # Both conditions are required, and the second one was added because the first alone
    # was wrong. A gap test by itself fires on Spartanburg, where the composite reads 18.6
    # and the worst hazard is hail at 51.7 — a 33-point gap and nothing anyone needs to
    # act on. Warning that a composite "understates" a middling hazard is noise, and noise
    # in a caveat channel teaches people to skip the caveats. The note only earns its
    # place when the hidden hazard is genuinely elevated.
    composite = profile.get("nri_composite_risk") or {}
    composite_pct = composite.get("percentile")
    if composite_pct is not None and ranked:
        worst = ranked[0]
        if worst["percentile"] >= 75.0 and worst["percentile"] - composite_pct >= 25.0:
            print(
                f"    {DIM}FEMA's all-hazard composite reads {composite_pct:.1f} for this "
                f"tract, which understates {worst['label']} at "
                f"{worst['percentile']:.1f} — the composite averages 18 hazards, most of "
                f"which do not apply here{OFF}"
            )


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
