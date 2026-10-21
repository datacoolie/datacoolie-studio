"""Public errors for metadata editor application workflows."""

from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.metadata.reader import MetadataReadError


class MetadataConflictError(MetadataReadError):
    """The source or its configuration changed during an editor operation."""


class MetadataValidationError(MetadataReadError):
    """The editor document cannot be published in its current form."""

    def __init__(self, message: str, validation: dict[str, Any]) -> None:
        super().__init__(message)
        self.validation = validation
