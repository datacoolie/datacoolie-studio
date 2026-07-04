"""Catalog of Studio capability modules.

A *capability module* is a feature package that groups one or more
environment-scoped navigation pages and can be enabled or disabled at the
Studio level. Core platform navigation (projects, environments, sources,
settings) is always available and is intentionally not modeled here.

This module is the single source of truth for which capability modules exist,
how they are grouped, and their default enablement. Persisted enable/disable
state lives in the ``studio_module_states`` table and is resolved against these
definitions by :mod:`datacoolie_studio.domains.modules.service`.
"""

from __future__ import annotations

from dataclasses import dataclass

ModuleStatus = str  # "available" | "coming_soon"


@dataclass(frozen=True)
class ModuleDefinition:
    """Static definition of a Studio capability module."""

    key: str
    name: str
    description: str
    group: str
    status: ModuleStatus
    togglable: bool
    default_enabled: bool
    pages: tuple[str, ...]


MODULE_CATALOG: tuple[ModuleDefinition, ...] = (
    ModuleDefinition(
        key="metadata",
        name="Metadata",
        description=(
            "Inspect and edit metadata, view stitched lineage, and monitor ETL "
            "execution from local run logs."
        ),
        group="Data Intelligence",
        status="available",
        togglable=True,
        default_enabled=True,
        pages=("metadata", "assets", "lineage", "monitoring"),
    ),
    ModuleDefinition(
        key="master-data",
        name="Master Data",
        description=(
            "Define database connections and manage centralized reference tables "
            "instead of scattered spreadsheets. Coming soon."
        ),
        group="Data Intelligence",
        status="coming_soon",
        togglable=False,
        default_enabled=False,
        pages=("master-data",),
    ),
)

_CATALOG_BY_KEY: dict[str, ModuleDefinition] = {
    definition.key: definition for definition in MODULE_CATALOG
}


def module_by_key(key: str) -> ModuleDefinition | None:
    """Return the module definition for ``key`` or ``None`` when unknown."""

    return _CATALOG_BY_KEY.get(key)
