from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StudioTestPaths:
    data_dir: Path
    database_path: Path
    result_cache_path: Path
    analytics_path: Path


@dataclass(frozen=True)
class ProjectEnvironment:
    project_id: int
    environment_id: int
