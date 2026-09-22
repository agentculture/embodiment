"""Tests for ``embodiment tunnel`` — plan task t20.

Covers the three acceptance criteria verbatim:

1. ``embodiment tunnel`` prints the exact commands and mutates nothing, with
   ``--json`` and an ``explain`` entry.
2. README documents that Cloudflare Access protects only the public hostname
   and how the daemon validates the assertion.
3. the verb never invokes ``cultureflare`` with ``--apply`` (structural AST
   scan + behavioural monkeypatch of every process-spawning primitive).

Round 3 adds the review fix: a value outside a safe charset for
``--hostname``/``--allow``/``--tunnel-name`` is refused at parse time, and
every printed command line is ``shlex.quote``-d regardless (two independent
layers — see ``embodiment/cli/_commands/tunnel.py``'s module docstring).
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.cli._commands.tunnel import TUNNEL_NAME_PLACEHOLDER, _render, build_plan
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
    assert f"cloudflared tunnel run {shlex.quote(TUNNEL_NAME_PLACEHOLDER)}" in out


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
    assert payload["run_command"] == ["cloudflared", "tunnel", "run", TUNNEL_NAME_PLACEHOLDER]
    assert payload["allow"] == []
    assert payload["with_service_token"] is False
    assert payload["tunnel_name"] is None


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


def test_tunnel_run_command_uses_placeholder_when_tunnel_name_absent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # cloudflared rejects a bare `cloudflared tunnel run` — it needs the
    # tunnel name (or --token). cultureflare derives that name in step 1; this
    # verb never derives it itself, so it prints a placeholder plus a line
    # saying where the real value comes from.
    rc = main(["tunnel", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tunnel_name"] is None
    assert payload["run_command"] == ["cloudflared", "tunnel", "run", TUNNEL_NAME_PLACEHOLDER]

    rc = main(["tunnel"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"cloudflared tunnel run {shlex.quote(TUNNEL_NAME_PLACEHOLDER)}" in out
    # One line explaining where the placeholder's value comes from.
    assert "step 1" in out
    assert "tunnel name" in out.lower()


def test_tunnel_run_command_uses_given_tunnel_name_in_both_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["tunnel", "--tunnel-name", "gwen-tunnel", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tunnel_name"] == "gwen-tunnel"
    assert payload["run_command"] == ["cloudflared", "tunnel", "run", "gwen-tunnel"]
    assert "--tunnel-name" in payload["setup_command"]
    assert "gwen-tunnel" in payload["setup_command"]
    # Given a name, the placeholder must not appear anywhere.
    assert TUNNEL_NAME_PLACEHOLDER not in payload["run_command"]
    assert TUNNEL_NAME_PLACEHOLDER not in payload["setup_command"]

    rc = main(["tunnel", "--tunnel-name", "gwen-tunnel"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cloudflared tunnel run gwen-tunnel" in out
    assert "--tunnel-name gwen-tunnel" in out
    assert TUNNEL_NAME_PLACEHOLDER not in out


def test_tunnel_placeholder_is_stable_and_not_derived_from_hostname(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Regression against deriving the name ourselves: changing --hostname must
    # never change the placeholder text, because this verb is not allowed to
    # guess cultureflare's derivation rule.
    rc = main(["tunnel", "--hostname", "one.example.org", "--json"])
    assert rc == 0
    payload_one = json.loads(capsys.readouterr().out)
    rc = main(["tunnel", "--hostname", "two.example.org", "--json"])
    assert rc == 0
    payload_two = json.loads(capsys.readouterr().out)
    assert payload_one["run_command"][-1] == TUNNEL_NAME_PLACEHOLDER
    assert payload_two["run_command"][-1] == TUNNEL_NAME_PLACEHOLDER
    assert payload_one["run_command"][-1] == payload_two["run_command"][-1]


def _assert_structured_user_error(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# --- round 3: charset validation refuses shell metacharacters --------------


def test_tunnel_reproduces_reported_attack_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The exact repro from the round-2 review: a paste of the printed command
    # used to do something else. Both offending flags must now be refused.
    _assert_structured_user_error(
        capsys,
        ["tunnel", "--tunnel-name", "a; touch pwned", "--allow", "x y@example.com"],
    )


@pytest.mark.parametrize(
    "bad_hostname",
    [
        "evil.com; touch pwned",
        "evil.com && touch pwned",
        "$(touch pwned)",
        "`touch pwned`",
        "evil.com|touch pwned",
        "evil.com\ttouch pwned",
        "evil.com\ntouch pwned",
        "-evil.com",  # leading hyphen label
        "evil-.com",  # trailing hyphen label
        "a" * 300,  # too long
        "",
    ],
)
def test_tunnel_rejects_hostname_outside_rfc1123_charset(
    capsys: pytest.CaptureFixture[str], bad_hostname: str
) -> None:
    if bad_hostname == "":
        # argparse treats an empty string as a present-but-empty value, not a
        # missing one, so this still goes through the type= validator.
        _assert_structured_user_error(capsys, ["tunnel", "--hostname", bad_hostname])
    else:
        _assert_structured_user_error(capsys, ["tunnel", "--hostname", bad_hostname])


@pytest.mark.parametrize(
    "bad_email",
    [
        "x y@example.com",  # whitespace
        "noatsign.example.com",  # no '@'
        "a@b@example.com",  # two '@'
        "a@example.com; touch pwned",
        "a@example.com\ntouch pwned",
        "`touch pwned`@example.com",
        "a@",  # empty domain
        "@example.com",  # empty local part
    ],
)
def test_tunnel_rejects_allow_email_outside_safe_charset(
    capsys: pytest.CaptureFixture[str], bad_email: str
) -> None:
    _assert_structured_user_error(capsys, ["tunnel", "--allow", bad_email])


@pytest.mark.parametrize(
    "bad_name",
    [
        "a; touch pwned",
        "a && touch pwned",
        "$(touch pwned)",
        "`touch pwned`",
        "a|touch pwned",
        "a touch pwned",  # bare space
        "a\ttouch pwned",
        "a\ntouch pwned",
        "a" * 300,  # too long
        "",
    ],
)
def test_tunnel_rejects_tunnel_name_outside_safe_charset(
    capsys: pytest.CaptureFixture[str], bad_name: str
) -> None:
    _assert_structured_user_error(capsys, ["tunnel", "--tunnel-name", bad_name])


def test_tunnel_valid_values_still_accepted_after_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(
        [
            "tunnel",
            "--hostname",
            "gwen.example.org",
            "--allow",
            "me@example.com",
            "--allow",
            "team@sub.example.co.uk",
            "--tunnel-name",
            "gwen-tunnel.v1_2",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hostname"] == "gwen.example.org"
    assert payload["allow"] == ["me@example.com", "team@sub.example.co.uk"]
    assert payload["tunnel_name"] == "gwen-tunnel.v1_2"


def test_tunnel_render_quotes_every_token_of_both_command_lines() -> None:
    # Second, independent defence: even a value that could never reach here
    # through the validated CLI flags must still render shell-safe if it
    # reaches build_plan()/_render() directly (e.g. a future caller of this
    # module that bypasses argparse). Round-trip through shlex.split proves
    # the printed line is quoted, not a bare space-join.
    plan = build_plan(
        hostname="safe.example.org",
        port=8823,
        allow=("x y@example.com; touch pwned",),
        with_service_token=False,
        tunnel_name="a; touch pwned",
    )
    text = _render(plan)
    setup_line = next(
        line for line in text.splitlines() if line.strip().startswith("cultureflare")
    ).strip()
    run_line = next(
        line for line in text.splitlines() if line.strip().startswith("cloudflared")
    ).strip()
    assert tuple(shlex.split(setup_line)) == plan.setup_command
    assert tuple(shlex.split(run_line)) == plan.run_command
    # And critically: shlex.split never produces a second command — "touch"
    # and "pwned" stay glued inside one quoted token, never becoming their
    # own argv entries.
    assert "touch" not in shlex.split(setup_line)
    assert "pwned" not in shlex.split(setup_line)
    assert "touch" not in shlex.split(run_line)
    assert "pwned" not in shlex.split(run_line)


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
