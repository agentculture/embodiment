"""Tests for :mod:`embodiment.daemon.state` (plan task t4).

Organised around the task's three verbatim acceptance criteria, plus seven
post-review fixes across two rounds:

Round 2 (path traversal, world-readable transcripts, an unfindable fallback
directory, unreported write errors):

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

Round 3 (never-raise gap, TOCTOU re-check, orphaned temp files):

8. construction never raises even when NO directory anywhere can be
   created — it enters a recorded no-persistence mode instead
9. the fallback directory is re-checked (``lstat``) immediately after
   creation, not trusted from before it, closing a TOCTOU window
10. construction sweeps and removes its own orphaned atomic-rewrite temp
    files, recording one degradation naming the count

Round 4 (every bounded-log write was a full read+rewrite+fsync regardless of
how far the file was from its bound):

11. below the bound, a write is a true ``O_APPEND`` of one line; the
    expensive rewrite only runs when an append would cross the bound, and it
    trims to a low-water mark so appends resume afterward — the file never
    exceeds its configured size after ANY of a few thousand writes
12. the fsync policy is a named, per-log-type choice: the ledger fsyncs
    every append, the operational and transcript logs do not
13. a torn last line (a crash mid-append) is tolerated by every reader, and
    the NEXT append after one starts on a fresh line rather than gluing onto
    the fragment
14. several threads appending to one log concurrently never interleave bytes
    within a line and never lose or duplicate a record
15. a performance regression test asserts on the REWRITE HELPER's call
    count, not wall-clock, so it cannot flake

Round 5 (a bounded log did not count what it evicted — pre-existing in the
merged code, folded into this same round of fixes):

16. eviction, rewrite and oversize-record counts are exact across a known
    sequence of crossings, visible in ``status()``, and thread-safe under
    concurrent writers; an oversize record is never written, is counted
    every time, and degrades exactly once per log instance

Round 6 (two reviewers, both independently reproduced by the coordinator
before filing):

17. two instances on one path (two ``open_transcript`` calls for the same
    session, two ``DaemonState``s on the same directory) share ONE
    process-wide lock, not one each — closing a race that silently dropped
    records AND double-counted evictions; ``open_transcript`` is now
    idempotent per session id (pinned: ``a is b``)
18. the torn-tail newline PREFIX byte is counted in the append-vs-rewrite
    decision, so it can never itself push a file one byte over its bound
19. a read failure on the tail-probe (an unreadable-but-writable file) is
    treated as dirty, never as "confirmed clean" — a spurious blank line
    beats a silently glued-and-lost record — and is counted
20. a torn fragment a trim pops is counted separately from a real evicted
    record, never conflated with one
21. a record with a non-JSON-serializable field degrades instead of raising
    a bare ``TypeError`` out of a "never raises" API
22. ``on_degrade`` is called only after the write lock is released, for
    every reason a write can degrade — a hook that writes back to the same
    log does not deadlock

Every test passes an explicit ``tmp_path``-derived override or monkeypatches
the env/tempdir seams (``EMBODIMENT_STATE_DIR`` / ``XDG_STATE_HOME`` / ``HOME``
/ ``tempfile.gettempdir``) — none ever writes to the real home directory or a
real, un-isolated ``/tmp`` path (important under ``pytest -n auto``: the
fallback directory name is now deterministic per-uid, so two parallel test
workers sharing a real ``/tmp`` would otherwise collide).
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path

import pytest

import embodiment.daemon.state as state_mod
from embodiment.daemon.state import (
    DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
    LEDGER_FILENAME,
    LEDGER_FSYNC_PER_APPEND,
    OPERATIONAL_LOG_FSYNC_PER_APPEND,
    OVERSIZE_RECORD_CODE,
    PERSISTENCE_STATUS_UNAVAILABLE,
    SESSION_ID_REJECTED_CODE,
    STALE_TEMP_FILES_SWEPT_CODE,
    STATE_DIR_ENV_VAR,
    STATE_DIR_FALLBACK_CODE,
    STATE_DIR_TIGHTENED_CODE,
    TRANSCRIPT_LOG_FSYNC_PER_APPEND,
    DaemonState,
    DegradationLedger,
    OperationalLog,
    TranscriptLog,
    _bootstrap_fallback_dir,
    _last_byte_is_newline_or_empty,
    _secure_fallback_dir,
    _sweep_stale_temp_files,
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
        review's fix request.

        Updated for the append-below-the-bound fix (this round): a single
        small write no longer takes the rewrite path at all (that is the
        fix), so ``os.replace`` is exercised directly through
        ``_bounded_rewrite_append`` — the only remaining caller of
        ``os.replace`` in this module — rather than through ``OperationalLog``.
        """
        old_umask = os.umask(0o022)
        seen_modes: list[int] = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen_modes.append(stat.S_IMODE(os.stat(src).st_mode))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        try:
            state_mod._bounded_rewrite_append(
                tmp_path / "op.log", "hello", max_bytes=10_000, low_water_bytes=7_500
            )
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
        """Updated for the append-below-the-bound fix (this round): an append
        below the bound only needs write permission on the FILE, not the
        directory (that is the point of the fix — it no longer creates a new
        temp file on every write), so the file itself is blocked here rather
        than its directory.
        """
        state = DaemonState(tmp_path / "state")
        state.operational_log.write("a baseline write that succeeds")

        os.chmod(state.operational_log.path, 0o400)  # no write, even for the owner
        try:
            state.operational_log.write("this one cannot be persisted")
            status = state.status()
        finally:
            os.chmod(state.operational_log.path, 0o600)

        assert status["operational_log"]["write_error_count"] >= 1
        assert status["operational_log"]["last_write_error"] is not None
        assert "Error" in status["operational_log"]["last_write_error"]

    def test_status_reports_operational_log_write_failures_when_directory_blocked(
        self, tmp_path
    ) -> None:
        """The crossing-the-bound (rewrite) path still needs directory write
        access, since it creates a fresh temp file — covered separately from
        the append-path case above. Bound of 150 (not 80, as an earlier
        version of this test used): round 5's oversize handling now rejects
        any single record whose OWN encoded size exceeds max_bytes before it
        ever reaches the rewrite path, and the crossing write's own encoded
        size (measured ~89 bytes) exceeded an 80-byte bound on its own — this
        test needs a genuine CROSSING (each write individually under the
        bound, combined over it), not an oversize rejection.
        """
        state = DaemonState(tmp_path / "state", operational_log_max_bytes=150)
        state.operational_log.write("a baseline write that fits comfortably under the bound")

        os.chmod(state.dir, 0o500)  # remove write access, even for the owner
        try:
            # Individually under 150 bytes; combined with the baseline above,
            # over it — a genuine crossing, not an oversize rejection.
            state.operational_log.write("this write is long enough to cross the tiny bound")
            status = state.status()
        finally:
            os.chmod(state.dir, 0o700)

        assert status["operational_log"]["write_error_count"] >= 1

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


