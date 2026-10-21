from __future__ import annotations

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.sql import quote_identifier


def table_source_row_count(conn, table_name: str, source_id: int) -> int:
    if (
        not schema.table_exists(conn, table_name)
        or "_source_id" not in schema.table_columns(conn, table_name)
    ):
        return 0
    return int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} WHERE _source_id = ?",
            [source_id],
        ).fetchone()[0]
        or 0
    )


def delete_rows_by_source_ids(
    conn,
    table_name: str,
    source_ids: list[int],
    *,
    source_column: str = "_source_id",
) -> int:
    if not source_ids or not schema.table_exists(conn, table_name):
        return 0
    if source_column not in schema.table_columns(conn, table_name):
        return 0
    quoted_source_column = quote_identifier(source_column)
    placeholders = ", ".join("?" for _ in source_ids)
    row_count = int(
        conn.execute(
            f"SELECT count(*) FROM {table_name} "
            f"WHERE {quoted_source_column} IN ({placeholders})",
            source_ids,
        ).fetchone()[0]
    )
    if row_count:
        conn.execute(
            f"DELETE FROM {table_name} "
            f"WHERE {quoted_source_column} IN ({placeholders})",
            source_ids,
        )
    return row_count
