#!/usr/bin/env python3
"""worker_scoped_overhead — what a *tiny* scoped worker call actually costs.

Plan task **t8** of `error-derived-timeouts-bee-hive-architecture`
(`docs/plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md`),
covering claim ``c43`` and honesty condition ``h29``. A pre-measurement: it
runs *before* the B0/B1/B2 sweep fixes its call granularity, so the sweep cites
a number instead of a hope.

The claim under test
--------------------
The Bee-Hive's B1 tier argues scoped worker calls are cheap: *"the cortex
spends 5,000–14,000 tokens on a turn; a scoped worker call spends tens. At 76.4
tok/s and ~9-way concurrency, N scoped calls cost less wall clock than one
cortex turn."* Both cited numbers come from
`docs/live-test-results/worker-throughput.md` — **and that series measured
1200-token completions**. A B1 call is *tens* of tokens. In that regime the
per-call costs that do not scale with completion length (queueing, prefill of
repeated context, request framing, network) stop being a rounding error and can
dominate, and the ~9× effective-concurrency figure has no reason to transfer
unexamined. Nobody had measured it. This module does.

What is actually varied
-----------------------
Four axes, crossed into a committed table of cells (:data:`CELLS`) rather than
swept by flags, so the shape of the measurement is reviewable in git:

* **completion budget** — ``scoped`` (:data:`SCOPED_MAX_TOKENS`, *tens* of
  tokens, the B1 regime) against ``natural`` (:data:`NATURAL_MAX_TOKENS`, room
  to finish), because "what does a scoped call cost" has two honest readings:
  what it costs when you *cap* it, and what it costs when you *let it answer*.
  Reporting only the first would hide a worker that cannot answer in tens of
  tokens at all.
* **thinking** — ``off``/``on`` on the wire (:data:`THINKING_WIRE`, the same
  ``chat_template_kwargs`` convention `examples/arch_arms.py` already uses).
  The worker is a thinking model whose reasoning text ran ~90% of every
  completion in the throughput series; whether a scoped call can be answered in
  tens of tokens is *entirely* a question about whether that reasoning can be
  suppressed, so the toggle is a measured axis, never an assumption.
* **context size** — ``lean`` (the unit's own block, as
  `examples/arch_league.py:describe_text` renders it) against ``rich`` (that
  block behind a realistic shared preamble: rules, roster, map notes, recent
  history). This is the prefill axis ``c43`` names. Because the ``rich``
  preamble is *identical* across calls, this measures the cost of repeated
  context the way B1 would actually pay it — see :func:`residual_seconds` on
  what a null result here does and does not prove.
* **width** — 1 and 8, the two widths the B1 economics claim rests on
  (single-stream baseline, and the width `worker-throughput.md` measured as
  this rig's practical throughput ceiling).

Reuse, not reimplementation
---------------------------
The measurement shape is `examples/worker_throughput.py`'s, deliberately: same
per-stream/aggregate separation (**never** conflated), same
``effective_concurrency = aggregate / per_stream_mean``, same bounded
:func:`concurrent.futures.wait` with no ``while`` anywhere, same one-fresh-seam-
per-call rule (`WorkerSeam.meter` has no lock), same "a failed call is DATA,
never a raise from a thread". The transport is
`examples/worker_seam.py`'s :class:`~worker_seam.WorkerSeam` subclassed
(:class:`ScopedSeam`) for exactly two additions: extra wire keys for the
thinking toggle, and keeping the raw request/response so a test can assert what
went on the wire. The dial is resolved by
:func:`~worker_seam.resolve_worker_config` — flags and ``EMBODIMENT_WORKER_*``
only, **never** a silent fallback to another endpoint.

The metric this task adds: residual overhead
--------------------------------------------
:func:`residual_seconds` is the number ``c43`` asks for — the share of a call's
wall clock that its *completion tokens do not explain*. Everything the B1
economics claim assumes is cheap lives in that residual. Its arithmetic is
pinned against synthetic data in ``tests/test_worker_scoped_overhead.py``
before it is ever trusted on a live number, matching `worker_throughput.py`'s
own habit.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/worker_scoped_overhead.py \\
        --worker-url http://thor.tail0be7e0.ts.net:8000/v1 \\
        --worker-model unsloth/Qwen3.6-35B-A3B-NVFP4 \\
        --out docs/live-test-results/worker-scoped-overhead.jsonl \\
        --summary-out docs/live-test-results/worker-scoped-overhead-summary.json \\
        --markdown-out docs/live-test-results/worker-scoped-overhead-tables.md
"""

from __future__ import annotations

import argparse
import functools
import itertools
import json
import math
import re
import statistics
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.worker_seam import (  # noqa: E402
    THOR_WORKER_MODEL_DOCUMENTED,
    THOR_WORKER_URL_DOCUMENTED,
    WorkerConfig,
    WorkerSeam,
    resolve_worker_config,
)

__all__ = [
    "SCOPED_MAX_TOKENS",
    "NATURAL_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "THINKING_WIRE",
    "REFERENCE_DECODE_TOK_S",
    "REFERENCE_WIDTH8_AGGREGATE_TOK_S",
    "REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY",
    "CORTEX_TURN_TOKENS_LOW",
    "CORTEX_TURN_TOKENS_HIGH",
    "CORTEX_TURN_SECONDS_LOW",
    "CORTEX_TURN_SECONDS_HIGH",
    "SITUATIONS",
    "SHARED_PREAMBLE",
    "SCOPED_QUESTION",
    "SCOPED_SYSTEM_PROMPT",
    "CONTEXT_LEAN",
    "CONTEXT_RICH",
    "Situation",
    "build_prompt",
    "parse_menu_index",
    "Cell",
    "CELLS",
    "ScopedSeam",
    "CallSpec",
    "CallRecord",
    "residual_seconds",
    "CellSummary",
    "CellRun",
    "ProbeResult",
    "concurrency_transfer",
    "prefill_cost",
    "thinking_cost",
    "scoped_calls_per_cortex_turn",
    "run_probe",
    "parse_batch_overrides",
    "apply_batch_overrides",
    "RETRY_BACKOFF_SECONDS",
    "retry_batches",
    "without_retry_batches",
    "transport_summary",
    "rebuild_from_artifacts",
    "render_markdown",
    "build_parser",
    "main",
]

# ── budgets, rates and the figures this task is checking ─────────────────────

#: *Tens* of tokens — the B1 regime, and the whole point of this probe. Chosen
#: so a suppressed-thinking answer to a typed question ("menu_index=3: <one
#: short clause>") fits comfortably while nothing longer can hide inside it.
SCOPED_MAX_TOKENS = 48

#: Room to finish. NOT d16's 16000 cortex floor: this is a single bare
#: completion answering one typed question, and 2000 is already ~40x the scoped
#: budget — generous enough that a natural answer is measured rather than
#: clipped, bounded enough that the thinking-on cells cannot run away with the
#: window this probe has to run in.
NATURAL_MAX_TOKENS = 2000

DEFAULT_TEMPERATURE = 0.3

#: What each thinking mode puts on the wire, as data rather than as a branch —
#: the convention `examples/arch_arms.py` established and
#: `docs/live-test-results/arch-arms-sampling.json` commits. A mode with no
#: entry here is a config error, never a silent no-op.
THINKING_WIRE: dict[str, dict[str, Any]] = {
    "on": {"chat_template_kwargs": {"enable_thinking": True}},
    "off": {"chat_template_kwargs": {"enable_thinking": False}},
}

#: `docs/live-test-results/worker-throughput.md`, width 1: per-stream mean
#: **76.43 tok/s**, measured on 1200-token completions. Used ONLY as the
#: reference decode rate the residual is computed against — see
#: :func:`residual_seconds` for why that choice is stated rather than assumed.
REFERENCE_DECODE_TOK_S = 76.43

#: The same document's width-8 readings, the two figures the B1 economics claim
#: cites. This probe's job is to say whether they transfer to tiny calls.
REFERENCE_WIDTH8_AGGREGATE_TOK_S = 254.18
REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY = 6.14
REFERENCE_WIDTH14_EFFECTIVE_CONCURRENCY = 8.993

#: One cortex turn, as the spec itself measures it
#: (`docs/specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md`:
#: "every recurring task re-pays a full cortex turn (5,000-14,265 tokens,
#: 400-730s)"). A RANGE, never a point estimate — the comparison below reports
#: both ends rather than picking the flattering one.
CORTEX_TURN_TOKENS_LOW = 5000
CORTEX_TURN_TOKENS_HIGH = 14265
CORTEX_TURN_SECONDS_LOW = 400.0
CORTEX_TURN_SECONDS_HIGH = 730.0

# ── the prompts: a genuine B1 scoped question against a league unit ──────────

CONTEXT_LEAN = "lean"
CONTEXT_RICH = "rich"

#: The typed question. Small enumerable answer space, exactly the B1 shape:
#: the harness keeps control flow and the worker returns one index. Note it
#: asks for the answer FIRST — a scoped call whose answer arrives after its
#: justification is a scoped call that truncation destroys.
SCOPED_QUESTION = (
    "Answer with exactly one line and nothing else:\n"
    "menu_index=<n>: <at most eight words of why>\n"
    "where <n> is one of the menu indices above."
)

