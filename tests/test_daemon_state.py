"""Tests for :mod:`embodiment.daemon.state` (plan task t4).

Organised around the task's three verbatim acceptance criteria, plus four
post-review fixes (path traversal, world-readable transcripts, an unfindable
fallback directory, and unreported write errors):

1. a crash leaves a ledger ``status`` can read, and the log never exceeds its
   configured size
2. a test writes a transcript through the session API and asserts the text is
   absent from the operational log
3. the state dir resolves outside the repo regardless of cwd
4. ``open_transcript`` never escapes the sessions directory for any input
5. every directory/file this module writes is private (0700/0600) regardless
   of umask
6. the bootstrap fallback directory is deterministic, findable by a second
   process, and refuses an unsafe pre-existing path at that name
7. ``status()`` reports write failures rather than looking healthy

Every test passes an explicit ``tmp_path``-derived override or monkeypatches
the env/tempdir seams (``EMBODIMENT_STATE_DIR`` / ``XDG_STATE_HOME`` / ``HOME``
/ ``tempfile.gettempdir``) — none ever writes to the real home directory or a
real, un-isolated ``/tmp`` path (important under ``pytest -n auto``: the
fallback directory name is now deterministic per-uid, so two parallel test
workers sharing a real ``/tmp`` would otherwise collide).
"""

from __future__ import annotations

import os
import stat
import tempfile

import pytest

from embodiment.daemon.state import (
    DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
    LEDGER_FILENAME,
    SESSION_ID_REJECTED_CODE,
    STATE_DIR_ENV_VAR,
    STATE_DIR_FALLBACK_CODE,
    STATE_DIR_TIGHTENED_CODE,
    DaemonState,
    DegradationLedger,
    OperationalLog,
    _secure_fallback_dir,
    candidate_state_dirs,
    resolve_fallback_state_dir,
    resolve_state_dir,
)