# ── round 3, fix 1 (the important one): no-persistence-anywhere never raises ─


@pytest.fixture
def _unwritable_tempdir(tmp_path, monkeypatch):
    """Point ``tempfile.gettempdir()`` at a path that cannot be created.

    A merely-nonexistent path is not enough on its own: ``Path.mkdir(parents=
    True)`` (what ``_ensure_private_dir`` uses for the deterministic fallback)
    would silently create it. This points at a nonexistent CHILD of a
    directory with no write permission, so neither the deterministic fallback
    (``_ensure_private_dir``) nor the last-resort ``tempfile.mkdtemp()`` (which
    does not create missing parents at all) can create anything there — the
    "system temp directory is missing or unwritable" case from the module
    docstring.
    """
    base = tmp_path / "unwritable-base"
    base.mkdir()
    os.chmod(base, 0o500)
    fake = base / "does-not-exist"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake))
    try:
        yield fake
    finally:
        os.chmod(base, 0o700)  # let pytest's tmp_path cleanup remove it


class TestNoPersistenceMode:
    """The floor: nothing anywhere can be created, and nothing may raise."""

    def test_bootstrap_fallback_dir_returns_none_when_nothing_can_be_created(
        self, _unwritable_tempdir
    ) -> None:
        result_dir, detail = _bootstrap_fallback_dir()  # must not raise

        assert result_dir is None
        assert detail

    def test_construction_never_raises_when_everything_is_unwritable(
        self, tmp_path, _unwritable_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        preferred = blocked / "sub" / "state"

        state = DaemonState(preferred)  # must not raise

        assert state.dir is None
        assert state.persistent is False

    def test_status_reports_no_persistence_and_why(self, tmp_path, _unwritable_tempdir) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        state = DaemonState(blocked / "sub" / "state")

        status = state.status()

        assert status["state_dir"] is None
        assert status["persistence"] == PERSISTENCE_STATUS_UNAVAILABLE
        assert status["persistence_detail"]

    def test_operational_log_writes_are_dropped_and_counted(
        self, tmp_path, _unwritable_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        state = DaemonState(blocked / "sub" / "state")

        state.operational_log.write("heartbeat")  # must not raise

        assert state.operational_log.write_errors
        status = state.status()
        assert status["operational_log"]["write_error_count"] >= 1
        assert status["operational_log"]["path"] is None

    def test_ledger_writes_are_dropped_and_counted(self, tmp_path, _unwritable_tempdir) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        state = DaemonState(blocked / "sub" / "state")

        result = state.ledger.append("some-code", "some detail")  # must not raise

        assert result is None
        assert state.ledger.write_errors

    def test_open_transcript_returns_a_working_dropping_log(
        self, tmp_path, _unwritable_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        state = DaemonState(blocked / "sub" / "state")

        transcript = state.open_transcript("s1")
        transcript.write("user", "hello")  # must not raise

        assert transcript.path is None
        assert transcript.write_errors

    def test_happy_path_is_still_persistent(self, tmp_path) -> None:
        """Sanity check: no-persistence mode is not the new default."""
        state = DaemonState(tmp_path / "state")
        assert state.persistent is True
        assert state.dir is not None


# ── round 3, fix 2: the fallback dir is re-checked after creation (TOCTOU) ──


class TestSecureFallbackDirReChecksAfterCreate:
    def test_a_symlink_swapped_in_right_after_creation_is_caught(
        self, tmp_path, monkeypatch
    ) -> None:
        """The reviewer's scenario: ``_ensure_private_dir`` reports success,
        but by the time control returns, the path is no longer the directory
        it just created — simulated by monkeypatching ``_ensure_private_dir``
        itself to swap in a symlink right after doing its real work."""
        real_ensure = state_mod._ensure_private_dir

        def racy_ensure(path):
            result = real_ensure(path)
            if result is None and path.is_dir() and not path.is_symlink():
                path.rmdir()
                target = path.parent / "elsewhere"
                target.mkdir(exist_ok=True)
                path.symlink_to(target)
            return result

        monkeypatch.setattr(state_mod, "_ensure_private_dir", racy_ensure)

        candidate = tmp_path / "fallback"
        usable, detail = state_mod._secure_fallback_dir(candidate)

        assert usable is False
        assert "symlink" in detail

    def test_a_genuinely_clean_directory_is_still_accepted(self, tmp_path) -> None:
        candidate = tmp_path / "fallback"
        usable, detail = _secure_fallback_dir(candidate)
        assert usable is True
        assert candidate.is_dir()
        assert stat.S_IMODE(candidate.stat().st_mode) == 0o700


# ── round 3, fix 3: orphaned atomic-rewrite temp files are swept on start ───


class TestStaleTempFileSweep:
    def test_construction_removes_its_own_stale_temp_files_and_records_one_degradation(
        self, tmp_path
    ) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / ".embodiment.log.ab12cd34.tmp").write_text("leftover 1")
        (state_dir / ".degradations.jsonl.zz99yy88.tmp").write_text("leftover 2")
        (state_dir / ".gitignore").write_text("an unrelated dotfile, must survive")

        state = DaemonState(state_dir)

        assert not (state_dir / ".embodiment.log.ab12cd34.tmp").exists()
        assert not (state_dir / ".degradations.jsonl.zz99yy88.tmp").exists()
        assert (state_dir / ".gitignore").exists()

        records = state.ledger.read_all()
        matching = [r for r in records if r.code == STALE_TEMP_FILES_SWEPT_CODE]
        assert len(matching) == 1
        assert "2" in matching[0].detail

    def test_sweep_also_covers_the_sessions_directory(self, tmp_path) -> None:
        state_dir = tmp_path / "state"
        sessions_dir = state_dir / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / ".sess-1.jsonl.abcdefgh.tmp").write_text("leftover")

        state = DaemonState(state_dir)

        assert not (sessions_dir / ".sess-1.jsonl.abcdefgh.tmp").exists()
        codes = [r.code for r in state.ledger.read_all()]
        assert STALE_TEMP_FILES_SWEPT_CODE in codes

    def test_no_sweep_degradation_when_nothing_is_stale(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        codes = [r.code for r in state.ledger.read_all()]
        assert STALE_TEMP_FILES_SWEPT_CODE not in codes

    def test_sweep_never_removes_a_directory_or_a_symlink_matching_the_pattern(
        self, tmp_path
    ) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / ".weird.dirlike.tmp").mkdir()
        target = tmp_path / "outside-target.txt"
        target.write_text("do not touch")
        (state_dir / ".linked.name.tmp").symlink_to(target)

        DaemonState(state_dir)

        assert (state_dir / ".weird.dirlike.tmp").is_dir()
        assert (state_dir / ".linked.name.tmp").is_symlink()
        assert target.exists()

    def test_sweep_helper_matches_only_its_own_naming_pattern(self, tmp_path) -> None:
        (tmp_path / ".a.b.tmp").write_text("x")
        (tmp_path / "not-a-tmp-file.txt").write_text("x")
        (tmp_path / ".tmp").write_text("x")  # too short to match the pattern

        removed = _sweep_stale_temp_files(tmp_path)

        assert removed == 1
        assert not (tmp_path / ".a.b.tmp").exists()
        assert (tmp_path / "not-a-tmp-file.txt").exists()
        assert (tmp_path / ".tmp").exists()

    def test_sweep_of_a_nonexistent_directory_is_a_silent_no_op(self, tmp_path) -> None:
        assert _sweep_stale_temp_files(tmp_path / "does-not-exist") == 0


# ── documentation-only note: verified in the docstring, not enforced here ──


class TestOpenTranscriptCaseSensitivityIsDocumented:
    def test_the_docstring_names_the_case_insensitive_filesystem_caveat(self) -> None:
        doc = DaemonState.open_transcript.__doc__ or ""
        assert "case-insensitive" in doc
        assert "daemon" in doc.lower()


# ── round 4, fix 1: append below the bound, rewrite (+ low-water trim) only ─
#    when crossing it


class TestAppendBelowBoundRewriteOnlyWhenCrossing:
    def test_low_water_ratio_is_named_and_between_zero_and_one(self) -> None:
        assert state_mod._LOW_WATER_RATIO == 0.75
        assert 0 < state_mod._LOW_WATER_RATIO < 1

    def test_operational_log_size_never_exceeds_bound_across_a_few_thousand_writes(
        self, tmp_path
    ) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=20_000)
        for i in range(3_000):
            log.write("heartbeat", step=i, note="roughly a ninety byte event payload, more or less")
            assert log.path.stat().st_size <= 20_000

    def test_transcript_log_size_never_exceeds_bound_across_a_few_thousand_writes(
        self, tmp_path
    ) -> None:
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=20_000)
        for i in range(3_000):
            transcript.write("user", f"turn {i} of a long rambling spoken conversation transcript")
            assert transcript.path.stat().st_size <= 20_000

    def test_a_crossing_rewrite_trims_to_the_low_water_mark_not_to_the_bound(
        self, tmp_path, monkeypatch
    ) -> None:
        """The file's FINAL size after N writes can legitimately sit anywhere
        between the low-water mark and the bound (appends resume and grow it
        again after a trim) — what must hold is that EACH rewrite, at the
        moment it happens, trims down to the low-water mark rather than to
        just-under-the-bound. Captured via a spy on the rewrite helper."""
        sizes_after_rewrite: list[int] = []
        real_rewrite = state_mod._bounded_rewrite_append

        def spy(path, *args, **kwargs):
            result = real_rewrite(path, *args, **kwargs)
            sizes_after_rewrite.append(Path(path).stat().st_size)
            return result

        monkeypatch.setattr(state_mod, "_bounded_rewrite_append", spy)

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        for i in range(200):
            log.write("heartbeat", step=i)

        assert sizes_after_rewrite, "expected at least one rewrite over 200 writes at this bound"
        low_water_bytes = int(2_000 * state_mod._LOW_WATER_RATIO)
        assert all(size <= low_water_bytes for size in sizes_after_rewrite)

    def test_records_stay_readable_and_in_order_after_many_crossings(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=5_000)
        for i in range(1_000):
            log.write("heartbeat", step=i)
        records = log.read_all()
        assert records
        assert records[-1]["step"] == 999
        assert log.path.stat().st_size <= 5_000


