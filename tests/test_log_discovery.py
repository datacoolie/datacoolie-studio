from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from datacoolie_studio.domains.logs.discovery import (
    LogStreamCheckpoint,
    LogSyncMode,
    LogSyncSpec,
    LookbackRange,
    PartitionFormatError,
    discover_incremental_candidates,
    discover_lookback_candidates,
)
from datacoolie_studio.domains.logs.partition import PartitionGranularity, parse_partition_path
from datacoolie_studio.domains.logs.partition import PartitionLayout
from datacoolie_studio.domains.logs.planner import (
    PlannerState,
    StreamDefinition,
    plan_stream_sync,
)
from datacoolie_studio.domains.storage.adapters import (
    StorageRevision,
    LocalStorageAdapter,
    StorageObject,
)
from datacoolie_studio.domains.storage.uri import parse_storage_uri
from datacoolie_studio.domains.storage.inventory import StorageInventory


def test_stream_plan_reingests_changed_provider_revision_at_same_timestamp():
    from datacoolie_studio.domains.logs.ingestion import _plan_stream_sync

    uri = "s3://bucket/logs/job_run_log/run.jsonl"
    modified = datetime(2026, 7, 23, tzinfo=timezone.utc)
    current = StorageRevision(
        canonical_uri=uri,
        size=12,
        last_modified=modified,
        provider_revision="etag-new",
    )
    previous = StorageRevision(
        canonical_uri=uri,
        size=12,
        last_modified=modified,
        provider_revision="etag-old",
    )

    class FakeAdapter:
        def inventory(self, request):
            objects = [
                StorageObject(
                    canonical_uri=uri,
                    name="run.jsonl",
                    object_type="file",
                    size=current.size,
                    last_modified=current.last_modified,
                    provider_revision=current.provider_revision,
                )
            ] if "file" in request.object_types else []
            return StorageInventory(
                objects=tuple(objects),
                completeness="complete",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=len(objects),
                matching_objects=len(objects),
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=1,
            )

    checkpoint = LogStreamCheckpoint(
        partition_value=modified.date(),
        boundary_last_modified=modified,
        partition_format="%Y-%m-%d",
    )
    incremental, candidates, _ = _plan_stream_sync(
        FakeAdapter(),
        "s3://bucket/logs/job_run_log",
        suffix=".jsonl",
        checkpoint=checkpoint,
        spec=LogSyncSpec(),
        manifest={uri: previous},
    )

    assert [item.canonical_uri for item in incremental] == [uri]
    assert [item.canonical_uri for item in candidates] == [uri]


@pytest.mark.parametrize(
    ("raw_path", "expected_date", "granularity", "partition_format"),
    [
        ("2026", date(2026, 1, 1), PartitionGranularity.YEAR, "%Y"),
        ("202607", date(2026, 7, 1), PartitionGranularity.MONTH, "%Y%m"),
        ("2026-07", date(2026, 7, 1), PartitionGranularity.MONTH, "%Y-%m"),
        ("2026_07", date(2026, 7, 1), PartitionGranularity.MONTH, "%Y_%m"),
        ("20260722", date(2026, 7, 22), PartitionGranularity.DAY, "%Y%m%d"),
        ("2026-07-22", date(2026, 7, 22), PartitionGranularity.DAY, "%Y-%m-%d"),
        ("2026_07_22", date(2026, 7, 22), PartitionGranularity.DAY, "%Y_%m_%d"),
        ("2026/07", date(2026, 7, 1), PartitionGranularity.MONTH, "%Y/%m"),
        ("2026/07/22", date(2026, 7, 22), PartitionGranularity.DAY, "%Y/%m/%d"),
        (
            "__run_date=2026-07-22",
            date(2026, 7, 22),
            PartitionGranularity.DAY,
            "__run_date=%Y-%m-%d",
        ),
        (
            "year=2026/month=07/day=22",
            date(2026, 7, 22),
            PartitionGranularity.DAY,
            "year=%Y/month=%m/day=%d",
        ),
    ],
)
def test_parse_partition_path_supports_explicit_formats(
    raw_path: str,
    expected_date: date,
    granularity: PartitionGranularity,
    partition_format: str,
) -> None:
    parsed = parse_partition_path(raw_path)

    assert parsed is not None
    assert parsed.partition_value == expected_date
    assert parsed.partition_granularity is granularity
    assert parsed.partition_format == partition_format
    layout = PartitionLayout(partition_format, granularity)
    assert layout.render(expected_date) == raw_path


@pytest.mark.parametrize("raw_path", ["", "archive-20260722", "2026-13", "2026-02-30", "run_20260722"])
def test_parse_partition_path_rejects_invalid_or_embedded_dates(raw_path: str) -> None:
    assert parse_partition_path(raw_path) is None


