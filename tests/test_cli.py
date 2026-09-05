from __future__ import annotations

import io
import os
import sys
from types import SimpleNamespace

import pytest

from datacoolie_studio import __version__, cli
from datacoolie_studio import startup_banner
from datacoolie_studio.startup_banner import (
    DATACOOLIE_WORDMARK,
    WORDMARK_WIDTH,
    build_application_url,
    format_startup_banner,
    terminal_supports_wordmark,
)


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    (
        ("127.0.0.1", 8765, "http://127.0.0.1:8765"),
        ("0.0.0.0", 9000, "http://127.0.0.1:9000"),
        ("::", 8765, "http://[::1]:8765"),
        ("::1", 8765, "http://[::1]:8765"),
        ("[::1]", 8765, "http://[::1]:8765"),
        ("studio.local", 8765, "http://studio.local:8765"),
        ("127.0.0.1", 0, None),
    ),
)
def test_build_application_url(host: str, port: int, expected: str | None):
    assert build_application_url(host, port) == expected


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_support_requires_a_tty_and_enough_width(monkeypatch):
    assert terminal_supports_wordmark(_TtyStream(), terminal_width=WORDMARK_WIDTH)
    assert not terminal_supports_wordmark(_TtyStream(), terminal_width=WORDMARK_WIDTH - 1)
    assert not terminal_supports_wordmark(io.StringIO(), terminal_width=WORDMARK_WIDTH + 10)
    monkeypatch.setattr(startup_banner.shutil, "get_terminal_size", lambda fallback: SimpleNamespace(columns=0))
    assert not terminal_supports_wordmark(_TtyStream())


def test_format_startup_banner_has_full_and_compact_layouts():
    full = format_startup_banner(__version__, "http://127.0.0.1:8765", use_wordmark=True)
    compact = format_startup_banner(__version__, "http://127.0.0.1:8765", use_wordmark=False)

    assert DATACOOLIE_WORDMARK in full
    assert f"Studio v{__version__}" in full
    assert f"DataCoolie Studio v{__version__}" in compact
    assert DATACOOLIE_WORDMARK not in compact
    for output in (full, compact):
        assert "https://github.com/datacoolie/datacoolie" in output
        assert "https://github.com/datacoolie/datacoolie-studio" in output
        assert "Press Ctrl+C to stop." in output
        assert "\x1b" not in output


def test_main_prints_compact_banner_before_starting_uvicorn(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["datacoolie-studio", "--no-open"])
    monkeypatch.setattr(cli, "terminal_supports_wordmark", lambda stream: False)
    observed_output: list[str] = []

    def fake_run(*args, **kwargs):
        observed_output.append(capsys.readouterr().out)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert f"DataCoolie Studio v{__version__}" in observed_output[0]
    assert "https://github.com/datacoolie/datacoolie" in observed_output[0]


def test_no_banner_does_not_disable_server_start(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["datacoolie-studio", "--no-banner", "--no-open"])
    started: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: started.append((args, kwargs)))

    cli.main()

    assert started
    assert capsys.readouterr().out == ""


def test_browser_uses_display_url_and_port_zero_does_not_open(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["datacoolie-studio", "--host", "0.0.0.0"])
    monkeypatch.setattr(cli, "terminal_supports_wordmark", lambda stream: False)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    scheduled: list[tuple[float, object]] = []
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    class _Timer:
        def __init__(self, interval: float, function):
            scheduled.append((interval, function))

        def start(self):
            return None

    monkeypatch.setattr(cli.threading, "Timer", _Timer)
    cli.main()

    assert len(scheduled) == 1
    scheduled[0][1]()
    assert opened == ["http://127.0.0.1:8765"]
    monkeypatch.setattr(sys, "argv", ["datacoolie-studio", "--port", "0"])
    scheduled.clear()

    cli.main()

    assert scheduled == []


def test_storage_arguments_keep_configuring_the_existing_environment(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["datacoolie-studio", "--database-url", "sqlite:///workspace.db", "--no-banner", "--no-open"],
    )
    monkeypatch.delenv("DATACOOLIE_STUDIO_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATACOOLIE_STUDIO_DB", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)

    cli.main()

    assert os.environ["DATACOOLIE_STUDIO_DATABASE_URL"] == "sqlite:///workspace.db"
    assert "DATACOOLIE_STUDIO_DB" not in os.environ


def test_help_does_not_print_banner_or_start_server(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["datacoolie-studio", "--help"])
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: pytest.fail("server should not start"))

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--no-banner" in output
    assert "DataCoolie Studio v" not in output
