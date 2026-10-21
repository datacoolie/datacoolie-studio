from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest


def source_admissions(session: Session, source_ids: list[int]) -> dict[int, dict]:
    """Read bounded revision-bound log admission evidence for current sources."""
    results = {source_id: {"admitted": 0, "excluded": 0, "unsupported_only": False, "files": []} for source_id in source_ids}
    if not source_ids:
        return results
    statement = select(LogFileManifest.source_id, LogFileManifest.file_uri, LogFileManifest.admission_summary_json).where(
        LogFileManifest.source_id.in_(source_ids)
    ).order_by(LogFileManifest.source_id, LogFileManifest.file_uri)
    for source_id, uri, payload in session.execute(statement):
        if not payload:
            continue
        summary = json.loads(payload)
        result = results[source_id]
        result["admitted"] += int(summary.get("admitted", 0))
        result["excluded"] += int(summary.get("excluded", 0))
        if summary.get("excluded") and len(result["files"]) < 32:
            result["files"].append({"uri": uri, **summary})
    for result in results.values():
        result["unsupported_only"] = bool(result["excluded"] and not result["admitted"])
    return results


def monitoring_admission(session: Session, paths: list[EnvironmentSource]) -> dict:
    summaries = source_admissions(session, [path.id for path in paths if path.enabled])
    admitted = sum(item["admitted"] for item in summaries.values())
    excluded = sum(item["excluded"] for item in summaries.values())
    files = [{"source_id": source_id, **item} for source_id, summary in summaries.items() for item in summary["files"]]
    return {"admitted": admitted, "excluded": excluded, "unsupported_only": bool(excluded and not admitted), "files": files[:32]}


def list_sources(
    session: Session,
    environment_id: int,
    source_kind: str | None = None,
) -> list[EnvironmentSource]:
    """Read environment-owned sources without invoking workspace orchestration."""

    statement = (
        select(EnvironmentSource)
        .options(selectinload(EnvironmentSource.registration))
        .where(EnvironmentSource.environment_id == environment_id)
    )
    if source_kind is not None:
        statement = statement.where(EnvironmentSource.source_kind == source_kind)
    return list(session.scalars(statement.order_by(EnvironmentSource.id)))


def get_source(
    session: Session,
    environment_id: int,
    source_id: int,
    source_kind: str,
) -> EnvironmentSource | None:
    """Return a source only when it belongs to the requested environment/kind."""

    return session.scalar(
        select(EnvironmentSource)
        .options(selectinload(EnvironmentSource.registration))
        .where(
            EnvironmentSource.id == source_id,
            EnvironmentSource.environment_id == environment_id,
            EnvironmentSource.source_kind == source_kind,
        )
    )
