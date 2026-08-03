"""The bound, enforced: every clock in front of a token-budgeted model call.

Task ``t2`` of the ``error-derived-timeouts-bee-hive-architecture`` plan, and
the keystone of the timeout lane. Claims ``c2``/``h2``, ``c4``/``h4``,
``c13``/``h5``, ``c14``/``h6``, ``c16``/``h8``, ``c36``/``h24``.

The rule, from [#42](https://github.com/agentculture/embodiment/issues/42)::

    REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate

Derived from the **budget**, never from a latency percentile. Two properties a
percentile does not have. It is **not circular** — a percentile is sized from a
tail the current timeout has already censored, which is the exact defect
pre-registration amendment 1 found: eight completions all landed under the
~6,975-token ceiling that 300 s at ~23 tok/s implies, because the ones above it
could not be recorded, while every call logged ``truncated: false``. And it
moves the binding constraint onto the quantity that **reports itself**: above
the bound an over-long turn ends ``finish_reason == "length"``, visible and
gradeable; below it the identical turn vanishes into a transport retry.

Where this lives, and where it does not
---------------------------------------

Per-harness constants plus **one** shared CI test — the recorded ``q4``
decision. Nothing here moves transport policy into ``embodiment/``; that is
parked as a follow-up. This module imports no embodiment package code, and the
rates it divides by come from ``docs/live-test-results/timeout-rate-measurements.json``
through :mod:`tests.rate_config`, which is the only way in and defines no
fallback. **There is no rate literal in this file**, and
``tests/test_rate_config.py`` walks this module's AST to prove it.

Three findings this test exists to encode
------------------------------------------

**1. A constant fronts every model dialled through it** (task ``t4``,
`corrections.md` §9). ``league_commander.py`` dials Gemma 4 31B through the
*same* ``REQUEST_TIMEOUT`` as the Qwen cortex, and #42 derived that bound at the
cortex rate alone. At Gemma's measured rate the bound is ~1,322 s, so the 900 s
#42's audit table called *the narrowest passing margin* was **0.68× and below
bound** for the other model on the same wire. Every clock below therefore
declares *every* role it fronts, and its bound is the worst of them — not the
one whose rate was to hand. Three more constants front that model.

Applying that rule turns up a second instance, in a document rather than a
harness: **amendment 1's own 1200.0 is below bound.** It derived
``16000 / 21.5 = 744 s`` at the *cortex* rate, but ``WorkerSeam`` is the
transport for the **worker** too (``arch_arms.ArchSeam`` subclasses it), and at
the worker's committed rate the bound is 1238.3 s. Amendment 2, hours later on
the same branch, used that very figure as its own per-turn bound. Recorded in
`corrections.md` §11.

**2. The bound is generation-only; the clock is not.** One committed call spent
**179.3 s not generating** — queue wait plus prompt processing. 900 s over a
744 s generation bound leaves 155.8 s, *less* than that observed gap, so a
full-budget completion queued behind the same call totals ~923 s and is cut.
:func:`derive_timeout` therefore takes an explicit ``non_generation_s``, and
every pair below is evaluated with the committed allowance. It is **not** added
where the measured rate already contains queue and prefill — and that claim is
checked, never believed: see :func:`per_turn_bound`.

**3. A third clock category the audit never examined** (task ``t8``): the retry
backoff. It is not derivable from a token budget, it is **exempt with a stated
reason**, and the exemption is not silence — see :data:`BACKOFFS` and
:class:`TestBackoffConstants`, which pin the accounting property that makes the
exemption safe and the layering property that makes it survive the raises.

What "bound" means for a wait deadline
---------------------------------------

``DEFAULT_FANOUT_TIMEOUT`` does not bound one model call. It bounds a whole
unit *drive* — up to ``DEFAULT_FANOUT_MAX_STEPS`` turns, each with its own
budget — so its bound is the per-turn bound **times the turn budget actually
granted** (claim ``c16``). Below it, units land ``fanout-unit-absent`` and the
fan-out layer censors exactly the way the transport layer did, with the added
cruelty that ``fanout-unit-absent`` names a straggler rather than a truncation.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Mapping, Optional, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import rate_config as rc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
#: The scope lane's own folder. Walked by the same guard as the rest of
#: ``examples/`` since ``rglob`` replaced ``glob``; ``t10`` proves that reach
#: rather than trusting it (:class:`TestTheWalkReachesEverySubfolder`).
SCOPE_DIR = EXAMPLES_DIR / "scope"
#: The package itself. ``t10`` brings it under the same declare-or-derive rule
#: the harnesses are under — see :data:`_NOT_A_MODEL_CLOCK_IN_PACKAGE`.
PACKAGE_DIR = REPO_ROOT / "embodiment"
RESULTS_DIR = REPO_ROOT / "docs" / "live-test-results"


# ── the helper (acceptance criterion 1) ──────────────────────────────────────


def derive_timeout(max_tokens: int, rate_tok_s: float, *, non_generation_s: float = 0.0) -> float:
    """Seconds a clock must allow so the **budget** binds before the clock does.

    Pure: no IO, no clock, no config read. Its two positional inputs are #42's
    rule verbatim, and the default ``non_generation_s=0.0`` is that rule
    unchanged rather than an invented value — 0.0 is the identity element, not
    a measurement standing in for one.

    :param max_tokens: the completion budget the call is dialled with.
    :param rate_tok_s: the measured generation rate the bound divides into.
    :param non_generation_s: seconds the request may spend *not* generating —
        queue wait and prompt processing. Finding 2: the rule's right-hand side
        bounds generation and the clock in front of it does not, and one
        committed call's gap was larger than the entire slack a *passing*
        constant had left.
    :raises ValueError: on a non-positive budget or rate. There is no bound to
        derive from a rate nobody measured, and returning ``inf`` (or 0.0)
        would be a bound that either never binds or always does — both of them
        answers to a question that was never asked.
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens!r}")
    if rate_tok_s <= 0:
        raise ValueError(f"rate_tok_s must be positive, got {rate_tok_s!r}")
    if non_generation_s < 0:
        raise ValueError(f"non_generation_s must not be negative, got {non_generation_s!r}")
    return max_tokens / rate_tok_s + non_generation_s


# ── where a budget actually lives ────────────────────────────────────────────


def _sampling_max_tokens(config_name: str, role: str) -> int:
    """The largest ``max_tokens`` any arm gives *role* in a committed sampling table.

    The largest, not the first: a constant fronts every arm that will be
    dialled through it, and an arm added later with a bigger budget must move
    the bound rather than hide behind a sibling.
    """
    payload = json.loads((RESULTS_DIR / config_name).read_text(encoding="utf-8"))
    budgets = [
        int(cell["max_tokens"])
        for arm, roles in (payload.get("sampling") or {}).items()
        if arm != "why" and isinstance(roles, Mapping)
        for name, cell in roles.items()
        if name == role and isinstance(cell, Mapping)
    ]
    if not budgets:
        raise AssertionError(f"{config_name} declares no max_tokens for role {role!r}")
    return max(budgets)


@dataclass(frozen=True)
class Budget:
    """A completion budget, read from where it is defined rather than retyped.

    An import is honest about what the module actually holds; a number copied
    into a test is a second source of truth that drifts on the first edit
    nobody thought to mirror.
    """

    label: str
    module: Optional[str] = None
    attr: Optional[str] = None
    sampling: Optional[str] = None
    role: Optional[str] = None

    def resolve(self) -> int:
        if self.module is not None and self.attr is not None:
            value = getattr(importlib.import_module(self.module), self.attr)
            return int(value)
        if self.sampling is not None and self.role is not None:
            return _sampling_max_tokens(self.sampling, self.role)
        raise AssertionError(f"budget {self.label!r} names no source to resolve from")


# ── the declaration: which models each clock fronts ──────────────────────────


@dataclass(frozen=True)
class Fronted:
    """One (model, budget) pair a clock stands in front of.

    ``role`` is a role name in the committed rate config, never a model id
    parsed out of a string — the repo's standing rule, and the reason the muse
    entry says plainly that a harness running Gemma as an acting cortex still
    consumes the *muse role's* measured rate: the role name identifies whose
    rate this is, not what the harness asks it to do.
    """

    role: str
    budget: Budget
    width: Optional[int] = None
    why: str = ""


@dataclass(frozen=True)
class Clock:
    """One constant under the bound, and everything needed to derive it."""

    module: str
    constant: str
    kind: str
    fronts: tuple[Fronted, ...]
    #: ``(module, attr)`` naming the turn budget a *wait deadline* covers. A
    #: client timeout bounds one model call and leaves this ``None``.
    turn_budget: Optional[tuple[str, str]] = None
    #: Roles this clock fronts that have no committed rate. Declared, so the
    #: gap is visible; the config carries the detail and the closing trigger.
    unmeasured: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        return f"{self.module.rsplit('.', 1)[-1]}.{self.constant}"

    @property
    def source_path(self) -> Path:
        return EXAMPLES_DIR / f"{self.module.rsplit('.', 1)[-1]}.py"

    def shipped(self) -> float:
        return float(getattr(importlib.import_module(self.module), self.constant))

    def turns(self) -> int:
        if self.turn_budget is None:
            return 1
        module, attr = self.turn_budget
        return int(getattr(importlib.import_module(module), attr))


CLIENT_TIMEOUT = "client-timeout"
WAIT_DEADLINE = "wait-deadline"

ARCH_ARMS_SAMPLING = "arch-arms-sampling.json"
ARCH_HIVE_SAMPLING = "arch-hive-sampling.json"

