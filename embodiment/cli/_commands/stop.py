"""``embodiment stop`` — stop the running daemon within a bounded time.

Thin over :func:`embodiment.daemon.lifecycle.stop`, which sends ``SIGTERM``,
waits, escalates to ``SIGKILL`` and records that it had to. Two contract
decisions live here rather than in the mechanism:

* **Stopping nothing is not an error.** ``embodiment stop`` with no daemon
  running exits ``0`` and says so, so ``stop`` is as safe to repeat as
  ``start`` is — the pair is idempotent from either end.
* **An escalation is reported, not smoothed over.** A daemon that had to be
  killed is named as killed in both text and JSON output, and the same fact is
  in the degradation ledger ``embodiment status`` reads.

A stop that cannot be confirmed — the process still holds the daemon lock
after ``SIGKILL`` — is an environment error (exit ``2``), because reporting
"stopped" there would be the silent degradation C3 forbids.
"""

from __future__ import annotations

import argparse

from embodiment.cli._errors import EXIT_ENV_ERROR, CliError
from embodiment.cli._output import emit_result
from embodiment.daemon import lifecycle

_HINTS = {
    lifecycle.STOP_UNCONFIRMED_CODE: (
        "the process survived SIGKILL — it is probably stuck in an uninterruptible "
        "call; check it with 'ps' before starting another daemon"
    ),
    lifecycle.SIGNAL_FAILED_CODE: (
        "the daemon may belong to another user; run 'embodiment status' to see it"
    ),
    lifecycle.REFUSED_PID_CODE: (
        "the pidfile names a process that must never be signalled; remove the "
        "daemon.pid named by 'embodiment status' once you know what holds it"
    ),
}


def _render(result: lifecycle.StopResult) -> str:
    if not result.was_running:
        return "embodiment stop: not running"
    if result.escalated:
        return (
            f"embodiment stop: killed (pid {result.pid}) after it ignored SIGTERM "
            f"for {result.waited_seconds:.1f}s"
        )
    return f"embodiment stop: stopped (pid {result.pid}) in {result.waited_seconds:.1f}s"


def cmd_stop(args: argparse.Namespace) -> int:
    result = lifecycle.stop(
        state_dir=args.state_dir,
        timeout=args.timeout,
        kill_grace=args.kill_grace,
    )
    if result.code is not None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=result.detail or "the daemon could not be stopped",
            remediation=_HINTS.get(result.code, "run 'embodiment status' for the details"),
        )
    json_mode = bool(getattr(args, "json", False))
    emit_result(result.to_dict() if json_mode else _render(result), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "stop",
        help="Stop the running daemon within a bounded time (idempotent).",
    )
    p.add_argument(
        "--state-dir",
        default=None,
        help="Override the state directory (default: EMBODIMENT_STATE_DIR, then XDG).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=lifecycle.DEFAULT_STOP_TIMEOUT,
        help="Seconds to wait after SIGTERM before escalating to SIGKILL.",
    )
    p.add_argument(
        "--kill-grace",
        type=float,
        default=lifecycle.DEFAULT_KILL_GRACE,
        help="Seconds to wait after SIGKILL before reporting the stop unconfirmed.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_stop)
