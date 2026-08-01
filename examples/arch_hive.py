#!/usr/bin/env python3
"""arch_hive — arm B, the Bee-Hive: the worker as a **tool**, never an agent.

Plan task **t6** of `error-derived-timeouts-bee-hive-architecture`
(`docs/plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md`),
covering frame claims ``c17``/``h9`` (the worker-as-tool claim and its
structural honesty condition), ``c11``/``h15`` (bounded termination survives
the swarm) and decision ``c42`` (plain concurrent scoped calls, not fan-out).

The claim this module builds
----------------------------
The four arms in ``examples/arch_arms.py`` all hand the worker *something to
interpret*: ``E`` and ``W`` are flat, and ``M``/``H`` delegate a **subtask** —
a free-text goal the worker must read, plan against, and drive its own bounded
loop over. Arm **B** is structurally a different thing:

    the worker never runs a loop, never decides how to do anything, and never
    holds a turn. It is a fast pure function the cortex calls —
    ``transform(N things) -> N results``.

Three reasons that matters, and each one shapes something below:

1. It is the only arm that takes lobes' ``worker.forbidden_responsibilities =
   [final_decision, security_decision]`` literally. Every declared question is
   :data:`AUTHORITY_ADVISORY` and nothing here can promote one: an answer
   reaches the cortex as an observation, and the *only* place an answer becomes
   the attempt's output is the cortex's own ``finish`` call.
2. It removes the component nobody can grade. ``M`` and ``H`` require the
   worker to interpret an underspecified subtask, which is where the
   ``NO_ANSWER`` failures of issues #32/#33 came from. **A tool call has a
   schema; a delegated goal does not.**
3. If B wins, M and H are unnecessary complexity. If B loses to M, that is a
   real finding about agency being worth its cost.

The tier is a spectrum, held as data
------------------------------------
============  ==========================================================
tier          what it is
============  ==========================================================
``B0``        **rigid** — 0 play-time worker calls; a declared code policy
              answers every scoped question.
``B1``        **scoped** — N *small* worker calls, concurrent; each one
              delegates a QUESTION with a typed, small, enumerable answer
              space, and the harness keeps control flow.
``B2``        **agentic** — an open goal with the worker holding control.
              That is arm ``M``, which already exists and is already
              dialable. :data:`HIVE_ARMS` deliberately has no ``B2`` entry
              (:data:`TIER_ELSEWHERE` says where it lives); a second
              implementation of a shipped arm is not a tier.
============  ==========================================================

:data:`HIVE_ARMS` describes the two tiers this module ships and nothing here
branches on an arm id. ``B0`` and ``B1`` differ by exactly one field —
:attr:`HiveArm.answerer` — which is what makes ``B0`` a *control* for ``B1``
rather than a different experiment: same cortex surface, same questions, same
grain vocabulary, and the only variable is whether a scoped answer comes from a
declared code rule or from a bounded worker call.

The declaration IS the enforcement
----------------------------------
``examples/orchestrator_tools.py`` makes ``WORKER_TOOLS = ('report',)``
simultaneously the pre-registration of what a worker may call *and* the
dispatch condition enforcing it, so the two cannot drift. The same habit runs
through here:

* :data:`HIVE_QUESTIONS` is both the catalog a reader quotes and the ``enum``
  emitted into the cortex's ``transform`` schema; a question id outside it is
  refused at dispatch.
* :data:`HIVE_WORKER_TOOLS` is empty, and that is the enforcement: the hive
  never passes a tool schema to a worker, never passes a subagent seam to
  :func:`embodiment.run`, and never grants a spawn allowance. A hive worker
  cannot hold a turn because there is nothing to hold one with.
* Every answer space is enumerated *before* the call and re-checked *after* it
  (:func:`parse_answers`), so an out-of-space answer is a recorded refusal
  rather than a value that quietly propagates.

No free text reaches a worker, and none comes back
--------------------------------------------------
:func:`render_prompt` composes a worker prompt from harness-authored parts
only: the question's own ``ask`` line, the item's harness-authored ``facts``,
the enumerated options, and a fixed answer protocol. Nothing the cortex wrote
in prose is on that wire — the cortex selects a declared question id and a list
of declared item ids, and that is the whole of its influence. Symmetrically,
every property on every hive schema (cortex *and* worker) is either an ``enum``
or a bounded integer; :class:`tests.test_arch_hive.TestEnumerableAnswerSpaces`
walks them and fails any string property that permits arbitrary text.

Call-acceptance is measured apart from outcome
----------------------------------------------
Issue #33 measured 17 of 23 calls refused on a *shape* error. A harness that
folded refusals into outcome would report a broken arm as a losing arm, so
:class:`AcceptanceLedger` is a separate ledger with a disjoint key namespace
from :class:`HiveAttemptRecord`'s outcome fields, and
``tests/test_arch_hive.py`` asserts that disjointness rather than trusting it.

Concurrency, per decision ``c42``
---------------------------------
B1's scoped calls are **plain concurrent scoped calls** through the
``examples/worker_seam.py`` path the throughput probe already measured — not
fan-out unit dispatch, and not streaming (there is nothing to stream in a
tens-of-token answer). The partitioned-grant fan-out in
``examples/orchestrator_tools.py`` stays the M/H seam, because only units that
hold turns need turn budgets partitioned. Width is measured saturation (8), not
the advertised 14 — see ``docs/live-test-results/worker-throughput.md``.

Termination, by construction
----------------------------
There is **no** ``while`` in this file. Every ``for`` walks a settled sequence.
:func:`plan_calls` clamps the dispatch count before any thread exists, and the
drive's total scoped-call budget is clamped again by :class:`HiveOrchestrator`.
:func:`dispatch` makes exactly one :func:`concurrent.futures.wait` with a
finite deadline, reads every future with ``timeout=0``, and shuts its pool down
with ``wait=False`` in a ``finally``. ``tests/test_arch_hive.py``'s
``TestHiveTerminates`` proves all of that over this module's own AST, in the
shape ``tests/test_orchestrator_tools.py``'s ``TestFanoutTerminates`` and
``tests/test_muse_tool_loop_ast.py`` established.

No live dial happens here
-------------------------
The whole harness is exercisable hermetically with scripted minds; the live
lane is gated on ``EMBODIMENT_LIVE_RIG=1`` (``arch_arms.require_live_rig``).
Running the sweep is task ``t10``'s, under task ``t9``'s pre-registration.

Usage::

    uv run python examples/arch_hive.py plan
    uv run python examples/arch_hive.py schemas --arm B1 --json
    uv run python examples/arch_hive.py config --json
    uv run python examples/arch_hive.py run --arm B0 --arm B1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    LoopAborted,
    ModelResponse,
    Task,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    ledger,
    run,
)
from embodiment.contract import ToolCall  # noqa: E402
from examples import arch_arms as aa  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

__all__ = [
    "ACCEPTANCE_OUTCOMES",
    "ACCEPTED",
    "ABSENT_TIMEOUT",
    "ABSENT_TRANSPORT",
    "ABSENT_TRUNCATED",
    "ABSENCES",
    "FINISH_TRUNCATED",
    "REFUSALS",
    "finish_reason_of",
    "ANSWERER_CODE",
    "ANSWERER_WORKER",
    "AUTHORITY_ADVISORY",
    "AcceptanceLedger",
    "CODE_POLICIES",
    "DEFAULT_CONFIG_PATH",
    "FINISH_TOOL",
    "FORBIDDEN_RESPONSIBILITIES",
    "HIVE_ARMS",
    "HIVE_ARM_ORDER",
    "HIVE_ORCHESTRATOR_TOOLS",
    "HIVE_FINAL_AUTHORITY_TOOLS",
    "HIVE_QUESTIONS",
    "HIVE_WORKER_TOOLS",
    "HiveArm",
    "HiveAttemptRecord",
    "HiveConfig",
    "HiveItem",
    "HiveOrchestrator",
    "HiveSetup",
    "MAX_ANSWER_SPACE",
    "MAX_HIVE_WIDTH",
    "QUESTION_ORDER",
    "REFUSED_EMPTY",
    "REFUSED_OFF_SPACE",
    "SLOT_LABELS",
    "SPACE_LITERAL",
    "SPACE_SLOTS",
    "ScopeGrain",
    "ScopedCall",
    "ScopedQuestion",
    "ScopedResult",
    "TIER_ELSEWHERE",
    "TIER_ORDER",
    "TIER_RIGID",
    "TIER_SCOPED",
    "TIER_AGENTIC",
    "answer_by_code",
    "answer_by_worker",
    "answer_space_for",
    "assert_senses_identical",
    "build_parser",
    "build_worker_factory",
    "demo_items",
    "dispatch",
    "finish_schema",
    "grade",
    "hive_schema",
    "hive_tools",
    "load_hive_config",
    "main",
    "parse_answers",
    "plan_calls",
    "render_plan",
    "render_prompt",
    "render_results",
    "run_attempt",
    "scripted_cortex",
    "scripted_worker",
    "transform_schema",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The committed tier + sampling table. An INPUT, like ``arch-arms-sampling.json``.
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / "live-test-results" / "arch-hive-sampling.json"

#: Reused rather than restated — one live gate for every harness in this repo.
LIVE_GATE_ENV = aa.LIVE_GATE_ENV

#: Reused rather than restated: a missing cell raises the same error class the
#: four-arm harness raises, so an operator debugging a config sees one vocabulary.
ConfigError = aa.ConfigError

ROLE_CORTEX = aa.ROLE_CORTEX
ROLE_WORKER = aa.ROLE_WORKER
ROLE_SENSES = aa.ROLE_SENSES
ROLES = aa.ROLES


# ── the tiers ────────────────────────────────────────────────────────────────

TIER_RIGID = "B0"
TIER_SCOPED = "B1"
TIER_AGENTIC = "B2"
TIER_ORDER: tuple[str, ...] = (TIER_RIGID, TIER_SCOPED, TIER_AGENTIC)

#: Where a tier this module does NOT build already lives. Stated as data so the
#: absence of a ``B2`` entry in :data:`HIVE_ARMS` reads as a decision rather than
#: an oversight: B2 is an open goal with the worker holding control, which IS
#: ``examples/arch_arms.py``'s manager arm — already built, already dialable.
TIER_ELSEWHERE: dict[str, str] = {
    TIER_AGENTIC: (
        "B2 is an open goal with the worker holding control, which is arm "
        f"{aa.ARM_MANAGER!r} ({aa.ARMS[aa.ARM_MANAGER].label}) in examples/arch_arms.py. "
        "Building it here would be a second implementation of a shipped arm."
    )
}

#: Who answers a scoped question. The ONE field separating B0 from B1.
ANSWERER_CODE = "code"
ANSWERER_WORKER = "worker"
ANSWERERS: tuple[str, ...] = (ANSWERER_CODE, ANSWERER_WORKER)

#: lobes' own words, quoted so the claim this arm makes is checkable against the
#: contract it claims to honour rather than against a paraphrase.
FORBIDDEN_RESPONSIBILITIES: tuple[str, ...] = ("final_decision", "security_decision")

#: The only authority a hive question may carry. There is no second value: an
#: answer is an observation the cortex weighs, never a decision it inherits.
AUTHORITY_ADVISORY = "advisory"


# ── answer spaces: enumerable, small, declared ───────────────────────────────

#: A space whose members are literal strings, fixed at declaration time.
SPACE_LITERAL = "literal"

#: A space of labelled option slots. The item supplies the option TEXT; the
#: answer is a slot label, so the space stays enumerable however the options
#: read. This is issue #44's "target A, B or C?" in its exact shape.
SPACE_SLOTS = "slots"
ANSWER_SPACE_KINDS: tuple[str, ...] = (SPACE_LITERAL, SPACE_SLOTS)

#: The slot alphabet. Finite, declared once, and the ceiling on a slots space.
SLOT_LABELS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")

#: The most answers any declared question may offer. "Small and enumerable" is
#: the arm's claim, so it is a number a test can check, not an adjective.
MAX_ANSWER_SPACE = len(SLOT_LABELS)

#: Measured saturation, not the advertised width — docs/live-test-results/
#: worker-throughput.md: 8 to 14 buys +5.5% aggregate for +75% concurrent load,
#: and lobes has relabelled the x14 figure a KV-pool ceiling.
MAX_HIVE_WIDTH = 8


@dataclass(frozen=True)
class ScopedQuestion:
    """One question the harness may ask a worker. Authored here, never by a model.

    A question is the whole of what B delegates. It carries no goal, no plan and
    no room for prose: :attr:`ask` is a fixed line, the answer space is
    enumerable before the call, and :attr:`authority` is
    :data:`AUTHORITY_ADVISORY` with no other value available.
    """

    id: str
    kind: str
    #: The literal space, for :data:`SPACE_LITERAL`. Empty for a slots question,
    #: whose space is :data:`SLOT_LABELS` truncated to the item's option count.
    answers: tuple[str, ...]
    ask: str
    authority: str
    why: str

    def space(self, options: Sequence[str] = ()) -> tuple[str, ...]:
        """This question's answer space for one item. Always enumerable."""
        if self.kind == SPACE_SLOTS:
            return SLOT_LABELS[: min(len(options), MAX_ANSWER_SPACE)]
        return self.answers

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "answers": list(self.answers),
            "slot_alphabet": list(SLOT_LABELS) if self.kind == SPACE_SLOTS else [],
            "ask": self.ask,
            "authority": self.authority,
            "why": self.why,
        }


