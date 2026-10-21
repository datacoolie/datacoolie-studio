from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Base, Environment, EnvironmentSource, Project
from datacoolie_studio.domains.sources.project_config import (
    ProjectConfigError,
    resolve_project_component_paths,
)
from datacoolie_studio.domains.storage.errors import StorageAccessError
from datacoolie_studio.domains.sql.operations import (
    SqlOperationError,
    normalize_pending_sql_files,
    parse_sql_file_reference,
)
from datacoolie_studio.domains.sql.reader import (
    SqlReferenceError,
    resolve_sql_file_target,
    resolve_sql_reference,
)


def _metadata_source(project: Path) -> EnvironmentSource:
    return EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri=str(project / "metadata" / "source.json"),
        enabled=True,
        storage_provider="local",
        storage_auth_mode="none",
        source_config_json=json.dumps(
            {
                "project_uri": str(project),
                "metadata_root_uri": str(project / "metadata"),
                "sql_root_uri": str(project / "queries"),
            }
        ),
    )


@pytest.mark.parametrize("marker", [None, "components:\n  metadata: metadata\n"])
def test_sql_reader_preserves_full_fallback_roots(tmp_path, marker):
    if marker is not None:
        (tmp_path / "datacoolie.yml").write_text(marker, encoding="utf-8")
    roots = resolve_project_component_paths(
        str(tmp_path), sql_override=["queries_a", "queries_b"],
        functions_override=["functions_a", "functions_b"],
    )
    source = _metadata_source(tmp_path)
    source.source_config_json = json.dumps({**roots.as_config(), "discovery_mode": "datacoolie_project"})
    target = resolve_sql_file_target(source, "queries_b/orders.sql")
    assert Path(target["file_uri"]) == tmp_path / "queries_b" / "orders.sql"
    assert Path(target["base_uri"]) == tmp_path / "queries_b"


def test_project_component_paths_use_defaults_without_marker(tmp_path: Path):
    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.config_found is False
    assert paths.metadata_path == "metadata"
    assert paths.sql_path == "sql"
    assert paths.functions_path == "functions"


def test_project_discovery_auto_skips_missing_optional_components(tmp_path: Path):
    functions = tmp_path / "functions"
    functions.mkdir()
    (functions / "transform.py").write_text(
        "def transform(value):\n    return value\n",
        encoding="utf-8",
    )

    from datacoolie_studio.domains.sources.discovery import (
        discover_datacoolie_project_sources,
    )

    discovered = discover_datacoolie_project_sources(str(tmp_path))

    assert discovered.metadata_sources == []
    assert discovered.sql_sources == []
    assert len(discovered.code_artifacts) == 1
    assert discovered.errors == []


def test_project_discovery_registers_an_existing_sql_root(tmp_path: Path):
    sql_root = tmp_path / "sql"
    sql_root.mkdir()
    (sql_root / "orders.sql").write_text("SELECT 1;\n", encoding="utf-8")

    from datacoolie_studio.domains.sources.discovery import (
        discover_datacoolie_project_sources,
    )

    discovered = discover_datacoolie_project_sources(str(tmp_path))

    assert discovered.metadata_sources == []
    assert discovered.code_artifacts == []
    assert discovered.errors == []
    assert [(item.source_kind, item.uri) for item in discovered.sql_sources] == [
        ("sql", str(sql_root))
    ]
    assert discovered.sql_sources[0].source_config["discovery_managed"] is True


def test_remote_missing_marker_uses_defaults_when_provider_wraps_not_found():
    class MissingConfigAdapter:
        def open_read(self, _uri: str):
            try:
                raise FileNotFoundError("datacoolie.yml")
            except FileNotFoundError as exc:
                raise StorageAccessError("remote open failed", provider="dbfs") from exc

    paths = resolve_project_component_paths(
        "dbfs:/Volumes/catalog/schema/project",
        adapter=MissingConfigAdapter(),  # type: ignore[arg-type]
    )

    assert paths.config_found is False
    assert paths.metadata_path == "metadata"
    assert paths.sql_path == "sql"
    assert paths.functions_path == "functions"


