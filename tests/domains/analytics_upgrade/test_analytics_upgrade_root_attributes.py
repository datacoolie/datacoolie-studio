from __future__ import annotations

import duckdb
import pytest

from datacoolie_studio.domains.analytics import cache_state, schema, schema_tables
from datacoolie_studio.domains.analytics_upgrade import service as upgrade
from datacoolie_studio.domains.logs import batching
from tests.domains.analytics_upgrade.analytics_upgrade_test_support import _workspace_with_sources, _write_old_live_cache


@pytest.mark.parametrize("old_version", [15, 16])
def test_source_replay_retains_rollback_and_publishes_four_log_tables(tmp_path, monkeypatch, old_version):
    session, sources = _workspace_with_sources(tmp_path, monkeypatch, count=2)
    session.close()
    live = tmp_path / 'analytics.duckdb'
    _write_old_live_cache(live, schema_version=old_version)
    original = live.read_bytes()
    monkeypatch.setattr(upgrade, 'analytics_database_path', lambda: live)
    result = upgrade.run_analytics_upgrade_once()
    assert result['state'] == 'succeeded', result
    with duckdb.connect(str(live), read_only=True) as conn:
        assert schema.typed_cache_schema_is_ready(conn)
        bases = {name for name, in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'").fetchall()}
        assert bases == {schema.DATAFLOW_TABLE, schema.JOB_TABLE, schema.ANALYTICS_META_TABLE, schema.CACHE_SOURCES_TABLE, schema.FILTER_VALUES_TABLE, "monitoring_dataflow_facts", "monitoring_job_facts"}
        assert conn.execute('SELECT source_id FROM etl_cache_sources ORDER BY source_id').fetchall() == [(source.id,) for source in sources]
        for table in (schema.DATAFLOW_TABLE, schema.JOB_TABLE):
            assert 'raw_record_json' not in schema.table_columns(conn, table)
    retained = list(tmp_path.glob(f'analytics.rollback-v{old_version}-*.duckdb'))
    assert len(retained) == 1
    assert retained[0].read_bytes() == original


def test_candidate_same_version_with_old_or_wrong_nested_columns_cannot_resume():
    with duckdb.connect() as conn:
        schema_tables.ensure_tables(conn)
        cache_state.mark_candidate_building(conn)
        assert cache_state.candidate_is_resumable(conn)
        conn.execute('ALTER TABLE etl_dataflow_runs DROP COLUMN attempts')
        conn.execute('ALTER TABLE etl_dataflow_runs ADD COLUMN attempts VARCHAR')
        assert not cache_state.candidate_is_resumable(conn)


def test_replay_checkpoint_rejects_changed_mapping_or_parser_fingerprint():
    fingerprint = batching.materialization_fingerprint()
    completed = {'status': 'completed', 'materialization_fingerprint': fingerprint}
    assert batching.resume_batches({'batches': {'one': completed}}) == {'one': completed}
    for obsolete in ({'status': 'completed'}, {**completed, 'materialization_fingerprint': 'old'}):
        assert batching.resume_batches({'batches': {'one': obsolete}}) == {}


def test_replay_checkpoint_rejects_changed_admission_schema(monkeypatch):
    from copy import deepcopy
    from datacoolie_studio.domains.analytics import schema_compiler

    fingerprint = batching.materialization_fingerprint()
    modified = deepcopy(schema_compiler.SCHEMA_DOCUMENT)
    modified['$defs']['typedDateTime']['properties']['__datetime__'] = {
        'type': 'string', 'format': 'date-time',
    }
    monkeypatch.setattr(schema_compiler, 'SCHEMA_DOCUMENT', modified)
    assert batching.materialization_fingerprint() != fingerprint
    checkpoint = {'status': 'completed', 'materialization_fingerprint': fingerprint}
    assert batching.resume_batches({'batches': {'one': checkpoint}}) == {}


def test_completed_source_reuse_requires_current_materialization_fingerprint(tmp_path):
    from datacoolie_studio.domains.analytics_upgrade.assessment import resumable_candidate_sources
    candidate = tmp_path / 'candidate.duckdb'
    with duckdb.connect(str(candidate)) as conn:
        schema_tables.ensure_tables(conn)
        cache_state.mark_candidate_building(conn)
        conn.execute("INSERT INTO etl_cache_sources VALUES (7, current_timestamp, 1)")
    snapshot = {'7': {'config_fingerprint': 'same'}}
    checkpoint = {'status': 'completed', 'config_fingerprint': 'same',
                  'materialization_fingerprint': batching.materialization_fingerprint()}
    def reusable(value):
        return resumable_candidate_sources(candidate, [7], previous_snapshot=snapshot,
                                          current_snapshot=snapshot, checkpoints={'7': value})
    assert reusable(checkpoint) == {7}
    assert reusable({**checkpoint, 'materialization_fingerprint': 'old'}) == set()
    assert reusable({'status': 'completed', 'config_fingerprint': 'same'}) == set()


def test_changed_admission_schema_prevents_completed_source_reuse(tmp_path, monkeypatch):
    from copy import deepcopy
    from datacoolie_studio.domains.analytics import schema_compiler
    from datacoolie_studio.domains.analytics_upgrade.assessment import resumable_candidate_sources

    candidate = tmp_path / 'candidate.duckdb'
    with duckdb.connect(str(candidate)) as conn:
        schema_tables.ensure_tables(conn)
        cache_state.mark_candidate_building(conn)
        conn.execute('INSERT INTO etl_cache_sources VALUES (7, current_timestamp, 1)')
    fingerprint = batching.materialization_fingerprint()
    modified = deepcopy(schema_compiler.SCHEMA_DOCUMENT)
    modified['$defs']['typedDateTime']['properties']['__datetime__'] = {'type': 'string', 'format': 'date-time'}
    monkeypatch.setattr(schema_compiler, 'SCHEMA_DOCUMENT', modified)
    snapshot = {'7': {'config_fingerprint': 'same'}}
    checkpoint = {'status': 'completed', 'config_fingerprint': 'same', 'materialization_fingerprint': fingerprint}
    assert resumable_candidate_sources(candidate, [7], previous_snapshot=snapshot,
                                       current_snapshot=snapshot, checkpoints={'7': checkpoint}) == set()


def test_schema_setup_recreates_wrong_kind_log_relation():
    with duckdb.connect() as conn:
        schema_tables.ensure_tables(conn)
        conn.execute("CREATE TABLE copied AS SELECT * FROM etl_dataflow_runs")
        conn.execute("DROP TABLE etl_dataflow_runs")
        conn.execute("CREATE VIEW etl_dataflow_runs AS SELECT * FROM copied")
        assert schema_tables.ensure_dataflow_cache_table(conn)
        assert schema.typed_table_schema_is_current(conn, schema.DATAFLOW_TABLE, schema.DATAFLOW_COLUMN_TYPES)
