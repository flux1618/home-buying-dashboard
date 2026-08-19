"""Enforces ADR 0002: the scoring core imports nothing that touches a network.

Without this test the architecture decision is a comment in a markdown file. With it,
CI fails the moment someone adds `import requests` to a scoring module — which is the
exact shortcut that turns a testable engine back into an untestable one.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

CORE = pathlib.Path(__file__).resolve().parents[1] / "analyzer" / "core"

FORBIDDEN = {
    # network
    "requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp", "ftplib",
    # frameworks
    "fastapi", "flask", "starlette", "uvicorn", "django",
    # models
    "openai", "anthropic", "ollama", "transformers", "litellm",
    # db
    "sqlite3", "psycopg2", "sqlalchemy", "asyncpg",
    # third-party config we deliberately avoid in favour of stdlib tomllib
    "yaml", "pydantic",
}

CORE_MODULES = sorted(CORE.glob("*.py"))


def top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_core_has_modules_to_check():
    assert CORE_MODULES, "no core modules found — the check would vacuously pass"


@pytest.mark.parametrize("module", CORE_MODULES, ids=lambda p: p.name)
def test_core_module_imports_nothing_forbidden(module: pathlib.Path):
    offending = top_level_imports(module) & FORBIDDEN
    assert not offending, (
        f"{module.name} imports {sorted(offending)}, violating ADR 0002. "
        f"The scoring core must stay pure — put network calls in a source adapter."
    )


def test_core_is_importable_with_no_third_party_deps():
    """Smoke test: a full analysis runs on a stdlib-only interpreter."""
    from datetime import date

    from analyzer.core.analyze import analyze
    from analyzer.core.profile import load_profile
    from analyzer.core.scoring import PropertyFacts

    doc = analyze(
        PropertyFacts(price=300_000, sqft=1600, beds=3, baths=3, garage_spaces=2,
                      year_built=2015, flood_zone="X", water_sewer="public",
                      commute_min=15.0, fiber_available=True),
        load_profile(),
        date.today().year,
        address="test",
    )
    assert doc["score"]["verdict"] == "TAKE"
