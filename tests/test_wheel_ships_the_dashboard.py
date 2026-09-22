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

import importlib.util
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, builds this repo's own wheel
import sys
import time
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
HATCH_BUILD_PATH = REPO_ROOT / "hatch_build.py"


def _load_hatch_build() -> ModuleType:
    """Load ``hatch_build.py`` as a module, fresh, for direct unit testing.

    ``hatch_build.py`` is importable without ``hatchling`` installed (its own
    ``try/except`` around the ``BuildHookInterface`` import) precisely so this
    works in the ordinary dev venv, not just inside a real ``hatch build``.
    """
    spec = importlib.util.spec_from_file_location("hatch_build_under_test", HATCH_BUILD_PATH)
    assert spec is not None  # nosec B101 - test setup invariant
    assert spec.loader is not None  # nosec B101 - test setup invariant
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_hook(module: ModuleType) -> Any:
    """A ``WebDistBuildHook`` built without hatchling's constructor args."""
    return object.__new__(module.WebDistBuildHook)


#: How long ``touch``ed files must differ to survive filesystem mtime
#: resolution reliably across platforms (some report 1s granularity).
_MTIME_STEP_S = 1.1

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

    # One source of truth for the npm bounds: hatch_build.py's own timeout
    # constants, not a second hardcoded 600 here. `uv build` runs npm ci and
    # npm run build serially inside the hook, plus hatchling/uv's own
    # packaging overhead, so this test's outer bound is the sum of both npm
    # bounds plus a fixed buffer for that overhead.
    hatch_build = _load_hatch_build()
    overall_timeout = hatch_build.NPM_CI_TIMEOUT_S + hatch_build.NPM_BUILD_TIMEOUT_S + 60

    result = subprocess.run(  # nosec B603 - fixed argv, no shell, uv on PATH by design
        [_uv_path(), "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=overall_timeout,
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


def _build_fake_web_tree(root: Path) -> tuple[Path, Path]:
    """A throwaway ``web/`` tree with one staleness source (``src/App.tsx``)
    and a pre-built ``dist/index.html`` — returns (web_dir, dist_index)."""
    web_dir = root / "web"
    (web_dir / "src").mkdir(parents=True)
    (web_dir / "src" / "App.tsx").write_text("// app\n", encoding="utf-8")
    (web_dir / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (web_dir / "package.json").write_text("{}\n", encoding="utf-8")
    (web_dir / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    dist_dir = web_dir / "dist"
    dist_dir.mkdir()
    dist_index = dist_dir / "index.html"
    dist_index.write_text("<!doctype html><built/>\n", encoding="utf-8")
    return web_dir, dist_index


def _staleness_sources_for(web_dir: Path) -> tuple[Path, ...]:
    """The same four inputs ``hatch_build._STALENESS_SOURCES`` names, rooted
    at a test's throwaway ``web_dir`` instead of the real ``web/``."""
    return (
        web_dir / "src",
        web_dir / "index.html",
        web_dir / "package.json",
        web_dir / "vite.config.ts",
    )


def test_hook_skips_npm_when_dist_is_newer_than_every_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No source under web/ is newer than dist/index.html: the hook is a no-op.

    Proves the skip half of criterion "never ships a stale dashboard silently"
    added in review round 2: it must actually SKIP (not merely "not crash")
    when nothing changed, so a rebuild-on-every-build regression would be
    caught. ``subprocess.run`` is monkeypatched to raise if called at all —
    npm must never be invoked on this path.
    """
    module = _load_hatch_build()
    web_dir, dist_index = _build_fake_web_tree(tmp_path)
    # dist/index.html was written after every source file above; make the gap
    # unambiguous against filesystem mtime resolution.
    time.sleep(_MTIME_STEP_S)
    dist_index.write_text("<!doctype html><built/>\n", encoding="utf-8")

    monkeypatch.setattr(module, "_WEB_DIR", web_dir)
    monkeypatch.setattr(module, "_DIST_INDEX", dist_index)
    monkeypatch.setattr(module, "_STALENESS_SOURCES", _staleness_sources_for(web_dir))

    def _fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"npm must not be invoked when dist is newer; got {args!r}")

    def _fail_which(_name: str) -> None:
        raise AssertionError("shutil.which('npm') must not be consulted on the skip path")

    monkeypatch.setattr(module.subprocess, "run", _fail_if_called)
    monkeypatch.setattr(module.shutil, "which", _fail_which)

    hook = _make_hook(module)
    hook.initialize("0.0.0", {})

    assert "skipping npm build" in capsys.readouterr().out


def test_hook_rebuilds_npm_when_a_source_is_newer_than_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Editing ``web/src/App.tsx`` after the last build makes the hook rebuild.

    Proves the rebuild half: a wheel built after a source edit must not ship
    the stale artifact. npm is stubbed (never a real subprocess) via
    ``_run_npm_build`` so this test is fast and hermetic; the stub still goes
    through the module's real ``initialize`` staleness decision.
    """
    module = _load_hatch_build()
    web_dir, dist_index = _build_fake_web_tree(tmp_path)
    time.sleep(_MTIME_STEP_S)
    (web_dir / "src" / "App.tsx").write_text("// edited\n", encoding="utf-8")

    monkeypatch.setattr(module, "_WEB_DIR", web_dir)
    monkeypatch.setattr(module, "_DIST_INDEX", dist_index)
    monkeypatch.setattr(module, "_STALENESS_SOURCES", _staleness_sources_for(web_dir))

    def _fake_which(name: str) -> str | None:
        return "/fake/bin/npm" if name == "npm" else None

    monkeypatch.setattr(module.shutil, "which", _fake_which)

    calls: list[list[str]] = []

    def _stub_run_npm_build(npm: str) -> None:
        calls.append([npm])
        # Simulate a real `npm run build`: it rewrites dist/index.html.
        dist_index.write_text("<!doctype html><rebuilt/>\n", encoding="utf-8")

    monkeypatch.setattr(module, "_run_npm_build", _stub_run_npm_build)

    hook = _make_hook(module)
    hook.initialize("0.0.0", {})

    assert calls == [["/fake/bin/npm"]], "the stubbed npm build was not invoked exactly once"
    assert "rebuilt" in dist_index.read_text(encoding="utf-8")
    assert "rebuilding web/dist" in capsys.readouterr().out
