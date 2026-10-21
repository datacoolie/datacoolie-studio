from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.monitoring.page_contracts import (
    PUBLIC_PAGE_SECTIONS,
    RESPONSE_VERSION,
)


def public_monitoring_page(page: str, payload: dict[str, Any]) -> dict[str, Any]:
    if page == "environment-overview":
        return {
            "schema_version": RESPONSE_VERSION,
            "page": page,
            **payload,
        }
    sections = PUBLIC_PAGE_SECTIONS.get(page)
    if sections is None:
        raise ValueError(f"Unknown public Monitoring page: {page}")
    projected: dict[str, Any] = {
        "schema_version": RESPONSE_VERSION,
        "page": page,
        "summary": payload.get("summary") or {},
    }
    for section in sections:
        value = payload.get(section)
        if section == "diagnostics":
            value = dict(value or {})
            value.pop("job_id_evidence", None)
        projected[section] = value
    if payload.get("errors"):
        projected["errors"] = payload["errors"]
    if payload.get("log_admission"):
        projected["log_admission"] = payload["log_admission"]
    return projected