HIVE_QUESTIONS: dict[str, ScopedQuestion] = {
    "pick_option": ScopedQuestion(
        id="pick_option",
        kind=SPACE_SLOTS,
        answers=(),
        ask="Which listed option should this item take?",
        authority=AUTHORITY_ADVISORY,
        why=(
            "issue #44's own example — 'target A, B or C?'. The league lane's unit "
            "decision is exactly this shape (a menu index), and M/H spend a whole "
            "delegated drive plus a prose parse to reach it"
        ),
    ),
    "classify_load": ScopedQuestion(
        id="classify_load",
        kind=SPACE_LITERAL,
        answers=(aa.DIFFICULTY_SIMPLE, aa.DIFFICULTY_COMPLEX),
        ask="Is this item's decision simple or complex?",
        authority=AUTHORITY_ADVISORY,
        why=(
            "the hybrid arm's own routing judgement, asked as a typed question "
            "instead of inferred from a free-text reason field. Vocabulary is "
            "arch_arms.DIFFICULTIES, not a second word for the same thing"
        ),
    ),
    "looks_risky": ScopedQuestion(
        id="looks_risky",
        kind=SPACE_LITERAL,
        answers=("clear", "risky", "unclear"),
        ask="Does this item look risky to act on as described?",
        authority=AUTHORITY_ADVISORY,
        why=(
            "issue #44's 'is this safe?', deliberately reworded. 'Is this safe' "
            "names a SECURITY DECISION, which lobes' worker contract forbids "
            "(FORBIDDEN_RESPONSIBILITIES); an observation that something LOOKS "
            "risky is not that decision. 'unclear' is a first-class member so a "
            "worker with no view has an in-space way to say so rather than "
            "guessing — the #32 no-answer shape, closed by the schema"
        ),
    ),
}

#: Presentation order, and the order every emitted ``enum`` lists.
QUESTION_ORDER: tuple[str, ...] = ("pick_option", "classify_load", "looks_risky")


def answer_space_for(question: ScopedQuestion, item: "HiveItem") -> tuple[str, ...]:
    """The enumerated space for one (question, item) pair. Never empty for a
    well-formed item, and never open."""
    return question.space(item.options)


# ── the things being transformed ─────────────────────────────────────────────


@dataclass(frozen=True)
class HiveItem:
    """One thing in ``transform(N things) -> N results``.

    Every field is harness-authored. The cortex may *select* items by id — that
    is its whole influence over what reaches a worker — and may not author one.
    """

    id: str
    question: str
    #: Harness-authored fact lines. What a worker is told, verbatim.
    facts: tuple[str, ...]
    #: Option TEXT for a slots question, positionally aligned to SLOT_LABELS.
    options: tuple[str, ...] = ()
    #: The graded answer. Never rendered into a prompt — :func:`render_prompt`
    #: reads ``facts`` and ``options`` and nothing else.
    truth: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "facts": list(self.facts),
            "options": list(self.options),
        }


@dataclass(frozen=True)
class ScopeGrain:
    """How much of the work rides in one scoped call — **the swept variable**.

    Frame assumption: *scope size is the independent variable, swept rather than
    toggled*. It is a config cell rather than a constant here so task ``t9``'s
    sweep varies a cited value instead of editing a prompt.
    """

    id: str
    items_per_call: int
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "items_per_call": self.items_per_call, "why": self.why}


# ── the arms, as data ────────────────────────────────────────────────────────

SHAPE_HIVE = "hive"


@dataclass(frozen=True)
class HiveArm:
    """One hive tier, as data. Nothing in this module branches on an arm id."""

    id: str
    label: str
    tier: str
    shape: str
    #: The role driving the top-level acting loop. The cortex, in both tiers —
    #: it is the queen, and it is the ONLY role that ever holds a loop here.
    top_level_role: str
    acting_roles: tuple[str, ...]
    configured_roles: tuple[str, ...]
    #: True where a scoped answer leaves this process. B0 delegates nothing.
    delegates: bool
    #: The cortex holds the work in both tiers — a hive worker executes no
    #: ground work of its own, it answers a question about work the cortex holds.
    keeps_work: bool
    #: The one field separating B0 from B1.
    answerer: str
    #: Every role permitted to hold a loop. The worker is in neither tier's, and
    #: :func:`run_attempt` passes no subagent seam, so it cannot be added by use.
    holds_loop: tuple[str, ...]
    why: str

    @property
    def worker_calls_at_play_time(self) -> bool:
        return self.answerer == ANSWERER_WORKER

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "shape": self.shape,
            "top_level_role": self.top_level_role,
            "acting_roles": list(self.acting_roles),
            "configured_roles": list(self.configured_roles),
            "delegates": self.delegates,
            "keeps_work": self.keeps_work,
            "answerer": self.answerer,
            "holds_loop": list(self.holds_loop),
            "worker_calls_at_play_time": self.worker_calls_at_play_time,
            "why": self.why,
        }


HIVE_ARMS: dict[str, HiveArm] = {
    TIER_RIGID: HiveArm(
        id=TIER_RIGID,
        label="hive-rigid",
        tier=TIER_RIGID,
        shape=SHAPE_HIVE,
        top_level_role=ROLE_CORTEX,
        acting_roles=(ROLE_CORTEX,),
        configured_roles=(ROLE_CORTEX, ROLE_SENSES),
        delegates=False,
        keeps_work=True,
        answerer=ANSWERER_CODE,
        holds_loop=(ROLE_CORTEX,),
        why=(
            "0 play-time worker calls: a declared code policy answers every scoped "
            "question. The control B1 is measured against — same cortex surface, "
            "same questions, same grain, and only the answerer differs"
        ),
    ),
    TIER_SCOPED: HiveArm(
        id=TIER_SCOPED,
        label="hive-scoped",
        tier=TIER_SCOPED,
        shape=SHAPE_HIVE,
        top_level_role=ROLE_CORTEX,
        acting_roles=(ROLE_CORTEX, ROLE_WORKER),
        configured_roles=(ROLE_CORTEX, ROLE_WORKER, ROLE_SENSES),
        delegates=True,
        keeps_work=True,
        answerer=ANSWERER_WORKER,
        holds_loop=(ROLE_CORTEX,),
        why=(
            "N small worker calls, concurrent, each a typed question with an "
            "enumerable answer space; the harness keeps control flow throughout "
            "and the worker holds no turn"
        ),
    ),
}