SCOPED_SYSTEM_PROMPT = (
    "You are a scoped worker in a real-time strategy match. You are asked one "
    "typed question about one unit and you answer it in one line. You do not "
    "act on the arena, you do not decide what happens next, and you never ask "
    "for more information: your commander issues the order and may override "
    "you. Answer in the exact format requested, first token first."
)

#: The shared, IDENTICAL-across-calls context a B1 sweep would re-send on every
#: scoped call. This is the "repeated context" whose per-call prefill ``c43``
#: names as the unmeasured cost. Fixed and committed: editing it invalidates
#: comparison against any run committed before the edit.
SHARED_PREAMBLE = """\
MATCH RULES (identical every round, restated for every scoped question)

You are advising one team in a fog-of-war real-time strategy match. Time
advances in ticks. A unit is "due" when it holds a legal action and no order is
outstanding. Every due unit is offered a numbered MENU of legal actions; the
only legal answer is one of those indices. An index that is not on the menu is
discarded by the arena and the unit idles for the round, which is always worse
than a mediocre legal action.

Scoring, in priority order:
  1. crates delivered to your own depot          (+10 each)
  2. crates held at the end of the match          (+2 each)
  3. enemy crates denied (contested at their depot) (+1 each)
  4. units lost                                    (-4 each)
A carrying unit that is destroyed drops its crate where it falls.

MAP NOTES (static)
  The arena is 48x48. Two depots: BLUE at (4,4), RED at (44,44). Crates spawn
  along the central diagonal band and on the two flank spurs at (10,38) and
  (38,10). The central band is open ground with no cover; both flank spurs are
  broken terrain that blocks line of sight but costs one extra tick to cross.
  A unit standing in broken terrain is not visible to an enemy more than six
  tiles away.

YOUR ROSTER (BLUE)
  b1  scout     fast, fragile, carries one crate, sees eight tiles
  b2  scout     fast, fragile, carries one crate, sees eight tiles
  b3  hauler    slow, tough, carries three crates, sees four tiles
  b4  hauler    slow, tough, carries three crates, sees four tiles
  b5  guard     medium speed, contests depots, carries nothing
  b6  guard     medium speed, contests depots, carries nothing

WHAT YOU CAN SEE OF RED (fogged, last-known)
  r1  scout     last seen tick 41 near (31,29), heading toward the central band
  r2  scout     unseen since tick 12
  r3  hauler    last seen tick 44 at (40,40), one tile from the RED depot
  r4  hauler    unseen this match
  r5  guard     last seen tick 43 sitting on the RED depot
  r6  guard     unseen since tick 30

RECENT HISTORY (this team's last four rounds, most recent last)
  tick 38  b3 took menu index 1 (move toward (22,22)); reached it, no contact
  tick 40  b5 took menu index 0 (hold depot); RED scout turned away
  tick 42  b1 took menu index 2 (grab crate at (26,24)); succeeded, now carrying
  tick 44  b4 took menu index 1 (move toward (38,10)); in broken terrain now

STANDING GUIDANCE FROM THE COMMANDER
  Deliveries outrank everything. A carrying unit's default is to run its crate
  home unless the run is clearly contested. Do not send a fragile unit into
  open ground within sight of a known enemy scout. Guards hold depots unless a
  delivery is one tick from landing and undefended.
"""


@dataclass(frozen=True)
class Situation:
    """One due unit's own block: what it is, where it is, and what it may do.

    Rendered by :func:`build_prompt` in exactly the shape
    `examples/arch_league.py:describe_text` renders a real briefing, so the
    prompt this probe times is the prompt a B1 sweep would actually send.
    """

    unit_id: str
    role: str
    pos: tuple[int, int]
    carrying: str
    game_time: int
    menu: tuple[str, ...]
    outlook: tuple[str, ...]


#: FIXED and COMMITTED, and eight-wide to match `worker_throughput.PROMPT_SET`'s
#: rotation: cycled across the whole probe so no two concurrent calls in one
#: batch share a per-unit block, and every cell sees the same rotation. The
#: shared preamble above is *deliberately* identical across all eight — that is
#: the repeated context under measurement, not an oversight.
SITUATIONS: tuple[Situation, ...] = (
    Situation(
        unit_id="b1",
        role="scout",
        pos=(26, 24),
        carrying="one crate",
        game_time=45,
        menu=(
            "hold position in the open",
            "run the crate home toward (4,4)",
            "cut to the flank spur at (10,38)",
            "drop the crate and screen for b3",
        ),
        outlook=("RED scout r1 last seen 5 tiles east", "no cover within 3 tiles"),
    ),
    Situation(
        unit_id="b3",
        role="hauler",
        pos=(22, 22),
        carrying="nothing",
        game_time=45,
        menu=(
            "hold position",
            "move to the crate cluster at (28,26)",
            "escort b1 home",
            "withdraw to the depot at (4,4)",
        ),
        outlook=("two crates visible at (28,26)", "b1 is carrying and 4 tiles away"),
    ),
    Situation(
        unit_id="b5",
        role="guard",
        pos=(4, 4),
        carrying="nothing",
        game_time=45,
        menu=(
            "hold the BLUE depot",
            "advance to meet b1's delivery run",
            "push toward the central band",
            "screen the flank spur at (10,38)",
        ),
        outlook=("no enemy within sight", "b1 is carrying, 22 tiles out"),
    ),
    Situation(
        unit_id="b4",
        role="hauler",
        pos=(38, 10),
        carrying="two crates",
        game_time=45,
        menu=(
            "hold in broken terrain",
            "run both crates home the long way",
            "cut across the central band",
            "drop one crate and move faster",
        ),
        outlook=("in broken terrain, unseen", "the direct route crosses open ground"),
    ),
    Situation(
        unit_id="b2",
        role="scout",
        pos=(31, 31),
        carrying="nothing",
        game_time=45,
        menu=(
            "hold position",
            "scout toward the RED depot at (44,44)",
            "grab the crate at (33,29)",
            "fall back to b4 on the flank spur",
        ),
        outlook=("RED guard r5 sits on the RED depot", "one crate visible at (33,29)"),
    ),
    Situation(
        unit_id="b6",
        role="guard",
        pos=(12, 36),
        carrying="nothing",
        game_time=45,
        menu=(
            "hold the flank spur",
            "escort b4 home along the spur",
            "return to the BLUE depot",
            "push into the central band",
        ),
        outlook=("b4 is carrying two crates, 26 tiles out", "no enemy seen on this flank"),
    ),
    Situation(
        unit_id="b1",
        role="scout",
        pos=(18, 16),
        carrying="one crate",
        game_time=47,
        menu=(
            "continue home to (4,4)",
            "hold and wait for b5",
            "divert to the spur at (10,38)",
            "drop the crate and turn back",
        ),
        outlook=("b5 advancing to meet you", "RED scout r1 unseen for 6 ticks"),
    ),
    Situation(
        unit_id="b3",
        role="hauler",
        pos=(28, 26),
        carrying="three crates",
        game_time=47,
        menu=(
            "run all three home the direct way",
            "hold in place",
            "route via the spur at (10,38)",
            "drop one crate and run the rest",
        ),
        outlook=("full load, slowest speed", "the direct route is open ground"),
    ),
)


def _render_menu(menu: Sequence[str]) -> str:
    return "\n".join(f"  {index}. {entry}" for index, entry in enumerate(menu))


def build_prompt(situation: Situation, context: str) -> str:
    """The user message for one scoped call, at one context tier.

    ``lean`` is the unit's own block and the question — the smallest honest
    scoped call. ``rich`` puts the identical :data:`SHARED_PREAMBLE` in front of
    it, which is what a B1 sweep sends when it re-states the match situation on
    every call. Both end in the *same* typed question, so the only thing that
    moved between the two tiers is prompt size.
    """
    if context not in (CONTEXT_LEAN, CONTEXT_RICH):
        raise ValueError(f"unknown context tier {context!r}")
    unit_block = (
        f"UNIT {situation.unit_id} ({situation.role}) of team BLUE, "
        f"at {situation.pos}, carrying {situation.carrying}, at tick "
        f"{situation.game_time}.\n"
        f"MENU (answer with one of these indices):\n{_render_menu(situation.menu)}\n"
        f"OUTLOOK: {json.dumps(list(situation.outlook), sort_keys=True)}\n"
    )
    head = f"{SHARED_PREAMBLE}\n" if context == CONTEXT_RICH else ""
    return f"{head}{unit_block}\n{SCOPED_QUESTION}"


#: The answer shape the harness parses back. A scoped call that does not
#: produce one is an UNANSWERED call — recorded, never quietly counted as ok.
#: This is the #32 failure shape (a worker handed a narrow contract that writes
#: something else) measured as a rate rather than assumed absent.
_MENU_INDEX = re.compile(r"menu_index\s*=\s*(\d+)")


def parse_menu_index(content: Optional[str]) -> Optional[int]:
    """The index a scoped answer carries, or ``None`` if it carries none."""
    if not content:
        return None
    match = _MENU_INDEX.search(content)
    return int(match.group(1)) if match else None


# ── the committed cell table ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Cell:
    """One measured condition. The table below IS the design of this probe."""

    id: str
    budget: str  # "scoped" | "natural"
    thinking: str  # "on" | "off"
    context: str  # "lean" | "rich"
    width: int
    max_tokens: int
    batches: int
    why: str

    @property
    def calls(self) -> int:
        return self.width * self.batches

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "budget": self.budget,
            "thinking": self.thinking,
            "context": self.context,
            "width": self.width,
            "max_tokens": self.max_tokens,
            "batches": self.batches,
            "calls": self.calls,
            "why": self.why,
        }


