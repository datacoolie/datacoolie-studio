from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Base, Environment, EnvironmentSource, Project, ProjectReferenceMapping
from datacoolie_studio.domains.workspace.service import list_project_summaries
from datacoolie_studio.domains.assets import service as assets_service


def test_project_summaries_use_one_query_and_preserve_empty_projects() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        empty = Project(name="alpha")
        populated = Project(name="beta", description="Project with sources")
        session.add_all([empty, populated])
        session.flush()
        dev = Environment(project_id=populated.id, name="dev")
        prod = Environment(project_id=populated.id, name="prod")
        session.add_all([dev, prod])
        session.flush()
        session.add_all(
            [
                EnvironmentSource(environment_id=dev.id, source_kind="metadata", uri="metadata-a.json"),
                EnvironmentSource(environment_id=dev.id, source_kind="metadata", uri="metadata-b.json"),
                EnvironmentSource(environment_id=dev.id, source_kind="logs", uri="logs"),
                EnvironmentSource(environment_id=prod.id, source_kind="code", uri="src"),
            ]
        )
        session.add_all(
            [
                ProjectReferenceMapping(
                    project_id=populated.id,
                    reference_type="table_reference",
                    reference_normalized_value=f"raw.table_{index}",
                    reference_signature_json="{}",
                    target_identifier_kind="logical_table",
                    target_normalized_value=f"curated.table_{index}",
                    target_display_value=f"curated.table_{index}",
                )
                for index in range(2)
            ]
        )
        session.commit()

        statement_count = 0

        def count_statement(*_args: object) -> None:
            nonlocal statement_count
            statement_count += 1

        event.listen(engine, "before_cursor_execute", count_statement)
        try:
            summaries = list_project_summaries(session)
        finally:
            event.remove(engine, "before_cursor_execute", count_statement)

    engine.dispose()

    assert statement_count == 1
    assert [summary["name"] for summary in summaries] == ["alpha", "beta"]
    assert summaries[0]["environment_count"] == 0
    assert summaries[0]["environments"] == []
    assert summaries[1]["environment_count"] == 2
    assert summaries[1]["metadata_source_count"] == 2
    assert summaries[1]["etl_log_path_count"] == 1
    assert summaries[0]["reference_mapping_count"] == 0
    assert summaries[1]["reference_mapping_count"] == 2
    assert [environment["name"] for environment in summaries[1]["environments"]] == ["dev", "prod"]
    assert summaries[1]["environments"][0]["code_artifact_count"] == 0
    assert summaries[1]["environments"][1]["code_artifact_count"] == 1


