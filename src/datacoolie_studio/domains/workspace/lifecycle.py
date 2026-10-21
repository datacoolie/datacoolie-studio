from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    Environment,
    EnvironmentSource,
    Project,
    ProjectReferenceMapping,
)
from datacoolie_studio.domains.assets.reference_identity import normalize_reference_signature
from datacoolie_studio.domains.assets.reference_mappings import normalize_target_identifier
from datacoolie_studio.domains.read_models.cache import invalidate_project_read_models
from datacoolie_studio.domains.read_models.keys import ASSETS_CATALOG, LINEAGE_GRAPH, OVERVIEW
from datacoolie_studio.domains.reference_mappings import repository as reference_mapping_repository


_PROJECT_READ_MODEL_KEYS = {ASSETS_CATALOG, OVERVIEW, LINEAGE_GRAPH}


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.name)))


def list_project_reference_mappings(session: Session, project_id: int) -> list[dict]:
    return reference_mapping_repository.list_project_reference_mappings(session, project_id)


def create_project_reference_mapping(
    session: Session,
    project_id: int,
    *,
    reference_type: str,
    reference_value: str,
    target_identifier_kind: str,
    target_value: str,
    target_display_value: str | None = None,
    note: str | None = None,
) -> dict:
    _require_project(session, project_id)
    signature = normalize_reference_signature(
        reference_type=reference_type,
        value=reference_value,
    )
    target_kind, target_normalized_value = normalize_target_identifier(
        target_identifier_kind,
        target_value,
    )
    duplicate = session.scalars(
        select(ProjectReferenceMapping).where(
            ProjectReferenceMapping.project_id == project_id,
            ProjectReferenceMapping.reference_type == signature.reference_type,
            ProjectReferenceMapping.reference_normalized_value == signature.normalized_value,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("Mapping already exists for this reference signature")
    mapping = ProjectReferenceMapping(
        project_id=project_id,
        reference_type=signature.reference_type,
        reference_normalized_value=signature.normalized_value,
        reference_signature_json=signature.to_json(),
        target_identifier_kind=target_kind,
        target_normalized_value=target_normalized_value,
        target_display_value=(target_display_value or target_value).strip(),
        note=((str(note).strip() or None) if note is not None else None),
    )
    session.add(mapping)
    invalidate_project_read_models(session, project_id, model_keys=_PROJECT_READ_MODEL_KEYS)
    session.commit()
    session.refresh(mapping)
    return reference_mapping_repository.project_reference_mapping_to_dict(mapping)


def update_project_reference_mapping(
    session: Session,
    project_id: int,
    mapping_id: int,
    payload: dict[str, Any],
) -> dict | None:
    _require_project(session, project_id)
    mapping = session.get(ProjectReferenceMapping, mapping_id)
    if mapping is None or mapping.project_id != project_id:
        return None

    reference_type = payload.get("reference_type", mapping.reference_type)
    reference_value = payload.get("reference_value", mapping.reference_normalized_value)
    signature = normalize_reference_signature(
        reference_type=str(reference_type),
        value=str(reference_value),
    )

    target_kind_value = payload.get("target_identifier_kind", mapping.target_identifier_kind)
    target_value = payload.get("target_value", mapping.target_normalized_value)
    target_kind, target_normalized_value = normalize_target_identifier(
        str(target_kind_value),
        str(target_value),
    )

    target_display_value = payload.get("target_display_value", mapping.target_display_value)
    normalized_target_display_value = (target_display_value or str(target_value)).strip()
    note = payload.get("note", mapping.note)
    normalized_note = ((str(note).strip() or None) if note is not None else None)
    duplicate = session.scalars(
        select(ProjectReferenceMapping).where(
            ProjectReferenceMapping.project_id == project_id,
            ProjectReferenceMapping.reference_type == signature.reference_type,
            ProjectReferenceMapping.reference_normalized_value == signature.normalized_value,
            ProjectReferenceMapping.id != mapping.id,
        )
    ).first()
    if duplicate is not None:
        raise ValueError("Mapping already exists for this reference signature")

    mapping.reference_type = signature.reference_type
    mapping.reference_normalized_value = signature.normalized_value
    mapping.reference_signature_json = signature.to_json()
    mapping.target_identifier_kind = target_kind
    mapping.target_normalized_value = target_normalized_value
    mapping.target_display_value = normalized_target_display_value
    mapping.note = normalized_note
    invalidate_project_read_models(session, project_id, model_keys=_PROJECT_READ_MODEL_KEYS)
    session.commit()
    session.refresh(mapping)
    return reference_mapping_repository.project_reference_mapping_to_dict(mapping)


def delete_project_reference_mapping(session: Session, project_id: int, mapping_id: int) -> bool:
    _require_project(session, project_id)
    mapping = session.get(ProjectReferenceMapping, mapping_id)
    if mapping is None or mapping.project_id != project_id:
        return False
    invalidate_project_read_models(session, project_id, model_keys=_PROJECT_READ_MODEL_KEYS)
    session.delete(mapping)
    session.commit()
    return True


def list_project_summaries(session: Session) -> list[dict]:
    mapping_counts = (
        select(
            ProjectReferenceMapping.project_id.label("project_id"),
            func.count(ProjectReferenceMapping.id).label("reference_mapping_count"),
        )
        .group_by(ProjectReferenceMapping.project_id)
        .subquery()
    )
    statement = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.description.label("project_description"),
            Project.created_at.label("project_created_at"),
            Project.updated_at.label("project_updated_at"),
            func.coalesce(mapping_counts.c.reference_mapping_count, 0).label(
                "reference_mapping_count"
            ),
            Environment.id.label("environment_id"),
            Environment.name.label("environment_name"),
            Environment.created_at.label("environment_created_at"),
            Environment.updated_at.label("environment_updated_at"),
            func.sum(case((EnvironmentSource.source_kind == "metadata", 1), else_=0)).label(
                "metadata_count"
            ),
            func.sum(case((EnvironmentSource.source_kind == "logs", 1), else_=0)).label(
                "log_count"
            ),
            func.sum(case((EnvironmentSource.source_kind == "code", 1), else_=0)).label(
                "code_count"
            ),
        )
        .select_from(Project)
        .outerjoin(mapping_counts, mapping_counts.c.project_id == Project.id)
        .outerjoin(Environment, Environment.project_id == Project.id)
        .outerjoin(EnvironmentSource, EnvironmentSource.environment_id == Environment.id)
        .group_by(
            Project.id,
            Project.name,
            Project.description,
            Project.created_at,
            Project.updated_at,
            mapping_counts.c.reference_mapping_count,
            Environment.id,
            Environment.name,
            Environment.created_at,
            Environment.updated_at,
        )
        .order_by(Project.name, Environment.name)
    )

    summaries_by_id: dict[int, dict[str, Any]] = {}
    for row in session.execute(statement):
        summary = summaries_by_id.setdefault(
            row.project_id,
            {
                "id": row.project_id,
                "name": row.project_name,
                "description": row.project_description,
                "environment_count": 0,
                "metadata_source_count": 0,
                "log_source_count": 0,
                "reference_mapping_count": int(row.reference_mapping_count or 0),
                "environments": [],
                "created_at": row.project_created_at,
                "updated_at": row.project_updated_at,
            },
        )
        if row.environment_id is None:
            continue

        metadata_count = int(row.metadata_count or 0)
        log_count = int(row.log_count or 0)
        summary["environment_count"] += 1
        summary["metadata_source_count"] += metadata_count
        summary["log_source_count"] += log_count
        summary["environments"].append(
            {
                "id": row.environment_id,
                "name": row.environment_name,
                "metadata_source_count": metadata_count,
                "log_source_count": log_count,
                "code_artifact_count": int(row.code_count or 0),
                "created_at": row.environment_created_at,
                "updated_at": row.environment_updated_at,
            }
        )
    return list(summaries_by_id.values())