#: FIXED and COMMITTED. Six scoped cells (the B1 regime: 2 thinking modes x
#: 2 context tiers x widths 1/8, thinking-off crossed fully and thinking-on
#: carried at the rich tier only) plus two natural-budget cells that answer
#: "what does a scoped question cost when you let it finish". Width 1 runs more
#: batches because it is cheap and is the baseline every width-8 number is
#: measured against; width 8 runs two batches so batch-to-batch drift is visible
#: rather than a single sample.
CELLS: tuple[Cell, ...] = (
    Cell(
        id="lean-off-w1",
        budget="scoped",
        thinking="off",
        context=CONTEXT_LEAN,
        width=1,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=4,
        why="the floor: smallest prompt, smallest completion, one stream",
    ),
    Cell(
        id="rich-off-w1",
        budget="scoped",
        thinking="off",
        context=CONTEXT_RICH,
        width=1,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=4,
        why="the prefill axis at width 1: same call, realistic repeated context",
    ),
    Cell(
        id="lean-off-w8",
        budget="scoped",
        thinking="off",
        context=CONTEXT_LEAN,
        width=8,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=2,
        why="does the concurrency figure transfer to tiny calls at all",
    ),
    Cell(
        id="rich-off-w8",
        budget="scoped",
        thinking="off",
        context=CONTEXT_RICH,
        width=8,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=2,
        why="the cell the B1 sweep would actually run",
    ),
    Cell(
        id="rich-on-w1",
        budget="scoped",
        thinking="on",
        context=CONTEXT_RICH,
        width=1,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=4,
        why="what a tens-of-tokens cap costs a thinking model that was not suppressed",
    ),
    Cell(
        id="rich-on-w8",
        budget="scoped",
        thinking="on",
        context=CONTEXT_RICH,
        width=8,
        max_tokens=SCOPED_MAX_TOKENS,
        batches=2,
        why="the same, concurrent — a sweep that forgets the toggle runs this cell",
    ),
    Cell(
        id="rich-off-natural-w1",
        budget="natural",
        thinking="off",
        context=CONTEXT_RICH,
        width=1,
        max_tokens=NATURAL_MAX_TOKENS,
        batches=4,
        why="does a suppressed-thinking scoped answer actually FIT in tens of tokens",
    ),
    Cell(
        id="rich-on-natural-w1",
        budget="natural",
        thinking="on",
        context=CONTEXT_RICH,
        width=1,
        max_tokens=NATURAL_MAX_TOKENS,
        batches=4,
        why="what one scoped question really costs a thinking model, uncapped",
    ),
)

#: A batch-level circuit breaker, inherited from `worker_throughput.py`. If it
#: fires that is itself a finding — a *tiny* call taking longer than a
#: 1200-token one ever did — and it is reported as a timeout, never absorbed by
#: waiting longer.
#:
#: **Derived, never chosen** — issue #42's rule applied to a wait deadline.
#: ``bound = max over every dialled width of max_tokens / that width's rate +
#: queue allowance``, rates from
#: ``docs/live-test-results/timeout-rate-measurements.json`` and recomputed by
#: `tests/test_timeout_bounds.py`. The only model on this wire is the
#: **worker**; the turn budget is one, because a batch's wall clock is its
#: slowest call rather than the sum. The binding pair is
#: :data:`NATURAL_MAX_TOKENS` at width 8: 2000 / 38.678 tok/s = 51.7 s of
#: generation plus the 179.3 s measured **queue** allowance (corrections.md §9)
#: = 231.0 s. Shipped 300.0 is 1.30x. Unchanged.
#:
#: This constant was in neither #42's audit table nor plan task t2's list of
#: seven: it landed with this probe, after the audit was written, by inheriting
#: a sibling's value. The AST completeness guard in the bound test found it.
#: An underived clock in front of a model call is precisely what that guard
#: exists to catch, and this is the first one it caught.
BATCH_WAIT_TIMEOUT_SECONDS = 300.0

THREAD_NAME_PREFIX = "scoped-overhead"

#: league's own word for a completion that ran out of budget mid-thought.
FINISH_TRUNCATED = "length"


# ── transport: WorkerSeam plus the wire keys and the raw payloads ────────────


class ScopedSeam(WorkerSeam):
    """`WorkerSeam`, subclassed for extra wire keys and raw request/response.

    Everything else — endpoint construction, the bounded retries, ``self.meter``
    — is inherited unchanged. Two additions, both needed by this task and
    neither belonging in `worker_seam.py`:

    * ``wire_extra`` merges the thinking-mode keys (:data:`THINKING_WIRE`) into
      the body. Merged inside :meth:`_post` so it rides the *existing* retry
      path rather than a parallel one, and so a test can assert exactly what
      went on the wire (:attr:`last_body`) instead of trusting a branch.
    * ``last_payload`` keeps the raw response, matching
      `worker_throughput.ThroughputSeam`'s reason for the same field.
    """

    def __init__(self, *args: Any, wire_extra: Optional[Mapping[str, Any]] = None, **kw: Any):
        super().__init__(*args, **kw)
        self.wire_extra: dict[str, Any] = dict(wire_extra or {})
        self.last_body: Optional[dict[str, Any]] = None
        self.last_payload: Optional[dict[str, Any]] = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        merged = {**body, **self.wire_extra}
        self.last_body = merged
        payload = super()._post(merged)
        self.last_payload = payload
        return payload


# ── one call ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallSpec:
    """Which cell/batch/slot this call is, and which situation it asks about."""

    cell: str
    width: int
    batch: int
    slot: int
    situation_id: int
    context: str
    thinking: str
    max_tokens: int
    warmup: bool = False