#: Presentation order. ``B2`` is absent by decision — see :data:`TIER_ELSEWHERE`.
HIVE_ARM_ORDER: tuple[str, ...] = (TIER_RIGID, TIER_SCOPED)


# ── the tool surfaces ────────────────────────────────────────────────────────

#: The cortex's delegation verb, named for what the arm claims it is.
TRANSFORM_TOOL = "transform"

#: Final authority. It lives on the cortex's surface and on no other.
FINISH_TOOL = "finish"

HIVE_ORCHESTRATOR_TOOLS: tuple[str, ...] = (TRANSFORM_TOOL, FINISH_TOOL)
HIVE_FINAL_AUTHORITY_TOOLS: tuple[str, ...] = (FINISH_TOOL,)

#: **The worker's complete tool surface: nothing.** Simultaneously the
#: pre-registration and the enforcement, the way ``orchestrator_tools`` uses
#: ``WORKER_TOOLS = ('report',)`` — except here the tuple is empty, which is the
#: arm's whole claim. A hive worker receives no tool schema, so it has no verb
#: to finish with, no verb to delegate with, and no verb to act with.
HIVE_WORKER_TOOLS: tuple[str, ...] = ()


def transform_schema(
    *,
    items: Sequence[HiveItem],
    questions: Sequence[str],
) -> dict[str, Any]:
    """The cortex's ``transform`` schema, emitted with literal ``enum``s only.

    Two properties, both closed:

    * ``question`` enumerates the *declared* question ids this arm carries, so
      the catalog and the wire cannot drift;
    * ``items`` is an array whose members enumerate the *harness-authored* item
      ids, so the cortex selects from what exists and cannot author a thing.

    There is no third property. A ``subtask``, ``goal`` or ``instruction`` field
    is what makes M and H's delegation ungradeable, and its absence here is the
    difference between a tool call and a delegated goal.
    """
    return {
        "type": "function",
        "function": {
            "name": TRANSFORM_TOOL,
            "description": (
                "Ask one typed question about these items. Each item is answered "
                "independently and the answers come straight back to you; nothing "
                "you call here decides anything or acts on anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "enum": [name for name in QUESTION_ORDER if name in questions],
                        "description": "Which declared question to ask about each item.",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [item.id for item in items],
                        },
                        "description": "Which items to ask about.",
                    },
                },
                "required": ["question", "items"],
                "additionalProperties": False,
            },
        },
    }


def finish_schema(*, items: Sequence[HiveItem]) -> dict[str, Any]:
    """The cortex's ``finish`` schema. Enumerable too, and deliberately so.

    The criterion binds worker calls, not this surface — but a cortex verb whose
    answer field were free text would put the graded output through a prose
    parse, which is the failure mode the arm exists to remove. So the answer
    space here is the union of every item's own space: still finite, still
    enumerated, still checkable before the model is dialled.
    """
    spaces: list[str] = []
    for item in items:
        question = HIVE_QUESTIONS.get(item.question)
        if question is None:
            continue
        for value in answer_space_for(question, item):
            if value not in spaces:
                spaces.append(value)
    return {
        "type": "function",
        "function": {
            "name": FINISH_TOOL,
            "description": (
                "Submit your decision for every item. Yours alone — nothing you "
                "consulted can submit, override or extend this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {
                                    "type": "string",
                                    "enum": [item.id for item in items],
                                },
                                "answer": {"type": "string", "enum": spaces},
                            },
                            "required": ["item", "answer"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["answers"],
                "additionalProperties": False,
            },
        },
    }


def hive_schema(
    arm: HiveArm,
    *,
    items: Sequence[HiveItem],
    questions: Sequence[str],
) -> list[dict[str, Any]]:
    """The cortex's whole surface in one tier. Identical in B0 and B1.

    That identity is the control: if the two tiers offered different verbs, a
    difference in outcome could be the surface rather than the answerer.
    """
    return [
        transform_schema(items=items, questions=questions),
        finish_schema(items=items),
    ]


def hive_tools(
    arm: HiveArm, *, items: Sequence[HiveItem], questions: Sequence[str]
) -> tuple[str, ...]:
    """The cortex's verb list, quotable as-is."""
    return tuple(
        entry["function"]["name"] for entry in hive_schema(arm, items=items, questions=questions)
    )


# ── the prompts (harness-authored, every one of them) ────────────────────────

HIVE_SYSTEM = (
    "You are working this problem with a bank of workers available to you. They "
    "are not teammates and they hold no authority: each one answers ONE typed "
    "question about ONE item from a fixed list of allowed answers, and it can do "
    "nothing else. Call `transform` to ask a question about as many items as you "
    "like at once, weigh what comes back, and submit every item's decision "
    "yourself with `finish` — the decision is yours to make, never theirs."
)

#: The worker's system framing. It states the boundary the schema already
#: enforces, so a worker reading only its prompt and a reader reading only the
#: code reach the same conclusion.
WORKER_SYSTEM = (
    "Answer the question below about each listed item. Reply with the answer "
    "lines and nothing else — no preamble, no reasoning, no questions back. You "
    "are not deciding anything: someone else weighs your answer and decides."
)

#: The fixed answer protocol. Harness-authored, identical on every call, and the
#: only thing :func:`parse_answers` looks for.
ANSWER_PROTOCOL = "One line per item, exactly: <item id> = <answer>"


def render_prompt(question: ScopedQuestion, items: Sequence[HiveItem]) -> str:
    """One scoped call's user turn, composed from harness-authored parts only.

    Nothing the cortex wrote reaches this string. The cortex chose a declared
    question id and a list of declared item ids; the words come from
    :data:`HIVE_QUESTIONS` and from each item's own ``facts``.
    """
    lines: list[str] = [question.ask, ""]
    for item in items:
        lines.append(f"item {item.id}:")
        for fact in item.facts:
            lines.append(f"  {fact}")
        space = answer_space_for(question, item)
        for label, option in zip(space, item.options):
            lines.append(f"  {label}. {option}")
        lines.append(f"  allowed answers: {', '.join(space)}")
        lines.append("")
    lines.append(ANSWER_PROTOCOL)
    return "\n".join(lines)


# ── one scoped call ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopedCall:
    """One dispatched call: a question, a chunk of items, and the space each may
    be answered in. Built by :func:`plan_calls` and never by a model."""

    id: str
    question: str
    item_ids: tuple[str, ...]
    prompt: str
    #: One enumerated space per item, positionally aligned to ``item_ids``.
    spaces: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "item_ids": list(self.item_ids),
            "spaces": [list(space) for space in self.spaces],
        }


# ── call acceptance, measured apart from outcome (issue #33) ─────────────────

#: Every item in the call came back with an in-space answer.
ACCEPTED = "accepted"

#: An answer arrived for at least one item and was outside its declared space,
#: or an item was left unanswered. The #33 shape, made countable.
REFUSED_OFF_SPACE = "refused-off-space"

#: Nothing parseable came back at all. The #32 shape: handed a job, the mind
#: produced no usable prose.
REFUSED_EMPTY = "refused-empty"

#: The call missed the batch deadline. An INSTRUMENT event, never a refusal —
#: folding it into refusals would let a slow rig look like a broken schema.
ABSENT_TIMEOUT = "absent-timeout"

#: The transport failed. An instrument event too, and kept separate from the
#: deadline so a dead endpoint and a slow one are told apart.
ABSENT_TRANSPORT = "absent-transport"

#: The turn ran out of token budget mid-answer. **The #37 defect shape, one
#: layer up, and the reason this outcome exists at all**:
#: ``ModelResponse`` carries no ``finish_reason``, so a truncated turn and a
#: deliberate one arrive at a reader as the same object — and a truncated turn
#: that produced no complete answer line looks *exactly* like a schema refusal.
#: Counting it as one would inflate the very refusal rate the frame's falsifiable
#: prediction is measured on, in the flattering-to-nobody direction: the arm
#: would be published as refuted by an instrument setting. So the finish reason
#: is read off the transport where one is available, and a refusal that
#: coincides with truncation is reclassified as an ABSENCE.
ABSENT_TRUNCATED = "absent-truncated"

ACCEPTANCE_OUTCOMES: tuple[str, ...] = (
    ACCEPTED,
    REFUSED_OFF_SPACE,
    REFUSED_EMPTY,
    ABSENT_TIMEOUT,
    ABSENT_TRANSPORT,
    ABSENT_TRUNCATED,
)

#: league's word for a completion that ran out of budget mid-thought, reused so
#: one vocabulary spans every harness here.
FINISH_TRUNCATED = ws.FINISH_TRUNCATED

#: The two outcomes that are the SCHEMA's fault, and the pair the frame's
#: falsifiable prediction is measured on: the #32/#33 interface-failure class
#: should disappear when the cortex authors the schema it delegates across.
REFUSALS: tuple[str, ...] = (REFUSED_OFF_SPACE, REFUSED_EMPTY)

#: The three that are the RIG's or the INSTRUMENT's. Reported, never counted as
#: refusals — the distinction is the whole point of measuring acceptance apart
#: from outcome, and it would be lost if a slow rig or a small budget could be
#: read as a broken schema.
ABSENCES: tuple[str, ...] = (ABSENT_TIMEOUT, ABSENT_TRANSPORT, ABSENT_TRUNCATED)


def finish_reason_of(mind: Any) -> str:
    """The last turn's ``finish_reason``, where the transport records one.

    Duck-typed on purpose. ``examples/worker_seam.py``'s ``WorkerSeam`` keeps a
    ``Meter`` whose ``transcript`` carries the field ``ModelResponse`` cannot
    (issue #37); a scripted mind has neither, and gets ``""`` — which reads as
    "not reported", never as "not truncated".
    """
    meter = getattr(mind, "meter", None)
    transcript = getattr(meter, "transcript", None) or ()
    if not transcript:
        return ""
    return str(transcript[-1].get("finish_reason") or "")


