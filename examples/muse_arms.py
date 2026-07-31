"""The three-arm muse validation harness — tools-off / +pad / +pad+workspace.

Contract: ``docs/live-test-results/muse-arms-preregistration.md``, committed in
the same change as this file and before the first measured dial. This module is
plan task **t17**'s harness; **task t18 runs it**. It executes a pre-registered
protocol and does not re-open one.

The experiment, in one paragraph
--------------------------------
`Issue #21 <https://github.com/agentculture/embodiment/issues/21>`_ proposes that
the muse may need working memory and somewhere to execute, and asks for that to
be *validated* rather than assumed. Three arms, pre-registered:

======  =========================================================
arm     what the muse is wired with
======  =========================================================
``A``   nothing — today's tools-off seam, the baseline
``B``   :class:`~embodiment.muse_pad.MusePad`
``C``   :class:`~embodiment.muse_pad.MusePad` **and**
        :class:`~embodiment.workspace.MuseWorkspace`
======  =========================================================

One runner, and where the arms genuinely differ
-----------------------------------------------
:func:`run_once` is the *whole* runner. It is called identically for every arm;
the arm is **data** (:data:`ARM_LANES`), never a branch in the run path. What
that buys, stated precisely rather than claimed loosely:

* **The boundary is byte-identical** across the three arms. One
  :func:`boundary` builds it, it takes no arm argument, and
  ``tests/test_muse_arms.py`` captures the user message off all three live
  loops and asserts the three strings are equal.
* **The controls are identical** — one :func:`controls`, no arm argument.
  ``max_tool_rounds`` is set for every arm and is simply inert on arm ``A``,
  because :mod:`embodiment.muse` reads it only when a bench is wired.
* **The transport is one object.** :class:`RecordingSeam` builds the same body,
  against the same endpoint, at the same temperature and token budget, for
  every arm. The tools-on call adds ``tools`` to that body and changes nothing
  else.
* **The closing turn is one constant, put to every arm alike.** After the
  thinking session ends, :func:`ask_for_the_answer` appends
  :data:`CLOSING_PROMPT` to the muse's own message history and makes one
  **tools-off** call. It exists because the wiring smoke run measured that it
  has to: handed a pad, the reference muse spent eight turns writing six pad
  entries and **zero characters of prose**, so arms ``B`` and ``C`` would have
  reported ``NO_ANSWER`` on every run and DV1 would have measured the turn
  budget instead of the arms.

**The one difference that is not just "which tools", said out loud.** Wiring a
tool necessarily tells the model the tool exists, so arms ``B`` and ``C`` carry
more system text than arm ``A``: :mod:`embodiment.muse` appends
:data:`~embodiment.muse.MUSE_TOOL_AUTHORITY` whenever a bench reaches the wire,
and this harness appends each wired lane's own protocol text
(:data:`~embodiment.muse_pad.MUSE_PAD_PROTOCOL`,
:data:`~embodiment.workspace.WORKSPACE_PROTOCOL`). That is a confound in the
strict sense and it is unavoidable: an arm cannot be handed a tool it is not
told about. Three things bound it, and they are the reason it is stated here
rather than buried:

1. **No prose in this module is arm-conditional.** :data:`TASK_FRAMING` is one
   constant, first in every arm's framing, byte for byte. Every additional
   block is a constant *imported verbatim from the module that owns the tool* —
   this harness writes no per-arm text of its own, and a test asserts the
   composed framing equals exactly ``TASK_FRAMING`` plus those imported blocks.
2. **The difference is monotone and nested**: ``A``'s framing is a strict
   prefix-by-block of ``B``'s, and ``B``'s of ``C``'s.
3. **It is recorded.** Every arm's exact system message is written into the
   transcript, so a reader can weigh the added text instead of taking this
   paragraph's word for it.

The oracle is imported, never reimplemented
-------------------------------------------
:func:`examples.challenge_subset.truth` and
:func:`examples.challenge_subset.grade` are imported **unchanged** and are the
only judges of a final answer. They are brute-force verified (76 of 144
non-consecutive subsets of ``{1..10}`` have an even sum; ``72`` is the planted
"half of 144" trap) and pinned by ``tests/test_challenge_harnesses.py`` and
again by ``tests/test_muse_arms.py``. Byte-stability matters: any change to
either function invalidates every arm already run and restarts the series, so
:func:`oracle_pin` writes the pinned values into the transcript's own preamble
and :func:`main` refuses to dial if they have moved.

The problem statement is *derived* from the oracle's rather than retyped —
:data:`PROBLEM_STATEMENT` drops :data:`~examples.challenge_subset.PROBLEM`'s
final "Then call ``finish``" sentence, because the muse has no ``finish`` tool
in any arm (:data:`embodiment.muse_pad.OMITTED_TOOLS`) and instructing it to
call one would provoke off-protocol calls in arms ``B``/``C`` that arm ``A``
could not make. A test pins that the derivation is a prefix of the original.

What is measured (the pre-registered dependent variables)
---------------------------------------------------------
1. **The rate of confidently-wrong final answers** — the headline. A run states
   a final answer on an ``ANSWER:`` line; :func:`read_answer` classifies it into
   :data:`VERDICTS` and ``confidently_wrong`` is exactly
   ``verdict in (VERDICT_WRONG, VERDICT_TRAP)`` — a definite integer that is not
   76. Abstention is a first-class option the framing offers explicitly, so a
   wrong definite answer is a choice rather than the only available protocol.
2. **Pad protocol adherence** — straight off
   :meth:`embodiment.muse_pad.MusePad.counts`: one count per kind with zeros
   included, ``open_intents``, ``rejected_calls``, ``off_protocol_calls``. The
   measured failure this exists to detect is five intents and zero observations.
3. **Arm ``C``: what the workspace was used for** — every execution classified
   ``arithmetic-offloaded`` vs ``reasoning-displaced`` by :func:`classify_execution`
   (rule ``R-C1``, fixed here before any measured run), plus a counsel score
   (:func:`counsel_score`) computed with the answer removed from the text, so
   "more sums right, less counsel" is a reportable outcome rather than an
   invisible one.

A degraded call is data
-----------------------
Nothing here retries. A transport failure is recorded on the turn *and*
re-raised, so :meth:`embodiment.muse.MuseLoop.think` records its own
``muse-thinking-failed`` degradation and the session stops cleanly — a run that
degraded is reported as degraded, never quietly re-dialled for a better number.
Every workspace and pad degradation is carried into the transcript too.

Raw transcripts, per run
------------------------
A prior series could regrade only 3 of 18 responses because it stored verdicts.
This one stores **every prompt and every response**: :class:`RecordingSeam`
captures the full message list, the schema on the wire, the raw content, the raw
reasoning, the tool calls, the usage and the finish reason for every model turn,
and :class:`ArmTools` captures every tool call's arguments and the exact result
text the muse read back. :func:`analyse` renders every table from that file, so
a number in a results document is a number the transcript contains.

Usage::

    uv run python examples/muse_arms.py --dry-run
    COLLEAGUE_API_KEY=... uv run python examples/muse_arms.py --n 6 \\
        --out docs/live-test-results/muse-arms.jsonl
    uv run python examples/muse_arms.py --analyse \\
        --out docs/live-test-results/muse-arms.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment.contract import ModelResponse, ToolCall  # noqa: E402
from embodiment.muse import (  # noqa: E402
    COUNSEL_KIND_DURABLE,
    MARKER_DONE,
    MUSE_AUTHORITY,
    MUSE_TOOL_AUTHORITY,
    MuseControls,
    MuseLoop,
    MuseToolBench,
)
from embodiment.muse_pad import (  # noqa: E402
    MUSE_PAD_PROTOCOL,
    MUSE_PAD_TOOL_NAMES,
    MusePad,
)
from embodiment.presence_engine import BoundaryContext  # noqa: E402
from embodiment.workspace import (  # noqa: E402
    CLOSED_TEXT,
    PROVIDER_DOCKER,
    PROVIDER_FAKE,
    WORKSPACE_PROTOCOL,
    WORKSPACE_TOOL_NAME,
    WORKSPACE_TOOL_NAMES,
    MuseWorkspace,
)
from examples.challenge_config import write_config_preamble  # noqa: E402
from examples.challenge_subset import PROBLEM, grade, truth  # noqa: E402

# The counsel scorer reuses this repo's already-verified language machinery
# rather than forking it: ``MOVES``/``MOVE_MARKERS`` are the three challenge
# moves, ``word_tokens``/``_content_tokens``/``_bigrams`` the tokenisers, and
# ``_unnegated_hits`` the negation filter that stops "there is no risk" scoring
# as counsel. The three underscored names are imported deliberately — a
# reimplementation would be a second, untested copy of a rule that has already
# been adversarially reviewed once.
from examples.muse_challenge import (  # noqa: E402
    MAX_SHARED_BIGRAMS,
    MOVE_MARKERS,
    MOVES,
    _bigrams,
    _content_tokens,
    _unnegated_hits,
    word_tokens,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── rig settings ──────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
#: The muse under test. Roles resolve by name from the gateway's own contract;
#: this is the model that name currently resolves to on the reference rig, and
#: it is recorded so a later run can tell whether it changed.
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: ONE temperature for every arm. An A/B whose arms run at different
#: temperatures measures the temperature.
DEFAULT_TEMPERATURE = 0.3
#: The muse spends real tokens reasoning before it says anything usable; a small
#: budget returns an empty body with ``finish_reason=length``, which is
#: indistinguishable from model failure. Recorded, and identical per arm.
DEFAULT_MAX_TOKENS = 3000
REQUEST_TIMEOUT_S = 600.0


# ── the pre-registered configuration ──────────────────────────────────────────

ARM_A = "A"
ARM_B = "B"
ARM_C = "C"
ARMS: tuple[str, ...] = (ARM_A, ARM_B, ARM_C)

LANE_PAD = "pad"
LANE_WORKSPACE = "workspace"

#: **The whole of the arm difference.** Which lanes are wired, per arm, as data.
#: Nothing else in this module branches on the arm name.
ARM_LANES: dict[str, tuple[str, ...]] = {
    ARM_A: (),
    ARM_B: (LANE_PAD,),
    ARM_C: (LANE_PAD, LANE_WORKSPACE),
}

#: Each lane's protocol text, imported verbatim from the module that owns the
#: tool. This harness authors no per-arm prose; every byte an arm adds over arm
#: ``A`` comes from here or from :data:`~embodiment.muse.MUSE_TOOL_AUTHORITY`,
#: which :mod:`embodiment.muse` appends itself.
LANE_PROTOCOLS: dict[str, str] = {
    LANE_PAD: MUSE_PAD_PROTOCOL,
    LANE_WORKSPACE: WORKSPACE_PROTOCOL,
}

#: Which lane offers which tool name, so a recorded call is attributable without
#: guessing. Built from each module's own exported name tuple.
LANE_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    LANE_PAD: MUSE_PAD_TOOL_NAMES,
    LANE_WORKSPACE: WORKSPACE_TOOL_NAMES,
}

#: The problem, derived from the oracle's own statement rather than retyped. The
#: dropped final sentence tells the solver to call ``finish``; no arm has that
#: tool, and arms ``B``/``C`` would burn off-protocol calls on it that arm ``A``
#: could not make. ``tests/test_muse_arms.py`` pins the derivation.
PROBLEM_STATEMENT = "\n\n".join(PROBLEM.split("\n\n")[:2]).strip()

#: The answer protocol. One line, one integer, and an explicit refusal option —
#: without the refusal, "confidently wrong" would measure a protocol with no
#: alternative rather than a choice the mind made.
ANSWER_PROTOCOL_LINE = "ANSWER:"

#: The closing question, put to **every** arm identically after its thinking
#: session ends, on a **tools-off** call so no arm can spend it on another tool.
#:
#: It exists because the wiring smoke run measured why it has to. Handed a pad,
#: the reference muse emitted tool calls and **no prose at all** — eight turns,
#: six pad writes, zero characters of narration — so the session ended on the
#: quiet exit with no answer to grade. Arm A answered unprompted; arms B and C
#: would have reported ``NO_ANSWER`` on every run, and DV1 would have measured
#: the turn budget rather than the arms.
#:
#: It is part of the *protocol*, not part of an arm: one constant, one
#: tools-off call, appended to the muse's own real message history (see
#: :func:`closing_messages` — nothing is reconstructed). Arm A gets it too, and
#: usually has already answered before it arrives.
CLOSING_PROMPT = (
    "Stop here. Whatever you have worked out so far is what you have, and there is "
    "no more time.\n"
    f"Write your final answer now, as a single line of exactly the form "
    f"'{ANSWER_PROTOCOL_LINE} <integer>'.\n"
    f"If you are not willing to commit to an integer, write '{ANSWER_PROTOCOL_LINE} "
    "none'. An honest refusal is a valid response and is recorded as one; a "
    "confident wrong number is worse than no number."
)

#: Host framing. **Byte-identical for every arm** and first in all three, before
#: any tool protocol. It names the task and the answer protocol and says nothing
#: about tools — what tools exist is each lane's own text to state.
TASK_FRAMING = (
    "The acting loop is about to commit to an answer for the problem below, and it "
    "wants your counsel before it does. Two things are wanted from you, and they "
    "are scored separately.\n"
    "First, counsel: say what the acting loop should weigh before it commits — a "
    "premise the obvious approach depends on, a way the obvious approach fails, a "
    "different framing worth trying. Put anything meant for the acting loop on a "
    "'GUIDANCE:' line as usual.\n"
    "Second, an answer: work the problem yourself and end your last turn with a "
    "line of exactly the form 'ANSWER: <integer>' carrying the single integer you "
    "believe is correct.\n"
    "If you are not willing to commit to an integer, write 'ANSWER: none'. An "
    "honest refusal is a valid response and is recorded as one; a confident wrong "
    "number is worse than no number.\n\n"
    f"The problem:\n{PROBLEM_STATEMENT}"
)

#: Why the pump woke the muse. Rendered as the boundary's ``why`` line.
BOUNDARY_KIND = "cadence-tick"
BOUNDARY_REASON = "the acting loop is about to commit to an answer and wants counsel first"
BOUNDARY_STEP = 1

#: The muse's budget. Identical for every arm. ``max_quiet_turns`` is 2 rather
#: than the default 1 **deliberately and for every arm alike**: a tools-on turn
#: can legitimately spend its whole round allowance without emitting prose, and a
#: quiet budget of 1 would end arms ``B``/``C`` on their first pure-tool-call
#: turn — an artefact of the budget rather than a finding about the arm. Raising
#: it for all three keeps the arms identical and costs arm ``A`` at most one
#: extra empty turn.
MAX_TURNS = 8
MAX_QUIET_TURNS = 2
MAX_TOOL_ROUNDS = 4
BOUNDARY_CHARS = 4000
INSIGHT_CHARS = 4000
MAX_TOOL_RESULT_CHARS = 2000


def controls() -> MuseControls:
    """The one control set. No arm argument — that is the point."""
    return MuseControls(
        max_turns=MAX_TURNS,
        max_quiet_turns=MAX_QUIET_TURNS,
        max_context_chars=BOUNDARY_CHARS,
        max_insight_chars=INSIGHT_CHARS,
        max_tool_rounds=MAX_TOOL_ROUNDS,
        max_tool_result_chars=MAX_TOOL_RESULT_CHARS,
    )


def boundary() -> BoundaryContext:
    """The whole of what the muse is given. No arm argument — that is the point."""
    return BoundaryContext(
        kind=BOUNDARY_KIND,
        reason=BOUNDARY_REASON,
        step_count=BOUNDARY_STEP,
        task_state=PROBLEM_STATEMENT,
    )


def host_framing(arm: str) -> str:
    """The system framing for *arm*: :data:`TASK_FRAMING`, then each lane's text.

    Composed, never authored per arm. ``A`` is a strict prefix-by-block of ``B``
    and ``B`` of ``C``, which is what makes the added text auditable.
    """
    blocks = [TASK_FRAMING]
    blocks.extend(LANE_PROTOCOLS[lane] for lane in ARM_LANES[arm])
    return "\n\n".join(blocks)


def system_message(arm: str) -> str:
    """The system message *arm* actually puts on the wire.

    Reconstructed the way :func:`embodiment.muse._system_message` composes it —
    authority first, the tool correction next when a bench is wired, host framing
    last. A test asserts this equals the string captured off a live loop, so the
    reconstruction cannot drift away from what is sent.
    """
    blocks = [MUSE_AUTHORITY]
    if ARM_LANES[arm]:
        blocks.append(MUSE_TOOL_AUTHORITY)
    blocks.append(host_framing(arm))
    return "\n\n".join(blocks)


# ── the oracle: imported, pinned, never reimplemented ─────────────────────────

#: The verified answer and the planted trap, as literals, so a drift in the
#: imported oracle is a failing check rather than a silently different series.
PINNED_TRUTH = 76
PINNED_TRAP = 72


def oracle_pin() -> dict[str, Any]:
    """The oracle's own output, recorded into the transcript before any dial.

    ``matches_pin`` is the gate :func:`main` refuses to dial without: the whole
    series is comparable only while ``truth()`` and ``grade()`` are byte-stable,
    so a change to either must stop a run rather than quietly start a new series
    inside an old file.
    """
    computed = truth()
    trap = grade(PINNED_TRAP)
    correct = grade(computed)
    return {
        "source": "examples/challenge_subset.py",
        "truth": computed,
        "pinned_truth": PINNED_TRUTH,
        "trap": PINNED_TRAP,
        "trap_is_rejected": bool(not trap["is_correct"] and trap["is_trap"]),
        "truth_is_accepted": bool(correct["is_correct"]),
        "matches_pin": bool(
            computed == PINNED_TRUTH
            and not trap["is_correct"]
            and trap["is_trap"]
            and correct["is_correct"]
        ),
    }


# ── the answer protocol ───────────────────────────────────────────────────────

VERDICT_CORRECT = "CORRECT"
VERDICT_TRAP = "WRONG_TRAP"
VERDICT_WRONG = "WRONG"
VERDICT_ABSTAINED = "ABSTAINED"
VERDICT_MALFORMED = "MALFORMED"
VERDICT_NO_ANSWER = "NO_ANSWER"
VERDICTS: tuple[str, ...] = (
    VERDICT_CORRECT,
    VERDICT_TRAP,
    VERDICT_WRONG,
    VERDICT_ABSTAINED,
    VERDICT_MALFORMED,
    VERDICT_NO_ANSWER,
)

#: The primary dependent variable's membership test, as data rather than as a
#: condition written twice. A confidently-wrong run stated a definite integer
#: and the integer was not the truth.
CONFIDENTLY_WRONG_VERDICTS: tuple[str, ...] = (VERDICT_TRAP, VERDICT_WRONG)

#: What counts as an explicit refusal on the ``ANSWER:`` line. A fixed list,
#: committed before any run, matched case-insensitively against the whole
#: payload after punctuation is stripped.
ABSTENTIONS: tuple[str, ...] = (
    "none",
    "no answer",
    "n/a",
    "na",
    "unknown",
    "unsure",
    "i do not know",
    "i don't know",
)

#: Hedging vocabulary, recorded as a footnote on the final turn and **never**
#: part of the verdict. A hedge does not make a stated integer un-stated; the
#: count is here so a reader can weigh how confident "confidently wrong" was.
HEDGE_MARKERS: tuple[str, ...] = (
    "probably",
    "roughly",
    "approximately",
    "i think",
    "not certain",
    "uncertain",
    "unsure",
    "might be",
    "may be",
    "tentative",
    "best guess",
)

_ANSWER_RE = re.compile(r"^[^\S\n]*ANSWER[^\S\n]*:[^\S\n]*(.*?)[^\S\n]*$", re.IGNORECASE | re.M)
_INT_RE = re.compile(r"-?\d+")
_BARE_INT_RE = re.compile(r"^-?\d+$")
#: A standalone ``76`` in a workspace result — recorded, never a classifier.
_TRUTH_TOKEN_RE = re.compile(rf"(?<!\d){PINNED_TRUTH}(?!\d)")


def strip_answer_lines(text: str) -> str:
    """*text* with every ``ANSWER:`` line removed.

    The counsel score runs on this, which is how "scored independently of answer
    correctness" is a mechanism rather than a promise: the scorer cannot see the
    answer, so it cannot be right or wrong about it.
    """
    return "\n".join(
        line for line in (text or "").splitlines() if not _ANSWER_RE.match(line.strip())
    )


def _payload_of(raw: str) -> str:
    """One ``ANSWER:`` payload, normalised for matching. Never raises."""
    return (raw or "").strip().strip(".,;:!*_`\"'").strip().lower()


def read_answer(text: str) -> dict[str, Any]:
    """Read the final answer off *text*. Pure, and the ONLY verdict path.

    The **last** ``ANSWER:`` line wins: a muse that revises itself should be
    graded on where it ended up, and the last line is the one it ended on.

    ``lenient_answer`` — the last integer anywhere in the text — is recorded and
    is **never** the verdict, following ``challenge_subset``'s own precedent: a
    lenient parse rewards a mind that ignores the answer protocol, so it is a
    footnote a reader can weigh rather than the number.
    """
    matches = list(_ANSWER_RE.finditer(text or ""))
    lenient = _INT_RE.findall(text or "")
    record: dict[str, Any] = {
        "answer_lines": [m.group(1).strip() for m in matches],
        "raw_payload": matches[-1].group(1).strip() if matches else "",
        "answer": None,
        "verdict": VERDICT_NO_ANSWER,
        "confidently_wrong": False,
        "graded": None,
        "lenient_answer": int(lenient[-1]) if lenient else None,
    }
    if not matches:
        return record

    payload = _payload_of(record["raw_payload"])
    if payload in ABSTENTIONS:
        record["verdict"] = VERDICT_ABSTAINED
        return record
    if not _BARE_INT_RE.match(payload):
        record["verdict"] = VERDICT_MALFORMED
        return record

    answer = int(payload)
    graded = grade(answer)
    record["answer"] = answer
    record["graded"] = graded
    if graded["is_correct"]:
        record["verdict"] = VERDICT_CORRECT
    elif graded["is_trap"]:
        record["verdict"] = VERDICT_TRAP
    else:
        record["verdict"] = VERDICT_WRONG
    record["confidently_wrong"] = record["verdict"] in CONFIDENTLY_WRONG_VERDICTS
    return record


def hedges_in(text: str) -> list[str]:
    """Hedge markers present in *text*. A recorded footnote, never a verdict."""
    lowered = (text or "").lower()
    return [marker for marker in HEDGE_MARKERS if marker in lowered]


# ── rule R-C1: what an arm C execution was for ────────────────────────────────
#
# Fixed HERE, in the same commit as the harness and before any measured run.
# Task t18 applies it; it does not get to invent it after seeing the data.

CLASS_ARITHMETIC = "arithmetic-offloaded"
CLASS_DISPLACED = "reasoning-displaced"
CLASS_DEGRADED = "degraded"
EXECUTION_CLASSES: tuple[str, ...] = (CLASS_ARITHMETIC, CLASS_DISPLACED, CLASS_DEGRADED)

#: A domain bound reaching the problem's own ``{1..10}``.
SIGNAL_DOMAIN = "full-domain"
#: An enumeration over subsets rather than a single arithmetic evaluation.
SIGNAL_ENUMERATE = "enumerates-subsets"
#: The evenness constraint.
SIGNAL_PARITY = "parity-constraint"
#: The no-two-consecutive constraint.
SIGNAL_ADJACENCY = "adjacency-constraint"

_DOMAIN_TOKENS = (
    "range(1, 11)",
    "range(1,11)",
    "range(11)",
    "range(10)",
    "range(0, 10)",
    "range(0,10)",
    "1..10",
    "{1..10}",
    "n = 10",
    "n=10",
    "1 << 10",
    "1<<10",
    "1024",
)
_ENUMERATE_TOKENS = (
    "combinations",
    "itertools",
    "powerset",
    "subsets",
    "subset",
    "product",
    "mask",
    "1 <<",
    "1<<",
    "2 **",
    "2**",
    "bin(",
    "for r in range",
)
_PARITY_TOKENS = ("% 2", "%2", "even", "parity")
_ADJACENCY_TOKENS = (
    "consecutive",
    "adjacent",
    "zip(",
    "b - a",
    "b-a",
    "+ 1 ",
    "+1 ",
    "- 1 ",
    "-1 ",
    "> 1",
    ">1",
    "diff",
)


def _signals(command_text: str) -> list[str]:
    """Which of ``R-C1``'s four surface signals *command_text* carries."""
    lowered = (command_text or "").lower()
    found: list[str] = []
    if any(token in lowered for token in _DOMAIN_TOKENS):
        found.append(SIGNAL_DOMAIN)
    if any(token in lowered for token in _ENUMERATE_TOKENS):
        found.append(SIGNAL_ENUMERATE)
    if any(token in lowered for token in _PARITY_TOKENS):
        found.append(SIGNAL_PARITY)
    if any(token in lowered for token in _ADJACENCY_TOKENS):
        found.append(SIGNAL_ADJACENCY)
    return found


