from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from datacoolie_studio.domains.assets.endpoint_identity import (
    asset_seed_sort_key,
    primary_asset_identifier,
)
from datacoolie_studio.domains.assets.identifiers import canonical_asset_id, database_resolution_scope


@dataclass
class _AssetGroup:
    id: str
    node: dict[str, Any]
    identifiers: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    resolution_scopes: set[str] = field(default_factory=set)
    metadata_source_ids: set[int] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    connection_names: set[str] = field(default_factory=set)
    observations: list[dict[str, Any]] = field(default_factory=list)


class AssetRegistry:
    def __init__(self, environment_id: int):
        self.environment_id = environment_id
        self._groups: dict[str, _AssetGroup] = {}
        self._identifier_to_group: dict[tuple[str, str], str] = {}
        self._candidate_to_groups: dict[str, set[str]] = {}
        self.diagnostics: list[dict[str, Any]] = []

    def add_metadata_assets(
        self,
        nodes: list[dict[str, Any]],
        source_id: int,
        source_uri: str,
    ) -> list[str]:
        """Seed a batch in stable identifier order.

        Lineage historically fed nodes in document/source order.  The shared
        identity contract needs the same aliases to produce the same group
        regardless of that order, so callers with a batch should use this
        entrypoint instead of repeatedly choosing a first seed themselves.
        """
        return [
            self.add_metadata_asset(node, source_id, source_uri)
            for node in sorted(nodes, key=asset_seed_sort_key)
        ]

    def add_metadata_observations(
        self,
        observations: Iterable[tuple[dict[str, Any], int, str]],
    ) -> list[str]:
        """Seed metadata from all documents in one deterministic order."""
        ordered = sorted(
            observations,
            key=lambda item: (
                asset_seed_sort_key(item[0]),
                int(item[1]),
                str(item[2]),
            ),
        )
        return [
            self.add_metadata_asset(node, int(source_id), str(source_uri))
            for node, source_id, source_uri in ordered
        ]

    def add_metadata_asset(self, node: dict[str, Any], source_id: int, source_uri: str) -> str:
        identifiers = _identifier_map(node.get("identifiers", []))
        candidate_id = str(node["id"])
        matched_ids = {
            self._identifier_to_group[key]
            for key in identifiers
            if key in self._identifier_to_group
        }

        if len(matched_ids) > 1:
            return self._record_conflict(node, source_id, source_uri, matched_ids, "identifiers resolve to different assets")

        group = self._groups[next(iter(matched_ids))] if matched_ids else None
        if group is not None and _has_identity_conflict(group.identifiers, identifiers):
            return self._record_conflict(node, source_id, source_uri, {group.id}, "asset identifiers conflict")

        if group is None:
            primary = primary_asset_identifier(node, identifiers)
            group_id = canonical_asset_id(self.environment_id, primary)
            group = _AssetGroup(id=group_id, node={**node, "id": group_id, "identity": group_id})
            self._groups[group_id] = group

        resolution_scope = database_resolution_scope(node)
        if resolution_scope:
            group.resolution_scopes.add(resolution_scope)
        group.identifiers.update(identifiers)
        for key in identifiers:
            self._identifier_to_group[key] = group.id
        group.metadata_source_ids.add(source_id)
        role = node.get("role")
        if role:
            group.roles.add(str(role))
        connection_name = node.get("connection_name")
        if connection_name:
            group.connection_names.add(str(connection_name))
        group.observations.append({
            "source_type": "metadata",
            "metadata_source_id": source_id,
            "metadata_source_uri": source_uri,
            "role": role,
        })
        _merge_node_fields(group.node, node)
        self._candidate_to_groups.setdefault(candidate_id, set()).add(group.id)
        return group.id

    def resolve_candidate_id(self, candidate_id: str) -> str:
        candidates = self._candidate_to_groups.get(candidate_id, set())
        return next(iter(candidates)) if len(candidates) == 1 else candidate_id

    def resolve_candidate_ids(self, candidate_id: str) -> list[str]:
        return sorted(self._candidate_to_groups.get(candidate_id, {candidate_id}))

    def resolve_identifier(self, kind: str, normalized_value: str) -> str | None:
        return self._identifier_to_group.get((kind, normalized_value))

    def find_logical_table_suffix(
        self,
        suffix: str,
        namespace: str | None = None,
        resolution_scope: str | None = None,
        *,
        exact: bool = False,
    ) -> list[str]:
        normalized_suffix = suffix.lower()
        matches: set[str] = set()
        for (kind, value), group_id in self._identifier_to_group.items():
            if kind != "logical_table":
                continue
            group = self._groups[group_id]
            identifier = group.identifiers[(kind, value)]
            if namespace and identifier.get("namespace") != namespace:
                continue
            if resolution_scope and resolution_scope not in group.resolution_scopes:
                continue
            logical_name = value.split("|", 1)[-1]
            if logical_name == normalized_suffix or (not exact and logical_name.endswith(f".{normalized_suffix}")):
                matches.add(group_id)
        return sorted(matches)

    def add_observation(self, asset_id: str, observation: dict[str, Any]) -> None:
        group = self._groups.get(asset_id)
        if group is not None:
            group.observations.append(observation)

    def nodes(self) -> list[dict[str, Any]]:
        nodes = []
        for group in self._groups.values():
            node = {
                **group.node,
                "id": group.id,
                "identity": group.id,
                "identifiers": sorted(group.identifiers.values(), key=lambda item: (item["kind"], item["normalized_value"])),
                "metadata_source_ids": sorted(group.metadata_source_ids),
                "roles": sorted(group.roles),
                "connection_names": sorted(group.connection_names),
                "observations": sorted(
                    group.observations,
                    key=lambda item: (
                        str(item.get("source_type") or ""),
                        int(item.get("metadata_source_id") or 0),
                        str(item.get("metadata_source_uri") or ""),
                        str(item.get("role") or ""),
                    ),
                ),
            }
            nodes.append(node)
        return sorted(
            nodes,
            key=lambda item: (
                str(item.get("label") or ""),
                str(item.get("id") or ""),
            ),
        )

    def _record_conflict(
        self,
        node: dict[str, Any],
        source_id: int,
        source_uri: str,
        candidate_group_ids: set[str],
        reason: str,
    ) -> str:
        identifiers = _identifier_map(node.get("identifiers", []))
        primary = primary_asset_identifier(node, identifiers)
        conflict_id = canonical_asset_id(self.environment_id, {
            **primary,
            "normalized_value": f"conflict:{source_id}:{node['id']}:{primary['normalized_value']}",
        })
        group = _AssetGroup(id=conflict_id, node={**node, "id": conflict_id, "identity": conflict_id})
        resolution_scope = database_resolution_scope(node)
        if resolution_scope:
            group.resolution_scopes.add(resolution_scope)
        group.identifiers.update(identifiers)
        group.metadata_source_ids.add(source_id)
        if node.get("role"):
            group.roles.add(str(node["role"]))
        if node.get("connection_name"):
            group.connection_names.add(str(node["connection_name"]))
        group.observations.append({
            "source_type": "metadata",
            "metadata_source_id": source_id,
            "metadata_source_uri": source_uri,
            "role": node.get("role"),
        })
        self._groups[conflict_id] = group
        self._candidate_to_groups.setdefault(str(node["id"]), set()).add(conflict_id)
        self.diagnostics.append({
            "type": "asset_identity_conflict",
            "message": reason,
            "metadata_source_id": source_id,
            "metadata_source_uri": source_uri,
            "candidate_asset_ids": sorted(candidate_group_ids),
            "identifiers": list(identifiers.values()),
        })
        return conflict_id


