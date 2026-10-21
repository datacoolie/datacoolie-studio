from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.sql import (
    SqlReferenceRequest,
    SqlReferenceResponse,
)
from datacoolie_studio.api.v1.dependencies import (
    get_credential_secret_store,
    require_loopback_client,
)
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.sql.reader import SqlReferenceError, resolve_sql_reference


router = APIRouter(tags=["sql"])


@router.post(
    "/environments/{environment_id}/sql-reference",
    response_model=SqlReferenceResponse,
)
def read_sql_reference(
    environment_id: int,
    payload: SqlReferenceRequest,
    request: Request,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    source = session.get(EnvironmentSource, payload.source_id)
    if (
        source is None
        or source.environment_id != environment_id
        or source.source_kind != "metadata"
    ):
        raise HTTPException(status_code=404, detail="Metadata source not found")
    if source.storage_auth_mode == "credential_profile":
        require_loopback_client(request)
    try:
        resolved = resolve_sql_reference(
            session,
            source,
            payload.query,
            secret_store=secret_store,
        )
    except SqlReferenceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return {
        "schema_version": "sql-reference.v1",
        "source_id": source.id,
        "source_uri": source.uri,
        **resolved,
    }
