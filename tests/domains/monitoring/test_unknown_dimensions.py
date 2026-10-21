import json

import duckdb
import pytest

from datacoolie_studio.domains.analytics import publication, schema
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY, CONTEXT_PREFIX, VALUE_PREFIX, dimension_key, dimension_key_sql, dimension_label, dimension_predicate_sql
from datacoolie_studio.domains.monitoring.metrics.filtering import matches_filter_value, matches_connection
from datacoolie_studio.domains.monitoring.metrics.investigation import declared_destination_key_sql
from datacoolie_studio.domains.monitoring.read_models.common import filtered_ctes
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.repository import monitoring_filter_options_read_model
from tests.domains.monitoring.test_filter_range_counts import populate, counts


@pytest.mark.parametrize('value', [None, '', ' \t\r\n\v\f ', 'unknown', 'Unknown', 'null', 'none', 'all', UNKNOWN_KEY, VALUE_PREFIX + 'ff', CONTEXT_PREFIX + '[]', 'a|b', 'Éngine', ' Upper ', '\u00a0', '\u2003', '\ufeff'])
def test_dimension_key_sql_python_and_bound_selection_agree(value):
    key = dimension_key(value)
    with duckdb.connect() as conn:
        assert conn.execute(f'SELECT {dimension_key_sql("?")}', [value] * dimension_key_sql('?').count('?')).fetchone()[0] == key
        predicate, params = dimension_predicate_sql('value', [key])
        assert conn.execute(f'SELECT COUNT(*) FROM (SELECT ?::VARCHAR AS value) WHERE {predicate}', [value, *params]).fetchone()[0] == 1
    assert matches_filter_value(value, key)
    if dimension_key(value) == UNKNOWN_KEY:
        assert key == UNKNOWN_KEY and dimension_label(key) == 'Unknown'
    else:
        assert key != UNKNOWN_KEY


def rebuild(conn):
    publication.refresh_filter_values_for_sources(conn, [1, 2])
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES, job_column_types=schema.JOB_COLUMN_TYPES)


def test_catalog_counts_missing_literal_unknown_and_connection_exact_grain():
    with duckdb.connect() as conn:
        populate(conn)
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage=CASE WHEN _source_id=1 AND dataflow_run_id='old' THEN NULL WHEN _source_id=1 THEN '\t\n' WHEN dataflow_run_id='old' THEN 'unknown' ELSE 'Unknown' END, source_name=CASE WHEN dataflow_run_id='old' THEN '\tx' ELSE '\t\n' END, destination_name=CASE WHEN dataflow_run_id='old' THEN 'x' ELSE NULL END")
        conn.execute(f"UPDATE {schema.JOB_TABLE} SET engine_name=CASE WHEN _source_id=1 THEN NULL ELSE 'unknown' END")
        rebuild(conn)
        context = (conn, [1, 2], 'test')
        all_time = monitoring_filter_options_read_model([], context, filters={'range': 'all'})
        broad = monitoring_filter_options_read_model([], context, filters={'range': 'custom', 'startTime': '2026-01-01T00:00:00Z'})
        assert all_time['options'] == broad['options']
        assert counts(all_time, 'stage') == {UNKNOWN_KEY: 4, 'unknown': 1, 'Unknown': 3}
        assert counts(all_time, 'connection') == {'x': 2, UNKNOWN_KEY: 4}
        assert counts(all_time, 'engine_name') == {UNKNOWN_KEY: 2, 'unknown': 2}
        assert next(i for i in all_time['options']['stage'] if i['value'] == 'unknown')['label'] == 'unknown (value)'
        edge = monitoring_filter_options_read_model([], context, filters={'range': 'custom', 'startTime': '2026-10-09T00:00:00Z'})
        assert counts(edge, 'stage') == {UNKNOWN_KEY: 3, 'unknown': 0, 'Unknown': 3}
        assert counts(edge, 'connection') == {'x': 0, UNKNOWN_KEY: 4}
        empty = monitoring_filter_options_read_model([], context, filters={'range': 'custom', 'startTime': '2027-01-01'})
        assert all(i['count'] == 0 for v in empty['options'].values() for i in v)
        assert matches_connection({'source_name': '\t\n', 'destination_name': 'x'}, UNKNOWN_KEY)
        assert not matches_connection({'source_name': 'unknown', 'destination_name': 'x'}, UNKNOWN_KEY)