#: **The walk.** Seven constants, every one of #42's audit table, each with the
#: full set of models actually dialled through it. Adding a harness that dials
#: a model on a new clock means adding a row here; a clock that is not in this
#: tuple is not under the bound, which is why
#: :class:`TestTheWalkCoversTheAuditedSurface` refuses to let the tuple shrink.
CLOCKS: tuple[Clock, ...] = (
    Clock(
        module="examples.worker_seam",
        constant="REQUEST_TIMEOUT",
        kind=CLIENT_TIMEOUT,
        fronts=(
            Fronted(
                role="worker",
                budget=Budget("arch_arms worker cell", sampling=ARCH_ARMS_SAMPLING, role="worker"),
                why="examples/arch_arms.py's ArchSeam subclasses WorkerSeam; arms W/M/H "
                "dial the worker at the sampling table's worker budget",
            ),
            Fronted(
                role="cortex",
                budget=Budget("arch_arms cortex cell", sampling=ARCH_ARMS_SAMPLING, role="cortex"),
                why="the same ArchSeam drives the cortex in arms E/M/H, and "
                "examples/arch_hive.py drives it in both hive tiers",
            ),
            Fronted(
                role="worker",
                budget=Budget(
                    "worker_throughput sweep",
                    module="examples.worker_throughput",
                    attr="DEFAULT_MAX_TOKENS",
                ),
                width=14,
                why="the throughput sweep's own ThroughputSeam is a WorkerSeam subclass, "
                "dialled at width 14",
            ),
            Fronted(
                role="worker",
                budget=Budget("arch_hive scoped call", sampling=ARCH_HIVE_SAMPLING, role="worker"),
                why="hive tier B1's scoped calls build a fresh WorkerSeam per call",
            ),
            Fronted(
                role="cortex",
                budget=Budget(
                    "scopebench_live strategist seat",
                    module="examples.scopebench_live",
                    attr="STRATEGIST_MAX_TOKENS",
                ),
                why="t11's ScopeBench series builds a fresh WorkerSeam per episode for the "
                "STRATEGY seat; arm A3 seats the cortex role there. No width is declared "
                "because the harness dials serially (STREAM_QUEUE_WIDTH = 1), which is the "
                "concurrency this role's rate was measured at",
            ),
            Fronted(
                role="worker",
                budget=Budget(
                    "scopebench_live strategist seat",
                    module="examples.scopebench_live",
                    attr="STRATEGIST_MAX_TOKENS",
                ),
                why="the same seam and the same budget in arm A2, which seats the worker "
                "role in the strategist seat — the control that removes 'the gain is just "
                "the extra layer'. Divides by the role-level floor, which the config's "
                "scoped_run calling pattern already argues is the honest reading for a "
                "scope-lane dial nothing reserves the deployment for",
            ),
        ),
        unmeasured=("senses",),
        notes=(
            "The lowest timeout in the repository, in front of the slowest model on the "
            "rig, until amendment 1 raised it — and amendment 1 derived it at the cortex "
            "rate while this seam is the worker's transport too.",
        ),
    ),
    Clock(
        module="examples.league_commander",
        constant="REQUEST_TIMEOUT",
        kind=CLIENT_TIMEOUT,
        fronts=(
            Fronted(
                role="muse",
                budget=Budget(
                    "league_commander budget", module="examples.league_commander", attr="MAX_TOKENS"
                ),
                why="ARM_MODELS puts Gemma 4 31B on the commander seat in arm B and on "
                "every unit in arm C, through the same gateway_seam",
            ),
            Fronted(
                role="cortex",
                budget=Budget(
                    "league_commander budget", module="examples.league_commander", attr="MAX_TOKENS"
                ),
                why="the Qwen cortex holds the other seat in the same arms",
            ),
        ),
        notes=(
            "The finding that produced this whole rule: #42 read 900 s as passing at "
            "1.21x by deriving at the cortex alone.",
        ),
    ),
    Clock(
        module="examples.league_h2h",
        constant="REQUEST_TIMEOUT",
        kind=CLIENT_TIMEOUT,
        fronts=(
            Fronted(
                role="muse",
                budget=Budget(
                    "h2h muse budget", module="examples.league_h2h", attr="MUSE_MAX_TOKENS"
                ),
                why="Gemma 4 31B is the muse in the full-gemma and mixed arms, and the "
                "acting cortex in full-gemma, both through MeteredSeam",
            ),
            Fronted(
                role="muse",
                budget=Budget("h2h cortex budget", module="examples.league_h2h", attr="MAX_TOKENS"),
                why="in the full-gemma arm the same model holds the cortex seat at the "
                "cortex budget",
            ),
            Fronted(
                role="cortex",
                budget=Budget("h2h cortex budget", module="examples.league_h2h", attr="MAX_TOKENS"),
                why="Qwen holds the cortex seat in mixed and full-qwen",
            ),
        ),
    ),
    Clock(
        module="examples.devague_legs",
        constant="REQUEST_TIMEOUT_S",
        kind=CLIENT_TIMEOUT,
        fronts=(
            Fronted(
                role="cortex",
                budget=Budget(
                    "devague_legs cortex budget",
                    module="examples.devague_legs",
                    attr="CORTEX_MAX_TOKENS",
                ),
                why="both minds share one complete() and therefore one timeout",
            ),
            Fronted(
                role="muse",
                budget=Budget(
                    "devague_legs muse budget",
                    module="examples.devague_legs",
                    attr="MUSE_MAX_TOKENS",
                ),
                why="DEFAULT_MUSE is Gemma 4 31B on the same seam",
            ),
        ),
    ),
    Clock(
        module="examples.muse_arms",
        constant="REQUEST_TIMEOUT_S",
        kind=CLIENT_TIMEOUT,
        fronts=(
            Fronted(
                role="muse",
                budget=Budget(
                    "muse_arms budget", module="examples.muse_arms", attr="DEFAULT_MAX_TOKENS"
                ),
                why="the only model this harness dials",
            ),
        ),
    ),
    Clock(
        module="examples.worker_throughput",
        constant="BATCH_WAIT_TIMEOUT_SECONDS",
        kind=WAIT_DEADLINE,
        fronts=tuple(
            Fronted(
                role="worker",
                budget=Budget(
                    "throughput sweep budget",
                    module="examples.worker_throughput",
                    attr="DEFAULT_MAX_TOKENS",
                ),
                width=width,
                why=f"one batch at concurrency width {width}",
            )
            for width in (1, 2, 8, 14)
        ),
        notes=(
            "A batch's wall clock is its SLOWEST call, not the sum: the calls run "
            "concurrently, so the turn budget is one. The per-width rate is what varies, "
            "and every width this module dials has been measured — rates are not "
            "interpolated, so at_width refuses rather than inventing one.",
        ),
    ),
    Clock(
        module="examples.orchestrator_tools",
        constant="DEFAULT_FANOUT_TIMEOUT",
        kind=WAIT_DEADLINE,
        fronts=(
            Fronted(
                role="worker",
                budget=Budget("fan-out unit budget", sampling=ARCH_ARMS_SAMPLING, role="worker"),
                why="a fan-out unit is a bounded drive on the worker, dialled through "
                "build_worker_seam and therefore through WorkerSeam",
            ),
        ),
        turn_budget=("examples.orchestrator_tools", "DEFAULT_FANOUT_MAX_STEPS"),
        notes=(
            "Claim c16: #42's audit covered six client timeouts and missed this one "
            "entirely. 60.0 s was roughly 1/248th of the work it bounded, and the "
            "censoring it would have produced was biased against exactly the arms the "
            "series exists to test — arm E never fans out.",
        ),
    ),
    Clock(
        module="examples.worker_scoped_overhead",
        constant="BATCH_WAIT_TIMEOUT_SECONDS",
        kind=WAIT_DEADLINE,
        fronts=tuple(
            Fronted(
                role="worker",
                budget=Budget(
                    "scoped-overhead natural budget",
                    module="examples.worker_scoped_overhead",
                    attr="NATURAL_MAX_TOKENS",
                ),
                width=width,
                why=f"t8's overhead probe dials the worker at width {width}; the natural "
                "tier is the larger of its two budgets",
            )
            for width in (1, 8)
        ),
        notes=(
            "AN EIGHTH CONSTANT, found by test_no_timeout_constant_in_examples_escapes_"
            "the_walk rather than by #42's audit or by this plan's list of seven. t8's "
            "probe landed after the audit was written, inherited worker_throughput's "
            "circuit breaker, and would have been an underived clock in front of a model "
            "call. It passes — but nothing had checked, which is the whole argument for "
            "closing the category by AST instead of by a list somebody maintains.",
        ),
    ),
)


# ── evaluating one clock ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RateView:
    """The three numbers a bound needs, from either a role or one of its widths."""

    label: str
    divisor_tok_s: float
    clean_floor_tok_s: Optional[float]
    includes_non_generation: bool


def rate_view(config: rc.RateConfig, fronted: Fronted) -> RateView:
    measurement = config.rate(fronted.role)
    if fronted.width is None:
        return RateView(
            label=f"{fronted.role} ({measurement.bound_input_field})",
            divisor_tok_s=measurement.bound_input_tok_s,
            clean_floor_tok_s=measurement.retry_clean_floor_tok_s,
            includes_non_generation=measurement.rate_includes_non_generation,
        )
    at_width = measurement.at_width(fronted.width)
    return RateView(
        label=f"{fronted.role} at width {fronted.width}",
        divisor_tok_s=at_width.slowest_tok_s,
        clean_floor_tok_s=at_width.slowest_retry_clean_tok_s,
        includes_non_generation=measurement.rate_includes_non_generation,
    )


ABSORBED = "absorbed"
ADDED = "added"


def per_turn_bound(view: RateView, budget: int, allowance: float) -> tuple[float, str]:
    """One turn's bound, and whether the queue allowance was added or absorbed.

    Finding 2 has two halves and this is the second. Adding the allowance is
    right where the measured rate is a *generation* rate; it double-counts
    where the rate is wall-clock-over-tokens on short completions, because
    queue and prefill are already amortised into such a rate.

    The config declares which kind a role's rate is — and **the declaration is
    checked, not believed**. Absorption is permitted only where the role also
    carries a retry-clean floor and the cited rate is slower than that floor by
    at least the whole allowance *at this budget*. That condition is
    budget-dependent on purpose: a rate's conservatism scales with the number
    of tokens divided by it, while queue wait does not. Same role, same rate,
    16000 tokens absorbs and 1200 does not — and the arithmetic says so rather
    than a judgement call.
    """
    generation = derive_timeout(budget, view.divisor_tok_s)
    if view.includes_non_generation and view.clean_floor_tok_s is not None:
        clean = derive_timeout(budget, view.clean_floor_tok_s, non_generation_s=allowance)
        if generation >= clean:
            return generation, ABSORBED
    return derive_timeout(budget, view.divisor_tok_s, non_generation_s=allowance), ADDED


@dataclass(frozen=True)
class Term:
    """One (model, budget) pair's contribution to a clock's bound."""

    label: str
    budget: int
    divisor_tok_s: float
    per_turn: float
    treatment: str
    bound: float


@dataclass(frozen=True)
class Verdict:
    clock: Clock
    shipped: float
    bound: float
    turns: int
    terms: tuple[Term, ...]

    @property
    def ok(self) -> bool:
        return self.shipped >= self.bound

    @property
    def margin(self) -> float:
        return self.shipped / self.bound if self.bound else float("inf")

    @property
    def binding(self) -> Term:
        return max(self.terms, key=lambda term: term.bound)

    def message(self) -> str:
        rows = "\n".join(
            f"    {term.label:<28} {term.budget:>6} tok / {term.divisor_tok_s:>8.3f} tok/s"
            f"  -> {term.per_turn:>10.2f} s/turn ({term.treatment}) = {term.bound:>10.2f} s"
            for term in self.terms
        )
        worst = self.binding
        return (
            f"\n{self.clock.id} = {self.shipped} but its derived bound is "
            f"{self.bound:.2f} s ({self.margin:.2f}x).\n"
            f"  kind: {self.clock.kind}, turn budget {self.turns}\n"
            f"  every model this constant fronts:\n{rows}\n"
            f"  binding term: {worst.label}\n"
            f"  RAISE {self.clock.constant} in {self.clock.source_path.name} to at least "
            f"{self.bound:.1f}, or re-derive from "
            f"{rc.DEFAULT_CONFIG_PATH.name} if the rig changed. Below the bound a "
            "full-budget turn is cut mid-thought and arrives as a transport retry, "
            "which no record distinguishes from a turn that ended on purpose."
        )


def evaluate(clock: Clock, config: rc.RateConfig) -> Verdict:
    """Recompute *clock*'s bound from committed inputs only. No observed latency."""
    allowance = config.non_generation_allowance.seconds
    turns = clock.turns()
    terms = []
    for fronted in clock.fronts:
        view = rate_view(config, fronted)
        budget = fronted.budget.resolve()
        per_turn, treatment = per_turn_bound(view, budget, allowance)
        terms.append(
            Term(
                label=view.label,
                budget=budget,
                divisor_tok_s=view.divisor_tok_s,
                per_turn=per_turn,
                treatment=treatment,
                bound=per_turn * turns,
            )
        )
    return Verdict(
        clock=clock,
        shipped=clock.shipped(),
        bound=max(term.bound for term in terms),
        turns=turns,
        terms=tuple(terms),
    )


CLOCK_IDS = [clock.id for clock in CLOCKS]


@pytest.fixture(scope="module")
def config() -> rc.RateConfig:
    return rc.load_rate_config()


# ── criterion 1: the walk ────────────────────────────────────────────────────


