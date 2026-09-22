"""``embodiment start`` — bring the daemon up as a detached background process.

The first verb in this CLI that *starts* anything. It is thin on purpose:
:mod:`embodiment.daemon.lifecycle` owns the mechanism and never raises, and
this module's only job is to turn a :class:`~embodiment.daemon.lifecycle.StartResult`
into the repo's error contract — ``CliError`` with a ``hint:``, results on
stdout, diagnostics on stderr, never a traceback.

Idempotent: starting a daemon that is already running exits ``0`` and starts
nothing, which is what makes ``start`` safe to put in a retry loop or a unit
file. A target module that cannot be imported — the default
(``embodiment/daemon/app.py``) or one given with ``--target`` — is a clean
environment error (exit ``2``) naming it, never a traceback and never a
half-made claim.

``--target`` imports and runs the module it names, with the invoking user's
full authority. It exists so a host can run its own daemon application (and so
the lifecycle can be tested against a fake one); it is not a sandbox and does
not pretend to be.
"""

from __future__ import annotations

import argparse

from embodiment.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from embodiment.cli._output import emit_result
from embodiment.daemon import lifecycle

_HINTS = {
    lifecycle.TARGET_UNAVAILABLE_CODE: (
        "the named module could not be found on this interpreter's path; check "
        f"the spelling, or omit --target to run the default ({lifecycle.DEFAULT_TARGET})"
    ),
    lifecycle.TARGET_INVALID_CODE: (
        "--target must look like 'package.module:attribute', e.g. " f"'{lifecycle.DEFAULT_TARGET}'"
    ),
    lifecycle.CHILD_EXITED_EARLY_CODE: (
        "read the daemon's stderr log named above, then run 'embodiment status'"
    ),
    lifecycle.START_UNCONFIRMED_CODE: (
        "read the daemon's stderr log named above; 'embodiment stop' will clear a "
        "process that came up but never reported itself running"
    ),
    lifecycle.NO_STATE_DIR_CODE: (
        "make the state directory writable, or set EMBODIMENT_STATE_DIR to one that is"
    ),
    lifecycle.START_BUSY_CODE: (
        "another 'embodiment start' holds the lock right now; run 'embodiment status'"
    ),
}

_USER_ERROR_CODES = {lifecycle.TARGET_INVALID_CODE}


def _render(result: lifecycle.StartResult) -> str:
    if result.already_running:
        return f"embodiment start: already running (pid {result.pid})"
    lines = [f"embodiment start: running (pid {result.pid})"]
    if result.reclaimed_stale_pid is not None:
        lines.append(f"  note: reclaimed a stale pidfile left by pid {result.reclaimed_stale_pid}")
    lines.append(f"  state dir: {result.state_dir}")
    lines.append(f"  target: {result.target}")
    return "\n".join(lines)


def cmd_start(args: argparse.Namespace) -> int:
    result = lifecycle.start(
        args.target,
        state_dir=args.state_dir,
        confirm_timeout=args.confirm_timeout,
    )
    if not result.started and not result.already_running:
        code = EXIT_USER_ERROR if result.code in _USER_ERROR_CODES else EXIT_ENV_ERROR
        raise CliError(
            code=code,
            message=result.detail or "the daemon could not be started",
            remediation=_HINTS.get(result.code or "", "run 'embodiment status' for the details"),
        )
    json_mode = bool(getattr(args, "json", False))
    emit_result(result.to_dict() if json_mode else _render(result), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "start",
        help="Start the daemon as a detached background process (idempotent).",
    )
    p.add_argument(
        "--target",
        default=lifecycle.DEFAULT_TARGET,
        help=(
            "The daemon application to run, as 'package.module:attribute' "
            f"(default: {lifecycle.DEFAULT_TARGET}). It is imported and run with your "
            "own authority; there is no sandbox."
        ),
    )
    p.add_argument(
        "--state-dir",
        default=None,
        help="Override the state directory (default: EMBODIMENT_STATE_DIR, then XDG).",
    )
    p.add_argument(
        "--confirm-timeout",
        type=float,
        default=lifecycle.DEFAULT_START_CONFIRM_TIMEOUT,
        help="Seconds to wait for the daemon to report itself running.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_start)