@dataclass(frozen=True)
class CallRecord:
    """One call's whole outcome — the raw, per-call artifact this task commits."""

    cell: str
    width: int
    batch: int
    slot: int
    situation_id: int
    context: str
    thinking: str
    max_tokens: int
    warmup: bool
    ok: bool
    error: Optional[str]
    latency_seconds: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    content_chars: Optional[int]
    reasoning_chars: Optional[int]
    finish_reason: Optional[str]
    truncated: bool
    retries: int
    tokens_per_second: Optional[float]
    menu_index: Optional[int]
    answered: bool
    modelled_decode_seconds: Optional[float]
    residual_seconds: Optional[float]
    residual_fraction: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "width": self.width,
            "batch": self.batch,
            "slot": self.slot,
            "situation_id": self.situation_id,
            "context": self.context,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "warmup": self.warmup,
            "ok": self.ok,
            "error": self.error,
            "latency_seconds": _round(self.latency_seconds),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "content_chars": self.content_chars,
            "reasoning_chars": self.reasoning_chars,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "retries": self.retries,
            "tokens_per_second": _round(self.tokens_per_second),
            "menu_index": self.menu_index,
            "answered": self.answered,
            "modelled_decode_seconds": _round(self.modelled_decode_seconds),
            "residual_seconds": _round(self.residual_seconds),
            "residual_fraction": _round(self.residual_fraction),
        }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def residual_seconds(
    latency: Optional[float],
    completion_tokens: Optional[int],
    rate_tok_s: float = REFERENCE_DECODE_TOK_S,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """``(modelled_decode, residual, residual_fraction)`` for one call.

    ``modelled_decode = completion_tokens / rate_tok_s`` — how long this call's
    output *would* take to decode at the committed single-stream reference rate
    (`worker-throughput.md` width 1: 76.43 tok/s). The **residual** is
    everything else in the call's wall clock: request framing, network,
    queueing, prefill of the prompt, and — at width > 1 — contention with the
    other streams in its own batch.

    Three things this deliberately does NOT do, each of which would make the
    number look more precise than it is:

    * It does not re-derive the rate per cell. The reference is a *fixed,
      committed* constant so residuals are comparable across cells; a
      per-cell rate would silently absorb the very overhead being measured.
    * It does not separate prefill from queueing. This harness sends
      non-streaming requests, so there is no time-to-first-token to read —
      the same stated limit `worker_throughput.py` carries. The width-1
      cells are where the residual reads most cleanly as per-call overhead,
      and the lean-vs-rich *difference* at width 1 is the closest this probe
      gets to isolating prefill.
    * It does not clamp at zero. A call that decoded faster than the reference
      rate yields a negative residual, and that is reported, because silently
      flooring it would turn "the reference rate is wrong for this regime"
      into "there is no overhead".

    Returns ``(None, None, None)`` when either input is missing — a failed call
    has no residual, and inventing one would put a fabricated row in a cost
    table.
    """
    if latency is None or completion_tokens is None or rate_tok_s <= 0:
        return None, None, None
    modelled = completion_tokens / rate_tok_s
    residual = latency - modelled
    fraction = residual / latency if latency > 0 else None
    return modelled, residual, fraction


def _failed_record(spec: CallSpec, *, error: str, elapsed: Optional[float], retries: int = 0):
    return CallRecord(
        cell=spec.cell,
        width=spec.width,
        batch=spec.batch,
        slot=spec.slot,
        situation_id=spec.situation_id,
        context=spec.context,
        thinking=spec.thinking,
        max_tokens=spec.max_tokens,
        warmup=spec.warmup,
        ok=False,
        error=error,
        latency_seconds=elapsed,
        prompt_tokens=None,
        completion_tokens=None,
        content_chars=None,
        reasoning_chars=None,
        finish_reason=None,
        truncated=False,
        retries=retries,
        tokens_per_second=None,
        menu_index=None,
        answered=False,
        modelled_decode_seconds=None,
        residual_seconds=None,
        residual_fraction=None,
    )


def _one_call(
    config: WorkerConfig,
    spec: CallSpec,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    sleep: Callable[[float], None] = time.sleep,
) -> CallRecord:
    """One scoped completion, fully accounted. **This never raises.**

    A thread that raises leaves its `Future` holding an exception the collecting
    side must re-raise or swallow, and swallowing is what C3 forbids — so every
    fault becomes a `CallRecord` here, on the thread that saw it. Matches
    `worker_throughput._one_call` exactly in that discipline.
    """
    if spec.thinking not in THINKING_WIRE:
        return _failed_record(spec, error=f"unknown thinking mode {spec.thinking!r}", elapsed=None)

    seam = ScopedSeam(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        role=f"scoped-{spec.cell}",
        max_tokens=spec.max_tokens,
        temperature=temperature,
        wire_extra=THINKING_WIRE[spec.thinking],
        sleep=sleep,
    )
    situation = SITUATIONS[spec.situation_id % len(SITUATIONS)]
    messages = [
        {"role": "system", "content": SCOPED_SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt(situation, spec.context)},
    ]
    started = time.monotonic()
    try:
        reply = seam(messages)
    except Exception as exc:  # noqa: BLE001 -- a failed call is DATA, never a raise from a thread
        return _failed_record(
            spec,
            error=f"{type(exc).__name__}: {exc}",
            elapsed=time.monotonic() - started,
            retries=seam.meter.retries,
        )

    elapsed = time.monotonic() - started
    turn = seam.meter.transcript[0]
    modelled, residual, fraction = residual_seconds(elapsed, reply.completion_tokens)
    index = parse_menu_index(reply.content)
    tps = reply.completion_tokens / elapsed if elapsed > 0 and reply.completion_tokens else None
    return CallRecord(
        cell=spec.cell,
        width=spec.width,
        batch=spec.batch,
        slot=spec.slot,
        situation_id=spec.situation_id,
        context=spec.context,
        thinking=spec.thinking,
        max_tokens=spec.max_tokens,
        warmup=spec.warmup,
        ok=True,
        error=None,
        latency_seconds=elapsed,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        content_chars=len(reply.content or ""),
        reasoning_chars=len(reply.reasoning or ""),
        finish_reason=turn["finish_reason"],
        truncated=turn["finish_reason"] == FINISH_TRUNCATED,
        retries=seam.meter.retries,
        tokens_per_second=tps,
        menu_index=index,
        answered=index is not None,
        modelled_decode_seconds=modelled,
        residual_seconds=residual,
        residual_fraction=fraction,
    )


CallFn = Callable[[WorkerConfig, CallSpec], CallRecord]


# ── one batch ────────────────────────────────────────────────────────────────


def _run_batch(
    config: WorkerConfig,
    specs: Sequence[CallSpec],
    *,
    call_fn: CallFn,
    timeout: float = BATCH_WAIT_TIMEOUT_SECONDS,
) -> tuple[list[CallRecord], float]:
    """Dispatch every spec concurrently; return ``(records, batch_wall_seconds)``.

    Termination by construction, matching `worker_throughput._run_batch`: no
    ``while`` anywhere in this module, one bounded
    :func:`concurrent.futures.wait`, ``shutdown(wait=False,
    cancel_futures=True)`` in a ``finally``, and a future that misses the
    deadline is recorded as a timeout and never read again. Records come back in
    *submission* order — a report whose shape depends on the scheduler is not a
    report.
    """
    if not specs:
        return [], 0.0

    pool = ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix=THREAD_NAME_PREFIX)
    pending: dict[Future[CallRecord], CallSpec] = {}
    started = time.monotonic()
    try:
        for spec in specs:
            pending[pool.submit(call_fn, config, spec)] = spec
        _done, absent = wait(list(pending), timeout=timeout)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - started

    records: list[CallRecord] = []
    for future, spec in pending.items():
        if future in absent:
            records.append(_failed_record(spec, error="timeout", elapsed=timeout))
        else:
            records.append(future.result(timeout=0))
    return records, elapsed


def _build_specs(cell: Cell, batch: int, cursor: "itertools.count[int]") -> list[CallSpec]:
    return [
        CallSpec(
            cell=cell.id,
            width=cell.width,
            batch=batch,
            slot=slot,
            situation_id=next(cursor) % len(SITUATIONS),
            context=cell.context,
            thinking=cell.thinking,
            max_tokens=cell.max_tokens,
        )
        for slot in range(cell.width)
    ]


# ── per-cell aggregation ─────────────────────────────────────────────────────


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy's default method). Stdlib-only."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    return ordered[lo] * (hi - rank) + ordered[hi] * (rank - lo)


