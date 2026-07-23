from __future__ import annotations


def lakehouse_destination_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"""(
      CASE WHEN LOWER(TRIM(COALESCE({prefix}destination_connection_type, ''))) NOT IN ('', 'unknown', 'none', 'null', 'n/a')
             OR LOWER(TRIM(COALESCE({prefix}destination_format, ''))) NOT IN ('', 'unknown', 'none', 'null', 'n/a')
        THEN regexp_matches(LOWER(CONCAT_WS(' ', {prefix}destination_connection_type, {prefix}destination_format)), 'lakehouse|delta|iceberg|onelake')
        ELSE regexp_matches(LOWER(CONCAT_WS(' ', {prefix}destination_name, {prefix}destination_path)), 'lakehouse|delta|iceberg|onelake')
      END
    )"""


def estimated_rows_written_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"""CASE
      WHEN {lakehouse_destination_sql(alias)} THEN COALESCE({prefix}destination_rows_written, 0)
      WHEN {prefix}normalized_status = 'succeeded' THEN COALESCE(NULLIF({prefix}source_rows_read, 0), {prefix}destination_rows_written, 0)
      ELSE COALESCE({prefix}destination_rows_written, 0)
    END"""
