import json
from pathlib import Path


def test_prepare_does_not_publish_and_apply_is_idempotent(
    studio_paths,
    tmp_path: Path,
):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.service import (
        ensure_metadata_materialization,
        load_environment_editor_workspace,
    )
    from datacoolie_studio.domains.metadata.write_executor import (
        apply_environment_write,
        operation_response,
        prepare_environment_write,
    )
    from datacoolie_studio.domains.sources.persistence import create_source

    init_db()
    metadata_path = tmp_path / "metadata.json"
    original = {
        "connections": [{"name": "lake", "connection_type": "file"}],
        "dataflows": [],
        "schema_hints": [],
    }
    metadata_path.write_text(json.dumps(original), encoding="utf-8")
    session = create_session()
    project = Project(name="executor-project")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    source = create_source(
        session,
        environment.id,
        source_kind="metadata",
        uri=str(metadata_path),
        label="metadata",
        enabled=True,
    )
    session.commit()
    ensure_metadata_materialization(session, source, force=True)
    workspace = load_environment_editor_workspace(session, environment.id, [source])
    document = workspace["document"]
    document["sheets"]["connections"]["rows"][0]["name"] = "warehouse"
    expected = document["source"]["revision"]

    operation = prepare_environment_write(
        session,
        environment.id,
        [source],
        document,
        expected,
    )
    assert operation.state == "prepared"
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == original

    applied = apply_environment_write(
        session,
        operation.operation_key,
        environment_id=environment.id,
        confirm=True,
    )
    assert applied.state == "succeeded", json.dumps(
        operation_response(session, applied), default=str, indent=2
    )
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["connections"][0]["name"] == "warehouse"
    assert apply_environment_write(
        session,
        operation.operation_key,
        environment_id=environment.id,
        confirm=True,
    ).state == "succeeded"
    assert operation_response(session, operation)["summary"]["applied"] == 1
    session.close()