@dataclass(frozen=True)
class ScopedResult:
    """One scoped call's whole outcome. **Nothing here is a correctness claim.**

    ``answers`` are what came back; whether they are *right* is graded later,
    from the cortex's ``finish``, and never from this record.
    """

    call_id: str
    question: str
    item_ids: tuple[str, ...]
    #: One answer per item, positionally aligned. ``""`` where none arrived.
    answers: tuple[str, ...]
    acceptance: str
    detail: str
    answerer: str
    model_calls: int
    seconds: float
    prompt_tokens: int
    completion_tokens: int

    @property
    def accepted(self) -> bool:
        return self.acceptance == ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "question": self.question,
            "item_ids": list(self.item_ids),
            "answers": list(self.answers),
            "acceptance": self.acceptance,
            "detail": self.detail,
            "answerer": self.answerer,
            "model_calls": self.model_calls,
            "seconds": round(self.seconds, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class AcceptanceLedger:
    """Call-acceptance, kept in its own ledger with its own vocabulary.

    Deliberately NOT a field on the outcome record: issue #33 measured 17 of 23
    calls refused on a shape error, and a harness that folded refusals into
    outcome would have reported a broken arm as a losing arm.
    """

    def __init__(self) -> None:
        self.results: list[ScopedResult] = []

    def extend(self, results: Iterable[ScopedResult]) -> None:
        self.results.extend(results)

    @property
    def dispatched(self) -> int:
        return len(self.results)

    def counts(self) -> dict[str, int]:
        tally = {name: 0 for name in ACCEPTANCE_OUTCOMES}
        for result in self.results:
            tally[result.acceptance] = tally.get(result.acceptance, 0) + 1
        return tally

    def rate(self) -> Optional[float]:
        """Accepted / dispatched, or ``None`` when nothing was dispatched.

        ``None`` rather than ``1.0`` or ``0.0``: an arm that made no calls has
        no acceptance rate, and either number would be read as one.
        """
        if not self.results:
            return None
        accepted = sum(1 for result in self.results if result.accepted)
        return accepted / len(self.results)

    def refusal_rate(self) -> Optional[float]:
        """The figure the frame's prediction is falsifiable on (#33's 74%)."""
        if not self.results:
            return None
        refused = sum(1 for result in self.results if result.acceptance in REFUSALS)
        return refused / len(self.results)

    def to_dict(self) -> dict[str, Any]:
        rate = self.rate()
        refusals = self.refusal_rate()
        return {
            "dispatched": self.dispatched,
            "counts": self.counts(),
            "acceptance_rate": None if rate is None else round(rate, 4),
            "refusal_rate": None if refusals is None else round(refusals, 4),
            "model_calls": sum(result.model_calls for result in self.results),
            "prompt_tokens": sum(result.prompt_tokens for result in self.results),
            "completion_tokens": sum(result.completion_tokens for result in self.results),
            "seconds": round(sum(result.seconds for result in self.results), 3),
            "calls": [result.to_dict() for result in self.results],
        }


# ── planning: the dispatch count is bounded before any thread exists ─────────


def plan_calls(
    items: Sequence[HiveItem],
    *,
    question: str,
    grain: ScopeGrain,
    budget: int,
    stem: str,
) -> tuple[tuple[ScopedCall, ...], tuple[str, ...]]:
    """Chunk *items* at the declared grain, clamped by *budget*.

    Returns ``(calls, refused_item_ids)``. The refusal is the bound working and
    is reported as such — an item beyond the budget is named back to the cortex
    rather than silently dropped, so a drive that asked for more than it could
    afford can see that it did.

    The clamp happens here, before :func:`dispatch` creates a thread: the number
    of scoped calls a drive can make is knowable from the plan alone.
    """
    per_call = max(int(grain.items_per_call), 1)
    affordable = max(int(budget), 0)
    chunks: list[tuple[HiveItem, ...]] = []
    for start in range(0, len(items), per_call):
        chunks.append(tuple(items[start : start + per_call]))
    kept = chunks[:affordable]
    dropped = chunks[affordable:]

    declared = HIVE_QUESTIONS[question]
    calls: list[ScopedCall] = []
    for index, chunk in enumerate(kept, start=1):
        calls.append(
            ScopedCall(
                id=f"{stem}-{index}",
                question=question,
                item_ids=tuple(item.id for item in chunk),
                prompt=render_prompt(declared, chunk),
                spaces=tuple(answer_space_for(declared, item) for item in chunk),
            )
        )
    refused = tuple(item.id for chunk in dropped for item in chunk)
    return tuple(calls), refused


# ── parsing: an out-of-space answer is a refusal, never a value ─────────────


def parse_answers(raw: str, call: ScopedCall) -> tuple[tuple[str, ...], str, str]:
    """Read one worker reply against the call's declared spaces.

    Returns ``(answers, acceptance, detail)``. **Never raises** — a reply this
    function cannot read is data about the arm, and a raise from inside a
    dispatch thread is exactly what this repo's C3 forbids.

    The rule is total and stated once: an answer counts only if the line exists
    AND its value is a member of that item's own enumerated space. Anything else
    is a refusal with a named reason, never a nearest-match or a default.
    """
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    seen: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        seen.setdefault(name.strip(), value.strip())

    answers: list[str] = []
    off_space: list[str] = []
    missing: list[str] = []
    for item_id, space in zip(call.item_ids, call.spaces):
        value = seen.get(item_id, "")
        if not value:
            missing.append(item_id)
            answers.append("")
        elif value not in space:
            off_space.append(f"{item_id}={value!r}")
            answers.append("")
        else:
            answers.append(value)

    if not any(answers) and not off_space:
        return (
            tuple(answers),
            REFUSED_EMPTY,
            (
                "no `<item id> = <answer>` line for any item "
                f"({len(lines)} non-empty line(s) came back)"
            ),
        )
    if off_space or missing:
        parts: list[str] = []
        if off_space:
            parts.append("outside the declared space: " + ", ".join(off_space))
        if missing:
            parts.append("unanswered: " + ", ".join(missing))
        return tuple(answers), REFUSED_OFF_SPACE, "; ".join(parts)
    return tuple(answers), ACCEPTED, ""


# ── the two answerers. Exactly one model call, or exactly none. ─────────────

#: The declared code rules. B0 names one in config; there is NO default here,
#: for the same reason the sampling table has none — a rule that lives in code
#: is a rule nobody reviewed.
CODE_POLICIES: dict[str, Callable[[Sequence[str]], str]] = {
    "first": lambda space: space[0] if space else "",
    "last": lambda space: space[-1] if space else "",
}


def answer_by_code(call: ScopedCall, *, policy: Callable[[Sequence[str]], str]) -> ScopedResult:
    """B0's answerer. **Zero model calls**, by construction rather than by count.

    Nothing in this function can reach a network: it reads the call's own
    declared spaces and applies *policy*. ``model_calls`` is ``0`` because there
    is no seam here to make one with.
    """
    answers = tuple(policy(space) for space in call.spaces)
    empty = [item for item, value in zip(call.item_ids, answers) if not value]
    acceptance = REFUSED_OFF_SPACE if empty else ACCEPTED
    detail = ("the code policy produced no answer for: " + ", ".join(empty)) if empty else ""
    return ScopedResult(
        call_id=call.id,
        question=call.question,
        item_ids=call.item_ids,
        answers=answers,
        acceptance=acceptance,
        detail=detail,
        answerer=ANSWERER_CODE,
        model_calls=0,
        seconds=0.0,
        prompt_tokens=0,
        completion_tokens=0,
    )


def answer_by_worker(
    call: ScopedCall,
    *,
    mind: Callable[[list[dict[str, Any]]], ModelResponse],
) -> ScopedResult:
    """B1's answerer: **one** completion, and nothing that could become a turn.

    There is no loop here, no tool schema on the wire, no result fed back for a
    second look, and no way to signal completion — the call is a function
    application whose value is a string. That is the whole of the arm's claim
    about the worker, and it is visible in this function's shape rather than
    argued for in a docstring elsewhere.

    Like :func:`parse_answers` it **never raises**: a transport fault becomes an
    :data:`ABSENT_TRANSPORT` record on the thread that saw it, because a thread
    that raises leaves its ``Future`` holding an exception the collecting side
    must re-raise or swallow.
    """
    messages = [
        {"role": "system", "content": WORKER_SYSTEM},
        {"role": "user", "content": call.prompt},
    ]
    try:
        reply = mind(messages)
    except Exception as broken:  # noqa: BLE001 — a failed call is DATA, never a raise
        return ScopedResult(
            call_id=call.id,
            question=call.question,
            item_ids=call.item_ids,
            answers=tuple("" for _ in call.item_ids),
            acceptance=ABSENT_TRANSPORT,
            detail=f"{type(broken).__name__}: {broken}",
            answerer=ANSWERER_WORKER,
            model_calls=1,
            seconds=0.0,
            prompt_tokens=0,
            completion_tokens=0,
        )
    answers, acceptance, detail = parse_answers(reply.content or "", call)
    # A refusal that coincides with a truncated turn is an INSTRUMENT event, not
    # a schema failure. See :data:`ABSENT_TRUNCATED` for why folding the two
    # together would corrupt the arm's headline metric. A truncated turn that
    # still produced every in-space answer stays ACCEPTED: the answer arrived.
    if acceptance in REFUSALS and finish_reason_of(mind) == FINISH_TRUNCATED:
        acceptance = ABSENT_TRUNCATED
        detail = f"the turn was truncated ({FINISH_TRUNCATED}) before answering: {detail}"
    return ScopedResult(
        call_id=call.id,
        question=call.question,
        item_ids=call.item_ids,
        answers=answers,
        acceptance=acceptance,
        detail=detail,
        answerer=ANSWERER_WORKER,
        model_calls=1,
        seconds=0.0,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
    )


def build_worker_factory(
    *,
    dial: aa.Dial,
    sampling: aa.Sampling,
) -> Callable[[ScopedCall], Callable[[list[dict[str, Any]]], ModelResponse]]:
    """A fresh ``worker_seam`` transport per scoped call — decision ``c42``.

    One seam per call rather than one shared seam: ``WorkerSeam`` carries a
    ``Meter`` that is not written for concurrent use, and this is the same shape
    ``examples/worker_throughput.py`` uses to measure the width this arm sizes
    itself from. ``tools`` is never passed — there is nothing to pass.
    """

    def factory(call: ScopedCall) -> Callable[[list[dict[str, Any]]], ModelResponse]:
        return ws.WorkerSeam(
            base_url=dial.base_url,
            model=dial.model,
            api_key=dial.api_key,
            role=f"hive-{call.question}",
            max_tokens=sampling.max_tokens,
            temperature=sampling.temperature,
        )

    return factory


# ── dispatch: concurrent, bounded, and it never parks the queen ─────────────

THREAD_NAME_PREFIX = "hive-scoped"


def dispatch(
    calls: Sequence[ScopedCall],
    *,
    answer_fn: Callable[[ScopedCall], ScopedResult],
    max_workers: int,
    timeout: float,
) -> tuple[ScopedResult, ...]:
    """Run every call concurrently and return results in **plan order**.

    Termination, by construction and provable from this function's source:
    exactly one :func:`concurrent.futures.wait` with a finite deadline, every
    future read with ``timeout=0`` so a read cannot block even if ``wait`` were
    wrong about done-ness, and ``shutdown(wait=False, cancel_futures=True)`` in
    a ``finally`` so teardown never joins a call that is still running. A call
    that misses the deadline is recorded :data:`ABSENT_TIMEOUT` and its result
    is never read — ``orchestrator_tools``' fan-out discipline, inherited for
    the same reason.

    Plan order rather than completion order: a report whose shape depends on the
    scheduler is not a report.
    """
    if not calls:
        return ()
    width = max(1, min(int(max_workers), MAX_HIVE_WIDTH, len(calls)))
    pool = ThreadPoolExecutor(max_workers=width, thread_name_prefix=THREAD_NAME_PREFIX)
    pending: list[tuple[Future[ScopedResult], ScopedCall]] = []
    try:
        for call in calls:
            pending.append((pool.submit(answer_fn, call), call))
        _done, absent = wait([future for future, _ in pending], timeout=timeout)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    results: list[ScopedResult] = []
    for future, call in pending:
        if future in absent:
            results.append(_timed_out(call, timeout))
        else:
            results.append(future.result(timeout=0))
    return tuple(results)


def _timed_out(call: ScopedCall, timeout: float) -> ScopedResult:
    return ScopedResult(
        call_id=call.id,
        question=call.question,
        item_ids=call.item_ids,
        answers=tuple("" for _ in call.item_ids),
        acceptance=ABSENT_TIMEOUT,
        detail=f"the batch deadline of {timeout}s passed before this call landed",
        answerer=ANSWERER_WORKER,
        model_calls=1,
        seconds=timeout,
        prompt_tokens=0,
        completion_tokens=0,
    )


def render_results(results: Sequence[ScopedResult], refused: Sequence[str]) -> str:
    """What the cortex reads back. Refusals are named, never hidden as silence."""
    lines: list[str] = []
    for result in results:
        for item_id, answer in zip(result.item_ids, result.answers):
            lines.append(f"{item_id} = {answer}" if answer else f"{item_id} = (no answer)")
        if not result.accepted:
            lines.append(f"[{result.acceptance}] {result.call_id}: {result.detail}")
    for item_id in refused:
        lines.append(f"[not-dispatched] {item_id}: the scoped-call budget for this drive is spent")
    return "\n".join(lines) if lines else "(nothing was asked)"


# ── the cortex's executor ────────────────────────────────────────────────────


class HiveOrchestrator:
    """The cortex's surface in one tier. The only surface in this module.

    ``transform`` is answered by *answer_fn*, which is the tier's answerer and
    the one thing B0 and B1 differ in. ``finish`` is final authority and lives
    here alone: an answer that came back through ``transform`` becomes the
    attempt's output only by the cortex naming it in ``finish``, which is how
    :data:`FORBIDDEN_RESPONSIBILITIES` is honoured structurally rather than
    promised in prose.
    """

    def __init__(
        self,
        *,
        arm: HiveArm,
        items: Sequence[HiveItem],
        questions: Sequence[str],
        answer_fn: Callable[[ScopedCall], ScopedResult],
        grain: ScopeGrain,
        max_concurrency: int,
        call_budget: int,
        batch_timeout: float,
        task_id: str = "hive-1",
    ) -> None:
        self.arm = arm
        self.items = tuple(items)
        self.by_id = {item.id: item for item in self.items}
        self.questions = tuple(name for name in QUESTION_ORDER if name in questions)
        self.answer_fn = answer_fn
        self.grain = grain
        self.max_concurrency = max_concurrency
        self.call_budget = max(int(call_budget), 0)
        self.batch_timeout = batch_timeout
        self.task_id = task_id
        self.surface = hive_tools(arm, items=self.items, questions=self.questions)
        self.ledger = AcceptanceLedger()
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.batches = 0
        self.answers: dict[str, str] = {}
        self.finished = False

    @property
    def calls_remaining(self) -> int:
        """What is left of the drive's scoped-call budget. Never negative."""
        return max(self.call_budget - self.ledger.dispatched, 0)

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in self.surface:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available in arm {self.arm.id}; its tools are "
                f"{list(self.surface)}"
            )
        if name == TRANSFORM_TOOL:
            return self._transform(arguments)
        return self._finish(arguments)

    def _transform(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        question = str(arguments.get("question") or "").strip()
        # The catalog is the enum AND the dispatch condition; they cannot drift.
        if question not in self.questions:
            raise ToolError(
                f"{question!r} is not a question this arm asks; the declared "
                f"questions are {list(self.questions)}"
            )
        raw = arguments.get("items")
        ids = tuple(str(entry) for entry in raw) if isinstance(raw, (list, tuple)) else ()
        if not ids:
            raise ToolError("transform requires `items`: name the items to ask about")
        unknown = tuple(entry for entry in ids if entry not in self.by_id)
        if unknown:
            raise ToolError(
                f"no such item(s): {list(unknown)}. You may only ask about the "
                f"items you were given: {[item.id for item in self.items]}"
            )

        self.batches += 1
        chosen = tuple(self.by_id[entry] for entry in ids)
        calls, refused = plan_calls(
            chosen,
            question=question,
            grain=self.grain,
            budget=self.calls_remaining,
            stem=f"{self.task_id}-b{self.batches}",
        )
        self.refused.extend(refused)
        results = dispatch(
            calls,
            answer_fn=self.answer_fn,
            max_workers=self.max_concurrency,
            timeout=self.batch_timeout,
        )
        self.ledger.extend(results)
        return ToolOutcome(result=render_results(results, refused))

    def _finish(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        raw = arguments.get("answers")
        rows = list(raw) if isinstance(raw, (list, tuple)) else []
        if not rows:
            raise ToolError("finish requires `answers`: one entry per item")
        chosen: dict[str, str] = {}
        for row in rows:
            entry = dict(row) if isinstance(row, Mapping) else {}
            item_id = str(entry.get("item") or "").strip()
            value = str(entry.get("answer") or "").strip()
            item = self.by_id.get(item_id)
            if item is None:
                raise ToolError(f"no such item: {item_id!r}")
            space = answer_space_for(HIVE_QUESTIONS[item.question], item)
            if value not in space:
                raise ToolError(
                    f"{value!r} is not an allowed answer for {item_id}; allowed: {list(space)}"
                )
            chosen[item_id] = value
        self.answers = chosen
        self.finished = True
        return ToolOutcome(
            result="submitted",
            finished=True,
            finish_summary=json.dumps(chosen, sort_keys=True),
        )

    def state(self) -> str:
        return f"{len(self.calls)} tool call(s), {self.ledger.dispatched} scoped call(s)"


# ── grading ──────────────────────────────────────────────────────────────────


def grade(answers: Mapping[str, str], items: Sequence[HiveItem]) -> dict[str, Any]:
    """Outcome, and outcome only. No acceptance figure appears in this mapping."""
    correct = sum(1 for item in items if answers.get(item.id, "") == item.truth)
    wrong = sum(
        1 for item in items if item.id in answers and answers.get(item.id, "") != item.truth
    )
    return {
        "attempted": len(items),
        "answered": len([item for item in items if item.id in answers]),
        "correct": correct,
        "wrong": wrong,
        "is_correct": bool(items) and correct == len(items),
        "verdict": "CORRECT" if items and correct == len(items) else "WRONG",
    }


# ── the committed configuration ──────────────────────────────────────────────


def _required(raw: Mapping[str, Any], key: str, where: str) -> Any:
    """Read a required key. There is no default — that is the whole point."""
    if not isinstance(raw, Mapping) or key not in raw:
        raise ConfigError(
            f"{where}: missing required key {key!r}. The hive table is the only "
            "source for it; this harness carries no default to fall back on."
        )
    return raw[key]


@dataclass(frozen=True)
class HiveSetup:
    """One tier's hive block: which questions, at what grain, how wide."""

    arm: str
    grain: str
    questions: tuple[str, ...]
    max_concurrency: int
    code_policy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "grain": self.grain,
            "questions": list(self.questions),
            "max_concurrency": self.max_concurrency,
            "code_policy": self.code_policy,
        }


@dataclass(frozen=True)
class HiveBudget:
    """One tier's drive budgets. ``worker_max_steps`` is 0 in both, by claim."""

    max_steps: int
    worker_max_steps: int
    spawn_allowance: int
    max_scoped_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "worker_max_steps": self.worker_max_steps,
            "spawn_allowance": self.spawn_allowance,
            "max_scoped_calls": self.max_scoped_calls,
        }


