"""``embodiment history`` — Gwen's past conversations, read-only: ``export``.

It only READS the per-session logs the daemon writes, and prints them. It was
added for deviation d5 (culture-rules exporting conversations to the library).
Deviation d8 (operator, 2026-10-24) made conversations Gwen's own: the daemon
indexes each one in-process once it is quiet, and nothing outside it consumes this
verb today. It stays as a read-only view: ``--id`` names one conversation,
``--settled-for`` keeps those quiet for long enough, ``--since`` keeps one run
incremental, and every turn carries ``via`` (d7) with the ``evidence`` map saying
what each tag is worth: typed words are verbatim, audio is an AI transcription,
Gwen's replies are generated.

Built to run unattended, like ``embodiment memory``: no stdin, a
throwaway cwd, an env of only ``PATH`` and ``LANG`` — so the state directory is a
flag. The JSON carries conversation text (it is the export); the plain-text form
lists titles and counts only.
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from typing import Any, Optional

from embodiment.cli._commands.overview import emit_overview
from embodiment.cli._errors import EXIT_USER_ERROR, CliError
from embodiment.cli._output import emit_result
from embodiment.daemon.history import EVIDENCE, ConversationHistory
from embodiment.daemon.state import SESSIONS_DIRNAME, resolve_state_dir

_JSON_HELP = "Emit structured JSON."

_HISTORY_VERBS = [
    "history export [--id ID] [--since <epoch-seconds>] [--settled-for SECONDS]"
    " [--state-dir DIR] [--json] — print past conversations with their turns, each"
    " tagged with how it arrived (read-only)",
    "history overview — describe the history verbs (this command)",
]


def _history_sections() -> list[dict[str, object]]:
    return [
        {"title": "Verbs", "items": list(_HISTORY_VERBS)},
        {
            "title": "Conventions",
            "items": [
                "read-only: the verb never writes the state dir, the store or the library",
                "conversations are Gwen's own: the daemon indexes each once quiet (d8)",
                "every command supports --json; the JSON carries the conversation text",
            ],
        },
    ]


def _seconds(raw: Optional[str], flag: str, example: str) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{flag} must be a number of seconds, got {raw!r}",
            remediation=f"pass a number such as {example}, or omit {flag}",
        )
    return value


def export(
    state_dir: Optional[str],
    since: Optional[float],
    *,
    conversation_id: Optional[str] = None,
    settled_for: Optional[float] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """The listed conversations, with their tagged turns, newest first.

    *since* keeps those whose last turn is newer; *settled_for* those quiet for
    at least that many seconds; *conversation_id* names exactly one.
    """
    history = ConversationHistory(Path(resolve_state_dir(state_dir)) / SESSIONS_DIRNAME)
    now = time.time() if now is None else now
    out = []
    for summary in history.conversations()["conversations"]:
        last = summary["last"] or 0.0
        if conversation_id is not None and summary["id"] != conversation_id:
            continue
        if since is not None and last <= since:
            continue
        if settled_for is not None and now - last < settled_for:
            continue
        found = history.conversation(summary["id"])
        if found:
            item = dict(found["conversation"])
            item.pop("current", None)  # the CLI cannot see the daemon's live session
            out.append({**item, "turns": found["turns"]})
    return {
        "conversations": out,
        "count": len(out),
        "exported_at": now,
        "evidence": dict(EVIDENCE),
    }


def cmd_history_export(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    result = export(
        args.state_dir,
        _seconds(args.since, "--since", "1792800000"),
        conversation_id=args.id,
        settled_for=_seconds(args.settled_for, "--settled-for", "1800"),
    )
    if args.id is not None and not result["conversations"]:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"no conversation {args.id!r} in this state directory",
            remediation="run 'embodiment history export' to list the conversations",
        )
    if json_mode:
        emit_result(result, json_mode=True)
        return 0
    lines = [f"{result['count']} conversation(s)"]
    lines += [
        f"- {c['id']}  {len(c['turns'])} turns  {c['title']}" for c in result["conversations"]
    ]
    emit_result("\n".join(lines), json_mode=False)
    return 0


def cmd_history_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "embodiment history", _history_sections(), json_mode=bool(getattr(args, "json", False))
    )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("history", help="Read Gwen's past conversations (read-only).")
    p.add_argument("--json", action="store_true", help=_JSON_HELP)
    p.set_defaults(func=cmd_history_overview, json=False)
    noun = p.add_subparsers(dest="history_command", parser_class=type(p))

    ex = noun.add_parser("export", help="Print past conversations with their turns.")
    ex.add_argument("--id", default=None, help="Only this conversation (the event's id).")
    ex.add_argument("--since", default=None, help="Only conversations newer than epoch seconds.")
    ex.add_argument(
        "--settled-for",
        default=None,
        metavar="SECONDS",
        help="Only conversations quiet for at least this long (e.g. 1800).",
    )
    ex.add_argument("--state-dir", default=None, help="Daemon state directory (absolute path).")
    ex.add_argument("--json", action="store_true", help=_JSON_HELP)
    ex.set_defaults(func=cmd_history_export)

    ov = noun.add_parser("overview", help="Describe the history verbs.")
    ov.add_argument("--json", action="store_true", help=_JSON_HELP)
    ov.set_defaults(func=cmd_history_overview)
