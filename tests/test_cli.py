# SPDX-License-Identifier: MIT

"""
Tests for the command line entry point.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from megingjord import cli


def test_get_config_path_default(monkeypatch) -> None:
    """
    Without --config, the XDG config path is used.
    """
    monkeypatch.setattr(cli, "xdg_config_home", lambda: Path("/tmp/xdg"))
    assert cli.get_config_path(None) == Path("/tmp/xdg/megingjord/config.yaml")


def test_get_config_path_override() -> None:
    """
    --config overrides the XDG path.
    """
    assert cli.get_config_path("foo.yaml") == Path("foo.yaml")


def test_run_missing_config(tmp_path, monkeypatch, capsys) -> None:
    """
    A missing configuration file is a usage error.
    """
    monkeypatch.setattr(
        "sys.argv",
        ["megingjord", "--config", str(tmp_path / "nope.yaml")],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli.run()
    assert excinfo.value.code == 2
    assert "Configuration file not found" in capsys.readouterr().err


def test_run_invalid_yaml(tmp_path, monkeypatch, capsys) -> None:
    """
    Invalid YAML is a usage error.
    """
    path = tmp_path / "config.yaml"
    path.write_text("streamdeck: [unclosed", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["megingjord", "--config", str(path)])
    with pytest.raises(SystemExit) as excinfo:
        cli.run()
    assert excinfo.value.code == 2
    assert "Invalid YAML" in capsys.readouterr().err


def test_run_unknown_section(tmp_path, monkeypatch, capsys) -> None:
    """
    An unknown section is a usage error.
    """
    path = tmp_path / "config.yaml"
    path.write_text("bogus:\n  foo: bar\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["megingjord", "--config", str(path)])
    with pytest.raises(SystemExit) as excinfo:
        cli.run()
    assert excinfo.value.code == 2
    assert "Unknown configuration section" in capsys.readouterr().err


def test_run_setup(tmp_path, monkeypatch) -> None:
    """
    A valid config is passed to main as a setup callable.
    """
    path = tmp_path / "config.yaml"
    path.write_text(
        "streamdeck:\n  dials:\n    1:\n      type: brightness\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["megingjord", "--config", str(path)])
    calls = []

    def fake_main(setup):
        calls.append(setup)

    monkeypatch.setattr(cli, "main", fake_main)
    cli.run()
    assert len(calls) == 1

    app = web.Application()
    controller = MagicMock()
    controller.deck = MagicMock()
    app["deck_controller"] = controller
    calls[0](app)
    assert app["color_theme"] == "default"
