"""Provider-neutral revision comparisons for storage and metadata workflows."""

from __future__ import annotations

from typing import Any


def same_content_revision(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    """Compare content identity when both revisions carry a content hash."""

    left_hash = left.get("content_hash")
    right_hash = right.get("content_hash")
    if not left_hash or not right_hash:
        return False
    return (
        str(left_hash) == str(right_hash)
        and int(left.get("size", -1)) == int(right.get("size", -2))
    )


def same_provider_version(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    """Compare provider tokens without treating two missing tokens as equal."""

    left_token = left.get("provider_revision")
    right_token = right.get("provider_revision")
    if left_token is None or right_token is None:
        return False
    return (
        str(left_token) == str(right_token)
        and int(left.get("size", -1)) == int(right.get("size", -2))
    )


def same_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare a persisted revision using content identity or a version token.

    A content hash is preferred when both sides have one. When an older or
    provider-specific revision omits hashes, a non-empty provider token is the
    only safe fallback. Two missing tokens are never considered equal.
    """

    if left.get("content_hash") and right.get("content_hash"):
        return same_content_revision(left, right)
    return same_provider_version(left, right)