def _mean(values: Sequence[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


@dataclass
class CellSummary:
    """Everything one cell measured. Per-stream and aggregate never conflated."""

    cell: str
    budget: str
    thinking: str
    context: str
    width: int
    max_tokens: int
    batches: int
    calls: int
    ok: int
    errors: int
    timeouts: int
    truncated: int
    answered: int
    finish_reasons: dict[str, int]
    prompt_tokens_mean: Optional[float]
    prompt_tokens_median: Optional[float]
    prompt_tokens_total: int
    completion_tokens_mean: Optional[float]
    completion_tokens_median: Optional[float]
    completion_tokens_total: int
    latency_seconds_mean: Optional[float]
    latency_seconds_median: Optional[float]
    latency_seconds_p95: Optional[float]
    per_stream_tokens_per_second_mean: Optional[float]
    aggregate_seconds: float
    aggregate_tokens_per_second: Optional[float]
    effective_concurrency: Optional[float]
    seconds_per_call_wallclock: Optional[float]
    calls_per_second: Optional[float]
    residual_seconds_mean: Optional[float]
    residual_seconds_median: Optional[float]
    residual_fraction_mean: Optional[float]
    residual_fraction_median: Optional[float]
    content_chars_total: int
    reasoning_chars_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "budget": self.budget,
            "thinking": self.thinking,
            "context": self.context,
            "width": self.width,
            "max_tokens": self.max_tokens,
            "batches": self.batches,
            "calls": self.calls,
            "ok": self.ok,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "truncated": self.truncated,
            "answered": self.answered,
            "finish_reasons": dict(self.finish_reasons),
            "prompt_tokens_mean": _round(self.prompt_tokens_mean),
            "prompt_tokens_median": _round(self.prompt_tokens_median),
            "prompt_tokens_total": self.prompt_tokens_total,
            "completion_tokens_mean": _round(self.completion_tokens_mean),
            "completion_tokens_median": _round(self.completion_tokens_median),
            "completion_tokens_total": self.completion_tokens_total,
            "latency_seconds_mean": _round(self.latency_seconds_mean),
            "latency_seconds_median": _round(self.latency_seconds_median),
            "latency_seconds_p95": _round(self.latency_seconds_p95),
            "per_stream_tokens_per_second_mean": _round(self.per_stream_tokens_per_second_mean),
            "aggregate_seconds": round(self.aggregate_seconds, 4),
            "aggregate_tokens_per_second": _round(self.aggregate_tokens_per_second),
            "effective_concurrency": _round(self.effective_concurrency),
            "seconds_per_call_wallclock": _round(self.seconds_per_call_wallclock),
            "calls_per_second": _round(self.calls_per_second),
            "residual_seconds_mean": _round(self.residual_seconds_mean),
            "residual_seconds_median": _round(self.residual_seconds_median),
            "residual_fraction_mean": _round(self.residual_fraction_mean),
            "residual_fraction_median": _round(self.residual_fraction_median),
            "content_chars_total": self.content_chars_total,
            "reasoning_chars_total": self.reasoning_chars_total,
        }


@dataclass
class CellRun:
    """One cell's records plus each batch's own wall clock."""

    cell: Cell
    records: list[CallRecord] = field(default_factory=list)
    batch_elapsed_seconds: list[float] = field(default_factory=list)

    def summary(self) -> CellSummary:
        ok = [r for r in self.records if r.ok]
        errors = [r for r in self.records if not r.ok]
        finish_reasons: dict[str, int] = {}
        for record in ok:
            key = record.finish_reason or "unknown"
            finish_reasons[key] = finish_reasons.get(key, 0) + 1

        prompts = [float(r.prompt_tokens) for r in ok if r.prompt_tokens is not None]
        completions = [float(r.completion_tokens) for r in ok if r.completion_tokens is not None]
        latencies = [r.latency_seconds for r in ok if r.latency_seconds is not None]
        per_stream = [r.tokens_per_second for r in ok if r.tokens_per_second is not None]
        residuals = [r.residual_seconds for r in ok if r.residual_seconds is not None]
        fractions = [r.residual_fraction for r in ok if r.residual_fraction is not None]

        aggregate_seconds = sum(self.batch_elapsed_seconds)
        completion_total = int(sum(completions))
        per_stream_mean = _mean(per_stream)
        aggregate_tps = completion_total / aggregate_seconds if aggregate_seconds > 0 else None
        effective = (
            aggregate_tps / per_stream_mean
            if aggregate_tps is not None and per_stream_mean
            else None
        )
        # Wall clock actually *spent per call*, which is what a sweep budgets
        # against — a width-8 batch that takes 3s delivers 8 calls, so its
        # per-call cost is 0.375s even though each call's own latency was 3s.
        # These two numbers are different on purpose and both are reported.
        seconds_per_call = aggregate_seconds / len(self.records) if self.records else None
        calls_per_second = len(self.records) / aggregate_seconds if aggregate_seconds > 0 else None

        return CellSummary(
            cell=self.cell.id,
            budget=self.cell.budget,
            thinking=self.cell.thinking,
            context=self.cell.context,
            width=self.cell.width,
            max_tokens=self.cell.max_tokens,
            batches=self.cell.batches,
            calls=len(self.records),
            ok=len(ok),
            errors=len(errors),
            timeouts=sum(1 for r in errors if r.error == "timeout"),
            truncated=sum(1 for r in ok if r.truncated),
            answered=sum(1 for r in ok if r.answered),
            finish_reasons=finish_reasons,
            prompt_tokens_mean=_mean(prompts),
            prompt_tokens_median=_median(prompts),
            prompt_tokens_total=int(sum(prompts)),
            completion_tokens_mean=_mean(completions),
            completion_tokens_median=_median(completions),
            completion_tokens_total=completion_total,
            latency_seconds_mean=_mean(latencies),
            latency_seconds_median=_median(latencies),
            latency_seconds_p95=_percentile(latencies, 95) if latencies else None,
            per_stream_tokens_per_second_mean=per_stream_mean,
            aggregate_seconds=aggregate_seconds,
            aggregate_tokens_per_second=aggregate_tps,
            effective_concurrency=effective,
            seconds_per_call_wallclock=seconds_per_call,
            calls_per_second=calls_per_second,
            residual_seconds_mean=_mean(residuals),
            residual_seconds_median=_median(residuals),
            residual_fraction_mean=_mean(fractions),
            residual_fraction_median=_median(fractions),
            content_chars_total=sum(r.content_chars or 0 for r in ok),
            reasoning_chars_total=sum(r.reasoning_chars or 0 for r in ok),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.cell.to_dict(),
            "batch_elapsed_seconds": [round(s, 4) for s in self.batch_elapsed_seconds],
            "summary": self.summary().to_dict(),
        }


# ── transport retries, and the batches they contaminate ─────────────────────

#: `worker_seam.RETRY_SLEEP_SECONDS`, restated here as the constant this module
#: reports against rather than re-read, so the ratio below cannot silently
#: change meaning if the sibling constant moves without this document's
#: knowledge. Asserted equal to the live value by the hermetic suite.
RETRY_BACKOFF_SECONDS = 20.0


def retry_batches(run: "CellRun") -> tuple[int, ...]:
    """Batch indices in ``run`` containing at least one call that was retried.

    A retried call carries `worker_seam.RETRY_SLEEP_SECONDS` of pure ``sleep``
    inside its own latency. On a 1200-token completion that backoff is a
    fraction of the call; on a *tens-of-token* call it is **an order of
    magnitude larger than the call itself**, so a single retry does not perturb
    a batch's wall clock — it *replaces* it. Any aggregate computed over such a
    batch is measuring the backoff constant, not the rig.
    """
    return tuple(sorted({record.batch for record in run.records if record.retries}))


def without_retry_batches(run: "CellRun") -> "CellRun":
    """``run`` with every retry-containing batch dropped, whole.

    Whole batches, not individual calls: the retried call's *neighbours* shared
    a batch wall clock with it, so keeping them while dropping it would compare
    a batch's tokens against a clock that included a 20s sleep. Dropping the
    batch keeps records and their own wall clock consistent.

    This is a **second reading beside** the as-measured one, never a
    replacement — the results document publishes both, because a transport
    failure on a shared rig is a real property of that rig and deleting it
    would be the kind of quiet cleaning this repo's C3 exists to prevent.
    """
    contaminated = set(retry_batches(run))
    clean = CellRun(cell=run.cell)
    clean.records = [record for record in run.records if record.batch not in contaminated]
    clean.batch_elapsed_seconds = [
        elapsed
        for index, elapsed in enumerate(run.batch_elapsed_seconds)
        if index not in contaminated
    ]
    return clean


def transport_summary(records: Sequence[CallRecord]) -> dict[str, Any]:
    """The rig's transport behaviour over a whole probe, stated as a rate.

    Reported because the *ratio* is the finding: a fixed retry backoff sized for
    long completions is grossly mis-sized for scoped calls, which is the same
    class of defect this plan's timeout work exists to fix — a constant chosen
    for one budget silently governing another.
    """
    retried = [record for record in records if record.retries]
    healthy = [
        record.latency_seconds
        for record in records
        if record.ok and not record.retries and record.latency_seconds is not None
    ]
    median_healthy = _median(healthy)
    return {
        "calls": len(records),
        "calls_with_retries": len(retried),
        "retry_rate": _round(len(retried) / len(records)) if records else None,
        "retries_total": sum(record.retries for record in records),
        "failed_calls": sum(1 for record in records if not record.ok),
        "retry_backoff_seconds": RETRY_BACKOFF_SECONDS,
        "median_healthy_latency_seconds": _round(median_healthy),
        "backoff_over_median_healthy_latency": (
            _round(RETRY_BACKOFF_SECONDS / median_healthy) if median_healthy else None
        ),
        "retried_calls": [
            {
                "cell": record.cell,
                "batch": record.batch,
                "slot": record.slot,
                "latency_seconds": _round(record.latency_seconds),
                "retries": record.retries,
                "ok": record.ok,
            }
            for record in retried
        ],
    }


# ── the two derived answers the B1 sweep needs ───────────────────────────────


def concurrency_transfer(
    width1: Optional[CellSummary],
    width8: Optional[CellSummary],
    reference: float = REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY,
) -> dict[str, Any]:
    """Does `worker-throughput.md`'s width-8 concurrency figure transfer to tiny calls?

    Reported two ways, because ``effective_concurrency`` is defined on *token*
    throughput and a tens-of-token call barely has any:

    * ``effective_concurrency`` — the same ratio the throughput series
      published, recomputed here. Directly comparable to its 6.14, and it is
      the number ``c43`` questions.
    * ``call_throughput_speedup`` — calls/second at width 8 divided by
      calls/second at width 1. For scoped calls this is the figure a sweep
      actually budgets against: a B1 sweep buys *answers*, not tokens, and if
      per-call overhead dominates then the token ratio and the answer ratio
      come apart. Reporting only one would let the claim pick its own metric.
    """
    if width1 is None or width8 is None:
        return {"measured": False, "why": "a required cell is absent"}
    speedup = (
        width8.calls_per_second / width1.calls_per_second
        if width8.calls_per_second and width1.calls_per_second
        else None
    )
    measured = width8.effective_concurrency
    return {
        "measured": True,
        "width1_cell": width1.cell,
        "width8_cell": width8.cell,
        "reference_effective_concurrency_1200_token_calls": reference,
        "measured_effective_concurrency": _round(measured),
        "effective_concurrency_transfer_ratio": (
            _round(measured / reference) if measured is not None and reference else None
        ),
        "width1_calls_per_second": _round(width1.calls_per_second),
        "width8_calls_per_second": _round(width8.calls_per_second),
        "call_throughput_speedup": _round(speedup),
        "ideal_speedup": float(width8.width),
        "call_throughput_efficiency": (
            _round(speedup / width8.width) if speedup is not None and width8.width else None
        ),
    }


def prefill_cost(lean: Optional[CellSummary], rich: Optional[CellSummary]) -> dict[str, Any]:
    """What the extra prompt tokens of realistic repeated context actually cost.

    ``c43``'s question in one row: two cells identical in every way except the
    size of the context in front of the same typed question. The *difference* in
    latency divided by the *difference* in prompt tokens is the closest this
    non-streaming harness can get to a prefill rate, and it is labelled
    ``implied_`` throughout because it is a difference of two totals, not a
    measured time-to-first-token.

    ``throughput_ratio`` is the number a granularity choice actually needs: how
    much answer-throughput the richer context costs at this width.
    """
    if lean is None or rich is None:
        return {"measured": False, "why": "a required cell is absent"}
    extra_prompt = (rich.prompt_tokens_mean or 0) - (lean.prompt_tokens_mean or 0)
    extra_latency = (rich.latency_seconds_mean or 0) - (lean.latency_seconds_mean or 0)
    return {
        "measured": True,
        "lean_cell": lean.cell,
        "rich_cell": rich.cell,
        "width": rich.width,
        "lean_prompt_tokens_mean": _round(lean.prompt_tokens_mean),
        "rich_prompt_tokens_mean": _round(rich.prompt_tokens_mean),
        "extra_prompt_tokens": _round(extra_prompt),
        "lean_latency_seconds_mean": _round(lean.latency_seconds_mean),
        "rich_latency_seconds_mean": _round(rich.latency_seconds_mean),
        "extra_latency_seconds": _round(extra_latency),
        "implied_prefill_tokens_per_second": (
            _round(extra_prompt / extra_latency) if extra_latency > 0 else None
        ),
        "lean_calls_per_second": _round(lean.calls_per_second),
        "rich_calls_per_second": _round(rich.calls_per_second),
        "throughput_ratio_rich_over_lean": (
            _round(rich.calls_per_second / lean.calls_per_second)
            if rich.calls_per_second and lean.calls_per_second
            else None
        ),
    }


def thinking_cost(off: Optional[CellSummary], on: Optional[CellSummary]) -> dict[str, Any]:
    """What leaving the thinking toggle alone costs a scoped call.

    The B1 tier's whole cost model is "a scoped worker call spends tens [of
    tokens]". Whether that is even *reachable* on a thinking worker is a
    property of the wire keys, not of the question — so it is measured, and the
    multiple is reported rather than described.
    """
    if off is None or on is None:
        return {"measured": False, "why": "a required cell is absent"}
    off_tokens, on_tokens = off.completion_tokens_mean or 0, on.completion_tokens_mean or 0
    off_latency, on_latency = off.latency_seconds_mean or 0, on.latency_seconds_mean or 0
    return {
        "measured": True,
        "thinking_off_cell": off.cell,
        "thinking_on_cell": on.cell,
        "max_tokens": off.max_tokens,
        "off_completion_tokens_mean": _round(off_tokens),
        "on_completion_tokens_mean": _round(on_tokens),
        "completion_token_multiple": _round(on_tokens / off_tokens) if off_tokens else None,
        "off_latency_seconds_mean": _round(off_latency),
        "on_latency_seconds_mean": _round(on_latency),
        "latency_multiple": _round(on_latency / off_latency) if off_latency else None,
        "off_answered": f"{off.answered}/{off.ok}",
        "on_answered": f"{on.answered}/{on.ok}",
        "off_truncated": off.truncated,
        "on_truncated": on.truncated,
    }


def scoped_calls_per_cortex_turn(width8: Optional[CellSummary]) -> dict[str, Any]:
    """How many scoped calls at width 8 fit in the wall clock of one cortex turn.

    Both ends of the cortex-turn range are reported (:data:`CORTEX_TURN_SECONDS_LOW`
    / ``_HIGH``, from the spec's own measured figures) rather than a midpoint,
    and the token budget is reported beside the wall clock because the two
    answer different questions: wall clock says how many scoped calls fit in the
    *time*, tokens say how many fit in the *spend*. A granularity choice that
    cites only one of them is citing the flattering one.
    """
    if width8 is None or not width8.seconds_per_call_wallclock:
        return {"measured": False, "why": "the width-8 cell is absent or has no wall clock"}
    per_call = width8.seconds_per_call_wallclock
    tokens_per_call = width8.completion_tokens_mean or 0.0
    prompt_per_call = width8.prompt_tokens_mean or 0.0
    return {
        "measured": True,
        "cell": width8.cell,
        "seconds_per_scoped_call_at_width8": _round(per_call),
        "completion_tokens_per_scoped_call": _round(tokens_per_call),
        "prompt_tokens_per_scoped_call": _round(prompt_per_call),
        "cortex_turn_seconds_range": [CORTEX_TURN_SECONDS_LOW, CORTEX_TURN_SECONDS_HIGH],
        "cortex_turn_completion_tokens_range": [CORTEX_TURN_TOKENS_LOW, CORTEX_TURN_TOKENS_HIGH],
        "scoped_calls_per_cortex_turn_wallclock_low": _round(CORTEX_TURN_SECONDS_LOW / per_call),
        "scoped_calls_per_cortex_turn_wallclock_high": _round(CORTEX_TURN_SECONDS_HIGH / per_call),
        "scoped_calls_per_cortex_turn_completion_tokens_low": (
            _round(CORTEX_TURN_TOKENS_LOW / tokens_per_call) if tokens_per_call else None
        ),
        "scoped_calls_per_cortex_turn_completion_tokens_high": (
            _round(CORTEX_TURN_TOKENS_HIGH / tokens_per_call) if tokens_per_call else None
        ),
        "note": (
            "prompt tokens are NOT charged against the cortex-turn completion-token "
            "range: the two are different budgets on different boxes. The prompt "
            "figure is reported so a granularity choice can price prefill itself."
        ),
    }


# ── the probe ────────────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    config: WorkerConfig
    temperature: float
    warmup: Optional[CallRecord]
    cells: list[CellRun]
    window: dict[str, Any] = field(default_factory=dict)

    def all_records(self) -> list[CallRecord]:
        records: list[CallRecord] = [] if self.warmup is None else [self.warmup]
        for run in self.cells:
            records.extend(run.records)
        return records

    def summary_for(self, cell_id: str) -> Optional[CellSummary]:
        for run in self.cells:
            if run.cell.id == cell_id:
                return run.summary()
        return None

    def clean_summary_for(self, cell_id: str) -> Optional[CellSummary]:
        """The same cell with retry-contaminated batches dropped. See
        :func:`without_retry_batches` for why whole batches go."""
        for run in self.cells:
            if run.cell.id == cell_id:
                return without_retry_batches(run).summary()
        return None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "kind": "worker-scoped-overhead",
            "worker": self.config.to_dict(),
            "temperature": self.temperature,
            "reference": {
                "source": "docs/live-test-results/worker-throughput.md",
                "decode_tok_s_width1": REFERENCE_DECODE_TOK_S,
                "aggregate_tok_s_width8": REFERENCE_WIDTH8_AGGREGATE_TOK_S,
                "effective_concurrency_width8": REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY,
                "effective_concurrency_width14": REFERENCE_WIDTH14_EFFECTIVE_CONCURRENCY,
                "measured_on_completion_tokens": 1200,
            },
            "situations": len(SITUATIONS),
            "window": self.window,
            "warmup": self.warmup.to_dict() if self.warmup else None,
            "cells": [run.to_dict() for run in self.cells],
            "transport": transport_summary(self.all_records()),
            "retry_contaminated_batches": {
                run.cell.id: list(retry_batches(run)) for run in self.cells if retry_batches(run)
            },
            "cells_excluding_retry_batches": [
                without_retry_batches(run).to_dict() for run in self.cells if retry_batches(run)
            ],
            "concurrency_transfer": {
                "lean": concurrency_transfer(
                    self.summary_for("lean-off-w1"), self.summary_for("lean-off-w8")
                ),
                "rich": concurrency_transfer(
                    self.summary_for("rich-off-w1"), self.summary_for("rich-off-w8")
                ),
            },
            "concurrency_transfer_excluding_retry_batches": {
                "lean": concurrency_transfer(
                    self.clean_summary_for("lean-off-w1"), self.clean_summary_for("lean-off-w8")
                ),
                "rich": concurrency_transfer(
                    self.clean_summary_for("rich-off-w1"), self.clean_summary_for("rich-off-w8")
                ),
            },
            "prefill_cost": {
                "width1": prefill_cost(
                    self.clean_summary_for("lean-off-w1"), self.clean_summary_for("rich-off-w1")
                ),
                "width8": prefill_cost(
                    self.clean_summary_for("lean-off-w8"), self.clean_summary_for("rich-off-w8")
                ),
            },
            "thinking_cost": {
                "scoped_budget": thinking_cost(
                    self.summary_for("rich-off-w1"), self.summary_for("rich-on-w1")
                ),
                "natural_budget": thinking_cost(
                    self.summary_for("rich-off-natural-w1"),
                    self.summary_for("rich-on-natural-w1"),
                ),
            },
            "scoped_calls_per_cortex_turn": scoped_calls_per_cortex_turn(
                self.summary_for("rich-off-w8")
            ),
        }


