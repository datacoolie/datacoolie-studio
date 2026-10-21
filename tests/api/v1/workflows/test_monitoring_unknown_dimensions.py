from datetime import datetime, timezone
import json

import duckdb
from fastapi.testclient import TestClient

from datacoolie_studio.domains.analytics import access, cache_state, schema
from datacoolie_studio.domains.monitoring.dimensions import UNKNOWN_KEY, dimension_key
from tests.domains.monitoring.test_filter_range_counts import populate
from tests.domains.monitoring.test_unknown_dimensions import rebuild


def test_unknown_slicer_counts_and_filtered_pages_runs_preserve_native_values(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACOOLIE_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATACOOLIE_STUDIO_DATABASE_URL', f"sqlite:///{(tmp_path/'studio.db').as_posix()}")
    from datacoolie_studio.main import app
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'unknown'}).json()
        env = client.post(f"/api/v1/projects/{project['id']}/environments", json={'name': 'dev'}).json()
        logs = tmp_path/'logs'
        logs.mkdir()
        source = client.post(f"/api/v1/environments/{env['id']}/log-sources", json={'uri': str(logs)}).json()
        assert source['id'] == 1
        with duckdb.connect(str(access.analytics_database_path())) as conn:
            populate(conn)
            conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage=CASE WHEN dataflow_run_id='old' THEN 'unknown' ELSE '\t\n' END, operation_type=CASE WHEN dataflow_run_id='old' THEN 'etl' ELSE NULL END")
            rebuild(conn)
        base = f"/api/v1/environments/{env['id']}/monitoring"
        options = client.get(base+'/filter-options?range=all').json()['options']
        assert {i['value']: i['count'] for i in options['stage']} == {UNKNOWN_KEY: 3, 'unknown': 1}
        for value, expected in [(UNKNOWN_KEY, 1), ('unknown', 1)]:
            params = {'range': 'all', 'stage': value}
            page = client.get(base+'/pages/overview', params=params)
            assert page.status_code == 200, page.text
            assert int(page.json()['summary']['dataflow_records']) == expected
            runs = client.get(base+'/dataflows', params=params)
            assert runs.status_code == 200, runs.text
            assert runs.json()['summary']['total_records'] == expected
            if value == UNKNOWN_KEY:
                record = runs.json()['records'][0]
                assert record['stage'] == '\t\n'
                assert json.loads(record['raw_record_json'])['stage'] == '\t\n'
        with duckdb.connect(str(access.analytics_database_path())) as conn:
            conn.execute(f"UPDATE {schema.DATAFLOW_TABLE} SET stage='a|b' WHERE dataflow_run_id='edge'")
            rebuild(conn)
            cache_state.mark_cache_source(conn, 1, refreshed_at=datetime.now(timezone.utc))
        special = dimension_key('a|b')
        assert special in {i['value'] for i in client.get(base+'/filter-options?range=all').json()['options']['stage']}
        runs = client.get(base+'/dataflows', params={'range': 'all', 'stage': special})
        assert runs.status_code == 200 and runs.json()['summary']['total_records'] == 1
