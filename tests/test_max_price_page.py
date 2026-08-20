"""The page's max-price solver across the full range of the household gross slider.

The slider spans $50K to $2M. The right edge is reachable in the UI: at a 22% cap the
search ceiling starts binding around $1.99M of gross income, and at the slider's 36% cap
it binds from about $1.22M, so the top third of the range can land there. The left edge no
longer reaches $0 income, but the zero-income guard stays tested -- `solveMaxPrice` is
called from `renderMaxPrice` with whatever the DOM holds, and a future slider bound, a URL
parameter or a hand-edited input should not be able to divide by zero unnoticed.

The right edge is the one that matters. `analyzer/core/cost.py` bisects up to a
`_SOLVER_CEILING` and, on reaching it, attaches a note saying the ceiling was reached
rather than presenting the bracket edge as a solved maximum. The page duplicates that
solver for the live sliders (ADR 0008), so it has to duplicate the honesty too -- and it
has to use the *same* ceiling, or the two would disagree about where the search stops.
That constant is asserted here against the Python one so a change to either side fails.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from analyzer.core import cost

REPO = Path(__file__).resolve().parents[1]

HARNESS = r"""
global.DATA = {global:{tax:{typical_owner_millage_mills:__MILLS__,
  primary_assessment_ratio:__RATIO__},insurance:{sc_avg_annual:__INS__}}};
const src = require('fs').readFileSync(__APPJS__,'utf8');
eval(src.match(/function pmt\([\s\S]*?\n}\n/)[0]);
eval(src.match(/function pitiParts\([\s\S]*?\n}\n/)[0]);
eval(src.match(/function solveMaxPrice\([\s\S]*?\n}\n/)[0]);
console.log(JSON.stringify(
  __INCOMES__.map(inc => solveMaxPrice(inc, __DOWN__, __RATE__, __HOA__, __DTI__))
));
"""


def solve_in_browser(incomes, down=80_000.0, rate=0.0667, hoa=0.0, dti_pct=22.0):
    node = shutil.which("node")
    if not node:
        if os.environ.get("HBA_REQUIRE_NODE"):
            pytest.fail("HBA_REQUIRE_NODE is set but node is not installed; page checks would have been skipped")
        pytest.skip("node not installed; page solver unchecked in this environment")
    snapshot = json.loads((REPO / "data.json").read_text())
    script = (
        HARNESS.replace("__MILLS__", str(snapshot["global"]["tax"]["typical_owner_millage_mills"]))
        .replace("__RATIO__", str(snapshot["global"]["tax"]["primary_assessment_ratio"]))
        .replace("__INS__", str(snapshot["global"]["insurance"]["sc_avg_annual"]))
        .replace("__APPJS__", json.dumps(str(REPO / "app.js")))
        .replace("__INCOMES__", json.dumps(incomes))
        .replace("__DOWN__", repr(down))
        .replace("__RATE__", repr(rate))
        .replace("__HOA__", repr(hoa))
        .replace("__DTI__", repr(dti_pct))
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, cwd=REPO)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestSliderRange:
    def test_the_income_slider_spans_fifty_thousand_to_two_million(self):
        html = (REPO / "index.html").read_text()
        tag = re.search(r'<input id="i-inc"[^>]*>', html).group(0)
        assert 'min="50000"' in tag
        assert 'max="2000000"' in tag

    def test_the_slider_resolves_finer_than_ten_thousand_dollars_a_pixel(self):
        # Not decoration. The household's own income is ~$406K, and a range wide enough to
        # make that unreachable by dragging is a range that cannot answer the question the
        # page exists for. 249px of track is what the sidebar gives at 1600px wide.
        html = (REPO / "index.html").read_text()
        tag = re.search(r'<input id="i-inc"[^>]*>', html).group(0)
        lo = float(re.search(r'min="(\d+)"', tag).group(1))
        hi = float(re.search(r'max="(\d+)"', tag).group(1))
        assert (hi - lo) / 249 < 10_000

    def test_the_page_solver_ceiling_matches_the_engine(self):
        # Two solvers, one number. If either side moves, they stop agreeing about where the
        # search stops, and the page's "ceiling reached" message names the wrong figure.
        js = (REPO / "app.js").read_text()
        literal = re.search(r"const SOLVER_CEILING = (\d+);", js).group(1)
        assert float(literal) == cost._SOLVER_CEILING


class TestZeroIncome:
    """Not slider-reachable at the current bounds. Kept because the guard is the reason."""

    def test_zero_income_is_undefined_not_infeasible(self):
        # Not the "fixed costs are the binding constraint" answer: nothing about a cheaper
        # house or a smaller loan is the fix when the denominator is the problem.
        (s,) = solve_in_browser([0])
        assert s["feasible"] is False
        assert s["noIncome"] is True

    def test_a_dollar_of_income_stops_being_a_special_case(self):
        (s,) = solve_in_browser([12_000])
        assert s["feasible"] is False
        assert "noIncome" not in s
        assert s["floor"] > 0


class TestHighIncome:
    def test_the_top_of_the_slider_reports_the_search_ceiling_rather_than_a_maximum(self):
        # At the slider's loose end of the DTI range the ceiling is reached with room to
        # spare, so this is the stable case to assert on.
        (s,) = solve_in_browser([2_000_000], dti_pct=36.0)
        assert s["feasible"] is True
        assert s["capped"] is True
        assert s["price"] == cost._SOLVER_CEILING

    def test_a_higher_dti_setting_reaches_the_ceiling_earlier(self):
        # The cap is a function of both sliders, so the 36% end of the DTI slider pulls the
        # crossover down well inside the income range. Documented, not incidental.
        (tight,) = solve_in_browser([1_500_000], dti_pct=22.0)
        (loose,) = solve_in_browser([1_500_000], dti_pct=36.0)
        assert tight.get("capped") is not True
        assert loose["capped"] is True

    def test_the_top_of_the_slider_sits_on_the_ceiling_boundary_at_the_default_cap(self):
        # Worth pinning rather than leaving as a surprise: at 22% and $2M the crossover is
        # around $1.99M of income, so a single 5bp step of the rate slider flips the answer
        # between a solved maximum and "ceiling reached". Both readings are correct; the
        # point is that the boundary lives inside the slider's range, not outside it.
        (at_page_default,) = solve_in_browser([2_000_000], rate=0.0667, dti_pct=22.0)
        (one_step_up,) = solve_in_browser([2_000_000], rate=0.0672, dti_pct=22.0)
        assert at_page_default["capped"] is True
        assert one_step_up.get("capped") is not True
        assert one_step_up["price"] > 0.9 * cost._SOLVER_CEILING

    def test_the_engine_flags_the_same_ceiling_in_its_notes(self):
        from analyzer.core.profile import load_profile
        from dataclasses import replace

        profile = load_profile()
        profile = replace(profile, gross_annual_income=2_000_000.0, down_payment=80_000.0)
        s = cost.solve_max_price(profile, sqft=1650, hoa_monthly=0.0, current_year=2026)
        assert s.lender_max_price == cost._SOLVER_CEILING
        assert any("ceiling" in n for n in s.notes)

    def test_the_solved_answer_still_rises_with_income_below_the_ceiling(self):
        # The cap must be the only thing flattening the curve, not a bug in the bracket.
        results = solve_in_browser([200_000, 400_000, 800_000])
        prices = [r["price"] for r in results]
        assert all(r["feasible"] for r in results)
        assert not any(r.get("capped") for r in results)
        assert prices == sorted(prices)
        assert len(set(prices)) == 3
