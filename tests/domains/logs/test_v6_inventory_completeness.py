from __future__ import annotations

from datetime import date

import pytest

from datacoolie_studio.domains.logs.discovery import LogSyncSpec
from datacoolie_studio.domains.logs.planner import PlannerState, StreamDefinition, plan_stream_sync
from datacoolie_studio.domains.logs.partition import PartitionGranularity
from datacoolie_studio.domains.storage.inventory import StorageInventory


class PartialAdapter:
    def inventory(self, request):
        return StorageInventory(objects=(), completeness="partial", requests=1, pages=1,
            directories_visited=1, objects_inspected=0, matching_objects=0,
            retries=0, throttles=0, bytes_read=0, duration_ms=1, early_stop_reason="object_limit")


@pytest.mark.parametrize("initial", [True, False])
def test_partial_file_inventory_cannot_publish_or_infer_removal(initial):
    definition = StreamDefinition("dataflow_json", "s3://bucket/logs", ".json")
    state = None if initial else PlannerState("dataflow_json", definition.root_uri,
        "learned", "%Y%m%d", PartitionGranularity.DAY, date(2026, 10, 6))
    with pytest.raises(ValueError, match="complete file inventory"):
        plan_stream_sync(PartialAdapter(), definition, state=state,
            manifest={}, spec=LogSyncSpec(), today=date(2026, 10, 6))
