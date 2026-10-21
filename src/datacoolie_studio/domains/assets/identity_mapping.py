"""Assets-owned serving map for captured destination identity.

The metadata identity catalog and the log serving map have different grains:
the former contains published endpoint declarations, while the latter contains
one row for each distinct endpoint signature in the admitted analytics scope.
This module owns the second concern.  It deliberately keeps the map out of
the analytics database and materializes it as a connection-local temporary
relation for the request that consumes it.

The map cache is split into deterministic signature buckets.  A small manifest
is useful as a cheap completeness check, while buckets keep individual result
cache entries below the bounded result-cache payload limit.  A manifest without
every matching bucket is a cache miss; callers never receive a partial map.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.core.request_timing import timed_stage
from datacoolie_studio.domains.assets.endpoint_identity import (
    ASSETS_ENDPOINT_IDENTITY_VERSION,
    DESTINATION_CONFIGURE_IDENTITY_FIELDS,
    DESTINATION_SIGNATURE_COLUMNS,
    destination_signature,
    destination_signature_sql,
)
from datacoolie_studio.domains.read_models.cache import (
    fingerprint,
    read_model_build_lock,
    replace_read_model,
)
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
from datacoolie_studio.domains.read_models.provider import result_cache_provider


# This module owns request serving and cache lifecycle.
IDENTITY_MAP_NAMESPACE = "assets.identity-map"
IDENTITY_MAP_BUCKET_NAMESPACE = "assets.identity-map.bucket"
IDENTITY_MAP_TABLE = "_assets_destination_identity_map"
IDENTITY_MAP_PROJECTOR_VERSION = f"assets-identity-map-v4:{ASSETS_ENDPOINT_IDENTITY_VERSION}"

# A manifest plus 64 buckets fits the existing environment cache budget while
# keeping a high-distinctness 100k endpoint map well below the 8 MiB entry
# limit in normal payload distributions.  If an unusually large row still
# exceeds the limit, the request remains correct and simply serves uncached.
IDENTITY_MAP_MAX_BUCKET_COUNT = 64
IDENTITY_MAP_PAYLOAD_TARGET_BYTES = 1 * 1024 * 1024

IDENTITY_MAP_COLUMNS: tuple[str, ...] = (
    "signature",
    "target",
    "identity_status",
    "identity_reason",
    "identity_version",
    "identity_provenance",
    "identity_metadata_sources",
    "destination_catalog",
    "destination_database",
    "destination_schema",
    "destination_table",
    "destination_full_table",
    "destination_path",
    "destination_connection_type",
    "destination_id",
    "destination_name",
)
_IDENTITY_MAP_COLUMN_INDEX = {
    column: index for index, column in enumerate(IDENTITY_MAP_COLUMNS)
}

_MANIFEST_SCHEMA = "assets.identity-map-manifest.v4"
_BUCKET_SCHEMA = "assets.identity-map-bucket.v4"
_MAX_BUILD_RETRIES = 3


class IdentityMapRevisionMismatch(RuntimeError):
    """Raised when published metadata changes during map construction."""


class IdentityMapGenerationMismatch(RuntimeError):
    """Raised when a cache clear supersedes an in-flight map build."""


@dataclass(frozen=True, slots=True)
class _CacheFence:
    """Result-cache generation captured before an identity build starts."""

    manifest_generation: str
    family_generation: str


@dataclass(frozen=True, slots=True)
class IdentityMapContext:
    """Request-local identity map and its input/version fences.

    ``catalog`` is intentionally optional.  A complete cache hit does not
    parse published metadata; it only needs the already resolved compact map.
    It is populated on a cache miss for compatibility with callers that use
    ``resolve`` directly.
    """

    environment_id: int
    version: str
    metadata_revision: str
    table_name: str
    rows: tuple[Any, ...] = ()
    catalog: Any | None = field(default=None, repr=False, compare=False)
    _session: Session | None = field(default=None, repr=False, compare=False)

    def resolve(self, signatures: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        catalog = self.catalog
        if catalog is None:
            if self._session is None:
                raise RuntimeError("Assets identity catalog is unavailable for direct resolution")
            catalog = _load_catalog(self._session, self.environment_id, self.metadata_revision)
        return _resolve_distinct(
            self.environment_id,
            signatures,
            catalog,
            identity_version_value=self.version,
        )

    def create_mapping_table(
        self,
        conn: Any,
        signatures: Iterable[Mapping[str, Any]] | None = None,
    ) -> str:
        rows = self.resolve(signatures) if signatures is not None else list(self.rows)
        return create_identity_mapping_table(conn, rows, table_name=self.table_name)


def identity_map_context(
    session: Session,
    environment_id: int,
    analytics_context: Any | None = None,
    *,
    revision: str | None = None,
) -> IdentityMapContext:
    """Prepare one complete request-local map and TEMP relation.

    The cheap identity version and the manifest are checked before the
    distinct-fact scan or published-document catalog load.  Identical misses
    are coalesced by the existing result-build coordinator, so concurrent page
    requests resolve each distinct signature only once for the retained map.
    """

    metadata_revision = revision or _identity_revision(session, environment_id)
    version = _identity_version(
        metadata_revision,
        environment_id,
        analytics_context=analytics_context,
    )
    context = IdentityMapContext(
        environment_id=int(environment_id),
        version=version,
        metadata_revision=metadata_revision,
        table_name=IDENTITY_MAP_TABLE,
        _session=session,
    )

    fence = _analytics_fence(analytics_context)
    if fence is None:
        # Identity-only callers still receive a useful context.  No analytics
        # connection means there is no map relation to create.
        return _with_catalog(context, _load_catalog(session, environment_id, metadata_revision))

    conn, source_ids = fence
    map_parameters = _map_parameters(source_ids, _analytics_generation(analytics_context))
    cached_rows = _load_complete_cached_map(context, map_parameters)
    if cached_rows is not None:
        context = _with_rows(context, cached_rows)
    else:
        build_key = _build_key(context, map_parameters)
        with read_model_build_lock(build_key):
            # Another request may have completed while this one waited for the
            # coordinator.  This second lookup is what prevents duplicate
            # distinct scans and endpoint resolutions.
            cached_rows = _load_complete_cached_map(context, map_parameters)
            if cached_rows is not None:
                context = _with_rows(context, cached_rows)
            else:
                for attempt in range(_MAX_BUILD_RETRIES):
                    # Both generations are captured before any distinct scan,
                    # catalog extraction, or endpoint resolution.  Bucket
                    # writes must use this exact family fence, never a token
                    # captured after the expensive work has finished.
                    cache_fence = _capture_cache_fence(context, map_parameters)
                    with timed_stage("identity_distinct"):
                        signatures = _distinct_fact_signatures(conn, source_ids)
                    with timed_stage("identity_catalog"):
                        catalog = _load_catalog(session, environment_id, metadata_revision)
                    try:
                        rows, _published = _build_and_publish_map(
                            context,
                            map_parameters,
                            signatures,
                            catalog,
                            session,
                            cache_fence,
                        )
                    except IdentityMapGenerationMismatch:
                        if attempt + 1 >= _MAX_BUILD_RETRIES:
                            raise
                        continue
                    break
                # A result-cache backend can be unavailable or can reject a
                # stale generation.  The rows are still correct for this
                # request when the input fence is unchanged; only a complete
                # cache hit may be used by later requests.
                context = _with_rows(_with_catalog(context, catalog), rows)

    with timed_stage("identity_temp"):
        create_identity_mapping_table(conn, context.rows, table_name=context.table_name)
    return context


def create_identity_mapping_table(
    conn: Any,
    rows: Iterable[Any],
    *,
    table_name: str = IDENTITY_MAP_TABLE,
    batch_size: int = 512,
) -> str:
    """Create a typed, one-row-per-signature temporary mapping relation."""

    table_name = _sql_identifier(table_name)
    materialized = _materialize_mapping_rows(rows)
    if (
        materialized
        and len({values[0] for values in materialized}) == len(materialized)
        and _create_identity_mapping_table_arrow(conn, table_name, materialized)
    ):
        return table_name

    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {table_name} (
            signature VARCHAR PRIMARY KEY,
            target VARCHAR,
            identity_status VARCHAR NOT NULL,
            identity_reason VARCHAR,
            identity_version VARCHAR NOT NULL,
            identity_provenance JSON,
            identity_metadata_sources JSON,
            destination_catalog VARCHAR,
            destination_database VARCHAR,
            destination_schema VARCHAR,
            destination_table VARCHAR,
            destination_full_table VARCHAR,
            destination_path VARCHAR,
            destination_connection_type VARCHAR,
            destination_id VARCHAR,
            destination_name VARCHAR
        )
        """
    )
    row_placeholders = "(" + ", ".join("?" for _ in IDENTITY_MAP_COLUMNS) + ")"
    insert_prefix = f"INSERT INTO {table_name} ({', '.join(IDENTITY_MAP_COLUMNS)}) VALUES "
    batch: list[tuple[Any, ...]] = []
    for row in materialized:
        batch.append(row)
        if len(batch) >= max(1, int(batch_size)):
            conn.execute(
                insert_prefix + ", ".join(row_placeholders for _ in batch),
                [value for values in batch for value in values],
            )
            batch.clear()
    if batch:
        conn.execute(
            insert_prefix + ", ".join(row_placeholders for _ in batch),
            [value for values in batch for value in values],
        )
    return table_name


