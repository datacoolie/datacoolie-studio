from __future__ import annotations

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment, Project
from datacoolie_studio.domains.workspace.derived_cleanup import (
    log_source_ids_for_environment,
    log_source_ids_for_project,
    purge_analytics_cache_by_source_ids,
)
from datacoolie_studio.domains.workspace.lifecycle import (
    create_environment,
    create_project,
    create_project_reference_mapping,
    delete_project_reference_mapping,
    list_environments,
    list_project_reference_mappings,
    list_project_summaries,
    list_projects,
    rename_environment,
    rename_project,
    update_project_reference_mapping,
)
from datacoolie_studio.domains.workspace.sources_query import (
    list_code_artifacts,
    list_log_sources,
    list_metadata_sources,
    list_sql_sources,
    sources_workspace_log_summary,
    sources_workspace,
)
from datacoolie_studio.domains.read_models.cache import (
    invalidate_environment_read_models,
    invalidate_project_read_models,
)


__all__ = [
    "create_environment",
    "create_project",
    "create_project_reference_mapping",
    "delete_environment",
    "delete_project",
    "delete_project_reference_mapping",
    "list_code_artifacts",
    "list_environments",
    "list_log_sources",
    "list_metadata_sources",
    "list_project_reference_mappings",
    "list_project_summaries",
    "list_projects",
    "list_sql_sources",
    "rename_environment",
    "rename_project",
    "sources_workspace",
    "sources_workspace_log_summary",
    "update_project_reference_mapping",
]


# Keep cleanup and invalidation as injectable module-level seams for the
# application boundary. The delete response remains intentionally unchanged;
# derived cleanup reports failures through the cleanup coordinator's logs.
_purge_analytics_cache_by_source_ids = purge_analytics_cache_by_source_ids


def delete_project(session: Session, project_id: int) -> bool:
    project = session.get(Project, project_id)
    if project is None:
        return False
    source_ids = log_source_ids_for_project(session, project_id)
    invalidate_project_read_models(session, project_id)
    session.delete(project)
    session.commit()
    _purge_analytics_cache_by_source_ids(source_ids)
    return True


def delete_environment(session: Session, environment_id: int) -> bool:
    environment = session.get(Environment, environment_id)
    if environment is None:
        return False
    source_ids = log_source_ids_for_environment(session, environment_id)
    invalidate_environment_read_models(environment_id)
    session.delete(environment)
    session.commit()
    _purge_analytics_cache_by_source_ids(source_ids)
    return True
