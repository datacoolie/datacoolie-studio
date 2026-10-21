"""Native schema mapping and bounded evidence serialization for execution logs."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from datacoolie_studio.domains.analytics.schema_compiler import (
    MappingNode, SCHEMA_DOCUMENT, compile_mapping, mapping_document, contract_fingerprint,
    numeric_range_mapping,
)
from datacoolie_studio.domains.analytics.sql import quote_identifier


Kind = Literal["dataflow", "job"]
_KIND_ALIASES = {
    "dataflow": "dataflow",
    "dataflow_run_log": "dataflow",
    "job": "job",
    "job_run_log": "job",
}
_MISSING = object()


def _root_mapping(kind: Kind) -> dict[str, MappingNode]:
    return compile_mapping(SCHEMA_DOCUMENT, {"dataflow": "dataflow_run_log", "job": "job_run_log"}[kind])


ROOT_MAPPINGS: dict[Kind, dict[str, MappingNode]] = {
    "dataflow": _root_mapping("dataflow"),
    "job": _root_mapping("job"),
}

NUMERIC_RANGE_MAPPINGS = {kind: numeric_range_mapping(mapping) for kind, mapping in ROOT_MAPPINGS.items()}


def _normalise_kind(kind: str | None, record: Mapping[str, Any] | None = None) -> Kind:
    value = kind
    if value is None and record is not None:
        value = record.get("_type")
    try:
        return _KIND_ALIASES[str(value)]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError(f"Unsupported v6 execution-log kind: {value!r}") from exc


def root_column_types(kind: str) -> dict[str, str]:
    """Return the complete root-column DDL mapping for ``kind``.

    The returned dictionary is a copy.  Callers may add technical columns such
    as ``_record_line`` without mutating this canonical mapping.
    """

    canonical = _normalise_kind(kind)
    return {name: node.duckdb_type for name, node in ROOT_MAPPINGS[canonical].items()}


def _escape_pointer_component(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _join_path(parent: str, component: str | int) -> str:
    return f"{parent}/{_escape_pointer_component(str(component))}"


def _integer_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("JSON booleans are not integer attributes")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"Expected an integer, got {value!r}")
        # Direct callers may use the standard JSON decoder. Preserve the
        # supplied numeric value; source-byte precision belongs to admission.
        return str(int(value))
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError(f"Expected an integer, got {value!r}")
        return str(int(value))
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal integer {value!r}") from exc
        if not parsed.is_finite() or parsed != parsed.to_integral_value():
            raise ValueError(f"Expected an integer, got {value!r}")
        return str(int(parsed))
    raise TypeError(f"Expected an integer, got {type(value).__name__}")


def _encode_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"Non-finite Decimal is not valid JSON: {value!r}")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Non-finite float is not valid JSON: {value!r}")
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, Mapping):
        members: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be strings, got {type(key).__name__}")
            members.append(
                f"{json.dumps(key, ensure_ascii=False, separators=(',', ':'))}:{_encode_json(item)}"
            )
        return "{" + ",".join(members) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode_json(item) for item in value) + "]"
    raise TypeError(f"Value is not JSON serializable without loss: {type(value).__name__}")


def record_json(record: Mapping[str, Any]) -> str:
    """Serialize an admitted record with Decimal values as JSON numbers.

    ``json.dumps(..., default=str)`` is intentionally not used: it quotes
    Decimal values and changes their JSON kind.  This compact serializer keeps
    arbitrary integer and fractional Decimal values as numeric tokens.
    """

    if not isinstance(record, Mapping):
        raise TypeError("record_json expects a mapping")
    return _encode_json(record)


def _encode_node(node: MappingNode, value: Any) -> Any:
    if value is _MISSING:
        # A missing JSON leaf must remain SQL NULL.  An explicit JSON null is
        # handled by the following branch as the text ``null``.
        return None
    if value is None:
        # SQL NULL represents an absent optional field or a null fixed parent.
        # Dynamic JSON leaves keep JSON null text; fixed native values use NULL.
        return None if node.category != "json" else "null"
    if node.category == "integer":
        return _integer_text(value)
    if node.category == "json":
        return _encode_json(value)
    if node.category == "number":
        native = float(value)
        if not math.isfinite(native):
            raise ValueError(f"Non-finite native DOUBLE for {node.name!r}")
        return native
    if node.category in {"string", "boolean"}:
        return value
    if node.category == "struct":
        if not isinstance(value, Mapping):
            raise TypeError(f"Expected object for {node.name!r}, got {type(value).__name__}")
        return {
            child.name: _encode_node(
                child,
                value[child.name] if child.name in value else _MISSING,
            )
            for child in node.children
        }
    if node.category == "list":
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"Expected array for {node.name!r}, got {type(value).__name__}")
        if node.item is None:
            raise AssertionError(f"List node {node.name!r} has no item mapping")
        return [_encode_node(node.item, item) for item in value]
    raise AssertionError(f"Unknown mapping category {node.category!r}")


def encoded_record(record: Mapping[str, Any], kind: str | None = None) -> dict[str, Any]:
    """Encode root values for explicit typed SQL insertion.

    The result contains every mapped root field, with absent fields represented
    as ``None``. Integer binding uses decimal strings;
    dynamic JSON leaves are exact JSON text.  ``insert_record`` supplies the
    corresponding SQL casts.
    """

    canonical = _normalise_kind(kind, record)
    mapping = ROOT_MAPPINGS[canonical]
    encoded = {
        name: _encode_node(node, record[name] if name in record else _MISSING)
        for name, node in mapping.items()
    }
    return encoded


def _encoded_record_with_mapping(
    record: Mapping[str, Any],
    canonical: Kind,
    mapping: Mapping[str, MappingNode],
) -> dict[str, Any]:
    return {
        name: _encode_node(node, record[name] if name in record else _MISSING)
        for name, node in mapping.items()
    }


def _decode_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8")
    if not isinstance(value, str):
        return value

    def _invalid_constant(token: str) -> Any:
        raise ValueError(f"Non-finite JSON number {token!r}")

    return json.loads(
        value,
        parse_int=int,
        parse_float=Decimal,
        parse_constant=_invalid_constant,
    )


def _decode_integer(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("DuckDB returned a boolean for an integer column")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError(f"DuckDB returned a fractional integer: {value!r}")
        return int(value)
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"DuckDB returned an invalid integer: {value!r}") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ValueError(f"DuckDB returned an invalid integer: {value!r}")
    return int(parsed)


def _decode_node(node: MappingNode, value: Any, path: str, present: set[str]) -> Any:
    if value is None:
        return None
    if node.category == "json":
        return _decode_json(value)
    if node.category == "integer":
        return _decode_integer(value)
    if node.category == "number":
        return float(value)
    if node.category in {"string", "boolean"}:
        return value
    if node.category == "struct":
        if not isinstance(value, Mapping):
            raise TypeError(f"DuckDB returned a non-struct for {node.name!r}: {value!r}")
        result: dict[str, Any] = {}
        for child in node.children:
            child_path = _join_path(path, child.name)
            child_value = value.get(child.name)
            if child.name in value:
                result[child.name] = _decode_node(child, child_value, child_path, present)
        return result
    if node.category == "list":
        if node.item is None:
            raise AssertionError(f"List node {node.name!r} has no item mapping")
        return [
            _decode_node(node.item, item, _join_path(path, index), present)
            for index, item in enumerate(value)
        ]
    raise AssertionError(f"Unknown mapping category {node.category!r}")


def _row_mapping(row: Mapping[str, Any] | Sequence[Any], columns: Sequence[str] | None) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if hasattr(row, "_asdict"):
        return row._asdict()  # type: ignore[no-any-return]
    if columns is None:
        raise TypeError("A sequence row requires its column names")
    if len(row) != len(columns):
        raise ValueError(f"Row has {len(row)} values but {len(columns)} column names were supplied")
    return dict(zip(columns, row))


def reconstruct_record(
    row: Mapping[str, Any] | Sequence[Any],
    kind: str,
    *,
    columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Reconstruct one producer record from a typed row.

    ``row`` may be a mapping (the preferred bounded-fetch representation), a
    named tuple, or a tuple with explicit ``columns``.  Technical columns are
    ignored.  Dynamic JSON leaves are parsed with ``Decimal`` for fractional
    numbers, and native integer values become Python integers.
    """

    canonical = _normalise_kind(kind)
    values = _row_mapping(row, columns)
    mapping = ROOT_MAPPINGS[canonical]
    present: set[str] = set()
    result: dict[str, Any] = {}
    for name, node in mapping.items():
        value = values.get(name)
        path = _join_path("", name)
        if name in values:
            result[name] = _decode_node(node, value, path, present)
    return result