def test_filtered_pages_use_missing_context_and_keep_exact_destination_identity():
    with duckdb.connect() as conn:
        populate(conn)
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage=CASE WHEN _source_id=1 THEN ' \t' ELSE 'unknown' END, operation_type=CASE WHEN _source_id=1 THEN NULL ELSE 'etl' END, destination_name=' \t', destination_format=NULL")
        conn.execute(f"UPDATE {schema.JOB_TABLE} SET engine_name=CASE WHEN _source_id=1 THEN NULL ELSE 'unknown' END")
        rebuild(conn)
        ctes, params = filtered_ctes([1, 2], {}, dataflow_columns=('stage', 'operation_type', 'destination_name', 'destination_format', 'destination_connection_type', 'destination_catalog', 'destination_database', 'destination_schema', 'destination_full_table', 'destination_table', 'destination_path', '_source_id'), job_columns=('engine_name',))
        grouped = conn.execute(f'{ctes} SELECT stage, engine_name, COUNT(*) FROM filtered_dataflows GROUP BY stage, engine_name ORDER BY stage', params).fetchall()
        assert grouped == [(UNKNOWN_KEY, UNKNOWN_KEY, 2), ('unknown', 'unknown', 2)]
        raw_keys = conn.execute(f'SELECT DISTINCT {declared_destination_key_sql()} FROM {schema.DATAFLOW_TABLE}').fetchall()
        served_keys = conn.execute(f'{ctes} SELECT DISTINCT {declared_destination_key_sql()} FROM filtered_dataflows', params).fetchall()
        assert set(raw_keys) == set(served_keys)
        for key, expected in [(UNKNOWN_KEY, 2), ('unknown', 2)]:
            model = overview_read_model([], {'range': 'all', 'engine': key}, grain='day', timezone_name='UTC', analytics_context=(conn, [1, 2], 'test'))
            assert model['summary']['dataflow_records'] == expected
            assert model['summary']['active_engines'] == (0 if key == UNKNOWN_KEY else 1)
            assert model['summary']['dataflow_active_engines'] == (0 if key == UNKNOWN_KEY else 1)
        model = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=(conn, [1, 2], 'test'))
        operations = {r['operation_type']: r['count'] for r in model['dataflow_operation_health']}
        assert operations == {UNKNOWN_KEY: 2, 'etl': 2}


def test_reserved_and_multiselect_values_round_trip_without_missing_collision():
    with duckdb.connect() as conn:
        conn.execute('CREATE TABLE dimensions(value VARCHAR)')
        values = [None, 'all', UNKNOWN_KEY, VALUE_PREFIX + 'ff', 'a|b', 'unknown']
        conn.executemany('INSERT INTO dimensions VALUES (?)', [(value,) for value in values])
        selected = [dimension_key('a|b'), UNKNOWN_KEY]
        predicate, params = dimension_predicate_sql('value', selected)
        assert set(row[0] for row in conn.execute('SELECT value FROM dimensions WHERE ' + predicate, params).fetchall()) == {None, 'a|b'}
        assert matches_filter_value('a|b', '|'.join(selected))
        assert not matches_filter_value(UNKNOWN_KEY, '|'.join(selected))



def test_runtime_context_query_preserves_composite_identity():
    from datacoolie_studio.domains.monitoring.read_models.runtime_phase import runtime_phase_summary
    with duckdb.connect() as conn:
        populate(conn)
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET operation_type=CASE WHEN _source_id=1 THEN 'a · b' ELSE 'a' END, stage=CASE WHEN _source_id=1 THEN 'c' ELSE 'b · c' END")
        rebuild(conn)
        from datacoolie_studio.domains.monitoring.read_models.overview import _DATAFLOW_COLUMNS, _JOB_COLUMNS
        ctes, params = filtered_ctes([1, 2], {}, dataflow_columns=_DATAFLOW_COLUMNS, job_columns=_JOB_COLUMNS)
        groups = runtime_phase_summary(conn, ctes, params, group_column='context', limit=20)
        identities = {tuple(json.loads(row['context'][len(CONTEXT_PREFIX):])) for row in groups if not row['is_total']}
        assert identities == {('a · b', 'c'), ('a', 'b · c')}


def test_catalog_all_missing_and_connection_unknown_name_collision():
    with duckdb.connect() as conn:
        populate(conn)
        conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage='\u00a0', source_name=CASE WHEN dataflow_run_id='old' THEN '\tunknown' ELSE 'unknown' END, destination_name='unknown'")
        rebuild(conn)
        all_time = monitoring_filter_options_read_model([], (conn, [1, 2], 'test'), filters={'range': 'all'})
        broad = monitoring_filter_options_read_model([], (conn, [1, 2], 'test'), filters={'range': 'custom', 'startTime': '2025-01-01'})
        assert counts(all_time, 'stage') == {UNKNOWN_KEY: 8}
        assert counts(all_time, 'connection') == {'unknown': 6}
        assert broad['options'] == all_time['options']


def test_job_operation_bundle_missing_is_separate_from_literal_unknown():
    from datacoolie_studio.domains.monitoring.read_models.jobs import jobs_read_model
    with duckdb.connect() as conn:
        populate(conn)
        conn.execute(f"UPDATE {schema.JOB_TABLE} SET operation_types=CASE WHEN _source_id=1 THEN NULL ELSE '[\"unknown\"]' END")
        rebuild(conn)
        model = jobs_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=(conn, [1, 2], 'test'))
        groups = {row['operation_type']: row['count'] for row in model['job_duration_by_operation']}
        assert groups == {UNKNOWN_KEY: 1, 'unknown': 1}


def test_investigation_shape_label_uses_display_members_without_changing_identity():
    from datacoolie_studio.domains.monitoring.metrics.row_normalization import job_key, job_shape_label
    from datacoolie_studio.domains.monitoring.metrics.investigation import _job_shape_label
    missing = {'operation_types': None, 'stages': []}
    ordinary = {'operation_types': ['unknown'], 'stages': ['unknown']}
    assert job_shape_label(missing) == _job_shape_label(missing) == 'Unknown | Unknown'
    assert job_shape_label(ordinary) == 'unknown (value) | unknown (value)'
    assert job_key(missing) != job_key(ordinary)