def _create_identity_mapping_table_arrow(
    conn: Any,
    table_name: str,
    rows: list[tuple[Any, ...]],
) -> bool:
    """Use the installed Arrow bridge for one set-based DuckDB relation.

    PyArrow is an optional acceleration path.  The parameterized VALUES
    fallback remains available for minimal installations and keeps the same
    typed table contract.
    """

    try:
        import pyarrow as pa
    except ImportError:
        return False

    try:
        # Compact cache rows already contain the final JSON text.  Preserve
        # strings as-is and only stringify cold resolver values, avoiding a
        # second mapping pass on every warm request.
        arrays = {}
        for index, column in enumerate(IDENTITY_MAP_COLUMNS):
            values = [
                value if value is None or isinstance(value, str) else str(value)
                for value in (row[index] for row in rows)
            ]
            arrays[column] = pa.array(values, type=pa.string())
        # A registered Arrow relation is connection-local and typed (all map
        # leaves are VARCHAR, with JSON text decoded at the response boundary).
        # It avoids a second row-by-row/JSON-cast pass on every warm request.
        try:
            conn.unregister(table_name)
        except Exception:
            pass
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        except Exception:
            pass
        conn.register(table_name, pa.table(arrays))
        return True
    except Exception:
        try:
            conn.execute(f"DELETE FROM {table_name}")
        except Exception:
            pass
        return False