def _table_description(conn: Any, table: str) -> dict[str, str]:
    try:
        rows = conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()
    except Exception:
        return {}
    return {str(row[0]): str(row[1]) for row in rows}


def _sql_cast(type_name: str) -> str:
    return type_name


def _quote_relation(relation: str) -> str:
    """Quote a simple or qualified relation, accepting existing quoting."""

    if relation.startswith('"') and relation.endswith('"'):
        return relation
    return ".".join(quote_identifier(part) for part in relation.split("."))


def _insert_layout(
    conn: Any,
    table: str,
    canonical: Kind,
    metadata_keys: Sequence[str],
) -> tuple[str, tuple[str, ...], dict[str, str], Mapping[str, MappingNode]]:
    """Compile one explicit INSERT layout for a typed target table."""

    description = _table_description(conn, table)
    mapping = ROOT_MAPPINGS[canonical]
    available = set(description)
    root_names = list(mapping)
    if description:
        missing = [name for name in root_names if name not in available]
        if missing:
            raise ValueError(f"Typed table {table!r} is missing root columns: {missing[:8]}")

    columns: list[str] = []
    expressions: list[str] = []
    for name in root_names:
        columns.append(name)
        expressions.append(f"CAST(? AS {_sql_cast(mapping[name].duckdb_type)})")

    for name in metadata_keys:
        if name in columns:
            raise ValueError(f"Duplicate typed-record column {name!r}")
        if description and name not in available:
            raise ValueError(f"Typed table {table!r} has no metadata column {name!r}")
        columns.append(name)
        expressions.append(
            f"CAST(? AS {_sql_cast(description.get(name, 'VARCHAR'))})"
        )

    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    sql = (
        f"INSERT INTO {_quote_relation(table)} ({quoted_columns}) "
        f"SELECT {', '.join(expressions)}"
    )
    return sql, tuple(columns), description, mapping


