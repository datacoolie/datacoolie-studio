from __future__ import annotations


def lakehouse_destination_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"""(
      regexp_matches(LOWER(CONCAT_WS(' ', {prefix}destination_connection_type, {prefix}destination_format)), 'lakehouse|delta|iceberg|onelake')
    )"""


def measured_rows_written_sql(alias: str = "") -> str:
    """Return the measured destination row metric."""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}destination_rows_written"