@pytest.fixture(autouse=True)
def _clean_state_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits a state-dir env var from the outer environment."""
    monkeypatch.delenv(STATE_DIR_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


@pytest.fixture
def _isolated_tempdir(tmp_path, monkeypatch):
    """Redirect ``tempfile.gettempdir()`` to a per-test directory.

    Required by every test that can reach the deterministic fallback
    (``resolve_fallback_state_dir``/``_bootstrap_fallback_dir``): that name is
    now per-uid, not per-process, so leaving it pointed at the real ``/tmp``
    would let parallel ``pytest -n auto`` workers collide on one directory.
    """
    fake = tmp_path / "faketmp"
    fake.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake))
    return fake


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

    def test_falls_back_to_a_temp_dir_and_records_one_degradation(
        self, tmp_path, _isolated_tempdir
    ) -> None:
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


# ── fix 1 (BLOCKER): path traversal in DaemonState.open_transcript ─────────


class TestOpenTranscriptRejectsUnsafeSessionIds:
    """``session_id`` will eventually arrive from network clients — untrusted.

    Every case here must land inside the sessions directory, never raise, and
    never write the rejected id's raw text to the ledger.
    """

    UNSAFE_IDS = [
        "../../x",
        "../../../etc/passwd",
        "/etc/passwd",
        "a\x00b",
        "x" * 10_000,
        "..",
        ".",
        "a\\b",  # the other OS's path separator — still outside the charset
        "",
    ]

    @pytest.mark.parametrize("bad_id", UNSAFE_IDS)
    def test_rejected_ids_stay_contained_within_the_sessions_dir(self, tmp_path, bad_id) -> None:
        state = DaemonState(tmp_path / "state")
        sessions_dir = (state.dir / "sessions").resolve()

        transcript = state.open_transcript(bad_id)

        assert transcript.path.resolve().is_relative_to(sessions_dir)

    @pytest.mark.parametrize("bad_id", UNSAFE_IDS)
    def test_rejected_ids_record_exactly_one_degradation(self, tmp_path, bad_id) -> None:
        state = DaemonState(tmp_path / "state")
        state.open_transcript(bad_id)
        codes = [r.code for r in state.ledger.read_all()]
        assert codes.count(SESSION_ID_REJECTED_CODE) == 1

    # "." is excluded: a single-character id is trivially a substring of
    # almost any text (e.g. a float timestamp), so it cannot demonstrate
    # anything about leakage either way.
    @pytest.mark.parametrize("bad_id", [i for i in UNSAFE_IDS if len(i) > 1])
    def test_the_rejected_ids_raw_text_never_reaches_the_ledger(self, tmp_path, bad_id) -> None:
        state = DaemonState(tmp_path / "state")
        state.open_transcript(bad_id)
        raw = state.ledger.path.read_text(encoding="utf-8", errors="surrogateescape")
        assert bad_id not in raw

    def test_a_valid_session_id_is_used_verbatim_and_never_rejected(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        transcript = state.open_transcript("session-abc123_ok.v2")
        assert transcript.path.name == "session-abc123_ok.v2.jsonl"
        assert state.ledger.status()["count"] == 0

    def test_writing_through_a_rejected_id_still_produces_a_working_transcript(
        self, tmp_path
    ) -> None:
        state = DaemonState(tmp_path / "state")
        transcript = state.open_transcript("../../escape")
        transcript.write("user", "hello despite the bad id")  # must not raise
        assert "hello despite the bad id" in transcript.path.read_text(encoding="utf-8")


# ── fix 2 (MAJOR): transcripts and everything else are private on disk ─────


class TestFilesAndDirectoriesArePrivateRegardlessOfUmask:
    def test_everything_is_private_under_a_permissive_umask(self, tmp_path) -> None:
        old_umask = os.umask(0o022)
        try:
            state = DaemonState(tmp_path / "state")
            state.operational_log.write("started")
            state.ledger.append("some-code", "some detail")
            transcript = state.open_transcript("sess-1")
            transcript.write("user", "hi")

            def group_or_other_bits(path) -> int:
                return stat.S_IMODE(path.stat().st_mode) & 0o077

            assert group_or_other_bits(state.dir) == 0
            assert group_or_other_bits(state.dir / "sessions") == 0
            assert group_or_other_bits(state.operational_log.path) == 0
            assert group_or_other_bits(state.ledger.path) == 0
            assert group_or_other_bits(transcript.path) == 0
        finally:
            os.umask(old_umask)

    def test_the_atomic_rewrite_temp_file_is_private_before_it_is_renamed(
        self, tmp_path, monkeypatch
    ) -> None:
        """Spies on ``os.replace`` to catch the temp file's mode the instant
        before it becomes the final path — the file named explicitly in the
        review's fix request."""
        old_umask = os.umask(0o022)
        seen_modes: list[int] = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen_modes.append(stat.S_IMODE(os.stat(src).st_mode))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        try:
            log = OperationalLog(tmp_path / "op.log", max_bytes=10_000)
            log.write("hello")
        finally:
            os.umask(old_umask)

        assert seen_modes == [0o600]

    def test_a_pre_existing_over_open_state_dir_is_tightened_and_recorded(self, tmp_path) -> None:
        loose = tmp_path / "state"
        loose.mkdir(mode=0o777)
        os.chmod(loose, 0o777)  # mkdir's mode= is itself umask-masked; force it

        state = DaemonState(loose)

        assert stat.S_IMODE(state.dir.stat().st_mode) == 0o700
        codes = [r.code for r in state.ledger.read_all()]
        assert STATE_DIR_TIGHTENED_CODE in codes


# ── fix 3: the fallback directory is deterministic, findable, and guarded ──


