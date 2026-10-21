from __future__ import annotations

import json
from pathlib import Path

from datacoolie_studio.domains.metadata.reader import read_metadata_file
from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources


def _write_dataflow(path: Path, name: str, *, wrapped: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "name": name,
        "stage": "bronze",
        "source": {"connection_name": "raw", "table": name},
        "destination": {"connection_name": "lake", "table": name},
    }
    payload = {"dataflows": [item]} if wrapped else item
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_project_scan_supports_all_canonical_dataflow_fragment_layouts(tmp_path: Path):
    metadata = tmp_path / "metadata"
    _write_dataflow(metadata / "dataflows.json", "compact")
    _write_dataflow(metadata / "dataflows" / "orders.json", "branch_file")
    _write_dataflow(metadata / "dataflows" / "bronze.json", "stage_file")
    _write_dataflow(metadata / "dataflows" / "orders" / "bronze.json", "branch_stage")
    _write_dataflow(metadata / "dataflows" / "bronze" / "sharded.json", "stage_dataflow")

    discovered = discover_datacoolie_project_sources(str(tmp_path), include_code=False)

    assert discovered.errors == []
    assert {source.label for source in discovered.metadata_sources} == {
        "dataflows.json",
        "dataflows/orders.json",
        "dataflows/bronze.json",
        "dataflows/orders/bronze.json",
        "dataflows/bronze/sharded.json",
    }
    assert sum(source.record_counts["dataflows"] for source in discovered.metadata_sources) == 5


def test_modular_metadata_reader_wraps_array_and_single_record_fragments(tmp_path: Path):
    array_path = tmp_path / "metadata" / "dataflows" / "bronze.json"
    array_path.parent.mkdir(parents=True)
    array_path.write_text(json.dumps([{"name": "array", "stage": "bronze"}]), encoding="utf-8")
    single_path = tmp_path / "metadata" / "dataflows" / "bronze" / "single.json"
    single_path.parent.mkdir(parents=True)
    single_path.write_text(json.dumps({"name": "single", "stage": "bronze"}), encoding="utf-8")

    assert read_metadata_file(str(array_path))["dataflows"][0]["name"] == "array"
    assert read_metadata_file(str(single_path))["dataflows"][0]["name"] == "single"


def test_project_scan_follows_marker_roots_and_discovers_multiple_function_roots(
    tmp_path: Path,
):
    metadata_root = tmp_path / "authored" / "metadata"
    functions_one = tmp_path / "src" / "functions"
    functions_two = tmp_path / "shared" / "functions"
    metadata_root.mkdir(parents=True)
    functions_one.mkdir(parents=True)
    functions_two.mkdir(parents=True)
    (metadata_root / "connections.json").write_text(
        json.dumps({"connections": [{"name": "raw"}]}),
        encoding="utf-8",
    )
    (functions_one / "orders.py").write_text("def orders():\n    return 1\n", encoding="utf-8")
    (functions_two / "finance.py").write_text("def finance():\n    return 2\n", encoding="utf-8")
    (tmp_path / "datacoolie.yml").write_text(
        """
components:
  metadata:
    path: authored/metadata
  functions:
    - path: src/functions
    - path: shared/functions
""".lstrip(),
        encoding="utf-8",
    )

    discovered = discover_datacoolie_project_sources(str(tmp_path))

    assert [source.uri for source in discovered.metadata_sources] == [
        str(metadata_root / "connections.json")
    ]
    assert {source.uri for source in discovered.code_artifacts} == {
        str(functions_one),
        str(functions_two),
    }
    assert all(
        source.source_config["functions_root_uris"]
        == [str(functions_one), str(functions_two)]
        for source in discovered.code_artifacts
    )


def test_project_scan_source_config_keeps_effective_explicit_roots(tmp_path: Path):
    metadata_root = tmp_path / "submitted-metadata"
    functions_root = tmp_path / "submitted-functions"
    metadata_root.mkdir()
    functions_root.mkdir()
    (metadata_root / "connections.json").write_text(
        json.dumps({"connections": [{"name": "raw"}]}),
        encoding="utf-8",
    )
    (functions_root / "orders.py").write_text(
        "def orders():\n    return 1\n",
        encoding="utf-8",
    )

    discovered = discover_datacoolie_project_sources(
        str(tmp_path),
        metadata_uri=str(metadata_root),
        code_uri=str(functions_root),
    )

    assert discovered.errors == []
    metadata = discovered.metadata_sources[0].source_config
    code = discovered.code_artifacts[0].source_config
    assert metadata["metadata_root_uris"] == [str(metadata_root)]
    assert metadata["effective_component_path_provenance"]["metadata"] == "explicit_uri"
    assert code["metadata_root_uris"] == [str(metadata_root)]
    assert code["functions_root_uris"] == [str(functions_root)]
    assert code["effective_component_path_provenance"]["functions"] == "explicit_uri"


def test_project_scan_supports_multiple_sql_and_function_fallback_roots(
    tmp_path: Path,
):
    sql_roots = [tmp_path / "queries" / "core", tmp_path / "queries" / "finance"]
    function_roots = [
        tmp_path / "functions" / "core",
        tmp_path / "functions" / "finance",
    ]
    for index, root in enumerate(sql_roots):
        root.mkdir(parents=True)
        (root / f"query_{index}.sql").write_text("SELECT 1;\n", encoding="utf-8")
    for index, root in enumerate(function_roots):
        root.mkdir(parents=True)
        (root / f"transform_{index}.py").write_text(
            f"def transform_{index}(value):\n    return value\n",
            encoding="utf-8",
        )

    discovered = discover_datacoolie_project_sources(
        str(tmp_path),
        include_metadata=False,
        sql_subpaths=["queries/core", "queries/finance"],
        code_subpaths=["functions/core", "functions/finance"],
    )

    assert discovered.errors == []
    assert [source.uri for source in discovered.sql_sources] == [
        str(root) for root in sql_roots
    ]
    assert {source.uri for source in discovered.code_artifacts} == {
        str(root) for root in function_roots
    }
    assert all(
        source.source_config["sql_root_uris"] == [str(root) for root in sql_roots]
        for source in discovered.sql_sources
    )
    assert all(
        source.source_config["functions_root_uris"] == [str(root) for root in function_roots]
        for source in discovered.code_artifacts
    )
