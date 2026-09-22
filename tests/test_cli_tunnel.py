"""Tests for ``embodiment tunnel`` — plan task t20.

Covers the three acceptance criteria verbatim:

1. ``embodiment tunnel`` prints the exact commands and mutates nothing, with
   ``--json`` and an ``explain`` entry.
2. README documents that Cloudflare Access protects only the public hostname
   and how the daemon validates the assertion.
3. the verb never invokes ``cultureflare`` with ``--apply`` (structural AST
   scan + behavioural monkeypatch of every process-spawning primitive).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.explain.catalog import ENTRIES
from embodiment.http.server import DEFAULT_PORT

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO_ROOT / "embodiment" / "cli" / "_commands" / "tunnel.py"
_README_PATH = _REPO_ROOT / "README.md"


# --- criterion 1: prints the exact commands, mutates nothing ---------------


def test_tunnel_text_prints_setup_and_run_commands(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["tunnel"])
    assert rc == 0
    out = capsys.readouterr().out
    assert (
        f"cultureflare remote-login setup --hostname agent.culture.dev "
        f"--service http://127.0.0.1:{DEFAULT_PORT}" in out
    )
    assert "cloudflared tunnel run" in out


def test_tunnel_default_hostname_and_port_come_from_named_constants(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Regression against "restate the number": the default port in the printed
    # command must be embodiment.http.server.DEFAULT_PORT, not a copied literal.
    rc = main(["tunnel", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["port"] == DEFAULT_PORT
    assert payload["hostname"] == "agent.culture.dev"


def test_tunnel_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["tunnel", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["setup_command"][:3] == ["cultureflare", "remote-login", "setup"]
    assert payload["run_command"] == ["cloudflared", "tunnel", "run"]
    assert payload["allow"] == []
    assert payload["with_service_token"] is False


def test_tunnel_allow_flag_is_repeatable(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["tunnel", "--allow", "a@example.com", "--allow", "b@example.com", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allow"] == ["a@example.com", "b@example.com"]
    cmd = payload["setup_command"]
    assert cmd.count("--allow") == 2
    assert "a@example.com" in cmd and "b@example.com" in cmd


def test_tunnel_with_service_token_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["tunnel", "--with-service-token", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["with_service_token"] is True
    assert "--with-service-token" in payload["setup_command"]


def test_tunnel_custom_hostname_and_port(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["tunnel", "--hostname", "gwen.example.org", "--port", "9999", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hostname"] == "gwen.example.org"
    assert payload["port"] == 9999
    assert "--hostname" in payload["setup_command"]
    assert "gwen.example.org" in payload["setup_command"]
    assert "http://127.0.0.1:9999" in payload["setup_command"]


def test_tunnel_has_no_apply_flag() -> None:
    # There is no --apply here at all: an unknown flag must be a structured
    # user error (exit 1), never a silently-accepted mutation switch.
    with pytest.raises(SystemExit) as exc:
        main(["tunnel", "--apply"])
    assert exc.value.code == 1


def test_tunnel_has_no_stop_flag() -> None:
    # lobes tunnel has --stop; this verb has no mutating counterpart at all.
    with pytest.raises(SystemExit) as exc:
        main(["tunnel", "--stop"])
    assert exc.value.code == 1


def test_tunnel_explain_entry(capsys: pytest.CaptureFixture[str]) -> None:
    assert ("tunnel",) in ENTRIES
    rc = main(["explain", "tunnel"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tunnel" in out.lower()
    assert "cultureflare" in out.lower()
    assert "--apply" in out


# --- criterion 2: README documents the Access boundary ---------------------


def test_readme_has_remote_access_section() -> None:
    text = _README_PATH.read_text(encoding="utf-8")
    assert "## Remote access" in text


def test_readme_documents_access_protects_only_public_hostname() -> None:
    text = _README_PATH.read_text(encoding="utf-8")
    start = text.index("## Remote access")
    # Section body up to the next "## " heading.
    end = text.index("\n## ", start + 1)
    section = text[start:end]
    lowered = section.lower()
    assert "public hostname" in lowered
    assert "loopback" in lowered
    assert "cf-access-jwt-assertion" in lowered
    assert "install secret" in lowered
    assert "service token" in lowered
    assert "cf-access-client-id" in lowered
    assert "cf-access-client-secret" in lowered


# --- criterion 3: never invokes cultureflare with --apply -------------------


def _iter_string_constants(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def test_tunnel_module_imports_no_process_spawning_primitive() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    forbidden_modules = {"subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert (
                    alias.name.split(".")[0] not in forbidden_modules
                ), f"tunnel.py imports {alias.name}"
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules, f"tunnel.py imports from {node.module}"


def test_tunnel_module_ast_has_no_process_spawning_call() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    forbidden_attrs = {
        ("subprocess", "run"),
        ("subprocess", "call"),
        ("subprocess", "Popen"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("os", "system"),
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            assert pair not in forbidden_attrs, f"forbidden call found: {pair}"
            if func.value.id == "os" and (
                func.attr.startswith("exec") or func.attr.startswith("spawn")
            ):
                pytest.fail(f"forbidden os.{func.attr} call found")
        if isinstance(func, ast.Name):
            assert func.id not in {"Popen"}, "forbidden bare Popen call found"


def test_tunnel_apply_literal_never_built_into_a_command_list() -> None:
    """``--apply`` may only appear inside prose printed for the operator.

    It must never be an element of a list literal that looks like a command
    argv (the thing this module would hand to a process if it ever spawned
    one).
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    found_operator_guidance = False
    for node in _iter_string_constants(tree):
        if "--apply" not in node.value:
            continue
        # Standalone list-element occurrences (argv-shaped) are forbidden.
        assert node.value != "--apply", "bare '--apply' string literal found"
        if "yourself" in node.value or "re-run" in node.value.lower():
            found_operator_guidance = True
    assert found_operator_guidance, "expected '--apply' to appear in operator-facing guidance text"

    # No List literal anywhere in the module contains the string "--apply" as
    # one of its elements.
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and elt.value == "--apply":
                pytest.fail("'--apply' found as an element of a list literal")


def test_tunnel_never_invokes_subprocess_or_exec_behaviourally(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("a process-spawning primitive was called")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "call", _boom)
    monkeypatch.setattr(subprocess, "check_call", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr(os, "system", _boom)
    for name in ("execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom)
    for name in ("spawnv", "spawnve", "spawnvp", "spawnl", "spawnle"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom)

    invocations = [
        ["tunnel"],
        ["tunnel", "--json"],
        ["tunnel", "--allow", "a@example.com"],
        ["tunnel", "--with-service-token"],
        ["tunnel", "--hostname", "x.example.org", "--port", "1234", "--json"],
    ]
    for argv in invocations:
        rc = main(argv)
        assert rc == 0
        capsys.readouterr()  # drain, keep streams clean between calls


def test_tunnel_output_never_contains_bare_apply_flag_for_cultureflare(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed cultureflare command itself must never carry --apply."""
    rc = main(["tunnel", "--allow", "x@example.com", "--with-service-token", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "--apply" not in payload["setup_command"]
    assert "--apply" not in payload["run_command"]