class TestEveryConstantIsAtOrAboveItsBound:
    """``c14``/``h6``: the bound is enforced in CI, not noticed by a series."""

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_the_constant_clears_its_derived_bound(
        self, clock: Clock, config: rc.RateConfig
    ) -> None:
        verdict = evaluate(clock, config)
        assert verdict.ok, verdict.message()

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_the_bound_is_derived_from_committed_inputs_only(
        self, clock: Clock, config: rc.RateConfig
    ) -> None:
        """``h5``: budget and cited rate, never an observed latency distribution.

        Structural rather than aspirational: every term's divisor must be a
        value the loaded config actually holds, and every budget must resolve
        from a module attribute or a committed sampling table. A percentile
        computed here would satisfy neither.
        """
        published = set(config.every_measured_rate())
        verdict = evaluate(clock, config)
        assert verdict.terms
        for term in verdict.terms:
            assert term.divisor_tok_s in published, (
                f"{clock.id}: {term.label} divides by {term.divisor_tok_s}, which is not "
                f"a figure {rc.DEFAULT_CONFIG_PATH.name} publishes"
            )
            assert term.budget > 0

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_a_clock_fronting_more_than_one_model_binds_on_the_slowest(
        self, clock: Clock, config: rc.RateConfig
    ) -> None:
        """Finding 1, as an assertion rather than a habit."""
        verdict = evaluate(clock, config)
        assert verdict.bound == max(term.bound for term in verdict.terms)


# ── criterion 2: the test of the test ────────────────────────────────────────