@dataclass(frozen=True)
class HiveConfig:
    """The whole committed configuration, parsed and validated once.

    Structurally compatible with ``arch_arms``' ``ArchConfig`` where the seam
    builders touch it (:meth:`sampling_for`, :meth:`wire_extra`), so
    ``arch_arms.ScriptedSeams`` and ``arch_arms.LiveSeams`` build hive minds
    unchanged — one metering vocabulary across both harnesses, and no copy of
    either builder here.
    """

    path: Optional[Path]
    version: int
    roles: Mapping[str, aa.RoleDial]
    thinking_modes: Mapping[str, Mapping[str, Any]]
    sampling: Mapping[str, Mapping[str, aa.Sampling]]
    budgets: Mapping[str, HiveBudget]
    setups: Mapping[str, HiveSetup]
    grains: Mapping[str, ScopeGrain]
    batch_timeout_seconds: float
    decision: Mapping[str, Any]

    def role(self, name: str) -> aa.RoleDial:
        if name not in self.roles:
            raise ConfigError(f"roles: no entry for {name!r}; known roles are {sorted(self.roles)}")
        return self.roles[name]

    def sampling_for(self, arm: str, role: str) -> aa.Sampling:
        if arm not in self.sampling:
            raise ConfigError(f"sampling: no cell for arm {arm!r}")
        cells = self.sampling[arm]
        if role not in cells:
            raise ConfigError(
                f"sampling.{arm}: no cell for role {role!r}. Every role an arm "
                "configures needs its own temperature, thinking mode and max_tokens."
            )
        return cells[role]

    def budget_for(self, arm: str) -> HiveBudget:
        if arm not in self.budgets:
            raise ConfigError(f"budgets: no entry for arm {arm!r}")
        return self.budgets[arm]

    def setup_for(self, arm: str) -> HiveSetup:
        if arm not in self.setups:
            raise ConfigError(f"hive: no entry for arm {arm!r}")
        return self.setups[arm]

    def grain(self, name: str) -> ScopeGrain:
        if name not in self.grains:
            raise ConfigError(
                f"grains: no entry for {name!r}; known grains are {sorted(self.grains)}"
            )
        return self.grains[name]

    def code_policy(self, name: str) -> Callable[[Sequence[str]], str]:
        if name not in CODE_POLICIES:
            raise ConfigError(
                f"hive: code_policy {name!r} is not declared; known policies are "
                f"{sorted(CODE_POLICIES)}"
            )
        return CODE_POLICIES[name]

    def wire_extra(self, thinking: str) -> dict[str, Any]:
        if thinking not in self.thinking_modes:
            raise ConfigError(
                f"thinking_wire.modes: no entry for mode {thinking!r}; "
                f"known modes are {sorted(self.thinking_modes)}"
            )
        return dict(self.thinking_modes[thinking])

    def senses_hashes(self) -> dict[str, str]:
        dial = self.role(ROLE_SENSES).to_dict()
        out: dict[str, str] = {}
        for arm in HIVE_ARM_ORDER:
            payload = {"role": dial, "sampling": self.sampling_for(arm, ROLE_SENSES).to_dict()}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            out[arm] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "version": self.version,
            "roles": {name: dial.to_dict() for name, dial in self.roles.items()},
            "thinking_wire": {mode: dict(keys) for mode, keys in self.thinking_modes.items()},
            "sampling": {
                arm: {role: cell.to_dict() for role, cell in cells.items()}
                for arm, cells in self.sampling.items()
            },
            "budgets": {arm: budget.to_dict() for arm, budget in self.budgets.items()},
            "hive": {arm: setup.to_dict() for arm, setup in self.setups.items()},
            "grains": {name: grain.to_dict() for name, grain in self.grains.items()},
            "dispatch": {"batch_timeout_seconds": self.batch_timeout_seconds},
            "decision": dict(self.decision),
        }


