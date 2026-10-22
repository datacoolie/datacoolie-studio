from __future__ import annotations

import pytest

from contextlib import nullcontext
import json

import duckdb

from datacoolie_studio.domains.analytics import schema, store
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
from datacoolie_studio.domains.monitoring.evidence_hydration import cache_payload
from datacoolie_studio.domains.monitoring import log_repository
from datacoolie_studio.domains.monitoring.log_repository import query_cached_dataflow_logs, query_cached_job_logs
from datacoolie_studio.domains.monitoring.metrics.investigation import enrich_dataflow_run_for_investigation
from datacoolie_studio.domains.monitoring.query_service import dataflow_logs
from tests.support.v6_records import native_record


@pytest.mark.usefixtures("studio_paths")
def test_bounded_direct_detail_preserves_native_timing_watermark_and_cache_redaction(tmp_path):
    source = tmp_path / 'flows.jsonl'
    database = tmp_path / 'analytics.duckdb'
    rows = [native_record({'dataflow_run_id': 'empty', 'attempts': [], 'read_request': None}),
            native_record({'dataflow_run_id': 'recorded'})]
    source.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    result = store.publish_rows(7, [LogFileDescriptor(str(source), 'dataflow_json', '{}', str(source))],
                                [], [], [], database_path=database)
    assert result['published'], result
    with duckdb.connect(str(database), read_only=True) as conn:
        records, total, errors, generation = query_cached_dataflow_logs(
            None, [], {'investigateKind': 'dataflow_run_id', 'investigateValue': 'recorded'},
            limit=1, analytics_context=(conn, [7], 'generation-1'),
        )
        cursor = conn.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE} WHERE dataflow_run_id = 'recorded'")
        source_row = dict(zip([column[0] for column in cursor.description], cursor.fetchone()))
    assert total == 1 and not errors and len(records) == 1
    assert generation == 'generation-1'
    assert records[0]['_record_line'] == 2
    actual = enrich_dataflow_run_for_investigation(records[0])
    expected = enrich_dataflow_run_for_investigation(source_row)
    for field in ('attempt_timing', 'freshness_evidence', 'persistence_state', 'save_status',
                  'attempt_failure_count', 'recovered_attempt_failures'):
        assert actual[field] == expected[field]
    assert actual['attempt_timing']['reason'] != 'no_attempt'
    reconstructed = json.loads(records[0]['raw_record_json'])
    for field in ('dataflow_run_id', 'dataflow_id', 'job_id', 'log_session_id', 'status', 'stage', 'operation_type'):
        assert reconstructed[field] == rows[1][field]
    assert [item['attempt_number'] for item in reconstructed['attempts']] == [
        item['attempt_number'] for item in rows[1]['attempts']
    ]
    assert reconstructed['metrics']['rows_read'] == rows[1]['metrics']['rows_read']
    cached = cache_payload({'records': records}, generation='generation-1', input_fingerprint='source-1')
    assert not set(cached['records'][0]) & {'attempts', 'read_request', 'metrics', 'diagnostic_issues'}
    assert cached['records'][0]['raw_record_json'] is None


@pytest.mark.usefixtures("studio_paths")
def test_dataflow_service_forwards_reader_generation(monkeypatch, tmp_path):
    database = tmp_path / 'analytics.duckdb'
    row = native_record({'dataflow_run_id': 'recorded'})
    source = tmp_path / 'flows.jsonl'
    source.write_text(json.dumps(row), encoding='utf-8')
    result = store.publish_rows(
        7,
        [LogFileDescriptor(str(source), 'dataflow_json', '{}', str(source))],
        [],
        [],
        [],
        database_path=database,
    )
    assert result['published'], result

    with duckdb.connect(str(database), read_only=True) as conn:
        monkeypatch.setattr(
            log_repository,
            'reader',
            lambda _paths: nullcontext((conn, [7], 'generation-1')),
        )
        response = dataflow_logs([], limit=1, session=object())

    assert response['records']
    assert response['summary']['analytics_generation'] == 'generation-1'


@pytest.mark.usefixtures("studio_paths")
def test_job_detail_retains_source_root_fields_without_expanding_physical_facts(tmp_path):
    database = tmp_path / 'analytics.duckdb'
    row = native_record({'_type': 'job_run_log', 'total_bytes_added': 10**30,
                         'total_bytes_removed': 0, 'retry_delay': 1.25})
    result = store.publish_rows(7, [], [('job.json', 'job_json', '{}', row)], [], [], database_path=database)
    assert result['published'], result
    with duckdb.connect(str(database), read_only=True) as conn:
        records, total, errors = query_cached_job_logs(None, [], {}, limit=1,
                                                     analytics_context=(conn, [7], 'generation-1'))
        columns = {column[0] for column in conn.execute('DESCRIBE monitoring_job_facts').fetchall()}
    assert total == 1 and not errors
    assert int(records[0]['total_bytes_added']) == 10**30
    assert int(records[0]['total_bytes_removed']) == 0
    assert 'total_bytes_added' not in columns and 'retry_delay' not in columns
    reconstructed = json.loads(records[0]['raw_record_json'])
    assert reconstructed['job_id'] == row['job_id']
    assert reconstructed['total_bytes_added'] == 10**30
    assert reconstructed['retry_delay'] == row['retry_delay']
