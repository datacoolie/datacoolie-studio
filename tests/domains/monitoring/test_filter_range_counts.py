from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import duckdb

from datacoolie_studio.domains.analytics import access, cache_state, publication, schema, schema_tables
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring import filter_options
from datacoolie_studio.domains.monitoring.repository import monitoring_filter_options_read_model
from datacoolie_studio.domains.read_models.coordinator import InProcessResultBuildCoordinator
from datacoolie_studio.domains.read_models.provider import ResultCacheProvider
from datacoolie_studio.domains.read_models.sqlite_store import namespace_family
from tests.domains.read_models.test_result_cache import _MemoryStore
from tests.support.v6_records import insert_native_rows, native_record


def populate(conn):
    schema_tables.ensure_tables(conn)
    for source_id in [1, 2]:
        for name, instant, status in [('old', '2026-10-01T12:00:00Z', 'failed'), ('edge', '2026-10-09T00:00:00Z', 'succeeded')]:
            flow = native_record({'dataflow_run_id': name, 'job_id': 'job', 'stage': name, 'status': status, 'start_time': instant, 'end_time': instant, 'source_name': ' same ', 'destination_name': 'same'})
            insert_native_rows(conn, schema.DATAFLOW_TABLE, source_id, [(name+'.json', 'dataflow', '{}', flow)], schema.DATAFLOW_COLUMN_TYPES)
        # Duplicate record in same file counts as source evidence, connection once.
        insert_native_rows(conn, schema.DATAFLOW_TABLE, source_id, [('edge.json', 'dataflow', '{}', flow)], schema.DATAFLOW_COLUMN_TYPES)
        # A different source file remains another connection observation.
        insert_native_rows(conn, schema.DATAFLOW_TABLE, source_id, [('copy.json', 'dataflow', '{}', flow)], schema.DATAFLOW_COLUMN_TYPES)
        for observed in ['2026-10-01T13:00:00Z', '2026-10-10T13:00:00Z']:
            job = native_record({'_type': 'job_run_log', 'job_id': 'job', 'engine_name': 'polars', 'start_time': '2026-10-09T00:00:00Z', 'end_time': '2026-10-09T00:00:00Z', 'observed_at': observed})
            insert_native_rows(conn, schema.JOB_TABLE, source_id, [(observed+'.json', 'job', '{}', job)], schema.JOB_COLUMN_TYPES)
        cache_state.mark_cache_source(conn, source_id, refreshed_at=datetime.now(timezone.utc))
    publication.refresh_filter_values_for_sources(conn, [1, 2])
    schema.write_contract_metadata(conn)
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)
    conn.execute(f"UPDATE {schema.ANALYTICS_META_TABLE} SET build_state='ready'")


def counts(result, field):
    return {item['value']: item['count'] for item in result['options'].get(field, [])}


def test_ranges_keep_source_record_grain_and_exact_connection_deduplication():
    with duckdb.connect() as conn:
        populate(conn)
        context = (conn, [1, 2], 'test')
        all_time = monitoring_filter_options_read_model([], context, filters={'range': 'all'})
        broad = monitoring_filter_options_read_model([], context, filters={'range': 'custom', 'startTime': '2026-01-01T00:00:00Z'})
        assert all_time['options'] == broad['options']
        assert counts(all_time, 'operation_type') == {'etl': 8}
        assert counts(all_time, 'engine_name') == {'polars': 4}
        assert counts(all_time, 'connection') == {'same': 6}
        edge = monitoring_filter_options_read_model([], context, filters={'range': 'custom', 'startTime': '2026-10-09T00:00:00Z', 'endTime': '2026-10-09T00:00:00Z'})
        assert counts(edge, 'stage') == {'edge': 6, 'old': 0}
        assert counts(edge, 'connection') == {'same': 4}
        assert counts(edge, 'engine_name') == {'polars': 4}  # event time, not observed_at.
        empty = monitoring_filter_options_read_model([], (conn, [1], 'test'), filters={'range': 'custom', 'startTime': '2027-01-01T00:00:00Z'})
        assert counts(empty, 'stage') == {'edge': 0, 'old': 0}
        assert all(item['count'] == 0 for items in empty['options'].values() for item in items)
        one_source = monitoring_filter_options_read_model([], (conn, [1], 'test'), filters={'range': 'all'})
        assert counts(one_source, 'operation_type') == {'etl': 4}