# ── round 4, fix 2: fsync policy is a named, per-log-type choice ───────────


class TestFsyncPolicyIsNamedPerLogType:
    def test_ledger_fsync_policy_is_true(self) -> None:
        assert LEDGER_FSYNC_PER_APPEND is True

    def test_operational_log_fsync_policy_is_false(self) -> None:
        assert OPERATIONAL_LOG_FSYNC_PER_APPEND is False

    def test_transcript_log_fsync_policy_is_false(self) -> None:
        assert TRANSCRIPT_LOG_FSYNC_PER_APPEND is False

    def test_operational_log_and_transcript_log_instances_expose_the_policy(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=10_000)
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=10_000)
        assert log.fsync is False
        assert transcript.fsync is False

    def test_ledger_append_calls_fsync_every_time(self, tmp_path, monkeypatch) -> None:
        calls = {"n": 0}
        real_fsync = os.fsync

        def counting_fsync(fd):
            calls["n"] += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        ledger = DegradationLedger(tmp_path / "degradations.jsonl")
        for i in range(10):
            ledger.append("some-code", f"detail {i}")
        assert calls["n"] == 10

    def test_operational_log_append_never_calls_fsync_below_the_bound(
        self, tmp_path, monkeypatch
    ) -> None:
        calls = {"n": 0}
        real_fsync = os.fsync

        def counting_fsync(fd):
            calls["n"] += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        # A bound large enough that every write below is a plain append
        # (never a rewrite, whose own atomic-rename primitive fsyncs the
        # temp file for a different, orthogonal reason).
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=1_000_000)
        for i in range(10):
            log.write("heartbeat", step=i)
        assert calls["n"] == 0

    def test_transcript_log_append_never_calls_fsync_below_the_bound(
        self, tmp_path, monkeypatch
    ) -> None:
        calls = {"n": 0}
        real_fsync = os.fsync

        def counting_fsync(fd):
            calls["n"] += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=1_000_000)
        for i in range(10):
            transcript.write("user", f"turn {i}")
        assert calls["n"] == 0


