"""Read-model cache and stable catalog building for Assets."""
from __future__ import annotations

from sqlalchemy.orm import Session

from datacoolie_studio.domains.assets.endpoint_identity import ASSETS_ENDPOINT_IDENTITY_VERSION
from datacoolie_studio.domains.assets.inventory import build_assets_inventory
from datacoolie_studio.domains.assets.inventory_helpers import metadata_source_uri_by_id
from datacoolie_studio.domains.assets.models import AssetsCatalog
from datacoolie_studio.domains.lineage.query_service import (
    lineage_input_fingerprint,
    load_or_build_lineage_graph,
)
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.keys import ASSETS_CATALOG


ASSETS_PROJECTOR_VERSION = (
    f"assets-catalog-v6-context-aware-resolution:{ASSETS_ENDPOINT_IDENTITY_VERSION}"
)


def load_or_build_assets_catalog(
    session: Session,
    environment_id: int,
    *,
    input_fingerprint: str | None = None,
) -> AssetsCatalog:
    parameters_fingerprint = empty_parameters_fingerprint()
    build_key = f"{environment_id}:{ASSETS_CATALOG}:{parameters_fingerprint}"
    if input_fingerprint is not None:
        cached = cached_read_model(
            environment_id=environment_id,
            model_key=ASSETS_CATALOG,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=ASSETS_PROJECTOR_VERSION,
        )
        if cached is not None:
            return AssetsCatalog(cached.payload, input_fingerprint)
    for _attempt in range(3):
        input_fingerprint = lineage_input_fingerprint(session, environment_id)
        cached = cached_read_model(
            environment_id=environment_id,
            model_key=ASSETS_CATALOG,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=input_fingerprint,
            producer_version=ASSETS_PROJECTOR_VERSION,
        )
        if cached is not None:
            return AssetsCatalog(cached.payload, input_fingerprint)
        with read_model_build_lock(build_key):
            input_fingerprint = lineage_input_fingerprint(session, environment_id)
            cached = cached_read_model(
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
            )
            if cached is not None:
                return AssetsCatalog(cached.payload, input_fingerprint)
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
            )
            lineage = load_or_build_lineage_graph(session, environment_id)
            payload = build_assets_inventory(lineage, metadata_source_uri_by_id({}, lineage))
            current_fingerprint = lineage_input_fingerprint(session, environment_id)
            if current_fingerprint != input_fingerprint:
                continue
            replace_read_model(
                environment_id=environment_id,
                model_key=ASSETS_CATALOG,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=input_fingerprint,
                producer_version=ASSETS_PROJECTOR_VERSION,
                payload=payload,
                expected_generation=generation,
            )
            return AssetsCatalog(payload, input_fingerprint)
    raise RuntimeError("Unable to build a stable Assets catalog")
__all__ = ["ASSETS_PROJECTOR_VERSION", "load_or_build_assets_catalog"]
