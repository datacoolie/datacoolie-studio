from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import contracts, schema, store
from datacoolie_studio.domains.analytics.serving_facts import validate_monitoring_serving_facts


FIXTURES = Path(__file__).parents[2] / "fixtures/datacoolie/logging/v6"


def sample():
    return json.loads((FIXTURES / "dataflow_snapshot.json").read_text().splitlines()[0])


def publish(database, *paths, changed=()):
    files = tuple(contracts.LogFileDescriptor(str(p), "dataflow_json", "{}", str(p)) for p in paths)
    return store.publish_rows_batched(1, [contracts.LogPublishBatch(dataflow_files=files)], [], list(changed), database_path=database)


def write(path, *rows):
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def test_mixed_versions_typed_temporal_arrays_and_exact_large_metrics(tmp_path):
    row = sample()
    row["metrics"]["rows_read"] = 10**30
    row["source_watermark_columns"] = "updated_at"
    old = {"log_schema_version": 4, "_type": "dataflow_run_log"}
    path = write(tmp_path / "mixed.json", old, row)
    database = tmp_path / "logs.duckdb"
    result = publish(database, path)
    assert result["published"], result
    assert result["file_admission_summaries"][str(path)]["excluded"] == 1
    with duckdb.connect(str(database)) as conn:
        from datacoolie_studio.domains.analytics.record_codec import reconstruct_record
        cursor = conn.execute(f"SELECT * FROM {schema.DATAFLOW_TABLE}")
        names = [column[0] for column in cursor.description]
        physical = dict(zip(names, cursor.fetchone()))
        assert reconstruct_record(physical, "dataflow_run_log") == {**row, "diagnostic_issues": None}
        assert int(physical["metrics"]["rows_read"]) == 10**30
        assert isinstance(physical["attempts"], list)
        assert physical["source_watermark_columns"] == "updated_at"
        assert "raw_record_json" not in names


@pytest.mark.parametrize('value', [
    '2024-01-02T10:05:00', '2000-02-29T23:59:59.123456',
    '0001-01-01T00:00:00', '9999-12-31T23:59:59',
    '2024-01-02T10:05:00+07:00',
])
def test_tagged_datetime_bounds_publish_and_reconstruct_exactly(tmp_path, studio_paths, value):
    from datacoolie_studio.domains.analytics.record_codec import reconstruct_record

    row = sample()
    bound = {'event_date': {'__datetime__': value}}
    attempt = row['attempts'][0]
    attempt['source']['selection']['lower'] = bound
    attempt['watermark']['loaded_watermark'] = bound
    attempt['watermark']['candidate']['values'] = bound
    row['read_request']['read_range']['start'] = bound['event_date']
    path = write(tmp_path / 'datetime.json', row)
    database = tmp_path / 'datetime.duckdb'
    result = publish(database, path)
    assert result['published'], result
    with duckdb.connect(str(database)) as conn:
        cursor = conn.execute(f'SELECT * FROM {schema.DATAFLOW_TABLE}')
        reconstructed = reconstruct_record(dict(zip([c[0] for c in cursor.description], cursor.fetchone())), 'dataflow')
        assert reconstructed == {**row, "diagnostic_issues": None}
        assert conn.execute('SELECT count(*) FROM monitoring_dataflow_facts').fetchone() == (1,)


@pytest.mark.parametrize('value', [
    '1900-02-29T10:05:00', '2024-04-31T10:05:00', '0000-01-01T00:00:00',
    '2024-01-02T24:00:00', '2024-01-02T10:05:60', '2024-01-02T10:05:00.1234567',
    '2024-01-02T10:05:00\n', '2024-01-02T10:05:00 ', 'not-a-date',
])
def test_invalid_tagged_datetime_still_rejects_without_replacing_cache(tmp_path, studio_paths, value):
    from copy import deepcopy
    from datacoolie_studio.domains.analytics.record_codec import reconstruct_record

    database = tmp_path / 'datetime.duckdb'
    row = sample()
    baseline = deepcopy(row)
    path = write(tmp_path / 'datetime.json', row)
    assert publish(database, path)['published']
    row['attempts'][0]['source']['selection']['lower'] = {'event_date': {'__datetime__': value}}
    write(path, row)
    result = publish(database, path, changed=(str(path),))
    assert not result['published']
    assert result['errors'][0]['code'] == 'schema_incompatible'
    with duckdb.connect(str(database)) as conn:
        assert conn.execute('SELECT count(*) FROM monitoring_dataflow_facts').fetchone() == (1,)
        cursor = conn.execute(f'SELECT * FROM {schema.DATAFLOW_TABLE}')
        assert reconstruct_record(dict(zip([c[0] for c in cursor.description], cursor.fetchone())), 'dataflow') == {**baseline, 'diagnostic_issues': None}


