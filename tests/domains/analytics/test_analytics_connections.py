from __future__ import annotations

from pathlib import Path

import pytest

from datacoolie_studio.domains.analytics import access
from datacoolie_studio.domains.analytics.errors import AnalyticsRebuildRequired


def test_managed_connection_applies_effective_duckdb_resource_limits(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS", "2")
    monkeypatch.setenv("DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT", "512MiB")

    with access.connect(tmp_path / "analytics.duckdb") as connection:
        assert connection.execute("SELECT current_setting('threads')").fetchone()[0] == 2
        assert connection.execute("SELECT current_setting('memory_limit')").fetchone()[0] == "512.0 MiB"
        assert connection.execute("SELECT current_setting('temp_directory')").fetchone()[0] == str(
            tmp_path / "cache" / "analytics-tmp"
        )


def test_managed_connection_and_settings_report_same_auto_budget(monkeypatch, tmp_path):
    from datacoolie_studio.core import config
    from datacoolie_studio.domains.analytics.maintenance import analytics_resource_limits

    monkeypatch.setenv('DATACOOLIE_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_THREADS', 'auto')
    monkeypatch.setenv('DATACOOLIE_STUDIO_ANALYTICS_DUCKDB_MEMORY_LIMIT', 'auto')
    monkeypatch.setattr(config, 'available_cpu_count', lambda: 32)
    monkeypatch.setattr(config, 'available_memory_bytes', lambda: 2 * 1024**3)
    reported = analytics_resource_limits()
    with access.connect(tmp_path / 'analytics.duckdb') as connection:
        assert reported['duckdb_threads'] == connection.execute("SELECT current_setting('threads')").fetchone()[0] == 4
        assert reported['duckdb_memory_limit'] == '1433MiB'
        assert connection.execute("SELECT current_setting('memory_limit')").fetchone()[0] == '1.3 GiB'


@pytest.mark.parametrize('outcome', ['ready', 'consumer_error', 'unavailable', 'upgrade_error', 'begin_error', 'rollback_error'])
def test_snapshot_reader_always_closes_and_releases_transaction(monkeypatch, tmp_path, outcome):
    database = tmp_path / 'analytics.duckdb'
    database.touch()

    class Connection:
        def __init__(self):
            self.commands = []
            self.closed = False

        def execute(self, command):
            self.commands.append(command)
            if (outcome == 'begin_error' and command == 'BEGIN TRANSACTION') or (outcome == 'rollback_error' and command == 'ROLLBACK'):
                raise RuntimeError(outcome)

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(access, 'analytics_database_path', lambda: database)
    monkeypatch.setattr(access, 'connect', lambda *args, **kwargs: connection)

    def token(conn, source_ids):
        if outcome in {'unavailable', 'upgrade_error'}:
            raise AnalyticsRebuildRequired('not ready', source_ids=source_ids, missing_source_ids=source_ids, reason='not_ready')
        return 'generation-1'

    upgrade_error = AnalyticsRebuildRequired('upgrading', source_ids=[1], missing_source_ids=[1], reason='analytics_upgrade_in_progress')
    monkeypatch.setattr(access, 'materialization_token_from_connection', token)
    monkeypatch.setattr(access, '_upgrade_error', lambda source_ids: upgrade_error if outcome == 'upgrade_error' else None)

    def consume():
        with access.reader([1], snapshot=True) as result:
            if outcome == 'unavailable':
                assert result[0] is None and result[1] == []
                assert ':unavailable:not_ready:1' in result[2]
            else:
                assert result == (connection, [1], 'generation-1')
            if outcome == 'consumer_error':
                raise RuntimeError(outcome)

    if outcome == 'upgrade_error':
        with pytest.raises(AnalyticsRebuildRequired, match='upgrading'):
            consume()
    elif outcome in {'consumer_error', 'begin_error', 'rollback_error'}:
        with pytest.raises(RuntimeError, match=outcome):
            consume()
    else:
        consume()
    assert connection.closed
    assert connection.commands == (['BEGIN TRANSACTION'] if outcome == 'begin_error' else ['BEGIN TRANSACTION', 'ROLLBACK'])