def test_project_create_normalizes_and_rejects_invalid_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        created = client.post("/api/v1/projects", json={"name": "  demo  "})
        assert created.status_code == 200
        assert created.json()["name"] == "demo"
        registry = client.get(f"/api/v1/projects/{created.json()['id']}/reference-registry")
        assert registry.status_code == 200
        assert registry.json() == {
            "project_id": created.json()["id"],
            "mappings": [],
            "rows": [],
            "targets": [],
            "failures": [],
        }

        blank = client.post("/api/v1/projects", json={"name": "   "})
        assert blank.status_code == 422

        too_long = client.post("/api/v1/projects", json={"name": "x" * 256})
        assert too_long.status_code == 422

        duplicate = client.post("/api/v1/projects", json={"name": "demo"})
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "Project already exists: demo"

        after_conflict = client.post("/api/v1/projects", json={"name": "next"})
        assert after_conflict.status_code == 200

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_project_and_environment_rename_preserve_identity_and_validate_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "alpha", "description": "Stable project"},
        ).json()
        other_project = client.post("/api/v1/projects", json={"name": "beta"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        other_environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "prod"},
        ).json()

        renamed_project = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"name": "  sales analytics  "},
        )
        assert renamed_project.status_code == 200
        assert renamed_project.json()["id"] == project["id"]
        assert renamed_project.json()["name"] == "sales analytics"
        assert renamed_project.json()["description"] == "Stable project"

        idempotent_project = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"name": "sales analytics"},
        )
        assert idempotent_project.status_code == 200
        assert idempotent_project.json()["id"] == project["id"]

        project_conflict = client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"name": other_project["name"]},
        )
        assert project_conflict.status_code == 409
        assert project_conflict.json()["detail"] == "Project already exists: beta"
        assert client.patch(
            f"/api/v1/projects/{project['id']}",
            json={"name": "   "},
        ).status_code == 422
        assert client.patch(
            "/api/v1/projects/999999",
            json={"name": "missing"},
        ).status_code == 404

        renamed_environment = client.patch(
            f"/api/v1/environments/{environment['id']}",
            json={"name": "  UAT_1  "},
        )
        assert renamed_environment.status_code == 200
        assert renamed_environment.json()["id"] == environment["id"]
        assert renamed_environment.json()["project_id"] == project["id"]
        assert renamed_environment.json()["name"] == "UAT_1"

        idempotent_environment = client.patch(
            f"/api/v1/environments/{environment['id']}",
            json={"name": "UAT_1"},
        )
        assert idempotent_environment.status_code == 200
        assert idempotent_environment.json()["id"] == environment["id"]

        environment_conflict = client.patch(
            f"/api/v1/environments/{environment['id']}",
            json={"name": other_environment["name"].upper()},
        )
        assert environment_conflict.status_code == 409
        assert environment_conflict.json()["detail"] == "Environment already exists: PROD"
        assert client.patch(
            f"/api/v1/environments/{environment['id']}",
            json={"name": "uat environment"},
        ).status_code == 422
        assert client.patch(
            "/api/v1/environments/999999",
            json={"name": "uat"},
        ).status_code == 404

        environments = client.get(
            f"/api/v1/projects/{project['id']}/environments"
        ).json()
        renamed_from_list = next(
            item for item in environments if item["id"] == environment["id"]
        )
        assert renamed_from_list["name"] == "UAT_1"
        assert renamed_from_list["project_id"] == project["id"]

    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)


def test_project_reference_registry_loads_each_catalog_once_and_isolates_failures(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(name="registry")
        session.add(project)
        session.flush()
        dev = Environment(project_id=project.id, name="dev")
        prod = Environment(project_id=project.id, name="prod")
        session.add_all([prod, dev])
        session.flush()
        session.add(ProjectReferenceMapping(
            project_id=project.id,
            reference_type="table_reference",
            reference_normalized_value="raw.orders",
            reference_signature_json="{}",
            target_identifier_kind="logical_table",
            target_normalized_value="curated.orders",
            target_display_value="curated.orders",
        ))
        session.commit()

        catalog_calls: list[int] = []

        def load_catalog(_session: Session, environment_id: int, **_kwargs: object) -> assets_service.AssetsCatalog:
            catalog_calls.append(environment_id)
            if environment_id == prod.id:
                raise RuntimeError("catalog unavailable")
            return assets_service.AssetsCatalog(
                payload={"assets": [], "reference_groups": []},
                input_fingerprint=f"catalog-{environment_id}",
            )

        monkeypatch.setattr(assets_service, "load_or_build_assets_catalog", load_catalog)
        registry = assets_service.list_project_reference_registry(session, project.id)

    engine.dispose()

    assert catalog_calls == [dev.id, prod.id]
    assert registry["project_id"] == project.id
    assert len(registry["mappings"]) == 1
    assert registry["targets"] == []
    assert len(registry["rows"]) == 1
    assert registry["rows"][0]["normalized_value"] == "raw.orders"
    assert registry["rows"][0]["resolution"] == {"state": "unresolved", "reason": "target_missing"}
    assert registry["rows"][0]["environments"] == []
    assert registry["failures"] == [{
        "environment_id": prod.id,
        "environment_name": "prod",
        "message": "catalog unavailable",
    }]


def test_project_reference_registry_returns_404_for_missing_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "registry.db"))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/projects/999/reference-registry")

    assert response.status_code == 404
    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)
