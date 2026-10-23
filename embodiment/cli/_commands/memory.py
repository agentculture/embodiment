"""``embodiment memory`` — the producer-facing verbs: ``ingest``, ``forget``, ``overview``.

These verbs only **enqueue**: they validate a batch and drop it, atomically, into the
daemon's ingest inbox (:mod:`embodiment.ingest.spool`). They never open the memory
store; the daemon is its single writer. Both are a dry run unless ``--apply``.

Daemon liveness (:func:`embodiment.daemon.lifecycle.status`) is reported *separately*
from the enqueue result: a batch can be queued while the daemon is down, and that is
a fact to show, not an error. A failing liveness probe is itself recorded as
``{"state": "unknown", "error": ...}`` rather than raised (C3).

Built to run under culture-rules' runner: no stdin, a throwaway cwd, an env of only
``PATH`` and ``LANG`` — so the state directory is a flag and ``--file`` is a path.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path
from typing import Any, Optional

from embodiment.cli._commands.overview import emit_overview
from embodiment.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from embodiment.cli._output import emit_result
from embodiment.ingest import contract
from embodiment.ingest.spool import Spool

#: A batch is at most 400 records x ~1 KiB; refuse to slurp anything far past that.
MAX_FILE_BYTES = 4 * 1024 * 1024

_MEMORY_VERBS = [
    "memory ingest --file <ndjson> [--apply] — validate and enqueue a batch (dry run default)",
    "memory ingest --status — spool counts and daemon liveness",
    "memory forget --source <s> --source-id <id> | --id <wire-id> [--apply] — enqueue a forget",
    "memory overview — describe the memory verbs (this command)",
]


def _memory_sections() -> list[dict[str, object]]:
    return [
        {"title": "Verbs", "items": list(_MEMORY_VERBS)},
        {
            "title": "Conventions",
            "items": [
                "the verbs only enqueue; the daemon is the single writer of the store",
                "dry run unless --apply; every command supports --json",
                contract.REGISTRY_HINT,
            ],
        },
    ]


def _daemon(state_dir: Optional[str]) -> dict[str, Any]:
    """Daemon liveness, kept apart from any enqueue result. Never raises."""
    try:
        from embodiment.daemon import lifecycle

        report = lifecycle.status(state_dir=state_dir)
        return {"state": report.state, "pid": report.pid, "state_dir": report.state_dir}
    except Exception as exc:  # noqa: BLE001 - liveness is a report, never a failure
        return {"state": "unknown", "error": type(exc).__name__}


def _read_lines(path: str) -> list[str]:
    try:
        p = Path(path)
        if p.stat().st_size > MAX_FILE_BYTES:
            raise CliError(
                EXIT_USER_ERROR,
                f"{path} is over the {MAX_FILE_BYTES}-byte batch file cap",
                "split the batch into smaller files",
            )
        return p.read_text(encoding="utf-8").splitlines()
    except CliError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"cannot read {path}: {type(exc).__name__}",
            "pass an absolute path to a readable UTF-8 NDJSON file",
        ) from exc


def _enqueue(spool: Spool, kind: str, records: list) -> dict[str, Any]:
    result = spool.enqueue(kind, records)
    if not result.ok:
        raise CliError(
            EXIT_ENV_ERROR,
            f"could not enqueue the batch: {result.reason}",
            "check that --state-dir is writable by this user, then retry",
        )
    return {"ok": True, "batch_id": result.batch_id}


def _emit(payload: dict[str, Any], text: str, args: argparse.Namespace) -> None:
    json_mode = bool(getattr(args, "json", False))
    emit_result(payload if json_mode else text, json_mode=json_mode)


def _daemon_line(daemon: dict[str, Any]) -> str:
    return f"daemon: {daemon.get('state')}"


def _status(args: argparse.Namespace) -> int:
    spool = Spool.for_state_dir(args.state_dir)
    s = spool.status()
    payload = {
        "spool": {
            "pending": s.pending,
            "done": s.done,
            "failed": s.failed,
            "oldest_pending_age_s": s.oldest_pending_age_s,
        },
        "daemon": _daemon(args.state_dir),
        "degradations": [list(d) for d in spool.degradations],
    }
    text = f"spool: pending {s.pending}, done {s.done}, failed {s.failed}\n" + _daemon_line(
        payload["daemon"]
    )
    _emit(payload, text, args)
    return 0


def cmd_memory_ingest(args: argparse.Namespace) -> int:
    if args.status:
        return _status(args)
    if not args.file:
        raise CliError(
            EXIT_USER_ERROR,
            "memory ingest needs --file <ndjson> (or --status)",
            "run 'embodiment memory ingest --help'",
        )
    checked = contract.validate_batch(
        _read_lines(args.file), created=datetime.date.today().isoformat()
    )
    if isinstance(checked, contract.Refusal):
        raise checked.error
    records = [dict(r) for r in checked.records]
    enqueue = None
    if args.apply:
        enqueue = _enqueue(Spool.for_state_dir(args.state_dir), "ingest", records)
    payload = {
        "applied": bool(args.apply),
        "source": checked.source,
        "records": len(records),
        "enqueue": enqueue,
        "daemon": _daemon(args.state_dir),
    }
    verb = f"enqueued as {enqueue['batch_id']}" if enqueue else "dry run, nothing enqueued"
    text = (
        f"memory ingest: {len(records)} {checked.source} record(s) valid; {verb}\n"
        + _daemon_line(payload["daemon"])
    )
    _emit(payload, text, args)
    return 0


def _forget_record(args: argparse.Namespace) -> dict[str, str]:
    by_id = args.id is not None
    by_source = args.source is not None or args.source_id is not None
    if by_id == by_source:
        raise CliError(
            EXIT_USER_ERROR,
            "memory forget takes either --id, or --source with --source-id",
            "e.g. --source pocket --source-id <recording-id>, or --id pocket:<rec>:fact:<n>",
        )
    if by_id:
        if contract.stored_id(args.id) is None:
            raise CliError(
                EXIT_USER_ERROR,
                "--id is not a valid ingest wire id",
                "use <source>:<id>:<kind>:<n>, e.g. pocket:r1:fact:0",
            )
        return {"id": args.id}
    if not args.source or not args.source_id:
        raise CliError(
            EXIT_USER_ERROR,
            "--source and --source-id must be given together",
            "e.g. --source pocket --source-id <recording-id>",
        )
    err = contract.source_refusal(args.source)
    if err is not None:
        raise err
    return {"source": args.source, "source_id": args.source_id}


def cmd_memory_forget(args: argparse.Namespace) -> int:
    record = _forget_record(args)
    enqueue = None
    if args.apply:
        enqueue = _enqueue(Spool.for_state_dir(args.state_dir), "forget", [record])
    payload = {
        "applied": bool(args.apply),
        "target": record,
        "enqueue": enqueue,
        "daemon": _daemon(args.state_dir),
    }
    verb = f"enqueued as {enqueue['batch_id']}" if enqueue else "dry run, nothing enqueued"
    _emit(payload, f"memory forget: {record}; {verb}\n" + _daemon_line(payload["daemon"]), args)
    return 0


def cmd_memory_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "embodiment memory", _memory_sections(), json_mode=bool(getattr(args, "json", False))
    )
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--apply", action="store_true", help="Enqueue for real (default: dry run).")
    p.add_argument("--state-dir", default=None, help="Daemon state directory (absolute path).")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("memory", help="Enqueue facts for Gwen to remember or forget.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_memory_overview, json=False)
    # Propagate the structured-error parser class (see cli/_commands/cli.py).
    noun = p.add_subparsers(dest="memory_command", parser_class=type(p))

    ing = noun.add_parser("ingest", help="Validate an NDJSON batch and enqueue it.")
    ing.add_argument("--file", default=None, help="Absolute path to an NDJSON batch.")
    ing.add_argument("--status", action="store_true", help="Report spool counts and daemon state.")
    _common(ing)
    ing.set_defaults(func=cmd_memory_ingest)

    fg = noun.add_parser("forget", help="Enqueue a forget by source id or by id.")
    fg.add_argument("--source", default=None, help="Registered source (pocket, newspaper).")
    fg.add_argument("--source-id", default=None, help="Forget every record of this source id.")
    fg.add_argument("--id", default=None, help="Forget one record by its wire id.")
    _common(fg)
    fg.set_defaults(func=cmd_memory_forget)

    ov = noun.add_parser("overview", help="Describe the memory verbs.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_memory_overview)
