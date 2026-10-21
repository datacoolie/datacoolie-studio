"""Draft persistence and staged SQL target pinning for metadata editors."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import (
    EnvironmentMetadataEditorDraft,
    EnvironmentSource,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.metadata.document_lifecycle import (
    document_sheets_as_rows,
    materialize_generated_ids,
)
from datacoolie_studio.domains.metadata.document_primitives import validation_issue
from datacoolie_studio.domains.metadata.ordering import canonicalize_editor_document
from datacoolie_studio.domains.sql.operations import (
    SqlOperationError,
    normalize_pending_sql_files,
)
from datacoolie_studio.domains.sql.reader import (
    SqlReferenceError,
    resolve_sql_file_target,
)








def load_environment_editor_draft(
    session: Session,
    environment_id: int,
) -> dict[str, Any] | None:
    draft = latest_environment_draft(session, environment_id)
    if draft is None:
        return None
    document = canonicalize_editor_document(json.loads(draft.editor_document_json))
    document["issues"] = []
    return document


def save_environment_editor_draft(
    session: Session,
    environment_id: int,
    document: dict[str, Any],
    expected_revision: dict[str, Any],
    *,
    sources: list[EnvironmentSource] | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    document = canonicalize_editor_document(document)
    try:
        document["sql_files"] = normalize_pending_sql_files(document.get("sql_files"))
    except SqlOperationError as exc:
        document["sql_files"] = document.get("sql_files") or []
        document.setdefault("issues", []).append(
            validation_issue("error", "sql_files", 0, "content", str(exc))
        )
    else:
        pin_new_pending_sql_file_targets(
            session,
            environment_id,
            sources or [],
            document["sql_files"],
            secret_store=secret_store,
        )
    materialize_generated_ids(document_sheets_as_rows(document))
    document["issues"] = []
    payload = json.dumps(document, ensure_ascii=False)
    revision_payload = json.dumps(
        expected_revision or document.get("source", {}).get("revision") or {},
        sort_keys=True,
    )
    draft = latest_environment_draft(session, environment_id)
    if draft is None:
        draft = EnvironmentMetadataEditorDraft(
            environment_id=environment_id,
            base_revision_json=revision_payload,
            editor_document_json=payload,
        )
        session.add(draft)
    else:
        draft.base_revision_json = revision_payload
        draft.editor_document_json = payload
    session.commit()
    return document


def pin_new_pending_sql_file_targets(
    session: Session,
    environment_id: int,
    sources: list[EnvironmentSource],
    operations: list[dict[str, Any]],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> None:
    """Pin SQL resolution context when a staged draft is first persisted."""

    source_by_id = {
        source.id: source
        for source in sources
        if source.enabled
        and source.environment_id == environment_id
        and source.source_kind == "metadata"
    }
    for operation in operations:
        if (
            operation.get("base_uri") is not None
            or operation.get("config_revision") is not None
        ):
            continue
        source = source_by_id.get(int(operation["source_id"]))
        if source is None:
            continue
        try:
            target = resolve_sql_file_target(
                source,
                str(operation["query"]),
                session=session,
                secret_store=secret_store,
            )
        except SqlReferenceError:
            continue
        operation["base_uri"] = target["base_uri"]
        operation["config_revision"] = target.get("config_revision")


def delete_environment_editor_draft(
    session: Session,
    environment_id: int,
    *,
    commit: bool = True,
) -> None:
    draft = latest_environment_draft(session, environment_id)
    if draft is not None:
        session.delete(draft)
        if commit:
            session.commit()
        else:
            session.flush()




def latest_environment_draft(
    session: Session,
    environment_id: int,
) -> EnvironmentMetadataEditorDraft | None:
    return session.scalars(
        select(EnvironmentMetadataEditorDraft)
        .where(EnvironmentMetadataEditorDraft.environment_id == environment_id)
        .order_by(
            EnvironmentMetadataEditorDraft.updated_at.desc(),
            EnvironmentMetadataEditorDraft.id.desc(),
        )
    ).first()
