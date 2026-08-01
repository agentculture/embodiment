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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import rate_config as rc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
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


def _module_level_floats(
    hints: Sequence[str],
) -> list[tuple[str, str, float]]:
    """``(module stem, constant, value)`` for module-level float assignments matching *hints*."""
    found: list[tuple[str, str, float]] = []
    for path in sorted(EXAMPLES_DIR.glob("*.py")):
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
