from __future__ import annotations

import copy

import pytest

from datacoolie_studio.domains.analytics.serving_facts import MONITORING_DATAFLOW_FACTS_TABLE
from datacoolie_studio.domains.monitoring.read_models.overview import overview_read_model
from datacoolie_studio.domains.monitoring.read_models.performance import performance_read_model
from datacoolie_studio.domains.monitoring.read_models.dataflows import dataflows_read_model
from tests.domains.monitoring.test_v6_freshness_contract import connection
from tests.domains.monitoring.test_v6_timing_contract import record


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'skipped', 'pending', 'running', None])
def test_all_statuses_contribute_recorded_partial_phase_without_timestamps(tmp_path, status):
    row = record()
    row.update(status='succeeded', duration_seconds=None)
    attempt = row['attempts'][-1]
    attempt.update(start_time=None, end_time=None, duration_seconds=5, transform=None, destination=None)
    attempt['source'].update(start_time=None, end_time=None, duration_seconds=2, status='failed')
    with connection(tmp_path, [row]) as conn:
        # v6 ingestion accepts terminal statuses only. Exercise the consumer's
        # all-status behavior at its serving-fact boundary without changing that contract.
        conn.execute(f"UPDATE {MONITORING_DATAFLOW_FACTS_TABLE} SET status = ?, normalized_status = ?",
                     [status, status or 'unknown'])
        context = (conn, [1], 'test')
        overview = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)
        dataflows = dataflows_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)
        performance = performance_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)
    for groups in [overview['phase_health'], dataflows['phase_health_by_stage'], performance['phase_contribution_by_stage_operation']]:
        total = groups[0]
        assert total['phase_runs_eligible'] == total['phase_runs_measured'] == 1
        assert total['source_duration_seconds'] == 2
        assert total['source_duration_percent'] == 40
        assert total['overhead_duration_seconds'] == 3
        assert total['source_failed'] == 1
        assert total['destination_duration_seconds'] is None
        assert total['run_scope_run_count'] == 1
        assert total['run_scope_failed_count'] == int(status == 'failed')
        assert total['run_scope_duration_seconds'] is None
        assert total['run_scope_avg_duration_seconds'] is None
        assert total['run_scope_p95_duration_seconds'] is None


def test_present_null_phase_duration_preserves_other_phases_and_status_counts(tmp_path):
    row = record()
    row['attempts'][-1]['destination'].update(duration_seconds=None, status='failed')
    with connection(tmp_path, [row]) as conn:
        result = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=(conn, [1], 'test'))
    total = result['phase_health'][0]
    assert total['phase_runs_measured'] == 1
    assert total['source_duration_seconds'] == total['transform_duration_seconds'] == 2
    assert total['destination_duration_seconds'] is None
    assert total['destination_run_count'] == 0 and total['destination_failed'] == 1
    assert total['overhead_duration_seconds'] is None
    assert total['source_duration_percent'] == 50


def test_phase_average_percentile_and_shares_use_each_phases_observations(tmp_path):
    records = []
    for index, (source, destination) in enumerate([(2, 4), (6, None), (None, 8)]):
        row = record()
        row.update(dataflow_run_id=f'partial-{index}', status='skipped')
        attempt = row['attempts'][-1]
        attempt.update(duration_seconds=None, transform=None)
        attempt['source']['duration_seconds'] = source
        attempt['destination']['duration_seconds'] = destination
        records.append(row)
    with connection(tmp_path, records) as conn:
        result = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=(conn, [1], 'test'))
    total = result['phase_health'][0]
    assert total['phase_runs_eligible'] == total['phase_runs_measured'] == 3
    assert total['source_run_count'] == total['destination_run_count'] == 2
    assert total['source_avg_duration_seconds'] == 4 and total['source_p95_duration_seconds'] == 6
    assert total['destination_avg_duration_seconds'] == 6 and total['destination_p95_duration_seconds'] == 8
    assert total['source_duration_percent'] == 40 and total['destination_duration_percent'] == 60
    assert total['total_duration_seconds'] == 20
    assert total['transform_duration_seconds'] is None and total['overhead_duration_seconds'] is None


@pytest.mark.parametrize('attempt_duration,phase_duration,expected_overhead,expected_measured', [
    (None, None, None, 0), (None, 2, None, 1), (0, None, 0, 1), (5, None, 5, 1), (1, 2, 0, 1),
])
def test_overhead_null_zero_and_absent_inputs(tmp_path, attempt_duration, phase_duration, expected_overhead, expected_measured):
    row = record()
    attempt = row['attempts'][-1]
    attempt.update(duration_seconds=attempt_duration, source=None, transform=None, destination=None)
    if phase_duration is not None:
        attempt['source'] = {**record()['attempts'][-1]['source'], 'duration_seconds': phase_duration}
    with connection(tmp_path, [row]) as conn:
        result = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=(conn, [1], 'test'))
    total = result['phase_health'][0]
    assert total['phase_runs_eligible'] == 1 and total['phase_runs_measured'] == expected_measured
    assert total['overhead_duration_seconds'] == expected_overhead


