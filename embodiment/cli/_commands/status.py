"""``embodiment status`` — report the daemon's state truthfully. Read-only.

Four states, and the two that are not obvious are the reason this verb exists:
``running``, ``stopped``, **``dead (unclean)``** — a pidfile that still says
"running" while nothing holds its lock, reported together with the last
degradation-ledger entries so the operator can see what preceded the death —
and **``state unavailable``**, when no state directory can be read at all and
the honest answer is that a running daemon could not be seen from here.
Reporting "stopped" for either would be the silent degradation C3 forbids.

``running`` means a process holds the daemon's lock. It is **not** evidence
that Gwen heard anything: that is what the ledger and the operational-log
counters reported alongside it are for.

The headline is always about **one** directory: a live daemon wherever it was
found, otherwise the one you named. What another candidate directory holds is
printed underneath as ``other state dir: …`` — a note, never the verdict, so a
corpse left in the machine-wide fallback cannot make a healthy machine read as
``dead (unclean)``.

This verb never writes and never starts anything — not even the state
directory, which is why it builds its own read-only view instead of
constructing a :class:`~embodiment.daemon.state.DaemonState` (whose
construction creates directories, sweeps temp files and appends records). The
daemon's own ``DaemonState.status()`` counters reach this report across the
process boundary, through the snapshot the daemon writes into its pidfile.

It always exits ``0``: a daemon being stopped, or dead, is a fact to report,
not a failure of the command.
"""

from __future__ import annotations

import argparse

from embodiment.cli._output import emit_result
from embodiment.daemon import lifecycle


def _render(report: lifecycle.StatusReport) -> str:
    lines = [f"embodiment status: {report.state}"]
    if report.pid is not None:
        lines.append(f"  pid: {report.pid}")
    if report.target:
        lines.append(f"  target: {report.target}")
    if report.state_dir:
        lines.append(f"  state dir: {report.state_dir}")
    if report.exit_code is not None:
        lines.append(f"  exit code: {report.exit_code} (hard exit: {bool(report.hard_exit)})")
    if report.unfinished_threads:
        lines.append(f"  unfinished threads at exit: {report.unfinished_threads}")
    snapshot = report.daemon_state or {}
    log = snapshot.get("operational_log") or {}
    ledger_snapshot = snapshot.get("ledger") or {}
    if log or ledger_snapshot:
        lines.append(
            "  write errors (as the daemon last reported them): "
            f"log {log.get('write_error_count', 0)}, "
            f"ledger {ledger_snapshot.get('write_error_count', 0)}"
        )
    lines.append(f"  degradations recorded: {report.ledger.get('count', 0)}")
    for record in report.ledger.get("recent", []):
        lines.append(f"    - {record.get('code')}: {record.get('detail')}")
    if report.detail:
        lines.append(f"  note: {report.detail}")
    for other in report.other_candidates:
        pid = other.get("pid")
        lines.append(
            f"  other state dir: {other.get('state_dir')} — {other.get('state')}"
            + (f" (pid {pid})" if pid else "")
        )
    if not report.state_dir:
        lines.append(f"  looked in: {', '.join(report.candidates)}")
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    report = lifecycle.status(state_dir=args.state_dir)
    json_mode = bool(getattr(args, "json", False))
    emit_result(report.to_dict() if json_mode else _render(report), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "status",
        help="Report the daemon's state: running, stopped, dead (unclean), or unavailable.",
    )
    p.add_argument(
        "--state-dir",
        default=None,
        help="Override the state directory (default: EMBODIMENT_STATE_DIR, then XDG).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_status)
