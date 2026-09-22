"""hatch_build.py — the wheel-target build hook that ships the t17 dashboard.

Task ``t19`` of the ``realtime-embodiment-app`` plan. ``pyproject.toml``
scopes this hook to the wheel target only
(``[tool.hatch.build.targets.wheel.hooks.custom]``) and ships the artifact it
produces via ``[tool.hatch.build.targets.wheel.force-include]``, which maps
``web/dist`` to ``embodiment/web/dist`` inside the wheel.

Never ships a missing OR stale dashboard: if ``web/dist/index.html`` is
already present and newer than every file under ``web/src``,
``web/index.html``, ``web/package.json`` and ``web/vite.config.ts``, this hook
is a no-op (one line printed explaining why). Otherwise — missing, or an input
edited since the last build — it runs ``npm ci && npm run build`` in ``web/``
itself. A missing ``npm``, an ``npm ci``/``npm run build`` that exits
non-zero, one that times out, or one that exits zero without producing
``index.html``, all raise — which fails the ``hatch build`` / ``uv build``
invocation loudly, naming the step that failed. There is no code path that
lets the wheel finish without a dashboard, with a stale one, or without an
explicit error saying why.

Measured build time (task t19, reported in the PR): on this machine (aarch64,
20 cores, 2026-09-22), a cold ``rm -rf web/node_modules web/dist && npm ci &&
npm run build && npx vitest run`` took 3.4 s wall. The local npm package cache
was already warm, so that number does NOT measure a cold-registry network
fetch — :data:`NPM_CI_TIMEOUT_S` below is sized for that unmeasured, real
worst case, not for the 3.4 s figure.

This module is importable without ``hatchling`` installed (the ``try/except``
below) so ``tests/test_wheel_ships_the_dashboard.py`` can load it and exercise
the staleness/timeout logic directly, with a stub ``npm``, in the ordinary
dev venv — hatchling itself always has hatchling available when it actually
runs this hook during a real build.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, no shell, npm resolved via PATH
from pathlib import Path
from typing import Any

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ModuleNotFoundError:  # pragma: no cover - exercised only in a hatchling-less test venv
    BuildHookInterface = object  # type: ignore[assignment,misc]

__all__ = [
    "WebDistBuildHook",
    "NPM_CI_TIMEOUT_S",
    "NPM_BUILD_TIMEOUT_S",
]

_REPO_ROOT = Path(__file__).resolve().parent
_WEB_DIR = _REPO_ROOT / "web"
_DIST_INDEX = _WEB_DIR / "dist" / "index.html"

#: Files/directories whose mtime, if newer than :data:`_DIST_INDEX`, means the
#: shipped dashboard is stale and must be rebuilt even though it already
#: exists. Exactly the inputs a Vite build actually reads.
_STALENESS_SOURCES = (
    _WEB_DIR / "src",
    _WEB_DIR / "index.html",
    _WEB_DIR / "package.json",
    _WEB_DIR / "vite.config.ts",
)

#: Bound on ``npm ci``. Kept in one place and reused by
#: ``tests/test_wheel_ships_the_dashboard.py`` for its own ``uv build``
#: subprocess bound, rather than each hardcoding its own guess. Sized for a
#: cold package registry, not this machine's warm-cache 3.4 s measurement:
#: `npm ci` against a cold registry is minutes, not seconds.
NPM_CI_TIMEOUT_S = 600

#: Bound on ``npm run build``. Measured 3.4 s warm on this machine
#: (2026-09-21/22); 120 s gives roughly 35x headroom for a colder, more
#: loaded CI runner without masking a genuinely hung build.
NPM_BUILD_TIMEOUT_S = 120


def _first_newer_than(reference_mtime: float) -> Path | None:
    """The first staleness-source file newer than *reference_mtime*, if any."""
    for source in _STALENESS_SOURCES:
        if source.is_file():
            if source.stat().st_mtime > reference_mtime:
                return source
        elif source.is_dir():
            for candidate in sorted(source.rglob("*")):
                if candidate.is_file() and candidate.stat().st_mtime > reference_mtime:
                    return candidate
    return None


def _run_npm_build(npm: str) -> None:
    """Runs ``npm ci`` then ``npm run build`` in ``web/``, bounded and named on failure."""
    try:
        subprocess.run(  # nosec B603 - fixed argv, no shell, npm on PATH by design
            [npm, "ci"],
            cwd=_WEB_DIR,
            check=True,
            timeout=NPM_CI_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"`npm ci` in web/ did not finish within {NPM_CI_TIMEOUT_S}s; aborting the "
            "wheel build."
        ) from exc

    try:
        subprocess.run(  # nosec B603
            [npm, "run", "build"],
            cwd=_WEB_DIR,
            check=True,
            timeout=NPM_BUILD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"`npm run build` in web/ did not finish within {NPM_BUILD_TIMEOUT_S}s; "
            "aborting the wheel build."
        ) from exc


class WebDistBuildHook(BuildHookInterface):
    """Builds ``web/dist`` before the wheel's file list is assembled."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version, build_data

        if _DIST_INDEX.is_file():
            newer_source = _first_newer_than(_DIST_INDEX.stat().st_mtime)
            if newer_source is None:
                print(
                    f"hatch_build: {_DIST_INDEX} is newer than every tracked web/ input "
                    "(src/, index.html, package.json, vite.config.ts); skipping npm build."
                )
                return
            print(f"hatch_build: {newer_source} is newer than {_DIST_INDEX}; rebuilding web/dist.")
        else:
            print(f"hatch_build: {_DIST_INDEX} is missing; running npm ci && npm run build.")

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "embodiment wheel build requires web/dist/index.html and npm is not on "
                "PATH to build it. Install Node.js/npm, or run "
                "`npm ci && npm run build` in web/ before building the wheel."
            )

        _run_npm_build(npm)

        if not _DIST_INDEX.is_file():
            raise RuntimeError(
                "npm run build exited 0 but web/dist/index.html is still missing; "
                "refusing to ship an empty dashboard."
            )