def test_project_component_paths_read_datacoolie_yaml(tmp_path: Path):
    (tmp_path / "datacoolie.yml").write_text(
        """
schema_version: 1
project:
  name: orders
components:
  metadata:
    path: authored/metadata
  sql: authored/queries
  functions:
    path: src/functions
environments:
  dev:
    platform: local
""".lstrip(),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.config_found is True
    assert paths.metadata_uri == str(tmp_path / "authored" / "metadata")
    assert paths.sql_uri == str(tmp_path / "authored" / "queries")
    assert paths.functions_uri == str(tmp_path / "src" / "functions")
    assert paths.provenance == {
        "metadata": "datacoolie.yml",
        "sql": "datacoolie.yml",
        "functions": "datacoolie.yml",
    }
    assert paths.config_revision and paths.config_revision["content_hash"]


def test_project_component_paths_use_manifest_and_keep_partial_fallbacks(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "datacoolie_environment",
                "build_id": "build-1",
                "environment": "dev",
                "components": {
                    "metadata": {"path": "release/metadata", "files": ["metadata.json"]},
                    "sql": [
                        {"path": "release/sql/core", "files": ["orders.sql"]},
                        {"path": "release/sql/finance", "files": ["ledger.sql"]},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(
        str(tmp_path),
        metadata_override="fallback-metadata",
        sql_override="fallback-sql",
        functions_override="fallback-functions",
    )

    assert paths.directive_kind == "manifest.json"
    assert paths.manifest_found is True
    assert paths.metadata_paths == ("release/metadata",)
    assert paths.sql_paths == ("release/sql/core", "release/sql/finance")
    assert paths.functions_paths == ("fallback-functions",)
    assert paths.provenance == {
        "metadata": "manifest.json",
        "sql": "manifest.json",
        "functions": "default_input",
    }
    assert paths.component_descriptors["sql"][1]["files"] == ["ledger.sql"]


def test_project_component_paths_accept_multiple_sql_and_function_fallbacks(
    tmp_path: Path,
):
    paths = resolve_project_component_paths(
        str(tmp_path),
        sql_override=["submitted/sql/core", "submitted/sql/finance"],
        functions_override=["submitted/functions/core", "shared/functions"],
    )

    assert paths.sql_paths == ("submitted/sql/core", "submitted/sql/finance")
    assert paths.functions_paths == ("submitted/functions/core", "shared/functions")
    assert paths.sql_uris == tuple(
        str(tmp_path / path) for path in paths.sql_paths
    )
    assert paths.functions_uris == tuple(
        str(tmp_path / path) for path in paths.functions_paths
    )
    assert paths.provenance["sql"] == "default_input"
    assert paths.provenance["functions"] == "default_input"


def test_project_component_paths_reject_traversal_in_array_fallback(
    tmp_path: Path,
):
    with pytest.raises(ProjectConfigError, match=r"default sql path\[1\]"):
        resolve_project_component_paths(
            str(tmp_path),
            sql_override=["submitted/sql", "../outside"],
        )


def test_datacoolie_yaml_wins_over_manifest_per_selected_directive(tmp_path: Path):
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  sql: authored/sql\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "datacoolie_environment",
                "build_id": "build-1",
                "environment": "dev",
                "components": {"sql": "release/sql"},
            }
        ),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.directive_kind == "datacoolie.yml"
    assert paths.sql_path == "authored/sql"
    assert paths.manifest_found is False


def test_invalid_marker_falls_back_with_visible_diagnostic(tmp_path: Path):
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  metadata:\n    path: ../outside\n  sql: authored/sql\n",
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(
        str(tmp_path),
        metadata_override="submitted/metadata",
    )

    assert paths.metadata_path == "submitted/metadata"
    assert paths.sql_path == "authored/sql"
    assert paths.provenance["metadata"] == "default_input"
    assert paths.provenance["sql"] == "datacoolie.yml"
    assert paths.diagnostics[0]["code"] == "component_path_invalid"


def test_build_manifest_does_not_redirect_scan_to_another_environment(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "datacoolie_build",
                "build_id": "build-1",
                "components": {"metadata": {"path": "build/metadata"}},
            }
        ),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.metadata_path == "metadata"
    assert paths.diagnostics[0]["code"] == "manifest_invalid"
    assert "environment artifact root" in paths.diagnostics[0]["message"]


def test_project_component_paths_reject_traversal_in_fallback_input(tmp_path: Path):
    (tmp_path / "datacoolie.yml").write_text(
        """
project:
  name: unsafe
components:
  metadata:
    path: ../outside
environments:
  dev: {}
""".lstrip(),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.metadata_path == "metadata"
    assert paths.diagnostics[0]["code"] == "component_path_invalid"


@pytest.mark.parametrize(
    "unsafe_path",
    ["authored/sql?version=1", "authored/sql#fragment", "authored/%2f/sql", "~/.sql"],
)
def test_project_component_paths_reject_encoded_or_uri_like_marker_paths(
    tmp_path: Path,
    unsafe_path: str,
):
    (tmp_path / "datacoolie.yml").write_text(
        f"components:\n  sql: {unsafe_path}\n",
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path), sql_override="submitted/sql")

    assert paths.sql_path == "submitted/sql"
    assert paths.provenance["sql"] == "default_input"
    assert paths.diagnostics[0]["code"] == "component_path_invalid"


def test_project_component_paths_rejects_unknown_manifest_type_with_diagnostic(
    tmp_path: Path,
):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "some_other_artifact",
                "components": {"metadata": {"path": "release/metadata"}},
            }
        ),
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path))

    assert paths.directive_kind == "manifest.json"
    assert paths.metadata_path == "metadata"
    assert paths.diagnostics[0]["code"] == "manifest_invalid"
    assert "artifact_type" in paths.diagnostics[0]["message"]


