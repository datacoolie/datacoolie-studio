from __future__ import annotations

from typing import Any


def manual_mapping_from_observations(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in observations:
        if str(item.get("source_type") or "") != "manual_mapping":
            continue
        try:
            mapping_id = int(item.get("mapping_id"))
        except (TypeError, ValueError):
            continue
        return {
            "mapping_id": mapping_id,
            "status": str(item.get("mapping_status") or "matched"),
            "note": _text_or_none(item.get("mapping_note")),
            "target_identifier_kind": _text_or_none(item.get("target_identifier_kind")),
            "target_normalized_value": _text_or_none(item.get("target_normalized_value")),
        }
    return None


def _text_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