class TestTheTestCanFail:
    """``h6``: proven able to go red, per the M2/M3 discipline.

    A green bound test and a vacuous one are indistinguishable from the outside,
    and this repo has shipped the vacuous kind before (`corrections.md` §3). So
    every constant is mutated below its own bound, one at a time, and the walk
    must reject it — and the mutation is applied to the **module attribute the
    walk reads**, not to a copy, so what is proven is that this test's own path
    to the constant is live.
    """

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_one_constant_mutated_below_its_bound_goes_red(
        self, clock: Clock, config: rc.RateConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bound = evaluate(clock, config).bound
        module = importlib.import_module(clock.module)
        monkeypatch.setattr(module, clock.constant, bound - 1.0)

        verdict = evaluate(clock, config)
        assert not verdict.ok
        message = verdict.message()
        assert clock.constant in message
        assert clock.source_path.name in message
        assert verdict.binding.label in message

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_exactly_at_the_bound_passes(
        self, clock: Clock, config: rc.RateConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: the mutation harness is not simply always-red.

        ``>=`` is the rule, so a constant sitting exactly on its bound is
        compliant — and a test that failed here would be enforcing a margin
        nobody wrote down.
        """
        bound = evaluate(clock, config).bound
        module = importlib.import_module(clock.module)
        monkeypatch.setattr(module, clock.constant, bound)
        assert evaluate(clock, config).ok

    def test_the_mutation_reaches_the_real_module_attribute(
        self, config: rc.RateConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard on the guard: monkeypatching must change what ``shipped()`` reads."""
        clock = CLOCKS[0]
        before = clock.shipped()
        monkeypatch.setattr(importlib.import_module(clock.module), clock.constant, before + 1.0)
        assert clock.shipped() == before + 1.0


# ── criterion 3: the fan-out deadline, and main never red ────────────────────


class TestFanoutDeadline:
    """``c16``/``h8``: a deadline over a drive, not over a turn."""

    @staticmethod
    def _fanout() -> Clock:
        return next(clock for clock in CLOCKS if clock.kind == WAIT_DEADLINE and clock.turn_budget)

    def test_the_deadline_is_per_turn_bound_times_the_granted_turn_budget(
        self, config: rc.RateConfig
    ) -> None:
        clock = self._fanout()
        verdict = evaluate(clock, config)
        assert verdict.turns > 1, "a fan-out deadline over a single turn is not a drive"
        per_turn = verdict.binding.per_turn
        assert verdict.bound == pytest.approx(per_turn * verdict.turns)

    def test_the_turn_budget_is_the_one_the_module_actually_grants(self) -> None:
        """Read from the module, so a change to the grant moves the bound with it."""
        import examples.orchestrator_tools as ot

        clock = self._fanout()
        assert clock.turns() == ot.DEFAULT_FANOUT_MAX_STEPS

    def test_a_single_turn_bound_would_not_have_covered_it(self, config: rc.RateConfig) -> None:
        """The whole point of c16, stated as a comparison rather than a claim."""
        clock = self._fanout()
        verdict = evaluate(clock, config)
        assert verdict.bound > verdict.binding.per_turn


class TestMainIsNeverRed:
    """``c36``/``h24``: the test and the raises land in one commit."""

    def test_every_audited_constant_passes_at_this_commit(self, config: rc.RateConfig) -> None:
        failures = [
            evaluate(clock, config).message() for clock in CLOCKS if not evaluate(clock, config).ok
        ]
        assert not failures, "\n".join(failures)

    def test_the_two_constants_this_task_raised_are_the_ones_that_were_below_bound(
        self, config: rc.RateConfig
    ) -> None:
        """Named, so the atomicity claim is checkable rather than asserted.

        ``worker_seam.REQUEST_TIMEOUT`` shipped 300.0 and
        ``orchestrator_tools.DEFAULT_FANOUT_TIMEOUT`` shipped 60.0 on main
        before this commit; ``league_commander.REQUEST_TIMEOUT`` shipped 900.0
        and read as passing only because its bound had been derived at the
        wrong model. All three now clear.
        """
        raised = {
            "worker_seam.REQUEST_TIMEOUT",
            "league_commander.REQUEST_TIMEOUT",
            "orchestrator_tools.DEFAULT_FANOUT_TIMEOUT",
        }
        assert raised <= set(CLOCK_IDS)
        for clock in CLOCKS:
            if clock.id in raised:
                assert evaluate(clock, config).ok, clock.id


# ── criterion 4: every constant carries its derivation ───────────────────────


def comment_above(path: Path, constant: str) -> str:
    """The contiguous comment block immediately above a module-level assignment.

    Located through the AST rather than by regex, so what is read is the
    comment above the assignment the interpreter will actually execute — not
    the first line in the file that happens to mention the name.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines), filename=str(path))
    target: Optional[int] = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(name, ast.Name) and name.id == constant for name in node.targets
        ):
            target = node.lineno
    if target is None:
        raise AssertionError(f"{path.name} has no module-level assignment to {constant}")

    collected: list[str] = []
    index = target - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        collected.append(lines[index].lstrip().lstrip("#:").strip())
        index -= 1
    return "\n".join(reversed(collected))


class TestEveryConstantCitesItsDerivation:
    """``c4``/``h4``: a value with no citable inputs fails the test."""

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_the_comment_names_the_budget_the_rate_source_and_the_derivation(
        self, clock: Clock
    ) -> None:
        comment = comment_above(clock.source_path, clock.constant)
        assert comment.strip(), f"{clock.id} carries no module-level comment"

        missing = [
            needle
            for needle in (rc.DEFAULT_CONFIG_PATH.name, "max_tokens", "bound")
            if needle not in comment
        ]
        assert not missing, (
            f"{clock.id}'s comment never mentions {missing}. A timeout whose comment "
            "does not name its budget, its rate source and the derivation is a number "
            "someone chose (claim c4)."
        )

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_the_comment_names_every_role_the_constant_fronts(self, clock: Clock) -> None:
        """Finding 1 again, this time against the prose a reader will actually read."""
        comment = comment_above(clock.source_path, clock.constant)
        for role in sorted({fronted.role for fronted in clock.fronts} | set(clock.unmeasured)):
            assert role in comment, (
                f"{clock.id}'s comment never mentions the {role!r} role, which it "
                "fronts. That omission is exactly how 900 s came to be read as passing."
            )

    @pytest.mark.parametrize("clock", CLOCKS, ids=CLOCK_IDS)
    def test_the_comment_says_how_queue_time_is_handled(self, clock: Clock) -> None:
        """Finding 2: never left unaddressed, in the config *or* the comment."""
        comment = comment_above(clock.source_path, clock.constant).lower()
        assert "queue" in comment, (
            f"{clock.id}'s comment never says what happens to non-generation time. "
            "The bound divides tokens by tok/s; the clock also covers queue wait and "
            "prefill, and one committed call spent 179 s there."
        )


# ── the surface the walk claims to cover ─────────────────────────────────────


#: #42's audit table, which this plan's task ``t2`` was scoped against. Held as
#: data so the walk can be checked to be a *superset* of it — the audit is a
#: floor on coverage, never a ceiling.
AUDITED_BY_ISSUE_42 = (
    "worker_seam.REQUEST_TIMEOUT",
    "league_commander.REQUEST_TIMEOUT",
    "league_h2h.REQUEST_TIMEOUT",
    "devague_legs.REQUEST_TIMEOUT_S",
    "muse_arms.REQUEST_TIMEOUT_S",
    "worker_throughput.BATCH_WAIT_TIMEOUT_SECONDS",
    "orchestrator_tools.DEFAULT_FANOUT_TIMEOUT",
)


class TestTheWalkCoversTheAuditedSurface:
    """The seven audited constants, plus anything the AST guard turns up."""

    def test_all_seven_audited_constants_are_walked(self) -> None:
        missing = sorted(set(AUDITED_BY_ISSUE_42) - set(CLOCK_IDS))
        assert not missing, f"audited constants missing from the walk: {missing}"

    def test_the_walk_is_allowed_to_be_larger_than_the_audit(self) -> None:
        """And here it is: the audit's list was seven and the surface is eight.

        `worker_scoped_overhead.BATCH_WAIT_TIMEOUT_SECONDS` was not in #42's
        table and not in the plan's task text. It was found by the AST
        completeness guard below — which is the difference between a list
        somebody maintains and a category that closes itself.
        """
        extra = sorted(set(CLOCK_IDS) - set(AUDITED_BY_ISSUE_42))
        assert extra == ["worker_scoped_overhead.BATCH_WAIT_TIMEOUT_SECONDS"]

    def test_every_walked_constant_actually_exists_and_is_a_number(self) -> None:
        for clock in CLOCKS:
            assert isinstance(clock.shipped(), float)

    def test_every_declared_pair_names_how_the_model_reaches_the_clock(self) -> None:
        """A declaration nobody can check is a comment. Each pair says its path."""
        for clock in CLOCKS:
            assert clock.fronts, f"{clock.id} declares no model at all"
            for fronted in clock.fronts:
                assert fronted.why.strip(), f"{clock.id}: {fronted.role} has no stated path"

    def test_no_timeout_constant_in_examples_escapes_the_walk(self) -> None:
        """The category is closed by AST, not by memory.

        Every module-level float whose name looks like a client timeout or a
        wait deadline must be walked. A new harness that adds one and forgets
        this file fails here rather than shipping an underived clock — which is
        how ``DEFAULT_FANOUT_TIMEOUT`` escaped #42's audit in the first place.

        The streaming clocks ``t5`` added are walked too, by
        :class:`TestStreamingBounds` rather than by :data:`CLOCKS` — their bound
        is not ``max_tokens / tok_s``, so they get their own derivation. They
        count as walked here; being under *a* derivation is the property this
        guard is closing over.
        """
        walked = {(clock.module.rsplit(".", 1)[-1], clock.constant) for clock in CLOCKS}
        walked |= {(clock.module.rsplit(".", 1)[-1], clock.constant) for clock in STREAM_CLOCKS}
        stray = [
            f"{module}.{name}"
            for module, name, _ in _module_level_floats(_TIMEOUT_NAME_HINTS)
            if (module, name) not in walked and (module, name) not in _NOT_A_MODEL_CLOCK
        ]
        assert not stray, (
            f"these look like clocks in front of a model call and are not walked: {stray}. "
            "Add them to CLOCKS, or to _NOT_A_MODEL_CLOCK with a stated reason."
        )


#: Substrings that make a module-level float a candidate clock.
_TIMEOUT_NAME_HINTS = ("TIMEOUT", "WAIT_TIMEOUT", "DEADLINE")

#: Floats whose names match the hints but which do not bound a model call.
#: Each entry is a decision, recorded here rather than left to a reader.
_NOT_A_MODEL_CLOCK: Mapping[tuple[str, str], str] = {
    ("league_commander", "CONTENTION_SECONDS"): (
        "not a clock at all — nothing waits on it. It is the threshold above which a "
        "completed call is *labelled* contended in the record, so it can only ever "
        "annotate a result, never discard one."
    ),
}


def _walked_files(root: Path) -> list[Path]:
    """Every ``.py`` file the clock guard reads under *root*.

    Split out of :func:`_module_level_floats` by ``t10`` for one reason: the
    ``glob`` -> ``rglob`` widening that brought ``examples/scope/`` inside the
    guard was itself unproven. A walk that silently stops at the top level
    reads exactly like a walk that found nothing, and this repo has shipped a
    guard that checked nothing before (`corrections.md` §3). Naming the
    enumeration makes it assertable — see
    :class:`TestTheWalkReachesEverySubfolder`.
    """
    # rglob, not glob: per-architecture subfolders (examples/scope/) are inside the
    # guard too. A non-recursive walk let five files escape it silently, which is the
    # exact shape of the failure this whole module exists to prevent.
    return sorted(root.rglob("*.py"))


def _module_level_floats(
    hints: Sequence[str],
    root: Path = EXAMPLES_DIR,
) -> list[tuple[str, str, float]]:
    """``(module stem, constant, value)`` for module-level float assignments matching *hints*.

    *root* defaults to ``examples/`` so every existing caller is unchanged.
    ``t10`` added the parameter so the identical AST walk can be pointed at
    ``embodiment/`` — the package ships module-level clocks of its own and no
    guard covered them — and at a ``tmp_path`` for the walk's own tests.
    """
    found: list[tuple[str, str, float]] = []
    for path in _walked_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, float):
                continue
            for name in node.targets:
                if isinstance(name, ast.Name) and any(hint in name.id for hint in hints):
                    found.append((path.stem, name.id, node.value.value))
    return found


# ── finding 3: the third clock category ──────────────────────────────────────


@dataclass(frozen=True)
class Backoff:
    """A fixed retry backoff, and the reason it is exempt from the bound.

    Task ``t8``'s finding, brought under the same derive-or-justify rule the
    six client timeouts are under. The justification is the same for all of
    them and it is a real one rather than a shrug: **a backoff bounds no token
    budget.** ``max_tokens / tok_s`` answers "how long may generating take";
    a backoff answers "how long should we wait for a transient condition to
    clear", and nothing committed in this repo measures how long that is. A
    derived-looking number would be an invented one.

    What is *not* exempt is the accounting. Every one of these sleeps is timed
    inside its own call's stopwatch — ``started`` is set before the attempt
    loop and never reset — so a retried call reports its backoff as latency.
    That has corrupted three separate measurements: nine calls in
    `worker-throughput.jsonl` (`corrections.md` §10), the cortex rate the
    pre-registration first published (§5), and t8's scoped-overhead probe,
    where it would have published concurrency making calls *slower*. The
    property that makes it recoverable — and therefore the property this test
    pins — is that the same record carries a retry **count**, so the overhead
    is exactly ``retries × (timeout + backoff)`` and can be subtracted.
    """

    module: str
    constant: str
    attempts_constant: str
    timeout_constant: Optional[str]
    retry_field: str
    why_exempt: str


BACKOFFS: tuple[Backoff, ...] = (
    Backoff(
        module="examples.worker_seam",
        constant="RETRY_SLEEP_SECONDS",
        attempts_constant="MAX_TRANSPORT_RETRIES",
        timeout_constant="REQUEST_TIMEOUT",
        retry_field="retries",
        why_exempt="bounds no token budget; recoverable because Meter.retries is recorded "
        "per call and the arithmetic is exact",
    ),
    Backoff(
        module="examples.league_h2h",
        constant="RETRY_SLEEP_SECONDS",
        attempts_constant="MAX_TRANSPORT_RETRIES",
        timeout_constant="REQUEST_TIMEOUT",
        retry_field="retries",
        why_exempt="same seam shape, same accounting: MeteredSeam records retries per call",
    ),
    Backoff(
        module="examples.league_commander",
        constant="RETRY_WAIT_SECONDS",
        attempts_constant="MAX_RETRIES",
        timeout_constant="REQUEST_TIMEOUT",
        retry_field="retries",
        why_exempt="30 s rather than 20 s, and that difference is load-bearing in the "
        "exhausted-call arithmetic corrections.md §9 used to prove the series clean",
    ),
    Backoff(
        module="examples.worker_scoped_overhead",
        constant="RETRY_BACKOFF_SECONDS",
        attempts_constant="MAX_TRANSPORT_RETRIES",
        timeout_constant=None,
        retry_field="retries",
        why_exempt="a restatement of worker_seam's value for reporting, pinned equal to it "
        "by that module's own suite; it configures nothing",
    ),
)

BACKOFF_IDS = [f"{backoff.module.rsplit('.', 1)[-1]}.{backoff.constant}" for backoff in BACKOFFS]


class TestBackoffConstants:
    """Finding 3: declared and justified, never silent."""

    def test_every_backoff_in_examples_is_declared(self) -> None:
        declared = {(backoff.module.rsplit(".", 1)[-1], backoff.constant) for backoff in BACKOFFS}
        stray = [
            f"{module}.{name}"
            for module, name, _ in _module_level_floats(("SLEEP", "BACKOFF", "RETRY_WAIT"))
            if (module, name) not in declared
        ]
        assert not stray, (
            f"undeclared retry backoff constant(s): {stray}. A backoff is exempt from the "
            "budget bound, not from the rule — declare it in BACKOFFS with its reason."
        )

    @pytest.mark.parametrize("backoff", BACKOFFS, ids=BACKOFF_IDS)
    def test_the_exemption_is_stated(self, backoff: Backoff) -> None:
        assert backoff.why_exempt.strip()

    @pytest.mark.parametrize("backoff", BACKOFFS, ids=BACKOFF_IDS)
    def test_the_backoff_is_recoverable_from_the_record(self, backoff: Backoff) -> None:
        """The property the exemption rests on.

        A fixed backoff timed inside a call's own stopwatch is acceptable
        *because* the record says how many times it happened. Take the count
        away and the contamination becomes unrecoverable — which is the one
        change that would make the exemption unsafe.
        """
        module = importlib.import_module(backoff.module)
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        assert backoff.retry_field in source, (
            f"{backoff.module} sleeps {backoff.constant} inside its own stopwatch but "
            f"never records {backoff.retry_field!r}. Without the count the overhead is "
            "unrecoverable and every rate the harness reports is wrong by an unknown amount."
        )

    @pytest.mark.parametrize("backoff", BACKOFFS, ids=BACKOFF_IDS)
    def test_the_backoff_is_small_beside_the_timeout_it_sits_between(
        self, backoff: Backoff
    ) -> None:
        """A backoff longer than the call it retries is a schedule, not a backoff."""
        if backoff.timeout_constant is None:
            pytest.skip("this constant configures no transport")
        module = importlib.import_module(backoff.module)
        sleep = float(getattr(module, backoff.constant))
        timeout = float(getattr(module, backoff.timeout_constant))
        assert 0 < sleep < timeout

    @pytest.mark.parametrize("backoff", BACKOFFS, ids=BACKOFF_IDS)
    def test_the_exhausted_call_identity_is_what_the_corrections_arithmetic_uses(
        self, backoff: Backoff
    ) -> None:
        """``attempts × timeout + (attempts − 1) × backoff`` — the tell for a cut call.

        Asserted because two published re-exams turn on it: `corrections.md`
        §5's ``4 × 300 + 3 × 20 = 1260`` and §9's ``4 × 900 + 3 × 30 = 3690``.
        Raising a timeout moves this number, and a re-exam that kept the old
        one would be looking for a signature the harness no longer produces.
        """
        if backoff.timeout_constant is None:
            pytest.skip("this constant configures no transport")
        module = importlib.import_module(backoff.module)
        attempts = int(getattr(module, backoff.attempts_constant)) + 1
        sleep = float(getattr(module, backoff.constant))
        timeout = float(getattr(module, backoff.timeout_constant))
        exhausted = attempts * timeout + (attempts - 1) * sleep
        assert exhausted > timeout
        assert exhausted == attempts * timeout + (attempts - 1) * sleep


class TestClockLayering:
    """The raises must not reorder which clock speaks first.

    A dead endpoint should surface as a *transport* failure — which names the
    fault — rather than as ``fanout-unit-absent``, which names a straggler. That
    holds only while a unit's whole retry ladder still fits inside the fan-out
    deadline, and both numbers moved in this commit.
    """

    def test_a_units_exhausted_retry_ladder_still_fits_inside_the_fanout_deadline(self) -> None:
        import examples.orchestrator_tools as ot
        import examples.worker_seam as ws

        attempts = ws.MAX_TRANSPORT_RETRIES + 1
        exhausted = attempts * ws.REQUEST_TIMEOUT + (attempts - 1) * ws.RETRY_SLEEP_SECONDS
        assert exhausted < ot.DEFAULT_FANOUT_TIMEOUT, (
            f"a unit whose endpoint is dead now takes {exhausted:.0f} s to say so, past "
            f"the {ot.DEFAULT_FANOUT_TIMEOUT:.0f} s fan-out deadline — so the failure "
            "would be recorded as fanout-unit-absent, which names a straggler rather "
            "than a dead transport. Amendment 2 states this layering explicitly."
        )

    def test_the_batch_wait_deliberately_does_not_cover_a_retrying_call(self) -> None:
        """Stated rather than accidental, because it looks like a defect.

        ``worker_throughput``'s batch wait clears the bound for a *healthy*
        full-budget call at every width it dials, and is far below one retry
        cycle. That is the module's own choice — a batch that takes longer than
        any healthy call in the series is a stability finding, reported as a
        timeout rather than absorbed by waiting longer — and it is asserted so
        that a later raise of ``REQUEST_TIMEOUT`` cannot quietly turn the
        circuit breaker into a censor without this failing first.
        """
        import examples.worker_seam as ws
        import examples.worker_throughput as wt

        one_retry_cycle = ws.REQUEST_TIMEOUT + ws.RETRY_SLEEP_SECONDS
        assert wt.BATCH_WAIT_TIMEOUT_SECONDS < one_retry_cycle


# ── the streaming bounds: two phases, both derived ───────────────────────────


#: The seam whose per-request bound the queue model multiplies. Named rather
#: than indexed so a reordering of :data:`CLOCKS` cannot silently change which
#: bound the streaming clocks are built on.
STREAM_SEAM_CLOCK_ID = "worker_seam.REQUEST_TIMEOUT"

#: The committed probe the inter-chunk bound cites. Read, never retyped.
STREAM_PROBE_RECORD = "stream-probe.json"

#: The margin policy for the inter-chunk idle bound, as a number this file
#: owns. Not derived — it is the answer to "how much slower than anything
#: measured may a healthy stream get before we call it dead", and nothing
#: measures that. Stated at 100x, which both derivations clear by a further
#: factor of 3-5; the shipped 60 s carries 484x over the probe's largest
#: observed gap and 310x over the slowest committed per-stream rate.
MIN_INTER_CHUNK_MARGIN = 100.0


@dataclass(frozen=True)
class StreamClock:
    """One streaming clock, and the committed inputs its floor comes from.

    Separate from :class:`Clock` because the bound is a different shape. A
    client timeout is ``max_tokens / tok_s``: a *generation* bound. These two
    are not — the queue bound multiplies a per-request bound by a queue depth
    read off the deployment, and the idle bound divides one into a chunk
    cadence. Same discipline (committed inputs only, ``h5``), different
    arithmetic, so a different walk rather than a special case inside the old
    one.
    """

    module: str
    constant: str
    phase: str
    why: str

    @property
    def id(self) -> str:
        return f"{self.module.rsplit('.', 1)[-1]}.{self.constant}"

    @property
    def source_path(self) -> Path:
        return EXAMPLES_DIR / f"{self.module.rsplit('.', 1)[-1]}.py"

    def shipped(self) -> float:
        return float(getattr(importlib.import_module(self.module), self.constant))


STREAM_CLOCKS: tuple[StreamClock, ...] = (
    StreamClock(
        module="examples.worker_seam",
        constant="STREAM_FIRST_CHUNK_TIMEOUT",
        phase="queue",
        why="phase one: how long a request may receive NOTHING before it is scheduled. "
        "Derived from the queue model, never measured — both streaming-probe dials ran "
        "against an idle cortex and say nothing about a queued request.",
    ),
    StreamClock(
        module="examples.worker_seam",
        constant="STREAM_IDLE_TIMEOUT",
        phase="idle",
        why="phase two: the largest gap between chunks a live stream may show. Armed on "
        "the socket only after the first chunk, so queue wait is never charged as idle.",
    ),
    StreamClock(
        module="examples.worker_seam",
        constant="STREAM_TOTAL_TIMEOUT",
        phase="total",
        why="the outer backstop the deviation instruction keeps: a stream that dribbles "
        "one chunk just inside the idle bound forever trips neither phase clock.",
    ),
)

STREAM_CLOCK_IDS = [clock.id for clock in STREAM_CLOCKS]


def derived_per_request_bound(config: rc.RateConfig) -> float:
    """The seam's own derived bound — the quantity a queued request waits through."""
    clock = next(entry for entry in CLOCKS if entry.id == STREAM_SEAM_CLOCK_ID)
    return evaluate(clock, config).bound


def slowest_committed_rate(config: rc.RateConfig) -> float:
    """The slowest per-stream generation rate any role was measured at.

    Read from the config rather than typed here, per ``c39`` — and it is the
    honest input for a *cadence* bound because it is the condition the probe
    could not create: the probe ran on an idle cortex, while these rates were
    measured with neighbours in flight, where a stream's own cadence halves.
    """
    return min(measurement.slowest_tok_s for measurement in config.roles.values())


def measured_max_inter_chunk_gap() -> float:
    """The largest inter-chunk gap in the committed streaming probe."""
    payload = json.loads((RESULTS_DIR / STREAM_PROBE_RECORD).read_text(encoding="utf-8"))
    gaps = [
        float(run["max_inter_chunk_gap"])
        for run in payload
        if isinstance(run, Mapping) and run.get("max_inter_chunk_gap") is not None
    ]
    assert gaps, f"{STREAM_PROBE_RECORD} records no inter-chunk gap to derive from"
    return max(gaps)


def stream_bound_floor(clock: StreamClock, config: rc.RateConfig) -> float:
    """The derived floor for one streaming clock, from committed inputs only."""
    import examples.worker_seam as ws

    if clock.phase == "queue":
        waiters = ws.SERVER_MAX_NUM_SEQS - 1
        return ws.STREAM_QUEUE_MARGIN * (
            waiters * derived_per_request_bound(config) + config.non_generation_allowance.seconds
        )
    if clock.phase == "idle":
        cadence = max(measured_max_inter_chunk_gap(), 1.0 / slowest_committed_rate(config))
        return MIN_INTER_CHUNK_MARGIN * cadence
    return ws.STREAM_FIRST_CHUNK_TIMEOUT + ws.REQUEST_TIMEOUT


def stream_clock_ok(clock: StreamClock, config: rc.RateConfig) -> bool:
    """``>=`` its derived floor. One function, so the walk and its mutation agree."""
    return clock.shipped() >= stream_bound_floor(clock, config)


class TestStreamingBounds:
    """``c38``/``h26``: no phase of a streamed call is bounded by an underived constant.

    The failure this walk exists to prevent is specific and was named before it
    could happen: a fixed idle clock applied from ``t = 0`` would kill a request
    that is legitimately queued behind another sequence — the same censoring
    shape as the 300 s and 600 s constants, wearing a new clock. So the bound is
    two-phase, and both phases derive from things this repo has committed.
    """

    @pytest.mark.parametrize("clock", STREAM_CLOCKS, ids=STREAM_CLOCK_IDS)
    def test_the_constant_clears_its_derived_floor(
        self, clock: StreamClock, config: rc.RateConfig
    ) -> None:
        assert stream_clock_ok(clock, config), (
            f"{clock.id} = {clock.shipped()} but its derived floor is "
            f"{stream_bound_floor(clock, config):.2f} s. {clock.why}"
        )

    @pytest.mark.parametrize("clock", STREAM_CLOCKS, ids=STREAM_CLOCK_IDS)
    def test_one_constant_mutated_below_its_floor_goes_red(
        self, clock: StreamClock, config: rc.RateConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The test of the test, on the new clocks too — through the same path.

        The mutation is applied to the module attribute the walk reads, so what
        is proven is that this file's route to the constant is live, not that an
        inequality between two locals holds.
        """
        floor = stream_bound_floor(clock, config)
        monkeypatch.setattr(importlib.import_module(clock.module), clock.constant, floor - 1.0)
        assert not stream_clock_ok(clock, config)

    @pytest.mark.parametrize("clock", STREAM_CLOCKS, ids=STREAM_CLOCK_IDS)
    def test_exactly_at_the_floor_passes(
        self, clock: StreamClock, config: rc.RateConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: the mutation harness is not simply always-red."""
        floor = stream_bound_floor(clock, config)
        monkeypatch.setattr(importlib.import_module(clock.module), clock.constant, floor)
        assert stream_clock_ok(clock, config)

    def test_the_queue_depth_is_the_deployments_and_not_a_guess(
        self, config: rc.RateConfig
    ) -> None:
        """``--max-num-seqs`` is a fact about the server, so it is read from the config.

        The cortex measurement records it because that is the server the figure
        was taken against; the harness constant must equal it, or the queue
        model is built on a number nobody checked.
        """
        import examples.worker_seam as ws

        recorded = config.rate("cortex").server_max_num_seqs
        assert recorded is not None, (
            "the cortex rate entry no longer records server_max_num_seqs, so the "
            "time-to-first-chunk bound has no committed queue depth to derive from"
        )
        assert ws.SERVER_MAX_NUM_SEQS == recorded

    def test_the_prefill_allowance_is_the_committed_one(self, config: rc.RateConfig) -> None:
        import examples.worker_seam as ws

        assert ws.STREAM_PREFILL_ALLOWANCE_SECONDS == config.non_generation_allowance.cited_as

    def test_the_cited_chunk_gap_is_the_measured_one(self) -> None:
        """A figure retyped beside its derivation is a figure that can drift."""
        import examples.worker_seam as ws

        assert ws.MEASURED_MAX_INTER_CHUNK_GAP_SECONDS == measured_max_inter_chunk_gap()

    def test_the_idle_bound_clears_both_of_its_derivations(self, config: rc.RateConfig) -> None:
        """Probe-measured cadence AND committed contended rates, not one of them.

        The probe ran on an idle cortex; the committed rates were measured with
        neighbours in flight. Either alone would be a bound sized against one
        condition, and the contended one is slower — so it is the binding term.
        """
        import examples.worker_seam as ws

        from_probe = ws.STREAM_IDLE_TIMEOUT / measured_max_inter_chunk_gap()
        from_rates = ws.STREAM_IDLE_TIMEOUT * slowest_committed_rate(config)
        assert from_probe >= MIN_INTER_CHUNK_MARGIN
        assert from_rates >= MIN_INTER_CHUNK_MARGIN
        assert from_rates < from_probe, (
            "the contended-rate derivation is expected to be the tighter of the two; if "
            "it is not, the probe has become the binding term and the margin policy "
            "should be re-read rather than assumed"
        )

    def test_the_total_backstop_is_the_two_phases_summed(self) -> None:
        import examples.worker_seam as ws

        assert ws.STREAM_TOTAL_TIMEOUT == ws.STREAM_FIRST_CHUNK_TIMEOUT + ws.REQUEST_TIMEOUT

    def test_the_phases_are_ordered_so_each_clock_can_speak(self) -> None:
        """A phase bound that is not tighter than the one outside it is decoration."""
        import examples.worker_seam as ws

        assert ws.STREAM_IDLE_TIMEOUT < ws.REQUEST_TIMEOUT
        assert ws.REQUEST_TIMEOUT < ws.STREAM_FIRST_CHUNK_TIMEOUT
        assert ws.STREAM_FIRST_CHUNK_TIMEOUT < ws.STREAM_TOTAL_TIMEOUT

    def test_the_queue_bound_grows_with_the_width_the_caller_dials(self) -> None:
        """``c38``'s model, exercised rather than described.

        A harness with 8 requests in flight against a server admitting 2 has 6
        of its own siblings queued ahead of the last one, and the bound must
        move with that. The shipped constant is the width-1 value — a wider dial
        that keeps it is knowingly under-bounded, which is why the seam takes
        ``stream_queue_width`` rather than reading the constant.
        """
        import examples.worker_seam as ws

        widths = [1, 2, 8, 14]
        bounds = [ws.derive_first_chunk_timeout(dialled_width=width) for width in widths]
        assert bounds == sorted(bounds)
        assert bounds[0] == ws.STREAM_FIRST_CHUNK_TIMEOUT
        assert ws.derive_first_chunk_timeout(dialled_width=14) > bounds[0]

    def test_the_margin_is_a_margin(self) -> None:
        import examples.worker_seam as ws

        assert ws.STREAM_QUEUE_MARGIN >= 1.0

    @pytest.mark.parametrize("clock", STREAM_CLOCKS, ids=STREAM_CLOCK_IDS)
    def test_the_comment_names_the_inputs_the_derivation_uses(self, clock: StreamClock) -> None:
        """``c4``/``h4`` again, on the clocks that replaced the total deadline."""
        comment = comment_above(clock.source_path, clock.constant)
        assert comment.strip(), f"{clock.id} carries no module-level comment"
        lowered = comment.lower()
        for needle in ("queue", "bound", "margin"):
            assert needle in lowered, (
                f"{clock.id}'s comment never mentions {needle!r}. These two clocks "
                "replaced a total-request deadline; a reader has to be able to see what "
                "each phase is bounded by and where the margin came from."
            )
        assert rc.DEFAULT_CONFIG_PATH.name in comment or STREAM_PROBE_RECORD in comment, (
            f"{clock.id}'s comment cites neither {rc.DEFAULT_CONFIG_PATH.name} nor "
            f"{STREAM_PROBE_RECORD} — the two committed inputs these bounds derive from"
        )

    @pytest.mark.parametrize("clock", STREAM_CLOCKS, ids=STREAM_CLOCK_IDS)
    def test_the_comment_names_every_role_the_clock_fronts(self, clock: StreamClock) -> None:
        """The same seam, so the same roles — including the unmeasured one.

        ``worker_seam.REQUEST_TIMEOUT`` fronts worker, cortex and the unmeasured
        senses role; these clocks sit on the identical wire, so an omission here
        would be exactly the finding-1 mistake that read 900 s as passing.
        """
        comment = comment_above(clock.source_path, clock.constant)
        if clock.phase == "total":
            pytest.skip("the total backstop's roles are the two phases' it sums")
        for role in ("worker", "cortex", "senses"):
            assert role in comment, f"{clock.id}'s comment never mentions the {role!r} role"


class TestStreamingClockLayering:
    """Streaming moved the governing per-call clock. The layering must be re-checked.

    ``TestClockLayering`` proves a dead endpoint surfaces as a *transport*
    failure rather than as ``fanout-unit-absent``, and it proves it against
    ``REQUEST_TIMEOUT``. Under streaming that is no longer the clock a stuck
    call waits on: the first-chunk bound is, and it is larger. The property has
    to hold on the new clock or the raise quietly reintroduced the fan-out
    censoring it was written to prevent.
    """

    def test_the_streaming_ladder_still_fits_inside_the_fanout_deadline(self) -> None:
        import examples.orchestrator_tools as ot
        import examples.worker_seam as ws

        # The queue bound is measured from the START of the call, not per
        # attempt, so the ladder is one queue bound plus the backoffs between
        # attempts — not four of them. That choice is what keeps this assertion
        # comfortable instead of marginal.
        exhausted = (
            ws.STREAM_FIRST_CHUNK_TIMEOUT + ws.MAX_TRANSPORT_RETRIES * ws.RETRY_SLEEP_SECONDS
        )
        assert exhausted < ot.DEFAULT_FANOUT_TIMEOUT, (
            f"a unit whose endpoint never sends a byte now takes {exhausted:.0f} s to say "
            f"so, past the {ot.DEFAULT_FANOUT_TIMEOUT:.0f} s fan-out deadline — so it "
            "would be recorded as fanout-unit-absent, which names a straggler rather "
            "than a dead transport."
        )

    def test_the_queue_bound_is_not_multiplied_by_the_retry_ladder(self) -> None:
        """The property the assertion above rests on, stated as its own test."""
        import examples.worker_seam as ws

        per_attempt_ladder = (ws.MAX_TRANSPORT_RETRIES + 1) * ws.STREAM_FIRST_CHUNK_TIMEOUT
        from_call_start = ws.STREAM_FIRST_CHUNK_TIMEOUT + (
            ws.MAX_TRANSPORT_RETRIES * ws.RETRY_SLEEP_SECONDS
        )
        assert from_call_start < per_attempt_ladder

    def test_every_concurrent_dial_is_governed_by_its_own_batch_wait(self) -> None:
        """The answer to "isn't the width-1 queue bound wrong for a wide dial?".

        It would be, if it were the governing clock. It is not. All three
        harnesses that dial this seam concurrently — ``worker_throughput``,
        ``worker_scoped_overhead`` and ``arch_hive`` — cap a batch at 300 s,
        an order of magnitude below the queue bound, so their own circuit
        breaker always speaks first and the shipped width-1 constant is never
        reached. That is each harness's declared choice (a batch slower than any
        healthy call is a stability finding, reported rather than waited out),
        and it is asserted here so a later raise of any of the four numbers
        cannot make the relationship accidental. A harness that *does* raise its
        batch wait past this must pass ``stream_queue_width`` to the seam, which
        recomputes the bound for the width it actually dials.
        """
        import examples.worker_scoped_overhead as wso
        import examples.worker_seam as ws
        import examples.worker_throughput as wt

        hive = json.loads((RESULTS_DIR / ARCH_HIVE_SAMPLING).read_text(encoding="utf-8"))
        hive_batch = float(hive["dispatch"]["batch_timeout_seconds"])

        for label, batch in (
            ("worker_throughput", wt.BATCH_WAIT_TIMEOUT_SECONDS),
            ("worker_scoped_overhead", wso.BATCH_WAIT_TIMEOUT_SECONDS),
            ("arch_hive", hive_batch),
        ):
            assert batch < ws.STREAM_FIRST_CHUNK_TIMEOUT, label


# ── the gap, kept visible ────────────────────────────────────────────────────


class TestUnmeasuredRolesStayVisible:
    """``C3``: a degradation the host cannot see is the worst available outcome.

    ``worker_seam.REQUEST_TIMEOUT`` fronts the senses role too, and no
    committed record times that model. The bound is therefore proven against
    the cortex and the worker and **not** against a full-budget senses turn.
    That is said out loud here, in the config, and in the module comment —
    rather than hidden by a walk that simply omits the pair.
    """

    def test_every_declared_gap_is_recorded_in_the_config(self, config: rc.RateConfig) -> None:
        for clock in CLOCKS:
            for role in clock.unmeasured:
                assert role in config.unmeasured_roles, (
                    f"{clock.id} declares {role!r} unmeasured but "
                    f"{rc.DEFAULT_CONFIG_PATH.name} says nothing about it"
                )

    def test_the_config_says_which_constant_the_gap_belongs_to(self, config: rc.RateConfig) -> None:
        for clock in CLOCKS:
            for role in clock.unmeasured:
                fronted_by = config.unmeasured_roles[role].fronted_by
                assert any(clock.constant in entry for entry in fronted_by)

    def test_the_gap_closes_the_moment_the_role_is_measured(self, config: rc.RateConfig) -> None:
        """Self-closing: a measured role may not stay on the unmeasured list.

        The loader refuses a config carrying a role in both places, so the
        first committed senses rate forces the pair into the walk above instead
        of leaving a stale hole recorded beside a filled one.
        """
        for role in config.unmeasured_roles:
            assert role not in config.roles


# ══════════════════════════════════════════════════════════════════════════════
# t10 — the scope lane: the walk's reach, the package's own clocks, and the
# strategist defaults' trace back to the committed config.
#
# Plan ``strategic-scope-governor`` task ``t10``, covering ``c10``/``h9``. Three
# acceptance criteria, each proved in its own class below:
#
# 1. every timeout constant under ``examples/scope/`` is a walked ``Clock``, and
#    the AST guard fails any that is not — :class:`TestTheWalkReachesEverySubfolder`
#    and :class:`TestTheScopeLaneIntroducesNoClock`;
# 2. the ``worker`` role carries a measured, dated rate entry, and the divisor
#    the scoped-run calling pattern uses is the conservative reading rather than
#    the flattering one (plan risk ``r1``) —
#    :class:`TestTheScopedRunDivisorIsTheConservativeReading`;
# 3. the strategist runner's staleness and cadence defaults cite the measured
#    strategist latency, and that citation resolves to a figure this config
#    actually publishes — :class:`TestTheStrategistDefaultsTraceToTheConfig`.
#
# These live in THIS module rather than a new one on purpose.
# ``tests/test_rate_config.py``'s ``RATE_DERIVING_MODULES`` is an explicit list,
# and a new file would have been outside it — a rate-deriving test module the
# c39 no-literal guard does not walk is the staleness defect wearing a new file
# name. Everything below reads its numbers from the loaded config.
# ══════════════════════════════════════════════════════════════════════════════


class TestTheWalkReachesEverySubfolder:
    """The guard's reach, proved rather than assumed.

    ``glob`` became ``rglob`` in commit ``9c2eee9`` so ``examples/scope/`` would
    stop being invisible to the clock guard — five files had escaped it
    silently. That commit shipped the widening with no test, and a walk that
    stops at the top level is indistinguishable from a walk that found nothing.
    Proving the reach is the difference between coverage and the appearance of
    it, which is the whole argument of this module.
    """

    def test_the_walk_enumerates_files_inside_examples_scope(self) -> None:
        walked = _walked_files(EXAMPLES_DIR)
        inside = [path for path in walked if path.parent == SCOPE_DIR]
        assert inside, (
            f"the clock guard enumerates {len(walked)} file(s) under examples/ and none "
            f"of them is in {SCOPE_DIR.name}/. The scope lane is outside the guard."
        )

    def test_the_walk_sees_every_python_file_the_scope_lane_ships(self) -> None:
        shipped = sorted(SCOPE_DIR.glob("*.py"))
        assert shipped, "examples/scope/ ships no python at all; this guard is vacuous"
        walked = set(_walked_files(EXAMPLES_DIR))
        missing = sorted(str(path) for path in shipped if path not in walked)
        assert not missing, f"outside the clock guard: {missing}"

    def test_a_clock_planted_in_a_subfolder_is_found(self, tmp_path: Path) -> None:
        """The test of the test, on a throwaway tree rather than the repo's."""
        nested = tmp_path / "arch" / "deeper"
        nested.mkdir(parents=True)
        (nested / "harness.py").write_text("REQUEST_TIMEOUT = 300.0\n", encoding="utf-8")

        found = _module_level_floats(_TIMEOUT_NAME_HINTS, root=tmp_path)
        assert found == [("harness", "REQUEST_TIMEOUT", 300.0)]

    def test_a_non_recursive_walk_would_have_missed_it(self, tmp_path: Path) -> None:
        """The widening was load-bearing, stated as a comparison.

        Without this, "we use rglob" is a claim about a spelling. With it, the
        spelling is tied to the property it buys.
        """
        nested = tmp_path / "scope"
        nested.mkdir()
        (nested / "harness.py").write_text("REQUEST_TIMEOUT = 300.0\n", encoding="utf-8")

        assert sorted(tmp_path.glob("*.py")) == []
        assert _walked_files(tmp_path)


class TestTheScopeLaneIntroducesNoClock:
    """Criterion 1, and the honest answer to it: the scope lane ships none.

    That is not this task ducking the criterion — it is a **committed
    pre-registration**. ``docs/live-test-results/scopebench-preregistration.md``
    §14 states that no module under ``examples/scope/`` imports a transport,
    reaches ``embodiment.loop``, or introduces a timeout constant, and
    ``tests/test_scopebench.py::TestNoLiveDial`` asserts all three by AST. A
    clock added here by ``t10`` would break a pre-registration that was
    committed before any result exists, which is a far worse trade than a walk
    that is currently empty.

    So what ``t10`` owes is the *other* half of the criterion — that the guard
    fails any constant outside the walk, and that it can actually see this
    folder. Both are above. What is asserted here is that the emptiness is a
    fact rather than an assumption, and that the two guards agree about it.
    """

    @staticmethod
    def _scope_hits(hints: Sequence[str]) -> list[tuple[str, str, float]]:
        stems = {path.stem for path in SCOPE_DIR.rglob("*.py")}
        return [
            entry for entry in _module_level_floats(hints, root=EXAMPLES_DIR) if entry[0] in stems
        ]

    def test_the_scope_lane_introduces_no_timeout_constant_today(self) -> None:
        assert self._scope_hits(_TIMEOUT_NAME_HINTS) == []

    def test_the_scope_lane_introduces_no_retry_backoff_either(self) -> None:
        assert self._scope_hits(("SLEEP", "BACKOFF", "RETRY_WAIT")) == []

    def test_every_scope_clock_that_exists_is_a_walked_clock(self) -> None:
        """Vacuous today, and it stops being vacuous the moment one lands.

        Stated as a subset rather than as a count so it needs no edit when the
        seam is built: the assertion is the criterion, not the current tally.
        """
        walked = {(clock.module.rsplit(".", 1)[-1], clock.constant) for clock in CLOCKS}
        walked |= {(clock.module.rsplit(".", 1)[-1], clock.constant) for clock in STREAM_CLOCKS}
        stray = [
            f"{module}.{name}"
            for module, name, _ in self._scope_hits(_TIMEOUT_NAME_HINTS)
            if (module, name) not in walked
        ]
        assert not stray, f"scope-lane clock(s) outside CLOCKS: {stray}"

    def test_the_emptiness_is_a_committed_pre_registration_not_a_coincidence(self) -> None:
        text = (RESULTS_DIR / "scopebench-preregistration.md").read_text(encoding="utf-8")
        assert "introduces a timeout constant" in text, (
            "the ScopeBench pre-registration no longer claims the folder is clock-free, "
            "so this task's reading of criterion 1 has to be re-taken rather than assumed"
        )


# ── the gap the guard did not cover: the package's own module-level clocks ────


#: Names that make a module-level float in ``embodiment/`` a candidate clock.
#: Wider than :data:`_TIMEOUT_NAME_HINTS` because the package's clocks are
#: thread mechanics rather than request deadlines: a poll interval bounds a
#: wait just as surely as a timeout does, and leaving ``INTERVAL`` out would
#: have reproduced the audit-by-memory this module replaced.
_PACKAGE_CLOCK_HINTS = ("TIMEOUT", "DEADLINE", "INTERVAL", "WAIT")

#: **The judgement, recorded rather than left to a reader.** Every module-level
#: clock in ``embodiment/`` is here, and every one is exempt from
#: ``max_tokens / tok_s`` for the same structural reason: *it does not sit in
#: front of a model call*. The rule answers "how long may generating take"; a
#: join bound answers "how long do we wait for a thread we have already told to
#: stop", and a poll interval answers "how long may a missed wakeup cost". No
#: token budget is on the other side of any of them, so a derived-looking
#: number would be an invented one — the same reasoning :data:`BACKOFFS` is
#: exempt under.
#:
#: The exemption is not silence. Each of these carries a derivation comment in
#: its own source that ``tests/test_strategist_runner.py`` and
#: ``tests/test_muse_runner.py`` already read back, and
#: :class:`TestThePackageClockExemptionsAreSafe` pins the property that makes
#: the exemption *safe*: a join bound that could plausibly catch a model call
#: in flight would silently convert a completed review into a lost one, which
#: is exactly "a clock sized against the wrong quantity becomes the
#: measurement".
_NOT_A_MODEL_CLOCK_IN_PACKAGE: Mapping[tuple[str, str], str] = {
    ("strategist_runner", "DEFAULT_JOIN_TIMEOUT"): (
        "bounds threading.Thread.join at teardown, not a request. It cannot cut a "
        "review short: the worker is a daemon thread, so a review still in flight is "
        "abandoned rather than waited for, and the abandonment is recorded as a "
        "strategist-dropped-late transition rather than lost."
    ),
    ("strategist_runner", "DEFAULT_POLL_INTERVAL"): (
        "bounds threading.Event.wait between reviews. Correctness never depends on it "
        "— the wake event does the work — so it is the cost of a MISSED wakeup, not a "
        "deadline anything is measured against."
    ),
    ("muse_runner", "DEFAULT_JOIN_TIMEOUT"): (
        "the cited runner's identical join bound, on the identical daemon-thread "
        "discipline. The muse is archived (deviation d2/d3, embodiment#53) and its "
        "clock is declared here rather than skipped, because a walk that skips a "
        "module by name is the audit-by-memory this file exists to replace."
    ),
    ("muse_runner", "DEFAULT_POLL_INTERVAL"): (
        "the cited runner's poll-wake bound, sized against ~2.6 s muse sessions. Same "
        "structure as the strategist's and exempt for the same reason."
    ),
    ("workspace", "DEFAULT_DESTROY_TIMEOUT"): (
        "bounds a docker container teardown through headspace, not a model call. The "
        "quantity on the other side is a container stop, and nothing in this repo "
        "measures a token budget for it."
    ),
}


class TestThePackageDeclaresItsOwnClocks:
    """The gap ``t10`` found: the clock guard walked ``examples/`` and stopped.

    ``embodiment/`` ships five module-level clocks and no guard covered any of
    them. They are all exempt — none fronts a model call — but "exempt" and
    "unexamined" look identical from outside, and this repo's own rule is that
    a clock is either derived or exempt *with a stated reason*. So the walk is
    extended and the exemptions are written down, rather than the category
    being left open.
    """

    @staticmethod
    def _found() -> list[tuple[str, str, float]]:
        return _module_level_floats(_PACKAGE_CLOCK_HINTS, root=PACKAGE_DIR)

    def test_no_module_level_clock_in_the_package_is_undeclared(self) -> None:
        stray = [
            f"{module}.{name}"
            for module, name, _ in self._found()
            if (module, name) not in _NOT_A_MODEL_CLOCK_IN_PACKAGE
        ]
        assert not stray, (
            f"undeclared clock(s) in embodiment/: {stray}. Add a Clock to CLOCKS if it "
            "fronts a model call, or an entry to _NOT_A_MODEL_CLOCK_IN_PACKAGE with the "
            "reason it does not. An unexamined clock is how three of the four incidents "
            "in CLAUDE.md's load-bearing lesson stayed invisible while they fired."
        )

    def test_the_walk_actually_finds_something_so_it_is_not_vacuous(self) -> None:
        assert self._found(), "the package walk found no clock at all; it is checking nothing"

    def test_no_declared_exemption_names_a_constant_that_no_longer_exists(self) -> None:
        """A registry that can rot is a registry that will."""
        present = {(module, name) for module, name, _ in self._found()}
        gone = sorted(
            f"{module}.{name}"
            for module, name in _NOT_A_MODEL_CLOCK_IN_PACKAGE
            if (module, name) not in present
        )
        assert not gone, f"exemption(s) for constants that are gone: {gone}"

    @pytest.mark.parametrize(
        "reason",
        list(_NOT_A_MODEL_CLOCK_IN_PACKAGE.values()),
        ids=[f"{module}.{name}" for module, name in _NOT_A_MODEL_CLOCK_IN_PACKAGE],
    )
    def test_every_exemption_states_its_reason(self, reason: str) -> None:
        assert len(reason.strip()) > 60, reason

    def test_the_stems_the_registry_keys_on_are_unambiguous(self) -> None:
        """The walk keys by file stem; two clock-bearing files sharing one would alias."""
        stems = [path.stem for path in _walked_files(PACKAGE_DIR)]
        duplicated = {stem for stem in stems if stems.count(stem) > 1}
        clashing = sorted({module for module, _, _ in self._found()} & duplicated)
        assert not clashing, (
            f"more than one file under embodiment/ is named {clashing} and at least one "
            "carries a clock, so the registry's stem key is ambiguous"
        )

    def test_the_guard_would_catch_a_new_package_clock(self, tmp_path: Path) -> None:
        """The test of the test, on a throwaway tree."""
        (tmp_path / "newlane.py").write_text("DEFAULT_DIAL_TIMEOUT = 45.0\n", encoding="utf-8")
        found = _module_level_floats(_PACKAGE_CLOCK_HINTS, root=tmp_path)
        assert found == [("newlane", "DEFAULT_DIAL_TIMEOUT", 45.0)]


class TestThePackageClockExemptionsAreSafe:
    """*Why* the exemptions hold — the property, not the assertion.

    A join bound is only harmless while it cannot plausibly land inside a model
    call. If the shortest possible review were the same order as the join
    bound, teardown would start silently converting completed reviews into lost
    ones, and the record would show a clean shutdown either way. That is the
    exact failure shape this module exists to close, so it is checked against
    the committed rates rather than argued from the daemon-thread flag alone.
    """

    def test_a_join_bound_cannot_plausibly_catch_a_review_in_flight(
        self, config: rc.RateConfig
    ) -> None:
        import embodiment.strategist_runner as sr

        shortest = strategist_timings(config)["review_min"]
        assert sr.DEFAULT_JOIN_TIMEOUT * 10 < shortest, (
            f"the join bound is {sr.DEFAULT_JOIN_TIMEOUT} s against a shortest possible "
            f"review of {shortest:.0f} s. Close enough to overlap, and teardown starts "
            "discarding reviews that had already finished."
        )

    def test_the_poll_interval_is_noise_against_one_unit_of_work(
        self, config: rc.RateConfig
    ) -> None:
        import embodiment.strategist_runner as sr

        shortest = strategist_timings(config)["review_min"]
        assert sr.DEFAULT_POLL_INTERVAL / shortest < 0.05

    def test_the_join_bound_is_the_stated_multiple_of_the_poll_interval(self) -> None:
        """Its derivation comment says 2x; a drift here would make the comment fiction."""
        import embodiment.strategist_runner as sr

        assert sr.DEFAULT_JOIN_TIMEOUT == 2 * sr.DEFAULT_POLL_INTERVAL


# ── criterion 3: the strategist's defaults, traced back to committed figures ──


STRATEGIST_SRC = PACKAGE_DIR / "strategist_runner.py"

#: Every ``N tok/s`` the strategist runner's prose cites. A rate quoted in a
#: derivation is exactly the c39 target: nothing divides by it at runtime, so it
#: can drift away from the rig for a whole cycle and every test stays green
#: while the constants it justifies quietly stop being justified.
_CITED_RATE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*tok/s")

#: ``T_review_min`` — one review turn at the fastest measured strategist rate.
_REVIEW_MIN = re.compile(
    r"\((\d+) reasoning \+ (\d+) directive\) tokens / ([\d.]+) tok/s`` = \*\*(\d+) s\*\*"
)
#: ``T_review_max`` — a full review at the slowest rate, plus the queue allowance.
_REVIEW_MAX = re.compile(
    r"``(\d+) turns x (\d+) tokens / ([\d.]+) tok/s \+ ([\d.]+) s`` = \*\*(\d+) s\*\*"
)
#: ``T_actor_step`` — one acting step on the worker seat at its mean rate.
_ACTOR_STEP = re.compile(r"``(\d+) tokens / ([\d.]+) tok/s`` = \*\*(\d+) s\*\*")


def strategist_source() -> str:
    return STRATEGIST_SRC.read_text(encoding="utf-8")


def cited_rates(source: str) -> tuple[float, ...]:
    return tuple(sorted({float(value) for value in _CITED_RATE.findall(source)}))


def published_rate_index(config: rc.RateConfig) -> Mapping[float, tuple[str, ...]]:
    """``rate -> the (role, field) names that publish it``, for citation checking.

    Built from the loaded config rather than typed, so this module still carries
    no rate literal — the property ``tests/test_rate_config.py`` enforces over
    every entry in ``RATE_DERIVING_MODULES``, this file among them.
    """
    index: dict[float, list[str]] = {}
    role_fields = (
        "slowest_tok_s",
        "mean_tok_s",
        "fastest_tok_s",
        "cited_as",
        "cited_mean_as",
        "cited_fastest_as",
        "aggregate_tok_s",
    )
    for role, measurement in config.roles.items():
        for name in role_fields:
            value = getattr(measurement, name, None)
            if value is not None:
                index.setdefault(float(value), []).append(f"{role}.{name}")
        for width, at_width in measurement.by_width.items():
            for name in ("slowest_tok_s", "mean_tok_s", "fastest_tok_s"):
                value = float(getattr(at_width, name))
                index.setdefault(value, []).append(f"{role}.by_width.{width}.{name}")
    return {value: tuple(names) for value, names in index.items()}


def _first_match(pattern: "re.Pattern[str]", source: str, label: str) -> tuple[str, ...]:
    found = pattern.findall(source)
    assert found, (
        f"{STRATEGIST_SRC.name} no longer states {label} in the form this test reads. "
        "The derivation may still be correct, but it is no longer checkable — restate "
        "it or move the check, rather than deleting the trace."
    )
    return tuple(found[0])


def strategist_timings(config: rc.RateConfig) -> Mapping[str, float]:
    """The three quantities the runner's defaults are ratios of, recomputed.

    Token counts and turn budgets come out of the source's own derivation text;
    every **rate** and the queue allowance come out of the committed config. So
    what this reproduces is the arithmetic the comments claim, on the inputs the
    config actually holds — a drift in either direction fails rather than
    passing on a stale number nobody re-read.

    *config* is taken and not read only for its side of the contract: the
    figures are checked against it by the tests below, which is where a
    mismatch has something useful to say.
    """
    assert config.roles, "an empty config would make every timing below unfalsifiable"
    source = strategist_source()
    reasoning, directive, fastest, _ = _first_match(_REVIEW_MIN, source, "T_review_min")
    turns, per_turn, slowest, allowance, _ = _first_match(_REVIEW_MAX, source, "T_review_max")
    step_tokens, mean, _ = _first_match(_ACTOR_STEP, source, "T_actor_step")
    return {
        "review_min": (int(reasoning) + int(directive)) / float(fastest),
        "review_max": int(turns) * int(per_turn) / float(slowest) + float(allowance),
        "actor_step": int(step_tokens) / float(mean),
    }


class TestTheStrategistDefaultsTraceToTheConfig:
    """``t10`` criterion 3, in its strong form.

    ``tests/test_strategist_runner.py`` already asserts each default carries a
    derivation comment and that the comment contains a digit and the string
    ``tok/s``. That proves the *shape* of a citation, not that the citation
    resolves: a derivation reading "at 99.9 tok/s" would pass it. What is
    checked here is that every rate the runner's prose cites is a figure this
    repo's committed measurement actually publishes, and that the staleness and
    cadence defaults are the arithmetic those figures produce.

    This is the sibling of ``TestNoRateLiteralInCode``, one layer out: that
    guard keeps rate literals out of code that *divides* by them; this one keeps
    invented rates out of prose that *justifies* a constant. Both are claim
    ``c39`` — a rate nobody can trace back to a measurement goes stale in
    silence, and this one found a live instance (the worker mean at 38.9 tok/s,
    published nowhere until this task added ``cited_mean_as``).
    """

    def test_the_module_cites_rates_at_all(self) -> None:
        assert cited_rates(strategist_source())

    def test_every_rate_the_derivations_cite_is_a_figure_the_config_publishes(
        self, config: rc.RateConfig
    ) -> None:
        published = published_rate_index(config)
        uncited = [value for value in cited_rates(strategist_source()) if value not in published]
        assert not uncited, (
            f"{STRATEGIST_SRC.name} derives its defaults from {uncited} tok/s, which "
            f"{rc.DEFAULT_CONFIG_PATH.name} does not publish. Either the figure is a "
            "rounding no committed doc states — publish it — or it is a number someone "
            "remembered. DEFAULT_MAX_LAG and DEFAULT_REVIEW_GAP both divide by it."
        )

    def test_the_staleness_and_cadence_rates_name_the_two_seats(
        self, config: rc.RateConfig
    ) -> None:
        """The strategist seat is the cortex role; the actor seat is the worker role.

        Named, because a derivation that cited the right *number* off the wrong
        role would be the finding-1 mistake — a bound read against whichever
        rate was to hand — reappearing in a cadence policy.
        """
        published = published_rate_index(config)
        owners = {
            owner.split(".")[0]
            for value in cited_rates(strategist_source())
            for owner in published[value]
        }
        assert "cortex" in owners
        assert "worker" in owners

    def test_the_queue_allowance_the_review_bound_adds_is_the_committed_one(
        self, config: rc.RateConfig
    ) -> None:
        _, _, _, allowance, _ = _first_match(_REVIEW_MAX, strategist_source(), "T_review_max")
        assert float(allowance) == config.non_generation_allowance.cited_as

    def test_the_turn_budget_the_review_bound_assumes_is_the_shipped_default(self) -> None:
        from embodiment.scope import ScopeControls

        turns, _, _, _, _ = _first_match(_REVIEW_MAX, strategist_source(), "T_review_max")
        assert int(turns) == ScopeControls().max_turns

    def test_the_actor_step_budget_is_the_one_the_worker_was_measured_at(
        self, config: rc.RateConfig
    ) -> None:
        """The numerator too, not only the rate.

        ``T_actor_step`` is tokens over tok/s, and a token count carried over
        from a different harness would move the ratio just as surely as a stale
        rate would. It is the worker measurement's own ``max_tokens``.
        """
        step_tokens, _, _ = _first_match(_ACTOR_STEP, strategist_source(), "T_actor_step")
        assert int(step_tokens) == config.rate("worker").max_tokens

    @pytest.mark.parametrize("label", ["review_min", "review_max", "actor_step"])
    def test_each_stated_timing_is_the_arithmetic_of_its_committed_inputs(
        self, label: str, config: rc.RateConfig
    ) -> None:
        source = strategist_source()
        stated = {
            "review_min": _first_match(_REVIEW_MIN, source, label)[-1],
            "review_max": _first_match(_REVIEW_MAX, source, label)[-1],
            "actor_step": _first_match(_ACTOR_STEP, source, label)[-1],
        }[label]
        assert round(strategist_timings(config)[label]) == int(stated)

    def test_the_staleness_default_is_the_ceiling_of_the_two_timings(
        self, config: rc.RateConfig
    ) -> None:
        import embodiment.strategist_runner as sr

        timings = strategist_timings(config)
        assert sr.DEFAULT_MAX_LAG == ceil(timings["review_max"] / timings["actor_step"])

    def test_the_cadence_default_is_the_ceiling_of_the_two_timings(
        self, config: rc.RateConfig
    ) -> None:
        import embodiment.strategist_runner as sr

        timings = strategist_timings(config)
        assert sr.DEFAULT_REVIEW_GAP == ceil(timings["review_min"] / timings["actor_step"])

    def test_the_buffer_depth_covers_the_healthy_producer_consumer_ratio(
        self, config: rc.RateConfig
    ) -> None:
        """``DEFAULT_MAX_PENDING``'s stated derivation: 2x a depth of 2."""
        import embodiment.strategist_runner as sr

        timings = strategist_timings(config)
        healthy_depth = ceil(timings["review_min"] / timings["actor_step"])
        assert sr.DEFAULT_MAX_PENDING >= healthy_depth

    def test_an_uncited_rate_in_a_derivation_would_be_caught(self, config: rc.RateConfig) -> None:
        """The test of the test, on a fabricated source rather than the real one."""
        published = published_rate_index(config)
        invented = cited_rates("one acting step at 99.987 tok/s")
        assert invented == (99.987,)
        assert 99.987 not in published


# ── criterion 2 / plan risk r1: which worker reading the scope lane divides by ─


SCOPED_RUN_PATTERN = "scoped_run"
STRATEGIST_CADENCE_PATTERN = "strategist_cadence"


class TestTheScopedRunDivisorIsTheConservativeReading:
    """Plan risk ``r1``, closed in the config rather than in a reviewer's memory.

    The worker role has a dated rate entry, so criterion 2's first branch is
    satisfied and no ``unmeasured_roles`` gap is needed. But the entry is
    **width-dependent** — 76.4 tok/s at width 1 falling to 29.8 under fan-out,
    all of it measured on one easy cell through the proxy — and a bound derived
    from the width-1 reading would be nearly six times too generous for a rig
    nobody has promised. A rate that flatters the clock is the failure this
    whole lane exists to prevent, so the choice is recorded as data and the
    *direction* of the choice is checked arithmetically: a calling pattern may
    not divide by a figure faster than the one it says it rejected.
    """

    @staticmethod
    def _pattern(config: rc.RateConfig, name: str) -> rc.CallingPattern:
        patterns = config.rate("worker").calling_patterns
        assert name in patterns, (
            f"the worker entry records no {name!r} calling pattern. Its rate is "
            "width-dependent, so 'the worker rate' names four different numbers and a "
            "bound built on the wrong one is invisible until it censors something."
        )
        return patterns[name]

    def test_the_worker_rate_entry_is_measured_and_dated(self, config: rc.RateConfig) -> None:
        measurement = config.rate("worker")
        assert measurement.measured_on
        assert measurement.n_rate_bearing > 0

    def test_the_worker_is_not_also_declared_an_unmeasured_gap(self, config: rc.RateConfig) -> None:
        assert "worker" not in config.unmeasured_roles

    @pytest.mark.parametrize("name", [SCOPED_RUN_PATTERN, STRATEGIST_CADENCE_PATTERN])
    def test_the_pattern_divides_by_the_figure_it_names(
        self, name: str, config: rc.RateConfig
    ) -> None:
        pattern = self._pattern(config, name)
        assert pattern.tok_s == pattern.resolve(config.rate("worker"), pattern.divides_by)

    @pytest.mark.parametrize("name", [SCOPED_RUN_PATTERN, STRATEGIST_CADENCE_PATTERN])
    def test_the_pattern_names_the_reading_it_rejected(
        self, name: str, config: rc.RateConfig
    ) -> None:
        pattern = self._pattern(config, name)
        assert pattern.rejected_tok_s == pattern.resolve(config.rate("worker"), pattern.rejected)

    @pytest.mark.parametrize("name", [SCOPED_RUN_PATTERN, STRATEGIST_CADENCE_PATTERN])
    def test_the_chosen_divisor_is_no_faster_than_the_rejected_one(
        self, name: str, config: rc.RateConfig
    ) -> None:
        """The whole of ``r1``, as arithmetic. A faster divisor is a shorter clock."""
        pattern = self._pattern(config, name)
        assert pattern.tok_s <= pattern.rejected_tok_s

    @pytest.mark.parametrize("name", [SCOPED_RUN_PATTERN, STRATEGIST_CADENCE_PATTERN])
    def test_the_pattern_states_why(self, name: str, config: rc.RateConfig) -> None:
        pattern = self._pattern(config, name)
        assert any(line.strip() for line in pattern.why)

    @pytest.mark.parametrize("name", [SCOPED_RUN_PATTERN, STRATEGIST_CADENCE_PATTERN])
    def test_the_rejected_reading_is_the_width_one_one(
        self, name: str, config: rc.RateConfig
    ) -> None:
        """Named explicitly, because it is the specific figure r1 warns about."""
        assert "by_width.1." in self._pattern(config, name).rejected

    def test_the_scoped_run_divides_by_the_all_width_floor(self, config: rc.RateConfig) -> None:
        measurement = config.rate("worker")
        assert self._pattern(config, SCOPED_RUN_PATTERN).tok_s == measurement.bound_input_tok_s

    def test_the_cadence_divisor_is_the_one_the_strategist_runner_cites(
        self, config: rc.RateConfig
    ) -> None:
        """The two records must be the same number, or one of them is decoration."""
        _, mean, _ = _first_match(_ACTOR_STEP, strategist_source(), "T_actor_step")
        cadence = self._pattern(config, STRATEGIST_CADENCE_PATTERN)
        assert float(mean) == cadence.cited_as

    def test_the_procedure_doc_publishes_the_same_decision(self) -> None:
        text = rc.PROCEDURE_DOC_PATH.read_text(encoding="utf-8")
        assert SCOPED_RUN_PATTERN in text
        assert STRATEGIST_CADENCE_PATTERN in text

    def test_a_pattern_that_flatters_the_clock_is_refused(self, tmp_path: Path) -> None:
        """The test of the test: the conservatism check is load-bearing, not decoration.

        Built by mutating the committed config in a ``tmp_path`` copy, so the
        real file is never touched and the refusal is proved against the loader
        the bound test actually uses.
        """
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        worker = payload["roles"]["worker"]
        pattern = worker["calling_patterns"][SCOPED_RUN_PATTERN]
        pattern["divides_by"] = "fastest_tok_s"
        pattern["tok_s"] = worker["fastest_tok_s"]

        broken = tmp_path / "timeout-rate-measurements.json"
        broken.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(rc.RateConfigError) as raised:
            rc.load_rate_config(broken)
        assert SCOPED_RUN_PATTERN in str(raised.value)

    def test_a_pattern_naming_a_figure_that_is_not_measured_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A divisor is a path into the record, never a number typed beside one."""
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        pattern = payload["roles"]["worker"]["calling_patterns"][SCOPED_RUN_PATTERN]
        pattern["divides_by"] = "by_width.4.slowest_tok_s"

        broken = tmp_path / "timeout-rate-measurements.json"
        broken.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(rc.RateConfigError):
            rc.load_rate_config(broken)