def test_prepare_io_counts_skip_noop_and_serialize_each_changed_source_once(
    studio_paths,
    tmp_path: Path,
    monkeypatch,
):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata import write_executor
    from datacoolie_studio.domains.metadata.service import (
        ensure_metadata_materialization,
        load_environment_editor_workspace,
    )
    from datacoolie_studio.domains.metadata.write_executor import (
        abandon_environment_write,
        prepare_environment_write,
    )
    from datacoolie_studio.domains.sources.persistence import create_source

    init_db()
    session = create_session()
    project = Project(name="prepare-io-counts-project")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()

    sources = []
    for index in range(3):
        path = tmp_path / f"source-{index}.json"
        path.write_text(
            json.dumps(
                {
                    "connections": [{"name": f"lake-{index}"}],
                    "dataflows": [],
                    "schema_hints": [],
                }
            ),
            encoding="utf-8",
        )
        sources.append(
            create_source(
                session,
                environment.id,
                source_kind="metadata",
                uri=str(path),
                label=f"source-{index}",
                enabled=True,
            )
        )
    session.commit()
    for source in sources:
        ensure_metadata_materialization(session, source, force=True)

    workspace = load_environment_editor_workspace(session, environment.id, sources)
    document = workspace["document"]
    expected_revision = document["source"]["revision"]
    original_read = write_executor.read_source_bytes
    original_serialize = write_executor.serialize_editor_document_bytes
    read_count = 0
    serialize_count = 0

    def counted_read(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        return original_read(*args, **kwargs)

    def counted_serialize(*args, **kwargs):
        nonlocal serialize_count
        serialize_count += 1
        return original_serialize(*args, **kwargs)

    monkeypatch.setattr(write_executor, "read_source_bytes", counted_read)
    monkeypatch.setattr(
        write_executor,
        "serialize_editor_document_bytes",
        counted_serialize,
    )

    noop = prepare_environment_write(
        session,
        environment.id,
        sources,
        document,
        expected_revision,
    )
    assert noop.state == "succeeded"
    assert (read_count, serialize_count) == (3, 0)
    read_count = 0

    changed_one = json.loads(json.dumps(document))
    changed_one["sheets"]["connections"]["rows"][0]["description"] = "changed"
    one_source = prepare_environment_write(
        session,
        environment.id,
        sources,
        changed_one,
        expected_revision,
    )
    assert one_source.state == "prepared"
    assert (read_count, serialize_count) == (3, 1)
    abandon_environment_write(
        session,
        one_source.operation_key,
        environment_id=environment.id,
    )

    read_count = 0
    serialize_count = 0
    changed_two = json.loads(json.dumps(document))
    changed_two["sheets"]["connections"]["rows"][0]["description"] = "changed-0"
    changed_two["sheets"]["connections"]["rows"][1]["description"] = "changed-1"
    two_sources = prepare_environment_write(
        session,
        environment.id,
        sources,
        changed_two,
        expected_revision,
    )
    assert two_sources.state == "prepared"
    assert (read_count, serialize_count) == (3, 2)
    session.close()


def test_prepare_new_routed_source_is_source_free_until_apply(
    studio_paths,
    tmp_path: Path,
):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.service import (
        ensure_metadata_materialization,
        load_environment_editor_workspace,
    )
    from datacoolie_studio.domains.metadata.write_executor import (
        apply_environment_write,
        operation_response,
        prepare_environment_write,
    )
    from datacoolie_studio.domains.sources.persistence import create_source

    init_db()
    root = tmp_path / "metadata"
    root.mkdir()
    existing_path = root / "existing.json"
    existing_path.write_text(
        json.dumps({"connections": [], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    session = create_session()
    project = Project(name="new-source-project")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    source = create_source(
        session,
        environment.id,
        source_kind="metadata",
        uri=str(existing_path),
        label="existing",
        enabled=True,
    )
    session.commit()
    ensure_metadata_materialization(session, source, force=True)
    document = load_environment_editor_workspace(session, environment.id, [source])["document"]
    document["sheets"]["connections"]["rows"].append(
        {
            "name": "new-lake",
            "connection_type": "file",
            "__metadata_source_name": "new.json",
        }
    )

    operation = prepare_environment_write(
        session,
        environment.id,
        [source],
        document,
        document["source"]["revision"],
    )
    assert operation.state == "prepared"
    new_path = root / "new.json"
    assert not new_path.exists()
    assert len(
        [
            item
            for item in session.query(type(source)).filter_by(environment_id=environment.id)
            if item.uri == str(new_path)
        ]
    ) == 0

    applied = apply_environment_write(
        session,
        operation.operation_key,
        environment_id=environment.id,
        confirm=True,
    )
    assert applied.state == "succeeded", json.dumps(
        operation_response(session, applied), default=str, indent=2
    )
    assert new_path.exists()
    assert any(
        item.uri == str(new_path)
        for item in session.query(type(source)).filter_by(environment_id=environment.id)
    )
    session.close()


def test_partial_apply_retains_draft_and_retries_only_unresolved_items(
    studio_paths,
    tmp_path: Path,
    monkeypatch,
):
    from datacoolie_studio.db.models import Environment, Project
    from datacoolie_studio.db.session import create_session, init_db
    from datacoolie_studio.domains.metadata.drafts import (
        latest_environment_draft,
        save_environment_editor_draft,
    )
    from datacoolie_studio.domains.metadata.service import (
        ensure_metadata_materialization,
        load_environment_editor_workspace,
    )
    from datacoolie_studio.domains.metadata import write_executor
    from datacoolie_studio.domains.metadata.write_executor import (
        apply_environment_write,
        prepare_environment_write,
    )
    from datacoolie_studio.domains.sources.persistence import create_source

    init_db()
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(
        json.dumps({"connections": [{"name": "first"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps({"connections": [{"name": "second"}], "dataflows": [], "schema_hints": []}),
        encoding="utf-8",
    )

    session = create_session()
    project = Project(name="partial-apply-project")
    session.add(project)
    session.flush()
    environment = Environment(project_id=project.id, name="dev")
    session.add(environment)
    session.flush()
    first = create_source(
        session,
        environment.id,
        source_kind="metadata",
        uri=str(first_path),
        label="first",
        enabled=True,
    )
    second = create_source(
        session,
        environment.id,
        source_kind="metadata",
        uri=str(second_path),
        label="second",
        enabled=True,
    )
    session.commit()
    ensure_metadata_materialization(session, first, force=True)
    ensure_metadata_materialization(session, second, force=True)
    workspace = load_environment_editor_workspace(
        session,
        environment.id,
        [first, second],
    )
    document = workspace["document"]
    for row in document["sheets"]["connections"]["rows"]:
        row["name"] = f"{row['name']}-edited"
    expected_revision = document["source"]["revision"]
    save_environment_editor_draft(
        session,
        environment.id,
        document,
        expected_revision,
        sources=[first, second],
    )

    operation = prepare_environment_write(
        session,
        environment.id,
        [first, second],
        document,
        expected_revision,
    )
    original_replace = write_executor.conditional_replace_uri
    replace_calls: list[str] = []

    def fail_on_second_replace(*args, **kwargs):
        replace_calls.append(str(args[1]))
        if len(replace_calls) == 2:
            raise RuntimeError("injected second-source failure")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(write_executor, "conditional_replace_uri", fail_on_second_replace)
    partial = apply_environment_write(
        session,
        operation.operation_key,
        environment_id=environment.id,
        confirm=True,
    )
    assert partial.state == "partial"
    assert len(replace_calls) == 2
    assert "first-edited" in first_path.read_text(encoding="utf-8")
    assert "second-edited" not in second_path.read_text(encoding="utf-8")
    assert latest_environment_draft(session, environment.id) is not None

    monkeypatch.setattr(write_executor, "conditional_replace_uri", original_replace)
    succeeded = apply_environment_write(
        session,
        operation.operation_key,
        environment_id=environment.id,
        confirm=True,
    )
    assert succeeded.state == "succeeded"
    assert len(replace_calls) == 2
    assert "second-edited" in second_path.read_text(encoding="utf-8")
    assert latest_environment_draft(session, environment.id) is None
    session.close()