def classify_execution(call: dict[str, Any]) -> dict[str, Any]:
    """Classify ONE ``workspace_run`` execution. Rule ``R-C1``, fixed in advance.

    The rule reads the **command**, not the output, because what is being
    measured is what the muse *reached for*. An output-based rule would grade the
    workspace instead of the mind using it.

    In order:

    ``degraded``
        The call never ran — a refused argv, no engine, an engine failure, or a
        result that could not be read. Counted, never classified, because a call
        that did not happen is not evidence about what execution was used for.
    ``reasoning-displaced``
        The command is a **whole-problem solver**: it carries all four of
        :data:`SIGNAL_DOMAIN`, :data:`SIGNAL_ENUMERATE`, :data:`SIGNAL_PARITY`
        and :data:`SIGNAL_ADJACENCY` — a program that, run, yields the final
        answer. The machine was asked for the conclusion.
    ``arithmetic-offloaded``
        Anything else that ran: a partial sum, a parity check, one candidate
        subset, a small-``n`` instance, a recurrence value. The muse kept the
        reasoning and handed over a fact it would otherwise have had to assert.

    **The direction of this rule's error, stated before the data exists.** It is
    conservative *toward* ``arithmetic-offloaded``: a whole-problem solver
    written in an idiom none of the four token sets catch is classified
    ``arithmetic-offloaded``. So the ``reasoning-displaced`` count is a **floor**,
    and the rule errs in the direction that **flatters arm C** — it under-counts
    the very displacement the counter-hypothesis predicts.

    That hole is closed by procedure rather than by fudging the rule.
    ``result_contains_truth`` — a standalone ``76`` in the captured output — is
    recorded on every execution and is **never** part of the classification. Any
    execution classified ``arithmetic-offloaded`` whose result contains the truth
    is flagged ``audit_required``, and the pre-registration binds task t18 to
    hand-audit every flagged execution and report the audit, agreeing or not.
    """
    argv = call.get("arguments", {}).get("command")
    argv_list = [str(part) for part in argv] if isinstance(argv, (list, tuple)) else []
    command_text = " ".join(argv_list)
    result = str(call.get("result") or "")
    signals = _signals(command_text)
    ran = bool(call.get("ran"))
    contains_truth = bool(_TRUTH_TOKEN_RE.search(result))

    if not ran:
        klass = CLASS_DEGRADED
    elif len(signals) == 4:
        klass = CLASS_DISPLACED
    else:
        klass = CLASS_ARITHMETIC
    return {
        "rule": "R-C1",
        "class": klass,
        "signals": signals,
        "command": argv_list,
        "command_chars": len(command_text),
        "result_contains_truth": contains_truth,
        "audit_required": bool(klass == CLASS_ARITHMETIC and contains_truth),
    }


