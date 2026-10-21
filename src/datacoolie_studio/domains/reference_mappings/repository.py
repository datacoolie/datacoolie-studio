from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Project, ProjectReferenceMapping
from datacoolie_studio.domains.assets.reference_identity import normalize_reference_signature


def list_project_reference_mappings(session: Session, project_id: int) -> list[dict[str, Any]]:
    """Read mappings in the order used by lineage and asset projections."""
    require_project(session, project_id)
    statement = (
        select(ProjectReferenceMapping)
        .where(ProjectReferenceMapping.project_id == project_id)
        .order_by(ProjectReferenceMapping.updated_at.desc(), ProjectReferenceMapping.id.desc())
    )
    return [project_reference_mapping_to_dict(item) for item in session.scalars(statement)]


def project_reference_mapping_to_dict(mapping: ProjectReferenceMapping) -> dict[str, Any]:
    signature = normalize_reference_signature(
        reference_type=mapping.reference_type,
        value=mapping.reference_normalized_value,
    ).to_dict()
    return {
        "id": mapping.id,
        "project_id": mapping.project_id,
        "reference_type": signature["reference_type"],
        "reference_normalized_value": mapping.reference_normalized_value,
        "reference_signature": signature,
        "target_identifier_kind": mapping.target_identifier_kind,
        "target_normalized_value": mapping.target_normalized_value,
        "target_display_value": mapping.target_display_value,
        "note": mapping.note,
        "created_at": mapping.created_at,
        "updated_at": mapping.updated_at,
    }


def require_project(session: Session, project_id: int) -> None:
    if session.get(Project, project_id) is None:
        raise LookupError("Project not found")
