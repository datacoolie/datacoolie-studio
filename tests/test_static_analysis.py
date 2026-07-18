from __future__ import annotations


def test_sql_analysis_excludes_ctes_and_keeps_physical_tables():
    from datacoolie_studio.domains.analysis.sql import analyze_sql

    result = analyze_sql(
        """
        WITH recent AS (
            SELECT * FROM raw.orders
        )
        SELECT *
        FROM recent r
        JOIN analytics.customers c ON c.id = r.customer_id
        """
    )

    assert sorted(item.value for item in result.inputs) == [
        "analytics.customers",
        "raw.orders",
    ]
    assert result.diagnostics == []


def test_sql_analysis_keeps_repeated_reference_detections_with_ranges():
    from datacoolie_studio.domains.analysis.sql import analyze_sql

    result = analyze_sql("SELECT * FROM silver.orders first_order JOIN silver.orders second_order ON 1 = 1")

    assert [item.value for item in result.inputs] == ["silver.orders", "silver.orders"]
    assert [item.location.column for item in result.inputs] == [14, 45]
    assert all(item.location and item.location.coordinate_space == "query_source" for item in result.inputs)


def test_python_analysis_resolves_context_fstring():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine, source):
    catalog = source.configure.get("catalog")
    schema = source.connection.database
    query = f"SELECT * FROM {catalog}.{schema}.orders"
    return engine.execute_sql(query)
"""
    result = analyze_python_function(
        source,
        "demo.read_orders",
        context={
            "source": {
                "configure": {"catalog": "lake"},
                "connection": {"database": "curated"},
            }
        },
    )

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "table"
    assert result.inputs[0].value == "lake.curated.orders"
    assert result.inputs[0].provenance == "python_sql"


def test_python_analysis_keeps_repeated_table_detections_with_function_ranges():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    result = analyze_python_function(
        """
