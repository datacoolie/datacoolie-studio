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
