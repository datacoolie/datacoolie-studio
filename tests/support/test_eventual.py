from __future__ import annotations

import pytest

from tests.support.eventual import wait_for


def test_wait_for_returns_first_value_matching_predicate():
    values = iter(("queued", "running", "succeeded"))

    result = wait_for(
        lambda: next(values),
        lambda value: value == "succeeded",
        interval_seconds=0.001,
    )

    assert result == "succeeded"


def test_wait_for_reports_last_value_on_timeout():
    with pytest.raises(AssertionError, match="last value: 'still-running'"):
        wait_for(
            lambda: "still-running",
            lambda _value: False,
            timeout_seconds=0.005,
            interval_seconds=0.001,
            description="job completion",
        )
