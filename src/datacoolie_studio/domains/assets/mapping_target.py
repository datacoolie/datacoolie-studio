from __future__ import annotations

from typing import Any


def asset_mapping_target(identifiers: list[dict[str, Any]], asset: dict[str, Any]) -> dict[str, str] | None:
    preferred_kinds = ["api_endpoint"] if asset.get("asset_type") == "api" else []
    preferred_kinds.extend(["logical_table", "physical_path"])
    for kind in preferred_kinds:
        identifier = next((item for item in identifiers if str(item.get("kind") or "") == kind), None)
        if identifier and identifier.get("normalized_value"):
            return {
                "kind": kind,
                "value": str(identifier["normalized_value"]),
                "display": str(identifier.get("display_value") or identifier["normalized_value"]),
            }
    return None