def build_identity_map(
    environment_id: int,
    signatures: Iterable[Mapping[str, Any]],
    catalog: Any,
    *,
    identity_version_value: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve each distinct signature exactly once using the Assets catalog."""

    return _resolve_distinct(
        environment_id,
        signatures,
        catalog,
        identity_version_value=identity_version_value,
    )


def _build_and_publish_map(
    context: IdentityMapContext,
    map_parameters: str,
    signatures: list[dict[str, Any]],
    catalog: Any,
    session: Session,
    cache_fence: _CacheFence,
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve and publish a complete bucket set under generation fences."""

    rows = _resolve_distinct(
        context.environment_id,
        signatures,
        catalog,
        identity_version_value=context.version,
    )
    # The catalog owner uses a separate committed read session for this fence;
    # pending writes on the caller's ORM session cannot be mistaken for a
    # published materialization change.
    if _identity_revision(session, context.environment_id) != context.metadata_revision:
        raise IdentityMapRevisionMismatch(
            "Published metadata changed while preparing destination identity"
        )
    # Stable partitioning is based only on the opaque signature.  It does not
    # depend on input order or on the catalog's row order.
    bucket_count = _bucket_count(rows)
    buckets = _partition_rows(rows, bucket_count)
    inline_rows = (
        rows
        if bucket_count == 1
        and _json_size(
            _manifest_payload(
                context,
                map_parameters,
                bucket_count,
                buckets,
                inline_rows=rows,
            )
        ) <= IDENTITY_MAP_PAYLOAD_TARGET_BYTES
        else None
    )
    store = result_cache_provider().store
    manifest_key = _manifest_key(context, map_parameters)
    _assert_cache_fence(store, context, map_parameters, cache_fence)

    with timed_stage("identity_cache"):
        if inline_rows is None:
            for bucket_index in range(bucket_count):
                bucket_rows = buckets.get(bucket_index, [])
                bucket_key = _bucket_key(context, map_parameters, bucket_index)
                result = replace_read_model(
                    environment_id=context.environment_id,
                    model_key=bucket_key.namespace,
                    parameters_fingerprint=bucket_key.parameters_fingerprint,
                    input_fingerprint=bucket_key.input_fingerprint,
                    producer_version=bucket_key.producer_version,
                    payload=_bucket_payload(
                        context,
                        bucket_index,
                        bucket_count,
                        bucket_rows,
                    ),
                    expected_generation=cache_fence.family_generation,
                )
                if not result.stored:
                    # A clear/generation advance invalidated this build. Never
                    # publish a manifest that makes only some buckets current.
                    _assert_cache_fence(store, context, map_parameters, cache_fence)
                    # The resilient cache may be unavailable. The current request
                    # can use its computed rows, but no manifest is published.
                    return rows, False
                _assert_cache_fence(store, context, map_parameters, cache_fence)

        _assert_cache_fence(store, context, map_parameters, cache_fence)
        manifest_result = replace_read_model(
            environment_id=context.environment_id,
            model_key=manifest_key.namespace,
            parameters_fingerprint=manifest_key.parameters_fingerprint,
            input_fingerprint=manifest_key.input_fingerprint,
            producer_version=manifest_key.producer_version,
            payload=_manifest_payload(
                context,
                map_parameters,
                bucket_count,
                buckets,
                inline_rows=inline_rows,
            ),
            expected_generation=cache_fence.manifest_generation,
        )
        if not manifest_result.stored:
            _assert_cache_fence(store, context, map_parameters, cache_fence)
            return rows, False
        _assert_cache_fence(store, context, map_parameters, cache_fence)
        if _identity_revision(session, context.environment_id) != context.metadata_revision:
            raise IdentityMapRevisionMismatch(
                "Published metadata changed while publishing destination identity"
            )
    return rows, True


def _load_complete_cached_map(
    context: IdentityMapContext,
    map_parameters: str,
) -> list[Any] | None:
    """Load every bucket only when the manifest and all fences are current."""

    store = result_cache_provider().store
    cache_fence = _capture_cache_fence(context, map_parameters)
    with timed_stage("identity_cache"):
        manifest_key = _manifest_key(context, map_parameters)
        manifest_result = store.get(manifest_key)
        manifest = manifest_result.payload if manifest_result is not None else None
        if not _valid_manifest(manifest, context, map_parameters):
            return None
        bucket_count = int(manifest["bucket_count"])
        rows: list[Any] = []
        non_null_targets = 0
        entries = manifest["buckets"]
        inline_rows = manifest.get("inline_rows")
        if inline_rows is not None:
            if bucket_count != 1 or not isinstance(inline_rows, list):
                return None
            entry = entries[0]
            inline_payload = {
                "schema_version": _BUCKET_SCHEMA,
                "environment_id": context.environment_id,
                "version": context.version,
                "bucket_index": 0,
                "bucket_count": 1,
                "complete": True,
                "signature_count": len(inline_rows),
                "non_null_target_count": sum(
                    1
                    for row in inline_rows
                    if _cached_row_value(row, "target") not in (None, "")
                ),
                "signature_digest": entry["digest"],
                "rows": inline_rows,
            }
            bucket_rows = _valid_bucket(
                inline_payload,
                context,
                map_parameters,
                0,
                1,
            )
            if bucket_rows is None or len(bucket_rows) != int(entry["count"]):
                return None
            rows.extend(bucket_rows)
            non_null_targets = int(inline_payload["non_null_target_count"])
        else:
            for entry in entries:
                bucket_index = int(entry["index"])
                bucket_key = _bucket_key(context, map_parameters, bucket_index)
                cached = store.get(bucket_key)
                payload = cached.payload if cached is not None else None
                bucket_rows = _valid_bucket(
                    payload,
                    context,
                    map_parameters,
                    bucket_index,
                    bucket_count,
                )
                if bucket_rows is None or len(bucket_rows) != int(entry["count"]):
                    return None
                non_null_targets += int(payload["non_null_target_count"])
                rows.extend(bucket_rows)
        try:
            _assert_cache_fence(store, context, map_parameters, cache_fence)
        except IdentityMapGenerationMismatch:
            return None
        if len(rows) != int(manifest["signature_count"]):
            return None
        if non_null_targets != int(manifest["non_null_target_count"]):
            return None
        if _manifest_digest(entries) != str(manifest["signature_digest"]):
            return None
    return rows


def _resolve_distinct(
    environment_id: int,
    signatures: Iterable[Mapping[str, Any]],
    catalog: Any,
    *,
    identity_version_value: str | None,
) -> list[dict[str, Any]]:
    # The resolver is still the Assets catalog's shared semantic owner.  The
    # lazy import keeps catalog -> mapping re-exports acyclic during startup.
    from datacoolie_studio.domains.assets.identity_catalog import resolve_destination_signature

    version = identity_version_value or str(getattr(catalog, "version", ""))
    unique: dict[str, Mapping[str, Any]] = {}
    for value in signatures:
        signature = str(value.get("signature") or "") if isinstance(value, Mapping) else ""
        if not signature:
            signature = destination_signature(value)
        unique.setdefault(signature, value)
    rows: list[dict[str, Any]] = []
    for signature, value in sorted(unique.items()):
        rows.append(
            resolve_destination_signature(
                environment_id,
                value,
                catalog,
                signature=signature,
                identity_version_value=version,
            )
        )
    return rows


def _distinct_fact_signatures(conn: Any, source_ids: list[int]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    from datacoolie_studio.domains.analytics.serving_facts import MONITORING_DATAFLOW_FACTS_TABLE

    placeholders = ", ".join("?" for _ in source_ids)
    signature_sql = destination_signature_sql("d")
    query = f"""
        SELECT DISTINCT {signature_sql} AS signature
        FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
        WHERE d._source_id IN ({placeholders})
        ORDER BY signature
    """
    result = conn.execute(query, source_ids).fetchall()
    return [_signature_values(str(values[0])) for values in result]


def _signature_values(signature: str) -> dict[str, Any]:
    """Decode the canonical SQL signature without touching raw configure JSON."""

    try:
        payload = json.loads(signature)
    except (TypeError, ValueError):
        return {"signature": signature}
    if not isinstance(payload, Mapping):
        return {"signature": signature}

    values: dict[str, Any] = {"signature": signature}
    values["destination_configure_invalid"] = bool(payload.get("configure_invalid"))
    for column in DESTINATION_SIGNATURE_COLUMNS:
        if column == "destination_configure":
            continue
        payload_key = "source_id" if column == "_source_id" else column
        values[column] = payload.get(payload_key)
    binding = payload.get("configure_binding")
    if isinstance(binding, (list, tuple)):
        values["destination_configure"] = {
            field: binding[index] if index < len(binding) else None
            for index, field in enumerate(DESTINATION_CONFIGURE_IDENTITY_FIELDS)
        }
    else:
        values["destination_configure"] = {}
    return values


def _bucket_count(rows: list[Mapping[str, Any]]) -> int:
    if not rows:
        return 1
    count = 1
    while True:
        buckets = _partition_rows(rows, count)
        if max((_json_size(_bucket_payload_stub(index, count, values)) for index, values in buckets.items()), default=0) <= IDENTITY_MAP_PAYLOAD_TARGET_BYTES:
            break
        if count >= IDENTITY_MAP_MAX_BUCKET_COUNT:
            break
        count *= 2
    return min(count, IDENTITY_MAP_MAX_BUCKET_COUNT)


def _partition_rows(
    rows: Iterable[Mapping[str, Any]],
    bucket_count: int,
) -> dict[int, list[dict[str, Any]]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        signature = str(row.get("signature") or "")
        index = _bucket_index(signature, bucket_count)
        buckets.setdefault(index, []).append(dict(row))
    for values in buckets.values():
        values.sort(key=lambda row: str(row.get("signature") or ""))
    return buckets


def _bucket_index(signature: str, bucket_count: int) -> int:
    digest = hashlib.sha256(signature.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % max(1, int(bucket_count))


def _manifest_payload(
    context: IdentityMapContext,
    map_parameters: str,
    bucket_count: int,
    buckets: Mapping[int, list[dict[str, Any]]],
    *,
    inline_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = [
        {
            "index": index,
            "count": len(buckets.get(index, [])),
            "digest": _rows_digest(buckets.get(index, [])),
        }
        for index in range(bucket_count)
    ]
    payload = {
        "schema_version": _MANIFEST_SCHEMA,
        "environment_id": context.environment_id,
        "version": context.version,
        "map_parameters": map_parameters,
        "complete": True,
        "bucket_count": bucket_count,
        "signature_count": sum(len(buckets.get(index, [])) for index in range(bucket_count)),
        "non_null_target_count": sum(
            1
            for index in range(bucket_count)
            for row in buckets.get(index, [])
            if row.get("target") not in (None, "")
        ),
        "signature_digest": _manifest_digest(entries),
        "buckets": entries,
    }
    if inline_rows is not None:
        payload["inline_rows"] = [_cache_row(row) for row in inline_rows]
    return payload


def _bucket_payload(
    context: IdentityMapContext,
    bucket_index: int,
    bucket_count: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": _BUCKET_SCHEMA,
        "environment_id": context.environment_id,
        "version": context.version,
        "bucket_index": bucket_index,
        "bucket_count": bucket_count,
        "complete": True,
        "signature_count": len(rows),
        "non_null_target_count": sum(
            1 for row in rows if row.get("target") not in (None, "")
        ),
        "signature_digest": _rows_digest(rows),
        "rows": [_cache_row(row) for row in rows],
    }


def _bucket_payload_stub(
    bucket_index: int,
    bucket_count: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": _BUCKET_SCHEMA,
        "bucket_index": bucket_index,
        "bucket_count": bucket_count,
        "complete": True,
        "signature_count": len(rows),
        "non_null_target_count": sum(
            1 for row in rows if row.get("target") not in (None, "")
        ),
        "signature_digest": _rows_digest(rows),
        "rows": [_cache_row(row) for row in rows],
    }


def _valid_manifest(
    payload: Any,
    context: IdentityMapContext,
    map_parameters: str,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("schema_version") != _MANIFEST_SCHEMA or payload.get("complete") is not True:
        return False
    if int(payload.get("environment_id") or 0) != context.environment_id:
        return False
    if str(payload.get("version") or "") != context.version:
        return False
    if str(payload.get("map_parameters") or "") != map_parameters:
        return False
    try:
        bucket_count = int(payload.get("bucket_count"))
        signature_count = int(payload.get("signature_count"))
        non_null_target_count = int(payload.get("non_null_target_count"))
        buckets = payload.get("buckets")
    except (TypeError, ValueError):
        return False
    if (
        not 1 <= bucket_count <= IDENTITY_MAP_MAX_BUCKET_COUNT
        or signature_count < 0
        or not 0 <= non_null_target_count <= signature_count
    ):
        return False
    if not isinstance(buckets, list) or len(buckets) != bucket_count:
        return False
    indexes = []
    for entry in buckets:
        if not isinstance(entry, Mapping):
            return False
        try:
            index = int(entry.get("index"))
            count = int(entry.get("count"))
        except (TypeError, ValueError):
            return False
        if not 0 <= index < bucket_count or count < 0 or not entry.get("digest"):
            return False
        indexes.append(index)
    return sorted(indexes) == list(range(bucket_count)) and bool(payload.get("signature_digest"))


def _valid_bucket(
    payload: Any,
    context: IdentityMapContext,
    map_parameters: str,
    bucket_index: int,
    bucket_count: int,
) -> list[dict[str, Any]] | None:
    if not isinstance(payload, Mapping):
        return None
    if payload.get("schema_version") != _BUCKET_SCHEMA or payload.get("complete") is not True:
        return None
    if int(payload.get("environment_id") or 0) != context.environment_id:
        return None
    if str(payload.get("version") or "") != context.version:
        return None
    try:
        payload_bucket_index = int(payload.get("bucket_index"))
    except (TypeError, ValueError):
        return None
    if payload_bucket_index != bucket_index:
        return None
    try:
        payload_bucket_count = int(payload.get("bucket_count"))
        signature_count = int(payload.get("signature_count"))
    except (TypeError, ValueError):
        return None
    if payload_bucket_count != bucket_count:
        return None
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return None
    if signature_count != len(raw_rows):
        return None
    result: list[Any] = []
    compact_rows = True
    for row in raw_rows:
        if isinstance(row, list):
            if len(row) != len(IDENTITY_MAP_COLUMNS):
                return None
            result.append(row)
        elif isinstance(row, Mapping):
            compact_rows = False
            result.append(dict(row))
        else:
            return None
    try:
        non_null_target_count = int(payload.get("non_null_target_count"))
    except (TypeError, ValueError):
        return None
    signature_values: list[str] = []
    observed_non_null_targets = 0
    previous_signature = None
    signature_index = _IDENTITY_MAP_COLUMN_INDEX["signature"]
    target_index = _IDENTITY_MAP_COLUMN_INDEX["target"]
    signatures: set[str] = set()
    for row in result:
        if compact_rows:
            signature = str(row[signature_index] or "")
            target = row[target_index]
            # Cache writers emit rows in canonical signature order.  Checking
            # that order lets validation hash the already collected values
            # without allocating a second sorted list.
            if previous_signature is not None and signature < previous_signature:
                return None
            previous_signature = signature
        else:
            signature = str(_cached_row_value(row, "signature") or "")
            target = _cached_row_value(row, "target")
        if not signature or _bucket_index(signature, bucket_count) != bucket_index:
            return None
        if signature in signatures:
            return None
        signatures.add(signature)
        signature_values.append(signature)
        if target not in (None, ""):
            observed_non_null_targets += 1
    if non_null_target_count != observed_non_null_targets:
        return None
    if str(payload.get("signature_digest") or "") != _rows_digest_from_signatures(
        signature_values,
        assume_sorted=compact_rows,
    ):
        return None
    return result


def _rows_digest(rows: Iterable[Any]) -> str:
    signatures = sorted(str(_cached_row_value(row, "signature") or "") for row in rows)
    return _rows_digest_from_signatures(signatures, assume_sorted=True)


def _rows_digest_from_signatures(
    signatures: Iterable[str],
    *,
    assume_sorted: bool = False,
) -> str:
    values = signatures if isinstance(signatures, list) else list(signatures)
    if not assume_sorted:
        values.sort()
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode("utf-8")).hexdigest()


def _manifest_digest(entries: Iterable[Mapping[str, Any]]) -> str:
    compact = [
        {
            "index": int(entry.get("index") or 0),
            "count": int(entry.get("count") or 0),
            "digest": str(entry.get("digest") or ""),
        }
        for entry in entries
    ]
    return hashlib.sha256(
        json.dumps(compact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cache_row(row: Mapping[str, Any]) -> list[Any]:
    """Store JSON fields in their final SQL text form to avoid re-encoding."""

    cached: dict[str, Any] = {str(key): _json_safe(value) for key, value in row.items()}
    for column, empty in (
        ("identity_provenance", {}),
        ("identity_metadata_sources", []),
    ):
        value = cached.get(column)
        if isinstance(value, str):
            continue
        cached[column] = json.dumps(
            value if value not in (None, "") else empty,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return [_json_safe(cached.get(column)) for column in IDENTITY_MAP_COLUMNS]


def _cached_row_value(row: Any, column: str) -> Any:
    if isinstance(row, list):
        index = _IDENTITY_MAP_COLUMN_INDEX.get(column)
        if index is None or index >= len(row):
            return None
        return row[index]
    return row.get(column) if isinstance(row, Mapping) else None


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def _map_parameters(source_ids: Iterable[int], analytics_generation: Any) -> str:
    return fingerprint(
        {
            "scope": "admitted-destination-facts",
            "source_ids": sorted({int(source_id) for source_id in source_ids}),
            "analytics_generation": analytics_generation,
        }
    )


def _manifest_key(context: IdentityMapContext, map_parameters: str) -> ResultCacheKey:
    return _result_key(
        context,
        IDENTITY_MAP_NAMESPACE,
        map_parameters,
    )


def _bucket_key(
    context: IdentityMapContext,
    map_parameters: str,
    bucket_index: int,
) -> ResultCacheKey:
    return _result_key(
        context,
        f"{IDENTITY_MAP_BUCKET_NAMESPACE}.{bucket_index:02x}",
        fingerprint({"map_parameters": map_parameters, "bucket_index": bucket_index}),
    )


def _result_key(
    context: IdentityMapContext,
    namespace: str,
    parameters: str,
) -> ResultCacheKey:
    return ResultCacheKey(
        environment_id=context.environment_id,
        namespace=namespace,
        parameters_fingerprint=parameters,
        input_fingerprint=context.version,
        producer_version=IDENTITY_MAP_PROJECTOR_VERSION,
    )


def _build_key(context: IdentityMapContext, map_parameters: str) -> str:
    return ":".join(
        (
            str(context.environment_id),
            IDENTITY_MAP_NAMESPACE,
            map_parameters,
            context.version,
            IDENTITY_MAP_PROJECTOR_VERSION,
        )
    )


def _capture_cache_fence(
    context: IdentityMapContext,
    map_parameters: str,
) -> _CacheFence:
    store = result_cache_provider().store
    # Manifest and bucket keys intentionally share one result-cache family.
    # Reading that family generation once is equivalent to reading both keys
    # and avoids opening two cache-store sessions on every warm request.
    generation = store.generation(_manifest_key(context, map_parameters))
    return _CacheFence(
        manifest_generation=generation,
        family_generation=generation,
    )


def _assert_cache_fence(
    store: Any,
    context: IdentityMapContext,
    map_parameters: str,
    fence: _CacheFence,
) -> None:
    generation = store.generation(_manifest_key(context, map_parameters))
    if generation != fence.manifest_generation or generation != fence.family_generation:
        raise IdentityMapGenerationMismatch(
            "Identity map cache was cleared during map construction"
        )


def _analytics_fence(value: Any) -> tuple[Any, list[int]] | None:
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    conn = value[0]
    source_ids = value[1]
    if not hasattr(conn, "execute") or not isinstance(source_ids, (tuple, list)):
        return None
    if conn is None:
        return None
    return conn, sorted({int(source_id) for source_id in source_ids})


def _analytics_generation(value: Any) -> Any:
    return value[2] if isinstance(value, (tuple, list)) and len(value) > 2 else None


def _with_rows(context: IdentityMapContext, rows: Iterable[Any]) -> IdentityMapContext:
    normalized = tuple(
        row if isinstance(row, list) else dict(row)
        for row in rows
    )
    return IdentityMapContext(
        environment_id=context.environment_id,
        version=context.version,
        metadata_revision=context.metadata_revision,
        table_name=context.table_name,
        rows=normalized,
        catalog=context.catalog,
        _session=context._session,
    )


def _with_catalog(context: IdentityMapContext, catalog: Any) -> IdentityMapContext:
    return IdentityMapContext(
        environment_id=context.environment_id,
        version=context.version,
        metadata_revision=context.metadata_revision,
        table_name=context.table_name,
        rows=context.rows,
        catalog=catalog,
        _session=context._session,
    )


def _identity_revision(session: Session, environment_id: int) -> str:
    from datacoolie_studio.domains.assets.identity_catalog import identity_revision

    return identity_revision(session, environment_id)


def _identity_version(
    revision: str,
    environment_id: int,
    *,
    analytics_context: Any | None,
) -> str:
    from datacoolie_studio.domains.assets.identity_catalog import identity_version_from_inputs

    return identity_version_from_inputs(
        revision,
        environment_id,
        analytics_context=analytics_context,
    )


def _load_catalog(session: Session, environment_id: int, revision: str) -> Any:
    from datacoolie_studio.domains.assets.identity_catalog import load_or_build_identity_catalog

    catalog = load_or_build_identity_catalog(session, environment_id, revision=revision)
    if catalog.version != revision:
        raise IdentityMapRevisionMismatch("Published metadata changed while loading the identity catalog")
    return catalog


def _mapping_value(row: Any, column: str) -> Any:
    value = _cached_row_value(row, column)
    if column in {"identity_provenance", "identity_metadata_sources"}:
        if isinstance(value, str):
            return value
        return json.dumps(
            value or ({} if column == "identity_provenance" else []),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def _materialize_mapping_rows(rows: Iterable[Any]) -> list[tuple[Any, ...]]:
    """Normalize cold resolver rows while passing compact cache rows through."""

    materialized: list[tuple[Any, ...]] = []
    for row in rows:
        if isinstance(row, list):
            if len(row) != len(IDENTITY_MAP_COLUMNS):
                raise ValueError("Invalid compact destination identity row")
            materialized.append(tuple(row))
        else:
            materialized.append(
                tuple(_mapping_value(row, column) for column in IDENTITY_MAP_COLUMNS)
            )
    return materialized


def _sql_identifier(value: str) -> str:
    text = str(value or IDENTITY_MAP_TABLE)
    if not text.replace("_", "").isalnum() or text[0].isdigit():
        raise ValueError("SQL table name must be an internal identifier")
    return text


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "IDENTITY_MAP_BUCKET_NAMESPACE",
    "IDENTITY_MAP_COLUMNS",
    "IDENTITY_MAP_NAMESPACE",
    "IDENTITY_MAP_PROJECTOR_VERSION",
    "IDENTITY_MAP_TABLE",
    "IdentityMapContext",
    "IdentityMapGenerationMismatch",
    "IdentityMapRevisionMismatch",
    "build_identity_map",
    "create_identity_mapping_table",
    "identity_map_context",
]