# ── round 4, fix 3: a torn last line is tolerated, and never glued to ──────


class TestTornLastLineIsToleratedAndNeverGluedTo:
    @staticmethod
    def _corrupt_with_torn_line(path, fragment: str) -> None:
        """Simulates a crash mid-write: a fragment with no trailing newline."""
        with open(path, "a", encoding="utf-8") as handle:  # noqa: PTH123
            handle.write(fragment)

    def test_ledger_next_append_after_a_torn_line_starts_fresh(self, tmp_path) -> None:
        ledger = DegradationLedger(tmp_path / "degradations.jsonl")
        ledger.append("first", "one")
        ledger.append("second", "two")
        self._corrupt_with_torn_line(ledger.path, '{"ts": 1.0, "code": "torn-by-a-cra')

        ledger.append("third", "three")

        codes = [r.code for r in ledger.read_all()]
        assert codes == ["first", "second", "third"]

    def test_operational_log_next_append_after_a_torn_line_starts_fresh(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=1_000_000)
        log.write("first")
        log.write("second")
        self._corrupt_with_torn_line(log.path, '{"ts": 1.0, "event": "torn-by-a-cra')

        log.write("third")

        events = [r["event"] for r in log.read_all()]
        assert events == ["first", "second", "third"]

    def test_transcript_log_next_append_after_a_torn_line_starts_fresh(self, tmp_path) -> None:
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=1_000_000)
        transcript.write("user", "first")
        transcript.write("assistant", "second")
        self._corrupt_with_torn_line(transcript.path, '{"ts": 1.0, "role": "torn-by-a-cra')

        transcript.write("user", "third")

        texts = [r["text"] for r in transcript.read_all()]
        assert texts == ["first", "second", "third"]

    def test_a_torn_line_is_also_not_glued_to_across_a_crossing_rewrite(self, tmp_path) -> None:
        """The crossing-the-bound rewrite reads existing content directly
        (not through _append_line) so it needs the same guard independently.

        Bound of 150 (not 80): round 5's oversize handling now rejects a
        single record whose own encoded size exceeds max_bytes before it
        ever reaches the rewrite path, and the crossing write below
        (~93 bytes) exceeded an 80-byte bound on its own — this test needs a
        genuine crossing, not an oversize rejection.
        """
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=150)
        log.write("first")
        self._corrupt_with_torn_line(log.path, '{"ts": 1.0, "event": "torn-by-a-cra')

        log.write("this write is long enough to force a crossing rewrite")

        events = [r["event"] for r in log.read_all()]
        assert events[-1] == "this write is long enough to force a crossing rewrite"
        assert "torn-by-a-cra" not in " ".join(events)

    def test_last_byte_probe(self, tmp_path) -> None:
        path = tmp_path / "probe.jsonl"
        assert _last_byte_is_newline_or_empty(path) is True  # does not exist yet
        path.write_text("")
        assert _last_byte_is_newline_or_empty(path) is True  # empty
        path.write_text("no trailing newline")
        assert _last_byte_is_newline_or_empty(path) is False
        path.write_text("has a trailing newline\n")
        assert _last_byte_is_newline_or_empty(path) is True


# ── round 4, fix 4: concurrent appends never interleave or lose a record ───


class TestConcurrentAppendsNeverInterleaveOrLoseARecord:
    def test_many_threads_appending_below_the_bound_produce_an_exact_count(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=10_000_000)
        n_threads, n_per_thread = 8, 200

        def worker(thread_id: int) -> None:
            for i in range(n_per_thread):
                log.write("heartbeat", thread=thread_id, i=i)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        assert all(not th.is_alive() for th in threads)

        records = log.read_all()
        assert len(records) == n_threads * n_per_thread
        seen = {(r["thread"], r["i"]) for r in records}
        assert len(seen) == n_threads * n_per_thread  # no duplicate, none corrupted-and-dropped

    def test_many_threads_appending_to_the_ledger_produce_an_exact_count(self, tmp_path) -> None:
        ledger = DegradationLedger(tmp_path / "degradations.jsonl")
        n_threads, n_per_thread = 8, 100

        def worker(thread_id: int) -> None:
            for i in range(n_per_thread):
                ledger.append(f"code-{thread_id}", str(i))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        assert all(not th.is_alive() for th in threads)

        assert len(ledger.read_all()) == n_threads * n_per_thread

    def test_many_threads_appending_across_a_tight_bound_never_corrupt_a_line(
        self, tmp_path
    ) -> None:
        """A tight bound forces frequent rewrites under concurrent load.
        Trimming legitimately drops old records under load, so an exact
        SURVIVING count is not asserted here — only that every surviving
        line still parses (nothing interleaved), the bound still holds, and
        (round 5) the eviction counter is EXACTLY consistent with what
        actually survived, proving the counters are not racy under
        concurrent writers even though they are plain ``int`` attributes
        (correctness comes from the per-instance lock already held for the
        whole decide-then-write, the same lock these counters are mutated
        under).
        """
        import json as jsonlib

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=3_000)
        n_threads, n_per_thread = 6, 150

        def worker(thread_id: int) -> None:
            for i in range(n_per_thread):
                log.write("heartbeat", thread=thread_id, i=i)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
        assert all(not th.is_alive() for th in threads)

        raw = log.path.read_text(encoding="utf-8")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        for ln in lines:
            jsonlib.loads(ln)  # must not raise — proves no interleaved/corrupted line
        assert log.path.stat().st_size <= 3_000

        # round 5: the eviction/rewrite counters, exact even under 6 threads.
        total_written = n_threads * n_per_thread
        assert log.rewrites > 0
        assert log.oversize_records == 0  # every record here comfortably fits alone
        assert log.evicted_records == total_written - len(lines)


