from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.assets.reference_resolution import ReferenceResolution


def build_project_reference_registry(
    snapshots: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    targets = _build_targets(snapshots)
    targets_by_id = {item["id"]: item for item in targets}
    target_id_by_environment_asset = _target_ids_by_environment_asset(snapshots)
    mappings_by_reference = {
        _reference_key(str(item["reference_type"]), str(item["reference_normalized_value"])): item
        for item in mappings
    }
    rows_by_reference: dict[str, dict[str, Any]] = {}

    for snapshot in snapshots:
        environment = snapshot["environment"]
        for reference in snapshot.get("reference_groups") or []:
            normalized_value = str(reference.get("normalized_value") or "").strip()
            if not normalized_value:
                continue
            reference_type = str(reference.get("reference_type") or "unknown")
            row_id = _reference_key(reference_type, normalized_value)
            row = rows_by_reference.setdefault(row_id, {
                "id": row_id,
                "reference_type": reference_type,
                "normalized_value": normalized_value,
                "mapping": mappings_by_reference.get(row_id),
                "environments": [],
                "candidate_asset_ids": [],
                "resolved_asset_ids": [],
            })
            row["environments"].append(_environment_row(
                environment,
                reference,
                target_id_by_environment_asset,
            ))
            _append_unique(row["candidate_asset_ids"], reference.get("candidate_asset_ids") or [])
            _append_unique(row["resolved_asset_ids"], reference.get("resolved_asset_ids") or [])
            if reference.get("resolved_asset_id"):
                _append_unique(row["resolved_asset_ids"], [reference["resolved_asset_id"]])

    for mapping in mappings:
        row_id = _reference_key(
            str(mapping["reference_type"]),
            str(mapping["reference_normalized_value"]),
        )
        rows_by_reference.setdefault(row_id, {
            "id": row_id,
            "reference_type": str(mapping["reference_type"]),
            "normalized_value": str(mapping["reference_normalized_value"]),
            "mapping": mapping,
            "environments": [],
            "candidate_asset_ids": [],
            "resolved_asset_ids": [],
        })

    rows = []
    for row in rows_by_reference.values():
        mapping = row["mapping"]
        target = targets_by_id.get(_mapping_target_id(mapping)) if mapping else None
        observed_targets = _observed_targets(row["environments"], targets_by_id)
        row["target"] = target
        row["observed_targets"] = observed_targets
        row["target_coverage"] = _target_coverage(row["environments"], target)
        row["resolution"] = _project_resolution(row, target).to_dict()
        rows.append(row)

    rank = {"manual": 0, "unresolved": 1, "automatic": 2}
    rows.sort(key=lambda item: (
        rank[str(item["resolution"]["state"])],
        str(item["normalized_value"]).lower(),
        str(item["reference_type"]),
    ))
    return {"rows": rows, "targets": targets}


def _project_resolution(row: dict[str, Any], target: dict[str, Any] | None) -> ReferenceResolution:
    mapping = row.get("mapping")
    environments = list(row.get("environments") or [])
    if mapping:
        if not environments:
            return ReferenceResolution("manual") if target else ReferenceResolution("unresolved", "target_missing")
        if all(_resolution_state(item) == "manual" for item in environments):
            return ReferenceResolution("manual")
        if any(_resolution_reason(item) == "target_missing" for item in environments):
            return ReferenceResolution("unresolved", "target_missing")
        return ReferenceResolution("unresolved", "incomplete")

    if not environments:
        return ReferenceResolution("unresolved", "no_match")
    if any(_resolution_state(item) != "automatic" for item in environments):
        return ReferenceResolution("unresolved", _preferred_environment_reason(environments))
    observed_target_ids = {
        target_id
        for environment in environments
        for target_id in environment.get("observed_target_ids") or []
    }
    if len(observed_target_ids) != 1:
        reason = "conflicting_targets" if len(observed_target_ids) > 1 else "no_match"
        return ReferenceResolution("unresolved", reason)
    return ReferenceResolution("automatic")


def _preferred_environment_reason(environments: list[dict[str, Any]]) -> str:
    reasons = {_resolution_reason(item) for item in environments}
    for reason in ("target_missing", "conflicting_targets", "multiple_matches", "incomplete", "no_match"):
        if reason in reasons:
            return reason
    return "incomplete"


def _environment_row(
    environment: dict[str, Any],
    reference: dict[str, Any],
    target_id_by_environment_asset: dict[tuple[int, str], str],
) -> dict[str, Any]:
    environment_id = int(environment["id"])
    resolved_asset_ids = list(reference.get("resolved_asset_ids") or [])
    if reference.get("resolved_asset_id"):
        _append_unique(resolved_asset_ids, [reference["resolved_asset_id"]])
    observed_target_ids = [
        target_id_by_environment_asset[(environment_id, str(asset_id))]
        for asset_id in resolved_asset_ids
        if (environment_id, str(asset_id)) in target_id_by_environment_asset
    ]
    manual_mapping = reference.get("manual_mapping") or {}
    return {
        "environment_id": environment_id,
        "environment_name": str(environment["name"]),
        "resolution": _resolution(reference),
        "resolved_asset_id": reference.get("resolved_asset_id"),
        "resolved_asset_ids": resolved_asset_ids,
        "observed_target_ids": list(dict.fromkeys(observed_target_ids)),
        "candidate_asset_ids": list(reference.get("candidate_asset_ids") or []),
        "manual_mapping_id": manual_mapping.get("mapping_id"),
        "manual_mapping_status": manual_mapping.get("status"),
        "occurrence_count": int(reference.get("occurrence_count") or reference.get("dependency_count") or 0),
        "consumer_count": len(reference.get("consumer_asset_ids") or []),
    }


def _build_targets(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        environment = snapshot["environment"]
        for asset in snapshot.get("assets") or []:
            identifier = asset.get("mapping_target")
            if not isinstance(identifier, dict) or not identifier.get("kind") or not identifier.get("value"):
                continue
            target_id = _target_id(str(identifier["kind"]), str(identifier["value"]))
            current = targets.get(target_id)
            if current:
                _append_unique(current["asset_ids"], [asset["id"]])
                _append_unique(current["environment_ids"], [environment["id"]])
                _append_unique(current["environment_names"], [environment["name"]])
                continue
            display_name = str(asset.get("friendly_name") or asset.get("display_name") or identifier.get("display") or identifier["value"])
            targets[target_id] = {
                "id": target_id,
                "asset_id": str(asset["id"]),
                "asset_ids": [str(asset["id"])],
                "environment_ids": [int(environment["id"])],
                "environment_names": [str(environment["name"])],
                "asset_type": str(asset.get("asset_type") or "unresolved"),
                "format": asset.get("format"),
                "connection_name": str(asset.get("connection_name") or "unknown connection"),
                "display_name": display_name,
                "context": _target_context(asset, str(identifier.get("display") or identifier["value"]), display_name),
                "kind": str(identifier["kind"]),
                "value": str(identifier["value"]),
                "display": str(identifier.get("display") or identifier["value"]),
            }
    return sorted(targets.values(), key=lambda item: str(item["display_name"]).lower())


def _target_ids_by_environment_asset(snapshots: list[dict[str, Any]]) -> dict[tuple[int, str], str]:
    result: dict[tuple[int, str], str] = {}
    for snapshot in snapshots:
        environment_id = int(snapshot["environment"]["id"])
        for asset in snapshot.get("assets") or []:
            identifier = asset.get("mapping_target")
            if not isinstance(identifier, dict) or not identifier.get("kind") or not identifier.get("value"):
                continue
            result[(environment_id, str(asset["id"]))] = _target_id(
                str(identifier["kind"]),
                str(identifier["value"]),
            )
    return result


def _observed_targets(
    environments: list[dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = {}
    for environment in environments:
        for target_id in environment.get("observed_target_ids") or []:
            target = targets_by_id.get(str(target_id))
            if target is None:
                continue
            current = observed.setdefault(str(target_id), {
                "target": target,
                "environment_ids": [],
                "environment_names": [],
            })
            _append_unique(current["environment_ids"], [environment["environment_id"]])
            _append_unique(current["environment_names"], [environment["environment_name"]])
    return sorted(observed.values(), key=lambda item: str(item["target"]["display_name"]).lower())


def _target_coverage(
    environments: list[dict[str, Any]],
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    target_environment_ids = set(target.get("environment_ids") or []) if target else set()
    available = [
        item["environment_name"]
        for item in environments
        if item["environment_id"] in target_environment_ids
    ]
    missing = [
        item["environment_name"]
        for item in environments
        if item["environment_id"] not in target_environment_ids
    ]
    return {
        "available_environment_names": available,
        "missing_environment_names": missing,
        "available": len(available),
        "total": len(environments),
    }


def _target_context(asset: dict[str, Any], canonical_display: str, display_name: str) -> str | None:
    if asset.get("asset_type") == "table":
        return ".".join(str(value) for value in (asset.get("catalog"), asset.get("database")) if value) or None
    identity = str(asset.get("python_function") or asset.get("query") or canonical_display)
    if asset.get("table"):
        return identity
    if identity == display_name:
        return None
    for separator in (".", "/"):
        suffix = f"{separator}{display_name}"
        if identity.endswith(suffix):
            return identity[:-len(suffix)] or None
    return identity


def _resolution(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("resolution")
    if not isinstance(value, dict):
        return {"state": "unresolved", "reason": "no_match"}
    return {"state": str(value.get("state") or "unresolved"), "reason": value.get("reason")}


def _resolution_state(item: dict[str, Any]) -> str:
    return str(_resolution(item)["state"])


def _resolution_reason(item: dict[str, Any]) -> str | None:
    value = _resolution(item).get("reason")
    return str(value) if value else None


def _mapping_target_id(mapping: dict[str, Any]) -> str:
    return _target_id(str(mapping["target_identifier_kind"]), str(mapping["target_normalized_value"]))


def _reference_key(reference_type: str, normalized_value: str) -> str:
    return f"{reference_type}\x1f{normalized_value}"


def _target_id(kind: str, value: str) -> str:
    return f"{kind}\x1f{value}"


def _append_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