def _metadata_parameter(value: Any, type_name: str) -> Any:
    # DuckDB applies Python integer binding before SQL CAST.  Apply the same
    # decimal-text rule to technical BIGNUM columns if a caller supplies one.
    if value is not None and type_name.upper().startswith("BIGNUM"):
        return _integer_text(value)
    return value


def _insert_parameters(
    encoded: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    *,
    canonical: Kind,
    metadata_keys: Sequence[str],
    description: Mapping[str, str],
    mapping: Mapping[str, MappingNode],
) -> list[Any]:
    parameters = [encoded[name] for name in mapping]
    for name in metadata_keys:
        value = (metadata or {}).get(name)
        parameters.append(_metadata_parameter(value, description.get(name, "VARCHAR")))
    return parameters


def insert_record(
    conn: Any,
    table: str,
    record: Mapping[str, Any],
    kind: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Insert one record into an explicitly typed DuckDB table.

    ``metadata`` may contain technical columns such as ``filename`` and
    ``__record_line``.  Root values are always inserted with explicit casts;
    integer values are bound as decimal text. File ingestion uses native_loading
    instead of these helpers, which serve existing direct-row callers.
    """

    canonical = _normalise_kind(kind, record)
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("insert_record metadata must be a mapping")
    metadata_keys = tuple((metadata or {}).keys())
    sql, _columns, description, mapping = _insert_layout(
        conn,
        table,
        canonical,
        metadata_keys,
    )
    parameters = _insert_parameters(
        _encoded_record_with_mapping(record, canonical, mapping),
        metadata,
        canonical=canonical,
        metadata_keys=metadata_keys,
        description=description,
        mapping=mapping,
    )
    conn.execute(sql, parameters)


def insert_records(
    conn: Any,
    table: str,
    records: Iterable[Mapping[str, Any]],
    kind: str | None = None,
    metadata: Mapping[str, Any] | Sequence[Mapping[str, Any] | None] | Any | None = None,
) -> int:
    """Insert a sequence using the same explicit per-record codec contract.

    ``metadata`` can be one mapping reused for every row, an aligned sequence,
    or a callable ``metadata(record, index)``.  Returning the inserted count
    makes bounded staging callers easy to account for.
    """

    rows = list(records)
    if not rows:
        return 0
    if metadata is None:
        metadata_values = [None for _ in rows]
    elif isinstance(metadata, Mapping):
        metadata_values = [metadata for _ in rows]
    elif callable(metadata):
        metadata_values = [metadata(record, index) for index, record in enumerate(rows)]
    else:
        metadata_values = list(metadata)
        if len(metadata_values) != len(rows):
            raise ValueError("Metadata sequence length must match records")

    if any(value is not None and not isinstance(value, Mapping) for value in metadata_values):
        raise TypeError("Each insert_records metadata value must be a mapping or None")

    canonical_rows = [_normalise_kind(kind, record) for record in rows]
    metadata_key_rows = [tuple((value or {}).keys()) for value in metadata_values]
    if (
        len(set(canonical_rows)) != 1
        or len(set(metadata_key_rows)) != 1
    ):
        # Mixed kinds or changing metadata columns need separate layouts.
        for record, row_metadata, row_kind in zip(rows, metadata_values, canonical_rows):
            insert_record(conn, table, record, kind=row_kind, metadata=row_metadata)
        return len(rows)

    canonical = canonical_rows[0]
    metadata_keys = metadata_key_rows[0]
    _sql, columns, description, mapping = _insert_layout(
        conn,
        table,
        canonical,
        metadata_keys,
    )
    parameter_rows = [
        _insert_parameters(
            _encoded_record_with_mapping(record, canonical, mapping),
            row_metadata,
            canonical=canonical,
            metadata_keys=metadata_keys,
            description=description,
            mapping=mapping,
            )
        for record, row_metadata in zip(rows, metadata_values)
    ]
    # Bind one bounded vector per column. Each cast still uses the canonical
    # nested type (including decimal-text integer leaves), while avoiding one
    # Python/SQL binding cycle per record for the wide root schema.
    types = {name: node.duckdb_type for name, node in mapping.items()}
    types.update(description)
    expressions = ", ".join(
        f"unnest(CAST(? AS {types.get(column, 'VARCHAR')}[]))"
        for column in columns
    )
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    conn.execute(
        f"INSERT INTO {_quote_relation(table)} ({column_sql}) SELECT {expressions}",
        [[values[index] for values in parameter_rows] for index in range(len(columns))],
    )
    return len(rows)


__all__ = [
    "MappingNode",
    "ROOT_MAPPINGS",
    "NUMERIC_RANGE_MAPPINGS",
    "encoded_record",
    "insert_record",
    "insert_records",
    "reconstruct_record",
    "record_json",
    "root_column_types",
    "mapping_document",
    "contract_fingerprint",
]
