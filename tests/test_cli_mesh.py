"""Tests for ``embodiment mesh`` (plan task t7): prints, never runs."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.explain.catalog import ENTRIES

_MODULE = Path(__file__).resolve().parent.parent / "embodiment" / "cli" / "_commands" / "mesh.py"
_ENV = (
    "EMBODIMENT_MESH",
    "EMBODIMENT_MESH_NICK",
    "EMBODIMENT_MESH_SERVER",
    "EMBODIMENT_MESH_CHANNELS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_mesh_text_prints_nick_channels_env_and_steps(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mesh"]) == 0
    out = capsys.readouterr().out
    assert "spark-gwen" in out
    assert "#general" in out
    for name in _ENV:
        assert name in out
    assert "not yet supported" in out
    assert "culture-spark-gwen.sock" in out
    assert "spark-embodiment" in out


def test_mesh_json_shape_and_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mesh", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nick"] == "spark-gwen"
    assert payload["server"] == "localhost:6667"
    assert payload["channels"] == ["#general"]
    assert payload["enabled"] is True
    assert payload["env"] == list(_ENV)
    assert payload["registered_resident"] is False
    assert payload["registration_steps"]


def test_mesh_reads_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBODIMENT_MESH", "off")
    monkeypatch.setenv("EMBODIMENT_MESH_NICK", "spark-other")
    monkeypatch.setenv("EMBODIMENT_MESH_CHANNELS", "#a, #b")
    assert main(["mesh", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["nick"] == "spark-other"
    assert payload["channels"] == ["#a", "#b"]


def test_mesh_spawns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("subprocess spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    assert main(["mesh"]) == 0


def test_mesh_module_imports_no_process_api() -> None:
    tree = ast.parse(_MODULE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "socket", "asyncio", "pty"}


def test_catalog_has_mesh_entry() -> None:
    assert ("mesh",) in ENTRIES


def test_doctor_reports_two_distinct_identities(capsys: pytest.CaptureFixture[str]) -> None:
    main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    check = next(c for c in report["checks"] if c["id"] == "mesh_identities")
    assert check["passed"] is True
    assert "spark-embodiment" in check["message"]
    assert "spark-gwen" in check["message"]


@pytest.mark.parametrize("off", ["off", "0", "false", "no", "OFF"])
def test_mesh_reads_the_env_exactly_as_the_daemon_does(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], off: str
) -> None:
    """Review of #117: the CLI took only 'off' as off and kept invalid channels, so an
    agent checking setup saw enabled: true for a mesh the daemon had turned off."""
    monkeypatch.setenv("EMBODIMENT_MESH", off)
    monkeypatch.setenv("EMBODIMENT_MESH_CHANNELS", "general,#ops")
    assert main(["mesh", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["channels"] == ["#ops"]
    assert payload["error"] == ""


def test_mesh_reports_a_server_the_daemon_would_refuse(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBODIMENT_MESH_SERVER", "no-port-here")
    assert main(["mesh", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "mesh-config-invalid"
