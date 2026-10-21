from __future__ import annotations

import copy
import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
from datacoolie_studio.domains.analytics.log_contract import admit_log_record


FIXTURES = Path(__file__).parents[2] / "fixtures/datacoolie/logging/v6"


def sample():
    return json.loads((FIXTURES / "dataflow_snapshot.json").read_text().splitlines()[0])


def test_pinned_streams_and_packaged_schema():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    packaged = files("datacoolie_studio.domains.analytics").joinpath("logging_v6.schema.json").read_bytes()
    assert packaged == (FIXTURES / "schema.json").read_bytes()
    for name, metadata in manifest["streams"].items():
        payload = (FIXTURES / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]
        rows = [json.loads(line) for line in payload.splitlines()]
        assert len(rows) == metadata["rows"]
        for index, row in enumerate(rows, 1):
            original = copy.deepcopy(row)
            assert admit_log_record(row, expected_type=row["_type"], uri=name, line=index)
            assert row == original


@pytest.mark.parametrize("version", [None, 3, 4, 5, 8, "6", True])
def test_unsupported_versions_are_excluded(version):
    row = sample()
    row["log_schema_version"] = version
    assert not admit_log_record(row, expected_type="dataflow_run_log", uri="old.json", line=2)


@pytest.mark.parametrize("count", [0, 9007199254740993, 2**80, 10**50])
@pytest.mark.parametrize("session", [None, "", "unknown", " none "])
def test_exact_large_counts_and_lexical_sessions_are_schema_valid(count, session):
    row = sample()
    row["log_session_id"] = session
    row["metrics"]["rows_read"] = count
    assert admit_log_record(row, expected_type="dataflow_run_log", uri="valid.json", line=1)


def test_current_invalid_structure_and_datetime_have_location():
    row = sample()
    row["attempts"][0]["watermark"]["candidate"]["values"]["event_ts"]["__datetime__"] = "yesterday"
    with pytest.raises(AnalyticsSchemaIncompatibleError, match="bad.json:3: invalid v6"):
        admit_log_record(row, expected_type="dataflow_run_log", uri="bad.json", line=3)
    row = sample()
    del row["metrics"]
    with pytest.raises(AnalyticsSchemaIncompatibleError, match="required property"):
        admit_log_record(row, expected_type="dataflow_run_log", uri="bad.json", line=3)
