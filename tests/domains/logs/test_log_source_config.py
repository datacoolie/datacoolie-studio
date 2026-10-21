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


def test_log_base_path_resolves_execution_and_system_logs(tmp_path: Path):
    base = tmp_path / "logs"
    execution = base / "execution_logs"
    system = base / "system_logs"
    execution.mkdir(parents=True)
    system.mkdir()

    paths = resolve_log_source_paths(_source(base))

    assert Path(paths.log_base_path or "") == base
    assert Path(paths.execution_logs_uri or "") == execution
    assert Path(paths.system_logs_uri or "") == system
    assert paths.mode == "log_base_path"


def test_explicit_execution_root_is_scoped_to_execution_logs(tmp_path: Path):
    execution_root = tmp_path / "logs" / "execution_logs"
    execution_root.mkdir(parents=True)

    paths = resolve_log_source_paths(_source(execution_root))

    assert Path(paths.execution_logs_uri or "") == execution_root
    assert Path(paths.log_base_path or "") == execution_root.parent


def test_explicit_system_root_remains_supported(tmp_path: Path):
    system = tmp_path / "logs" / "system_logs"
    system.mkdir(parents=True)

    paths = resolve_log_source_paths(_source(system))

    assert Path(paths.system_logs_uri or "") == system


def test_log_base_path_is_the_canonical_source_setting(tmp_path: Path):
    base = tmp_path / "logs"
    paths = resolve_log_source_paths(
        _source(base, '{"mode":"log_base_path","log_base_path":"%s"}' % base)
    )

    from datacoolie_studio.domains.logs.source_config import normalized_log_source_config

    assert paths.log_base_path == base.as_posix()
    assert normalized_log_source_config(
        _source(base, '{"log_base_path":"%s"}' % base)
    )["log_base_path"] == base.as_posix()