def _identifier_map(identifiers: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (str(identifier["kind"]), str(identifier["normalized_value"])): dict(identifier)
        for identifier in identifiers
    }


def _primary_identifier(node: dict[str, Any], identifiers: dict[tuple[str, str], dict[str, str]]) -> dict[str, str]:
    """Compatibility wrapper; Assets owns primary-selection semantics."""
    return primary_asset_identifier(node, identifiers)


def _has_identity_conflict(
    existing: dict[tuple[str, str], dict[str, str]],
    incoming: dict[tuple[str, str], dict[str, str]],
) -> bool:
    for kind in ("api_endpoint", "logical_table", "physical_path"):
        existing_values = {value for (item_kind, value) in existing if item_kind == kind}
        incoming_values = {value for (item_kind, value) in incoming if item_kind == kind}
        if existing_values and incoming_values and (
            existing_values.isdisjoint(incoming_values)
            or bool(incoming_values - existing_values)
        ):
            return True
    return False


def _merge_node_fields(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key in {"id", "identity", "identifiers", "role"}:
            continue
        current = target.get(key)
        if (current is None or current == "") and value is not None and value != "":
            target[key] = value
            continue
        if current in (None, "", value) or value in (None, ""):
            continue
        # Display metadata can be repeated with different source order.  Keep
        # a stable representative for scalar conflicts while preserving the
        # explicit identifier conflict diagnostics above.
        if isinstance(current, (str, int, float, bool)) and isinstance(value, (str, int, float, bool)):
            target[key] = min(str(current), str(value))
