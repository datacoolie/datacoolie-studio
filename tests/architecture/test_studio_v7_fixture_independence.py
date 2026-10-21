"""Keep standalone Studio v7 consumer tests independent of the producer package."""

from __future__ import annotations

import ast
from pathlib import Path


_STUDIO_ROOT = Path(__file__).parents[1]
_STANDALONE_SOURCES = (
    _STUDIO_ROOT / "domains" / "analytics" / "test_framework_v7_observed_metrics.py",
    _STUDIO_ROOT / "domains" / "analytics_upgrade" / "test_v7_observed_metrics_recovery.py",
    _STUDIO_ROOT / "domains" / "monitoring" / "test_v7_observed_metrics.py",
    _STUDIO_ROOT / "support" / "v7_fixture.py",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_v7_consumer_tests_do_not_import_producer_package() -> None:
    """The Studio test suite must run when only Studio is installed."""

    violations = {
        f"{path.relative_to(_STUDIO_ROOT)}: {module}"
        for path in _STANDALONE_SOURCES
        for module in _imported_modules(path)
        if module == "datacoolie" or module.startswith("datacoolie.")
    }
    assert not violations, "standalone Studio sources import DataCoolie: " + ", ".join(
        sorted(violations)
    )
