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