def test_source_roundtrip_across_staging_buffer_boundary_preserves_line_order(tmp_path):
    from datacoolie_studio.domains.analytics.record_codec import record_json, reconstruct_record
    from datacoolie_studio.domains.analytics.serving_validation import _exact_json

    rows = []
    for index in range(257):
        row = sample()
        row["dataflow_run_id"] = f"boundary-{index}"
        row["metrics"]["rows_read"] = 10**30 + index
        row["duration_seconds"] = Decimal("1.2345678901234567890123456789") + index
        if index % 3 == 0:
            row["attempts"] = []
            row["read_request"] = None
        elif index % 3 == 1:
            row["attempts"][-1]["transform"] = None
        row.pop("diagnostic_issues", None)
        rows.append(row)
    path = tmp_path / "buffer.jsonl"
    path.write_text("\n".join(record_json(row) for row in rows), encoding="utf-8")
    database = tmp_path / "buffer.duckdb"
    result = publish(database, path)
    assert result["published"], result
    with duckdb.connect(str(database), read_only=True) as conn:
        cursor = conn.execute("SELECT * FROM etl_dataflow_runs ORDER BY _record_line")
        names = [column[0] for column in cursor.description]
        values = [dict(zip(names, item)) for item in cursor.fetchall()]
        assert [item["_record_line"] for item in values] == list(range(1, 258))
        assert [_exact_json(reconstruct_record(item, "dataflow")) for item in values] == [
            _exact_json({**row, "duration_seconds": float(row["duration_seconds"]), "diagnostic_issues": None}) for row in rows
        ]


def test_version_admission_and_physical_line_provenance_agree(tmp_path):
    valid = sample()
    valid["log_schema_version"] = 6.0
    text_version = {**sample(), "log_schema_version": "6"}
    path = tmp_path / "versions.json"
    path.write_text("\n" + json.dumps(text_version) + "\n\n" + json.dumps(valid) + "\n", encoding="utf-8")
    database = tmp_path / "logs.duckdb"
    result = publish(database, path)
    assert result["published"], result
    summary = result["file_admission_summaries"][str(path)]
    assert (summary["admitted"], summary["excluded"]) == (1, 1)
    assert summary["locations"][0]["line"] == 2
    with duckdb.connect(str(database)) as conn:
        assert conn.execute(f"SELECT _record_line FROM {schema.DATAFLOW_TABLE}").fetchone() == (4,)


def test_invalid_current_row_does_not_mutate_previous_generation(tmp_path):
    database = tmp_path / "logs.duckdb"
    path = write(tmp_path / "logs.json", sample())
    assert publish(database, path)["published"]
    invalid = sample()
    del invalid["metrics"]
    write(path, invalid)
    result = publish(database, path, changed=(str(path),))
    assert not result["published"]
    assert result["errors"][0]["code"] == "schema_incompatible"
    with duckdb.connect(str(database)) as conn:
        assert conn.execute(f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}").fetchone()[0] == 1


def test_old_only_is_empty_admission_and_duplicate_run_is_one_fact(tmp_path):
    database = tmp_path / "logs.duckdb"
    old = write(tmp_path / "old.json", {"log_schema_version": 5})
    result = publish(database, old)
    assert result["published"]
    assert result["parsed_dataflow_records"] == 0
    a, b = (write(tmp_path / name, sample()) for name in ("snapshot.json", "part.json"))
    assert publish(database, a, b)["published"]
    with duckdb.connect(str(database)) as conn:
        assert conn.execute(f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM monitoring_dataflow_facts").fetchone()[0] == 1
    conflict = sample()
    conflict["metrics"]["rows_read"] = 999
    write(b, conflict)
    result = publish(database, b, changed=(str(b),))
    assert not result["published"]
    assert result["errors"][0]["code"] == "publish_failed"


@pytest.mark.parametrize("column,value", [("source_rows_read", "999"), ("source_status", None), ("dataflow_id", "wrong")])
def test_serving_corruption_blocks_parity_gate(tmp_path, column, value):
    database = tmp_path / "logs.duckdb"
    assert publish(database, FIXTURES / "dataflow_snapshot.json")["published"]
    with duckdb.connect(str(database)) as conn:
        from datacoolie_studio.domains.analytics.sql import sql_string
        literal = "NULL" if value is None else sql_string(value)
        conn.execute(f'UPDATE monitoring_dataflow_facts SET "{column}" = {literal}')
        with pytest.raises(RuntimeError):
            validate_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE, job_table=schema.JOB_TABLE)
