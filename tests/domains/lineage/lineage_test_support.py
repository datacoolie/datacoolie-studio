from __future__ import annotations

def _metadata(name: str, source_table: str, destination_table: str) -> dict:
    return {
        "connections": [
            {"name": "lake", "connection_type": "file", "format": "parquet", "configure": {"base_path": "./data"}}
        ],
        "dataflows": [
            {
                "name": name,
                "stage": "demo",
                "source": {"connection_name": "lake", "table": source_table},
                "destination": {"connection_name": "lake", "table": destination_table, "load_type": "append"},
                "transform": {},
            }
        ],
    }

def _metadata_with_connection(name: str, connection_name: str, base_path: str, source_table: str, destination_table: str) -> dict:
    return {
        "connections": [{
            "name": connection_name,
            "connection_type": "file",
            "format": "parquet",
            "configure": {"base_path": base_path},
        }],
        "dataflows": [{
            "name": name,
            "source": {"connection_name": connection_name, "table": source_table},
            "destination": {"connection_name": connection_name, "table": destination_table},
        }],
    }

def _api_metadata(
    name: str,
    connection_name: str,
    base_url: str,
    endpoint: str,
    destination_table: str,
    *,
    connection_config: dict | None = None,
    source_config: dict | None = None,
) -> dict:
    return {
        "connections": [{
            "name": connection_name,
            "connection_type": "api",
            "format": "api",
            "configure": {"base_url": base_url, **(connection_config or {})},
        }, {
            "name": "lake",
            "connection_type": "file",
            "format": "parquet",
            "configure": {"base_path": "./data"},
        }],
        "dataflows": [{
            "name": name,
            "source": {
                "connection_name": connection_name,
                "table": "orders",
                "configure": {"endpoint": endpoint, **(source_config or {})},
            },
            "destination": {"connection_name": "lake", "table": destination_table},
        }],
    }

def _catalog_metadata(name: str, catalog: str, base_path: str, table: str) -> dict:
    return {
        "connections": [{
            "name": catalog,
            "connection_type": "lakehouse",
            "format": "iceberg",
            "catalog": catalog,
            "database": "default",
            "configure": {"base_path": base_path},
        }],
        "dataflows": [{
            "name": name,
            "source": {"connection_name": catalog, "table": f"{catalog}_{table}_input"},
            "destination": {"connection_name": catalog, "table": table},
        }],
    }

def _ambiguous_orders_metadata() -> dict:
    return {
        "connections": [{
            "name": "lake",
            "connection_type": "lakehouse",
            "format": "delta",
            "catalog": "main",
            "database": "warehouse",
        }],
        "dataflows": [
            {
                "name": "seed_sales",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                "destination": {"connection_name": "lake", "schema_name": "sales", "table": "orders"},
            },
            {
                "name": "seed_archive",
                "source": {"connection_name": "lake", "schema_name": "raw", "table": "seed"},
                "destination": {"connection_name": "lake", "schema_name": "archive", "table": "orders"},
            },
            {
                "name": "query_orders",
                "source": {"connection_name": "lake", "query": "SELECT * FROM orders"},
                "destination": {"connection_name": "lake", "schema_name": "curated", "table": "orders"},
            },
        ],
    }

def _schema_table_suffix_metadata(*, include_second_lakehouse: bool) -> dict:
    connections = [{
        "name": "lh1",
        "connection_type": "lakehouse",
        "format": "delta",
        "catalog": "main",
        "database": "lh1",
    }]
    if include_second_lakehouse:
        connections.append({
            "name": "lh2",
            "connection_type": "lakehouse",
            "format": "delta",
            "catalog": "main",
            "database": "lh2",
        })
    dataflows = [
        {
            "name": "seed_lh1_customer",
            "source": {"connection_name": "lh1", "schema_name": "raw", "table": "seed"},
            "destination": {"connection_name": "lh1", "schema_name": "silver", "table": "customer"},
        },
        {
            "name": "query_customer",
            "source": {"connection_name": "lh1", "query": "SELECT * FROM silver.customer"},
            "destination": {"connection_name": "lh1", "schema_name": "gold", "table": "customer"},
        },
    ]
    if include_second_lakehouse:
        dataflows.insert(1, {
            "name": "seed_lh2_customer",
            "source": {"connection_name": "lh2", "schema_name": "raw", "table": "seed"},
            "destination": {"connection_name": "lh2", "schema_name": "silver", "table": "customer"},
        })
    return {"connections": connections, "dataflows": dataflows}

def _path_backed_schema_table_metadata() -> dict:
    return {
        "connections": [{
            "name": "fabric_lakehouse",
            "connection_type": "lakehouse",
            "format": "delta",
            "configure": {"base_path": "Tables"},
        }],
        "dataflows": [
            {
                "name": "seed_salesorderheader",
                "source": {"connection_name": "fabric_lakehouse", "schema_name": "bronze", "table": "saleslt_salesorderheader"},
                "destination": {"connection_name": "fabric_lakehouse", "schema_name": "silver", "table": "saleslt_salesorderheader"},
            },
            {
                "name": "query_salesorderheader",
                "source": {"connection_name": "fabric_lakehouse", "query": "SELECT * FROM silver.saleslt_salesorderheader"},
                "destination": {"connection_name": "fabric_lakehouse", "schema_name": "gold", "table": "fact_sales"},
            },
        ],
    }

def _database_reference_with_file_candidate(*, include_database_target: bool) -> dict:
    dataflows = [
        {
            "name": "seed_bronze_stateprovinces",
            "source": {"connection_name": "bronze_parquet", "schema_name": "Application", "table": "StateProvinces"},
            "destination": {"connection_name": "bronze_parquet", "schema_name": "Application", "table": "StateProvinces"},
        },
        {
            "name": "query_stateprovinces",
            "source": {"connection_name": "wwi_sqlserver", "query": "SELECT * FROM Application.StateProvinces"},
            "destination": {"connection_name": "wwi_sqlserver", "schema_name": "stage", "table": "StateProvinces"},
        },
    ]
    if include_database_target:
        dataflows.insert(1, {
            "name": "declare_wwi_stateprovinces",
            "source": {"connection_name": "wwi_sqlserver", "database": "WideWorldImporters", "schema_name": "Application", "table": "StateProvinces"},
            "destination": {"connection_name": "wwi_sqlserver", "database": "WideWorldImporters", "schema_name": "Application", "table": "StateProvinces"},
        })
    return {
        "connections": [
            {
                "name": "wwi_sqlserver",
                "connection_type": "database",
                "database": "WideWorldImporters",
                "configure": {"database_type": "mssql", "connection_instance": "sqlserver:wwi"},
            },
            {
                "name": "bronze_parquet",
                "connection_type": "file",
                "format": "parquet",
                "configure": {"base_path": "data/bronze"},
            },
        ],
        "dataflows": dataflows,
    }