def read_orders(spark):
    first = spark.read.table(\"silver.orders\")
    second = spark.read.table(\"silver.orders\")
    return first, second
""",
        "demo.read_orders",
        module_name="demo",
    )

    assert [item.value for item in result.inputs] == ["silver.orders", "silver.orders"]
    assert [item.location.line for item in result.inputs] == [2, 3]
    assert all(item.location and item.location.function_path == "demo.read_orders" for item in result.inputs)
    assert all(item.location and item.location.coordinate_space == "function_source" for item in result.inputs)


def test_python_analysis_locations_match_the_extracted_function_source():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    result = analyze_python_function(
        """


def read_orders(spark):
    return spark.read.table("silver.orders")
""",
        "demo.read_orders",
    )

    assert result.inputs[0].location.line == 2


def test_python_analysis_resolves_execute_sql_with_concatenated_query():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    query = (
        "SELECT * "
        "FROM raw.orders o "
        "JOIN curated.customers c ON c.id = o.customer_id"
    )
    return engine.execute_sql(query)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert sorted(item.value for item in result.inputs) == ["curated.customers", "raw.orders"]
    assert all(item.provenance == "python_sql" for item in result.inputs)


def test_python_analysis_resolves_spark_sql_with_concatenated_query():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    spark = engine.spark
    query = (
        "SELECT * "
        "FROM silver.orders"
    )
    return spark.sql(query)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert [item.value for item in result.inputs] == ["silver.orders"]
    assert result.inputs[0].provenance == "python_sql"


def test_python_analysis_resolves_spark_sql_with_session_alias():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    session = engine.spark
    query = (
        "SELECT * "
        "FROM silver.orders"
    )
    return session.sql(query)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert [item.value for item in result.inputs] == ["silver.orders"]
    assert result.inputs[0].provenance == "python_sql"


def test_python_analysis_resolves_sql_context_execute_with_concatenated_query():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    pl_sql_context = engine.sql_context
    query = (
        "SELECT * "
        "FROM gold.daily_sales"
    )
    return pl_sql_context.execute(query)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert [item.value for item in result.inputs] == ["gold.daily_sales"]
    assert result.inputs[0].provenance == "python_sql"


def test_python_analysis_resolves_sql_context_execute_with_alias():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    ctx = engine.sql_context
    executor = ctx
    query = (
        "SELECT * "
        "FROM gold.daily_sales"
    )
    return executor.execute(query)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert [item.value for item in result.inputs] == ["gold.daily_sales"]
    assert result.inputs[0].provenance == "python_sql"


def test_python_analysis_maps_temp_view_to_loaded_path():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    path = "s3://warehouse/delta/orders"
    engine.spark.read.format("delta").load(path).createOrReplaceTempView("orders")
    return engine.execute_sql("SELECT * FROM orders")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "s3://warehouse/delta/orders"
    assert result.inputs[0].details == {"temp_view": "orders"}
    assert result.temp_views["orders"].value == "s3://warehouse/delta/orders"


def test_python_analysis_maps_temp_view_from_csv_chain_to_loaded_path():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(engine):
    engine.spark.read.option("header", "true").csv("s3://warehouse/raw/orders.csv").createOrReplaceTempView("orders")
    return engine.execute_sql("SELECT * FROM orders")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "s3://warehouse/raw/orders.csv"
    assert result.temp_views["orders"].value == "s3://warehouse/raw/orders.csv"


def test_python_analysis_ignores_create_dataframe_and_reports_dynamic_sql():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def build_rows(engine, query):
    local = engine.create_dataframe([{"id": 1}])
    engine.execute_sql(query)
    return local
"""
    result = analyze_python_function(source, "demo.build_rows")

    assert result.inputs == []
    assert [item["code"] for item in result.diagnostics] == ["dynamic_sql"]


def test_python_analysis_follows_same_module_helper_with_depth_bound():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def _read(engine):
    return engine.execute_sql("SELECT * FROM raw.orders")

def entrypoint(engine):
    return _read(engine)
"""
    result = analyze_python_function(source, "demo.entrypoint")

    assert [item.value for item in result.inputs] == ["raw.orders"]


def test_python_analysis_detects_spark_read_table_reference():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(spark):
    return spark.read.table("lake.curated.orders")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "table"
    assert result.inputs[0].value == "lake.curated.orders"
    assert result.inputs[0].table == "orders"
    assert result.inputs[0].schema_name == "curated"
    assert result.inputs[0].catalog == "lake"


def test_python_analysis_detects_spark_read_csv_path():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(spark):
    return spark.read.csv("s3://warehouse/raw/orders.csv")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "s3://warehouse/raw/orders.csv"


def test_python_analysis_detects_spark_read_csv_path_with_reader_alias_chain():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(spark):
    reader = spark.read
    configured = reader.option("header", "true").option("inferSchema", "true")
    return configured.csv("s3://warehouse/raw/orders.csv")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "s3://warehouse/raw/orders.csv"


def test_python_analysis_detects_spark_load_path_keyword_argument():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(spark):
    return spark.read.format("delta").load(path="abfss://lake/raw/orders")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "abfss://lake/raw/orders"


def test_python_analysis_detects_polars_scan_csv_path():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
import polars as pl

def read_orders():
    return pl.scan_csv("abfss://lake/raw/orders.csv")
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert len(result.inputs) == 1
    assert result.inputs[0].kind == "path"
    assert result.inputs[0].value == "abfss://lake/raw/orders.csv"


def test_python_analysis_reports_dynamic_table_expression():
    from datacoolie_studio.domains.analysis.python import analyze_python_function

    source = """
def read_orders(spark, table_name):
    return spark.read.table(table_name)
"""
    result = analyze_python_function(source, "demo.read_orders")

    assert result.inputs == []
    assert [item["code"] for item in result.diagnostics] == ["dynamic_table"]


def test_artifact_analysis_resolves_dotted_function_without_import(tmp_path):
    from datacoolie_studio.db.models import EnvironmentSource
    from datacoolie_studio.domains.analysis.service import analyze_code_artifact_function

    package = tmp_path / "src" / "functions"
    package.mkdir(parents=True)
    marker = tmp_path / "imported.txt"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text(
        f"""
from pathlib import Path
Path({str(marker)!r}).write_text("imported")

def read_orders(engine):
    return engine.execute_sql("SELECT * FROM raw.orders")
""",
        encoding="utf-8",
    )
    artifact = EnvironmentSource(
        environment_id=1,
        source_kind="code",
        uri=str(tmp_path),
        enabled=True,
        source_config_json='{"artifact_type": "directory", "module_roots": ["src"]}',
    )

    result = analyze_code_artifact_function(artifact, "functions.sources.read_orders")

    assert marker.exists() is False
    assert [item.value for item in result.inputs] == ["raw.orders"]
    assert result.inputs[0].location.module == "functions.sources"
    assert result.inputs[0].location.path == "src/functions/sources.py"