# ── round 4, fix 5: a performance regression test that asserts operation ───
#    counts, not wall-clock, so it cannot flake


class TestRewriteHelperCallCountRegressionGuard:
    @staticmethod
    def _spy_on_rewrite(monkeypatch) -> dict:
        calls = {"n": 0}
        real_rewrite = state_mod._bounded_rewrite_append

        def counting_rewrite(*args, **kwargs):
            calls["n"] += 1
            return real_rewrite(*args, **kwargs)

        monkeypatch.setattr(state_mod, "_bounded_rewrite_append", counting_rewrite)
        return calls

    def test_zero_rewrites_for_writes_that_stay_under_the_bound(
        self, tmp_path, monkeypatch
    ) -> None:
        calls = self._spy_on_rewrite(monkeypatch)

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=1_000_000)
        for i in range(300):
            log.write("heartbeat", step=i, note="roughly a ninety byte event payload, more or less")

        assert calls["n"] == 0

    def test_a_small_number_of_rewrites_for_writes_that_repeatedly_cross_the_bound(
        self, tmp_path, monkeypatch
    ) -> None:
        calls = self._spy_on_rewrite(monkeypatch)

        n_writes = 2_000
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=20_000)
        for i in range(n_writes):
            log.write("heartbeat", step=i, note="roughly a ninety byte event payload, more or less")

        # Measured empirically at 45 for this exact scenario; asserted with
        # generous margin (10x) so the test is robust to minor record-size
        # or JSON-formatting drift while still failing hard on a regression
        # back to "one rewrite per write" (which would be 2000, not < 200).
        assert 0 < calls["n"] < n_writes // 10

    def test_a_write_that_crosses_the_bound_rewrites_exactly_once(
        self, tmp_path, monkeypatch
    ) -> None:
        """Updated for round 5's oversize handling: a record whose own
        encoded size ALONE exceeds max_bytes is now never written and never
        reaches the rewrite helper at all (see
        TestOversizeRecordsAreNeverWrittenCountedAndDegradeOnce) — so this
        test crosses the bound with two writes, each individually under
        max_bytes, whose combined size is what forces exactly one rewrite.
        """
        calls = self._spy_on_rewrite(monkeypatch)

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=100)
        log.write("first")  # 45 bytes on disk — comfortably under 100 alone
        log.write("this second write is long enough to cross the tiny bound")  # 96 alone

        assert calls["n"] == 1


# ── round 5, fix: a bounded buffer counts what it drops ────────────────────


