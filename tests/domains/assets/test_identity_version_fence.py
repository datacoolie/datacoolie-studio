from __future__ import annotations

from datacoolie_studio.domains.assets.catalog import ASSETS_PROJECTOR_VERSION
from datacoolie_studio.domains.assets.endpoint_identity import (
    ASSETS_ENDPOINT_IDENTITY_VERSION,
)
from datacoolie_studio.domains.assets.identity_catalog import (
    IDENTITY_CATALOG_PROJECTOR_VERSION,
)
from datacoolie_studio.domains.assets.identity_mapping import (
    IDENTITY_MAP_PROJECTOR_VERSION,
)
from datacoolie_studio.domains.lineage.service import LINEAGE_ANALYZER_VERSION
from datacoolie_studio.domains.metadata.normalizer import METADATA_NORMALIZER_VERSION
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey


def test_identity_correction_fences_previous_derived_cache_producers():
    assert ASSETS_ENDPOINT_IDENTITY_VERSION == "assets-endpoint-identity-v3"
    assert METADATA_NORMALIZER_VERSION == "metadata-normalizer-v9-workspace-identity"

    producer_versions = (
        ASSETS_PROJECTOR_VERSION,
        IDENTITY_CATALOG_PROJECTOR_VERSION,
        IDENTITY_MAP_PROJECTOR_VERSION,
        LINEAGE_ANALYZER_VERSION,
    )
    for producer_version in producer_versions:
        assert ASSETS_ENDPOINT_IDENTITY_VERSION in producer_version
        legacy_version = producer_version.replace(
            ASSETS_ENDPOINT_IDENTITY_VERSION,
            "assets-endpoint-identity-v2",
        )
        current_key = ResultCacheKey(
            environment_id=7,
            namespace="identity-test",
            parameters_fingerprint="parameters",
            input_fingerprint="same-input",
            producer_version=producer_version,
        )
        legacy_key = ResultCacheKey(
            environment_id=7,
            namespace="identity-test",
            parameters_fingerprint="parameters",
            input_fingerprint="same-input",
            producer_version=legacy_version,
        )
        assert current_key.identity != legacy_key.identity