def run_probe(
    config: WorkerConfig,
    *,
    cells: Sequence[Cell] = CELLS,
    temperature: float = DEFAULT_TEMPERATURE,
    warmup: bool = True,
    call_fn: Optional[CallFn] = None,
    batch_timeout: float = BATCH_WAIT_TIMEOUT_SECONDS,
    window: Optional[dict[str, Any]] = None,
) -> ProbeResult:
    """Run every cell, sequentially, batch by batch.

    Cells run one at a time on purpose: two cells in flight would contend with
    each other and every width-1 number would silently become a width-N number.
    ``call_fn`` defaults to :func:`_one_call`; tests inject a fake to exercise
    scheduling and aggregation without a socket.
    """
    resolved: CallFn = call_fn or functools.partial(_one_call, temperature=temperature)
    cursor = itertools.count()

    warmup_record: Optional[CallRecord] = None
    if warmup:
        warmup_record = resolved(
            config,
            CallSpec(
                cell="warmup",
                width=0,
                batch=-1,
                slot=0,
                situation_id=0,
                context=CONTEXT_LEAN,
                thinking="off",
                max_tokens=SCOPED_MAX_TOKENS,
                warmup=True,
            ),
        )

    runs: list[CellRun] = []
    for cell in cells:
        run = CellRun(cell=cell)
        for batch_index in range(cell.batches):
            specs = _build_specs(cell, batch_index, cursor)
            records, elapsed = _run_batch(config, specs, call_fn=resolved, timeout=batch_timeout)
            run.records.extend(records)
            run.batch_elapsed_seconds.append(elapsed)
        runs.append(run)

    return ProbeResult(
        config=config,
        temperature=temperature,
        warmup=warmup_record,
        cells=runs,
        window=dict(window or {}),
    )


# ── rebuilding a result from committed artifacts, with no dial ───────────────


