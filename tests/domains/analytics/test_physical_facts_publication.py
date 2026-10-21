from __future__ import annotations

import json

import duckdb
import pytest

from datacoolie_studio.domains.analytics import maintenance, publication, schema
from datacoolie_studio.domains.analytics.serving_facts import validate_monitoring_serving_facts
from tests.support.monitoring_fixture import build_analytics_fixture
from tests.support.v6_records import native_record


def _validate(conn):
    validate_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE)


def _digest(path):
    with duckdb.connect(str(path), read_only=True) as conn:
        return tuple(conn.execute(f"SELECT count(*), sum(hash(*COLUMNS(*))) FROM {table}").fetchone()
                     for table in (schema.DATAFLOW_TABLE, schema.JOB_TABLE, schema.CACHE_SOURCES_TABLE,
                                   schema.FILTER_VALUES_TABLE, schema.ANALYTICS_META_TABLE,
                                   'monitoring_dataflow_facts', 'monitoring_job_facts'))


def test_source_purge_publishes_matching_facts_and_generation(tmp_path, monkeypatch):
    path = tmp_path / 'analytics.duckdb'
    build_analytics_fixture(path, source_ids=[7, 8], dataflow_rows=20)
    monkeypatch.setattr(maintenance, 'analytics_database_path', lambda: path)
    with duckdb.connect(str(path), read_only=True) as conn:
        generation = conn.execute('SELECT generation FROM etl_analytics_meta').fetchone()[0]
    result = maintenance.purge_source_ids([7])
    assert result['dataflow_rows_deleted'] > 0
    with duckdb.connect(str(path), read_only=True) as conn:
        for table in (schema.DATAFLOW_TABLE, schema.JOB_TABLE, 'monitoring_dataflow_facts', 'monitoring_job_facts'):
            assert conn.execute(f'SELECT count(*) FROM {table} WHERE _source_id = 7').fetchone() == (0,)
        assert conn.execute('SELECT generation FROM etl_analytics_meta').fetchone() == (generation + 1,)
        assert conn.execute('SELECT source_id FROM etl_cache_sources').fetchall() == [(8,)]
        _validate(conn)


def test_source_purge_validation_failure_rolls_back_sources_facts_and_generation(tmp_path, monkeypatch):
    path = tmp_path / 'analytics.duckdb'
    build_analytics_fixture(path, source_ids=[7, 8], dataflow_rows=20)
    monkeypatch.setattr(maintenance, 'analytics_database_path', lambda: path)
    before = _digest(path)
    def reject(*args, **kwargs):
        raise RuntimeError('injected publish rejection')
    monkeypatch.setattr(publication, 'validate_monitoring_serving_facts', reject)
    with pytest.raises(RuntimeError, match='injected publish rejection'):
        maintenance.purge_source_ids([7])
    assert _digest(path) == before


def test_source_scoped_refresh_matches_full_rebuild_and_preserves_unrelated_source(tmp_path):
    from datacoolie_studio.db.models import utc_now
    from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts

    path = tmp_path / "analytics.duckdb"
    build_analytics_fixture(path, source_ids=[7, 8], dataflow_rows=20)
    with duckdb.connect(str(path)) as conn:
        tables = ("monitoring_dataflow_facts", "monitoring_job_facts")
        untouched = [conn.execute(f"SELECT * FROM {table} WHERE _source_id=8 ORDER BY ALL").fetchall() for table in tables]
        conn.execute("UPDATE etl_job_runs SET engine_name='native-new' WHERE _source_id=7")
        conn.execute("BEGIN")
        publication.publish_generation(conn, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                                       job_column_types=schema.JOB_COLUMN_TYPES, published_at=utc_now(), source_ids=[7])
        conn.execute("COMMIT")
        scoped = [conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall() for table in tables]
        assert [conn.execute(f"SELECT * FROM {table} WHERE _source_id=8 ORDER BY ALL").fetchall() for table in tables] == untouched
        rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE,
                                        dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)
        assert [conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall() for table in tables] == scoped
        _validate(conn)


def test_legacy_source_purge_cannot_publish_current_generation(tmp_path, monkeypatch):
    path = tmp_path / 'analytics.duckdb'
    build_analytics_fixture(path, source_ids=[7, 8], dataflow_rows=20)
    monkeypatch.setattr(maintenance, 'analytics_database_path', lambda: path)
    with duckdb.connect(str(path)) as conn:
        conn.execute('UPDATE etl_analytics_meta SET schema_version = 16')
        generation = conn.execute('SELECT generation FROM etl_analytics_meta').fetchone()[0]
    maintenance.purge_source_ids([7])
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute('SELECT schema_version, generation FROM etl_analytics_meta').fetchone() == (16, generation)
        assert conn.execute('SELECT count(*) FROM monitoring_dataflow_facts WHERE _source_id = 7').fetchone() == (0,)


