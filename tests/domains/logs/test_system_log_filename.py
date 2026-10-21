from __future__ import annotations

from datetime import datetime, timezone

import pytest

from datacoolie_studio.domains.logs.system_log_filename import (
    ParsedSystemLogFilename,
    normalize_filename_job_id,
    parse_system_log_filename,
)


def test_parser_handles_plain_file_and_cloud_uri_boundaries() -> None:
    expected = ParsedSystemLogFilename(
        job_id="a/b",
        job_id_alt=None,
        log_timestamp=datetime(2026, 10, 10, 1, 2, 3, tzinfo=timezone.utc),
    )
    names = (
        "system_20261010_010203_1_0_a%2Fb.json",
        "file:///tmp/system_20261010_010203_1_0_a%252Fb.json",
        "s3://bucket/logs/system_20261010_010203_1_0_a%2Fb.json",
    )
    assert [parse_system_log_filename(value) for value in names] == [
        expected,
        expected,
        expected,
    ]


def test_parser_keeps_full_and_stripped_part_candidates() -> None:
    parsed = parse_system_log_filename(
        "system_20261010_010203_1_0_job%2E_part_00000001.json"
    )
    assert parsed.job_id == "job._part_00000001"
    assert parsed.job_id_alt == "job."


@pytest.mark.parametrize(
    ("token", "expected"),
    (
        ("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ("job_name_with_underscores", "job_name_with_underscores"),
        ("c%C3%B4ng_vi%E1%BB%87c", "công_việc"),
        ("%2Eleading", ".leading"),
        ("trailing%2E", "trailing."),
        ("job-1_part_000000001", "job-1_part_000000001"),
    ),
)
def test_parser_preserves_supported_identity_shapes(token: str, expected: str) -> None:
    parsed = parse_system_log_filename(
        f"system_20240229_235959_2_1_{token}.json"
    )
    assert parsed.job_id == expected
    assert parsed.log_timestamp == datetime(2024, 2, 29, 23, 59, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "name",
    (
        "system_20260230_010203_1_0_job.json",
        "system_20261010_010203_0_0_job.json",
        "system_20261010_010203_1_1_job.json",
        "system_20261010_010203_1_0_bad%2.json",
        "system_20261010_010203_1_0_job%2f.json",
        "system_20261010_010203_" + "9" * 5000 + "_0_job.json",
        "opaque.json",
    ),
)
def test_parser_returns_unknown_for_invalid_or_noncanonical_names(name: str) -> None:
    assert parse_system_log_filename(name) == ParsedSystemLogFilename()


@pytest.mark.parametrize(
    ("value", "expected"),
    ((None, "default"), ("", "default"), (" \t ", "default"), (" job-1 ", "job-1"), (42, "42")),
)
def test_lookup_normalization_is_trimmed_and_defaulted(value: object, expected: str) -> None:
    assert normalize_filename_job_id(value) == expected
