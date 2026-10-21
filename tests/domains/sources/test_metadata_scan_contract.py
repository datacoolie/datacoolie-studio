from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_marker_metadata_scan_keeps_empty_documents_without_runtime_validation(tmp_path: Path):
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    (metadata_root / "broken.json").write_text("{}", encoding="utf-8")
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  metadata:\n    path: metadata\n",
        encoding="utf-8",
    )

    from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources

    result = discover_datacoolie_project_sources(str(tmp_path), include_code=False)

    assert len(result.metadata_sources) == 1
    assert not result.errors


def test_markerless_scan_remains_permissive_for_non_metadata_files(tmp_path: Path):
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    (metadata_root / "broken.json").write_text("{}", encoding="utf-8")

    from datacoolie_studio.domains.sources.discovery import discover_metadata_sources

    result = discover_metadata_sources(str(metadata_root))

    assert result.metadata_sources == []
    assert not any(error.get("code") == "metadata_document_invalid" for error in result.errors)


def test_xls_metadata_is_discovered_as_not_datacoolie_compatible(tmp_path: Path):
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "connections"
    sheet.append(["name"])
    sheet.append(["lake"])
    for name in ("dataflows", "schema_hints"):
        workbook.create_sheet(name)
    xls_path = tmp_path / "metadata.xls"
    workbook.save(xls_path)

    from datacoolie_studio.domains.sources.discovery import discover_metadata_sources

    result = discover_metadata_sources(str(xls_path))

    assert len(result.metadata_sources) == 1
    assert result.metadata_sources[0].source_config["datacoolie_compatible"] is False
    assert result.metadata_sources[0].source_config["metadata_write_back_supported"] is False


def test_marker_scan_reports_symlinked_metadata_without_following_it(tmp_path: Path):
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    target = metadata_root / "target.json"
    target.write_text(
        json.dumps({"connections": [{"name": "lake"}]}),
        encoding="utf-8",
    )
    link = metadata_root / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("local test environment does not permit file symlinks")
    (tmp_path / "datacoolie.yml").write_text(
        "components:\n  metadata:\n    path: metadata\n",
        encoding="utf-8",
    )

    from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources

    result = discover_datacoolie_project_sources(str(tmp_path), include_code=False)

    assert [source.label for source in result.metadata_sources] == ["target.json"]
    assert any(error["code"] == "metadata_symlink" for error in result.errors)
