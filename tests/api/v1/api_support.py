from __future__ import annotations

from tests.support.paths import FIXTURE_ROOT


SAMPLE_LOGS = FIXTURE_ROOT / "usecase-sim" / "logs" / "v6"
SAMPLE_METADATA = (
    FIXTURE_ROOT / "usecase-sim" / "metadata" / "file" / "local_use_cases.json"
)


def _normalize_duckdb_type(value: str) -> str:
    return value.upper().replace("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE")


def _source_status(client, environment_id: int, source_id: int) -> dict:
    workspace = client.get(
        f"/api/v1/environments/{environment_id}/sources/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    return next(
        status
        for status in workspace.json()["statuses"]
        if status["source_id"] == source_id
    )
