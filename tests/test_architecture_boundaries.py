from __future__ import annotations

import ast
from pathlib import Path


DOMAINS_ROOT = Path(__file__).parents[1] / "src" / "datacoolie_studio" / "domains"
DOMAIN_PREFIX = ("datacoolie_studio", "domains")


def test_analytics_does_not_import_logs_or_monitoring() -> None:
    edges = _domain_import_edges("analytics")

    assert not (edges & {("analytics", "logs"), ("analytics", "monitoring")})


def test_logs_does_not_import_monitoring() -> None:
    edges = _domain_import_edges("logs")

    assert ("logs", "monitoring") not in edges


def _domain_import_edges(source_domain: str) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for source_file in (DOMAINS_ROOT / source_domain).rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = _imported_domain(node.module)
                if target:
                    edges.add((source_domain, target))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = _imported_domain(alias.name)
                    if target:
                        edges.add((source_domain, target))
    return edges


def _imported_domain(module: str) -> str | None:
    parts = tuple(module.split("."))
    if len(parts) < 3 or parts[:2] != DOMAIN_PREFIX:
        return None
    return parts[2]
