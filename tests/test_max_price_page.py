"""The page's max-price solver across the full range of the household gross slider.

The slider now spans $0 to $10M, which walks the solver into two edges that the old
$150K-$600K range could not reach: a zero denominator on the left, and a DTI ceiling so
high that no price inside the search bracket ever breaches it on the right. Neither edge
is hypothetical any more, so both get a test.

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


def solve_in_browser(incomes, down=80_000.0, rate=0.067, hoa=0.0, dti_pct=22.0):
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
    def test_the_income_slider_spans_zero_to_ten_million(self):
        html = (REPO / "index.html").read_text()
        tag = re.search(r'<input id="i-inc"[^>]*>', html).group(0)
        assert 'min="0"' in tag
        assert 'max="10000000"' in tag

    def test_the_page_solver_ceiling_matches_the_engine(self):
        # Two solvers, one number. If either side moves, they stop agreeing about where the
        # search stops, and the page's "ceiling reached" message names the wrong figure.
        js = (REPO / "app.js").read_text()
        literal = re.search(r"const SOLVER_CEILING = (\d+);", js).group(1)
        assert float(literal) == cost._SOLVER_CEILING


class TestZeroIncome:
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
    def test_ten_million_reports_the_search_ceiling_rather_than_a_maximum(self):
        (s,) = solve_in_browser([10_000_000])
        assert s["feasible"] is True
        assert s["capped"] is True
        assert s["price"] == cost._SOLVER_CEILING

    def test_the_engine_flags_the_same_ceiling_in_its_notes(self):
        from analyzer.core.profile import load_profile
        from dataclasses import replace

        profile = load_profile()
        profile = replace(profile, gross_annual_income=10_000_000.0, down_payment=80_000.0)
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