def test_time_identity_uses_actual_timezone_and_rolls_at_report_boundaries():
    tz = timezone(timedelta(hours=7))
    before = datetime(2026, 10, 9, 16, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=1)
    scope = filter_options.filter_time_scope({'range': '7d', 'status': 'failed', 'startTime': 'ignored'}, tz, now=before)
    assert scope == {'range': 'custom', 'startTime': '2026-10-01T17:00:00+00:00', 'endTime': ''}
    assert filter_options.filter_time_scope({'range': '7d'}, tz, now=after)['startTime'] == '2026-10-02T17:00:00+00:00'
    assert filter_options.filter_time_scope({'range': '24h'}, tz, now=before)['startTime'] == '2026-10-08T16:00:00+00:00'
    assert filter_options.filter_time_scope({'range': '24h'}, tz, now=after)['startTime'] == '2026-10-08T17:00:00+00:00'
    assert filter_options.filter_time_scope({'range': 'all', 'startTime': '2026-10-01'}, tz) == {'range': 'all'}
    assert filter_options.filter_time_scope({'range': 'today'}, tz, now=before)['endTime'] == '2026-10-09T16:59:59.999999+00:00'


def test_cache_hits_skip_count_query_and_new_generation_rebuilds(monkeypatch):
    with duckdb.connect() as conn:
        populate(conn)
        @contextmanager
        def reader(paths, *, snapshot=False):
            assert snapshot
            conn.execute('BEGIN TRANSACTION')
            try:
                yield conn, [1], access.materialization_token_from_connection(conn, [1])
            finally:
                conn.execute('ROLLBACK')
        monkeypatch.setattr(filter_options, 'analytics_reader', reader)
        store = _MemoryStore()
        monkeypatch.setattr(filter_options, 'result_cache_provider', lambda: ResultCacheProvider(store, InProcessResultBuildCoordinator()))
        original = filter_options.monitoring_filter_options_read_model
        calls = []
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(filter_options, 'monitoring_filter_options_read_model', counted)
        def query(**extra):
            return filter_options.monitoring_filter_options([], None, environment_id=7, filters={'range': 'custom', 'startTime': '2026-10-09T00:00:00Z', **extra})
        first = query()
        assert query(status='failed', operationType='maintenance', grain='hour') == first
        assert len(calls) == 1
        cache_state.mark_cache_source(conn, 1, refreshed_at=datetime.now(timezone.utc))
        assert query() == first
        assert len(calls) == 2
        assert namespace_family(filter_options.FILTER_OPTIONS_NAMESPACE) == 'monitoring.page'
        store.invalidate(7)
        assert query() == first and len(calls) == 3


def test_publication_between_key_and_counts_cannot_mix_catalog_and_generation(tmp_path, monkeypatch):
    db = str(tmp_path/'snapshot.duckdb')
    with duckdb.connect(db) as writer:
        populate(writer)
        with duckdb.connect(db) as reader_conn:
            @contextmanager
            def reader(paths, *, snapshot=False):
                assert snapshot
                with access.reader([1], snapshot=True) as result:
                    yield result
            monkeypatch.setattr(access, 'analytics_database_path', lambda: tmp_path/'snapshot.duckdb')
            monkeypatch.setattr(access, 'connect', lambda *args, **kwargs: reader_conn.cursor())
            monkeypatch.setattr(filter_options, 'analytics_reader', reader)
            original = filter_options.monitoring_filter_options_read_model
            published = []
            def publish_then_count(*args, **kwargs):
                if not published:
                    writer.execute('BEGIN TRANSACTION')
                    writer.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage='new' WHERE _source_id=1")
                    publication.refresh_filter_values_for_sources(writer, [1])
                    cache_state.mark_cache_source(writer, 1, refreshed_at=datetime.now(timezone.utc))
                    writer.execute('COMMIT')
                    published.append(True)
                return original(*args, **kwargs)
            monkeypatch.setattr(filter_options, 'monitoring_filter_options_read_model', publish_then_count)
            store = _MemoryStore()
            monkeypatch.setattr(filter_options, 'result_cache_provider', lambda: ResultCacheProvider(store, InProcessResultBuildCoordinator()))
            first = filter_options.monitoring_filter_options([], None, environment_id=7)
            second = filter_options.monitoring_filter_options([], None, environment_id=7)
            assert counts(first, 'stage') == {'edge': 3, 'old': 1}
            assert counts(second, 'stage') == {'new': 4}
            assert len(store.values) == 2
