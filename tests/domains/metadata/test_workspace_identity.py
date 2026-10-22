from __future__ import annotations


def test_workspace_identity_vectors_match_datacoolie_contract():
    import pytest

    from datacoolie_studio.core.identity import name_to_uuid

    assert name_to_uuid("orders") == "7172410a-6350-5482-b4a1-c584648f28f7"
    assert name_to_uuid("orders", "ws-a") == "fcceb473-347c-51ca-97c9-3f66ffa78ff4"
    assert name_to_uuid("orders", "ws-b") == "18051ca6-2b24-539f-a47b-d1a628596f32"
    assert name_to_uuid(" đơn hàng ", " ws-a ") == "47903909-075e-5594-b53f-df9874def877"
    with pytest.raises(ValueError, match="workspace_id"):
        name_to_uuid("orders", 7)


def test_normalizer_inherits_one_workspace_and_scopes_generated_ids():
    from datacoolie_studio.core.identity import name_to_uuid
    from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [{"name": "lake"}],
            "dataflows": [{
                "name": "orders",
                "workspace_id": " ws-a ",
                "source": {"connection_name": "lake"},
                "destination": {"connection_name": "lake"},
            }],
        },
    )

    connection = document["connections"][0]
    dataflow = document["dataflows"][0]
    assert connection["workspace_id"] == "ws-a"
    assert connection["connection_id"] == name_to_uuid("lake", "ws-a")
    assert dataflow["workspace_id"] == "ws-a"
    assert dataflow["dataflow_id"] == name_to_uuid("orders", "ws-a")
    assert dataflow["source"]["workspace_id"] == "ws-a"
    assert dataflow["source"]["connection_id"] == connection["connection_id"]


def test_invalid_workspace_does_not_project_a_generated_identity():
    from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [{"name": "lake", "workspace_id": {"bad": True}}],
            "dataflows": [{
                "name": "orders",
                "source": {"connection_name": "lake"},
                "destination": {},
            }],
        },
    )

    assert document["connections"] == []
    assert document["dataflows"] == []


def test_generated_editor_ids_refresh_when_later_composition_adds_scope():
    from datacoolie_studio.core.identity import name_to_uuid
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        materialize_generated_ids,
    )

    sheets = {
        "connections": [{"name": "lake"}],
        "dataflows": [{"name": "orders"}],
    }
    materialize_generated_ids(sheets)
    unscoped_connection_id = sheets["connections"][0]["connection_id"]
    unscoped_dataflow_id = sheets["dataflows"][0]["dataflow_id"]

    sheets["connections"][0]["workspace_id"] = "ws-a"
    sheets["dataflows"][0]["workspace_id"] = "ws-a"
    materialize_generated_ids(sheets)

    assert unscoped_connection_id != sheets["connections"][0]["connection_id"]
    assert unscoped_dataflow_id != sheets["dataflows"][0]["dataflow_id"]
    assert sheets["connections"][0]["connection_id"] == name_to_uuid("lake", "ws-a")
    assert sheets["dataflows"][0]["dataflow_id"] == name_to_uuid("orders", "ws-a")


def test_normalizer_keeps_same_name_connections_separate_by_workspace():
    from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [
                {"name": "lake", "workspace_id": "ws-a"},
                {"name": "lake", "workspace_id": "ws-b"},
            ],
            "dataflows": [{
                "name": "orders",
                "workspace_id": "ws-b",
                "source": {"connection_name": "lake"},
                "destination": {"connection_name": "lake"},
            }],
        },
    )

    connections = document["connections"]
    dataflow = document["dataflows"][0]
    assert len({row["connection_id"] for row in connections}) == 2
    assert dataflow["source"]["workspace_id"] == "ws-b"
    assert dataflow["source"]["connection_id"] == next(
        row["connection_id"] for row in connections if row["workspace_id"] == "ws-b"
    )


def test_conflicting_explicit_connection_id_is_not_resolved_by_workspace_order():
    from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [
                {"name": "lake-a", "connection_id": "same", "workspace_id": "ws-a"},
                {"name": "lake-b", "connection_id": "same", "workspace_id": "ws-b"},
            ],
            "dataflows": [{
                "name": "orders",
                "workspace_id": "ws-a",
                "source": {"connection_id": "same"},
                "destination": {},
            }],
        },
    )

    assert document["dataflows"] == []


def test_inline_connection_scope_mismatch_is_not_projected():
    from datacoolie_studio.domains.metadata.normalizer import normalize_metadata_document

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "dataflows": [{
                "name": "orders",
                "workspace_id": "ws-a",
                "source": {
                    "connection": {"name": "lake", "workspace_id": "ws-b"},
                },
                "destination": {},
            }],
        },
    )

    assert document["dataflows"] == []


def test_mixed_workspace_document_cannot_inherit_missing_entity_scope():
    import pytest

    with pytest.raises(ValueError):
        from datacoolie_studio.domains.metadata.document_lifecycle import (
            normalize_document_workspace,
        )

        normalize_document_workspace({
            "connections": [
                {"name": "lake", "workspace_id": "ws-a"},
                {"name": "lake", "workspace_id": "ws-b"},
            ],
            "dataflows": [{"name": "orders"}],
        })


