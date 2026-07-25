"""A scratchpad tool surface — force the thinking out where it can be seen.

The live-test series turned up one dominant failure: drives ending
``exit=stopped`` at ``turns=2`` having called **no tools at all**. The thinking
telemetry (``examples/thinking.py``) diagnosed it — roughly 15,000 characters of
internal reasoning per turn against a few hundred characters written, a 66:1
thought-to-written ratio, and an empty tool sequence. The model was not failing
to reason. It reasoned enormously and then emitted prose instead of a structured
call.

This is the counter-measure, and it is a tool surface rather than a prompt
tweak. A scratchpad the model **must** write to, one short entry per call:

    note(kind="approach", text="Split by parity and recurse on n.")   -> n1
    note(kind="claim",    text="S(n) obeys a two-term recurrence.")   -> n2
    note(kind="check",    text="n=3 gives 3 even of 5 — matches.")    -> n3
    revise(id="n2", text="Coupling flips when n is odd.")             -> n4
    finish(answer="76")

Two things follow, and the second is the point.

**It gives the reasoning somewhere to go.** A turn that must end in a call
cannot end in an essay. Whether this actually lowers the stop rate is an
empirical question this file exists to answer — not an assumption.

**It makes the journey evaluable.** Until now an embodiment could only be
scored on its answer. A scratchpad records the *route*: did it state an approach
before making claims, did it check anything, did it ever revise itself? A run
that reaches the right answer having checked nothing is not the same as one that
tested a claim and corrected it, and outcome scoring cannot tell them apart.

**Evaluation is a judgement, not a tally.** Counting notes measures nothing
worth knowing — a long scratchpad is not a good one, and a model can label a
line ``check`` having checked nothing. :func:`structure` therefore reports
only what can be read off without interpretation, and :func:`judge_journey`
hands the route to a *model* with the ground truth in hand, asking the
question a tally cannot: does this route EARN the answer, or did it arrive by
luck, recall, or assertion? Judging working is far closer to the muse's
advertised ``divergent_second_opinion`` than solving is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "KINDS",
    "Note",
    "Scratchpad",
    "SCRATCHPAD_TOOLS",
    "structure",
    "judge_journey",
    "render",
    "PROTOCOL",
]

#: The vocabulary. Small on purpose — a long menu invites classification games
#: instead of work, and every extra kind is another thing to get wrong.
KINDS = ("approach", "claim", "check", "revision", "conclusion")

PROTOCOL = (
    "You have a scratchpad. Use it as you work — one short entry per call, a "
    "single sentence each:\n"
    "  note(kind='approach', text=...)   how you intend to attack this\n"
    "  note(kind='claim', text=...)      something you now believe\n"
    "  note(kind='check', text=...)      a claim you tested, and what happened\n"
    "  note(kind='conclusion', text=...) what you have concluded\n"
    "  revise(id='n2', text=...)         correct an earlier note by its id\n"
    "Start with an approach note before you claim anything. Check your claims "
    "rather than asserting them. If you find you were wrong, revise the note "
    "rather than quietly moving on — a corrected claim is worth more than a "
    "confident one. Call finish only when the scratchpad shows your route."
)


@dataclass
class Note:
    id: str
    kind: str
    text: str
    revises: Optional[str] = None


@dataclass
class Scratchpad:
    """An id-addressed notebook the model writes as it works."""

    notes: list[Note] = field(default_factory=list)
    answer: Optional[str] = None
    rejected: list[str] = field(default_factory=list)

    # ── the tool surface ──────────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        from embodiment import ToolOutcome

        if name == "note":
            kind = str(arguments.get("kind", "")).strip().lower()
            text = str(arguments.get("text", "")).strip()
            if kind not in KINDS:
                self.rejected.append(f"unknown kind {kind!r}")
                return ToolOutcome(result=f"kind must be one of {', '.join(KINDS)}")
            if not text:
                self.rejected.append("empty note")
                return ToolOutcome(result="text must not be empty")
            note = Note(id=f"n{len(self.notes) + 1}", kind=kind, text=text)
            self.notes.append(note)
            return ToolOutcome(result=f"{note.id} recorded ({kind})")

        if name == "revise":
            target = str(arguments.get("id", "")).strip()
            text = str(arguments.get("text", "")).strip()
            known = {n.id for n in self.notes}
            if target not in known:
                self.rejected.append(f"revise unknown id {target!r}")
                return ToolOutcome(result=f"no note {target!r}; known: {sorted(known)}")
            if not text:
                return ToolOutcome(result="text must not be empty")
            note = Note(id=f"n{len(self.notes) + 1}", kind="revision", text=text, revises=target)
            self.notes.append(note)
            return ToolOutcome(result=f"{note.id} revises {target}")

        if name == "read":
            if not self.notes:
                return ToolOutcome(result="scratchpad is empty")
            lines = [
                f"{n.id} [{n.kind}]"
                + (f" (revises {n.revises})" if n.revises else "")
                + f" {n.text}"
                for n in self.notes
            ]
            return ToolOutcome(result="\n".join(lines))

        if name == "finish":
            self.answer = str(arguments.get("answer", "")).strip()
            return ToolOutcome(result="submitted", finished=True, finish_summary=self.answer)

        return ToolOutcome(result=f"unknown tool {name}")

    def state(self) -> str:
        if not self.notes:
            return "nothing written yet"
        return f"{len(self.notes)} note(s); last: {self.notes[-1].kind}"


SCRATCHPAD_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "note",
            "description": (
                "Record ONE short sentence about your work: an approach, a claim, "
                "a check you ran, or a conclusion. Returns the note's id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "text": {"type": "string", "description": "One sentence."},
                },
                "required": ["kind", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revise",
            "description": "Correct an earlier note by its id, when you find it wrong.",
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
            "description": "Read the scratchpad back.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the final answer, once the scratchpad shows your route.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    },
]


def structure(pad: Scratchpad) -> dict[str, Any]:
    """Objective facts about the record. Deliberately NOT a quality score.

    Counting notes and characters measures nothing worth knowing — a long
    scratchpad is not a good one, and a model can label a line ``check`` having
    checked nothing. What is here is only what can be read off without
    interpretation: what was written, in what order, and what it pointed at.
    Judgement is :func:`judge_journey`'s job, and it needs a mind to do it.
    """
    kinds = [n.kind for n in pad.notes]
    return {
        "sequence": kinds,
        "revised_ids": [n.revises for n in pad.notes if n.revises],
        "rejected_calls": pad.rejected,
        "answered": pad.answer is not None,
    }


JUDGE_BRIEF = (
    "You are reviewing another agent's WORKING, not its answer. You are given "
    "the problem, the correct answer, the agent's scratchpad in order, and what "
    "it finally submitted.\n\n"
    "Judge the ROUTE. The question is not whether it arrived — it is whether "
    "the route earns the destination.\n\n"
    "Look for, specifically:\n"
    "- an approach that actually fits the problem, rather than a generic plan;\n"
    "- claims that follow from what came before, rather than being asserted;\n"
    "- checks that genuinely test the claim they name — a 'check' that restates "
    "the claim tests nothing;\n"
    "- a revision that is a real correction, rather than a restatement;\n"
    "- and the case that matters most: a CORRECT answer reached by an UNSOUND "
    "route. Getting there by luck, by recalling a known result, or by asserting "
    "the key step is not the same as deriving it. Say so plainly when you see "
    "it.\n\n"
    "The reverse also matters: a WRONG answer reached by sound reasoning that "
    "slipped once is a better performance than a lucky right one, and should be "
    "scored that way.\n\n"
    "Call verdict exactly once."
)

JUDGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "verdict",
            "description": "Deliver the judgement on the working.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route_sound": {
                        "type": "boolean",
                        "description": "Does the working actually establish the answer?",
                    },
                    "earned": {
                        "type": "boolean",
                        "description": (
                            "Is the answer EARNED by this route, as opposed to "
                            "guessed, recalled, or asserted?"
                        ),
                    },
                    "weakest_step": {
                        "type": "string",
                        "description": "The note id of the least justified step, and why.",
                    },
                    "checks_were_real": {
                        "type": "boolean",
                        "description": "Did the checks test their claims, or restate them?",
                    },
                    "self_correction": {
                        "type": "string",
                        "description": "What any revision actually fixed, or 'none'.",
                    },
                    "verdict": {
                        "type": "string",
                        "description": "Two sentences: what this working did well and badly.",
                    },
                },
                "required": [
                    "route_sound",
                    "earned",
                    "weakest_step",
                    "checks_were_real",
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
    """Have a model read the scratchpad and judge whether the route earns the answer.

    ``complete`` is a model seam — pass the *muse* model where one is available.
    Judging working is much closer to ``divergent_second_opinion``, the muse's
    advertised role, than solving is, and it is the first task in this series
    that plays to it.

    The judge is shown the ground truth on purpose. Without it, it cannot
    distinguish a sound derivation from a confident assertion that happens to
    land on the right number — and that distinction is the entire reason this
    function exists rather than a note count.
    """
    from embodiment import Task, ToolOutcome
    from embodiment import run as default_run

    drive = run_fn or default_run
    transcript = render(pad) or "(the agent wrote nothing)"

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
    instruction = (
        f"{JUDGE_BRIEF}\n\n"
        f"PROBLEM:\n{problem}\n\n"
        f"CORRECT ANSWER: {truth}\n\n"
        f"THE AGENT'S SCRATCHPAD:\n{transcript}\n\n"
        f"IT SUBMITTED: {pad.answer!r}"
    )
    outcome = drive(
        complete,
        Task(id="judge", repo_path="", instruction=instruction),
        executor=judge,
        max_steps=6,
    )
    return {
        "judged": bool(judge.payload),
        "exit": outcome.exit_reason,
        **judge.payload,
    }


def render(pad: Scratchpad) -> str:
    """The journey as a human reads it."""
    if not pad.notes:
        return "(scratchpad empty — the model wrote nothing)"
    lines = []
    for n in pad.notes:
        arrow = f" ⟵ revises {n.revises}" if n.revises else ""
        lines.append(f"  {n.id:>3} [{n.kind:<10}] {n.text}{arrow}")
    if pad.answer is not None:
        lines.append(f"  answer: {pad.answer}")
    return "\n".join(lines)