def test_retries_and_slicers_control_the_population_and_strict_diagnostics_stay_separate(tmp_path):
    partial = record()
    partial.update(status='succeeded', stage='partial')
    final = partial['attempts'][-1]
    final.update(duration_seconds=3, transform=None, destination=None)
    final['source'].update(duration_seconds=2, start_time=None, end_time=None)
    earlier = copy.deepcopy(final)
    earlier['duration_seconds'] = earlier['source']['duration_seconds'] = 500
    partial['attempts'].insert(0, earlier)
    absent = record()
    absent.update(dataflow_run_id='absent', attempts=[], stage='absent', status='failed')
    with connection(tmp_path, [partial, absent]) as conn:
        conn.execute(f"UPDATE {MONITORING_DATAFLOW_FACTS_TABLE} SET status = 'running', normalized_status = 'running' WHERE stage = 'partial'")
        context = (conn, [1], 'test')
        all_rows = overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)['phase_health'][0]
        selected = overview_read_model([], {'status': 'running', 'stage': 'partial'}, grain='day', timezone_name='UTC', analytics_context=context)['phase_health'][0]
        diagnostics = performance_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)['summary']
    assert all_rows['phase_runs_eligible'] == 2 and all_rows['phase_runs_measured'] == 1
    assert all_rows['source_duration_seconds'] == 2 and all_rows['total_duration_seconds'] == 3
    assert selected['phase_runs_eligible'] == selected['phase_runs_measured'] == 1
    assert selected['source_duration_seconds'] == 2
    assert diagnostics['phase_runs_measured'] == 0 and diagnostics['bottleneck_phase'] == 'unknown'


def test_execution_scope_totals_are_not_phase_sums_and_respect_grouping_and_filters(tmp_path):
    records = []
    for index, (stage, duration, status) in enumerate([
        ('Total', 100, 'failed'), ('Total', 40, 'succeeded'),
        ('other', None, 'failed'), ('other', 0, 'skipped'),
    ]):
        row = record()
        row.update(dataflow_run_id=f'scope-{index}', stage=stage,
                   duration_seconds=duration, status=status)
        if index == 0:
            row['attempts'][-1]['source']['status'] = 'failed'
            row['attempts'][-1]['destination']['status'] = 'failed'
            row['attempts'].insert(0, copy.deepcopy(row['attempts'][-1]))
        elif index >= 2:
            row['attempts'] = []
        records.append(row)
    with connection(tmp_path, records) as conn:
        context = (conn, [1], 'test')
        pages = [
            overview_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)['phase_health'],
            dataflows_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)['phase_health_by_stage'],
            performance_read_model([], {}, grain='day', timezone_name='UTC', analytics_context=context)['phase_contribution_by_stage_operation'],
        ]
        for groups in pages:
            total = next(item for item in groups if item['is_total'])
            assert total['run_scope_run_count'] == 4
            assert total['run_scope_failed_count'] == 2
            assert total['run_scope_duration_seconds'] == 140
            assert total['run_scope_avg_duration_seconds'] == 46.667
            assert total['run_scope_p95_duration_seconds'] == 100
            assert total['total_duration_seconds'] == 20  # Final attempts only.
            assert total['source_failed'] + total['destination_failed'] == 2
        stage_groups = pages[1]
        literal_total = next(item for item in stage_groups if not item['is_total'] and item['stage'] == 'Total')
        assert literal_total['run_scope_run_count'] == 2
        assert literal_total['run_scope_failed_count'] == 1
        assert literal_total['run_scope_avg_duration_seconds'] == 70
        other = next(item for item in stage_groups if item['stage'] == 'other')
        assert other['run_scope_run_count'] == 2
        assert other['run_scope_failed_count'] == 1
        assert other['run_scope_duration_seconds'] == other['run_scope_avg_duration_seconds'] == other['run_scope_p95_duration_seconds'] == 0
        selected = dataflows_read_model([], {'stage': 'other', 'status': 'failed'}, grain='day', timezone_name='UTC', analytics_context=context)['phase_health_by_stage'][0]
        assert selected['run_scope_run_count'] == selected['run_scope_failed_count'] == 1
        assert selected['run_scope_duration_seconds'] is None
        assert selected['run_scope_avg_duration_seconds'] is None
        assert selected['run_scope_p95_duration_seconds'] is None


def test_execution_scope_counts_preserve_exact_api_transport():
    from datacoolie_studio.api.v1.contracts.monitoring_rows import MonitoringExtensionRowResponse

    payload = MonitoringExtensionRowResponse.model_validate({
        'run_scope_run_count': 9007199254740993,
        'run_scope_failed_count': 9007199254740992,
    }).model_dump(mode='json')
    assert payload['run_scope_run_count'] == '9007199254740993'
    assert payload['run_scope_failed_count'] == '9007199254740992'
