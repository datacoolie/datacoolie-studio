from __future__ import annotations

from copy import deepcopy

import pytest

from datacoolie_studio.domains.monitoring.metrics.freshness_projections import (
    _freshness_dataflow_registry,
    _latest_skipped_streaks_by_dataflow,
)
from tests.support.v6_fixture import coherent_v6_ledger


def _monitoring_row(
    source: dict,
    *,
    run_id: str,
    end_time: str,
    status: str | None = None,
    message: object = ...,
    message_present: bool | None = None,
    phase_message: str | None = None,
) -> dict:
    """Project the v6 root and final phase into the monitoring row contract."""

    row = deepcopy(source)
    if status is not None:
        row["status"] = status
    if message is not ...:
        row["message"] = message
    if message_present is None:
        message_present = "message" in row
    row["dataflow_run_id"] = run_id
    row["end_time"] = end_time
    row["event_time"] = end_time
    row["operation_type"] = "etl"
    row["message_present"] = message_present
    # This is the serving projection of a failed v6 final phase.  It is not a
    # historical producer field and is used only to test root-message precedence.
    row["error_message"] = phase_message
    return row


def test_v6_explicit_null_root_message_does_not_resurrect_phase_text() -> None:
    success, failed = coherent_v6_ledger()["dataflow"][0], coherent_v6_ledger()["dataflow"][-1]
    old = _monitoring_row(
        success,
        run_id="old-run",
        end_time="2026-11-01T12:00:01+00:00",
        message="old status text",
        phase_message="old phase text",
    )
    latest = _monitoring_row(
        failed,
        run_id="latest-run",
        end_time="2026-11-01T12:00:02+00:00",
        status="failed",
        message=None,
        message_present=True,
        phase_message="current phase text",
    )
    registry = _freshness_dataflow_registry([], [], {}, [old, latest])
    row = registry[0]
    assert row["latest_run_status"] == "failed"
    assert row["latest_message"] is None
    assert row["latest_error_message"] is None


@pytest.mark.parametrize("message", [None, ""])
def test_v6_root_message_presence_is_stable_for_empty_values(message: object) -> None:
    source = coherent_v6_ledger()["dataflow"][0]
    latest = _monitoring_row(
        source,
        run_id="latest-run",
        end_time="2026-11-01T12:00:02+00:00",
        status="succeeded",
        message=message,
        message_present=True,
        phase_message="must not replace root message",
    )
    row = _freshness_dataflow_registry([], [], {}, [latest])[0]
    assert row["latest_message"] == message


def test_v6_omitted_root_message_uses_current_failed_phase_evidence() -> None:
    source = coherent_v6_ledger()["dataflow"][-1]
    latest = _monitoring_row(
        source,
        run_id="failed-with-phase",
        end_time="2026-11-01T12:00:03+00:00",
        status="failed",
        message_present=False,
        phase_message="destination phase failed",
    )
    latest.pop("message", None)
    row = _freshness_dataflow_registry([], [], {}, [latest])[0]
    assert row["latest_message"] == "destination phase failed"
    assert row["latest_error_message"] == "destination phase failed"


def test_v6_latest_status_and_message_are_order_independent() -> None:
    source = coherent_v6_ledger()["dataflow"][0]
    rows = [
        _monitoring_row(
            source,
            run_id="same-time-a-skipped",
            end_time="2026-11-01T12:00:00+00:00",
            status="skipped",
            message="skipped by source",
        ),
        _monitoring_row(
            source,
            run_id="same-time-z-failed",
            end_time="2026-11-01T12:00:00+00:00",
            status="failed",
            message="failed at destination",
            phase_message="failed at destination",
        ),
    ]
    outputs = []
    for ordering in (rows, list(reversed(rows))):
        registry = _freshness_dataflow_registry(
            [], [], _latest_skipped_streaks_by_dataflow(ordering), ordering
        )
        outputs.append(
            (
                registry[0]["latest_run_status"],
                registry[0]["latest_message"],
                registry[0]["last_statuses"],
            )
        )
    assert outputs[0] == outputs[1]
    assert outputs[0][:2] == ("failed", "failed at destination")
