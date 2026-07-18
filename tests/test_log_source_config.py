from pathlib import Path

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs.source_config import resolve_log_source_paths


def _source(uri: Path, source_config_json: str | None = None) -> EnvironmentSource:
    return EnvironmentSource(
        environment_id=1,
        source_kind="logs",
        uri=str(uri),
        enabled=True,
        source_config_json=source_config_json,
    )


def test_base_log_path_resolves_only_analyst_etl_and_system_logs(tmp_path: Path):
    base = tmp_path / "logs"
    analyst = base / "etl_logs" / "analyst"
    debug = base / "etl_logs" / "debug_json"
    system = base / "system_logs"
    analyst.mkdir(parents=True)
    debug.mkdir()
    system.mkdir()

    paths = resolve_log_source_paths(_source(base))

    assert Path(paths.base_log_uri or "") == base
    assert Path(paths.etl_logs_uri or "") == analyst
    assert Path(paths.system_logs_uri or "") == system
    assert "debug_json" not in (paths.etl_logs_uri or "")


def test_explicit_etl_root_is_scoped_to_analyst(tmp_path: Path):
    etl_root = tmp_path / "logs" / "etl_logs"
    analyst = etl_root / "analyst"
    analyst.mkdir(parents=True)

    paths = resolve_log_source_paths(_source(etl_root))

    assert Path(paths.etl_logs_uri or "") == analyst


def test_explicit_analyst_path_remains_supported(tmp_path: Path):
    analyst = tmp_path / "logs" / "etl_logs" / "analyst"
    (analyst / "dataflow_run_log").mkdir(parents=True)

    paths = resolve_log_source_paths(_source(analyst))

    assert Path(paths.etl_logs_uri or "") == analyst