def load_hive_config(path: Optional[Path] = None) -> HiveConfig:
    """Read and validate the committed hive table. Eager, total, no defaults."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ConfigError(f"no hive table at {resolved}") from missing
    except ValueError as broken:
        raise ConfigError(f"{resolved} is not readable JSON: {broken}") from broken
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{resolved}: the hive table must be a JSON object")

    roles = {
        name: aa.RoleDial.from_dict(name, entry)
        for name, entry in (_required(raw, "roles", str(resolved))).items()
    }
    for name in ROLES:
        if name not in roles:
            raise ConfigError(f"{resolved}: roles is missing {name!r}")

    wire = _required(raw, "thinking_wire", str(resolved))
    modes = {mode: dict(keys) for mode, keys in (_required(wire, "modes", "thinking_wire")).items()}

    grains_raw = _required(raw, "grains", str(resolved))
    grains: dict[str, ScopeGrain] = {}
    for name, entry in grains_raw.items():
        if not isinstance(entry, Mapping) or "items_per_call" not in entry:
            continue
        grains[name] = ScopeGrain(
            id=name,
            items_per_call=int(entry["items_per_call"]),
            why=str(entry.get("why") or ""),
        )
    if not grains:
        raise ConfigError(f"{resolved}: grains declares no grain with an items_per_call")

    sampling_raw = _required(raw, "sampling", str(resolved))
    budgets_raw = _required(raw, "budgets", str(resolved))
    hive_raw = _required(raw, "hive", str(resolved))

    sampling: dict[str, dict[str, aa.Sampling]] = {}
    budgets: dict[str, HiveBudget] = {}
    setups: dict[str, HiveSetup] = {}
    for arm in HIVE_ARM_ORDER:
        if arm not in sampling_raw:
            raise ConfigError(f"sampling: no cell for arm {arm!r}")
        cells: dict[str, aa.Sampling] = {}
        for role in HIVE_ARMS[arm].configured_roles:
            if role not in sampling_raw[arm]:
                raise ConfigError(f"sampling.{arm}: no cell for role {role!r}")
            cell = aa.Sampling.from_dict(sampling_raw[arm][role], where=f"sampling.{arm}.{role}")
            if cell.thinking not in modes:
                raise ConfigError(
                    f"sampling.{arm}.{role}: thinking mode {cell.thinking!r} has no "
                    f"thinking_wire.modes entry; known modes are {sorted(modes)}"
                )
            cells[role] = cell
        sampling[arm] = cells

        budget_cell = _required(budgets_raw, arm, "budgets")
        budgets[arm] = HiveBudget(
            max_steps=int(_required(budget_cell, "max_steps", f"budgets.{arm}")),
            worker_max_steps=int(_required(budget_cell, "worker_max_steps", f"budgets.{arm}")),
            spawn_allowance=int(_required(budget_cell, "spawn_allowance", f"budgets.{arm}")),
            max_scoped_calls=int(_required(budget_cell, "max_scoped_calls", f"budgets.{arm}")),
        )

        hive_cell = _required(hive_raw, arm, "hive")
        grain_name = str(_required(hive_cell, "grain", f"hive.{arm}"))
        if grain_name not in grains:
            raise ConfigError(
                f"hive.{arm}: grain {grain_name!r} has no grains entry; "
                f"known grains are {sorted(grains)}"
            )
        declared = tuple(str(name) for name in _required(hive_cell, "questions", f"hive.{arm}"))
        for name in declared:
            if name not in HIVE_QUESTIONS:
                raise ConfigError(
                    f"hive.{arm}: question {name!r} is not declared in HIVE_QUESTIONS; "
                    f"declared questions are {sorted(HIVE_QUESTIONS)}"
                )
        setups[arm] = HiveSetup(
            arm=arm,
            grain=grain_name,
            questions=declared,
            max_concurrency=int(_required(hive_cell, "max_concurrency", f"hive.{arm}")),
            code_policy=str(_required(hive_cell, "code_policy", f"hive.{arm}")),
        )

    dispatch_raw = _required(raw, "dispatch", str(resolved))
    timeout = float(_required(dispatch_raw, "batch_timeout_seconds", "dispatch"))
    if not 0 < timeout < float("inf"):
        raise ConfigError(
            f"dispatch.batch_timeout_seconds must be finite and positive, got {timeout!r}"
        )

    return HiveConfig(
        path=resolved,
        version=int(raw.get("version") or 0),
        roles=roles,
        thinking_modes=modes,
        sampling=sampling,
        budgets=budgets,
        setups=setups,
        grains=grains,
        batch_timeout_seconds=timeout,
        decision=dict(_required(raw, "decision", str(resolved))),
    )


def assert_senses_identical(config: HiveConfig) -> str:
    """Refuse a run whose tiers disagree about senses. Returns the one hash."""
    hashes = config.senses_hashes()
    distinct = sorted(set(hashes.values()))
    if len(distinct) != 1:
        grouped: dict[str, list[str]] = {}
        for arm, digest in hashes.items():
            grouped.setdefault(digest, []).append(arm)
        detail = "; ".join(
            f"{digest[:12]}… = arms {sorted(arms)}" for digest, arms in sorted(grouped.items())
        )
        raise ConfigError(
            "senses configuration differs between hive tiers and would confound "
            f"both at once: {detail}. Check sampling.<arm>.senses and roles.senses."
        )
    return distinct[0]


# ── the attempt record: outcome and acceptance, in disjoint namespaces ───────

KIND_ATTEMPT = "hive-attempt"

#: The outcome half of :meth:`HiveAttemptRecord.to_dict`. Named here so the
#: disjointness from :data:`ACCEPTANCE_KEYS` is a checkable fact and not a habit.
OUTCOME_KEYS: tuple[str, ...] = (
    "graded",
    "is_correct",
    "answers",
    "raw_summary",
    "exit_reason",
    "model_turns",
    "aborted",
)

#: The acceptance half. Issue #33's lesson, kept structural.
ACCEPTANCE_KEYS: tuple[str, ...] = ("acceptance", "scoped_calls", "worker_model_calls")


@dataclass
class HiveAttemptRecord:
    """One tier's attempt at one item set, and everything it cost.

    Outcome and acceptance are separate blocks with disjoint keys. A reader can
    quote a refusal rate without touching a correctness number, and vice versa —
    which is precisely what issue #33's post-mortem says was missing.
    """

    arm: str
    tier: str
    rung: str
    route: str
    top_level_role: str
    answerer: str
    grain: str
    exit_reason: str = ""
    model_turns: int = 0
    child_model_turns: int = 0
    raw_summary: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    graded: dict[str, Any] = field(default_factory=dict)
    is_correct: bool = False
    aborted: bool = False
    degradation_codes: list[str] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    acceptance: dict[str, Any] = field(default_factory=dict)
    scoped_calls: int = 0
    worker_model_calls: int = 0
    cortex_cost: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_ATTEMPT,
            "arm": self.arm,
            "tier": self.tier,
            "rung": self.rung,
            "route": self.route,
            "top_level_role": self.top_level_role,
            "answerer": self.answerer,
            "grain": self.grain,
            "exit_reason": self.exit_reason,
            "model_turns": self.model_turns,
            "child_model_turns": self.child_model_turns,
            "raw_summary": self.raw_summary,
            "answers": dict(self.answers),
            "graded": dict(self.graded),
            "is_correct": self.is_correct,
            "aborted": self.aborted,
            "degradation_codes": list(self.degradation_codes),
            "refused_tools": list(self.refused_tools),
            "acceptance": dict(self.acceptance),
            "scoped_calls": self.scoped_calls,
            "worker_model_calls": self.worker_model_calls,
            "cortex_cost": dict(self.cortex_cost),
        }


# ── running one attempt ──────────────────────────────────────────────────────


def run_attempt(
    *,
    arm: HiveArm,
    config: HiveConfig,
    seams: Any,
    items: Sequence[HiveItem],
    senses_hash: str,
    worker_factory: Optional[
        Callable[[ScopedCall], Callable[[list[dict[str, Any]]], ModelResponse]]
    ] = None,
    rung: str = "H1",
    route: str = aa.ROUTE_TEXT,
    task_id: str = "hive-1",
) -> HiveAttemptRecord:
    """Drive one hive tier at one item set, and grade what the cortex submitted.

    **Exactly one** :func:`embodiment.run` call, with the cortex mind, and with
    no ``subagent`` seam and no ``spawn_allowance``: there is no argument here
    through which a worker could be handed a drive, which is what makes "the
    worker never holds a turn" a property of the call site rather than a promise.
    """
    setup = config.setup_for(arm.id)
    budget = config.budget_for(arm.id)
    grain = config.grain(setup.grain)
    log: aa.CallLog = seams.log
    mark = len(log.records)
    ctx = aa.CallContext(
        arm=arm.id,
        rung=rung,
        problem=task_id,
        route=route,
        senses_hash=senses_hash,
        live=bool(getattr(seams, "live", False)),
    )
    answer_fn = _answerer_for(
        arm,
        config=config,
        setup=setup,
        seams=seams,
        ctx=ctx,
        worker_factory=worker_factory,
    )
    executor = HiveOrchestrator(
        arm=arm,
        items=items,
        questions=setup.questions,
        answer_fn=answer_fn,
        grain=grain,
        max_concurrency=setup.max_concurrency,
        call_budget=budget.max_scoped_calls,
        batch_timeout=config.batch_timeout_seconds,
        task_id=task_id,
    )
    cortex = seams.build(ROLE_CORTEX, ctx, hive_schema(arm, items=items, questions=setup.questions))
    task = Task(
        id=f"{arm.id}-{rung}-{task_id}",
        repo_path="",
        instruction=_instruction(items),
    )
    outcome, aborted = _drive(
        cortex,
        task,
        executor=executor,
        max_steps=budget.max_steps,
        system_prompt=HIVE_SYSTEM,
        model=seams.model_for(ROLE_CORTEX),
    )
    # NOTE what is absent above and in :func:`_drive`: ``subagent`` and
    # ``spawn_allowance``. Both are ``embodiment.run`` parameters and both
    # default to "no descendant may exist"; neither is spelled anywhere in this
    # module, and ``_drive`` takes named arguments rather than ``**kwargs``, so
    # there is no hole through which a later edit could pass one by accident.

    record = HiveAttemptRecord(
        arm=arm.id,
        tier=arm.tier,
        rung=rung,
        route=route,
        top_level_role=arm.top_level_role,
        answerer=arm.answerer,
        grain=grain.id,
    )
    record.aborted = aborted
    record.exit_reason = outcome.exit_reason
    record.model_turns = outcome.result.stats.model_turns
    record.child_model_turns = outcome.child_model_turns
    record.refused_tools = list(executor.refused)
    # Bound to a name first: the structural termination test asks that every
    # iteration walk a SETTLED sequence, and a name is settled where a fresh
    # call is only settled if you already know what it returns.
    degradations = ledger.read(loop=outcome)
    record.degradation_codes = [entry.code for entry in degradations]
    record.raw_summary = (outcome.result.summary or "").strip()
    record.answers = dict(executor.answers)
    record.graded = grade(executor.answers, items)
    record.is_correct = bool(record.graded.get("is_correct"))
    record.acceptance = executor.ledger.to_dict()
    record.scoped_calls = executor.ledger.dispatched
    record.worker_model_calls = sum(result.model_calls for result in executor.ledger.results)
    record.cortex_cost = aa.fold_cost(log.since(mark))
    return record


def _answerer_for(
    arm: HiveArm,
    *,
    config: HiveConfig,
    setup: HiveSetup,
    seams: Any,
    ctx: aa.CallContext,
    worker_factory: Optional[
        Callable[[ScopedCall], Callable[[list[dict[str, Any]]], ModelResponse]]
    ],
) -> Callable[[ScopedCall], ScopedResult]:
    """Read the tier's answerer off the arm. Nothing here branches on an arm id."""
    if arm.answerer == ANSWERER_CODE:
        policy = config.code_policy(setup.code_policy)
        return lambda call: answer_by_code(call, policy=policy)
    factory = worker_factory
    if factory is None:
        factory = _seam_worker_factory(seams, ctx)
    resolved = factory
    return lambda call: answer_by_worker(call, mind=resolved(call))