def test_connection_rename_rewrites_only_matching_workspace():
    from datacoolie_studio.domains.metadata.document_rewrites import (
        synchronize_connection_name_references,
    )

    baseline = {
        "sheets": {
            "connections": {"rows": [
                {"connection_id": "a", "name": "lake", "workspace_id": "ws-a"},
                {"connection_id": "b", "name": "lake", "workspace_id": "ws-b"},
            ]},
            "dataflows": {"rows": []},
            "schema_hints": {"rows": []},
        }
    }
    document = {
        "sheets": {
            "connections": {"rows": [
                {"connection_id": "a", "name": "lake_a", "workspace_id": "ws-a"},
                {"connection_id": "b", "name": "lake_b", "workspace_id": "ws-b"},
            ]},
            "dataflows": {"rows": [
                {"workspace_id": "ws-a", "source_connection_name": "lake"},
                {"workspace_id": "ws-b", "source_connection_name": "lake"},
                {"source_connection_name": "lake"},
            ]},
            "schema_hints": {"rows": [
                {"connection_id": "a", "connection_name": "lake"},
                {"connection_name": "lake"},
            ]},
        }
    }

    rewritten = synchronize_connection_name_references(document, baseline)
    flows = rewritten["sheets"]["dataflows"]["rows"]
    hints = rewritten["sheets"]["schema_hints"]["rows"]
    assert flows[0]["source_connection_name"] == "lake_a"
    assert flows[1]["source_connection_name"] == "lake_b"
    assert flows[2]["source_connection_name"] == "lake"
    assert hints[0]["connection_name"] == "lake_a"
    assert hints[1]["connection_name"] == "lake"


def test_editor_scope_move_recomputes_unchanged_inference_and_generated_ids():
    from datacoolie_studio.core.identity import name_to_uuid
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        materialize_generated_ids,
    )

    sheets = {
        "connections": [{"name": "lake", "workspace_id": "ws-a"}],
        "dataflows": [{"name": "orders"}],
    }
    materialize_generated_ids(sheets)
    assert sheets["dataflows"][0]["workspace_id"] == "ws-a"
    first_id = sheets["dataflows"][0]["dataflow_id"]

    sheets["connections"][0]["workspace_id"] = "ws-b"
    materialize_generated_ids(sheets)

    assert sheets["dataflows"][0]["workspace_id"] == "ws-b"
    assert sheets["dataflows"][0]["dataflow_id"] == name_to_uuid("orders", "ws-b")
    assert sheets["dataflows"][0]["dataflow_id"] != first_id


def test_editor_mixed_scope_clears_inferred_row_and_refreshes_unscoped_id():
    from datacoolie_studio.core.identity import name_to_uuid
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        materialize_generated_ids,
    )

    sheets = {
        "connections": [{"name": "lake", "workspace_id": "ws-a"}],
        "dataflows": [{"name": "orders"}],
    }
    materialize_generated_ids(sheets)
    sheets["connections"].append({"name": "lake", "workspace_id": "ws-b"})
    materialize_generated_ids(sheets)

    flow = sheets["dataflows"][0]
    assert "workspace_id" not in flow
    assert "__studio_inferred_workspace_id" not in flow
    assert flow["dataflow_id"] == name_to_uuid("orders")


def test_editor_identity_strings_are_not_json_or_boolean_coerced():
    from datacoolie_studio.domains.metadata.document_normalization import (
        clean_row,
        unflatten_dataflow,
    )

    row = {
        "name": "true",
        "connection_id": "false",
        "workspace_id": '{"workspace":"a"}',
        "source_connection_name": "[\"lake\"]",
        "source_connection_id": "true",
    }
    cleaned = clean_row(row)
    flow = unflatten_dataflow(row)
    assert cleaned == row
    assert flow["source"] == {
        "connection_name": '["lake"]',
        "connection_id": "true",
    }


def test_editor_preserves_structured_inline_connection_edits():
    from datacoolie_studio.domains.metadata.document_normalization import (
        unflatten_dataflow,
    )

    row = {
        "name": "orders",
        "source_connection": '{"name":"lake","configure":{"path":"/new"}}',
        "__studio_original_values": {
            "name": "orders",
            "source_connection": {
                "name": "lake",
                "configure": {"path": "/old"},
            },
        },
    }
    result = unflatten_dataflow(row)
    assert result["source"]["connection"] == {
        "name": "lake",
        "configure": {"path": "/new"},
    }

    reference_row = {
        "name": "orders",
        "source_connection": "true",
        "__studio_original_values": {
            "name": "orders",
            "source_connection": "lake",
        },
    }
    assert unflatten_dataflow(reference_row)["source"]["connection"] == "true"


