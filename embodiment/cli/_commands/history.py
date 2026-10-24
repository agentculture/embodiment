"""``embodiment history`` — Gwen's past conversations, read-only: ``export``.

Deviation d5 (operator, 2026-10-24): conversations are to be "indexed via
culture-rules to the library". Gwen never writes to the library (c26), so this
verb only READS the per-session logs the daemon writes and prints them; culture-
rules runs it on its schedule, renders each conversation as a private library
item and pushes it. ``--since`` hands back only the conversations whose last turn
is newer than a timestamp, so a scheduled run stays incremental.

Built to run under culture-rules' runner, like ``embodiment memory``: no stdin, a
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
from embodiment.daemon.history import ConversationHistory
from embodiment.daemon.state import SESSIONS_DIRNAME, resolve_state_dir

_JSON_HELP = "Emit structured JSON."

_HISTORY_VERBS = [
    "history export [--since <epoch-seconds>] [--state-dir DIR] [--json] — print past"
    " conversations with their turns (read-only)",
    "history overview — describe the history verbs (this command)",
]


def _history_sections() -> list[dict[str, object]]:
    return [
        {"title": "Verbs", "items": list(_HISTORY_VERBS)},
        {
            "title": "Conventions",
            "items": [
                "read-only: the verb never writes the state dir, the store or the library",
                "culture-rules turns each conversation into a private library item (d5)",
                "every command supports --json; the JSON carries the conversation text",
            ],
        },
    ]


def _since(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--since must be epoch seconds, got {raw!r}",
            remediation="pass a number such as 1792800000, or omit --since",
        )
    return value


def export(state_dir: Optional[str], since: Optional[float]) -> dict[str, Any]:
    """Every listed conversation (newer than *since*), with its turns, newest first."""
    history = ConversationHistory(Path(resolve_state_dir(state_dir)) / SESSIONS_DIRNAME)
    out = []
    for summary in history.conversations()["conversations"]:
        if since is not None and (summary["last"] or 0.0) <= since:
            continue
        found = history.conversation(summary["id"])
        if found:
            item = dict(found["conversation"])
            item.pop("current", None)  # the CLI cannot see the daemon's live session
            out.append({**item, "turns": found["turns"]})
    return {"conversations": out, "count": len(out), "exported_at": time.time()}


def cmd_history_export(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    result = export(args.state_dir, _since(args.since))
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
    ex.add_argument("--since", default=None, help="Only conversations newer than epoch seconds.")
    ex.add_argument("--state-dir", default=None, help="Daemon state directory (absolute path).")
    ex.add_argument("--json", action="store_true", help=_JSON_HELP)
    ex.set_defaults(func=cmd_history_export)

    ov = noun.add_parser("overview", help="Describe the history verbs.")
    ov.add_argument("--json", action="store_true", help=_JSON_HELP)
    ov.set_defaults(func=cmd_history_overview)
