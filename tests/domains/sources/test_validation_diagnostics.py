from __future__ import annotations

import logging
from types import SimpleNamespace

from datacoolie_studio.domains.code_artifacts import service as code_artifacts
from datacoolie_studio.domains.sources import service


class _ValidationSession:
    def commit(self) -> None:
        pass

    def refresh(self, _source) -> None:
        pass


def _source(source_kind: str, *, provider: str = "s3") -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        source_kind=source_kind,
        uri="s3://bucket/private/source",
        storage_provider=provider,
        storage_auth_mode="ambient",
        credential_profile_id=None,
        storage_config_json=None,
        source_config_json=None,
        read_check_status=None,
        read_checked_at=None,
        read_check_result_json=None,
    )


def _raise_unexpected(*_args, **_kwargs):
    raise RuntimeError("secret=do-not-log path=C:\\private\\source")


def test_unexpected_metadata_validation_is_safe_and_diagnosable(monkeypatch, caplog) -> None:
    source = _source("metadata")
    monkeypatch.setattr(service, "create_storage_adapter", _raise_unexpected)

    with caplog.at_level(
        logging.WARNING,
        logger="datacoolie_studio.domains.sources.validation_diagnostics",
    ):
        result = service.validate_metadata_source(_ValidationSession(), source)

    assert result["message"] == "Metadata object is not readable"
    assert result["errors"] == [
        {
            "code": "source_validation_internal_error",
            "message": "Metadata object is not readable",
        }
    ]
    assert "RuntimeError" in caplog.text
    assert "secret=do-not-log" not in caplog.text
    assert "C:\\private\\source" not in caplog.text
    assert "secret=do-not-log" not in str(result)


def test_unexpected_log_validation_is_safe_and_diagnosable(monkeypatch, caplog) -> None:
    source = _source("logs")
    monkeypatch.setattr(service, "create_storage_adapter", _raise_unexpected)

    with caplog.at_level(
        logging.WARNING,
        logger="datacoolie_studio.domains.sources.validation_diagnostics",
    ):
        result = service.validate_log_source(_ValidationSession(), source)

    assert result["message"] == "Log storage is not accessible"
    assert result["errors"] == [
        {
            "code": "source_validation_internal_error",
            "message": "Log storage is not accessible",
        }
    ]
    assert "RuntimeError" in caplog.text
    assert "secret=do-not-log" not in caplog.text
    assert "C:\\private\\source" not in str(result)


def test_unexpected_code_validation_is_safe_and_diagnosable(monkeypatch, caplog) -> None:
    source = _source("code", provider="local")
    monkeypatch.setattr(code_artifacts, "_code_validation_statistics", _raise_unexpected)

    with caplog.at_level(
        logging.WARNING,
        logger="datacoolie_studio.domains.sources.validation_diagnostics",
    ):
        result = code_artifacts.validate_code_artifact(_ValidationSession(), source)

    assert result["message"] == "Code artifact validation failed"
    assert result["errors"] == [
        {
            "code": "source_validation_internal_error",
            "message": "Code artifact validation failed",
        }
    ]
    assert "RuntimeError" in caplog.text
    assert "secret=do-not-log" not in caplog.text
    assert "C:\\private\\source" not in str(result)


def test_unexpected_storage_probe_is_safe_and_diagnosable(monkeypatch, caplog) -> None:
    binding = SimpleNamespace(provider="s3")
    monkeypatch.setattr(
        service,
        "validate_and_normalize_binding",
        lambda *_args, **_kwargs: ("s3://bucket/private/source", binding),
    )
    monkeypatch.setattr(service, "create_storage_adapter", _raise_unexpected)

    with caplog.at_level(
        logging.WARNING,
        logger="datacoolie_studio.domains.sources.validation_diagnostics",
    ):
        result = service.validate_storage_connection(
            _ValidationSession(),
            uri="s3://bucket/private/source",
            storage={"provider": "s3"},
            source_config={},
            secret_store=None,
        )

    assert result == {
        "status": "error",
        "provider": "s3",
        "canonical_uri": None,
        "message": "Storage validation failed",
        "error": {
            "code": "storage_validation_internal_error",
            "message": "Storage validation failed",
        },
    }
    assert "RuntimeError" in caplog.text
    assert "secret=do-not-log" not in caplog.text
    assert "C:\\private\\source" not in str(result)
