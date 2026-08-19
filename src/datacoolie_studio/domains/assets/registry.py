from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
        self._candidate_to_group: dict[str, str] = {}
        self.diagnostics: list[dict[str, Any]] = []

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
            primary = _primary_identifier(node, identifiers)
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
        self._candidate_to_group[candidate_id] = group.id
        return group.id

    def resolve_candidate_id(self, candidate_id: str) -> str:
        return self._candidate_to_group.get(candidate_id, candidate_id)

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
                "observations": group.observations,
            }
            nodes.append(node)
        return sorted(nodes, key=lambda item: str(item.get("label") or ""))

    def _record_conflict(
        self,
        node: dict[str, Any],
        source_id: int,
        source_uri: str,
        candidate_group_ids: set[str],
        reason: str,
    ) -> str:
        identifiers = _identifier_map(node.get("identifiers", []))
        primary = _primary_identifier(node, identifiers)
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
        self._candidate_to_group[str(node["id"])] = conflict_id
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
    preferred_kind = str(node.get("identity_type") or "")
    for (identifier_kind, _), identifier in identifiers.items():
        if identifier_kind == preferred_kind:
            return identifier
    for kind in ("api_endpoint", "logical_table", "physical_path"):
        for (identifier_kind, _), identifier in identifiers.items():
            if identifier_kind == kind:
                return identifier
    return {
        "kind": str(node.get("identity_type") or "unresolved"),
        "normalized_value": str(node.get("identity") or node["id"]),
        "display_value": str(node.get("label") or node["id"]),
        "namespace": "",
        "source": "metadata",
    }


def _has_identity_conflict(
    existing: dict[tuple[str, str], dict[str, str]],
    incoming: dict[tuple[str, str], dict[str, str]],
) -> bool:
    for kind in ("api_endpoint", "logical_table", "physical_path"):
        existing_values = {value for (item_kind, value) in existing if item_kind == kind}
        incoming_values = {value for (item_kind, value) in incoming if item_kind == kind}
        if existing_values and incoming_values and existing_values.isdisjoint(incoming_values):
            return True
    return False


def _merge_node_fields(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key in {"id", "identity", "identifiers", "role"}:
            continue
        current = target.get(key)
        if (current is None or current == "") and value is not None and value != "":
            target[key] = value