def test_project_component_paths_rejects_local_symlink_root_outside_supplied_root(
    tmp_path: Path,
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "linked-metadata"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("local test environment does not permit directory symlinks")
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  metadata: linked-metadata\n",
        encoding="utf-8",
    )

    paths = resolve_project_component_paths(str(tmp_path), metadata_override="submitted/metadata")

    assert paths.metadata_path == "submitted/metadata"
    assert paths.provenance["metadata"] == "default_input"
    assert paths.diagnostics[0]["code"] == "component_path_invalid"


def test_sql_reference_reads_sql_root_and_artifact_root(tmp_path: Path):
    sql_root = tmp_path / "queries"
    sql_root.mkdir()
    sql_file = sql_root / "orders.sql"
    sql_file.write_text("SELECT * FROM orders\n", encoding="utf-8")
    source = _metadata_source(tmp_path)

    shorthand = resolve_sql_reference(None, source, "orders.sql")
    artifact = resolve_sql_reference(None, source, "artifact:/queries/orders.sql")

    assert shorthand["kind"] == "file"
    assert shorthand["content"].replace("\r\n", "\n") == "SELECT * FROM orders\n"
    assert shorthand["base_kind"] == "sql_root"
    assert shorthand["file_uri"] == str(sql_file)
    assert artifact["base_kind"] == "project_root"
    assert artifact["file_uri"] == str(sql_file)
    assert artifact["revision"]["content_hash"]


def test_sql_reference_uses_a_manual_sql_source_when_metadata_has_no_sql_root(
    tmp_path: Path,
):
    sql_root = tmp_path / "queries"
    sql_root.mkdir()
    sql_file = sql_root / "orders.sql"
    sql_file.write_text("SELECT 42;\n", encoding="utf-8")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        project = Project(name="manual-sql")
        session.add(project)
        session.flush()
        environment = Environment(project_id=project.id, name="dev")
        session.add(environment)
        session.flush()
        metadata_source = EnvironmentSource(
            environment_id=environment.id,
            source_kind="metadata",
            uri=str(tmp_path / "metadata.json"),
            enabled=True,
            storage_provider="local",
            storage_auth_mode="none",
            source_config_json=json.dumps({"project_uri": str(tmp_path)}),
        )
        session.add(metadata_source)
        session.flush()
        session.add(
            EnvironmentSource(
                environment_id=environment.id,
                source_kind="sql",
                uri=str(sql_root),
                enabled=True,
                storage_provider="local",
                storage_auth_mode="none",
                source_config_json=json.dumps({"sql_root_uri": str(sql_root)}),
            )
        )
        session.commit()

        resolved = resolve_sql_reference(session, metadata_source, "orders.sql")

        assert resolved["content"].replace("\r\n", "\n") == "SELECT 42;\n"
        assert resolved["file_uri"] == str(sql_file)
        assert resolved["base_kind"] == "sql_root"
    finally:
        session.close()


def test_sql_reference_requires_a_root_prefix_when_multiple_roots_are_configured(
    tmp_path: Path,
):
    core = tmp_path / "sql" / "core"
    finance = tmp_path / "sql" / "finance"
    core.mkdir(parents=True)
    finance.mkdir(parents=True)
    (core / "orders.sql").write_text("SELECT 1", encoding="utf-8")
    (finance / "ledger.sql").write_text("SELECT 2", encoding="utf-8")
    source = EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri=str(tmp_path / "metadata" / "source.json"),
        enabled=True,
        storage_provider="local",
        storage_auth_mode="none",
        source_config_json=json.dumps(
            {
                "project_uri": str(tmp_path),
                "sql_root_uris": [str(core), str(finance)],
            }
        ),
    )

    target = resolve_sql_file_target(source, "finance/ledger.sql")

    assert target["base_uri"] == str(finance)
    assert target["relative_path"] == "ledger.sql"
    assert resolve_sql_reference(None, source, "finance/ledger.sql")["content"] == "SELECT 2"
    with pytest.raises(SqlReferenceError, match="must start with one of"):
        resolve_sql_file_target(source, "ledger.sql")


