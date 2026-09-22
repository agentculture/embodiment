"""tests/test_wheel_ships_the_dashboard.py — the wheel ships the dashboard.

Task ``t19`` of the ``realtime-embodiment-app`` plan (spec targets ``h22``,
``h18``). Acceptance criterion 2 (verbatim): "a test builds the wheel and
asserts ``web/dist/index.html`` and the hashed assets are inside it."

This test actually builds a wheel with ``uv build --wheel`` — into a temp
directory under this process's (sandboxed, per ``tests/conftest.py``)
``TMPDIR`` — and inspects it with ``zipfile``, so it exercises the real
packaging path: ``pyproject.toml``'s ``force-include`` mapping AND
``hatch_build.py``'s build hook (which runs ``npm ci && npm run build`` when
``web/dist/index.html`` is absent, or refuses the build).

Marked ``slow`` (registered in ``pyproject.toml``) so it does not run under
the default ``-n auto`` suite: a cold ``npm ci`` alone measures well over the
task brief's 20 s threshold. CI runs it explicitly. Skips — with a named
reason, never a vacuous pass — when ``npm`` is not on ``PATH``, per the task
brief.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, builds this repo's own wheel
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What "a hashed asset" means here: Vite's default output naming
#: (`assets/<name>-<8-char base64url content hash>.<ext>`, e.g.
#: ``index-CmHo2FtQ.js`` — mixed-case, not hex), so this is a real content-hash
#: check, not merely "a file exists under assets/".
_HASHED_ASSET_RE = re.compile(r"^embodiment/web/dist/assets/.+-[0-9A-Za-z_-]{8,}\.\w+$")


def _npm_path() -> str | None:
    return shutil.which("npm")


def _uv_path() -> str | None:
    return shutil.which("uv")


@pytest.mark.skipif(_npm_path() is None, reason="npm is not on PATH; cannot build web/dist")
@pytest.mark.skipif(_uv_path() is None, reason="uv is not on PATH; cannot build the wheel")
def test_wheel_contains_dashboard_index_and_hashed_assets(tmp_path: Path) -> None:
    """Building the wheel ships ``embodiment/web/dist/index.html`` plus a hashed asset."""
    out_dir = tmp_path / "wheel-out"
    out_dir.mkdir()

    result = subprocess.run(  # nosec B603 - fixed argv, no shell, uv on PATH by design
        [_uv_path(), "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"uv build --wheel failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    wheels = sorted(out_dir.glob("*.whl"))
    assert wheels, f"uv build --wheel produced no .whl in {out_dir}"
    wheel_path = wheels[-1]

    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()

    assert "embodiment/web/dist/index.html" in names, (
        "wheel is missing embodiment/web/dist/index.html; contents: "
        f"{[n for n in names if 'web' in n]}"
    )

    hashed_assets = [n for n in names if _HASHED_ASSET_RE.match(n)]
    assert hashed_assets, (
        "wheel has no hashed asset under embodiment/web/dist/assets/; contents: "
        f"{[n for n in names if 'web/dist' in n]}"
    )

    with zipfile.ZipFile(wheel_path) as archive:
        index_html = archive.read("embodiment/web/dist/index.html").decode("utf-8")
    assert index_html.strip(), "embodiment/web/dist/index.html is present but empty"