def test_parse_partition_path_honors_persisted_format() -> None:
    assert parse_partition_path("20260722", expected_format="%Y-%m-%d") is None
    assert parse_partition_path("2026-07-22", expected_format="%Y-%m-%d") is not None


def test_incremental_discovery_prunes_old_partition_before_file_listing(tmp_path: Path) -> None:
    adapter = _RecordingLocalStorageAdapter()
    old = tmp_path / "__run_date=2026-07-20"
    boundary = tmp_path / "__run_date=2026-07-21"
    current = tmp_path / "__run_date=2026-07-22"
    for folder in (old, boundary, current):
        folder.mkdir()
    _write_with_mtime(old / "old.jsonl", 100)
    _write_with_mtime(boundary / "at-boundary.jsonl", 200)
    _write_with_mtime(boundary / "newer.jsonl", 201)
    _write_with_mtime(current / "new.jsonl", 202)
    checkpoint = LogStreamCheckpoint(
        partition_value=date(2026, 7, 21),
        boundary_last_modified=datetime.fromtimestamp(200, tz=timezone.utc),
        partition_format="__run_date=%Y-%m-%d",
    )

    candidates = discover_incremental_candidates(
        adapter,
        str(tmp_path),
        suffix=".jsonl",
        checkpoint=checkpoint,
    )

    assert [Path(item.canonical_uri).name for item in candidates] == ["newer.jsonl", "new.jsonl"]
    assert old.resolve().as_posix() not in adapter.listed_file_partitions
    assert boundary.resolve().as_posix() in adapter.listed_file_partitions
    assert current.resolve().as_posix() in adapter.listed_file_partitions


def test_initial_incremental_sync_selects_all_partition_files(tmp_path: Path) -> None:
    for raw_partition in ("20260720", "20260721"):
        folder = tmp_path / raw_partition
        folder.mkdir()
        _write_with_mtime(folder / f"{raw_partition}.parquet", 100)

    candidates = discover_incremental_candidates(LocalStorageAdapter(), str(tmp_path), suffix="parquet")

    assert [item.partition.raw_partition_path for item in candidates] == ["20260720", "20260721"]


def test_nested_partition_discovery_uses_the_deepest_partition(tmp_path: Path) -> None:
    folder = tmp_path / "2026" / "07" / "22"
    folder.mkdir(parents=True)
    _write_with_mtime(folder / "run.parquet", 100)

    candidates = discover_incremental_candidates(LocalStorageAdapter(), str(tmp_path), suffix=".parquet")

    assert len(candidates) == 1
    assert candidates[0].partition.raw_partition_path == "2026/07/22"
    assert candidates[0].partition.partition_format == "%Y/%m/%d"


def test_initial_discovery_rejects_mixed_partition_formats(tmp_path: Path) -> None:
    (tmp_path / "20260721").mkdir()
    (tmp_path / "2026-07-22").mkdir()

    with pytest.raises(PartitionFormatError, match="Mixed partition formats"):
        discover_incremental_candidates(LocalStorageAdapter(), str(tmp_path), suffix=".jsonl")


def test_lookback_selects_new_or_changed_files_without_advancing_policy(tmp_path: Path) -> None:
    folder = tmp_path / "__run_date=2026-07-20"
    folder.mkdir()
    unchanged_path = folder / "unchanged.jsonl"
    changed_path = folder / "changed.jsonl"
    new_path = folder / "new.jsonl"
    for path, modified in ((unchanged_path, 100), (changed_path, 101), (new_path, 102)):
        _write_with_mtime(path, modified)
    adapter = LocalStorageAdapter()
    unchanged_revision = adapter.stat(str(unchanged_path))
    old_changed_revision = StorageRevision(
        canonical_uri=adapter.canonical_uri(str(changed_path)),
        size=0,
        last_modified=datetime.fromtimestamp(99, tz=timezone.utc),
        provider_revision="99000000000",
    )

    candidates = discover_lookback_candidates(
        adapter,
        str(tmp_path),
        suffix="jsonl",
        lookback=LookbackRange(date(2026, 7, 20), date(2026, 7, 20)),
        manifest_revisions={
            unchanged_revision.canonical_uri: unchanged_revision,
            old_changed_revision.canonical_uri: old_changed_revision,
        },
        expected_format="__run_date=%Y-%m-%d",
    )

    assert [Path(item.canonical_uri).name for item in candidates] == ["changed.jsonl", "new.jsonl"]


