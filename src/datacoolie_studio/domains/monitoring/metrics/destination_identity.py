"""Monitoring declaration/contributor grain; object identity belongs to Assets."""


def destination_declared_sql(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return f"COALESCE(NULLIF(TRIM({p}destination_id), ''), NULLIF(TRIM({p}destination_name), ''), NULLIF(TRIM({p}destination_path), ''), NULLIF(TRIM({p}destination_full_table), ''), NULLIF(TRIM({p}destination_table), '')) IS NOT NULL"


def contributing_dataflow_key_sql(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    # No trustworthy native producer binding currently exists for cross-source
    # contributor IDs; source remains provenance for unresolved producers.
    return f"CAST(json_array({p}_source_id, {p}workspace_id, {p}dataflow_id) AS VARCHAR)"