@pytest.mark.parametrize('table', ['monitoring_dataflow_facts', 'monitoring_job_facts'])
def test_consistently_corrupt_derived_dates_still_fail_source_parity(tmp_path, table):
    path = tmp_path / 'analytics.duckdb'
    build_analytics_fixture(path, source_ids=[7], dataflow_rows=20)
    with duckdb.connect(str(path)) as conn:
        conn.execute(f"UPDATE {table} SET __event_time = TIMESTAMPTZ '2099-01-01', event_time = TIMESTAMPTZ '2099-01-01', __run_date = DATE '2099-01-01', run_date = DATE '2099-01-01'")
        with pytest.raises(RuntimeError, match='parity'):
            _validate(conn)


def test_duplicate_identity_and_stale_job_context_fail_publication_gate(tmp_path):
    path = tmp_path / 'analytics.duckdb'
    build_analytics_fixture(path, source_ids=[7], dataflow_rows=20)
    with duckdb.connect(str(path)) as conn:
        conn.execute('BEGIN')
        conn.execute("DELETE FROM monitoring_dataflow_facts WHERE dataflow_run_id = 'run-1'")
        conn.execute("INSERT INTO monitoring_dataflow_facts SELECT * FROM monitoring_dataflow_facts WHERE dataflow_run_id = 'run-0'")
        with pytest.raises(RuntimeError, match='not unique'):
            _validate(conn)
        conn.execute('ROLLBACK')
        conn.execute("UPDATE monitoring_dataflow_facts SET engine_name = 'stale'")
        with pytest.raises(RuntimeError, match='job context parity'):
            _validate(conn)


def test_job_only_replacement_and_removal_update_existing_dataflow_context(tmp_path):
    from datacoolie_studio.domains.analytics import store

    path = tmp_path / 'analytics.duckdb'
    flow = native_record({'job_id': 'shared', 'log_session_id': None})
    job = native_record({'_type': 'job_run_log', 'job_id': 'shared',
                         'log_session_id': None, 'engine_name': 'first'})
    def publish(records, changed=(), removed=()):
        return store.publish_rows(7, [], records, list(removed), list(changed), database_path=path)
    from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
    flow_file = tmp_path / 'flow.json'
    flow_file.write_text(json.dumps(flow), encoding='utf-8')
    initial = store.publish_rows(7, [LogFileDescriptor(str(flow_file), 'dataflow_json', '{}', str(flow_file))],
                                 [('job.json', 'job_json', '{}', job)], [], [], database_path=path)
    assert initial['published'], initial
    job['engine_name'] = 'replacement'
    assert publish([('job.json', 'job_json', '{"revision":2}', job)], changed=('job.json',))['published']
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute('SELECT engine_name FROM monitoring_dataflow_facts').fetchone() == ('replacement',)
        _validate(conn)
    assert publish([], removed=('job.json',))['published']
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute('SELECT engine_name FROM monitoring_dataflow_facts').fetchone() == ('unknown',)
        assert conn.execute('SELECT count(*) FROM monitoring_job_facts').fetchone() == (0,)
        _validate(conn)


def test_equal_observations_keep_canonical_provenance_and_removal_reselects(tmp_path):
    from datacoolie_studio.domains.analytics import store

    path = tmp_path / 'analytics.duckdb'
    row = native_record({})
    from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
    descriptors = []
    for name in ('z.json', 'a.json'):
        source_file = tmp_path / name
        source_file.write_text(json.dumps(row), encoding='utf-8')
        descriptors.append(LogFileDescriptor(name, 'dataflow_json', '{}', str(source_file)))
    initial = store.publish_rows(7, descriptors, [], [], [], database_path=path)
    assert initial['published'], initial
    with duckdb.connect(str(path)) as conn:
        assert conn.execute('SELECT _file_uri FROM monitoring_dataflow_facts').fetchone() == ('a.json',)
        conn.execute("UPDATE monitoring_dataflow_facts SET _file_uri = 'z.json'")
        with pytest.raises(RuntimeError, match='winner parity'):
            _validate(conn)
    assert store.publish_rows(7, [], [], ['a.json'], [], database_path=path)['published']
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute('SELECT _file_uri FROM monitoring_dataflow_facts').fetchone() == ('z.json',)
        _validate(conn)