def rebuild_from_artifacts(records_path: Path, summary_path: Path) -> ProbeResult:
    """Reconstruct a :class:`ProbeResult` from a committed run. **Dials nothing.**

    Why this exists: the results document's tables must be *derivable from the
    committed artifacts*, not only from a live run nobody can repeat. With this,
    anyone can regenerate every table from the two files in git and check the
    published numbers — the t12 discipline that a typed table is a table nobody
    can re-derive, taken one step further to "and the generator does not need
    the rig".

    The per-call records come from the JSONL; the per-batch wall clocks and the
    cell specs come from the summary JSON, because a per-call record cannot
    carry its batch's own clock without lying about which call owned it.
    """
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    raw_records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    fields = set(CallRecord.__dataclass_fields__)
    by_cell: dict[str, list[CallRecord]] = {}
    warmup: Optional[CallRecord] = None
    for raw in raw_records:
        record = CallRecord(**{key: value for key, value in raw.items() if key in fields})
        if record.warmup:
            warmup = record
            continue
        by_cell.setdefault(record.cell, []).append(record)

    runs: list[CellRun] = []
    for entry in summary.get("cells", []):
        spec = dict(entry["spec"])
        spec.pop("calls", None)  # derived property, not a constructor argument
        cell = Cell(**spec)
        run = CellRun(cell=cell)
        run.records = by_cell.pop(cell.id, [])
        run.batch_elapsed_seconds = list(entry.get("batch_elapsed_seconds") or [])
        runs.append(run)

    if by_cell:
        # A record whose cell the summary does not name would otherwise vanish
        # here, and a rebuilt table would quietly be computed over fewer calls
        # than the committed raw file holds — a silent loss of evidence, which
        # is exactly what C3 forbids. Refuse instead.
        raise ValueError(
            f"{records_path.name} holds records for cell(s) {sorted(by_cell)} that "
            f"{summary_path.name} does not describe; refusing to rebuild a table over "
            "fewer calls than the raw file contains"
        )

    worker = summary.get("worker") or {}
    return ProbeResult(
        config=WorkerConfig(
            base_url=str(worker.get("base_url", "")),
            model=str(worker.get("model", "")),
            api_key="",  # never serialized, never needed to re-derive a table
        ),
        temperature=float(summary.get("temperature") or DEFAULT_TEMPERATURE),
        warmup=warmup,
        cells=runs,
        window=dict(summary.get("window") or {}),
    )


# ── markdown rendering: every committed table comes from here ────────────────


