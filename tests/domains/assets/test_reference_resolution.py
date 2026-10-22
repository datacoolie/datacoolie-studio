from datacoolie_studio.domains.assets.project_reference_registry import build_project_reference_registry
from datacoolie_studio.domains.assets.mapping_target import asset_mapping_target
from datacoolie_studio.domains.assets.reference_resolution import (
    AUTOMATIC_RESOLUTION,
    MANUAL_RESOLUTION,
    group_resolution,
    merge_resolution,
    unresolved_resolution,
)


def test_group_resolution_requires_complete_single_target_evidence() -> None:
    assert group_resolution([AUTOMATIC_RESOLUTION], ["asset:a"]) == AUTOMATIC_RESOLUTION
    assert group_resolution([MANUAL_RESOLUTION, MANUAL_RESOLUTION], ["asset:a"]) == MANUAL_RESOLUTION
    assert group_resolution(
        [AUTOMATIC_RESOLUTION, unresolved_resolution("no_match")],
        ["asset:a"],
    ) == unresolved_resolution("no_match")
    assert group_resolution(
        [AUTOMATIC_RESOLUTION, AUTOMATIC_RESOLUTION],
        ["asset:a", "asset:b"],
    ) == unresolved_resolution("conflicting_targets")


def test_duplicate_resolution_merge_is_fail_closed() -> None:
    assert merge_resolution(AUTOMATIC_RESOLUTION, MANUAL_RESOLUTION) == MANUAL_RESOLUTION
    assert merge_resolution(
        MANUAL_RESOLUTION,
        unresolved_resolution("target_missing"),
    ) == unresolved_resolution("target_missing")


def test_mapping_target_is_never_invented_from_non_resolvable_asset_fields() -> None:
    assert asset_mapping_target([], {"asset_type": "python_function", "table": "friendly_alias"}) is None
    assert asset_mapping_target(
        [{"kind": "logical_table", "normalized_value": "bronze.orders", "display_value": "bronze.orders"}],
        {"asset_type": "table"},
    ) == {"kind": "logical_table", "value": "bronze.orders", "display": "bronze.orders"}


def test_project_registry_uses_one_automatic_state_for_a_shared_canonical_target() -> None:
    result = build_project_reference_registry(
        [
            _snapshot(1, "dev", "asset:dev-orders", "bronze.orders", "automatic"),
            _snapshot(2, "test", "asset:test-orders", "bronze.orders", "automatic"),
        ],
        [],
    )

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["resolution"] == {"state": "automatic", "reason": None}
    assert len(row["observed_targets"]) == 1
    assert row["observed_targets"][0]["environment_names"] == ["dev", "test"]


def test_project_registry_marks_conflicting_automatic_targets_unresolved() -> None:
    result = build_project_reference_registry(
        [
            _snapshot(1, "dev", "asset:dev-orders", "bronze.orders", "automatic"),
            _snapshot(2, "test", "asset:test-orders", "silver.orders", "automatic"),
        ],
        [],
    )

    assert result["rows"][0]["resolution"] == {
        "state": "unresolved",
        "reason": "conflicting_targets",
    }


def test_project_registry_preserves_manual_mapping_ownership_when_target_is_missing() -> None:
    mapping = _mapping()
    snapshot = _snapshot(
        1,
        "dev",
        "asset:dev-orders",
        "bronze.orders",
        "unresolved",
        reason="target_missing",
        manual_mapping={"mapping_id": mapping["id"], "status": "target_missing"},
    )
    result = build_project_reference_registry([snapshot], [mapping])

    row = result["rows"][0]
    assert row["resolution"] == {"state": "unresolved", "reason": "target_missing"}
    assert row["mapping"]["id"] == mapping["id"]
    assert row["environments"][0]["manual_mapping_id"] == mapping["id"]


def test_saved_only_mapping_without_a_valid_target_is_unresolved() -> None:
    result = build_project_reference_registry([], [_mapping()])

    assert result["rows"][0]["resolution"] == {
        "state": "unresolved",
        "reason": "target_missing",
    }
    assert result["rows"][0]["environments"] == []


