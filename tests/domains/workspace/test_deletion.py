from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, func, inspect, select, text

from datacoolie_studio.db import session as db_session
from datacoolie_studio.db.models import (
    CodeArtifactMaterialization,
    Environment,
    EnvironmentMetadataEditorDraft,
    EnvironmentSource,
    LogFileManifest,
    LogStreamState,
    MetadataBackup,
    MetadataEditorDraft,
    MetadataMaterialization,
    MetadataSaveEvent,
    MetadataValidationResult,
    Project,
    SourceObservation,
    SourceRegistration,
    SyncJob,
)
from datacoolie_studio.domains.workspace import service as workspace


def test_environment_delete_uses_foreign_key_cascade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    db_session.init_db()
    engine = db_session.get_engine()

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    index_names = {
        item["name"]
        for table_name in (
            "environments",
            "environment_sources",
            "sync_jobs",
            "metadata_editor_drafts",
            "metadata_validation_results",
            "metadata_save_events",
            "metadata_backups",
        )
        for item in inspect(engine).get_indexes(table_name)
    }
    assert {
        "ix_environments_project_id",
        "ix_environment_sources_environment_id",
        "ix_sync_jobs_environment_id",
        "ix_metadata_editor_drafts_source_id",
        "ix_metadata_validation_results_source_id",
        "ix_metadata_save_events_source_id",
        "ix_metadata_backups_environment_id",
        "ix_metadata_backups_source_id",
    } <= index_names

    with db_session.create_session() as session:
        project = Project(name="cascade")
        session.add(project)
        session.flush()
        environment = Environment(project_id=project.id, name="dev")
        session.add(environment)
        session.flush()
        registration = SourceRegistration(
            environment_id=environment.id,
            purpose="metadata",
            input_uri="metadata.json",
            canonical_uri="metadata.json",
            identity_key="identity",
        )
        session.add(registration)
        session.flush()
        source = EnvironmentSource(
            environment_id=environment.id,
            registration_id=registration.id,
            source_kind="metadata",
            uri="metadata.json",
        )
        session.add(source)
        session.flush()
        session.add_all(
            [
                SourceObservation(source_id=source.id),
                SyncJob(
                    environment_id=environment.id,
                    source_id=source.id,
                    source_kind="metadata",
                    job_type="manual_refresh",
                    status="succeeded",
                ),
                LogFileManifest(
                    source_id=source.id,
                    file_uri="log.jsonl",
                    file_kind="job_jsonl",
                    revision_json="{}",
                    status="ok",
                ),
                LogStreamState(
                    source_id=source.id,
                    stream_kind="job",
                    root_uri="logs",
                    layout_status="ready",
                ),
                MetadataMaterialization(
                    source_id=source.id,
                    source_revision_json="{}",
                    normalizer_version="test",
                    materialization_fingerprint="m" * 64,
                    editor_document_json="{}",
                    normalized_metadata_json="{}",
                ),
                CodeArtifactMaterialization(
                    source_id=source.id,
                    source_revision_json="{}",
                    materialization_fingerprint="c" * 64,
                    artifact_manifest_json="{}",
                    module_index_json="{}",
                    diagnostics_json="[]",
                    analyzer_version="test",
                ),
                MetadataEditorDraft(
                    source_id=source.id,
                    base_revision_json="{}",
                    editor_document_json="{}",
                ),
                EnvironmentMetadataEditorDraft(
                    environment_id=environment.id,
                    base_revision_json="{}",
                    editor_document_json="{}",
                ),
                MetadataValidationResult(
                    source_id=source.id,
                    status="valid",
                    result_json="{}",
                ),
                MetadataSaveEvent(source_id=source.id, status="saved"),
                MetadataBackup(
                    project_id=project.id,
                    environment_id=environment.id,
                    source_id=source.id,
                    source_uri="metadata.json",
                    backup_path="backup.json",
                ),
            ]
        )
        session.commit()

        assert workspace.delete_environment(session, environment.id) is True
        assert session.get(Project, project.id) is not None
        for model in (
            Environment,
            EnvironmentSource,
            SourceRegistration,
            SourceObservation,
            SyncJob,
            LogFileManifest,
            LogStreamState,
            MetadataMaterialization,
            CodeArtifactMaterialization,
            MetadataEditorDraft,
            EnvironmentMetadataEditorDraft,
            MetadataValidationResult,
            MetadataSaveEvent,
            MetadataBackup,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_project_delete_statement_count_is_bounded_by_database_cascade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    db_session.init_db()
    engine = db_session.get_engine()

    with db_session.create_session() as session:
        project = Project(name="bounded-delete")
        session.add(project)
        session.flush()
        session.add_all(
            [Environment(project_id=project.id, name=f"env-{index}") for index in range(100)]
        )
        session.commit()
        project_id = project.id
        session.expunge_all()

        monkeypatch.setattr(workspace, "invalidate_project_read_models", lambda *_args, **_kwargs: None)
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(statement.strip().upper())

        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            assert workspace.delete_project(session, project_id) is True
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)

        assert len(statements) <= 4
        assert sum(statement.startswith("DELETE FROM PROJECTS") for statement in statements) == 1
        assert session.scalar(select(func.count()).select_from(Environment)) == 0
