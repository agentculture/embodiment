"""Tests for :mod:`embodiment.daemon.state` (plan task t4).

Organised around the task's three verbatim acceptance criteria:

1. a crash leaves a ledger ``status`` can read, and the log never exceeds its
   configured size
2. a test writes a transcript through the session API and asserts the text is
   absent from the operational log
3. the state dir resolves outside the repo regardless of cwd

Every test passes an explicit ``tmp_path``-derived override or monkeypatches
the env seams (``EMBODIMENT_STATE_DIR`` / ``XDG_STATE_HOME`` / ``HOME``) — none
ever writes to the real home directory.
"""

from __future__ import annotations

import pytest

from embodiment.daemon.state import (
    DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
    STATE_DIR_ENV_VAR,
    STATE_DIR_FALLBACK_CODE,
    DaemonState,
    DegradationLedger,
    OperationalLog,
    resolve_state_dir,
)


@pytest.fixture(autouse=True)
def _clean_state_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits a state-dir env var from the outer environment."""
    monkeypatch.delenv(STATE_DIR_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


# ── criterion 3: the state dir resolves outside the repo regardless of cwd ──


class TestResolveStateDir:
    def test_explicit_override_wins_outright(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "env-wins"))
        chosen = tmp_path / "explicit"
        assert resolve_state_dir(chosen) == chosen.resolve()

    def test_env_var_wins_over_xdg(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "from-env"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        assert resolve_state_dir() == (tmp_path / "from-env").resolve()

    def test_xdg_state_home_used_when_no_override_or_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        assert resolve_state_dir() == (tmp_path / "xdg" / "embodiment").resolve()

    def test_falls_back_to_home_local_state(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_state_dir() == (tmp_path / ".local" / "state" / "embodiment").resolve()

    def test_resolution_ignores_cwd_from_inside_a_repo(self, tmp_path, monkeypatch) -> None:
        """The acceptance criterion, stated directly: chdir into a fake repo,
        and the resolved state dir is still outside it — for every source."""
        fake_repo = tmp_path / "repo"
        (fake_repo / ".git").mkdir(parents=True)
        monkeypatch.chdir(fake_repo)

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        home_default = resolve_state_dir()
        assert not home_default.is_relative_to(fake_repo)

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        xdg_based = resolve_state_dir()
        assert not xdg_based.is_relative_to(fake_repo)

        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "env"))
        env_based = resolve_state_dir()
        assert not env_based.is_relative_to(fake_repo)

        explicit = resolve_state_dir(tmp_path / "explicit")
        assert not explicit.is_relative_to(fake_repo)

    def test_resolution_is_identical_regardless_of_which_directory_called_it_from(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "state"))
        from_a = resolve_state_dir()
        (tmp_path / "elsewhere").mkdir()
        monkeypatch.chdir(tmp_path / "elsewhere")
        from_b = resolve_state_dir()
        assert from_a == from_b


# ── criterion 1: crash-durable ledger + a log that never exceeds its bound ──


class TestDegradationLedgerSurvivesACrash:
    def test_status_reads_every_clean_entry(self, tmp_path) -> None:
        ledger = DegradationLedger(tmp_path / "degradations.jsonl")
        ledger.append("connect-failed", "gateway down")
        ledger.append("recall-timeout", "deadline exceeded")

        status = ledger.status()
        assert status["count"] == 2
        assert status["last"]["code"] == "recall-timeout"

    def test_a_torn_last_line_does_not_break_status(self, tmp_path) -> None:
        """Simulates a process killed mid-append: the final line is truncated
        JSON with no trailing newline — exactly what an interrupted write
        leaves behind. Prior entries must still read cleanly."""
        path = tmp_path / "degradations.jsonl"
        ledger = DegradationLedger(path)
        ledger.append("connect-failed", "gateway down")
        ledger.append("recall-timeout", "deadline exceeded")

        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"ts": 1.0, "code": "torn-by-a-cra')  # no closing brace, no newline

        # A brand-new instance, as a restarted daemon would construct.
        reopened = DegradationLedger(path)
        status = reopened.status()
        assert status["count"] == 2
        assert status["last"]["code"] == "recall-timeout"

    def test_status_on_a_never_written_ledger_is_empty_not_an_error(self, tmp_path) -> None:
        ledger = DegradationLedger(tmp_path / "never-written.jsonl")
        status = ledger.status()
        assert status["count"] == 0
        assert status["last"] is None

    def test_append_never_raises_when_the_directory_cannot_be_created(self, tmp_path) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        ledger = DegradationLedger(blocked / "sub" / "degradations.jsonl")

        result = ledger.append("whatever", "detail")

        assert result is None
        assert ledger.write_errors  # recorded, not silently dropped


class TestOperationalLogNeverExceedsItsConfiguredSize:
    def test_repeated_writes_stay_under_the_configured_bound(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        for i in range(500):
            log.write("heartbeat", step=i, note="a modestly sized operational record")
            assert log.path.stat().st_size <= 2_000

    def test_oldest_entries_are_dropped_first(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=600)
        for i in range(50):
            log.write("heartbeat", step=i)

        records = log.read_all()
        assert records, "expected at least one surviving record"
        # The most recent write must have survived rotation.
        assert records[-1]["step"] == 49
        # Rotation must have actually dropped something over 50 writes at
        # this bound, i.e. this is not simply "everything fits".
        assert len(records) < 50

    def test_default_bound_is_documented_and_positive(self) -> None:
        assert DEFAULT_OPERATIONAL_LOG_MAX_BYTES > 0

    def test_write_never_raises_when_the_directory_cannot_be_created(self, tmp_path) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        log = OperationalLog(blocked / "sub" / "embodiment.log", max_bytes=1_000)

        log.write("heartbeat")  # must not raise

        assert log.write_errors


# ── criterion 2: a transcript written through the session API never reaches
#    the operational log ──────────────────────────────────────────────────


class TestTranscriptNeverReachesTheOperationalLog:
    SECRET_MARKER = "the quick brown fox told me a private secret about the shipment"

    def test_transcript_text_is_absent_from_the_operational_log(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        state.operational_log.write("session-started", session_id="s1")

        transcript = state.open_transcript("s1")
        transcript.write("user", self.SECRET_MARKER)
        transcript.write("assistant", "acknowledged, noted quietly")

        state.operational_log.write("session-ended", session_id="s1")

        operational_raw = state.operational_log.path.read_text(encoding="utf-8")
        assert self.SECRET_MARKER not in operational_raw

        transcript_raw = transcript.path.read_text(encoding="utf-8")
        assert self.SECRET_MARKER in transcript_raw

    def test_transcript_and_operational_log_are_different_files(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        transcript = state.open_transcript("s1")
        assert transcript.path != state.operational_log.path

    def test_transcript_log_is_size_bounded(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state", transcript_log_max_bytes=500)
        transcript = state.open_transcript("s2")
        for i in range(200):
            transcript.write("user", f"turn number {i} of a long rambling conversation")
            assert transcript.path.stat().st_size <= 500

    def test_two_sessions_get_two_transcript_files(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        a = state.open_transcript("session-a")
        b = state.open_transcript("session-b")
        a.write("user", "hello from a")
        b.write("user", "hello from b")
        assert a.path != b.path
        assert "hello from a" in a.path.read_text(encoding="utf-8")
        assert "hello from b" not in a.path.read_text(encoding="utf-8")


# ── DaemonState: bootstrap, fallback, status ────────────────────────────────


class TestDaemonState:
    def test_constructs_and_creates_its_directory(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        assert state.dir.is_dir()
        assert state.dir == (tmp_path / "state").resolve()

    def test_status_reports_dir_log_and_ledger(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        state.operational_log.write("started")
        status = state.status()
        assert status["state_dir"] == str(state.dir)
        assert status["operational_log"]["size_bytes"] > 0
        assert status["ledger"]["count"] == 0

    def test_falls_back_to_a_temp_dir_and_records_one_degradation(self, tmp_path) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        preferred = blocked / "sub" / "state"

        state = DaemonState(preferred)  # must not raise

        assert state.dir != preferred.resolve()
        assert state.dir.is_dir()
        status = state.ledger.status()
        assert status["count"] == 1
        assert status["last"]["code"] == STATE_DIR_FALLBACK_CODE

    def test_happy_path_records_no_bootstrap_degradation(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        assert state.ledger.status()["count"] == 0

    def test_default_state_dir_env_var_seam_is_honoured(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "via-env"))
        state = DaemonState()
        assert state.dir == (tmp_path / "via-env").resolve()

    def test_open_transcript_handles_an_empty_session_id(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        transcript = state.open_transcript("")
        transcript.write("user", "hi")  # must not raise
        assert transcript.path.exists()
