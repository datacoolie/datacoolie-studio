"""Canonical sheet and field definitions for metadata editor documents."""

from __future__ import annotations


CONNECTION_COLUMNS = [
    "connection_id",
    "name",
    "description",
    "connection_type",
    "format",
    "catalog",
    "database",
    "configure",
    "secrets_ref",
    "is_active",
]

DATAFLOW_COLUMNS = [
    "dataflow_id",
    "name",
    "description",
    "stage",
    "group_number",
    "execution_order",
    "processing_mode",
    "is_active",
    "configure",
    "source_connection_name",
    "source_schema_name",
    "source_table",
    "source_query",
    "source_python_function",
    "source_watermark_columns",
    "source_filter_expression",
    "source_configure",
    "destination_connection_name",
    "destination_schema_name",
    "destination_table",
    "destination_load_type",
    "destination_merge_keys",
    "destination_partition_columns",
    "destination_configure",
    "transform_deduplicate_columns",
    "transform_latest_data_columns",
    "transform_filter_expression",
    "transform_additional_columns",
    "transform_schema_hints",
    "transform_select_columns",
    "transform_drop_columns",
    "transform_rename_columns",
    "transform_value_rules",
    "transform_hash_columns",
    "transform_masking_rules",
    "transform_configure",
]

SCHEMA_HINT_COLUMNS = [
    "connection_name",
    "connection_id",
    "table_name",
    "schema_name",
    "column_name",
    "data_type",
    "format",
    "precision",
    "scale",
    "default_value",
    "ordinal_position",
    "is_active",
]

REQUIRED_SHEETS = ("connections", "dataflows", "schema_hints")

FILE_RUNTIME_FIELDS = {
    "connections": {"connection_id"},
    "dataflows": {"dataflow_id"},
    "schema_hints": set(),
}

VISIBLE_STUDIO_ROUTING_COLUMN = "__metadata_source_name"
