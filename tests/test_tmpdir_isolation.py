"""The suite must never touch the machine's real fallback state directory.

See ``tests/conftest.py::_no_writes_to_the_real_tmpdir`` for what happened when it did.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from embodiment.daemon.state import DaemonState, resolve_fallback_state_dir
from tests.conftest import REAL_TMPDIR


def test_the_fallback_state_dir_is_not_under_the_real_tmpdir(tmp_path: Path) -> None:
    fallback = resolve_fallback_state_dir()
    assert Path(REAL_TMPDIR).resolve() not in (fallback.parent, *fallback.parents[:0])
    assert fallback.parent == Path(tempfile.gettempdir()).resolve()
    assert fallback.parent != Path(REAL_TMPDIR).resolve()


def test_a_forced_fallback_lands_in_the_private_tmpdir(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory was asked for")
    state = DaemonState(blocker / "state")
    used = Path(state.status()["state_dir"])
    assert Path(REAL_TMPDIR).resolve() != used.parent
    assert used == resolve_fallback_state_dir()


def test_a_spawned_child_inherits_the_private_tmpdir() -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    assert Path(out).resolve() == Path(tempfile.gettempdir()).resolve()
    assert Path(out).resolve() != Path(REAL_TMPDIR).resolve()
