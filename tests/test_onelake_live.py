"""Opt-in read-only smoke test for a real OneLake Lakehouse Files location.

Set DATACOOLIE_ONELAKE_TEST_URI to an ABFSS or HTTPS Files path. Authentication
is resolved by DefaultAzureCredential, so the same test supports Azure CLI,
managed identity, workload identity, or AZURE_CLIENT_* service-principal
environment variables. The test never writes or stores credential values.
"""

from __future__ import annotations

import os

import pytest

from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.inventory import (
    StorageInventoryRequest,
    inventory,
)
from datacoolie_studio.domains.storage.uri import canonical_cloud_uri


ONELAKE_TEST_URI = os.getenv("DATACOOLIE_ONELAKE_TEST_URI")


@pytest.mark.skipif(
    not ONELAKE_TEST_URI,
    reason="DATACOOLIE_ONELAKE_TEST_URI is not configured",
)
def test_real_onelake_files_location_supports_bounded_read_probe() -> None:
    canonical_uri = canonical_cloud_uri(ONELAKE_TEST_URI or "", provider="onelake")
    adapter = create_storage_adapter(
        StorageBinding(provider="onelake", auth_mode="ambient"),
        uri=canonical_uri,
    )

    observed = inventory(
        adapter,
        StorageInventoryRequest(
            uri=canonical_uri,
            purpose="probe",
            recursive=False,
            object_limit=1,
        ),
    )

    assert observed.requests == 1
    assert observed.pages <= 1
    assert len(observed.objects) <= 1
    assert observed.bytes_read == 0
