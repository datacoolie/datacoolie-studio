from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from tests.domains.metadata.metadata_editor_support import (
    _column_keys,
    _editor_document,
    _editor_workspace,
    _save_environment_document,
    _source_revision,
)


def test_sql_file_draft_is_not_written_until_save_to_source(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "project"
    metadata_path = project_root / "metadata" / "flows.json"
    sql_path = project_root / "sql" / "orders.sql"
    metadata_path.parent.mkdir(parents=True)
    sql_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps({
            "connections": [
                {"name": "lake", "connection_type": "file", "format": "parquet"}
            ],
            "dataflows": [{
                "name": "orders",
                "source": {
                    "connection_name": "lake",
                    "query": "orders.sql",
                },
                "destination": {
                    "connection_name": "lake",
                    "table": "orders_curated",
                },
            }],
            "schema_hints": [],
        }),
        encoding="utf-8",
    )
    sql_path.write_text("SELECT id FROM raw_orders\n", encoding="utf-8")
    original_metadata = metadata_path.read_bytes()
    original_sql = sql_path.read_bytes()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "draft-sql"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": str(metadata_path),
                "source_config": {
                    "project_uri": str(project_root),
                    "metadata_root_uri": str(project_root / "metadata"),
                    "sql_root_uri": str(project_root / "sql"),
                },
            },
        ).json()
        document = _editor_document(client, environment["id"])
        sql_reference = client.post(
            f"/api/v1/environments/{environment['id']}/sql-reference",
            json={"source_id": source["id"], "query": "orders.sql"},
        )
        assert sql_reference.status_code == 200, sql_reference.text
        document["sql_files"] = [{
            "source_id": source["id"],
            "query": "orders.sql",
            "content": "SELECT id, customer_id FROM raw_orders\n",
            "base_revision": sql_reference.json()["revision"],
        }]

        draft_response = client.put(
            f"/api/v1/environments/{environment['id']}/metadata-editor-document/draft",
            json=document,
        )
        assert draft_response.status_code == 200, draft_response.text
        assert metadata_path.read_bytes() == original_metadata
        assert sql_path.read_bytes() == original_sql
        draft_document = draft_response.json()
        assert draft_document["sql_files"][0]["query"] == "orders.sql"

        workspace_with_draft = _editor_workspace(client, environment["id"])
        assert workspace_with_draft["draft"]["sql_files"][0]["content"].startswith("SELECT id, customer_id")

        invalid_sql_document = json.loads(json.dumps(draft_document))
        invalid_sql_document["sql_files"][0]["content"] = "SELECT FROM"
        validation_response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-editor-document/validate",
            json=invalid_sql_document,
        )
        assert validation_response.status_code in {404, 405}
        assert metadata_path.read_bytes() == original_metadata
        assert sql_path.read_bytes() == original_sql

        from datacoolie_studio.domains.metadata import write_executor
        original_conditional_replace = write_executor.conditional_replace_uri

        def fail_after_sql_write(*args, **kwargs):
            original_conditional_replace(*args, **kwargs)
            raise RuntimeError("injected metadata failure")

        monkeypatch.setattr(
            write_executor,
            "conditional_replace_uri",
            fail_after_sql_write,
        )
        prepared_response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-write-operations/prepare",
            json={
                "expected_revision": document["source"]["revision"],
                "editor_document": draft_document,
            },
        )
        assert prepared_response.status_code == 200, prepared_response.text
        operation_key = prepared_response.json()["operation_key"]
        from datacoolie_studio.core.config import metadata_write_artifact_dir

        with sqlite3.connect(tmp_path / "studio.db") as connection:
            sql_backup_ref = connection.execute(
                "select backup_ref from metadata_write_items "
                "where operation_id = (select id from metadata_write_operations "
                "where operation_key = ?)",
                (operation_key,),
            ).fetchone()[0]
        assert sql_backup_ref
        assert (metadata_write_artifact_dir() / sql_backup_ref).exists()
        partial_response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-write-operations/"
            f"{operation_key}/apply",
            json={"confirm_apply": True},
        )
        assert partial_response.status_code == 200, partial_response.text
        assert partial_response.json()["state"] == "failed"
        assert sql_path.read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "SELECT id, customer_id FROM raw_orders\n"
        )
        partial_draft = _editor_workspace(client, environment["id"])["draft"]
        assert partial_draft["sql_files"][0]["content"] == "SELECT id, customer_id FROM raw_orders\n"

        monkeypatch.setattr(
            write_executor,
            "conditional_replace_uri",
            original_conditional_replace,
        )

        save_response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-write-operations/"
            f"{operation_key}/apply",
            json={"confirm_apply": True},
        )
        assert save_response.status_code == 200, save_response.text
        assert save_response.json()["state"] == "succeeded"
        assert not (metadata_write_artifact_dir() / sql_backup_ref).exists()
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            assert connection.execute(
                "select backup_ref, payload_ref from metadata_write_items "
                "where operation_id = (select id from metadata_write_operations "
                "where operation_key = ?) and kind = 'sql'",
                (operation_key,),
            ).fetchone() == (None, None)

        assert sql_path.read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "SELECT id, customer_id FROM raw_orders\n"
        )
        assert metadata_path.read_bytes() == original_metadata
        assert _editor_workspace(client, environment["id"])["draft"] is None

        retry_response = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-write-operations/"
            f"{operation_key}/apply",
            json={"confirm_apply": True},
        )
        assert retry_response.status_code == 200, retry_response.text
        assert sql_path.read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "SELECT id, customer_id FROM raw_orders\n"
        )

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_new_sql_draft_pins_project_config_target_before_source_save(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "project"
    metadata_path = project_root / "metadata" / "flows.json"
    metadata_path.parent.mkdir(parents=True)
    (project_root / "queries").mkdir(parents=True)
    (project_root / "datacoolie.yml").write_text(
        "components:\n  sql:\n    path: queries\n",
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake", "connection_type": "file"}],
                "dataflows": [
                    {
                        "name": "orders",
                        "source": {"connection_name": "lake", "query": "new.sql"},
                        "destination": {
                            "connection_name": "lake",
                            "table": "orders_curated",
                        },
                    }
                ],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATACOOLIE_STUDIO_DATABASE_URL",
        f"sqlite:///{(tmp_path / 'studio.db').as_posix()}",
    )

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "config-pin"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": str(metadata_path),
                "source_config": {
                    "project_uri": str(project_root),
                    "discovery_mode": "datacoolie_project",
                },
            },
        ).json()
        document = _editor_document(client, environment["id"])
        document["sql_files"] = [
            {
                "source_id": source["id"],
                "query": "new.sql",
                "content": "SELECT 1\n",
            }
        ]

        draft_response = client.put(
            f"/api/v1/environments/{environment['id']}/metadata-editor-document/draft",
            json=document,
        )
        assert draft_response.status_code == 200, draft_response.text
        draft_document = draft_response.json()
        operation = draft_document["sql_files"][0]
        assert operation["base_uri"] == str(project_root / "queries")
        assert operation["config_revision"]["content_hash"]

        (project_root / "authored" / "sql").mkdir(parents=True)
        (project_root / "datacoolie.yml").write_text(
            "components:\n  sql:\n    path: authored/sql\n",
            encoding="utf-8",
        )
        save_response = _save_environment_document(
            client,
            environment["id"],
            draft_document,
        )
        assert save_response.status_code == 409, save_response.text
        assert not (project_root / "queries" / "new.sql").exists()
        assert not (project_root / "authored" / "sql" / "new.sql").exists()
        retained_draft = _editor_workspace(client, environment["id"])["draft"]
        assert retained_draft["sql_files"][0]["base_uri"] == str(project_root / "queries")

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_read_only_metadata_source_allows_draft_but_blocks_source_save(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    sql_path = tmp_path / "sql" / "new.sql"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake", "connection_type": "file"}],
                "dataflows": [{"name": "orders", "source": {"connection_name": "lake"}}],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    original_metadata = metadata_path.read_bytes()
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient
    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "read-only"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        source = client.post(
            f"/api/v1/environments/{environment['id']}/metadata-sources",
            json={
                "uri": str(metadata_path),
                "source_config": {
                    "read_only": True,
                    "project_uri": str(tmp_path),
                    "sql_root_uri": str(tmp_path / "sql"),
                },
            },
        ).json()
        document = _editor_document(client, environment["id"])
        assert document["source"]["read_only"] is True

        document["sheets"]["dataflows"]["rows"][0]["description"] = "draft only"
        document["sheets"]["dataflows"]["rows"][0]["source_query"] = "new.sql"
        document["sql_files"] = [{
            "source_id": source["id"],
            "query": "new.sql",
            "content": "SELECT 1\n",
        }]
        draft_response = client.put(
            f"/api/v1/environments/{environment['id']}/metadata-editor-document/draft",
            json=document,
        )
        assert draft_response.status_code == 200, draft_response.text
        assert metadata_path.read_bytes() == original_metadata

        save_response = _save_environment_document(
            client,
            environment["id"],
            draft_response.json(),
        )
        assert save_response.status_code == 422, save_response.text
        assert "read-only" in save_response.text
        assert metadata_path.read_bytes() == original_metadata
        assert not sql_path.exists()
        assert _editor_workspace(client, environment["id"])["draft"] is not None

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_metadata_editor_expands_dynamic_nested_columns_and_stringifies_complex_values(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [
                    {"name": "lake", "connection_type": "file", "format": "parquet", "configure": {"base_path": "./data"}}
                ],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a", "custom_option": {"enabled": True}},
                        "transform": {
                            "additional_columns": [{"column": "x", "expression": "1"}],
                            "select_columns": ["id", "email"],
                            "drop_columns": [],
                            "rename_columns": {"email": "contact_email"},
                            "value_rules": [{"operation": "trim", "columns": ["email"]}],
                            "hash_columns": [{"target_column": "email_hash", "columns": ["email"]}],
                            "masking_rules": [{"method": "redact", "columns": ["email"], "value": "[PRIVATE]"}],
                            "configure": {"missing_column_policy": "ignore"},
                            "custom_rule": ["a", "b"],
                        },
                        "destination": {"connection_name": "lake", "table": "b", "load_type": "append", "custom_sink": {"mode": "fast"}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        invalid_document = json.loads(json.dumps(document))
        invalid_document["sheets"]["dataflows"]["rows"][0]["transform_value_rules"] = "[{"
        invalid_draft = client.put(
            f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=invalid_document,
        )
        assert invalid_draft.status_code == 200
        assert invalid_draft.json()["issues"] == []
        save_response = _save_environment_document(client, env["id"], document)
        assert save_response.status_code == 200

    columns = _column_keys(document, "dataflows")
    row = document["sheets"]["dataflows"]["rows"][0]
    assert columns.index("source_custom_option") > columns.index("source_configure")
    assert columns.index("transform_custom_rule") > columns.index("transform_configure")
    assert columns.index("destination_custom_sink") > columns.index("destination_configure")
    assert columns.index("source_configure") < columns.index("destination_connection_name")
    assert columns.index("destination_configure") < columns.index("transform_deduplicate_columns")
    assert [column for column in columns if column.startswith("transform_")] == [
        "transform_deduplicate_columns",
        "transform_latest_data_columns",
        "transform_filter_expression",
        "transform_additional_columns",
        "transform_schema_hints",
        "transform_select_columns",
        "transform_drop_columns",
        "transform_rename_columns",
        "transform_value_rules",
        "transform_hash_columns",
        "transform_masking_rules",
        "transform_configure",
        "transform_custom_rule",
    ]
    assert row["source_custom_option"] == '{"enabled": true}'
    assert row["transform_additional_columns"].startswith("[")
    assert json.loads(row["transform_select_columns"]) == ["id", "email"]
    assert json.loads(row["transform_drop_columns"]) == []
    assert json.loads(row["transform_rename_columns"]) == {"email": "contact_email"}
    assert json.loads(row["transform_value_rules"])[0]["operation"] == "trim"
    assert json.loads(row["transform_hash_columns"])[0]["target_column"] == "email_hash"
    assert json.loads(row["transform_masking_rules"])[0]["value"] == "[PRIVATE]"
    assert json.loads(row["transform_configure"])["missing_column_policy"] == "ignore"
    assert row["destination_custom_sink"] == '{"mode": "fast"}'
    saved_transform = json.loads(metadata_path.read_text(encoding="utf-8"))["dataflows"][0]["transform"]
    assert saved_transform["select_columns"] == ["id", "email"]
    assert saved_transform["drop_columns"] == []
    assert saved_transform["rename_columns"] == {"email": "contact_email"}
    assert saved_transform["value_rules"][0]["operation"] == "trim"
    assert saved_transform["hash_columns"][0]["target_column"] == "email_hash"
    assert saved_transform["masking_rules"][0]["value"] == "[PRIVATE]"

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_environment_metadata_merge_reorders_columns_from_stale_materializations():
    from datacoolie_studio.domains.metadata.environment_documents import (
        merge_editor_sheet_documents,
    )

    canonical_before_expansion = [
        "transform_deduplicate_columns",
        "transform_latest_data_columns",
        "transform_filter_expression",
        "transform_additional_columns",
        "transform_schema_hints",
        "transform_configure",
        "destination_connection_name",
        "__metadata_source_name",
    ]
    expanded_columns = [
        "transform_select_columns",
        "transform_drop_columns",
        "transform_rename_columns",
        "transform_value_rules",
        "transform_hash_columns",
        "transform_masking_rules",
    ]
    documents = [
        {
            "sheets": {
                "dataflows": {
                    "columns": [{"key": key, "name": key} for key in canonical_before_expansion],
                    "rows": [{"name": "old_materialization"}],
                }
            }
        },
        {
            "sheets": {
                "dataflows": {
                    "columns": [{"key": key, "name": key} for key in expanded_columns],
                    "rows": [{"name": "new_materialization"}],
                }
            }
        },
    ]

    merged = merge_editor_sheet_documents(documents, "dataflows")
    transform_columns = [
        column["key"]
        for column in merged["columns"]
        if column["key"].startswith("transform_")
    ]

    assert transform_columns == [
        "transform_deduplicate_columns",
        "transform_latest_data_columns",
        "transform_filter_expression",
        "transform_additional_columns",
        "transform_schema_hints",
        "transform_select_columns",
        "transform_drop_columns",
        "transform_rename_columns",
        "transform_value_rules",
        "transform_hash_columns",
        "transform_masking_rules",
        "transform_configure",
    ]
    assert merged["columns"][-1]["key"] == "__metadata_source_name"

def test_metadata_editor_materializes_default_is_active_for_studio(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [
                    {"name": "lake", "connection_type": "file"},
                    {"name": "disabled_lake", "connection_type": "file", "is_active": False},
                ],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a"},
                        "destination": {"connection_name": "lake", "table": "b"},
                    },
                    {
                        "name": "disabled_flow",
                        "is_active": False,
                        "source": {"connection_name": "lake", "table": "c"},
                        "destination": {"connection_name": "lake", "table": "d"},
                    },
                ],
                "schema_hints": [
                    {
                        "connection_name": "lake",
                        "table_name": "orders",
                        "hints": [
                            {"column_name": "id", "data_type": "int"},
                            {"column_name": "old_id", "data_type": "int", "is_active": False},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])

    connections = {row["name"]: row["is_active"] for row in document["sheets"]["connections"]["rows"]}
    dataflows = {row["name"]: row["is_active"] for row in document["sheets"]["dataflows"]["rows"]}
    schema_hints = {row["column_name"]: row["is_active"] for row in document["sheets"]["schema_hints"]["rows"]}
    assert connections == {"lake": True, "disabled_lake": False}
    assert dataflows == {"flow": True, "disabled_flow": False}
    assert schema_hints == {"id": True, "old_id": False}

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)

def test_metadata_editor_draft_and_safe_save_create_backup(tmp_path: Path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "connections": [{"name": "lake", "connection_type": "file", "format": "json"}],
                "dataflows": [
                    {
                        "name": "flow",
                        "source": {"connection_name": "lake", "table": "a"},
                        "destination": {"connection_name": "lake", "table": "b"},
                    }
                ],
                "schema_hints": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={"name": "dev"}).json()
        source = client.post(
            f"/api/v1/environments/{env['id']}/metadata-sources",
            json={"uri": str(metadata_path), "label": "json"},
        ).json()
        document = _editor_document(client, env["id"])
        original_text = metadata_path.read_text(encoding="utf-8")

        document["sheets"]["dataflows"]["rows"][0]["description"] = "draft only"
        with monkeypatch.context() as draft_patch:
            draft_patch.setattr(
                "datacoolie_studio.domains.metadata.revisions.storage_for_source",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("draft save must not access source storage")
                ),
            )
            draft_patch.setattr(
                "datacoolie_studio.domains.metadata.storage_io.storage_for_source",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("draft save must not access source storage")
                ),
            )
            draft_response = client.put(
                f"/api/v1/environments/{env['id']}/metadata-editor-document/draft",
                json=document,
            )
        assert draft_response.status_code == 200
        draft = draft_response.json()
        assert draft["sheets"]["dataflows"]["rows"][0]["description"] == "draft only"
        assert metadata_path.read_text(encoding="utf-8") == original_text

        document["sheets"]["dataflows"]["rows"][0]["description"] = "saved"
        saved = _save_environment_document(client, env["id"], document).json()["document"]
        assert saved["sheets"]["dataflows"]["rows"][0]["description"] == "saved"
        assert "saved" in metadata_path.read_text(encoding="utf-8")
        saved_file = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert "connection_id" not in saved_file["connections"][0]
        assert "dataflow_id" not in saved_file["dataflows"][0]
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 1
        assert backups[0]["backup_path"] is None
        assert backups[0]["backup_ref"]
        from datacoolie_studio.core.config import metadata_write_artifact_dir

        backup_paths = [metadata_write_artifact_dir() / backups[0]["backup_ref"]]
        assert all(path.exists() for path in backup_paths)
        assert _editor_workspace(client, env["id"])["draft"] is None

        preview = client.get(
            f"/api/v1/environments/{env['id']}/metadata-backups/{backups[0]['id']}/editor-document"
        ).json()
        assert preview["source"]["source_id"] == source["id"]
        assert preview["sheets"]["dataflows"]["rows"][0].get("description") is None

        saved["sheets"]["dataflows"]["rows"][0]["description"] = "stale draft"
        client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=saved)
        restored = client.post(
            f"/api/v1/environments/{env['id']}/metadata-backups/{backups[0]['id']}/restore",
            json={
                "expected_revision": _source_revision(saved, source["id"]),
                "confirm_restore": True,
            },
        ).json()["document"]
        assert restored["sheets"]["dataflows"]["rows"][0].get("description") is None
        assert _editor_workspace(client, env["id"])["draft"] is None
        backups = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()
        assert len(backups) == 2
        backup_paths = [metadata_write_artifact_dir() / backup["backup_ref"] for backup in backups]
        assert all(path.exists() for path in backup_paths)

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-backups")
        assert response.status_code == 204
        assert client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json() == []
        assert all(not path.exists() for path in backup_paths)
        assert metadata_path.exists()

        restored = _editor_document(client, env["id"])
        restored["sheets"]["dataflows"]["rows"][0]["description"] = "save before source delete"
        saved_for_delete = _save_environment_document(
            client,
            env["id"],
            restored,
        ).json()["document"]
        saved_for_delete["sheets"]["dataflows"]["rows"][0]["description"] = "draft before source delete"
        client.put(f"/api/v1/environments/{env['id']}/metadata-editor-document/draft", json=saved_for_delete)
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            connection.execute(
                """
                insert into metadata_validation_results (source_id, base_revision_json, status, result_json, created_at)
                values (?, '{}', 'ok', '{}', CURRENT_TIMESTAMP)
                """,
                (source["id"],),
            )
            connection.commit()
        source_backup = client.get(f"/api/v1/environments/{env['id']}/metadata-backups").json()[0]
        source_backup_path = metadata_write_artifact_dir() / source_backup["backup_ref"]
        assert source_backup_path.exists()

        impact = client.get(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}/delete-impact").json()
        impact_counts = {item["kind"]: item["count"] for item in impact["impacts"]}
        assert impact["has_impact"] is True
        assert impact["metadata_file_deleted"] is False
        assert impact_counts["environment_draft"] == 1
        assert impact_counts["backup"] == 1
        assert impact_counts["validation_result"] == 1
        assert impact_counts["materialization"] == 1
        assert impact_counts["save_event"] >= 1
        assert impact_counts["source_observation"] == 1
        assert impact_counts["sync_job"] >= 1
        assert "metadata file will not be deleted" in impact["summary"]

        response = client.delete(f"/api/v1/environments/{env['id']}/metadata-sources/{source['id']}")
        assert response.status_code == 204
        assert not source_backup_path.exists()
        assert client.get(f"/api/v1/environments/{env['id']}/metadata-sources").json() == []
        assert metadata_path.exists()
        with sqlite3.connect(tmp_path / "studio.db") as connection:
            for table in [
                "metadata_editor_drafts",
                "metadata_backups",
                "metadata_validation_results",
                "metadata_save_events",
                "metadata_materializations",
                "source_observations",
                "sync_jobs",
            ]:
                count = connection.execute(f"select count(*) from {table} where source_id = ?", (source["id"],)).fetchone()[0]
                assert count == 0
            env_drafts = connection.execute(
                "select count(*) from environment_metadata_editor_drafts where environment_id = ?",
                (env["id"],),
            ).fetchone()[0]
            assert env_drafts == 0

    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)
