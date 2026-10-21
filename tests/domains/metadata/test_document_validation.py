from datacoolie_studio.domains.metadata.document_rewrites import (
    synchronize_connection_name_references,
)



def _document(*, connection_name: str = "lake") -> dict:
    return {
        "sheets": {
            "connections": {"rows": [{"name": connection_name, "connection_id": "c1"}]},
            "dataflows": {
                "rows": [
                    {
                        "name": "orders",
                        "dataflow_id": "d1",
                        "source_connection_name": connection_name,
                        "destination_table": "orders_curated",
                    }
                ]
            },
            "schema_hints": {
                "rows": [
                    {
                        "connection_name": connection_name,
                        "table_name": "orders",
                        "column_name": "id",
                    }
                ]
            },
        }
    }


def test_connection_reference_rewrite_is_pure_and_identity_based() -> None:
    baseline = _document()
    renamed = _document(connection_name="lake_curated")

    updated = synchronize_connection_name_references(renamed, baseline)

    assert updated["sheets"]["dataflows"]["rows"][0]["source_connection_name"] == "lake_curated"
    assert updated["sheets"]["schema_hints"]["rows"][0]["connection_name"] == "lake_curated"
    assert renamed["sheets"]["dataflows"]["rows"][0]["source_connection_name"] == "lake_curated"
