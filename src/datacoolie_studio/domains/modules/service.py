"""Resolve and persist Studio capability module enablement."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import StudioModuleState
from datacoolie_studio.domains.modules.catalog import (
    MODULE_CATALOG,
    ModuleDefinition,
    module_by_key,
)


def _resolve_enabled(definition: ModuleDefinition, override: bool | None) -> bool:
    if definition.status == "coming_soon":
        return False
    if override is None:
        return definition.default_enabled
    return override


def _to_dict(definition: ModuleDefinition, enabled: bool) -> dict[str, Any]:
    return {
        "key": definition.key,
        "name": definition.name,
        "description": definition.description,
        "group": definition.group,
        "status": definition.status,
        "togglable": definition.togglable,
        "default_enabled": definition.default_enabled,
        "pages": list(definition.pages),
        "enabled": enabled,
    }


def _state_overrides(session: Session) -> dict[str, bool]:
    return {
        state.key: state.enabled
        for state in session.scalars(select(StudioModuleState)).all()
    }


def list_modules(session: Session) -> list[dict[str, Any]]:
    """Return the catalog merged with persisted enable/disable state."""

    overrides = _state_overrides(session)
    return [
        _to_dict(definition, _resolve_enabled(definition, overrides.get(definition.key)))
        for definition in MODULE_CATALOG
    ]


def get_module(session: Session, key: str) -> dict[str, Any] | None:
    """Return a single resolved module, or ``None`` when ``key`` is unknown."""

    definition = module_by_key(key)
    if definition is None:
        return None
    override = session.get(StudioModuleState, key)
    enabled = _resolve_enabled(definition, override.enabled if override else None)
    return _to_dict(definition, enabled)


def set_module_enabled(session: Session, key: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable a togglable module and return its resolved state."""

    definition = module_by_key(key)
    if definition is None:
        raise KeyError(key)
    if not definition.togglable:
        raise ValueError(f"Module '{key}' cannot be toggled")

    state = session.get(StudioModuleState, key)
    if state is None:
        state = StudioModuleState(key=key, enabled=enabled)
        session.add(state)
    else:
        state.enabled = enabled
    session.commit()
    return _to_dict(definition, _resolve_enabled(definition, enabled))


def enabled_module_keys(session: Session) -> set[str]:
    """Return the set of currently enabled module keys."""

    return {module["key"] for module in list_modules(session) if module["enabled"]}
