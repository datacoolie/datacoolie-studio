"""Current source evidence derived from revision-bound file manifests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from datacoolie_studio.domains.sources.repository import source_admissions
from datacoolie_studio.domains.analytics.errors import AnalyticsInvalidJsonError
from datacoolie_studio.domains.analytics.log_contract import admit_log_record


def inspect_log_file(uri: str, expected_type: str) -> dict:
    summary = {"admitted": 0, "excluded": 0, "versions": {}, "locations": []}
    try:
        with Path(uri).open(encoding="utf-8") as handle:
            for index, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                record = json.loads(line, parse_float=Decimal)
                version = str(record.get("log_schema_version", "absent")) if isinstance(record, dict) else "non-object"
                summary["versions"][version] = summary["versions"].get(version, 0) + 1
                admitted = admit_log_record(record, expected_type=expected_type, uri=uri, line=index)
                summary["admitted" if admitted else "excluded"] += 1
                if not admitted and len(summary["locations"]) < 16:
                    summary["locations"].append({"line": index, "version": version})
    except (OSError, ValueError) as exc:
        raise AnalyticsInvalidJsonError(f"{uri}: {exc}") from exc
    return summary


def source_admission(session, source_id: int) -> dict:
    return source_admissions(session, [source_id])[source_id]
