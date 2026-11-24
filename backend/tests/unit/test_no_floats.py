"""Floats are banned from financial domain code (FINANCIAL_CALCULATIONS.md §1).

Ruff has no rule for this, so the source is checked here: no float literals, no ``float``
calls and no ``math`` module (which works in floats) in any pure calculation package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
FINANCIAL_PACKAGES = (
    APP / "modules" / "loans" / "engine",
    APP / "modules" / "affordability" / "domain",
    APP / "modules" / "applicants" / "domain",
    APP / "modules" / "applications" / "domain",
    APP / "modules" / "decisions" / "domain",
)
SOURCES = sorted(path for package in FINANCIAL_PACKAGES for path in package.rglob("*.py"))


def _float_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            problems.append(f"line {node.lineno}: float literal {node.value!r}")
        elif isinstance(node, ast.Name) and node.id == "float":
            problems.append(f"line {node.lineno}: use of float")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            modules = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            if "math" in modules:
                problems.append(f"line {node.lineno}: math module")
    return problems


def test_packages_exist() -> None:
    assert all(package.is_dir() for package in FINANCIAL_PACKAGES)
    assert len(SOURCES) >= 14


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(APP)))
def test_no_floats_in_financial_domain_code(path: Path) -> None:
    assert _float_uses(path) == []


def test_the_checker_catches_floats(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("import math\nrate = 0.1\nx = float('1')\n", encoding="utf-8")
    assert len(_float_uses(sample)) == 3