def _seam_worker_factory(
    seams: Any, ctx: aa.CallContext
) -> Callable[[ScopedCall], Callable[[list[dict[str, Any]]], ModelResponse]]:
    """Build one worker mind per scoped call, with **no tool schema**.

    ``tools=None`` is not a default anyone can forget: there is no other value
    this call site can pass, and ``tests/test_arch_hive.py`` reads that from the
    AST rather than from this sentence.
    """
    return lambda call: seams.build(ROLE_WORKER, ctx, None)


def _instruction(items: Sequence[HiveItem]) -> str:
    lines = [
        "Decide the right answer for every item below.",
        "",
        "items: " + ", ".join(item.id for item in items),
        "",
    ]
    for item in items:
        question = HIVE_QUESTIONS[item.question]
        lines.append(f"{item.id}: {question.ask}")
        for fact in item.facts:
            lines.append(f"  {fact}")
        for label, option in zip(answer_space_for(question, item), item.options):
            lines.append(f"  {label}. {option}")
    return "\n".join(lines)


def _drive(
    mind: Any,
    task: Task,
    *,
    executor: Any,
    max_steps: int,
    system_prompt: str,
    model: str,
) -> tuple[Any, bool]:
    """Run the loop, and treat an abort as data rather than an exception.

    The arguments are **named, not** ``**kwargs``. A passthrough would be a hole
    an edit could route ``subagent=`` or ``spawn_allowance=`` through without
    ever touching :func:`run`'s call site, and criterion 3 is a property of that
    call site — so the call site enumerates what it may pass.
    """
    try:
        return (
            run(
                mind,
                task,
                executor=executor,
                max_steps=max_steps,
                system_prompt=system_prompt,
                model=model,
            ),
            False,
        )
    except LoopAborted as aborted:
        return aborted.outcome, True


# ── a hermetic item source, and hermetic minds ──────────────────────────────

#: The demo item set's option texts. Deterministic and harness-authored; no
#: randomness anywhere in this module, which is both a bandit-clean choice and
#: an honest one — a seeded generator is one more hidden variable.
_DEMO_OPTIONS: tuple[str, ...] = (
    "hold position and wait",
    "advance to the near marker",
    "fall back to the depot",
    "take the contested post",
)

_DEMO_TRUTHS: dict[str, tuple[str, ...]] = {
    "pick_option": ("B", "D", "A", "C"),
    "classify_load": (aa.DIFFICULTY_SIMPLE, aa.DIFFICULTY_COMPLEX),
    "looks_risky": ("clear", "risky", "unclear"),
}


