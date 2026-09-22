"""Tests for :mod:`embodiment.daemon.lifecycle` (plan task t5).

Organised around the task's three verbatim acceptance criteria:

1. double ``start`` is idempotent, ``stop`` returns within a bounded time with
   a reader thread parked in a blocking call, and ``status`` reports
   ``dead (unclean)`` with the last ledger entry against a stale pidfile
2. ``stop`` then ``start`` each complete in under 5 s in a test with fake
   components
3. ``teken cli doctor . --strict`` and ``tests/test_cli_introspection.py``
   pass with the three verbs registered (criterion 3 lives in
   ``tests/test_cli_lifecycle.py``, which is where the CLI surface is proved)

Real processes, never a mock of one
-----------------------------------
Criterion 1 names a *thread parked in a blocking call* and a *stale pidfile*.
Neither is provable against a fake: a mocked ``Popen`` cannot park on
``os.read``, and a mocked liveness check cannot prove pid reuse was handled.
So every lifecycle test here spawns a real detached child interpreter running
a fake target module written into ``tmp_path``, and every process is reaped by
the fixture even when an assertion fails.

Isolation
---------
Every test points ``EMBODIMENT_STATE_DIR`` at a ``tmp_path`` directory and
redirects ``tempfile`` — ``gettempdir``, the ``tempdir`` attribute **and**
``TMPDIR`` in the environment — so t4's deterministic per-uid fallback
directory (``<tempdir>/embodiment-state-<uid>``) resolves inside ``tmp_path``
in this process *and* in every spawned child. That directory is machine-global
and is where a real daemon lives; a test that writes into it is writing into
the operator's running system, and one that did left a stale 'running' pidfile
that every later ``embodiment status`` on the box read.
:class:`TestNoTestTouchesTheRealFallbackDir` holds that line.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess  # nosec B404 - fixed argv, no shell; spawns a dead pid for the stale test
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

import embodiment.daemon.lifecycle as lifecycle_mod
from embodiment.daemon.lifecycle import (
    CHILD_EXITED_EARLY_CODE,
    DAEMON_STDERR_NAME,
    DEFAULT_KILL_GRACE,
    DEFAULT_SHUTDOWN_DEADLINE,
    DEFAULT_STOP_TIMEOUT,
    DEFAULT_TARGET,
    HARD_EXIT_CODE,
    PIDFILE_NAME,
    REFUSED_PID_CODE,
    STALE_PIDFILE_RECLAIMED_CODE,
    STATE_DEAD_UNCLEAN,
    STATE_RUNNING,
    STATE_STOPPED,
    STATE_UNAVAILABLE,
    STOP_ESCALATED_CODE,
    TARGET_UNAVAILABLE_CODE,
    THREADS_LINGERING_CODE,
    DaemonRunner,
    PidFile,
    resolve_target,
    run_daemon,
    start,
    status,
    stop,
)
from embodiment.daemon.state import (
    LEDGER_FILENAME,
    STATE_DIR_ENV_VAR,
    DaemonState,
    resolve_fallback_state_dir,
)

# ── fake targets, as real modules a real child interpreter imports ───────────

#: Waits for the stop event and returns cleanly — the happy path.
IDLE_TARGET = """
class App:
    def run(self, stop_event):
        stop_event.wait()
        return 0


def main():
    return App()
"""

#: A NON-daemon thread parked forever on a blocking read, and a main thread
#: parked the same way. PEP 475 restarts an interrupted ``os.read``, so the
#: signal handler alone can never unpark either one: only the watchdog's hard
#: exit ends this process. This is criterion 1's "reader thread parked in a
#: blocking call", reproduced rather than simulated.
PARKED_TARGET = """
import os
import threading


def _park(read_fd):
    os.read(read_fd, 1)  # nothing is ever written to the other end


class App:
    def run(self, stop_event):
        r1, _w1 = os.pipe()
        threading.Thread(target=_park, args=(r1,), daemon=False, name="parked-reader").start()
        r2, _w2 = os.pipe()
        os.read(r2, 1)
        return 0


def main():
    return App()
"""

#: Ignores SIGTERM outright, so the graceful path never runs and ``stop`` must
#: escalate to SIGKILL to keep its bound.
DEAF_TARGET = """
import signal
import time


class App:
    def run(self, stop_event):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(0.05)


def main():
    return App()
