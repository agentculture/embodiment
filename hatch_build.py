"""hatch_build.py — the wheel-target build hook that ships the t17 dashboard.

Task ``t19`` of the ``realtime-embodiment-app`` plan. ``pyproject.toml``
scopes this hook to the wheel target only
(``[tool.hatch.build.targets.wheel.hooks.custom]``) and ships the artifact it
produces via ``[tool.hatch.build.targets.wheel.force-include]``, which maps
``web/dist`` to ``embodiment/web/dist`` inside the wheel.

Never ships an empty dashboard: if ``web/dist/index.html`` is already present
(a prior local build, or a CI step that ran ``npm run build`` before ``uv
build``), this hook is a no-op. Otherwise it runs ``npm ci && npm run build``
in ``web/`` itself. A missing ``npm``, or a build that exits non-zero, or one
that exits zero without producing ``index.html``, all raise — which fails the
``hatch build`` / ``uv build`` invocation loudly. There is no code path that
lets the wheel finish without a dashboard or without an explicit error saying
why.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, no shell, npm resolved via PATH
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

__all__ = ["WebDistBuildHook"]

_REPO_ROOT = Path(__file__).resolve().parent
_WEB_DIR = _REPO_ROOT / "web"
_DIST_INDEX = _WEB_DIR / "dist" / "index.html"


class WebDistBuildHook(BuildHookInterface):
    """Builds ``web/dist`` before the wheel's file list is assembled."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version, build_data
        if _DIST_INDEX.is_file():
            return

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "embodiment wheel build requires web/dist/index.html and npm is not on "
                "PATH to build it. Install Node.js/npm, or run "
                "`npm ci && npm run build` in web/ before building the wheel."
            )

        subprocess.run(  # nosec B603 - fixed argv, no shell, npm on PATH by design
            [npm, "ci"],
            cwd=_WEB_DIR,
            check=True,
        )
        subprocess.run(  # nosec B603
            [npm, "run", "build"],
            cwd=_WEB_DIR,
            check=True,
        )

        if not _DIST_INDEX.is_file():
            raise RuntimeError(
                "npm run build exited 0 but web/dist/index.html is still missing; "
                "refusing to ship an empty dashboard."
            )