def demo_items(
    count: int = 6, *, questions: Sequence[str] = QUESTION_ORDER
) -> tuple[HiveItem, ...]:
    """A deterministic, harness-authored item set for the hermetic lane.

    Shaped like the league lane's unit decisions this tier is aimed at — a unit,
    a handful of facts, a short menu — without importing the league, which task
    ``t9``/``t10`` wire. Nothing here is a graded environment: a scripted run
    that scored well would be mistaken for data, so the demo exists to exercise
    the *paths*, and the sweep grades against league.
    """
    allowed = tuple(name for name in QUESTION_ORDER if name in questions) or QUESTION_ORDER
    items: list[HiveItem] = []
    for index in range(max(int(count), 0)):
        question = allowed[index % len(allowed)]
        declared = HIVE_QUESTIONS[question]
        options = _DEMO_OPTIONS if declared.kind == SPACE_SLOTS else ()
        truths = _DEMO_TRUTHS[question]
        items.append(
            HiveItem(
                id=f"u{index + 1}",
                question=question,
                facts=(
                    f"unit {index + 1} is idle at t={index * 3}",
                    f"it is holding {index % 3} resource(s)",
                ),
                options=options,
                truth=truths[index % len(truths)],
            )
        )
    return tuple(items)


def scripted_worker(
    answer_index: int = 0,
    *,
    off_space: bool = False,
    silent: bool = False,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A hermetic worker: reads the allowed answers off its own prompt and picks
    one. Never opens a socket, and models the two refusal shapes on request."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        if silent:
            return ModelResponse(content="", prompt_tokens=40, completion_tokens=0)
        prompt = str(messages[-1].get("content") or "")
        lines: list[str] = []
        for block in prompt.split("item ")[1:]:
            item_id = block.split(":", 1)[0].strip()
            allowed = _allowed_from(block)
            if off_space or not allowed:
                lines.append(f"{item_id} = MAYBE")
            else:
                lines.append(f"{item_id} = {allowed[min(answer_index, len(allowed) - 1)]}")
        return ModelResponse(
            content="\n".join(lines), prompt_tokens=40, completion_tokens=len(lines) * 4
        )

    return complete


def _allowed_from(block: str) -> tuple[str, ...]:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("allowed answers:"):
            raw = stripped.split(":", 1)[1]
            return tuple(part.strip() for part in raw.split(",") if part.strip())
    return ()


def scripted_cortex(
    items: Sequence[HiveItem],
    *,
    ask: bool = True,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A hermetic cortex: asks once per declared question, then finishes.

    It reads its own transcript for what came back and submits the worker's
    answer where it has one, falling back to the first allowed answer where it
    does not — which is the honest thing for a stand-in to do and keeps the
    scripted lane from silently depending on a refusal path.
    """
    by_question: dict[str, list[HiveItem]] = {}
    for item in items:
        by_question.setdefault(item.question, []).append(item)
    order = [name for name in QUESTION_ORDER if name in by_question]

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        transcript = " ".join(aa.message_text(message) for message in messages)
        if ask:
            for question in order:
                if f"[asked:{question}]" not in transcript:
                    return ModelResponse(
                        content=f"[asked:{question}] asking about {question}.",
                        tool_calls=[
                            ToolCall(
                                id=f"call-{question}",
                                name=TRANSFORM_TOOL,
                                arguments={
                                    "question": question,
                                    "items": [item.id for item in by_question[question]],
                                },
                            )
                        ],
                        prompt_tokens=80,
                        completion_tokens=12,
                    )
        answers = []
        for item in items:
            heard = _heard(transcript, item.id)
            space = answer_space_for(HIVE_QUESTIONS[item.question], item)
            value = heard if heard in space else (space[0] if space else "")
            answers.append({"item": item.id, "answer": value})
        return ModelResponse(
            content="Submitting.",
            tool_calls=[
                ToolCall(id="call-finish", name=FINISH_TOOL, arguments={"answers": answers})
            ],
            prompt_tokens=90,
            completion_tokens=20,
        )

    return complete


def _heard(transcript: str, item_id: str) -> str:
    marker = f"{item_id} = "
    if marker not in transcript:
        return ""
    tail = transcript.split(marker)[-1]
    return tail.split("\n")[0].split(" ")[0].strip()


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan(config: Optional[HiveConfig] = None) -> str:
    lines = ["arch_hive — arm B, the Bee-Hive: the worker as a tool", "", "tiers:"]
    for arm_id in HIVE_ARM_ORDER:
        arm = HIVE_ARMS[arm_id]
        lines.append(
            f"  {arm.id}  {arm.label:<12} answerer={arm.answerer:<7} "
            f"delegates={str(arm.delegates).lower()}"
        )
        lines.append(f"      {arm.why}")
    for tier, where in TIER_ELSEWHERE.items():
        lines += ["", f"  {tier}  NOT BUILT HERE", f"      {where}"]
    lines += ["", "questions (every answer space enumerable, every authority advisory):"]
    for name in QUESTION_ORDER:
        question = HIVE_QUESTIONS[name]
        space = question.answers or (SLOT_LABELS[:1] + ("…",))
        lines.append(f"  {question.id:<15} {question.kind:<8} {list(space)}")
        lines.append(f"      {question.ask}")
    lines += [
        "",
        f"worker tool surface: {list(HIVE_WORKER_TOOLS)}  (empty IS the enforcement)",
        f"final authority:     {list(HIVE_FINAL_AUTHORITY_TOOLS)}  (cortex only)",
        f"forbidden for a worker: {list(FORBIDDEN_RESPONSIBILITIES)}",
        f"max concurrent width:   {MAX_HIVE_WIDTH}  (measured saturation)",
    ]
    if config is not None:
        lines += ["", f"hive table: {config.path}", "", "grains (the swept variable):"]
        for name, grain in sorted(config.grains.items()):
            lines.append(f"  {name:<8} items_per_call={grain.items_per_call}  {grain.why}")
        lines += ["", "setups:"]
        for arm_id in HIVE_ARM_ORDER:
            setup = config.setup_for(arm_id)
            budget = config.budget_for(arm_id)
            lines.append(
                f"  {arm_id}  grain={setup.grain} width={setup.max_concurrency} "
                f"questions={list(setup.questions)} max_scoped_calls={budget.max_scoped_calls}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--config", default=None, help="path to the hive table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the tiers, the questions and the bounds")
    plan.add_argument("--json", action="store_true")

    config_cmd = subparsers.add_parser("config", help="the hive table as it was read")
    config_cmd.add_argument("--json", action="store_true")
    config_cmd.add_argument("--config", default=None, help="path to the hive table")

    schemas = subparsers.add_parser("schemas", help="the emitted schemas, for inspection")
    schemas.add_argument("--arm", default=TIER_SCOPED, help="which tier's surface")
    schemas.add_argument("--json", action="store_true")
    schemas.add_argument("--config", default=None, help="path to the hive table")

    runner = subparsers.add_parser("run", help="run the hermetic lane (no live dial here)")
    runner.add_argument("--arm", action="append", default=None, help="restrict to these tiers")
    runner.add_argument("--items", type=int, default=6)
    runner.add_argument("--config", default=None, help="path to the hive table")
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if getattr(args, "config", None) else None

    if args.command == "plan":
        if args.json:
            print(
                json.dumps(
                    {
                        "arms": {arm: HIVE_ARMS[arm].to_dict() for arm in HIVE_ARM_ORDER},
                        "tier_order": list(TIER_ORDER),
                        "tier_elsewhere": dict(TIER_ELSEWHERE),
                        "questions": {
                            name: HIVE_QUESTIONS[name].to_dict() for name in QUESTION_ORDER
                        },
                        "worker_tools": list(HIVE_WORKER_TOOLS),
                        "final_authority_tools": list(HIVE_FINAL_AUTHORITY_TOOLS),
                        "forbidden_responsibilities": list(FORBIDDEN_RESPONSIBILITIES),
                        "max_hive_width": MAX_HIVE_WIDTH,
                    },
                    indent=2,
                )
            )
        else:
            print(render_plan())
        return 0

    try:
        config = load_hive_config(config_path)
    except ConfigError as broken:
        return _fail(str(broken), f"check the hive table at {config_path or DEFAULT_CONFIG_PATH}")

    if args.command == "config":
        payload = config.to_dict()
        try:
            payload["senses_config_hash"] = assert_senses_identical(config)
        except ConfigError as drifted:
            return _fail(str(drifted), "make every tier's senses cell identical, then re-run")
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(render_plan(config))
            print()
            print(f"senses_config_hash: {payload['senses_config_hash']}")
        return 0

    if args.command == "schemas":
        if args.arm not in HIVE_ARMS:
            return _fail(
                f"unknown tier {args.arm!r}",
                f"known tiers: {', '.join(HIVE_ARM_ORDER)} "
                f"({TIER_ELSEWHERE.get(args.arm, 'and no other')})",
            )
        setup = config.setup_for(args.arm)
        items = demo_items(4, questions=setup.questions)
        payload = {
            "arm": args.arm,
            "cortex": hive_schema(HIVE_ARMS[args.arm], items=items, questions=setup.questions),
            "worker_tools": list(HIVE_WORKER_TOOLS),
        }
        print(json.dumps(payload, indent=2))
        return 0

    arms = tuple(args.arm) if args.arm else HIVE_ARM_ORDER
    unknown = [name for name in arms if name not in HIVE_ARMS]
    if unknown:
        return _fail(
            f"unknown tier(s) {unknown}",
            f"known tiers: {', '.join(HIVE_ARM_ORDER)}; B2 is arm M in examples/arch_arms.py",
        )
    try:
        senses_hash = assert_senses_identical(config)
    except ConfigError as drifted:
        return _fail(str(drifted), "make every tier's senses cell identical, then re-run")

    records: list[dict[str, Any]] = []
    for arm_id in arms:
        setup = config.setup_for(arm_id)
        items = demo_items(args.items, questions=setup.questions)
        log = aa.CallLog()
        seams = aa.ScriptedSeams({ROLE_CORTEX: scripted_cortex(items)}, config=config, log=log)
        record = run_attempt(
            arm=HIVE_ARMS[arm_id],
            config=config,
            seams=seams,
            items=items,
            senses_hash=senses_hash,
            worker_factory=lambda call: scripted_worker(),
        )
        records.append(record.to_dict())
    print(json.dumps({"kind": "hive-run", "live": False, "attempts": records}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
