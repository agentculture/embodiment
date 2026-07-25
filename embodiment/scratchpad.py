"""A scratchpad that survives a reset — working memory, not a lab notebook.

The first version of this file was a journal: the model recorded what it had
already done, so a reader could grade the route afterwards. That is the wrong
artefact. A journal is written for whoever comes later; **working memory is
written for yourself, in case you stop existing between two thoughts.**

The ordering is the whole design:

    intend("Split by parity and recurse on n; I expect a two-term recurrence.")
    ... act ...
    observe("n=3 gives 3 even of 5 — the recurrence holds so far.")
    conclude("The count of even-sum subsets is 76.")

**The intent is written BEFORE the act.** That single rule is what makes a reset
survivable. A mind that wakes with no context and reads

    n4 [intend]  Check n=5 against the recurrence before trusting it.
    (nothing after it)

knows exactly two things: what it meant to do, and that it had not yet done it.
A journal written after the fact cannot tell you that — the most important entry
is precisely the one that never got written.

This is the claim issue #2 makes about the package as a whole — *"an embodiment
without memory is a sequence of awakenings"* — applied one level down: not
continuity between sessions, but continuity across a **dropped thought**.

**Reading only the scratchpad should show the mind.** Not a transcript, not a
token count — a short ordered record of intent, observation and belief, where
the *gap* between an intent and its observation is itself information. Entries
are one sentence and carry ids because a mind you can read in twenty lines is
one you can actually check.

Persistence is deliberate: the pad is written to disk as it goes, because a
working memory that only exists inside the process it serves protects against
nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "KINDS",
    "Entry",
    "Scratchpad",
    "SCRATCHPAD_TOOLS",
    "JUDGE_TOOLS",
    "PROTOCOL",
    "RESUME_PROTOCOL",
    "render",
    "structure",
    "judge_journey",
    "resume_report",
]

#: ``intend`` comes first on purpose — it is the entry that makes a reset
#: survivable, and the only one that must precede what it describes.
KINDS = ("intend", "observe", "conclude", "revise")

PROTOCOL = (
    "You have a scratchpad that survives you. If this process dies mid-task, a "
    "fresh one wakes with NO memory and only this pad to go on — so write it for "
    "that successor, who is you.\n\n"
    "  intend(text)   what you are ABOUT to do, and what you expect. Write this "
    "BEFORE you do it, always.\n"
    "  observe(text)  what actually happened, and whether it matched.\n"
    "  conclude(text) what you now believe, and why.\n"
    "  revise(id, text)  correct an earlier entry you now know was wrong.\n"
    "  read()         re-read the pad.\n\n"
    "One sentence per entry. The rule that matters: never act before recording "
    "the intent. An intent with no observation after it tells your successor "
    "exactly where you were interrupted — that gap is the point."
)

RESUME_PROTOCOL = (
    "You are resuming work that was interrupted. You have no memory of it. The "
    "scratchpad below is everything you left yourself.\n\n"
    "Read it, work out where you got to, and continue. If the last entry is an "
    "`intend` with nothing after it, you were interrupted before doing that "
    "thing — do it now. Do not restart from the beginning, and do not assume "
    "work was done that the pad does not record."
)


@dataclass
class Entry:
    id: str
    kind: str
    text: str
    revises: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "kind": self.kind, "text": self.text}
        if self.revises:
            payload["revises"] = self.revises
        return payload


@dataclass
class Scratchpad:
    """An id-addressed working memory, persisted as it is written."""

    path: Optional[Path] = None
    entries: list[Entry] = field(default_factory=list)
    answer: Optional[str] = None
    rejected: list[str] = field(default_factory=list)

    # ── persistence: a memory that dies with its process is only a cache ────

    @classmethod
    def load(cls, path: Any) -> "Scratchpad":
        """Reopen a pad left by a previous process. An absent file is a fresh mind."""
        pad = cls(path=Path(path))
        if pad.path is not None and pad.path.exists():
            for line in pad.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue  # a torn final write is survivable; skip it
                if raw.get("answer") is not None:
                    pad.answer = str(raw["answer"])
                else:
                    pad.entries.append(
                        Entry(
                            id=str(raw.get("id", "")),
                            kind=str(raw.get("kind", "")),
                            text=str(raw.get("text", "")),
                            revises=raw.get("revises"),
                        )
                    )
        return pad

    def _append(self, payload: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
            handle.flush()

    # ── what the pad can answer about itself ────────────────────────────────

    @property
    def open_intent(self) -> Optional[Entry]:
        """The intent with no observation after it — where a reset caught this mind.

        The single most valuable thing a resuming mind can read, and it exists
        only because intents are written *before* the act they describe.
        """
        for entry in reversed(self.entries):
            if entry.kind == "observe":
                return None
            if entry.kind == "intend":
                return entry
        return None

    # ── the tool surface ────────────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        from embodiment import ToolOutcome

        if name in ("intend", "observe", "conclude"):
            text = str(arguments.get("text", "")).strip()
            if not text:
                self.rejected.append(f"empty {name}")
                return ToolOutcome(result="text must not be empty")
            entry = Entry(id=f"n{len(self.entries) + 1}", kind=name, text=text)
            self.entries.append(entry)
            self._append(entry.to_dict())
            return ToolOutcome(result=f"{entry.id} recorded")

        if name == "revise":
            target = str(arguments.get("id", "")).strip()
            text = str(arguments.get("text", "")).strip()
            known = {e.id for e in self.entries}
            if target not in known:
                self.rejected.append(f"revise unknown id {target!r}")
                return ToolOutcome(result=f"no entry {target!r}; known: {sorted(known)}")
            if not text:
                return ToolOutcome(result="text must not be empty")
            entry = Entry(id=f"n{len(self.entries) + 1}", kind="revise", text=text, revises=target)
            self.entries.append(entry)
            self._append(entry.to_dict())
            return ToolOutcome(result=f"{entry.id} revises {target}")

        if name == "read":
            return ToolOutcome(result=render(self) or "the scratchpad is empty")

        if name == "finish":
            self.answer = str(arguments.get("answer", "")).strip()
            self._append({"answer": self.answer})
            return ToolOutcome(result="submitted", finished=True, finish_summary=self.answer)

        return ToolOutcome(result=f"unknown tool {name}")

    def state(self) -> str:
        if not self.entries:
            return "nothing written yet"
        pending = self.open_intent
        if pending is not None:
            return f"{len(self.entries)} entries; mid-intent ({pending.id})"
        return f"{len(self.entries)} entries; last: {self.entries[-1].kind}"


def _text_tool(name: str, description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "One sentence."}},
                "required": ["text"],
            },
        },
    }


SCRATCHPAD_TOOLS: list[dict[str, Any]] = [
    _text_tool(
        "intend",
        "Record what you are ABOUT to do and what you expect — before you do it. "
        "If you are interrupted, this is what tells your successor where you were.",
    ),
    _text_tool("observe", "Record what actually happened, and whether it matched the intent."),
    _text_tool("conclude", "Record what you now believe, and why."),
    {
        "type": "function",
        "function": {
            "name": "revise",
            "description": "Correct an earlier entry you now know was wrong, by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "e.g. n2"},
                    "text": {"type": "string", "description": "One sentence."},
                },
                "required": ["id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Re-read the scratchpad.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the final answer.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    },
]


def render(pad: Scratchpad) -> str:
    """The pad as a resuming mind reads it — and as a human glimpses it."""
    if not pad.entries:
        return ""
    lines = []
    for entry in pad.entries:
        arrow = f"  <- revises {entry.revises}" if entry.revises else ""
        lines.append(f"{entry.id:>3} [{entry.kind:<8}] {entry.text}{arrow}")
    pending = pad.open_intent
    if pending is not None:
        lines.append(f"    ! {pending.id} has no observation — you were interrupted here.")
    if pad.answer is not None:
        lines.append(f"    answer: {pad.answer}")
    return "\n".join(lines)


def structure(pad: Scratchpad) -> dict[str, Any]:
    """Facts readable without interpretation. Deliberately not a quality score.

    Counting entries measures nothing worth knowing. What is here is only what
    can be read off directly — including the one thing that decides whether a
    reset is survivable: is an intent left dangling, and can it be acted on.
    Judgement is :func:`judge_journey`'s job, and it needs a mind.
    """
    kinds = [e.kind for e in pad.entries]
    pending = pad.open_intent
    return {
        "sequence": kinds,
        "open_intent": pending.id if pending else None,
        "revised_ids": [e.revises for e in pad.entries if e.revises],
        "rejected_calls": pad.rejected,
        "answered": pad.answer is not None,
        "persisted": bool(pad.path and pad.path.exists()),
    }


JUDGE_BRIEF = (
    "You are reading another agent's working memory — not a transcript, but a "
    "scratchpad it wrote for itself in case it was interrupted. You have the "
    "problem, the correct answer, the pad in order, and what it submitted.\n\n"
    "Two questions. The second matters more.\n\n"
    "1. Does the route EARN the answer? A correct answer reached by luck, by "
    "recalling a known result, or by asserting the key step is not the same as "
    "deriving it — say so plainly when you see it. A wrong answer reached by "
    "sound reasoning that slipped once is the better performance.\n\n"
    "2. Could a mind with NO memory resume from this pad alone? Read it as that "
    "successor. Does each intent say enough to act on? Would you know what had "
    "already been done and what had not? If the pad ends on an intent with no "
    "observation, could you tell what to do next — or is the record too thin to "
    "recover from?\n\n"
    "Call verdict exactly once."
)

JUDGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "verdict",
            "description": "Judge the working and whether it survives a reset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "earned": {
                        "type": "boolean",
                        "description": (
                            "Is the answer earned by this route, not guessed or recalled?"
                        ),
                    },
                    "weakest_step": {
                        "type": "string",
                        "description": "Id of the least justified step, and why.",
                    },
                    "resumable_by_a_blank_mind": {
                        "type": "boolean",
                        "description": (
                            "Could a successor with no memory continue from this pad alone?"
                        ),
                    },
                    "what_a_successor_would_miss": {
                        "type": "string",
                        "description": (
                            "What the pad fails to record that a resuming mind would need."
                        ),
                    },
                    "self_correction": {
                        "type": "string",
                        "description": "What any revision actually fixed, or 'none'.",
                    },
                    "verdict": {
                        "type": "string",
                        "description": "Two sentences on this mind's working.",
                    },
                },
                "required": [
                    "earned",
                    "weakest_step",
                    "resumable_by_a_blank_mind",
                    "what_a_successor_would_miss",
                    "self_correction",
                    "verdict",
                ],
            },
        },
    }
]


def judge_journey(
    pad: Scratchpad,
    *,
    problem: str,
    truth: str,
    complete: Any,
    run_fn: Any = None,
) -> dict[str, Any]:
    """Have a model read the pad and judge both the route and its survivability.

    Pass the *muse* seam where one exists: judging working is far closer to the
    muse's advertised ``divergent_second_opinion`` than solving is, and the muse
    has now failed to help on four solving tasks.

    The judge sees the ground truth on purpose — without it, a sound derivation
    and a confident assertion that lands on the right number are
    indistinguishable, and telling those apart is the whole reason this exists
    rather than a tally.
    """
    from embodiment import Task, ToolOutcome
    from embodiment import run as default_run

    drive = run_fn or default_run

    class _Judge:
        def __init__(self) -> None:
            self.payload: dict[str, Any] = {}

        def execute(self, name: str, arguments: dict[str, Any]) -> Any:
            if name == "verdict":
                self.payload = dict(arguments)
                return ToolOutcome(
                    result="recorded",
                    finished=True,
                    finish_summary=str(arguments.get("verdict", "")),
                )
            return ToolOutcome(result=f"unknown tool {name}")

        def state(self) -> str:
            return "judging" if not self.payload else "judged"

    judge = _Judge()
    outcome = drive(
        complete,
        Task(
            id="judge",
            repo_path="",
            instruction=(
                f"{JUDGE_BRIEF}\n\nPROBLEM:\n{problem}\n\nCORRECT ANSWER: {truth}\n\n"
                f"THE SCRATCHPAD:\n{render(pad) or '(empty)'}\n\n"
                f"IT SUBMITTED: {pad.answer!r}"
            ),
        ),
        executor=judge,
        max_steps=6,
    )
    return {"judged": bool(judge.payload), "exit": outcome.exit_reason, **judge.payload}
