"""Narrow read contracts used by neighboring read-model projections."""

from __future__ import annotations

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.lineage import service


def lineage_input_fingerprint(session: Session, environment_id: int) -> str:
    return service.lineage_input_fingerprint(session, environment_id)


def load_or_build_lineage_graph(
    session: Session,
    environment_id: int,
    *,
    input_fingerprint: str | None = None,
    include_draft: bool = False,
) -> dict:
    return service.load_or_build_lineage_graph(
        session,
        environment_id,
        input_fingerprint=input_fingerprint,
        include_draft=include_draft,
    )


def build_lineage_overview_summary(
    session: Session,
    metadata: dict,
    environment_id: int,
    code_artifacts: list[EnvironmentSource],
    reference_mappings: list[dict] | None = None,
) -> dict:
    return service.build_lineage_overview_summary(
        session,
        metadata,
        environment_id,
        code_artifacts,
        reference_mappings,
    )
