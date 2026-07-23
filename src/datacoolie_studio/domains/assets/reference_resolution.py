from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal


ResolutionState = Literal["automatic", "manual", "unresolved"]
ResolutionReason = Literal[
    "no_match",
    "multiple_matches",
    "conflicting_targets",
    "incomplete",
    "target_missing",
]


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    state: ResolutionState
    reason: ResolutionReason | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


AUTOMATIC_RESOLUTION = ReferenceResolution("automatic")
MANUAL_RESOLUTION = ReferenceResolution("manual")
UNRESOLVED_NO_MATCH = ReferenceResolution("unresolved", "no_match")


def unresolved_resolution(reason: ResolutionReason) -> ReferenceResolution:
    return ReferenceResolution("unresolved", reason)


def merge_resolution(
    current: ReferenceResolution,
    candidate: ReferenceResolution,
) -> ReferenceResolution:
    """Merge duplicate evidence without turning incomplete evidence into a false success."""
    if current.state == "unresolved" or candidate.state == "unresolved":
        return ReferenceResolution(
            "unresolved",
            _preferred_reason(current.reason, candidate.reason, fallback="incomplete"),
        )
    if current.state == "manual" or candidate.state == "manual":
        return MANUAL_RESOLUTION
    return AUTOMATIC_RESOLUTION


def group_resolution(
    occurrence_resolutions: Iterable[ReferenceResolution],
    resolved_asset_ids: Iterable[str],
) -> ReferenceResolution:
    resolutions = list(occurrence_resolutions)
    targets = {value for value in resolved_asset_ids if value}
    if len(targets) > 1:
        return unresolved_resolution("conflicting_targets")
    if not resolutions or not targets:
        reason = _first_unresolved_reason(resolutions) or "no_match"
        return unresolved_resolution(reason)
    if any(item.state == "unresolved" for item in resolutions):
        return unresolved_resolution(_first_unresolved_reason(resolutions) or "incomplete")
    if any(item.state == "manual" for item in resolutions):
        return MANUAL_RESOLUTION
    return AUTOMATIC_RESOLUTION


def _first_unresolved_reason(
    resolutions: Iterable[ReferenceResolution],
) -> ResolutionReason | None:
    result: ResolutionReason | None = None
    for item in resolutions:
        if item.state != "unresolved":
            continue
        result = _preferred_reason(result, item.reason, fallback="incomplete")
    return result


def _preferred_reason(
    current: ResolutionReason | None,
    candidate: ResolutionReason | None,
    *,
    fallback: ResolutionReason,
) -> ResolutionReason:
    priority: tuple[ResolutionReason, ...] = (
        "target_missing",
        "conflicting_targets",
        "multiple_matches",
        "incomplete",
        "no_match",
    )
    values = {value for value in (current, candidate) if value is not None}
    return next((value for value in priority if value in values), fallback)
