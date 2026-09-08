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
    data_dir = tmp_path / "data"
    studio = data_dir / "db" / "studio.db"
    result_cache = data_dir / "cache" / "result-cache.db"
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATABASE_URL", f"sqlite:///{studio.as_posix()}")
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", f"sqlite:///{result_cache}")

    normalized_openapi()

    assert not studio.exists()
    assert not result_cache.exists()
