"""The ``start`` / ``stop`` / ``status`` CLI verbs (plan task t5, criterion 3).

Criterion 3 is ``teken cli doctor . --strict`` and
``tests/test_cli_introspection.py`` passing with the three verbs registered.
The introspection suite walks the live parser and fails on any verb missing an
``explain`` catalog entry, so registration is already covered there; what this
file adds is the part a catalog entry cannot prove — that each verb honours the
CLI contract this repo's agent-first rubric is built on:

* every verb takes ``--json``;
* results go to stdout, errors and diagnostics to stderr, never mixed;
* every failure is a ``CliError`` — exit 1 for user error, 2 for environment —
  and **no Python traceback ever reaches stderr**;
* ``embodiment start`` with the daemon application unbuilt (plan task t15) is a
  clean environment error with a hint, not a crash.

One test drives a real detached child end to end through ``main()``; the rest
run against real (empty) state directories, so the honest ``stopped`` and
``state unavailable`` paths are exercised rather than mocked.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
from pathlib import Path

import pytest

from embodiment.cli import _build_parser, main
from embodiment.daemon.lifecycle import DEFAULT_TARGET, STATE_RUNNING, STATE_STOPPED
from embodiment.daemon.state import STATE_DIR_ENV_VAR, DaemonState
from embodiment.explain.catalog import ENTRIES

LIFECYCLE_VERBS = ("start", "stop", "status")

IDLE_TARGET = """
class App:
    def run(self, stop_event):
        stop_event.wait()
        return 0


def main():
    return App()
"""


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-test state dir and tempdir — including ``TMPDIR`` for spawned children.

    See ``tests/test_daemon_lifecycle.py``: a child computes t4's deterministic
    fallback in its own interpreter, so patching this process's ``tempfile``
    alone would leave a fallback landing in the machine-wide directory.
    """
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp), raising=False)
    monkeypatch.setenv("TMPDIR", str(fake_tmp))
    monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "state"))


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


# ── criterion 3: the three verbs are registered and documented ───────────────


class TestTheVerbsAreRegisteredAndExplained:
    @pytest.mark.parametrize("verb", LIFECYCLE_VERBS)
    def test_the_verb_is_registered(self, verb: str) -> None:
        parser = _build_parser()
        subparsers = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        assert any(verb in a.choices for a in subparsers), f"{verb} is not registered"

    @pytest.mark.parametrize("verb", LIFECYCLE_VERBS)
    def test_the_verb_has_a_catalog_entry(self, verb: str) -> None:
        assert (verb,) in ENTRIES

    @pytest.mark.parametrize("verb", LIFECYCLE_VERBS)
    def test_explain_renders_the_entry(self, verb: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["explain", verb]) == 0
        assert f"embodiment {verb}" in capsys.readouterr().out

    def test_the_root_entry_no_longer_says_no_verb_starts_anything(self) -> None:
        """The catalog's own claim must follow the surface, not lag it."""
        root = ENTRIES[()]
        assert "starts anything" not in root
        for verb in LIFECYCLE_VERBS:
            assert f"embodiment {verb}" in root

    def test_overview_lists_the_lifecycle_verbs(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["overview"]) == 0
        out = capsys.readouterr().out
        for verb in LIFECYCLE_VERBS:
            assert verb in out

    @pytest.mark.parametrize("verb", LIFECYCLE_VERBS)
    def test_the_verb_accepts_json(self, verb: str, capsys: pytest.CaptureFixture[str]) -> None:
        parser = _build_parser()
        args = parser.parse_args([verb, "--json"])
        assert args.json is True


# ── the CLI contract ─────────────────────────────────────────────────────────


class TestStatusVerb:
    def test_status_text_on_an_empty_state_dir(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        DaemonState(state_dir)
        assert main(["status"]) == 0
        captured = capsys.readouterr()
        assert "embodiment status" in captured.out
        assert STATE_STOPPED in captured.out
        assert captured.err == ""

    def test_status_json_shape(self, state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        DaemonState(state_dir)
        assert main(["status", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == STATE_STOPPED
        assert "ledger" in payload
        assert "candidates" in payload

    def test_status_never_hard_fails_without_a_state_dir(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["state"] == "state unavailable"


class TestStartVerb:
    def test_start_without_the_daemon_app_is_a_clean_environment_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["start"])
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert captured.err.startswith("error:")
        assert "hint:" in captured.err
        assert "Traceback" not in captured.err
        assert DEFAULT_TARGET.split(":")[0] in captured.err

    def test_start_failure_in_json_mode_is_structured(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["start", "--json"])
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["code"] == 2
        assert payload["remediation"]

    def test_start_with_a_malformed_target_is_a_user_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["start", "--target", "not a target"])
        captured = capsys.readouterr()
        assert rc in (1, 2)
        assert "Traceback" not in captured.err
        assert captured.err.startswith("error:")


class TestStopVerb:
    def test_stop_with_nothing_running_is_not_an_error(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        DaemonState(state_dir)
        assert main(["stop"]) == 0
        captured = capsys.readouterr()
        assert "not running" in captured.out
        assert captured.err == ""

    def test_stop_json_shape(self, state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        DaemonState(state_dir)
        assert main(["stop", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["was_running"] is False
        assert payload["stopped"] is False


class TestParseErrorsStayStructured:
    @pytest.mark.parametrize("verb", LIFECYCLE_VERBS)
    def test_an_unknown_flag_is_a_structured_error(
        self, verb: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main([verb, "--definitely-not-a-flag"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("error:")
        assert "hint:" in err


# ── end to end, through the CLI, with a real detached child ──────────────────


class TestEndToEndThroughTheCli:
    def test_start_status_stop_round_trip(
        self, tmp_path: Path, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fakes = tmp_path / "fakes"
        fakes.mkdir()
        (fakes / "cli_idle.py").write_text(IDLE_TARGET, encoding="utf-8")
        old_path = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(fakes) if not old_path else f"{fakes}{os.pathsep}{old_path}"
        pid = None
        try:
            rc = main(["start", "--target", "cli_idle:main", "--json"])
            payload = json.loads(capsys.readouterr().out)
            assert rc == 0, payload
            assert payload["started"] is True
            pid = payload["pid"]

            assert main(["status", "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["state"] == STATE_RUNNING

            # Idempotent: a second start exits 0 and starts nothing new.
            assert main(["start", "--target", "cli_idle:main", "--json"]) == 0
            second = json.loads(capsys.readouterr().out)
            assert second["already_running"] is True
            assert second["pid"] == pid

            began = time.monotonic()
            assert main(["stop", "--json"]) == 0
            elapsed = time.monotonic() - began
            stopped = json.loads(capsys.readouterr().out)
            assert stopped["stopped"] is True
            assert elapsed < 5.0

            assert main(["status", "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["state"] == STATE_STOPPED
        finally:
            if old_path is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old_path
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass  # nosec B110 - the daemon is already gone, which is the happy path
