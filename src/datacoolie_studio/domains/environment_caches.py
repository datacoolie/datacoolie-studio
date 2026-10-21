from __future__ import annotations

from datacoolie_studio.domains.read_models.cache import invalidate_environment_read_models
from datacoolie_studio.domains.read_models.keys import (
    ASSETS_CATALOG, ASSETS_IDENTITY_CATALOG, ASSETS_IDENTITY_MAP,
    LINEAGE_GRAPH, LINEAGE_LATEST_RUNS, OVERVIEW, monitoring_page,
)


def invalidate_environment_derived_caches(
    environment_id: int,
    *,
    structural: bool,
    identity: bool = True,
) -> None:
    """Evict derived Environment data after its materialized inputs change.

    Metadata and Code change the structural model used by Lineage, Assets, and
    Overview. Log ingestion only changes monitoring/summary inputs.
    """
    model_keys = {OVERVIEW, LINEAGE_LATEST_RUNS}
    if identity:
        model_keys.add(ASSETS_IDENTITY_MAP)
    if structural:
        model_keys.update({
            ASSETS_CATALOG, LINEAGE_GRAPH,
        })
        if identity:
            model_keys.update({ASSETS_IDENTITY_CATALOG, monitoring_page("maintenance")})
    invalidate_environment_read_models(environment_id, model_keys=model_keys)
