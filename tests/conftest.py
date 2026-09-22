"""Shared pytest fixtures.

The one job this file has today: **stop the developer's shell from making tests
pass.**

Two CLI tests asserted `exit_code == 0` while passing no API key, and passed
locally for two months because `COLLEAGUE_API_KEY` happened to be exported in
the shell that ran them. On CI, where it is not, the same tests exited `2`
(`worker-api-key-absent`) and failed. Both carried comments describing
themselves as hermetic — and they were, about the *network*, which was the
reach anyone had thought to close.

A per-test fix (set the variable in those two tests) closes those two holes and
leaves the class open, so the guard is structural instead: every live-rig
variable is **removed from the environment for every test**, and a test that
needs one sets it explicitly. After this, a test that depends on ambient
credentials fails on the machine that wrote it rather than on the machine that
did not.
"""

from __future__ import annotations

import pytest

#: Every environment variable that can make a harness reach a real rig, or make
#: a config resolve that would otherwise degrade. Removing them is what keeps a
#: hermetic test hermetic; the live-gated tests set what they need themselves.
LIVE_RIG_ENV = (
    "COLLEAGUE_API_KEY",
    "EMBODIMENT_CORTEX_MODEL",
    "EMBODIMENT_CORTEX_URL",
    "EMBODIMENT_WORKER_URL",
    "EMBODIMENT_WORKER_MODEL",
    "EMBODIMENT_SENSES_URL",
    "EMBODIMENT_SENSES_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    # The drone opt-in switch and its paths (task t12). Exactly the class of
    # hole this file exists to close: `EMBODIMENT_DRONES_ENABLED` left exported
    # in a developer's shell would make "a fresh checkout evokes nothing" pass
    # or fail on which terminal ran it, and the failure would land on CI.
    "EMBODIMENT_DRONES_ENABLED",
    "EMBODIMENT_DRONES_DIR",
    "EMBODIMENT_DRONE_LEDGER",
)

#: The gates that deliberately opt a test *into* the real rig. They are read by
#: the tests' own skip markers, so they must survive this fixture — clearing
#: them would silently skip the live suite instead of running it.
LIVE_GATES = ("EMBODIMENT_LIVE_RIG", "EMBODIMENT_LIVE_ARENA")


@pytest.fixture(autouse=True)
def _no_ambient_rig_credentials(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Remove ambient rig credentials from every test's environment.

    Skipped for tests that opt into the live rig: those are gated on
    ``EMBODIMENT_LIVE_RIG`` / ``EMBODIMENT_LIVE_ARENA`` and are *supposed* to
    read the operator's real environment. Everything else runs with the
    variables absent, so an assertion can only pass on what the test itself
    supplies.
    """
    import os

    if any(os.environ.get(gate) for gate in LIVE_GATES):
        return
    for name in LIVE_RIG_ENV:
        monkeypatch.delenv(name, raising=False)


#: The temp directory as it was when the session started - the REAL one, where a
#: real daemon's deterministic fallback state dir (`<tmp>/embodiment-state-<uid>`)
#: would live. Captured at import, before any fixture can repoint it, so a guard
#: test can prove the real one was left alone.
REAL_TMPDIR = __import__("tempfile").gettempdir()


@pytest.fixture(autouse=True)
def _no_writes_to_the_real_tmpdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Point ``tempfile.gettempdir()`` at a per-test directory, children included.

    ``embodiment.daemon.state`` falls back to ``<tmp>/embodiment-state-<uid>`` when
    the requested state dir is unusable. That path is machine-global and is where a
    real daemon would keep its pidfile and crash ledger - and a dozen tests force
    exactly that fallback. Found by driving the real CLI: ``embodiment status`` on
    a clean machine reported ``dead (unclean)`` from a pidfile a test run had left
    there, beside 24 ledger entries written by three different tasks' tests.

    The same shape as the credentials guard above: closing it per test leaves the
    class open, so it is closed for every test. ``TMPDIR`` goes into the
    environment so spawned daemons inherit it; ``tempfile.tempdir`` is reset so
    this process re-reads it. ``tmp_path_factory`` is requested first, so pytest's
    own base directory is already resolved against the real temp dir and
    ``tmp_path`` keeps working.
    """
    import tempfile

    private_tmp = tmp_path_factory.mktemp("tmpdir")
    monkeypatch.setenv("TMPDIR", str(private_tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