def test_generated_connection_rename_chain_uses_baseline_current_id():
    from copy import deepcopy

    from datacoolie_studio.domains.metadata.document_lifecycle import (
        materialize_generated_ids,
    )
    from datacoolie_studio.domains.metadata.document_rewrites import (
        synchronize_connection_name_references,
    )

    sheets = {
        "connections": [{"name": "lake"}],
        "dataflows": [{"name": "orders", "source_connection_name": "lake"}],
    }
    materialize_generated_ids(sheets)
    baseline = {
        "sheets": {name: {"rows": deepcopy(rows)} for name, rows in sheets.items()}
    }

    sheets["connections"][0]["name"] = "lake_b"
    materialize_generated_ids(sheets)
    first = {
        "sheets": {name: {"rows": deepcopy(rows)} for name, rows in sheets.items()}
    }
    first = synchronize_connection_name_references(first, baseline)
    assert first["sheets"]["dataflows"]["rows"][0]["source_connection_name"] == "lake_b"

    second_baseline = deepcopy(first)
    second_sheets = {
        name: rows["rows"]
        for name, rows in first["sheets"].items()
    }
    second_sheets["connections"][0]["name"] = "lake_c"
    materialize_generated_ids(second_sheets)
    second = {"sheets": {name: {"rows": rows} for name, rows in second_sheets.items()}}
    second = synchronize_connection_name_references(second, second_baseline)
    assert second["sheets"]["dataflows"]["rows"][0]["source_connection_name"] == "lake_c"


def test_enrichment_composes_split_shards_before_generating_ids():
    from datacoolie_studio.core.identity import name_to_uuid
    from datacoolie_studio.domains.metadata.normalizer import (
        enrich_metadata_documents_with_connections,
        normalize_metadata_document,
    )

    connection_shard = normalize_metadata_document(
        1,
        "connections.json",
        {"connections": [{"name": "lake", "workspace_id": "ws-a"}]},
    )
    flow_shard = normalize_metadata_document(
        2,
        "flows.json",
        {"dataflows": [{
            "name": "orders",
            "source": {"connection_name": "lake"},
            "destination": {"connection_name": "lake"},
        }]},
    )

    enrich_metadata_documents_with_connections([connection_shard, flow_shard])
    dataflow = flow_shard["dataflows"][0]
    assert dataflow["workspace_id"] == "ws-a"
    assert dataflow["dataflow_id"] == name_to_uuid("orders", "ws-a")
    assert dataflow["source"]["workspace_id"] == "ws-a"
    assert dataflow["source"]["connection_id"] == name_to_uuid("lake", "ws-a")


def test_enrichment_invalidates_missing_scope_in_mixed_shards():
    from datacoolie_studio.domains.metadata.normalizer import (
        enrich_metadata_documents_with_connections,
        normalize_metadata_document,
    )

    documents = [
        normalize_metadata_document(
            1,
            "a.json",
            {"connections": [{"name": "lake", "workspace_id": "ws-a"}]},
        ),
        normalize_metadata_document(
            2,
            "b.json",
            {"connections": [{"name": "lake", "workspace_id": "ws-b"}]},
        ),
        normalize_metadata_document(
            3,
            "flows.json",
            {"dataflows": [{
                "name": "orders",
                "source": {"connection_name": "lake"},
                "destination": {"connection_name": "lake"},
            }]},
        ),
    ]

    enrich_metadata_documents_with_connections(documents)
    assert documents[2]["dataflows"][0]["_identity_workspace_error"]
    assert documents[2]["edges"] == []


def test_enrichment_retains_source_local_inference_marker_for_global_mixed_check():
    from datacoolie_studio.domains.metadata.normalizer import (
        enrich_metadata_documents_with_connections,
        normalize_metadata_document,
    )

    local_default = normalize_metadata_document(
        1,
        "a.json",
        {
            "connections": [{"name": "lake-a", "workspace_id": "ws-a"}],
            "dataflows": [{
                "name": "orders-a",
                "source": {"connection_name": "lake-a"},
                "destination": {"connection_name": "lake-a"},
            }],
        },
    )
    other_scope = normalize_metadata_document(
        2,
        "b.json",
        {"connections": [{"name": "lake-b", "workspace_id": "ws-b"}]},
    )

    assert local_default["dataflows"][0]["_identity_inferred_workspace_id"] == "ws-a"
    enrich_metadata_documents_with_connections([local_default, other_scope])
    assert local_default["dataflows"][0]["_identity_workspace_error"]
    assert local_default["edges"] == []


def test_enrichment_updates_inferred_inline_scope_after_connection_scope_edit():
    from datacoolie_studio.domains.metadata.normalizer import (
        enrich_metadata_documents_with_connections,
        normalize_metadata_document,
    )

    document = normalize_metadata_document(
        1,
        "metadata.json",
        {
            "connections": [{"name": "lake", "workspace_id": "ws-a"}],
            "dataflows": [{
                "name": "orders",
                "source": {"connection": {"name": "lake"}},
                "destination": {"connection": {"name": "lake"}},
            }],
        },
    )
    document["connections"][0]["workspace_id"] = "ws-b"

    enrich_metadata_documents_with_connections([document])
    dataflow = document["dataflows"][0]
    assert dataflow["workspace_id"] == "ws-b"
    assert dataflow["source"]["workspace_id"] == "ws-b"
    assert dataflow["source"]["connection"]["workspace_id"] == "ws-b"
