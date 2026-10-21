from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest


pytestmark = pytest.mark.benchmark


@pytest.fixture
def benchmark_module(script_module_loader):
    return script_module_loader(
        "scripts/benchmarks/databricks_storage.py",
        "databricks_storage_benchmark",
    )


def test_invalid_run_count_fails_before_adapter_construction(monkeypatch, benchmark_module):
    monkeypatch.setattr(
        benchmark_module,
        "_adapter",
        lambda _profile: pytest.fail("adapter must not be constructed"),
    )

    with pytest.raises(SystemExit):
        benchmark_module.main(
            [
                "--directory-uri",
                "dbfs:/Volumes/catalog/schema/volume",
                "--file-uri",
                "dbfs:/Volumes/catalog/schema/volume/file.json",
                "--runs",
                "1",
            ]
        )


def test_benchmark_measures_read_only_list_stat_and_read(monkeypatch, capsys, benchmark_module):
    calls: list[tuple[str, str]] = []

    class FakeAdapter:
        def stat(self, uri):
            calls.append(("stat", uri))
            return {"size": 7}

        @contextmanager
        def open_read(self, uri):
            calls.append(("read", uri))
            yield io.BytesIO(b"payload")

    def fake_inventory(_adapter, request):
        calls.append(("list", request.uri))
        return []

    monkeypatch.setattr(benchmark_module, "_adapter", lambda _profile: FakeAdapter())
    monkeypatch.setattr(benchmark_module, "inventory", fake_inventory)
    monkeypatch.setattr(benchmark_module, "storage_diagnostics", lambda _adapter: {"read": 2})

    result = benchmark_module.main(
        [
            "--directory-uri",
            "dbfs:/Volumes/catalog/schema/volume",
            "--file-uri",
            "dbfs:/Volumes/catalog/schema/volume/file.json",
            "--runs",
            "2",
        ]
    )

    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["runs"] == 2
    assert [kind for kind, _uri in calls] == [
        "list",
        "stat",
        "read",
        "list",
        "stat",
        "read",
    ]
