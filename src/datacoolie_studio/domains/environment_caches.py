from __future__ import annotations

from sqlalchemy.orm import Session

from datacoolie_studio.domains.read_models.cache import invalidate_environment_read_models
from datacoolie_studio.domains.read_models.keys import ASSETS_CATALOG, LINEAGE_GRAPH, LINEAGE_LATEST_RUNS, OVERVIEW


def invalidate_environment_derived_caches(
    session: Session,
    environment_id: int,
    *,
    structural: bool,
) -> None:
    """Evict derived Environment data after its materialized inputs change.

    Metadata and Code change the structural model used by Lineage, Assets, and
    Overview. Log ingestion only changes monitoring/summary inputs.
    """
    model_keys = {OVERVIEW, LINEAGE_LATEST_RUNS}
    if structural:
        model_keys.update({ASSETS_CATALOG, LINEAGE_GRAPH})
    invalidate_environment_read_models(session, environment_id, model_keys=model_keys)
