"""Revised v7 keeps native columns and preserves incompatible local evidence."""
from copy import deepcopy
import hashlib
from pathlib import Path

import duckdb

from datacoolie_studio.domains.analytics import schema, schema_compiler, schema_migration
from datacoolie_studio.domains.analytics.access import swap_candidate
from datacoolie_studio.domains.analytics.publication import validate_analytics_candidate
from datacoolie_studio.domains.analytics_upgrade import assessment
from tests.domains.analytics.test_framework_v7_observed_metrics import _entry, _publish


def test_v6_native18_remains_current_without_new_storage_fields(tmp_path):
    legacy = _entry("historical-zero", count=0)
    legacy["log_schema_version"] = 6
    live = _publish(tmp_path, [legacy])
    assert schema_compiler.mapping_document() == schema_compiler.mapping_document(schema_compiler.SCHEMA_DOCUMENT)
    assert assessment.assess_cache(live, [7]).action == "current"
    with duckdb.connect(str(live), read_only=True) as conn:
        assert conn.execute(f"SELECT log_schema_version, metrics.rows_written FROM {schema.DATAFLOW_TABLE}").fetchall() == [(6, 0)]
        assert "metric_eligibility" not in {row[0] for row in conn.execute(f"DESCRIBE {schema.DATAFLOW_TABLE}").fetchall()}


def test_obsolete_local_cache_requires_fresh_compatible_candidate_and_retains_rollback(tmp_path):
    legacy = _entry("historical-zero", count=0)
    legacy["log_schema_version"] = 6
    live = _publish(tmp_path, [legacy])
    with duckdb.connect(str(live)) as conn:
        conn.execute(f"ALTER TABLE {schema.DATAFLOW_TABLE} ADD COLUMN metric_eligibility STRUCT(rows_written BOOLEAN)")
        conn.execute("CHECKPOINT")
    original = live.read_bytes()
    original_source = (tmp_path / "runs.jsonl").read_bytes()
    assessed = assessment.assess_cache(live, [7])
    assert assessed.action == "unsupported"
    assert assessed.reason_code == "unknown_cache_columns"
    assert live.read_bytes() == original

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    candidate = _publish(fresh, [legacy, _entry("revised-v7", count=100), _entry("unmeasured")])
    validate_analytics_candidate(candidate, [7])
    assert live.read_bytes() == original
    rollback = swap_candidate(candidate, live)
    assert rollback is not None
    retained = Path(rollback["path"])
    assert retained.read_bytes() == original
    assert rollback["sha256"] == hashlib.sha256(original).hexdigest()
    assert (tmp_path / "runs.jsonl").read_bytes() == original_source
    assert assessment.assess_cache(live, [7]).action == "current"
    with duckdb.connect(str(live), read_only=True) as conn:
        assert conn.execute(f"SELECT log_schema_version, metrics.rows_written FROM {schema.DATAFLOW_TABLE} ORDER BY dataflow_run_id").fetchall() == [(6, 0), (7, 100), (7, None)]
    with duckdb.connect(str(retained), read_only=True) as conn:
        assert conn.execute(f"SELECT metrics.rows_written FROM {schema.DATAFLOW_TABLE}").fetchall() == [(0,)]


def test_removing_experimental_storage_attribute_is_not_an_additive_migration():
    current = schema_compiler.mapping_document()
    experimental = deepcopy(current)
    experimental["columns"]["dataflow_run_log"]["metric_eligibility"] = "STRUCT(rows_written BOOLEAN)"
    result = schema_migration.diff_mapping_documents(experimental, current)
    assert result.kind == "unsupported"
    assert "dataflow_run_log.metric_eligibility" in result.removed_columns
