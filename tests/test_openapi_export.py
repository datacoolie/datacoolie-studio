from pathlib import Path

from datacoolie_studio.openapi_export import export_openapi, normalized_openapi


def test_openapi_export_is_deterministic_and_checkable(tmp_path: Path):
    output = tmp_path / "openapi.json"

    assert export_openapi(output)
    first = output.read_bytes()
    assert first == normalized_openapi().encode("utf-8")
    assert export_openapi(output, check=True)

    output.write_text("{}\n", encoding="utf-8")
    assert not export_openapi(output, check=True)


def test_openapi_export_does_not_create_runtime_databases(tmp_path: Path, monkeypatch):
    studio = tmp_path / "studio.db"
    result_cache = tmp_path / "result-cache.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(studio))
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", f"sqlite:///{result_cache}")

    normalized_openapi()

    assert not studio.exists()
    assert not result_cache.exists()