def _require_project(session: Session, project_id: int) -> None:
    if session.get(Project, project_id) is None:
        raise LookupError("Project not found")


def create_project(session: Session, name: str, description: str | None = None) -> Project:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Project name cannot be blank")
    if len(normalized) > 255:
        raise ValueError("Project name cannot exceed 255 characters")
    project = Project(name=normalized, description=description)
    session.add(project)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"Project already exists: {normalized}") from exc
    session.refresh(project)
    return project


def rename_project(session: Session, project_id: int, name: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found")
    normalized = name.strip()
    if project.name == normalized:
        return project
    existing = session.scalar(
        select(Project).where(
            Project.id != project_id,
            Project.name == normalized,
        )
    )
    if existing is not None:
        raise ValueError(f"Project already exists: {normalized}")
    project.name = normalized
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"Project already exists: {normalized}") from exc
    session.refresh(project)
    return project


def list_environments(session: Session, project_id: int) -> list[Environment]:
    return list(
        session.scalars(
            select(Environment)
            .where(Environment.project_id == project_id)
            .order_by(Environment.name)
        )
    )


def create_environment(session: Session, project_id: int, name: str) -> Environment:
    display_name = name.strip()
    existing = session.scalar(
        select(Environment).where(
            Environment.project_id == project_id,
            func.lower(Environment.name) == display_name.lower(),
        )
    )
    if existing is not None:
        raise ValueError(f"Environment already exists: {display_name}")
    env = Environment(project_id=project_id, name=display_name)
    session.add(env)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError(f"Environment already exists: {display_name}")
    session.refresh(env)
    return env


def rename_environment(session: Session, environment_id: int, name: str) -> Environment:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise LookupError("Environment not found")
    display_name = name.strip()
    if environment.name == display_name:
        return environment
    existing = session.scalar(
        select(Environment).where(
            Environment.project_id == environment.project_id,
            Environment.id != environment_id,
            func.lower(Environment.name) == display_name.lower(),
        )
    )
    if existing is not None:
        raise ValueError(f"Environment already exists: {display_name}")
    environment.name = display_name
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"Environment already exists: {display_name}") from exc
    session.refresh(environment)
    return environment
