from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

class ModuleInfo(BaseModel):
    key: str
    name: str
    description: str
    group: str
    status: Literal["available", "coming_soon"]
    togglable: bool
    default_enabled: bool
    pages: list[str]
    enabled: bool


class ModuleStateUpdateRequest(BaseModel):
    enabled: bool
