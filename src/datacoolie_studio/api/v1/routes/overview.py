from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datacoolie_studio.api.v1.contracts.overview import EnvironmentOverviewResponse
from datacoolie_studio.api.v1.dependencies import get_credential_secret_store
from datacoolie_studio.db.session import get_session
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.code_artifacts.service import CodeArtifactSourceUnavailableError
from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import MetadataSourcesUnavailableError
from datacoolie_studio.domains.overview.service import load_environment_overview


router = APIRouter(tags=["overview"])


@router.get("/environments/{environment_id}/overview", response_model=EnvironmentOverviewResponse)
def get_environment_overview(
    environment_id: int,
    session: Session = Depends(get_session),
    secret_store: CredentialSecretStore = Depends(get_credential_secret_store),
):
    try:
        return load_environment_overview(session, environment_id, secret_store=secret_store)
    except MetadataSourcesUnavailableError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.code,
                "message": str(exc),
                "source_ids": exc.source_ids,
            },
        ) from exc
    except MetadataReadError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "metadata_read_failed", "message": str(exc)},
        ) from exc
    except CodeArtifactSourceUnavailableError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.code,
                "message": str(exc),
                "source_ids": [exc.source_id],
            },
        ) from exc
    except ArtifactIndexError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "code_artifact_read_failed", "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