# ── counsel quality, scored with the answer removed ───────────────────────────

COUNSEL_VERDICT_COUNSEL = "COUNSEL"
COUNSEL_VERDICT_RESTATED = "RESTATED"
COUNSEL_VERDICT_SILENT = "SILENT"
COUNSEL_VERDICTS: tuple[str, ...] = (
    COUNSEL_VERDICT_COUNSEL,
    COUNSEL_VERDICT_RESTATED,
    COUNSEL_VERDICT_SILENT,
)

#: Below this many content words a bigram ratio is noise rather than a
#: measurement. Mirrors ``muse_challenge``'s own guard.
_MIN_WORDS_FOR_RATIO = 4


def counsel_score(text: str) -> dict[str, Any]:
    """Score the counsel in *text*, with the answer stripped out first.

    Three verdicts and no more, because the fourth and fifth
    (``muse_challenge``'s ``UNTARGETED`` / ``UNARGUED``) need a per-case anchor
    key naming what the counsel should have landed on. This experiment's task is
    solving a problem rather than challenging a stated conclusion, so no such key
    exists and inventing one would be inventing a grader.

    ``restatement`` is the fraction of the response's content bigrams the
    **problem statement** already contained — a response that re-words the
    question is not counsel about it.

    Independence from correctness is structural: :func:`strip_answer_lines` runs
    first, so the same narration scores identically whether it ended on 76 or on
    72. ``tests/test_muse_arms.py`` asserts exactly that, and asserts the scorer
    is not vacuous (a bare answer line scores ``SILENT``, a genuine reframing
    scores ``COUNSEL``).

    **The known limit, stated before the numbers exist.** ``_unnegated_hits``
    filters negated challenge vocabulary only for the phrases
    ``muse_challenge`` marks polarity-sensitive; the exemptions are load-bearing
    there and are inherited here unchanged. So agreement written in exempt
    vocabulary — *"there is no unstated assumption; I would not change a thing"*
    — still scores ``COUNSEL``. The error **flatters** the response, in every
    arm equally, and ``FIXTURE_EXEMPT_NEGATION`` pins it as a passing test so it
    is a recorded limit rather than a defect waiting to be rediscovered.
    """
    body = strip_answer_lines(text).replace(MARKER_DONE, " ")
    tokens = word_tokens(body)
    content = _content_tokens(body)
    response_bigrams = _bigrams(content)
    problem_bigrams = _bigrams(_content_tokens(PROBLEM_STATEMENT))
    shared = (
        len(response_bigrams & problem_bigrams) / len(response_bigrams) if response_bigrams else 0.0
    )
    moves = [move for move in MOVES if _unnegated_hits(tokens, MOVE_MARKERS[move])]

    if not body.strip():
        verdict, reason = COUNSEL_VERDICT_SILENT, "nothing but the answer"
    elif len(content) >= _MIN_WORDS_FOR_RATIO and shared > MAX_SHARED_BIGRAMS:
        verdict, reason = (
            COUNSEL_VERDICT_RESTATED,
            f"near-copy of the problem: {shared:.2f} shared bigrams (limit {MAX_SHARED_BIGRAMS})",
        )
    elif not moves:
        verdict, reason = (
            COUNSEL_VERDICT_RESTATED,
            "no counsel move: names no premise, no failure condition, no alternative framing",
        )
    else:
        verdict, reason = COUNSEL_VERDICT_COUNSEL, "/".join(moves)
    return {
        "verdict": verdict,
        "reason": reason,
        "moves": moves,
        "content_words": len(content),
        "restatement": round(shared, 3),
        # A regex cannot tell whether the counsel is any GOOD, and saying so is
        # better than implying otherwise (the proof.py precedent).
        "counsel_soundness": "not machine-graded — read it",
    }