def test_sql_reference_re_resolves_project_config_changes(tmp_path: Path):
    first_root = tmp_path / "queries"
    second_root = tmp_path / "authored" / "queries"
    first_root.mkdir()
    second_root.mkdir(parents=True)
    (first_root / "orders.sql").write_text("SELECT 1", encoding="utf-8")
    (second_root / "orders.sql").write_text("SELECT 2", encoding="utf-8")
    config = tmp_path / "datacoolie.yml"
    config.write_text("components:\n  sql: queries\n", encoding="utf-8")
    source = EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri=str(tmp_path / "metadata" / "source.json"),
        enabled=True,
        storage_provider="local",
        storage_auth_mode="none",
        source_config_json=json.dumps({
            "project_uri": str(tmp_path),
            "discovery_mode": "datacoolie_project",
            "component_paths": {
                "metadata": "metadata",
                "sql": "queries",
                "functions": "functions",
            },
            "component_path_provenance": {
                "metadata": "default",
                "sql": "datacoolie.yml",
                "functions": "default",
            },
        }),
    )

    first = resolve_sql_reference(None, source, "orders.sql")
    config.write_text("components:\n  sql: authored/queries\n", encoding="utf-8")
    second = resolve_sql_reference(None, source, "orders.sql")

    assert first["content"] == "SELECT 1"
    assert second["content"] == "SELECT 2"
    assert first["config_revision"] != second["config_revision"]


def test_sql_reference_preserves_subpath_fallback_when_marker_omits_sql(
    tmp_path: Path,
):
    sql_root = tmp_path / "authored" / "queries"
    sql_root.mkdir(parents=True)
    sql_file = sql_root / "orders.sql"
    sql_file.write_text("SELECT 7", encoding="utf-8")
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  metadata: metadata\n",
        encoding="utf-8",
    )
    source = EnvironmentSource(
        environment_id=1,
        source_kind="metadata",
        uri=str(tmp_path / "metadata" / "source.json"),
        enabled=True,
        storage_provider="local",
        storage_auth_mode="none",
        source_config_json=json.dumps(
            {
                "project_uri": str(tmp_path),
                "discovery_mode": "datacoolie_project",
                "component_paths": {
                    "metadata": "metadata",
                    "sql": "authored/queries",
                    "functions": "functions",
                },
                "component_path_provenance": {
                    "metadata": "datacoolie.yml",
                    "sql": "default_input",
                    "functions": "default",
                },
            }
        ),
    )

    resolved = resolve_sql_reference(None, source, "orders.sql")

    assert resolved["file_uri"] == str(sql_file)


def test_sql_reference_keeps_inline_query_and_rejects_unsafe_paths(tmp_path: Path):
    source = _metadata_source(tmp_path)

    inline = resolve_sql_reference(None, source, "SELECT 1")

    assert inline == {
        "declared_query": "SELECT 1",
        "kind": "inline",
        "content": "SELECT 1",
        "file_uri": None,
        "base_uri": None,
        "base_kind": None,
            "relative_path": None,
            "source_uri": source.uri,
            "revision": None,
            "config_revision": None,
        }
    with pytest.raises(SqlReferenceError):
        resolve_sql_file_target(source, "../orders.sql")
    with pytest.raises(SqlReferenceError):
        resolve_sql_reference(None, source, "orders.sql?version=2")


def test_sql_operation_allows_explicit_artifact_reference_without_extension():
    assert parse_sql_file_reference("artifact:/queries/orders") == (
        "queries/orders",
        "project_root",
    )
    with pytest.raises(SqlOperationError, match="end with .sql"):
        parse_sql_file_reference("queries/orders")


def test_pending_sql_operations_deduplicate_normalized_same_file_and_reject_divergent_content():
    operation = {
        "source_id": 1,
        "query": "orders.sql",
        "content": "SELECT 1",
    }

    normalized = normalize_pending_sql_files([
        operation,
        {**operation, "query": "./orders.sql"},
    ])

    assert len(normalized) == 1
    assert normalized[0]["relative_path"] == "orders.sql"
    with pytest.raises(SqlOperationError, match="same SQL file"):
        normalize_pending_sql_files([
            operation,
            {**operation, "query": "./orders.sql", "content": "SELECT 2"},
        ])