"""

#: Raises at import, so the child dies before it can take the pidfile record to
#: ``running``.
BROKEN_TARGET = """
raise RuntimeError("this target explodes at import")
"""

#: A target module that genuinely does not exist, for the tests that need
#: ``start`` to refuse. Under ``embodiment.daemon`` so the refusal is about the
#: *module* being absent rather than about an unimportable parent package.
MISSING_TARGET = "embodiment.daemon.nope:main"


#: The REAL machine-wide fallback directory, captured at import time — before
#: any fixture repoints ``tempfile`` — so a test can prove it was never touched.
#: It is machine-global (``<tempdir>/embodiment-state-<uid>``, t4) and is where
#: a real daemon would live, so a test that writes into it is writing into the
#: operator's running system. Never delete it from a test either: a stale one is
#: evidence of which suite is still misbehaving.
REAL_FALLBACK_DIR = Path(tempfile.gettempdir()) / f"embodiment-state-{os.getuid()}"


def _snapshot(path: Path) -> object:
    """Everything about *path* that a write would change. Never raises."""
    try:
        entries = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in path.iterdir())
        return (True, path.stat().st_mtime_ns, entries)
    except OSError:
        return (False, None, [])


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A per-test state dir, and a per-test tempdir for t4's fallback path.

    ``tempfile`` is repointed three ways on purpose. ``gettempdir`` covers this
    process; ``tempfile.tempdir`` covers anything that reads the module
    attribute directly; and ``TMPDIR`` covers **spawned children**, which
    compute their own fallback in their own interpreter and would otherwise
    land in the machine-wide :data:`REAL_FALLBACK_DIR`.
    """
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    monkeypatch.setattr(tempfile, "tempdir", str(fake_tmp), raising=False)
    monkeypatch.setenv("TMPDIR", str(fake_tmp))
    state = tmp_path / "state"
    monkeypatch.setenv(STATE_DIR_ENV_VAR, str(state))
    return state


@pytest.fixture(autouse=True)
def _never_spawn_the_real_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may start the REAL daemon application.

    Three tests here and in ``test_cli_lifecycle.py`` used ``start()`` with no
    target as a convenient way to exercise "the target module does not exist",
    because ``embodiment.daemon.app`` did not exist yet. When t15 built it,
    those tests quietly turned into tests that **launch a real detached daemon
    on every suite run and never stop it** — three had to be killed by hand.

    No assertion could have caught that, because nothing about the tests
    changed. So the guard is structural and lives at the choke point: any call
    to :func:`~embodiment.daemon.lifecycle.start` that would use
    :data:`DEFAULT_TARGET` fails the test instead of spawning. Both bindings
    are patched — the module attribute the CLI reaches through
    ``lifecycle.start(...)``, and this module's own imported ``start`` name,
    which a plain ``monkeypatch.setattr`` on the module would not touch.
    Patching ``DEFAULT_TARGET`` alone would not do it either: ``start``'s own
    default argument was bound at definition time.
    """
    real_start = lifecycle_mod.start

    def guarded(target: str = lifecycle_mod.DEFAULT_TARGET, **kwargs: object):
        if target == lifecycle_mod.DEFAULT_TARGET:
            pytest.fail(
                "this test would start the REAL daemon application "
                f"({lifecycle_mod.DEFAULT_TARGET}) as a detached process that nothing "
                f"stops. Pass an explicit target — {MISSING_TARGET} to exercise a "
                "missing one, or a fake module via the make_target fixture."
            )
        return real_start(target, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lifecycle_mod, "start", guarded)
    monkeypatch.setitem(globals(), "start", guarded)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def make_target(tmp_path: Path) -> Callable[[str, str], tuple[str, dict[str, str]]]:
    """Write a fake target module and return ``(dotted, env)`` for ``start``."""
    fakes = tmp_path / "fakes"
    fakes.mkdir(exist_ok=True)

    def make(name: str, source: str) -> tuple[str, dict[str, str]]:
        (fakes / f"{name}.py").write_text(source, encoding="utf-8")
        return f"{name}:main", {"PYTHONPATH": str(fakes)}

    return make


@pytest.fixture
def reaper():
    """Kill every pid a test started, however the test ended."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _is_gone(pid: int) -> bool:
    """Has *pid* really stopped running?

    ``os.kill(pid, 0)`` is not enough here and the reason is worth stating: a
    daemon started from *this* process is this process's child, so between its
    exit and pytest reaping it, it is a **zombie** — dead, but still signalable.
    The production callers never see that (``embodiment stop`` is not the
    daemon's parent, and the CLI process that started it has exited, so init
    reaps it), but a test that asserted on ``os.kill`` alone would be asserting
    on the harness rather than on the daemon.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True
    return stat_line.rpartition(")")[2].split()[0] == "Z"


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_gone(pid):
            return True
        time.sleep(0.02)
    return _is_gone(pid)


def _ledger_codes(state_dir: Path) -> list[str]:
    path = state_dir / LEDGER_FILENAME
    if not path.exists():
        return []
    codes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            codes.append(json.loads(line)["code"])
    return codes


def _dead_pid() -> int:
    """A pid that is definitely not running: a real child, waited for and reaped."""
    proc = subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        [sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    proc.wait(timeout=30)
    return proc.pid


# ── criterion 1a: double start is idempotent ─────────────────────────────────


class TestDoubleStartIsIdempotent:
    def test_second_start_finds_the_first_and_starts_nothing(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_a", IDLE_TARGET)
        first = start(target, state_dir=state_dir, env=env)
        reaper.append(first.pid or 0)
        assert first.started is True
        assert first.pid and first.pid > 0

        second = start(target, state_dir=state_dir, env=env)
        assert second.started is False
        assert second.already_running is True
        assert second.pid == first.pid

        # And exactly one process exists: the pidfile still names the first.
        assert status(state_dir=state_dir).pid == first.pid
        stop(state_dir=state_dir)

    def test_the_pidfile_lock_is_exclusive_not_merely_a_number(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """A second acquirer must LOSE the lock, not merely read a pid.

        The number-in-a-file design passes the idempotence test above and still
        lets two daemons win a race. This asserts the lock itself.
        """
        target, env = make_target("idle_b", IDLE_TARGET)
        result = start(target, state_dir=state_dir, env=env)
        reaper.append(result.pid or 0)
        pidfile = PidFile(state_dir / PIDFILE_NAME)
        try:
            assert pidfile.acquire() is False
        finally:
            pidfile.close()
        stop(state_dir=state_dir)

    def test_a_stale_pidfile_is_reclaimed_with_a_recorded_degradation(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        DaemonState(state_dir)
        dead = _dead_pid()
        (state_dir / PIDFILE_NAME).write_text(
            json.dumps({"schema": 1, "pid": dead, "state": "running", "token": "abc"}),
            encoding="utf-8",
        )
        target, env = make_target("idle_c", IDLE_TARGET)
        result = start(target, state_dir=state_dir, env=env)
        reaper.append(result.pid or 0)
        assert result.started is True
        assert result.reclaimed_stale_pid == dead
        assert STALE_PIDFILE_RECLAIMED_CODE in _ledger_codes(state_dir)
        stop(state_dir=state_dir)


# ── criterion 1b: bounded stop with a parked reader thread ───────────────────


class TestStopIsBounded:
    def test_stop_returns_within_the_bound_with_a_reader_thread_parked(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """Criterion 1: the process dies even though two threads cannot be woken."""
        target, env = make_target("parked", PARKED_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        assert started.started is True

        began = time.monotonic()
        result = stop(state_dir=state_dir)
        elapsed = time.monotonic() - began

        assert result.was_running is True
        assert result.stopped is True
        assert result.confirmed is True
        assert elapsed < DEFAULT_STOP_TIMEOUT + DEFAULT_KILL_GRACE
        assert _wait_gone(started.pid or 0)
        # The daemon's own watchdog ended it, not SIGKILL from outside.
        assert result.escalated is False
        assert HARD_EXIT_CODE in _ledger_codes(state_dir)

    def test_stop_escalates_to_sigkill_and_records_it(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("deaf", DEAF_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        result = stop(state_dir=state_dir, timeout=0.5, kill_grace=1.0)
        assert result.escalated is True
        assert result.confirmed is True
        assert _wait_gone(started.pid or 0)
        assert STOP_ESCALATED_CODE in _ledger_codes(state_dir)

    def test_stop_on_nothing_is_not_an_error(self, state_dir: Path) -> None:
        DaemonState(state_dir)
        result = stop(state_dir=state_dir)
        assert result.was_running is False
        assert result.stopped is False
        assert result.code is None

    def test_the_default_bounds_add_up_to_under_five_seconds(self) -> None:
        """Criterion 2's budget, asserted on the constants themselves."""
        assert DEFAULT_SHUTDOWN_DEADLINE < DEFAULT_STOP_TIMEOUT
        assert DEFAULT_STOP_TIMEOUT + DEFAULT_KILL_GRACE < 5.0


