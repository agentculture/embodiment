"""``embodiment drone`` — author, run and discover drones.

A **drone** is a named unit that does small-smart tasks (explore, review,
search) as deterministic code plus **scoped** calls to a worker where judgement
is genuinely needed. The library half lives in :mod:`embodiment.drone`; this
module is the CLI substrate.

Why a CLI verb and not only a skill (issue #45, open question 4): authoring is
genuinely prompt-shaped, but ``evoke`` is mechanical and must be callable by a
script or CI **without a mind in the loop**. So the verbs are the substrate and
a skill wraps them.

Three verbs, plus the noun's own ``overview``:

* ``create`` — stage, smoke-prove, then save. A drone that fails its smoke
  invocation is never written to ``.drones/``.
* ``evoke`` — run a saved drone. Cheap: code plus tens of tokens.
* ``list`` — discovery, so "is there already a drone for this?" costs one
  command instead of an authoring turn.

**This runs model-written code in this process. There is no sandbox** — see
``embodiment explain drone`` and every drone's generated README.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from embodiment import drone as drone_lib
from embodiment.cli._commands.overview import emit_overview
from embodiment.cli._commands.whoami import read_agent_fields
from embodiment.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from embodiment.cli._output import emit_diagnostic, emit_result


def _as_cli_error(exc: drone_lib.DroneError) -> CliError:
    return CliError(
        code=EXIT_ENV_ERROR if exc.env else EXIT_USER_ERROR,
        message=exc.message,
        remediation=exc.remediation,
    )


def _drones_dir(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "drones_dir", None)
    if explicit:
        return Path(explicit).expanduser()
    return drone_lib.find_drones_dir()


def _read_json_file(path: Path, what: str) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot read the {what} file {path}: {exc.strerror or exc}",
            remediation="check the path exists and is readable",
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{path} is not valid JSON: {exc}",
            remediation=f"the {what} file must be a JSON object",
        ) from exc
    if not isinstance(data, dict):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{path} is a JSON {type(data).__name__}, not an object",
            remediation=f"the {what} file must be a JSON object",
        )
    return data


# ── overview ────────────────────────────────────────────────────────────────


def drone_sections() -> list[dict[str, object]]:
    """Describe the drone subsystem — including the threat model, per C2."""
    return [
        {
            "title": "Verbs",
            "items": [
                "drone create <name> — author a drone; refuses to save one that "
                "fails its smoke invocation",
                "drone evoke <name> — run a saved drone (code plus tens of tokens)",
                "drone list — what exists, what each does, how old it is, its status",
                "drone overview — this description",
            ],
        },
        {
            "title": "When a drone is worth authoring",
            "items": [
                "authoring costs one cortex turn (measured: 5,000-14,265 completion "
                "tokens, 400-730 s)",
                "a task done once: pure loss",
                "a task done 2-3 times: roughly break-even",
                "a task done often on a stable surface: wins by a widening margin",
                "the failure mode is quiet waste, not a crash",
            ],
        },
        {
            "title": "The artifact (committed, not hidden)",
            "items": [
                f"{drone_lib.DRONES_DIRNAME}/<name>/manifest.json — description, "
                "purpose, provenance, assumed surface, declared questions + schemas",
                f"{drone_lib.DRONES_DIRNAME}/<name>/drone.py — the code the cortex "
                f"wrote; entry point `{drone_lib.DRONE_ENTRYPOINT}(request)`",
                f"{drone_lib.DRONES_DIRNAME}/<name>/README.md — what it does, when "
                "it is wrong, how to re-author it",
            ],
        },
        {
            "title": "Threat model (stated, never implied by a name)",
            "items": [
                "evoke imports and runs model-written Python IN THIS PROCESS, with "
                "the permissions this process already has",
                "there is NO sandbox; the manifest's capabilities list is a "
                "declaration for review, not an enforcement boundary",
                "read drone.py before evoking a drone you did not author",
                "the network-less workspace jail stays available per-drone; it is "
                "not the default",
            ],
        },
        {
            "title": "Limits of v1",
            "items": [
                "an undecidable case returns 'I cannot' — there is no escalate-to-"
                "cortex path, which is the whole point of the cost model",
                f"status is {drone_lib.STATUS_UNCHECKED!r} until a host wires an "
                "assumed-surface check; no check ran is reported as no check ran",
            ],
        },
    ]


def cmd_drone_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "embodiment drone",
        drone_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


# ── create ──────────────────────────────────────────────────────────────────


def cmd_drone_create(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    drones_dir = _drones_dir(args)
    draft: Mapping[str, Any] = {}
    if args.manifest:
        draft = _read_json_file(Path(args.manifest).expanduser(), "draft manifest")
    notes: Optional[str] = None
    if args.notes:
        notes_path = Path(args.notes).expanduser()
        try:
            notes = notes_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"cannot read the notes file {notes_path}: {exc.strerror or exc}",
                remediation="check the path exists and is readable",
            ) from exc

    author_model = args.author_model
    if not author_model:
        draft_author = draft.get("author")
        if isinstance(draft_author, Mapping):
            author_model = draft_author.get("model")
    if not author_model:
        author_model = read_agent_fields().get("model")

    try:
        created = drone_lib.create(
            args.name,
            source=Path(args.source).expanduser(),
            drones_dir=drones_dir,
            draft=draft,
            description=args.description,
            author_model=author_model,
            commit=args.commit,
            notes=notes,
            force=args.force,
        )
    except drone_lib.DroneError as exc:
        raise _as_cli_error(exc) from exc

    smoke_block = created.manifest.get("smoke", {})
    payload = {
        "name": created.name,
        "path": str(created.home),
        "description": created.description,
        "capabilities": list(created.capabilities),
        "questions": [str(q.get("id")) for q in created.questions],
        "smoke": {
            "passed": bool(smoke_block.get("passed")),
            "summary": str(smoke_block.get("summary", "")),
        },
    }
    if json_mode:
        emit_result(payload, json_mode=True)
        return 0
    lines = [
        f"created drone: {created.name}",
        f"  does:    {created.description}",
        f"  home:    {created.home}",
        f"  asks:    {len(created.questions)} declared question(s)",
        f"  grants:  {', '.join(created.capabilities) or 'no capabilities declared'}",
        f"  smoke:   passed — {smoke_block.get('summary', '')}",
    ]
    emit_result("\n".join(lines), json_mode=False)
    emit_diagnostic(
        "note: this drone is model-written code that runs in-process with no "
        "sandbox — commit it and review it like any script (see its README.md)"
    )
    return 0


# ── evoke ───────────────────────────────────────────────────────────────────


def _parse_args_pairs(pairs: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"--arg expects KEY=VALUE, got {pair!r}",
                remediation="pass each argument as --arg key=value",
            )
        parsed[key] = value
    return parsed


def cmd_drone_evoke(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    drones_dir = _drones_dir(args)
    try:
        drone = drone_lib.load(args.name, drones_dir)
    except drone_lib.DroneError as exc:
        raise _as_cli_error(exc) from exc

    ask = drone_lib.no_worker_ask
    if args.answers:
        answers = _read_json_file(Path(args.answers).expanduser(), "answers")
        ask = drone_lib.mapping_ask(answers)
    elif drone.questions:
        emit_diagnostic(
            f"note: {drone.name} declares {len(drone.questions)} scoped question(s) "
            "and no worker seam is wired, so they will go unanswered — inject one "
            "with embodiment.drone.invoke(..., ask=...) or pass --answers"
        )

    evocation = drone_lib.invoke(
        drone,
        root=drones_dir.parent,
        args=_parse_args_pairs(args.arg),
        ask=ask,
    )
    if not evocation.ok:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"drone {drone.name!r} did not complete: {evocation.failure}",
            remediation=(
                f"read {drone.home / 'drone.py'} — this is the drone's own code "
                "failing, not the harness; re-author it with "
                f"'embodiment drone create {drone.name} --force'"
            ),
        )

    author = drone.manifest.get("author", {})
    authored = str(author.get("date", ""))
    if json_mode:
        emit_result(
            {
                "name": evocation.name,
                "ok": True,
                "answer": evocation.answer,
                "cannot": evocation.cannot,
                "detail": dict(evocation.detail),
                "source_sha256": evocation.source_sha256,
                "capabilities": list(evocation.capabilities),
                "calls": [
                    {"question": c.question, "accepted": c.accepted, "reason": c.reason}
                    for c in evocation.calls
                ],
                "calls_asked": len(evocation.calls),
                "calls_accepted": evocation.calls_accepted,
                "call_acceptance": evocation.call_acceptance,
                "authored": authored,
                "age": drone_lib.humanize_age(authored),
                "model": str(author.get("model", "")),
                "commit": str(author.get("commit", "")),
            },
            json_mode=True,
        )
        return 0

    lines = [
        f"drone: {evocation.name}",
        f"authored: {drone_lib.humanize_age(authored)} by {author.get('model', 'unknown')} "
        f"against commit {author.get('commit', 'unknown')}",
        "",
    ]
    if evocation.cannot:
        lines.append(f"I cannot: {evocation.cannot}")
    else:
        lines.append(str(evocation.answer))
    acceptance = evocation.call_acceptance
    lines += [
        "",
        f"calls: {len(evocation.calls)} asked, {evocation.calls_accepted} accepted"
        + (f" ({acceptance:.0%})" if acceptance is not None else " (none asked)"),
    ]
    emit_result("\n".join(lines), json_mode=False)
    return 0


# ── list ────────────────────────────────────────────────────────────────────


def cmd_drone_list(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    drones_dir = _drones_dir(args)
    # `status_fn` is left unwired here on purpose: the assumed-surface re-check
    # is task t12's, and reporting "ok" because nothing looked would be exactly
    # the confident false claim the status column exists to prevent.
    records = drone_lib.catalog(drones_dir, now=datetime.now(timezone.utc))
    for record in records:
        if record.problem:
            emit_diagnostic(f"warning: drone {record.name!r}: {record.problem}")
    if json_mode:
        emit_result(
            {
                "drones_dir": str(drones_dir),
                "drones": [
                    {
                        "name": r.name,
                        "description": r.description,
                        "authored": r.authored,
                        "age": r.age,
                        "status": r.status,
                        "model": r.model,
                        "commit": r.commit,
                        "problem": r.problem,
                    }
                    for r in records
                ],
            },
            json_mode=True,
        )
        return 0
    emit_result(drone_lib.render_catalog(records), json_mode=False)
    return 0


# ── registration ────────────────────────────────────────────────────────────


def _no_verb(args: argparse.Namespace) -> int:
    # `embodiment drone` with no sub-verb prints the noun's overview.
    return cmd_drone_overview(args)


def _add_shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--drones-dir",
        help=(
            f"Where drones live (default: <repo root>/{drone_lib.DRONES_DIRNAME}, "
            f"or ${drone_lib.DRONES_DIR_ENV})."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "drone",
        help="Author, run and discover drones (see 'embodiment drone overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (propagated by the top-level subparsers'
    # parser_class); propagate it again so verb-level parse errors route through
    # the structured error contract rather than argparse's default exit 2.
    noun_sub = p.add_subparsers(dest="drone_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the drone surface and threat model.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_drone_overview)

    create = noun_sub.add_parser(
        "create",
        help="Author a drone. Refuses to save one that fails its smoke invocation.",
    )
    create.add_argument("name", help="Drone name (lowercase [a-z0-9._-]).")
    create.add_argument("--source", required=True, help="Path to the drone.py to save.")
    create.add_argument(
        "--description",
        help="REQUIRED one-line description — what `list` prints. May also come "
        "from the draft manifest; create refuses if neither supplies one.",
    )
    create.add_argument(
        "--manifest",
        help="Draft manifest JSON: purpose, assumed_surface, capabilities, "
        "questions, and the smoke block (args + one canned answer per question).",
    )
    create.add_argument("--notes", help="Optional markdown appended to the drone's README.")
    create.add_argument("--author-model", help="Authoring model (default: this agent's).")
    create.add_argument("--commit", help="Commit authored against (default: git rev-parse HEAD).")
    create.add_argument("--force", action="store_true", help="Replace an existing drone.")
    _add_shared(create)
    create.set_defaults(func=cmd_drone_create)

    evoke = noun_sub.add_parser("evoke", help="Run a saved drone.")
    evoke.add_argument("name", help="Drone name.")
    evoke.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="An argument for the drone; repeatable.",
    )
    evoke.add_argument(
        "--answers",
        help="JSON map of question id -> answer, for a scripted run with no live worker.",
    )
    _add_shared(evoke)
    evoke.set_defaults(func=cmd_drone_evoke)

    listing = noun_sub.add_parser(
        "list",
        help="What drones exist, what each does, how old, and their status.",
    )
    _add_shared(listing)
    listing.set_defaults(func=cmd_drone_list)
