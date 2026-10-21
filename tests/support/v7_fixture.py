"""Pinned producer v7 records used by standalone Studio consumer tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "datacoolie" / "logging" / "v7"


def _read(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def v7_dataflow_entry() -> dict[str, Any]:
    """Return a fresh copy of an actual-producer v7 dataflow record."""

    return copy.deepcopy(_read("observed_dataflow.json"))


def v7_job_entry(**updates: Any) -> dict[str, Any]:
    """Return a fresh producer v7 job record with selected test overrides."""

    entry = _read("observed_job.json")
    entry.update(updates)
    return entry


def v7_schema_document() -> dict[str, Any]:
    """Return the pinned producer schema used for consumer parity checks."""

    return _read("schema.json")