class TestFallbackStateDirIsDeterministicAndGuarded:
    def test_two_constructions_with_the_same_blocked_dir_share_one_fallback(
        self, tmp_path, _isolated_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        preferred = blocked / "sub" / "state"

        first = DaemonState(preferred)
        second = DaemonState(preferred)

        assert first.dir == second.dir == resolve_fallback_state_dir()

    def test_candidate_state_dirs_lists_preferred_then_fallback(
        self, tmp_path, monkeypatch, _isolated_tempdir
    ) -> None:
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(tmp_path / "preferred"))
        candidates = candidate_state_dirs()
        assert candidates == [(tmp_path / "preferred").resolve(), resolve_fallback_state_dir()]

    def test_a_status_reader_finds_a_fallen_back_daemon_via_candidate_state_dirs(
        self, tmp_path, monkeypatch, _isolated_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        preferred = blocked / "sub" / "state"
        monkeypatch.setenv(STATE_DIR_ENV_VAR, str(preferred))

        state = DaemonState()

        found = [d for d in candidate_state_dirs() if (d / LEDGER_FILENAME).exists()]
        assert found == [state.dir]

    def test_a_symlinked_fallback_is_refused_and_a_random_one_used_instead(
        self, tmp_path, _isolated_tempdir
    ) -> None:
        deterministic = resolve_fallback_state_dir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        deterministic.symlink_to(elsewhere)

        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        preferred = blocked / "sub" / "state"

        state = DaemonState(preferred)  # must not raise

        assert state.dir != deterministic
        assert not state.dir.is_symlink()
        codes = [r.code for r in state.ledger.read_all()]
        assert STATE_DIR_FALLBACK_CODE in codes

    def test_secure_fallback_dir_refuses_a_symlink(self, tmp_path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        link = tmp_path / "fallback-link"
        link.symlink_to(elsewhere)

        usable, detail = _secure_fallback_dir(link)

        assert usable is False
        assert "symlink" in detail

    def test_secure_fallback_dir_refuses_a_directory_owned_by_someone_else(
        self, tmp_path, monkeypatch
    ) -> None:
        """Exercises the ownership-mismatch branch directly (white-box): this
        sandbox has no privilege to actually chown a directory to a different
        real uid, so ``os.getuid`` is monkeypatched for the DURATION OF THIS
        CHECK ONLY (unlike ``resolve_fallback_state_dir``, which is not
        called here, so the patch cannot also change the candidate path's
        name — see the report for what this does and does not prove)."""
        candidate = tmp_path / "owned-by-someone-else"
        candidate.mkdir()
        real_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)

        usable, detail = _secure_fallback_dir(candidate)

        assert usable is False
        assert "owned by uid" in detail


# ── fix 4: status() reports write failures instead of looking healthy ──────


class TestStatusReportsWriteFailures:
    def test_happy_path_has_no_write_errors(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        state.operational_log.write("started")
        status = state.status()
        assert status["operational_log"]["write_error_count"] == 0
        assert status["operational_log"]["last_write_error"] is None
        assert status["ledger"]["write_error_count"] == 0
        assert status["ledger"]["last_write_error"] is None

    def test_status_reports_operational_log_write_failures(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        state.operational_log.write("a baseline write that succeeds")

        os.chmod(state.dir, 0o500)  # remove write access, even for the owner
        try:
            state.operational_log.write("this one cannot be persisted")
            status = state.status()
        finally:
            os.chmod(state.dir, 0o700)

        assert status["operational_log"]["write_error_count"] >= 1
        assert status["operational_log"]["last_write_error"] is not None
        assert "Error" in status["operational_log"]["last_write_error"]

    def test_status_reports_ledger_write_failures(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")  # ledger file not yet created

        os.chmod(state.dir, 0o500)
        try:
            state.ledger.append("some-code", "some detail")
            status = state.status()
        finally:
            os.chmod(state.dir, 0o700)

        assert status["ledger"]["write_error_count"] >= 1
        assert status["ledger"]["last_write_error"] is not None

    def test_write_error_text_never_contains_transcript_text(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        transcript = state.open_transcript("s1")
        secret = "the quick brown fox told me a private secret"

        os.chmod(state.dir / "sessions", 0o500)
        try:
            transcript.write("user", secret)
        finally:
            os.chmod(state.dir / "sessions", 0o700)

        assert transcript.write_errors  # the write did fail, as the test intends
        assert secret not in " ".join(transcript.write_errors)