class TestEvictionRewriteCountersAreExactAndVisible:
    """`` evicted_records`` / ``rewrites`` — pre-existing gap, not new to round
    4: a trim silently dropped records and nothing anywhere recorded it.
    """

    def test_zero_evictions_and_zero_rewrites_when_nothing_is_ever_trimmed(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=1_000_000)
        for i in range(200):
            log.write("heartbeat", step=i)
        assert log.evicted_records == 0
        assert log.rewrites == 0

    def test_evicted_records_equals_written_minus_currently_present(self, tmp_path) -> None:
        """The exact, algorithm-independent invariant: nothing but eviction
        ever removes a record once it is written (this scenario keeps every
        record comfortably under max_bytes individually, so none is ever
        oversize-rejected either) — so after N writes, the running eviction
        count must equal N minus however many records currently survive on
        disk, regardless of exactly when or how many each individual rewrite
        dropped. Proven across "a known sequence of crossings": max_bytes is
        small enough that this triggers many rewrites over 400 writes.
        """
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        n_writes = 400
        for i in range(n_writes):
            log.write("heartbeat", step=i, note="a fixed-shape note to force several crossings")

        assert log.rewrites > 0
        assert log.oversize_records == 0
        surviving = len(log.read_all())
        assert log.evicted_records == n_writes - surviving

    def test_rewrites_counts_exactly_how_many_times_the_rewrite_helper_ran(
        self, tmp_path, monkeypatch
    ) -> None:
        real_rewrite = state_mod._bounded_rewrite_append
        spy_calls = {"n": 0}

        def counting(*args, **kwargs):
            spy_calls["n"] += 1
            return real_rewrite(*args, **kwargs)

        monkeypatch.setattr(state_mod, "_bounded_rewrite_append", counting)

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        for i in range(400):
            log.write("heartbeat", step=i, note="a fixed-shape note to force several crossings")

        assert log.rewrites == spy_calls["n"]
        assert log.rewrites > 0

    def test_transcript_log_gets_the_same_counters_one_shared_implementation(
        self, tmp_path
    ) -> None:
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=2_000)
        n_writes = 400
        for i in range(n_writes):
            transcript.write("user", f"turn {i} of a rambling conversation about nothing much")

        assert transcript.rewrites > 0
        surviving = len(transcript.read_all())
        assert transcript.evicted_records == n_writes - surviving

    def test_counters_appear_in_the_bounded_logs_own_status(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        for i in range(400):
            log.write("heartbeat", step=i, note="a fixed-shape note to force several crossings")

        status = log.status()
        assert status["rewrites"] == log.rewrites
        assert status["evicted_records"] == log.evicted_records
        assert status["oversize_records"] == log.oversize_records

    def test_counters_appear_in_daemon_state_status(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state", operational_log_max_bytes=2_000)
        for i in range(400):
            state.operational_log.write(
                "heartbeat", step=i, note="a fixed-shape note to force several crossings"
            )

        status = state.status()
        ol = status["operational_log"]
        assert ol["rewrites"] > 0
        assert ol["evicted_records"] > 0
        assert ol["oversize_records"] == 0

    def test_counters_appear_in_daemon_state_status_even_in_no_persistence_mode(
        self, tmp_path, _unwritable_tempdir
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        state = DaemonState(blocked / "sub" / "state")

        status = state.status()

        ol = status["operational_log"]
        assert ol["evicted_records"] == 0
        assert ol["rewrites"] == 0
        assert ol["oversize_records"] == 0

    def test_counters_are_never_persisted_to_disk(self, tmp_path) -> None:
        """In-memory only, as instructed — a fresh instance over the SAME
        file starts back at zero even though the file itself still carries
        the trimming history the old instance produced."""
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        for i in range(400):
            log.write("heartbeat", step=i, note="a fixed-shape note to force several crossings")
        assert log.evicted_records > 0

        reopened = OperationalLog(tmp_path / "embodiment.log", max_bytes=2_000)
        assert reopened.evicted_records == 0
        assert reopened.rewrites == 0
        assert reopened.oversize_records == 0


class TestOversizeRecordsAreNeverWrittenCountedAndDegradeOnce:
    def test_an_oversize_record_is_never_written(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=200)
        log.write("heartbeat", note="x" * 5_000)  # far larger than the 200-byte bound

        assert log.read_all() == []
        assert not log.path.exists() or log.path.stat().st_size == 0

    def test_an_oversize_record_never_grows_an_existing_file(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=200)
        log.write("heartbeat", step=1)
        size_before = log.path.stat().st_size

        log.write("heartbeat", note="x" * 5_000)

        assert log.path.stat().st_size == size_before
        assert [r["step"] for r in log.read_all() if "step" in r] == [1]

    def test_an_oversize_record_is_counted_every_time_it_happens(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=200)
        for _ in range(5):
            log.write("heartbeat", note="x" * 5_000)
        assert log.oversize_records == 5

    def test_an_oversize_record_degrades_exactly_once_per_log_instance(self, tmp_path) -> None:
        degradations: list[tuple[str, str]] = []
        log = OperationalLog(
            tmp_path / "embodiment.log",
            max_bytes=200,
            on_degrade=lambda code, detail: degradations.append((code, detail)),
        )
        for _ in range(7):
            log.write("heartbeat", note="x" * 5_000)

        oversize = [d for d in degradations if d[0] == OVERSIZE_RECORD_CODE]
        assert len(oversize) == 1
        assert log.oversize_records == 7

    def test_a_second_oversize_size_still_only_degrades_once_even_if_different(
        self, tmp_path
    ) -> None:
        degradations: list[tuple[str, str]] = []
        log = OperationalLog(
            tmp_path / "embodiment.log",
            max_bytes=200,
            on_degrade=lambda code, detail: degradations.append((code, detail)),
        )
        log.write("heartbeat", note="x" * 5_000)
        log.write("heartbeat", note="y" * 9_000)  # a different, also-oversize record

        oversize = [d for d in degradations if d[0] == OVERSIZE_RECORD_CODE]
        assert len(oversize) == 1
        assert log.oversize_records == 2

    def test_degradation_detail_carries_only_the_byte_count_and_bound(self, tmp_path) -> None:
        degradations: list[tuple[str, str]] = []
        marker = "the-secret-transcript-text-must-never-appear-anywhere"
        transcript = TranscriptLog(
            tmp_path / "sess.jsonl",
            max_bytes=100,
            on_degrade=lambda code, detail: degradations.append((code, detail)),
        )
        transcript.write("user", marker * 20)  # far over the 100-byte bound

        assert degradations
        code, detail = degradations[0]
        assert code == OVERSIZE_RECORD_CODE
        assert marker not in detail
        assert "100" in detail  # the configured bound
        assert any(ch.isdigit() for ch in detail.replace("100", ""))  # the byte count too

    def test_marker_text_never_reaches_status_write_errors_or_the_file(self, tmp_path) -> None:
        marker = "the-secret-transcript-text-must-never-appear-anywhere"
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=100)
        transcript.write("user", marker * 20)

        assert marker not in str(transcript.status())
        assert marker not in " ".join(transcript.write_errors)
        assert not transcript.path.exists() or marker not in transcript.path.read_text(
            encoding="utf-8"
        )

    def test_a_record_exactly_at_the_bound_is_not_oversize(self, tmp_path) -> None:
        """The oversize gate is ``>``, not ``>=`` — a record that exactly
        fills the bound is legitimate, not rejected. Captures one record's
        EXACT serialized bytes and replays them through the internal helper
        directly (bypassing ``write()``, which would mint a fresh timestamp
        of possibly different length and make the byte count non-
        deterministic) so the boundary check itself is exact, not timing-
        dependent.
        """
        probe = OperationalLog(tmp_path / "probe.log", max_bytes=1_000_000)
        probe.write("first")
        exact_line = probe.path.read_text(encoding="utf-8").rstrip("\n")
        exact_size = len(exact_line.encode("utf-8")) + 1

        log = OperationalLog(tmp_path / "embodiment.log", max_bytes=exact_size)
        with log._lock:
            log._append_or_rewrite(exact_line)

        assert log.oversize_records == 0
        assert len(log.read_all()) == 1


# ── round 6, fix 1: one lock per PATH, process-wide; cached transcripts ────


class TestOnePathOneLockAndCachedTranscripts:
    def test_open_transcript_twice_returns_the_same_object(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        a = state.open_transcript("s")
        b = state.open_transcript("s")
        assert a is b

    def test_a_different_max_bytes_on_the_second_call_is_ignored(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        a = state.open_transcript("s", max_bytes=1_000)
        b = state.open_transcript("s", max_bytes=5_000)
        assert a is b
        assert a.max_bytes == 1_000

    def test_different_session_ids_get_different_objects(self, tmp_path) -> None:
        state = DaemonState(tmp_path / "state")
        a = state.open_transcript("s1")
        b = state.open_transcript("s2")
        assert a is not b
        assert a.path != b.path

    def test_two_raw_transcript_log_instances_on_one_path_share_one_lock(self, tmp_path) -> None:
        path = tmp_path / "shared.jsonl"
        a = TranscriptLog(path, max_bytes=3_000)
        b = TranscriptLog(path, max_bytes=3_000)
        assert a is not b
        assert a._lock is b._lock

    def test_two_daemon_states_on_the_same_dir_share_the_ledger_and_log_locks(
        self, tmp_path
    ) -> None:
        d = tmp_path / "state"
        s1 = DaemonState(d)
        s2 = DaemonState(d)
        assert s1.ledger._lock is s2.ledger._lock
        assert s1.operational_log._lock is s2.operational_log._lock

    def test_the_probes_scenario_via_cached_open_transcript_exact_accounting(
        self, tmp_path
    ) -> None:
        """Reproduces the reviewers' probe exactly (two open_transcript calls
        for the same session id, two threads each writing 400 records over a
        3 kB bound) through the public API. With caching, ``a is b``, so
        there is only ONE object's own counters to reason about — and the
        exact invariant holds: records currently on disk plus everything
        that object's own eviction count claims must equal everything
        written, with the file never exceeding its bound.
        """
        state = DaemonState(tmp_path / "state")
        a = state.open_transcript("s", max_bytes=3_000)
        b = state.open_transcript("s", max_bytes=3_000)
        assert a is b

        def worker(key: str) -> None:
            for i in range(400):
                a.write("user", f"{key}-{i} " + "z" * 30)

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
        assert not t1.is_alive() and not t2.is_alive()

        assert a.path.stat().st_size <= 3_000
        records = a.read_all()
        written = 800
        assert len(records) + a.evicted_records == written

    def test_two_raw_instances_on_one_path_share_a_lock_and_never_corrupt(self, tmp_path) -> None:
        """Even bypassing DaemonState's caching — constructing TranscriptLog
        directly twice on the same path, as the reviewers' probe originally
        did — the shared per-path lock guarantees no byte-level corruption
        and the bound is never exceeded. Two SEPARATE Python objects' own
        eviction counters are independently kept and are not expected to sum
        to a meaningful total across two different objects (that
        attribution problem is exactly what caching removes — see the test
        above, which IS exact because there is only one object there).
        """
        path = tmp_path / "shared.jsonl"
        a = TranscriptLog(path, max_bytes=3_000)
        b = TranscriptLog(path, max_bytes=3_000)

        def worker(log: TranscriptLog, key: str) -> None:
            for i in range(400):
                log.write("user", f"{key}-{i} " + "z" * 30)

        t1 = threading.Thread(target=worker, args=(a, "A"))
        t2 = threading.Thread(target=worker, args=(b, "B"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
        assert not t1.is_alive() and not t2.is_alive()

        raw = path.read_text(encoding="utf-8")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        for ln in lines:
            json.loads(ln)  # must not raise — no interleaved/corrupted line
        assert path.stat().st_size <= 3_000


# ── round 6, fix 2: the torn-tail prefix byte counts toward the bound ──────


class TestTornTailPrefixByteCountsTowardTheBound:
    def test_prefix_byte_never_pushes_the_file_over_the_bound(self, tmp_path) -> None:
        """Deterministic, byte-exact version of the reviewers' finding: a
        torn 1-byte tail plus a line chosen so existing+line alone lands
        EXACTLY at max_bytes — which only fits if the prefix byte the write
        must also add is (wrongly) not counted. The fixed code must instead
        take the rewrite path and keep the file within bound.
        """
        path = tmp_path / "op.log"
        path.write_bytes(b"{")  # a 1-byte torn fragment, no trailing newline

        line = "x" * 10
        encoded_len = len(line.encode("utf-8")) + 1  # + this line's own newline
        max_bytes = 1 + encoded_len  # existing(1) + line_with_newline — NO prefix counted

        log = OperationalLog(path, max_bytes=max_bytes)
        with log._lock:
            log._append_or_rewrite(line)

        assert log.path.stat().st_size <= max_bytes

    def test_prefix_byte_never_pushes_over_the_bound_across_many_record_sizes(
        self, tmp_path
    ) -> None:
        """A sweep instead of hunting for one exact boundary value (which the
        reviewers' probe does and which can miss depending on timestamp
        digit width) — the invariant must hold for every size, not just one
        lucky hit.
        """
        for n in range(1, 100, 7):
            path = tmp_path / f"op-{n}.log"
            log = OperationalLog(path, max_bytes=200)
            log.write("seed")
            with open(path, "ab") as fh:
                fh.write(b"{")  # torn, no trailing newline
            log.write("y" * n)
            assert log.path.stat().st_size <= 200


# ── round 6, fix 3: an unreadable tail is treated as dirty, never as clean ─


class TestUnreadableTailIsTreatedAsDirty:
    def test_0200_torn_tail_does_not_glue_the_new_record(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("first")
        with open(log.path, "ab") as fh:
            fh.write(b'{"torn')  # no trailing newline
        os.chmod(log.path, 0o200)  # write-only: the tail probe cannot read it
        try:
            log.write("second")
        finally:
            os.chmod(log.path, 0o600)

        events = [r["event"] for r in log.read_all()]
        assert "second" in events

    def test_probe_error_is_counted(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("first")
        with open(log.path, "ab") as fh:
            fh.write(b'{"torn')
        os.chmod(log.path, 0o200)
        try:
            log.write("second")
        finally:
            os.chmod(log.path, 0o600)

        assert log.probe_errors >= 1
        assert log.status()["probe_errors"] >= 1

    def test_probe_tail_directly_treats_a_read_error_as_dirty_and_reports_it(
        self, tmp_path
    ) -> None:
        path = tmp_path / "unreadable.jsonl"
        path.write_bytes(b"no newline here")
        os.chmod(path, 0o200)
        try:
            needs_prefix, probe_error = state_mod._probe_tail(path)
        finally:
            os.chmod(path, 0o600)
        assert needs_prefix is True
        assert probe_error is True

    def test_probe_tail_on_a_nonexistent_file_is_not_an_error(self, tmp_path) -> None:
        needs_prefix, probe_error = state_mod._probe_tail(tmp_path / "does-not-exist")
        assert needs_prefix is False
        assert probe_error is False

    def test_last_byte_wrapper_returns_false_on_a_read_error(self, tmp_path) -> None:
        """The thin boolean wrapper: an unreadable tail is no longer read as
        "confirmed clean" — the old, buggy default this round replaces."""
        path = tmp_path / "unreadable.jsonl"
        path.write_bytes(b"content\n")
        os.chmod(path, 0o200)
        try:
            result = _last_byte_is_newline_or_empty(path)
        finally:
            os.chmod(path, 0o600)
        assert result is False


# ── round 6, fix 4: a trimmed torn fragment is not an evicted record ───────


class TestTrimmedFragmentIsNotAnEvictedRecord:
    def test_trim_classifies_a_popped_fragment_separately_from_a_real_record(
        self, tmp_path
    ) -> None:
        """Hand-constructed, byte-exact content: one real JSON record
        followed by a torn fragment (no trailing newline) — exactly what an
        interrupted earlier write leaves sitting at the front of the file.
        A crossing trim forced small enough to drop both must count them
        separately.
        """
        path = tmp_path / "op.log"
        path.write_bytes(b'{"event": "first"}\n{"torn')

        dropped_records, dropped_fragments = state_mod._bounded_rewrite_append(
            path, '{"event": "second"}', max_bytes=40, low_water_bytes=20
        )

        assert dropped_records == 1
        assert dropped_fragments == 1
        assert '"second"' in path.read_text(encoding="utf-8")

    def test_fragments_dropped_is_visible_via_the_public_write_api(self, tmp_path) -> None:
        """max_bytes=150 (not a tighter bound): round 6's oversize handling
        rejects a single record whose OWN encoded size exceeds max_bytes
        before it ever reaches the rewrite path (see
        TestOversizeRecordsAreNeverWrittenCountedAndDegradeOnce), so the
        crossing write here must stay individually under the bound while
        still combining with the existing content to cross it.
        """
        log = OperationalLog(tmp_path / "op.log", max_bytes=150)
        log.write("first")
        with open(log.path, "ab") as fh:
            fh.write(b'{"event": "torn-fragment-that-will-never-parse')  # no newline
        # Individually under 150 bytes; combined with what's already on
        # disk (the real "first" record plus the torn fragment), over it.
        log.write("y" * 40)

        assert log.fragments_dropped >= 1
        status = log.status()
        assert status["fragments_dropped"] == log.fragments_dropped

    def test_fragments_dropped_is_zero_when_nothing_is_ever_torn(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=2_000)
        for i in range(400):
            log.write("heartbeat", step=i, note="a fixed-shape note to force several crossings")
        assert log.rewrites > 0
        assert log.fragments_dropped == 0


# ── round 6, fix 5: a non-serializable field degrades, never raises ────────


class TestNonSerializableFieldNeverRaisesOutOfWrite:
    def test_write_with_a_bytes_field_does_not_raise(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("evt", raw=b"x")  # must not raise

    def test_the_record_is_dropped_not_written(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("evt", raw=b"x")
        assert log.read_all() == []

    def test_write_error_names_the_field_and_its_type(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("evt", raw=b"x")
        assert log.write_errors
        assert "raw" in log.write_errors[0]
        assert "bytes" in log.write_errors[0]

    def test_write_error_never_carries_a_repr_of_the_value(self, tmp_path) -> None:
        marker = "super-secret-value-content-xyz"

        class Weird:
            def __repr__(self) -> str:
                return marker

        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("evt", odd=Weird())

        assert log.write_errors
        assert marker not in log.write_errors[0]

    def test_other_writes_still_work_after_a_serialization_failure(self, tmp_path) -> None:
        log = OperationalLog(tmp_path / "op.log", max_bytes=10**6)
        log.write("evt", raw=b"x")
        log.write("evt", step=1)
        records = log.read_all()
        assert len(records) == 1
        assert records[0]["step"] == 1

    def test_transcript_log_shares_the_same_code_path(self, tmp_path) -> None:
        transcript = TranscriptLog(tmp_path / "sess.jsonl", max_bytes=10**6)
        transcript._write({"role": "user", "text": "ok", "bad": object()})
        assert transcript.write_errors
        assert transcript.read_all() == []


# ── round 6, fix 6: on_degrade never runs while a write lock is held ───────


class TestOnDegradeNeverRunsUnderTheLock:
    def test_a_hook_that_writes_back_to_the_same_log_does_not_deadlock(self, tmp_path) -> None:
        holder: dict[str, OperationalLog] = {}

        def hook(code: str, detail: str) -> None:
            holder["log"].write("evt", x="y")

        log = OperationalLog(tmp_path / "op.log", max_bytes=100, on_degrade=hook)
        holder["log"] = log

        done = threading.Event()

        def go() -> None:
            log.write("evt", big="q" * 500)  # triggers oversize -> degrade
            done.set()

        threading.Thread(target=go, daemon=True).start()
        finished = done.wait(5)
        assert finished, "write() did not return - the hook likely deadlocked"

    def test_the_hooks_own_write_actually_lands(self, tmp_path) -> None:
        """Deliberately run through a bounded background thread, exactly
        like the sibling test above — calling ``log.write()`` directly on
        the main test thread here would, against the PRE-FIX code, deadlock
        the main thread itself with no timeout at all and hang the whole
        test run rather than just this one test failing. (Caught during
        this round's own red/green check: the first version of this test
        called ``log.write()`` unguarded and hung pytest indefinitely
        against the reverted, pre-fix module.)
        """
        holder: dict[str, OperationalLog] = {}
        calls: list[tuple[str, str]] = []

        def hook(code: str, detail: str) -> None:
            calls.append((code, detail))
            holder["log"].write("evt", from_hook=True)

        log = OperationalLog(tmp_path / "op.log", max_bytes=100, on_degrade=hook)
        holder["log"] = log

        done = threading.Event()

        def go() -> None:
            log.write("evt", big="q" * 500)
            done.set()

        threading.Thread(target=go, daemon=True).start()
        finished = done.wait(5)
        assert finished, "write() did not return - the hook likely deadlocked"
        assert calls
        assert any(r.get("from_hook") for r in log.read_all())

    def test_the_write_failure_hook_site_also_never_deadlocks(self, tmp_path) -> None:
        """The OTHER pre-existing hook site (an OSError write failure): both
        now share one call pattern, so this must be safe too."""
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file")
        holder: dict[str, OperationalLog] = {}

        def hook(code: str, detail: str) -> None:
            holder["log2"].write("evt", noted=True)

        log2 = OperationalLog(tmp_path / "ok.log", max_bytes=10**6)
        log = OperationalLog(blocked / "sub" / "op.log", max_bytes=10**6, on_degrade=hook)
        holder["log2"] = log2

        done = threading.Event()

        def go() -> None:
            log.write("evt")  # parent dir can't be created -> OSError -> degrade
            done.set()

        threading.Thread(target=go, daemon=True).start()
        assert done.wait(5), "write() did not return - the hook likely deadlocked"
        assert log2.read_all()