# ── the wired tools: one bench, however many lanes ────────────────────────────


class ArmTools:
    """The lanes wired for one arm, on ONE :class:`~embodiment.muse.MuseToolBench`.

    Arm ``C`` needs the pad's tools and the workspace's tool on a single bench,
    and neither module composes with the other by design — what a bench holds is
    the host's to state. This is that statement, and it lives in the harness
    rather than in :mod:`embodiment` on purpose: composition is an experimental
    variable here, not a library opinion.

    Dispatch is by **schema membership**, resolved once at construction. A name
    no wired lane offers is refused here with
    :class:`~embodiment.loop.UnknownToolError` and never reaches a lane, so a
    hallucinated name is counted once, in one place, instead of by whichever lane
    happened to see it first.

    Every call — arguments and the exact result text the muse read back — is
    appended to :attr:`calls`. That list is the raw record arm ``C``'s
    classification is computed from.
    """

    def __init__(self, lanes: Iterable[tuple[str, Any]]) -> None:
        self._lanes: tuple[tuple[str, Any], ...] = tuple(lanes)
        self._owner: dict[str, str] = {}
        for label, _lane in self._lanes:
            for name in LANE_TOOL_NAMES.get(label, ()):
                self._owner.setdefault(name, label)
        self.calls: list[dict[str, Any]] = []
        self.off_protocol: int = 0

    @property
    def lanes(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self._lanes)

    @property
    def schema(self) -> tuple[dict[str, Any], ...]:
        """Every wired lane's schema, in wiring order."""
        return tuple(tool for _label, lane in self._lanes for tool in lane.schema)

    def lane(self, label: str) -> Any:
        """The wired lane object called *label*, or ``None``."""
        for name, lane in self._lanes:
            if name == label:
                return lane
        return None

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run one call, recording it whatever happens.

        Satisfies :data:`~embodiment.muse.MuseToolExecuteFn`. A lane that raises
        is recorded and the raise is passed through: :mod:`embodiment.muse`
        already turns it into a recorded ``muse-tool-failed`` plus readable text
        the muse keeps thinking with, and swallowing it here would hide a fault
        the host is supposed to see.
        """
        label = self._owner.get(name, "")
        record: dict[str, Any] = {
            "seq": len(self.calls),
            "lane": label or "unwired",
            "name": name,
            "arguments": dict(arguments) if isinstance(arguments, dict) else {},
            "result": "",
            "error": None,
            "ran": False,
        }
        if not label:
            self.off_protocol += 1
            record["error"] = f"no wired lane offers {name!r}"
            self.calls.append(record)
            from embodiment.loop import UnknownToolError

            raise UnknownToolError(
                f"no wired lane offers {name!r}; this arm offers "
                f"{', '.join(sorted(self._owner)) or '(nothing)'}"
            )
        try:
            outcome = self.lane(label).execute(name, record["arguments"])
        except Exception as exc:  # noqa: BLE001  # recorded, then re-raised for the seam
            record["error"] = f"{type(exc).__name__}: {exc}"
            self.calls.append(record)
            raise
        value = getattr(outcome, "result", None)
        record["result"] = str(outcome if value is None else value)
        record["ran"] = self._ran(label, record["result"])
        self.calls.append(record)
        return outcome

    @staticmethod
    def _ran(label: str, result: str) -> bool:
        """Whether a workspace call actually executed something.

        :meth:`embodiment.workspace.MuseWorkspace.execute` never raises for an
        engine problem — it returns readable text — so "did this run?" is read
        off the refusal texts that module documents. Pad calls always "ran"; a
        pad has nothing to be unavailable.

        :data:`~embodiment.workspace.CLOSED_TEXT` is imported rather than
        retyped, because this list drifted from that module once already: it
        was missing the closed-lane refusal entirely, so a call the lane had
        refused would have been counted as a call that ran. **No published
        result changes** — the committed series contains zero closed-lane
        refusals (grep the run's own JSONL), so this corrects the instrument
        for future runs rather than re-grading a past one.
        """
        if label != LANE_WORKSPACE:
            return True
        refusals = (
            "command must be a non-empty list",
            "no workspace is available",
            "the command could not be run",
            "its result could not be read",
            CLOSED_TEXT,
        )
        return not any(refusal in result for refusal in refusals)

    def bench(self, complete: Any) -> MuseToolBench:
        """The bench handed to :class:`~embodiment.muse.MuseLoop` as ``tools=``."""
        return MuseToolBench(schema=self.schema, complete=complete, execute=self.execute)

    def counts(self) -> dict[str, Any]:
        """Per-lane counters plus this composite's own off-protocol tally."""
        out: dict[str, Any] = {
            "lanes": list(self.lanes),
            "calls": len(self.calls),
            "off_protocol_calls": self.off_protocol,
            "by_name": {},
        }
        for call in self.calls:
            out["by_name"][call["name"]] = out["by_name"].get(call["name"], 0) + 1
        for label, lane in self._lanes:
            counts = lane.counts()
            out[label] = counts.to_dict() if hasattr(counts, "to_dict") else counts
        return out


def build_tools(arm: str, *, pad_dir: Optional[Path], provider: str) -> Optional[ArmTools]:
    """The wired lanes for *arm* — ``None`` when the arm wires none.

    ``None`` is not "an empty bench": it is ``MuseLoop(tools=None)``, the
    tools-off seam exactly as it shipped, which is what makes arm ``A`` the
    baseline rather than a degenerate case of the others.
    """
    lanes: list[tuple[str, Any]] = []
    for label in ARM_LANES[arm]:
        if label == LANE_PAD:
            lanes.append((label, MusePad.in_directory(pad_dir) if pad_dir else MusePad()))
        elif label == LANE_WORKSPACE:
            lanes.append((label, MuseWorkspace(provider=provider)))
    return ArmTools(lanes) if lanes else None


# ── the transport: ONE seam, every arm ────────────────────────────────────────


class RecordingSeam:
    """The model seam every arm runs on, recording every prompt and every reply.

    Two entry points and one body builder. :meth:`floor` is
    :data:`~embodiment.muse.MuseCompleteFn` — the tools-off call, byte-identical
    to the pre-seam release's — and :meth:`tools` is
    :data:`~embodiment.muse.MuseToolCompleteFn`. The only difference between the
    two bodies is the presence of ``tools``.

    **Nothing is retried.** A transport failure is recorded on the turn and then
    re-raised, so :meth:`embodiment.muse.MuseLoop.think` records its own
    degradation and the session ends. A harness that swallowed the error and
    returned empty content would be silently re-dialling for a better number.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        if not endpoint.startswith(("http://", "https://")):
            raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")
        self._endpoint = endpoint
        self._model = model
        self._key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self.turns: list[dict[str, Any]] = []

    # ── the two seams ─────────────────────────────────────────────────────────

    def floor(self, messages: list[dict[str, Any]]) -> ModelResponse:
        """The tools-off call. Arm ``A`` uses only this."""
        return self._call(messages, None)

    def tools(self, messages: list[dict[str, Any]], schema: list[dict[str, Any]]) -> ModelResponse:
        """The tool-carrying call. Arms ``B``/``C`` use this at depth 0."""
        return self._call(messages, schema)

    # ── one call ──────────────────────────────────────────────────────────────

    def _call(
        self, messages: list[dict[str, Any]], schema: Optional[list[dict[str, Any]]]
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if schema:
            body["tools"] = schema
        record: dict[str, Any] = {
            "seq": len(self.turns),
            "tools_on": bool(schema),
            "messages": [dict(message) for message in messages],
            "schema": [dict(tool) for tool in schema] if schema else None,
            "raw_content": "",
            "raw_reasoning": "",
            "tool_calls": [],
            "finish_reason": None,
            "usage": None,
            "latency_s": None,
            "transport_error": None,
            "argument_parse_errors": [],
            # Set by ask_for_the_answer on the one tools-off closing turn.
            "closing": False,
        }
        started = time.time()
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed into an artifact.
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            # Scheme pinned to http(s) in __init__; the endpoint is the operator's.
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            record["latency_s"] = round(time.time() - started, 2)
            record["transport_error"] = f"{type(exc).__name__}: {exc}"
            self.turns.append(record)
            raise

        record["latency_s"] = round(time.time() - started, 2)
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        calls = self._tool_calls(message, record)
        record["raw_content"] = message.get("content") or ""
        record["raw_reasoning"] = message.get("reasoning_content") or message.get("reasoning") or ""
        record["tool_calls"] = [call.to_dict() for call in calls]
        record["finish_reason"] = choice.get("finish_reason")
        record["usage"] = dict(usage) if usage else None
        self.turns.append(record)
        return ModelResponse(
            content=record["raw_content"],
            reasoning=record["raw_reasoning"],
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    @staticmethod
    def _tool_calls(message: dict[str, Any], record: dict[str, Any]) -> list[ToolCall]:
        """The reply's tool calls. Unparseable arguments are NAMED, not fatal.

        A model that emits malformed JSON arguments is model behaviour, not
        transport failure: the call is forwarded with empty arguments, the lane
        rejects it as unusable, and both facts land in the transcript. Letting
        the ``json`` error escape would kill the session over one bad turn and
        report it as a thinking degradation, which it is not.
        """
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments: dict[str, Any] = {}
            try:
                parsed = json.loads(function.get("arguments") or "{}")
                arguments = parsed if isinstance(parsed, dict) else {}
                if not isinstance(parsed, dict):
                    record["argument_parse_errors"].append(
                        f"{function.get('name')}: arguments were not an object"
                    )
            except (ValueError, TypeError) as exc:
                record["argument_parse_errors"].append(f"{function.get('name')}: {exc}")
            calls.append(
                ToolCall(
                    id=raw.get("id") or "",
                    name=function.get("name") or "",
                    arguments=arguments,
                )
            )
        return calls


# ── the runner: one function, three arms ──────────────────────────────────────


@dataclass
class ArmRun:
    """One arm, one repeat. Everything the transcript needs, and nothing derived."""

    arm: str
    repeat: int
    turns: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    insights: list[dict[str, Any]] = field(default_factory=list)
    exit_reason: str = ""
    muse_turns: int = 0
    tool_rounds: int = 0
    tokens: Optional[int] = None
    degradations: list[dict[str, Any]] = field(default_factory=list)
    lane_counts: dict[str, Any] = field(default_factory=dict)
    lane_degradations: list[dict[str, Any]] = field(default_factory=list)
    pad_lines: list[str] = field(default_factory=list)
    pad_render: str = ""
    elapsed_s: float = 0.0
    system_message: str = ""
    user_message: str = ""
    closing_error: Optional[str] = None

    def raw_text(self) -> str:
        """Every byte the muse wrote this run, in turn order.

        The verdict is read off this rather than off the folded insights: the
        insight path strips ``[done]`` and clips to
        :data:`INSIGHT_CHARS`, and a final answer must never be lost to a cap.
        """
        return "\n".join(turn["raw_content"] for turn in self.turns if turn["raw_content"])

    def to_dict(self) -> dict[str, Any]:
        text = self.raw_text()
        answer = read_answer(text)
        executions = [
            classify_execution(call)
            for call in self.tool_calls
            if call["name"] == WORKSPACE_TOOL_NAME
        ]
        final_turn = self.turns[-1]["raw_content"] if self.turns else ""
        return {
            "kind": "run",
            "run_id": f"{self.arm}-{self.repeat}",
            "arm": self.arm,
            "repeat": self.repeat,
            "lanes": list(ARM_LANES[self.arm]),
            # ── the raw record ────────────────────────────────────────────────
            "system_message": self.system_message,
            "user_message": self.user_message,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "insights": self.insights,
            "pad_lines": self.pad_lines,
            "pad_render": self.pad_render,
            # ── what the loop reported ────────────────────────────────────────
            "exit_reason": self.exit_reason,
            "muse_turns": self.muse_turns,
            "tool_rounds": self.tool_rounds,
            "tokens": self.tokens,
            "degradations": self.degradations,
            "lane_counts": self.lane_counts,
            "lane_degradations": self.lane_degradations,
            "elapsed_s": round(self.elapsed_s, 2),
            "closing_turn_used": any(turn.get("closing") for turn in self.turns),
            "closing_error": self.closing_error,
            "transport_errors": [t["transport_error"] for t in self.turns if t["transport_error"]],
            "truncated_turns": sum(1 for t in self.turns if t["finish_reason"] == "length"),
            # ── the dependent variables, all computed from the above ──────────
            "answer": answer,
            "hedges_in_final_turn": hedges_in(final_turn),
            "counsel": counsel_score(text),
            "guidance_lines": sum(
                len([line for line in (i["guidance"] or "").splitlines() if line.strip()])
                for i in self.insights
            ),
            "durable_insights": sum(
                1 for i in self.insights if i.get("kind") == COUNSEL_KIND_DURABLE
            ),
            "executions": executions,
            "answer_timeline": self.answer_timeline(),
            "belief_changed_after_execution": self.belief_changed(),
        }

    def answer_timeline(self) -> list[dict[str, Any]]:
        """The definite answer, if any, stated at each turn — in turn order."""
        timeline: list[dict[str, Any]] = []
        for turn in self.turns:
            read = read_answer(turn["raw_content"])
            timeline.append(
                {
                    "seq": turn["seq"],
                    "answer": read["answer"],
                    "verdict": read["verdict"],
                    "tool_calls": [call["name"] for call in turn["tool_calls"]],
                }
            )
        return timeline

    def belief_changed(self) -> Optional[bool]:
        """Did a definite answer change across an execution? ``None`` if unaskable.

        ``None`` — not ``False`` — when the run made no execution, or stated no
        definite answer on both sides of one: the question was never posed, and a
        fabricated ``False`` would read as evidence that execution changed
        nothing.
        """
        executions = [
            turn["seq"]
            for turn in self.turns
            if any(call["name"] == WORKSPACE_TOOL_NAME for call in turn["tool_calls"])
        ]
        if not executions:
            return None
        timeline = self.answer_timeline()
        first = executions[0]
        before = [row["answer"] for row in timeline if row["seq"] <= first and row["answer"]]
        after = [row["answer"] for row in timeline if row["seq"] > first and row["answer"]]
        if not before or not after:
            return None
        return before[-1] != after[-1]


def run_once(
    arm: str,
    repeat: int,
    *,
    seam: RecordingSeam,
    pad_dir: Optional[Path],
    provider: str,
) -> ArmRun:
    """Drive ONE arm once. The whole runner — every arm takes this exact path.

    The arm reaches this function only as a key into :data:`ARM_LANES` and
    :data:`LANE_PROTOCOLS`. There is no ``if arm ==`` anywhere below, and
    ``tests/test_muse_arms.py`` asserts that by AST.
    """
    started = time.time()
    tools = build_tools(arm, pad_dir=pad_dir, provider=provider)
    bench = tools.bench(seam.tools) if tools is not None else None
    loop = MuseLoop(
        seam.floor,
        controls=controls(),
        system=host_framing(arm),
        tools=bench,
        depth=0,
    )
    first_turn = len(seam.turns)
    try:
        outcome = loop.think(boundary())
    finally:
        workspace = tools.lane(LANE_WORKSPACE) if tools is not None else None
        if workspace is not None:
            workspace.destroy()

    closing_error = ask_for_the_answer(seam, seam.turns[first_turn:])

    run = ArmRun(arm=arm, repeat=repeat)
    run.closing_error = closing_error
    run.turns = seam.turns[first_turn:]
    run.tool_calls = list(tools.calls) if tools is not None else []
    run.insights = [insight.to_dict() for insight in outcome.insights]
    run.exit_reason = outcome.exit_reason
    run.muse_turns = outcome.turns
    run.tool_rounds = outcome.tool_rounds
    run.tokens = outcome.tokens
    run.degradations = [record.to_dict() for record in outcome.degradations]
    run.elapsed_s = time.time() - started
    if run.turns:
        messages = run.turns[0]["messages"]
        run.system_message = messages[0]["content"] if messages else ""
        run.user_message = messages[1]["content"] if len(messages) > 1 else ""
    if tools is not None:
        run.lane_counts = tools.counts()
        workspace = tools.lane(LANE_WORKSPACE)
        if workspace is not None:
            run.lane_degradations = [d.to_dict() for d in workspace.degradations]
        pad = tools.lane(LANE_PAD)
        if pad is not None:
            run.pad_render = pad.render()
            run.pad_lines = _pad_lines(pad)
    return run


def closing_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The muse's OWN history plus :data:`CLOSING_PROMPT`. Nothing is rebuilt.

    The last recorded turn carries the exact message list
    :class:`~embodiment.muse.MuseLoop` had assembled at that point — system
    framing, boundary, its own prior turns, and every tool exchange the loop
    ran. Appending to it means the closing question is asked of the real
    session, identically for every arm, with no reconstruction that could drift
    from what was actually sent.

    That turn's *own* reply is appended as an assistant message when it carried
    text. Its unresolved tool calls are deliberately **not** echoed: a message
    list ending in an assistant turn with tool calls and no results is a
    malformed exchange, and the calls were never run.

    ``[]`` when the session produced no turn at all — there is nothing to close.
    """
    if not turns:
        return []
    messages = [dict(message) for message in turns[-1]["messages"]]
    content = turns[-1]["raw_content"]
    if content.strip():
        messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": CLOSING_PROMPT})
    return messages


def ask_for_the_answer(seam: Any, turns: list[dict[str, Any]]) -> Optional[str]:
    """Put :data:`CLOSING_PROMPT` once, **tools-off**. Returns any failure text.

    Tools-off for every arm, via :meth:`RecordingSeam.floor`, so no arm can
    spend its closing turn on another tool call — which is exactly what the
    tool arms did with every turn they were given.

    A failure here is data like any other: it is returned and recorded, never
    retried. The run then simply has whatever answer its thinking session
    produced, which for arm A is usually one already.
    """
    messages = closing_messages(turns)
    if not messages:
        return "no turn to close"
    if turns[-1].get("transport_error"):
        # Dialling again straight after a transport failure is a retry in all
        # but name, and a degraded call is data. The run keeps its degradation.
        return "the session's last turn failed in transport; not re-dialling"
    try:
        seam.floor(messages)
    except Exception as exc:  # noqa: BLE001  # a closing failure is recorded, never retried
        return f"{type(exc).__name__}: {exc}"
    seam.turns[-1]["closing"] = True
    return None


def _pad_lines(pad: MusePad) -> list[str]:
    """The pad's own on-disk record, verbatim. ``[]`` for a pad with no file."""
    path = pad.path
    if path is None or not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


# ── the dry run: prove the arms differ only in tools, before spending a token ─


def wire_capture(arm: str, *, provider: str = PROVIDER_FAKE) -> dict[str, Any]:
    """What *arm* actually puts on the wire, captured off a real loop.

    Driven with a seam that records and immediately concludes, so this reports
    the true prompt rather than a reconstruction of it. Dials nothing.
    """
    captured: dict[str, Any] = {"messages": [], "schema": None, "tools_on": False}

    def floor(messages: list[dict[str, Any]]) -> ModelResponse:
        captured["messages"] = [dict(message) for message in messages]
        return ModelResponse(content=MARKER_DONE)

    def tools_seam(messages: list[dict[str, Any]], schema: list[dict[str, Any]]) -> ModelResponse:
        captured["messages"] = [dict(message) for message in messages]
        captured["schema"] = [dict(tool) for tool in schema]
        captured["tools_on"] = True
        return ModelResponse(content=MARKER_DONE)

    tools = build_tools(arm, pad_dir=None, provider=provider)
    bench = tools.bench(tools_seam) if tools is not None else None
    MuseLoop(floor, controls=controls(), system=host_framing(arm), tools=bench, depth=0).think(
        boundary()
    )
    messages = captured["messages"]
    return {
        "arm": arm,
        "lanes": list(ARM_LANES[arm]),
        "tools_on": captured["tools_on"],
        "tool_names": [
            (tool.get("function") or {}).get("name") for tool in (captured["schema"] or [])
        ],
        "system_message": messages[0]["content"] if messages else "",
        "user_message": messages[1]["content"] if len(messages) > 1 else "",
    }


def wire_report() -> dict[str, Any]:
    """The three arms' wire images plus the identity claims, as data."""
    captures = {arm: wire_capture(arm) for arm in ARMS}
    users = {arm: capture["user_message"] for arm, capture in captures.items()}
    systems = {arm: capture["system_message"] for arm, capture in captures.items()}
    return {
        "captures": captures,
        "user_message_identical": len(set(users.values())) == 1,
        "system_matches_reconstruction": {arm: systems[arm] == system_message(arm) for arm in ARMS},
        "system_extra_chars": {arm: len(systems[arm]) - len(systems[ARM_A]) for arm in ARMS},
        "task_framing_present": {arm: TASK_FRAMING in systems[arm] for arm in ARMS},
    }


# ── analysis: every table rendered from the committed transcript ──────────────


def load_records(path: Path) -> list[dict[str, Any]]:
    """Every JSONL record, skipping nothing and inventing nothing."""
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _runs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("kind") == "run"]