def test_metadata_name_lookup_stays_ambiguous_for_distinct_connection_ids() -> None:
    from datacoolie_studio.domains.assets.identity_catalog import (
        IdentityCatalog,
        resolve_destination_signature,
    )

    def row(connection_id: str) -> dict:
        endpoint = {
            "connection_id": connection_id,
            "connection_name": "lake",
            "connection_type": "database",
            "workspace_id": "ws-a",
            "database": "warehouse",
            "schema_name": "raw",
            "table": "orders",
        }
        return {
            "asset_id": "asset:same-physical-binding",
            "connection_id": connection_id,
            "connection_name": "lake",
            "workspace_id": "ws-a",
            "dataflow_id": f"flow-{connection_id}",
            "dataflow_name": "orders",
            "endpoint": endpoint,
            "metadata_source_ids": [1],
        }

    catalog = IdentityCatalog(
        environment_id=1,
        version="catalog-v1",
        rows=(row("connection-a"), row("connection-b")),
    )
    resolved = resolve_destination_signature(
        1,
        {
            "destination_name": "lake",
            "destination_connection_type": "database",
            "workspace_id": "ws-a",
            "destination_database": "warehouse",
            "destination_schema": "raw",
            "destination_table": "orders",
        },
        catalog,
    )

    assert resolved["identity_status"] == "conflict"
    assert resolved["identity_reason"] == "multiple metadata connection identities"


def test_raw_endpoint_lookup_validates_global_id_owners_for_name_and_inline_refs() -> None:
    from datacoolie_studio.domains.assets.identity_catalog import _resolve_raw_endpoint

    connections = [
        {
            "name": "lake-a",
            "connection_id": "same-id",
            "workspace_id": "ws-a",
        },
        {
            "name": "lake-b",
            "connection_id": "same-id",
            "workspace_id": "ws-b",
        },
    ]
    named = _resolve_raw_endpoint(
        {"connection_name": "lake-a"},
        connections,
        workspace_id="ws-a",
    )[0]
    inline = _resolve_raw_endpoint(
        {
            "connection": {
                "name": "lake-a",
                "connection_id": "same-id",
                "workspace_id": "ws-a",
            },
        },
        connections,
        workspace_id="ws-a",
    )[0]

    assert named["_identity_error"] == "connection_id has conflicting workspace owners"
    assert inline["_identity_error"] == "connection_id has conflicting workspace owners"


def _snapshot(
    environment_id: int,
    environment_name: str,
    asset_id: str,
    target_value: str,
    state: str,
    *,
    reason: str | None = None,
    manual_mapping: dict | None = None,
) -> dict:
    return {
        "environment": {"id": environment_id, "name": environment_name},
        "catalog_version": f"catalog-{environment_id}",
        "assets": [{
            "id": asset_id,
            "friendly_name": target_value,
            "display_name": target_value,
            "asset_type": "table",
            "format": "delta",
            "connection_name": "lake",
            "catalog": "main",
            "database": "warehouse",
            "mapping_target": {
                "kind": "logical_table",
                "value": target_value,
                "display": target_value,
            },
        }],
        "reference_groups": [{
            "id": "reference:orders",
            "reference_type": "table_reference",
            "normalized_value": "raw.orders",
            "display_name": "raw.orders",
            "resolution": {"state": state, "reason": reason},
            "resolved_asset_id": asset_id if state != "unresolved" else None,
            "resolved_asset_ids": [asset_id] if state != "unresolved" else [],
            "candidate_asset_ids": [asset_id],
            "consumer_asset_ids": [f"asset:consumer-{environment_id}"],
            "occurrence_count": 1,
            "dependency_count": 1,
            "manual_mapping": manual_mapping,
        }],
    }


def _mapping() -> dict:
    return {
        "id": 41,
        "project_id": 7,
        "reference_type": "table_reference",
        "reference_normalized_value": "raw.orders",
        "reference_signature": {},
        "target_identifier_kind": "logical_table",
        "target_normalized_value": "missing.orders",
        "target_display_value": "missing.orders",
        "note": None,
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-21T00:00:00Z",
    }