# ── criterion 1c: status tells the truth about an unclean death ──────────────


class TestStatusTellsTheTruth:
    def test_status_reports_dead_unclean_with_the_last_ledger_entry(self, state_dir: Path) -> None:
        state = DaemonState(state_dir)
        state.ledger.append("lifecycle-test-marker", "the last thing that happened")
        (state_dir / PIDFILE_NAME).write_text(
            json.dumps(
                {"schema": 1, "pid": _dead_pid(), "state": "running", "token": "t"},
            ),
            encoding="utf-8",
        )
        report = status(state_dir=state_dir)
        assert report.state == STATE_DEAD_UNCLEAN
        assert report.ledger["last"]["code"] == "lifecycle-test-marker"
        assert report.ledger["count"] >= 1

    def test_a_running_daemon_reads_as_running(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_d", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        report = status(state_dir=state_dir)
        assert report.state == STATE_RUNNING
        assert report.pid == started.pid
        assert report.target == target
        stop(state_dir=state_dir)

    def test_a_cleanly_stopped_daemon_reads_as_stopped_not_dead(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """The whole point of the clean-exit marker: a deliberate stop is not a death."""
        target, env = make_target("idle_e", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        stop(state_dir=state_dir)
        report = status(state_dir=state_dir)
        assert report.state == STATE_STOPPED
        assert report.exit_code == 0
        assert report.hard_exit is False

    def test_a_missing_state_dir_is_state_unavailable(self, state_dir: Path) -> None:
        assert not state_dir.exists()
        assert status(state_dir=state_dir).state == STATE_UNAVAILABLE

    def test_an_empty_state_dir_is_stopped(self, state_dir: Path) -> None:
        DaemonState(state_dir)
        assert status(state_dir=state_dir).state == STATE_STOPPED

    def test_an_unreadable_state_dir_is_state_unavailable(self, state_dir: Path) -> None:
        DaemonState(state_dir)
        os.chmod(state_dir, 0o000)
        try:
            report = status(state_dir=state_dir)
        finally:
            os.chmod(state_dir, 0o700)
        assert report.state == STATE_UNAVAILABLE
        assert report.detail

    def test_status_writes_nothing(self, state_dir: Path) -> None:
        DaemonState(state_dir)
        before = sorted((p.name, p.stat().st_mtime_ns) for p in state_dir.iterdir())
        status(state_dir=state_dir)
        status(state_dir=state_dir)
        after = sorted((p.name, p.stat().st_mtime_ns) for p in state_dir.iterdir())
        assert before == after

    def test_status_surfaces_the_daemons_own_write_error_counts(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """``DaemonState.status()``'s counters, carried across the process boundary."""
        target, env = make_target("idle_f", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        report = status(state_dir=state_dir)
        snapshot = report.daemon_state
        assert snapshot is not None
        assert snapshot["operational_log"]["write_error_count"] == 0
        assert snapshot["ledger"]["write_error_count"] == 0
        stop(state_dir=state_dir)


# ── finding a daemon that fell back (t4's candidate_state_dirs) ──────────────


class TestFindsAFallenBackDaemon:
    def test_status_looks_in_every_candidate_dir(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        fallback = resolve_fallback_state_dir()
        target, env = make_target("idle_g", IDLE_TARGET)
        started = start(target, state_dir=fallback, env=env)
        reaper.append(started.pid or 0)
        # The preferred dir (state_dir) holds nothing; the reader must look on.
        report = status(state_dir=state_dir)
        assert report.state == STATE_RUNNING
        assert report.pid == started.pid
        assert report.state_dir == str(fallback.resolve())
        stop(state_dir=state_dir)
        assert _wait_gone(started.pid or 0)

    def test_stop_looks_in_every_candidate_dir(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        fallback = resolve_fallback_state_dir()
        target, env = make_target("idle_h", IDLE_TARGET)
        started = start(target, state_dir=fallback, env=env)
        reaper.append(started.pid or 0)
        result = stop(state_dir=state_dir)
        assert result.was_running is True
        assert result.pid == started.pid
        assert _wait_gone(started.pid or 0)


# ── criterion 2: stop then start each under 5 s ──────────────────────────────


class TestFiveSecondBudget:
    def test_stop_then_start_each_complete_under_five_seconds(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_budget", IDLE_TARGET)
        first = start(target, state_dir=state_dir, env=env)
        reaper.append(first.pid or 0)
        assert first.started is True

        began = time.monotonic()
        stopped = stop(state_dir=state_dir)
        stop_elapsed = time.monotonic() - began
        assert stopped.stopped is True

        began = time.monotonic()
        second = start(target, state_dir=state_dir, env=env)
        start_elapsed = time.monotonic() - began
        reaper.append(second.pid or 0)
        assert second.started is True

        assert stop_elapsed < 5.0, f"stop took {stop_elapsed:.2f}s"
        assert start_elapsed < 5.0, f"start took {start_elapsed:.2f}s"
        stop(state_dir=state_dir)


# ── the target seam (what t15 plugs into) ────────────────────────────────────


class TestTheTargetSeam:
    def test_the_default_target_names_the_daemon_app(self) -> None:
        """The seam t15 plugs into. It is now built, which is why no test uses it."""
        assert DEFAULT_TARGET == "embodiment.daemon.app:main"

    def test_start_fails_cleanly_when_the_target_module_does_not_exist(
        self, state_dir: Path
    ) -> None:
        """This used to point at the default target, and must never again.

        ``embodiment.daemon.app`` did not exist when this was written, so
        ``start()`` with no target was a safe way to exercise the
        target-unavailable path. t15 built it, and the test silently became
        one that launched a **real detached daemon on every suite run and
        never stopped it**. The intent — a missing target is a clean,
        pidfile-free refusal — is kept by naming a module that genuinely does
        not exist; :data:`MISSING_TARGET` is that module, and
        ``_never_spawn_the_real_daemon`` makes the old shape impossible.
        """
        result = start(MISSING_TARGET, state_dir=state_dir)
        assert result.started is False
        assert result.already_running is False
        assert result.code == TARGET_UNAVAILABLE_CODE
        assert MISSING_TARGET.split(":")[0] in result.detail
        assert not (state_dir / PIDFILE_NAME).exists()

    def test_start_reports_a_child_that_dies_at_import(self, state_dir: Path, make_target) -> None:
        target, env = make_target("broken", BROKEN_TARGET)
        result = start(target, state_dir=state_dir, env=env, confirm_timeout=5.0)
        assert result.started is False
        assert result.code == CHILD_EXITED_EARLY_CODE
        assert result.stderr_path is not None
        # The child recorded WHY, and neither the result nor the ledger carries
        # the target's own exception text (lesson 5: no speech in a record).
        assert TARGET_UNAVAILABLE_CODE in _ledger_codes(state_dir)
        assert "Traceback" not in result.detail
        assert "explodes" not in result.detail
        assert "explodes" not in (state_dir / LEDGER_FILENAME).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "nocolon",
            "a:b:c",
            "../etc/passwd:main",
            "os;rm -rf /:main",
            "mod:main\x00",
            "mod:ma in",
            "a" * 400 + ":main",
            "-mevil:main",
            "mod:主",
        ],
    )
    def test_a_malformed_target_is_refused_before_any_import(self, bad: str) -> None:
        resolved, error = resolve_target(bad)
        assert resolved is None
        assert error

    def test_a_resolvable_target_returns_a_runnable_factory(self, make_target) -> None:
        target, env = make_target("resolvable", IDLE_TARGET)
        sys.path.insert(0, env["PYTHONPATH"])
        try:
            factory, error = resolve_target(target)
            assert error is None
            assert factory is not None
            assert hasattr(factory(), "run")
        finally:
            sys.path.remove(env["PYTHONPATH"])


# ── the daemon-side stop path, in process ────────────────────────────────────


class _Recorder:
    """Captures the exit code instead of ending the interpreter.

    ``raises=True`` stands in for ``os._exit``'s "control never comes back"
    on the main path. The watchdog path passes ``raises=False``: raising out
    of a daemon thread is not a better simulation, only a noisier one.
    """

    def __init__(self, *, raises: bool = True) -> None:
        self.code: int | None = None
        self._raises = raises

    def __call__(self, code: int) -> None:
        self.code = code
        if self._raises:
            raise SystemExit(code)


class TestRunDaemonInProcess:
    def test_a_clean_run_exits_with_the_targets_code(self, state_dir: Path) -> None:
        state = DaemonState(state_dir)
        exiter = _Recorder()

        class App:
            def run(self, stop_event: threading.Event) -> int:
                stop_event.set()
                return 0

        with pytest.raises(SystemExit):
            run_daemon(App(), state=state, exit_process=exiter, install_signal_handlers=False)
        assert exiter.code == 0

    def test_the_watchdog_hard_exits_and_records_first(self, state_dir: Path) -> None:
        """Criterion 1's bound, proved without a subprocess: the ledger is written FIRST."""
        state = DaemonState(state_dir)
        exiter = _Recorder(raises=False)
        parked = threading.Event()

        class App:
            def run(self, stop_event: threading.Event) -> int:
                threading.Thread(target=parked.wait, daemon=False, name="parked").start()
                parked.wait()  # never woken: only the watchdog can end this
                return 0

        runner = DaemonRunner(App(), state=state, exit_process=exiter, shutdown_deadline=0.3)
        worker = threading.Thread(
            target=lambda: runner.run(install_signal_handlers=False), daemon=True
        )
        worker.start()
        time.sleep(0.05)
        runner.request_stop("test")
        deadline = time.monotonic() + 5.0
        while exiter.code is None and time.monotonic() < deadline:
            time.sleep(0.01)
        parked.set()
        assert exiter.code is not None, "the watchdog never fired"
        assert HARD_EXIT_CODE in _ledger_codes(state_dir)

    def test_lingering_non_daemon_threads_are_recorded_on_a_clean_exit(
        self, state_dir: Path
    ) -> None:
        state = DaemonState(state_dir)
        exiter = _Recorder()
        release = threading.Event()

        class App:
            def run(self, stop_event: threading.Event) -> int:
                threading.Thread(target=release.wait, daemon=False, name="lingerer").start()
                return 0

        try:
            with pytest.raises(SystemExit):
                run_daemon(App(), state=state, exit_process=exiter, install_signal_handlers=False)
        finally:
            release.set()
        assert THREADS_LINGERING_CODE in _ledger_codes(state_dir)

    def test_a_target_that_raises_is_recorded_not_propagated(self, state_dir: Path) -> None:
        state = DaemonState(state_dir)
        exiter = _Recorder()

        class App:
            def run(self, stop_event: threading.Event) -> int:
                raise RuntimeError("target blew up")

        with pytest.raises(SystemExit):
            run_daemon(App(), state=state, exit_process=exiter, install_signal_handlers=False)
        assert exiter.code != 0
        codes = _ledger_codes(state_dir)
        assert any(code.startswith("lifecycle-") for code in codes)

    def test_a_targets_shutdown_is_called_and_its_failure_recorded(self, state_dir: Path) -> None:
        state = DaemonState(state_dir)
        exiter = _Recorder()
        called: list[float] = []

        class App:
            def run(self, stop_event: threading.Event) -> int:
                return 0

            def shutdown(self, deadline: float) -> None:
                called.append(deadline)
                raise RuntimeError("shutdown blew up")

        with pytest.raises(SystemExit):
            run_daemon(App(), state=state, exit_process=exiter, install_signal_handlers=False)
        assert called and called[0] > 0
        assert any(c.startswith("lifecycle-") for c in _ledger_codes(state_dir))


class TestCandidatePrecedence:
    """A corpse in one candidate dir must never speak for the dir the user named.

    Searching ``candidate_state_dirs()`` exists to **find a live daemon** that
    fell back during bootstrap. Letting a dead record in the machine-wide
    fallback win the headline made ``embodiment status --state-dir <brand new
    empty dir>`` report ``dead (unclean)`` on a healthy machine — and nothing
    ever cleared it, because a later ``start`` succeeds in the primary dir and
    leaves the corpse where it is. The rule: a **live lock anywhere** wins; with
    nothing live, the **primary** dir's own state is the headline; another
    candidate's record is a secondary note.
    """

    @staticmethod
    def _plant_corpse(directory: Path, pid: int) -> None:
        DaemonState(directory)
        (directory / PIDFILE_NAME).write_text(
            json.dumps(
                {"schema": 1, "pid": pid, "state": "running", "target": "ghost:main"},
            ),
            encoding="utf-8",
        )

    def test_a_corpse_in_the_fallback_does_not_speak_for_a_clean_primary(
        self, state_dir: Path
    ) -> None:
        self._plant_corpse(resolve_fallback_state_dir(), _dead_pid())
        DaemonState(state_dir)
        report = status(state_dir=state_dir)
        assert report.state == STATE_STOPPED
        assert report.state_dir == str(state_dir.resolve())
        assert report.pid is None

    def test_a_corpse_in_the_fallback_is_reported_as_a_secondary_note(
        self, state_dir: Path
    ) -> None:
        dead = _dead_pid()
        fallback = resolve_fallback_state_dir()
        self._plant_corpse(fallback, dead)
        DaemonState(state_dir)
        notes = status(state_dir=state_dir).other_candidates
        assert [n["state_dir"] for n in notes] == [str(fallback)]
        assert notes[0]["state"] == STATE_DEAD_UNCLEAN
        assert notes[0]["pid"] == dead

    def test_a_never_started_primary_is_not_made_dead_by_a_corpse_elsewhere(
        self, state_dir: Path
    ) -> None:
        self._plant_corpse(resolve_fallback_state_dir(), _dead_pid())
        assert not state_dir.exists()
        report = status(state_dir=state_dir)
        assert report.state == STATE_UNAVAILABLE
        assert report.other_candidates

    def test_a_live_daemon_in_the_fallback_still_wins_the_headline(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """(a) of the rule: liveness anywhere beats the primary's own state."""
        fallback = resolve_fallback_state_dir()
        target, env = make_target("idle_prec", IDLE_TARGET)
        started = start(target, state_dir=fallback, env=env)
        reaper.append(started.pid or 0)
        DaemonState(state_dir)
        report = status(state_dir=state_dir)
        assert report.state == STATE_RUNNING
        assert report.state_dir == str(fallback.resolve())
        assert report.pid == started.pid
        stop(state_dir=state_dir)

    def test_start_reclaims_a_corpse_in_another_candidate_dir(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        dead = _dead_pid()
        fallback = resolve_fallback_state_dir()
        self._plant_corpse(fallback, dead)
        target, env = make_target("idle_reclaim", IDLE_TARGET)
        result = start(target, state_dir=state_dir, env=env)
        reaper.append(result.pid or 0)
        assert result.started is True
        assert str(fallback) in result.reclaimed_elsewhere
        assert STALE_PIDFILE_RECLAIMED_CODE in _ledger_codes(fallback)
        # And the corpse no longer reads as a death anywhere.
        report = status(state_dir=state_dir)
        assert report.state == STATE_RUNNING
        assert all(n["state"] != STATE_DEAD_UNCLEAN for n in report.other_candidates)
        stop(state_dir=state_dir)

    def test_stop_names_the_primary_dir_when_nothing_is_running(self, state_dir: Path) -> None:
        self._plant_corpse(resolve_fallback_state_dir(), _dead_pid())
        DaemonState(state_dir)
        result = stop(state_dir=state_dir)
        assert result.was_running is False
        assert result.state_dir == str(state_dir.resolve())


class TestNoTestTouchesTheRealFallbackDir:
    """The machine-wide fallback dir is where a REAL daemon lives. Stay out of it.

    A test that forces ``DaemonState`` to fall back writes into
    ``<tempdir>/embodiment-state-<uid>`` unless ``tempfile`` is repointed for
    this process *and* for spawned children. That happened: an attack run left
    a stale 'running' pidfile there, and every later ``embodiment status`` on
    this machine read it.
    """

    def test_forcing_a_fallback_never_touches_the_machine_wide_dir(
        self, tmp_path: Path, make_target, reaper: list[int]
    ) -> None:
        before = _snapshot(REAL_FALLBACK_DIR)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        target, env = make_target("idle_fallback", IDLE_TARGET)
        result = start(target, state_dir=blocker / "state", env=env)
        reaper.append(result.pid or 0)
        try:
            # It really did fall back — otherwise this test proves nothing.
            assert result.state_dir is not None
            assert str(blocker) not in result.state_dir
            assert str(tmp_path) in result.state_dir
        finally:
            stop(state_dir=blocker / "state")
        assert _snapshot(REAL_FALLBACK_DIR) == before

    def test_the_daemon_child_inherits_this_processs_environment(
        self, tmp_path: Path, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """A child that loses TMPDIR computes the wrong fallback dir."""
        reporter = (
            "import os\n"
            "class App:\n"
            "    def run(self, stop_event):\n"
            "        import pathlib\n"
            "        pathlib.Path(os.environ['EMBODIMENT_STATE_DIR'], 'seen-tmpdir').write_text(\n"
            "            os.environ.get('TMPDIR', '<unset>'))\n"
            "        stop_event.wait()\n"
            "        return 0\n"
            "\n"
            "def main():\n"
            "    return App()\n"
        )
        target, env = make_target("tmpdir_reporter", reporter)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        deadline = time.monotonic() + 5.0
        seen = state_dir / "seen-tmpdir"
        while not seen.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert seen.read_text(encoding="utf-8") == str(tmp_path / "tmp")
        stop(state_dir=state_dir)


class TestDefectsFoundByAttackingTheModule:
    """Three defects the adversarial pass found. Each keeps a test so it stays fixed."""

    def test_a_thread_name_never_reaches_a_record(self, state_dir: Path) -> None:
        """A marker planted in a thread name came back out of the ledger verbatim.

        ``_safe_token`` restricts the charset, which does nothing to a marker
        that is already alphanumeric — and a thread's name is chosen by the
        daemon *application*, so it can carry whatever the host put there. Only
        a count and a digest may be recorded.
        """
        marker = "MARKER-a91f4c-what-the-user-said"
        state = DaemonState(state_dir)
        exiter = _Recorder()
        release = threading.Event()

        class App:
            def run(self, stop_event: threading.Event) -> int:
                threading.Thread(target=release.wait, daemon=False, name=marker).start()
                return 0

        try:
            with pytest.raises(SystemExit):
                run_daemon(App(), state=state, exit_process=exiter, install_signal_handlers=False)
        finally:
            release.set()
        ledger_text = (state_dir / LEDGER_FILENAME).read_text(encoding="utf-8")
        assert THREADS_LINGERING_CODE in ledger_text
        assert marker not in ledger_text
        assert "digest" in ledger_text

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_state_dir_override_is_refused_not_resolved_to_the_cwd(
        self, blank: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path("")`` resolves to the CWD, which could be a git checkout.

        Silently claiming a pidfile in whatever directory the operator happened
        to be standing in is exactly the cwd-dependence t4's state directory
        exists to prevent ("Privacy of the room").
        """
        monkeypatch.chdir(tmp_path)
        assert status(state_dir=blank).state == STATE_UNAVAILABLE
        result = start("idle:main", state_dir=blank)
        assert result.started is False
        assert stop(state_dir=blank).was_running is False
        assert not (tmp_path / PIDFILE_NAME).exists()

    def test_an_unusable_state_dir_path_never_raises(self, state_dir: Path) -> None:
        """``Path.is_dir()`` raises ``ENAMETOOLONG`` — it does not swallow every OSError."""
        too_long = "/tmp/" + "a" * 10_000  # nosec B108 - never created; it cannot be
        assert status(state_dir=too_long).state == STATE_UNAVAILABLE
        assert status(state_dir=too_long).detail
        assert start("idle:main", state_dir=too_long).started is False
        assert stop(state_dir=too_long).was_running is False


# ── the daemonisation contract ───────────────────────────────────────────────


class TestDaemonisation:
    def test_the_child_is_in_its_own_session_and_survives_the_parent_group(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_sess", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        assert os.getsid(started.pid or 0) != os.getsid(0)
        stop(state_dir=state_dir)

    def test_child_stdio_is_detached_and_stderr_lands_in_the_log(
        self, state_dir: Path, make_target, reaper: list[int], capfd: pytest.CaptureFixture[str]
    ) -> None:
        noisy = IDLE_TARGET.replace(
            "    def run(self, stop_event):",
            "    def run(self, stop_event):\n"
            "        import sys\n"
            "        print('DAEMON-NOISE-STDERR', file=sys.stderr, flush=True)\n"
            "        print('DAEMON-NOISE-STDOUT', flush=True)\n"
            "        assert sys.stdin.read() == ''\n",
        )
        target, env = make_target("noisy", noisy)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        time.sleep(0.4)
        log = (state_dir / DAEMON_STDERR_NAME).read_text(encoding="utf-8")
        assert "DAEMON-NOISE-STDERR" in log
        assert "DAEMON-NOISE-STDOUT" in log
        captured = capfd.readouterr()
        assert "DAEMON-NOISE" not in captured.out + captured.err
        stop(state_dir=state_dir)

    def test_the_pidfile_and_the_stderr_log_are_private(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_priv", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        for name in (PIDFILE_NAME, DAEMON_STDERR_NAME):
            mode = stat.S_IMODE((state_dir / name).stat().st_mode)
            assert mode == 0o600, f"{name} is {oct(mode)}"
        stop(state_dir=state_dir)


# ── attacks on the records ───────────────────────────────────────────────────


class TestHostileInput:
    @pytest.mark.parametrize(
        "content",
        [
            "",
            "not json at all",
            "{",
            '{"pid": "not-an-int"}',
            '{"pid": -1}',
            '{"pid": 0}',
            '{"pid": 1}',
            "[]",
            '"a string"',
            '{"pid": 99999999999999999999}',
            '{"pid": 42, "state": "\\u0000running"}',
            "\x00\x01\x02",
        ],
    )
    def test_a_corrupt_pidfile_never_raises_and_never_reports_running(
        self, state_dir: Path, content: str
    ) -> None:
        DaemonState(state_dir)
        (state_dir / PIDFILE_NAME).write_text(content, encoding="utf-8", errors="surrogateescape")
        report = status(state_dir=state_dir)
        assert report.state in (STATE_STOPPED, STATE_DEAD_UNCLEAN, STATE_UNAVAILABLE)
        result = stop(state_dir=state_dir)
        assert result.was_running is False

    def test_a_huge_pidfile_is_refused_rather_than_read(self, state_dir: Path) -> None:
        DaemonState(state_dir)
        (state_dir / PIDFILE_NAME).write_text("x" * 200_000, encoding="utf-8")
        report = status(state_dir=state_dir)
        assert report.state != STATE_RUNNING
        assert report.detail

    @pytest.mark.parametrize("hostile", ["init", "self", "parent"])
    def test_stop_refuses_to_signal_init_or_itself(self, state_dir: Path, hostile: str) -> None:
        """A live lock naming a pid we must never signal is refused, not obeyed.

        The lock is what proves liveness, so a pidfile whose *content* was
        tampered with while something really does hold the lock is the one case
        where ``stop`` could be talked into killing pid 1 — or pytest.
        """
        DaemonState(state_dir)
        pid = {"init": 1, "self": os.getpid(), "parent": os.getppid()}[hostile]
        holder = PidFile(state_dir / PIDFILE_NAME)
        assert holder.acquire() is True
        try:
            holder.write({"schema": 1, "pid": pid, "state": "running"})
            result = stop(state_dir=state_dir)
        finally:
            holder.close()
        assert result.stopped is False, f"stop would have signalled pid {pid}"
        assert result.code == REFUSED_PID_CODE

    def test_no_record_carries_the_targets_output(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """Lesson 5: plant a marker and scan every record this module writes."""
        marker = "MARKER-7f3a9c-SECRET-SPEECH"
        noisy = IDLE_TARGET.replace(
            "    def run(self, stop_event):",
            "    def run(self, stop_event):\n"
            f"        import sys; print({marker!r}, file=sys.stderr, flush=True)\n",
        )
        target, env = make_target("marked", noisy)
        started = start(target, state_dir=state_dir, env=dict(env, MARKER_ENV=marker))
        reaper.append(started.pid or 0)
        time.sleep(0.4)
        report = stop(state_dir=state_dir)
        for name in (PIDFILE_NAME, LEDGER_FILENAME, "embodiment.log"):
            path = state_dir / name
            if path.exists():
                assert marker not in path.read_text(encoding="utf-8"), name
        assert marker not in json.dumps(report.to_dict())
        assert marker not in json.dumps(status(state_dir=state_dir).to_dict())

    def test_results_are_json_serialisable(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        target, env = make_target("idle_json", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        for payload in (started.to_dict(), status(state_dir=state_dir).to_dict()):
            assert isinstance(json.loads(json.dumps(payload)), dict)
        assert isinstance(json.loads(json.dumps(stop(state_dir=state_dir).to_dict())), dict)

    def test_start_never_raises_when_the_state_dir_cannot_be_created(self, tmp_path: Path) -> None:
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        try:
            result = start("no_such_target_module:main", state_dir=blocked / "nested" / "state")
        finally:
            os.chmod(blocked, 0o700)
        assert result.started is False
        assert result.detail

    def test_repeated_status_is_stable_and_cheap(
        self, state_dir: Path, make_target, reaper: list[int]
    ) -> None:
        """The same call a dashboard makes every second, a thousand times."""
        target, env = make_target("idle_hot", IDLE_TARGET)
        started = start(target, state_dir=state_dir, env=env)
        reaper.append(started.pid or 0)
        states = {status(state_dir=state_dir).state for _ in range(1000)}
        assert states == {STATE_RUNNING}
        stop(state_dir=state_dir)


def test_module_exports_what_it_advertises() -> None:
    for name in lifecycle_mod.__all__:
        assert hasattr(lifecycle_mod, name), name


def test_every_degradation_code_is_prefixed_with_the_module_name() -> None:
    codes = [
        value
        for name, value in vars(lifecycle_mod).items()
        if name.endswith("_CODE") and isinstance(value, str)
    ]
    assert codes
    assert all(code.startswith("lifecycle-") for code in codes)


def test_state_names_are_the_four_the_task_names() -> None:
    assert {STATE_RUNNING, STATE_STOPPED, STATE_DEAD_UNCLEAN, STATE_UNAVAILABLE} == {
        "running",
        "stopped",
        "dead (unclean)",
        "state unavailable",
    }