def answer_table(runs: list[dict[str, Any]]) -> list[str]:
    """The headline table: confidently-wrong rate per arm."""
    lines = ["| arm | n | " + " | ".join(VERDICTS) + " | confidently wrong |"]
    lines.append("|---" * (len(VERDICTS) + 3) + "|")
    for arm in ARMS:
        rows = [run for run in runs if run["arm"] == arm]
        if not rows:
            continue
        counts = {verdict: 0 for verdict in VERDICTS}
        for run in rows:
            counts[run["answer"]["verdict"]] += 1
        wrong = sum(1 for run in rows if run["answer"]["confidently_wrong"])
        rate = f"{wrong}/{len(rows)} ({wrong / len(rows):.0%})"
        cells = " | ".join(str(counts[verdict]) for verdict in VERDICTS)
        lines.append(f"| {arm} | {len(rows)} | {cells} | {rate} |")
    return lines


def pad_table(runs: list[dict[str, Any]]) -> list[str]:
    """Pad protocol adherence — the second dependent variable."""
    lines = [
        "| arm | runs | entries | intend | observe | conclude | revise "
        "| open intents | rejected | off-protocol |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        rows = [run for run in runs if run["arm"] == arm and LANE_PAD in run["lanes"]]
        if not rows:
            continue
        pads = [run["lane_counts"].get(LANE_PAD, {}) for run in rows]
        kinds = {kind: 0 for kind in ("intend", "observe", "conclude", "revise")}
        for pad in pads:
            for kind, count in (pad.get("kinds") or {}).items():
                kinds[kind] = kinds.get(kind, 0) + count
        lines.append(
            f"| {arm} | {len(rows)} | {sum(p.get('entries', 0) for p in pads)} "
            f"| {kinds['intend']} | {kinds['observe']} | {kinds['conclude']} | {kinds['revise']} "
            f"| {sum(p.get('open_intents', 0) for p in pads)} "
            f"| {sum(p.get('rejected_calls', 0) for p in pads)} "
            f"| {sum(p.get('off_protocol_calls', 0) for p in pads)} |"
        )
    return lines


def execution_summary(runs: list[dict[str, Any]]) -> list[str]:
    """The R-C1 roll-up per arm, plus how many argv the workspace refused.

    The refused count is its own column because it is the difference between
    "the muse chose not to execute" and "the muse could not form a command the
    tool would accept" — and the wiring smoke run hit the second, 3 times of 3.
    """
    lines = [
        "| arm | executions | " + " | ".join(EXECUTION_CLASSES) + " | refused argv |",
        "|---|---|" + "---|" * (len(EXECUTION_CLASSES) + 1),
    ]
    for arm in ARMS:
        rows = [run for run in runs if run["arm"] == arm and LANE_WORKSPACE in run["lanes"]]
        if not rows:
            continue
        executions = [e for run in rows for e in (run.get("executions") or [])]
        counts = {name: 0 for name in EXECUTION_CLASSES}
        for execution in executions:
            counts[execution["class"]] += 1
        refused = sum(
            (run["lane_counts"].get(LANE_WORKSPACE) or {}).get("rejected_calls", 0) for run in rows
        )
        cells = " | ".join(str(counts[name]) for name in EXECUTION_CLASSES)
        lines.append(f"| {arm} | {len(executions)} | {cells} | {refused} |")
    if len(lines) == 2:
        lines.append("| — | 0 | (no arm wired a workspace) | — | — | — |")
    return lines


def _cell(text: str, cap: int = 80) -> str:
    """One table cell's worth of arbitrary model text.

    Real commands carry newlines and pipes — the smoke run's first successful
    execution was a five-line ``python3 -c`` payload — and either one breaks a
    markdown row silently, turning the committed results table into something
    that renders wrong and reads plausibly. Whitespace is collapsed and the pipe
    escaped; the FULL command is in the transcript regardless, so nothing is
    lost by clipping the display.
    """
    flat = " ".join((text or "").split()).replace("|", "\\|").replace("`", "'")
    return flat[:cap] + ("…" if len(flat) > cap else "")


def execution_table(runs: list[dict[str, Any]]) -> list[str]:
    """Arm C's executions, one row each, classified by rule ``R-C1``."""
    lines = [
        "| run | # | class | signals | audit | truth in output | command |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        for index, execution in enumerate(run.get("executions") or []):
            command = _cell(" ".join(execution["command"]))
            lines.append(
                f"| `{run['run_id']}` | {index} | {execution['class']} "
                f"| {'/'.join(execution['signals']) or '—'} "
                f"| {'AUDIT' if execution['audit_required'] else '—'} "
                f"| {execution['result_contains_truth']} | `{command}` |"
            )
    if len(lines) == 2:
        lines.append("| — | — | (no executions recorded) | — | — | — | — |")
    return lines


def counsel_table(runs: list[dict[str, Any]]) -> list[str]:
    """Counsel quality, scored with the answer stripped out."""
    lines = [
        "| arm | n | " + " | ".join(COUNSEL_VERDICTS) + " | mean moves | mean words "
        "| guidance lines |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        rows = [run for run in runs if run["arm"] == arm]
        if not rows:
            continue
        counts = {verdict: 0 for verdict in COUNSEL_VERDICTS}
        for run in rows:
            counts[run["counsel"]["verdict"]] += 1
        moves = sum(len(run["counsel"]["moves"]) for run in rows) / len(rows)
        words = sum(run["counsel"]["content_words"] for run in rows) / len(rows)
        cells = " | ".join(str(counts[verdict]) for verdict in COUNSEL_VERDICTS)
        lines.append(
            f"| {arm} | {len(rows)} | {cells} | {moves:.1f} | {words:.0f} "
            f"| {sum(run['guidance_lines'] for run in rows)} |"
        )
    return lines


def health_table(runs: list[dict[str, Any]]) -> list[str]:
    """What degraded. A degraded call is data, so it gets its own table."""
    lines = [
        "| arm | n | exits | degradations | lane degradations | transport errors "
        "| truncated turns | closing turns | mean elapsed s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        rows = [run for run in runs if run["arm"] == arm]
        if not rows:
            continue
        exits: dict[str, int] = {}
        for run in rows:
            exits[run["exit_reason"]] = exits.get(run["exit_reason"], 0) + 1
        exit_text = ", ".join(f"{name}×{count}" for name, count in sorted(exits.items()))
        lines.append(
            f"| {arm} | {len(rows)} | {exit_text} "
            f"| {sum(len(run['degradations']) for run in rows)} "
            f"| {sum(len(run['lane_degradations']) for run in rows)} "
            f"| {sum(len(run['transport_errors']) for run in rows)} "
            f"| {sum(run['truncated_turns'] for run in rows)} "
            f"| {sum(1 for run in rows if run.get('closing_turn_used'))}/{len(rows)} "
            f"| {sum(run['elapsed_s'] for run in rows) / len(rows):.1f} |"
        )
    return lines


def belief_table(runs: list[dict[str, Any]]) -> list[str]:
    """Whether an execution moved a stated belief, for the runs that made one."""
    lines = ["| run | executions | belief changed | answers stated in order |", "|---|---|---|---|"]
    for run in runs:
        if not run.get("executions"):
            continue
        stated = [row["answer"] for row in run["answer_timeline"] if row["answer"] is not None]
        changed = run["belief_changed_after_execution"]
        label = "unaskable" if changed is None else str(changed)
        lines.append(
            f"| `{run['run_id']}` | {len(run['executions'])} | {label} "
            f"| {', '.join(str(a) for a in stated) or '—'} |"
        )
    if len(lines) == 2:
        lines.append("| — | — | (no executions recorded) | — |")
    return lines


def analyse(path: Path) -> str:
    """Render every results table from the committed transcript, never by hand.

    Three of this repo's own corrections were transcription errors made between
    a run and its write-up. A results document pastes this output.
    """
    records = load_records(path)
    runs = _runs(records)
    config = next((r for r in records if r.get("kind") == "config"), {})
    pin = next((r for r in records if r.get("kind") == "oracle"), {})

    lines: list[str] = []
    if config.get("measured") is False:
        lines.append(
            "**WIRING RUN — NOT MEASURED** (`measured: false`). Read no dependent "
            "variable off this transcript."
        )
        lines.append("")
    lines.append(
        f"runs: {len(runs)} · muse: {config.get('muse_model')} · "
        f"provider: {config.get('workspace_provider')}"
    )
    lines.append(
        f"oracle: truth={pin.get('truth')} trap={pin.get('trap')} "
        f"rejected={pin.get('trap_is_rejected')} matches_pin={pin.get('matches_pin')}"
    )
    lines.append("")
    lines.append("### 1. Final answers (the headline)")
    lines.append("")
    lines.extend(answer_table(runs))
    lines.append("")
    lines.append("### 2. Pad protocol adherence")
    lines.append("")
    lines.extend(pad_table(runs))
    lines.append("")
    lines.append("### 3. Arm C executions (rule R-C1)")
    lines.append("")
    lines.extend(execution_summary(runs))
    lines.append("")
    lines.extend(execution_table(runs))
    lines.append("")
    lines.append("### 4. Counsel quality (answer stripped before scoring)")
    lines.append("")
    lines.extend(counsel_table(runs))
    lines.append("")
    lines.append("### 5. Did an execution move a stated belief?")
    lines.append("")
    lines.extend(belief_table(runs))
    lines.append("")
    lines.append("### 6. Health — degradation is data")
    lines.append("")
    lines.extend(health_table(runs))
    audits = [
        f"{run['run_id']}#{index}"
        for run in runs
        for index, execution in enumerate(run.get("executions") or [])
        if execution["audit_required"]
    ]
    lines.append("")
    lines.append(
        "AUDIT REQUIRED (rule R-C1's named hole — hand-audit each and report): "
        + (", ".join(audits) if audits else "none")
    )
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────


def _parse_arms(raw: str) -> list[str]:
    arms = [part.strip().upper() for part in raw.split(",") if part.strip()]
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise SystemExit(
            f"error: unknown arm(s) {', '.join(unknown)}; choose from {', '.join(ARMS)}"
        )
    return arms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--arms", default=",".join(ARMS), help="comma-separated subset of A,B,C")
    parser.add_argument("--n", type=int, default=1, help="repeats per arm")
    parser.add_argument(
        "--provider",
        default=PROVIDER_DOCKER,
        choices=(PROVIDER_DOCKER, PROVIDER_FAKE),
        help="arm C's headspace backend; 'fake' is in-memory and needs no daemon",
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs/live-test-results/muse-arms.jsonl"),
        help="JSONL transcript path; the config lands beside it as *-config.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build everything, dial nothing.")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "This is a wiring run, not a measured one. Stamps measured=false into the "
            "config and is REQUIRED by --provider fake."
        ),
    )
    parser.add_argument(
        "--analyse",
        action="store_true",
        help="Re-render the results tables from an existing --out transcript.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.analyse:
        print(analyse(Path(args.out)))
        return 0

    pin = oracle_pin()
    arms = _parse_arms(args.arms)

    if args.dry_run:
        report = wire_report()
        print(json.dumps({"kind": "oracle", **pin}, indent=2))
        for arm in arms:
            capture = report["captures"][arm]
            print(
                f"\n{'=' * 78}\narm {arm} — lanes {capture['lanes'] or ['(none)']} "
                f"— tools {capture['tool_names'] or ['(none)']}\n{'=' * 78}"
            )
            print("--- system ---")
            print(capture["system_message"])
            print("--- user ---")
            print(capture["user_message"])
        print(
            "\n"
            + json.dumps(
                {
                    "user_message_identical": report["user_message_identical"],
                    "system_matches_reconstruction": report["system_matches_reconstruction"],
                    "system_extra_chars": report["system_extra_chars"],
                    "task_framing_present": report["task_framing_present"],
                },
                indent=2,
            )
        )
        return 0

    # headspace's ``fake`` provider reports success WITHOUT executing anything —
    # it fabricates a result package with no captured output. That is exactly
    # right for a wiring smoke run and is poison in a measured one: arm C's
    # whole dependent variable is what a real execution returned. So the fake
    # backend is reachable only behind an explicit --smoke, which also stamps
    # ``measured: false`` into the config a reader will meet first.
    if args.provider == PROVIDER_FAKE and not args.smoke:
        print(
            f"error: --provider {PROVIDER_FAKE} fabricates success without executing anything",
            file=sys.stderr,
        )
        print(
            "hint: pass --smoke to acknowledge this is a wiring run, or use "
            f"--provider {PROVIDER_DOCKER} for a measured one",
            file=sys.stderr,
        )
        return 1

    if not pin["matches_pin"]:
        print("error: the imported oracle no longer matches its pin", file=sys.stderr)
        print(
            "hint: truth()/grade() must stay byte-stable for the whole series; "
            "a change restarts it",
            file=sys.stderr,
        )
        return 2

    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        print(f"error: {API_KEY_ENV} is not set", file=sys.stderr)
        print("hint: the gateway requires Authorization: Bearer <key>", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    config = write_config_preamble(
        str(out.with_name(out.stem + "-config.json")),
        cortex_model="(none — this experiment dials no cortex)",
        cortex_temperature=args.temperature,
        muse_model=args.muse_model,
        muse_temperature=args.temperature,
        max_turns=MAX_TURNS,
        staleness_policy="n/a-single-boundary",
        n=args.n,
        extra={
            "experiment": "muse-arms: tools-off / +pad / +pad+workspace (issue #21)",
            "preregistration": "docs/live-test-results/muse-arms-preregistration.md",
            "arms": arms,
            "arm_lanes": {arm: list(ARM_LANES[arm]) for arm in arms},
            "base_url": args.base_url,
            "muse_max_tokens": args.max_tokens,
            "max_quiet_turns": MAX_QUIET_TURNS,
            "max_tool_rounds": MAX_TOOL_ROUNDS,
            "max_tool_result_chars": MAX_TOOL_RESULT_CHARS,
            "workspace_provider": args.provider,
            "classification_rule": "R-C1",
            "retries": 0,
            # False means "wiring only": read no dependent variable off this file.
            "measured": not args.smoke,
        },
    )

    seam = RecordingSeam(
        base_url=args.base_url,
        model=args.muse_model,
        api_key=api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    with open(out, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "config", **config}) + "\n")
        handle.write(json.dumps({"kind": "oracle", **pin}) + "\n")
        handle.write(
            json.dumps({"kind": "problem", "statement": PROBLEM_STATEMENT, "framing": TASK_FRAMING})
            + "\n"
        )
        handle.flush()
        with tempfile.TemporaryDirectory(prefix="muse-arms-") as scratch:
            for repeat in range(args.n):
                for arm in arms:
                    pad_dir = Path(scratch) / f"{arm}-{repeat}"
                    pad_dir.mkdir(parents=True, exist_ok=True)
                    run = run_once(
                        arm,
                        repeat,
                        seam=seam,
                        pad_dir=pad_dir,
                        provider=args.provider,
                    )
                    record = run.to_dict()
                    handle.write(json.dumps(record) + "\n")
                    handle.flush()
                    print(
                        f"{run.arm}-{run.repeat}: {record['answer']['verdict']:<10} "
                        f"counsel={record['counsel']['verdict']:<8} "
                        f"exit={run.exit_reason:<9} turns={run.muse_turns} "
                        f"tools={len(run.tool_calls)} {run.elapsed_s:.1f}s",
                        file=sys.stderr,
                    )
    print(analyse(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