def test_sync_spec_requires_mode_appropriate_lookback() -> None:
    lookback = LookbackRange(date(2026, 7, 1), date(2026, 7, 20))

    assert LogSyncSpec() == LogSyncSpec(mode=LogSyncMode.INCREMENTAL)
    assert LogSyncSpec(LogSyncMode.INCREMENTAL_WITH_LOOKBACK, lookback).lookback == lookback
    with pytest.raises(ValueError):
        LogSyncSpec(LogSyncMode.INCREMENTAL, lookback)
    with pytest.raises(ValueError):
        LogSyncSpec(LogSyncMode.INCREMENTAL_WITH_LOOKBACK)
    with pytest.raises(ValueError):
        LookbackRange(date(2026, 7, 20), date(2026, 7, 1))


def test_learned_layout_renders_only_incremental_and_lookback_partitions(
    tmp_path: Path,
) -> None:
    for raw_partition in (
        "__run_date=2026-07-20",
        "__run_date=2026-07-22",
        "__run_date=2026-07-24",
    ):
        folder = tmp_path / raw_partition
        folder.mkdir()
        _write_with_mtime(folder / f"{raw_partition}.jsonl", 100)
    adapter = _RecordingLocalStorageAdapter()
    state = PlannerState(
        stream_kind="job_jsonl",
        root_uri=str(tmp_path),
        layout_status="learned",
        partition_format="__run_date=%Y-%m-%d",
        partition_granularity=PartitionGranularity.DAY,
        checkpoint_partition_value=date(2026, 7, 22),
        boundary_last_modified=datetime.fromtimestamp(100, tz=timezone.utc),
        last_scanned_partition_value=date(2026, 7, 22),
    )

    plan = plan_stream_sync(
        adapter,
        StreamDefinition(
            stream_kind="job_jsonl",
            root_uri=str(tmp_path),
            suffix=".jsonl",
        ),
        state=state,
        manifest={},
        spec=LogSyncSpec(
            LogSyncMode.INCREMENTAL_WITH_LOOKBACK,
            LookbackRange(date(2026, 7, 20), date(2026, 7, 20)),
        ),
        today=date(2026, 7, 24),
    )

    assert adapter.listed_partition_children == []
    assert [
        Path(value).name for value in adapter.listed_file_partitions
    ] == [
        "__run_date=2026-07-22",
        "__run_date=2026-07-23",
        "__run_date=2026-07-24",
        "__run_date=2026-07-20",
    ]
    assert plan.incremental_partition_values == (
        date(2026, 7, 22),
        date(2026, 7, 23),
        date(2026, 7, 24),
    )
    assert plan.lookback_partition_values == (date(2026, 7, 20),)
    assert plan.state.last_scanned_partition_value == date(2026, 7, 24)
    assert plan.state.checkpoint_partition_value == date(2026, 7, 24)


@pytest.mark.parametrize(
    ("granularity", "start", "end", "expected"),
    [
        (
            PartitionGranularity.YEAR,
            date(2024, 7, 1),
            date(2026, 2, 1),
            (date(2024, 1, 1), date(2025, 1, 1), date(2026, 1, 1)),
        ),
        (
            PartitionGranularity.MONTH,
            date(2025, 12, 20),
            date(2026, 2, 3),
            (date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)),
        ),
        (
            PartitionGranularity.DAY,
            date(2026, 7, 22),
            date(2026, 7, 24),
            (date(2026, 7, 22), date(2026, 7, 23), date(2026, 7, 24)),
        ),
    ],
)
def test_partition_layout_values_are_inclusive_and_calendar_aware(
    granularity: PartitionGranularity,
    start: date,
    end: date,
    expected: tuple[date, ...],
) -> None:
    format_by_granularity = {
        PartitionGranularity.YEAR: "%Y",
        PartitionGranularity.MONTH: "%Y-%m",
        PartitionGranularity.DAY: "%Y-%m-%d",
    }
    layout = PartitionLayout(format_by_granularity[granularity], granularity)

    assert layout.values(start, end) == expected


def test_gcs_uri_is_recognized_for_future_adapter_selection() -> None:
    parsed = parse_storage_uri("gs://monitoring-logs/etl_logs")

    assert parsed.provider == "gcs"
    assert not parsed.is_local


class _RecordingLocalStorageAdapter(LocalStorageAdapter):
    def __init__(self) -> None:
        self.listed_file_partitions: list[str] = []
        self.listed_partition_children: list[str] = []

    def inventory(self, request):
        if request.object_types == frozenset({"directory"}):
            self.listed_partition_children.append(self.canonical_uri(request.uri))
        if request.object_types == frozenset({"file"}):
            self.listed_file_partitions.append(self.canonical_uri(request.uri))
        return super().inventory(request)


def _write_with_mtime(path: Path, modified_seconds: int) -> None:
    path.write_text("{}\n", encoding="utf-8")
    timestamp_ns = modified_seconds * 1_000_000_000
    path.touch()
    os.utime(path, ns=(timestamp_ns, timestamp_ns))