def _fmt(value: Optional[float], places: int = 2, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value:,.{places}f}"


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_markdown(summary: Mapping[str, Any]) -> str:
    """Render every results table from the summary dict.

    Tables in the committed results document are **generated by this function**,
    never typed by hand — the t12 precedent: a typed table is a table nobody can
    re-derive. `main` writes the output to ``--markdown-out`` and the results
    document quotes it.
    """
    cells = [row["summary"] for row in summary.get("cells", [])]
    lines: list[str] = []

    lines += [
        "<!-- generated by examples/worker_scoped_overhead.py::render_markdown "
        "— do not hand-edit -->",
        "",
        # A top-level heading so this file is a valid standalone markdown
        # document (markdownlint MD041). `render-doc.py` strips everything above
        # the first `###` when splicing these tables into the results document,
        # so the assembled page keeps exactly one H1 — its own.
        "# Worker scoped-call overhead — generated tables",
        "",
        "## Tables",
        "",
        "### Table 1 — per-cell cost of one scoped call",
        "",
        "| cell | thinking | context | width | budget | calls | prompt tok (mean) "
        "| completion tok (mean) | latency median (s) | answered | truncated |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cell in cells:
        lines.append(
            f"| `{cell['cell']}` | {cell['thinking']} | {cell['context']} | {cell['width']} "
            f"| {cell['max_tokens']} | {cell['calls']} "
            f"| {_fmt(cell['prompt_tokens_mean'], 0)} "
            f"| {_fmt(cell['completion_tokens_mean'], 1)} "
            f"| {_fmt(cell['latency_seconds_median'], 2)} "
            f"| {cell['answered']}/{cell['ok']} | {cell['truncated']} |"
        )

    lines += [
        "",
        "### Table 2 — the residual: wall clock a call's completion tokens do not explain",
        "",
        f"Modelled decode uses the committed reference rate "
        f"**{summary['reference']['decode_tok_s_width1']} tok/s** "
        f"(`{summary['reference']['source']}`, width 1, measured on "
        f"{summary['reference']['measured_on_completion_tokens']}-token completions).",
        "",
        "| cell | width | completion tok (mean) | modelled decode (s) | latency mean (s) "
        "| residual mean (s) | residual share |",
        "|---|---|---|---|---|---|---|",
    ]
    for cell in cells:
        completion = cell["completion_tokens_mean"]
        modelled = (
            completion / summary["reference"]["decode_tok_s_width1"]
            if completion is not None
            else None
        )
        lines.append(
            f"| `{cell['cell']}` | {cell['width']} | {_fmt(completion, 1)} "
            f"| {_fmt(modelled, 2)} | {_fmt(cell['latency_seconds_mean'], 2)} "
            f"| {_fmt(cell['residual_seconds_mean'], 2)} "
            f"| {_pct(cell['residual_fraction_mean'])} |"
        )

    lines += [
        "",
        "### Table 3 — throughput per cell, per-stream and aggregate never conflated",
        "",
        "| cell | width | batches | aggregate wall (s) | aggregate tok/s "
        "| per-stream tok/s (mean) | effective concurrency | s per call | calls/s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cell in cells:
        lines.append(
            f"| `{cell['cell']}` | {cell['width']} | {cell['batches']} "
            f"| {_fmt(cell['aggregate_seconds'], 2)} "
            f"| {_fmt(cell['aggregate_tokens_per_second'], 2)} "
            f"| {_fmt(cell['per_stream_tokens_per_second_mean'], 2)} "
            f"| {_fmt(cell['effective_concurrency'], 3)} "
            f"| {_fmt(cell['seconds_per_call_wallclock'], 3)} "
            f"| {_fmt(cell['calls_per_second'], 3)} |"
        )

    lines += [
        "",
        "### Table 4 — does the ~9× concurrency figure transfer to tiny calls?",
        "",
        "| context | reference eff. concurrency (1200-tok calls) | measured eff. concurrency "
        "| transfer ratio | calls/s w1 | calls/s w8 | call-throughput speedup | efficiency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for context, row in (summary.get("concurrency_transfer") or {}).items():
        if not row.get("measured"):
            lines.append(f"| {context} | — | — | — | — | — | — | ABSENT |")
            continue
        lines.append(
            f"| {context} | {row['reference_effective_concurrency_1200_token_calls']} "
            f"| {_fmt(row['measured_effective_concurrency'], 3)} "
            f"| {_fmt(row['effective_concurrency_transfer_ratio'], 3)}× "
            f"| {_fmt(row['width1_calls_per_second'], 3)} "
            f"| {_fmt(row['width8_calls_per_second'], 3)} "
            f"| {_fmt(row['call_throughput_speedup'], 2)}× "
            f"| {_pct(row['call_throughput_efficiency'])} |"
        )

    transport = summary.get("transport") or {}
    clean_cells = [row["summary"] for row in summary.get("cells_excluding_retry_batches", [])]
    lines += ["", "### Table 5 — transport retries, and the batches they contaminate", ""]
    if not transport:
        lines += ["**ABSENT** — no transport summary in this artifact.", ""]
    else:
        lines += [
            f"**{transport['calls_with_retries']} of {transport['calls']} calls "
            f"({_pct(transport['retry_rate'])}) were retried**, "
            f"{transport['failed_calls']} failed outright. `WorkerSeam`'s backoff is a fixed "
            f"**{transport['retry_backoff_seconds']:.0f} s** against a median healthy scoped-call "
            f"latency of **{_fmt(transport['median_healthy_latency_seconds'], 2)} s** — a ratio of "
            f"**{_fmt(transport['backoff_over_median_healthy_latency'], 0)}×**.",
            "",
        ]
        if transport.get("retried_calls"):
            lines += [
                "| cell | batch | slot | latency (s) | retries | ok |",
                "|---|---|---|---|---|---|",
            ]
            for row in transport["retried_calls"]:
                lines.append(
                    f"| `{row['cell']}` | {row['batch']} | {row['slot']} "
                    f"| {_fmt(row['latency_seconds'], 2)} | {row['retries']} | {row['ok']} |"
                )
            lines.append("")
    if clean_cells:
        lines += [
            "Those batches dropped whole (a retried call's neighbours shared its wall clock), "
            "the affected cells read:",
            "",
            "| cell | batches kept | calls | aggregate wall (s) | s per call | calls/s "
            "| latency median (s) |",
            "|---|---|---|---|---|---|---|",
        ]
        for cell in clean_cells:
            lines.append(
                f"| `{cell['cell']}` | {cell['batches']} | {cell['calls']} "
                f"| {_fmt(cell['aggregate_seconds'], 2)} "
                f"| {_fmt(cell['seconds_per_call_wallclock'], 3)} "
                f"| {_fmt(cell['calls_per_second'], 3)} "
                f"| {_fmt(cell['latency_seconds_median'], 2)} |"
            )
        clean_transfer = summary.get("concurrency_transfer_excluding_retry_batches") or {}
        if clean_transfer:
            lines += [
                "",
                "and Table 4 recomputed on those same retry-free batches:",
                "",
                "| context | measured eff. concurrency | transfer ratio | calls/s w1 | calls/s w8 "
                "| call-throughput speedup | efficiency |",
                "|---|---|---|---|---|---|---|",
            ]
            for context, row in clean_transfer.items():
                if not row.get("measured"):
                    lines.append(f"| {context} | — | — | — | — | — | ABSENT |")
                    continue
                lines.append(
                    f"| {context} | {_fmt(row['measured_effective_concurrency'], 3)} "
                    f"| {_fmt(row['effective_concurrency_transfer_ratio'], 3)}× "
                    f"| {_fmt(row['width1_calls_per_second'], 3)} "
                    f"| {_fmt(row['width8_calls_per_second'], 3)} "
                    f"| {_fmt(row['call_throughput_speedup'], 2)}× "
                    f"| {_pct(row['call_throughput_efficiency'])} |"
                )

    per_turn = summary.get("scoped_calls_per_cortex_turn") or {}
    lines += [
        "",
        "### Table 6 — what realistic repeated context costs (the prefill axis)",
        "",
        "| width | lean prompt tok | rich prompt tok | extra prompt tok | lean latency (s) "
        "| rich latency (s) | extra latency (s) | implied prefill tok/s | calls/s lean "
        "| calls/s rich | throughput ratio |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _key, row in (summary.get("prefill_cost") or {}).items():
        if not row.get("measured"):
            lines.append("| — | — | — | — | — | — | — | — | — | — | ABSENT |")
            continue
        lines.append(
            f"| {row['width']} | {_fmt(row['lean_prompt_tokens_mean'], 0)} "
            f"| {_fmt(row['rich_prompt_tokens_mean'], 0)} "
            f"| {_fmt(row['extra_prompt_tokens'], 0)} "
            f"| {_fmt(row['lean_latency_seconds_mean'], 2)} "
            f"| {_fmt(row['rich_latency_seconds_mean'], 2)} "
            f"| {_fmt(row['extra_latency_seconds'], 2)} "
            f"| {_fmt(row['implied_prefill_tokens_per_second'], 0)} "
            f"| {_fmt(row['lean_calls_per_second'], 3)} "
            f"| {_fmt(row['rich_calls_per_second'], 3)} "
            f"| {_fmt(row['throughput_ratio_rich_over_lean'], 2)}× |"
        )

    lines += [
        "",
        "### Table 7 — what leaving the thinking toggle alone costs a scoped call",
        "",
        "| budget | max_tokens | completion tok off | completion tok on | multiple "
        "| latency off (s) | latency on (s) | multiple | answered off | answered on |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, row in (summary.get("thinking_cost") or {}).items():
        if not row.get("measured"):
            lines.append(f"| {key} | — | — | — | — | — | — | — | — | ABSENT |")
            continue
        lines.append(
            f"| {key} | {row['max_tokens']} "
            f"| {_fmt(row['off_completion_tokens_mean'], 1)} "
            f"| {_fmt(row['on_completion_tokens_mean'], 1)} "
            f"| **{_fmt(row['completion_token_multiple'], 1)}×** "
            f"| {_fmt(row['off_latency_seconds_mean'], 2)} "
            f"| {_fmt(row['on_latency_seconds_mean'], 2)} "
            f"| **{_fmt(row['latency_multiple'], 1)}×** "
            f"| {row['off_answered']} | {row['on_answered']} |"
        )

    lines += ["", "### Table 8 — N scoped calls at width 8 versus one cortex turn", ""]
    if not per_turn.get("measured"):
        lines += ["**ABSENT** — " + str(per_turn.get("why", "not measured")), ""]
    else:
        lines += [
            f"Measured on cell `{per_turn['cell']}`: "
            f"**{_fmt(per_turn['seconds_per_scoped_call_at_width8'], 3)} s** of wall clock and "
            f"**{_fmt(per_turn['completion_tokens_per_scoped_call'], 1)}** completion tokens "
            f"(+{_fmt(per_turn['prompt_tokens_per_scoped_call'], 0)} prompt tokens) per scoped "
            "call.",
            "",
            "| cortex-turn budget | low end | high end |",
            "|---|---|---|",
            f"| one cortex turn | {per_turn['cortex_turn_seconds_range'][0]:,.0f} s / "
            f"{per_turn['cortex_turn_completion_tokens_range'][0]:,} completion tok "
            f"| {per_turn['cortex_turn_seconds_range'][1]:,.0f} s / "
            f"{per_turn['cortex_turn_completion_tokens_range'][1]:,} completion tok |",
            f"| **scoped calls that fit — wall clock** "
            f"| **{_fmt(per_turn['scoped_calls_per_cortex_turn_wallclock_low'], 0)}** "
            f"| **{_fmt(per_turn['scoped_calls_per_cortex_turn_wallclock_high'], 0)}** |",
            f"| scoped calls that fit — completion tokens "
            f"| {_fmt(per_turn['scoped_calls_per_cortex_turn_completion_tokens_low'], 0)} "
            f"| {_fmt(per_turn['scoped_calls_per_cortex_turn_completion_tokens_high'], 0)} |",
            "",
            per_turn["note"],
        ]

    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--worker-url",
        default=None,
        help=f"OpenAI-compatible base URL, e.g. {THOR_WORKER_URL_DOCUMENTED}",
    )
    parser.add_argument("--worker-model", default=None, help=f"e.g. {THOR_WORKER_MODEL_DOCUMENTED}")
    parser.add_argument(
        "--cells",
        default=None,
        help="comma-separated cell ids to run (default: every cell in CELLS)",
    )
    parser.add_argument(
        "--batches",
        default=None,
        help=(
            "override batches per cell, e.g. 'lean-off-w1:8,rich-off-w8:4'. Raises n "
            "WITHOUT editing the committed cell table — the design stays reviewable in "
            "git and only the sample size moves. Mirrors worker_throughput.py's flag."
        ),
    )
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--out", default=None, help="write every per-call record as JSONL here")
    parser.add_argument("--summary-out", default=None, help="write the aggregated summary JSON")
    parser.add_argument("--markdown-out", default=None, help="write the generated tables markdown")
    parser.add_argument(
        "--window-log",
        default=None,
        help="a thor-idle-guard JSONL log to embed in the summary as the declared window",
    )
    parser.add_argument(
        "--rebuild-from",
        nargs=2,
        metavar=("RECORDS_JSONL", "SUMMARY_JSON"),
        default=None,
        help=(
            "recompute the summary and tables from a committed run instead of dialling. "
            "Dials NOTHING and needs no API key — this is how the results document's "
            "tables are re-derived from the artifacts in git."
        ),
    )
    return parser


def parse_batch_overrides(raw: Optional[str]) -> dict[str, int]:
    """``'a:8,b:4'`` -> ``{'a': 8, 'b': 4}``. Empty input is no override at all."""
    result: dict[str, int] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        cell_id, _, count = pair.partition(":")
        result[cell_id.strip()] = int(count)
    return result


def apply_batch_overrides(cells: Sequence[Cell], overrides: Mapping[str, int]) -> tuple[Cell, ...]:
    """Return ``cells`` with batch counts replaced. Never mutates :data:`CELLS`.

    An override for an id that is not being run is silently irrelevant rather
    than an error — the caller has already validated the *selection* — but every
    cell that runs carries its actual batch count into its own summary, so a
    committed artifact always states the n it was measured at.
    """
    return tuple(
        Cell(**{**cell.__dict__, "batches": overrides[cell.id]}) if cell.id in overrides else cell
        for cell in cells
    )


def _load_window(path: Optional[str]) -> dict[str, Any]:
    if not path:
        return {"declared": False, "why": "no --window-log supplied"}
    file = Path(path)
    if not file.exists():
        return {"declared": False, "why": f"no window log at {path}"}
    checks = [
        json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {
        "declared": bool(checks),
        "source": path,
        "checks": [
            {"label": c.get("label"), "at": c.get("at"), "verdict": c.get("verdict")}
            for c in checks
        ],
    }


def _write_artifacts(result: ProbeResult, args: argparse.Namespace) -> dict[str, Any]:
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            for record in result.all_records():
                handle.write(json.dumps(record.to_dict()) + "\n")
    summary = result.to_summary_dict()
    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).write_text(render_markdown(summary), encoding="utf-8")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.rebuild_from:
        records_path, summary_path = (Path(part) for part in args.rebuild_from)
        for path in (records_path, summary_path):
            if not path.exists():
                print(f"notice: no such artifact: {path}", file=sys.stderr)
                return 1
        rebuilt = rebuild_from_artifacts(records_path, summary_path)
        print(json.dumps(_write_artifacts(rebuilt, args), indent=2))
        return 0

    resolution = resolve_worker_config(cli_url=args.worker_url, cli_model=args.worker_model)
    if not resolution.ok:
        for degradation in resolution.degradations:
            print(f"notice: {degradation.code}: {degradation.detail}", file=sys.stderr)
        print(json.dumps(resolution.to_dict(), indent=2))
        return 2

    selected = CELLS
    if args.cells:
        wanted = {part.strip() for part in args.cells.split(",") if part.strip()}
        selected = tuple(cell for cell in CELLS if cell.id in wanted)
        missing = wanted - {cell.id for cell in selected}
        if missing:
            print(f"notice: unknown cell id(s): {sorted(missing)}", file=sys.stderr)
            return 1
    selected = apply_batch_overrides(selected, parse_batch_overrides(args.batches))

    assert resolution.config is not None  # narrows for readers; ok implies this
    result = run_probe(
        resolution.config,
        cells=selected,
        temperature=args.temperature,
        warmup=not args.no_warmup,
        window=_load_window(args.window_log),
    )

    print(json.dumps(_write_artifacts(result, args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
