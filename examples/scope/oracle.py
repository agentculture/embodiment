"""ScopeBench's exact oracle — the grader nothing can talk out of its answer.

``t9`` acceptance criterion 1: *episode outcomes are machine-graded against an
exact oracle or declared Pareto frontier; an LLM judge is structurally secondary
and cannot determine the primary verdict.* This module is the first half of
that. The second half — the structural fact that no judge value can reach the
verdict function — lives in :mod:`examples.scope.scopebench`.

What is exact here
------------------
:func:`solve` searches **every** plan an episode admits. A plan is one
allocation per review tick, the world between reviews is deterministic, and the
per-review feasible set is small by construction (:data:`MAX_FEASIBLE_PER_REVIEW`
is checked by test, so a generator edit cannot quietly make CI take minutes). So
"the optimum" is a maximum over an enumerated set, not an estimate, and
:func:`brute_force_optimum` re-derives it without the memo table as the
test-of-the-test.

Alongside it a **declared Pareto frontier** over ``(net utility, spend)``, so an
arm that buys utility with runaway spend is visible as a point off the frontier
rather than as a good score.

The oracle is clairvoyant, and that is stated rather than hidden
---------------------------------------------------------------
:func:`solve` plans knowing every scheduled event, including ones that have not
fired yet. No arm can. Three consequences, all of them deliberate:

* ``optimum`` is an **upper bound**, not an achievable target. Reporting an
  arm's raw regret against it as "how much it left on the table" would be
  wrong, and the pre-registration says so.
* Regret is nevertheless a **valid comparator between arms**: the foresight
  component is a per-episode constant and cancels in every arm-to-arm
  difference, which is the only comparison the verdict rule makes.
* The two verified schema properties compare **clairvoyant against
  clairvoyant** — :func:`best_after` on the trap against :func:`best_from` at
  the same state — so the foresight component cancels there too. A trap is a
  move that forgoes value that really was available.

:func:`naive` gives the non-clairvoyant view (events at or after a tick
removed), which is what the ``static`` and ``revising`` scripted planners plan
under. That is the honest ceiling for a decision-maker who cannot see the
future.

Nothing here dials a model, reads a clock, opens a socket or starts a thread.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from examples.scope import episodes as ep

__all__ = [
    "FIXTURES_PATH",
    "MAX_FEASIBLE_PER_REVIEW",
    "NonIntervention",
    "Plan",
    "Solution",
    "State",
    "Trap",
    "advance",
    "best_after",
    "best_from",
    "brute_force_optimum",
    "every_plan",
    "feasible",
    "fixture_for",
    "greedy_allocation",
    "greedy_plan",
    "initial_state",
    "invalidity",
    "load_fixtures",
    "local_score",
    "naive",
    "plan_from",
    "schema_report",
    "score_plan",
    "solve",
    "spend_of",
    "state_after",
    "trivial_episode",
    "utility",
    "window_end",
]

#: The pinned oracle values. An **expectation**, on ``arch-policy-fixtures.json``'s
#: pattern: every published ScopeBench number is relative to these, so a moved
#: number has to be re-pinned in a diff a reviewer sees.
FIXTURES_PATH = ep.REPO_ROOT / "docs" / "live-test-results" / "scopebench-fixtures.json"

#: The ceiling on one review's feasible allocation set. Not a tuning knob — a
#: **budget on the search**, asserted by test at every committed episode's
#: opening state so an edit to a generator that widened the world would fail
#: here rather than in CI's wall clock.
MAX_FEASIBLE_PER_REVIEW = 64

#: One plan: an allocation per review tick, in review order.
Plan = tuple[ep.Allocation, ...]


# ── the world state ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class State:
    """The whole world at a tick. Hashable, so the search memoises on it.

    Every mutable-looking field is a tuple in the episode's own declaration
    order, so two states that mean the same thing *are* the same key.
    """

    tick: int
    remaining: tuple[int, ...]
    done_at: tuple[int, ...]
    spend: int
    lost: frozenset[str]
    deadlines: tuple[int, ...]
    values: tuple[int, ...]
    unattended: tuple[int, ...]
    breached: frozenset[str]


def initial_state(episode: ep.Episode) -> State:
    """The world at tick 0, before anything has happened."""
    return State(
        tick=0,
        remaining=tuple(stream.effort for stream in episode.workstreams),
        done_at=tuple(-1 for _ in episode.workstreams),
        spend=0,
        lost=frozenset(),
        deadlines=tuple(entry.deadline for entry in episode.objectives),
        values=tuple(entry.value for entry in episode.objectives),
        unattended=tuple(0 for _ in episode.workstreams),
        breached=frozenset(),
    )


def _index(names: Sequence[str], name: str) -> int:
    return names.index(name)


def _blocked(episode: ep.Episode, state: State, stream: ep.Workstream) -> bool:
    names = episode.workstream_ids
    return any(state.done_at[_index(names, need)] < 0 for need in stream.depends_on)


def _fire(episode: ep.Episode, state: State, tick: int) -> State:
    """Apply every event scheduled at *tick*. Deterministic, order-independent."""
    remaining = list(state.remaining)
    deadlines = list(state.deadlines)
    values = list(state.values)
    lost = set(state.lost)
    streams = episode.workstream_ids
    objectives = tuple(entry.id for entry in episode.objectives)
    for event in episode.events:
        if event.tick != tick:
            continue
        if event.kind == ep.EVENT_EFFORT_JUMP and event.subject in streams:
            index = _index(streams, event.subject)
            if state.done_at[index] < 0:
                remaining[index] = max(0, remaining[index] + event.amount)
        elif event.kind == ep.EVENT_DEADLINE_MOVE and event.subject in objectives:
            index = _index(objectives, event.subject)
            deadlines[index] = max(0, deadlines[index] + event.amount)
        elif event.kind == ep.EVENT_VALUE_CHANGE and event.subject in objectives:
            index = _index(objectives, event.subject)
            values[index] = max(0, values[index] + event.amount)
        elif event.kind == ep.EVENT_ACTOR_LOST:
            lost.add(event.subject)
    return State(
        tick=state.tick,
        remaining=tuple(remaining),
        done_at=state.done_at,
        spend=state.spend,
        lost=frozenset(lost),
        deadlines=tuple(deadlines),
        values=tuple(values),
        unattended=state.unattended,
        breached=state.breached,
    )


def _forbidden(episode: ep.Episode, allocation: ep.Allocation) -> frozenset[str]:
    """Constraint ids this allocation breaches by being *ordered* at all."""
    hits: set[str] = set()
    for constraint in episode.constraints:
        if constraint.kind != ep.CONSTRAINT_FORBIDDEN_TAG:
            continue
        for actor_id, target in allocation:
            stream = episode.workstream(target)
            if actor_id == constraint.subject and stream is not None:
                if constraint.target in stream.tags:
                    hits.add(constraint.id)
    return frozenset(hits)


def _one_tick(episode: ep.Episode, state: State, allocation: ep.Allocation) -> State:
    """One tick of work under a fixed allocation. Events for this tick fire first."""
    state = _fire(episode, state, state.tick)
    names = episode.workstream_ids
    remaining = list(state.remaining)
    done_at = list(state.done_at)
    spend = state.spend
    attended: set[int] = set()
    for actor_id, target in allocation:
        actor = episode.actor(actor_id)
        if actor is None or actor_id in state.lost or target == ep.IDLE:
            continue
        stream = episode.workstream(target)
        if stream is None or stream.kind not in actor.skills:
            continue
        index = _index(names, target)
        if done_at[index] >= 0:
            continue
        spend += actor.cost
        if _blocked(episode, state, stream):
            continue
        attended.add(index)
        remaining[index] = max(0, remaining[index] - actor.rate)
        if remaining[index] == 0:
            done_at[index] = state.tick + 1

    unattended = list(state.unattended)
    breached = set(state.breached) | _forbidden(episode, allocation)
    for index, stream in enumerate(episode.workstreams):
        if done_at[index] >= 0 or _blocked(episode, state, stream):
            unattended[index] = 0
            continue
        unattended[index] = 0 if index in attended else unattended[index] + 1
    for constraint in episode.constraints:
        if constraint.kind != ep.CONSTRAINT_UNATTENDED:
            continue
        if constraint.subject in names:
            if unattended[_index(names, constraint.subject)] > constraint.bound:
                breached.add(constraint.id)
    return State(
        tick=state.tick + 1,
        remaining=tuple(remaining),
        done_at=tuple(done_at),
        spend=spend,
        lost=state.lost,
        deadlines=state.deadlines,
        values=state.values,
        unattended=tuple(unattended),
        breached=frozenset(breached),
    )


def window_end(episode: ep.Episode, review: int) -> int:
    """The tick the allocation issued at review *review* stops governing."""
    following = review + 1
    if following < len(episode.review_ticks):
        return episode.review_ticks[following]
    return episode.horizon


def advance(
    episode: ep.Episode,
    state: State,
    allocation: ep.Allocation,
    until: int,
) -> State:
    """Run the world from ``state.tick`` up to (not including) *until*."""
    while state.tick < until:
        state = _one_tick(episode, state, allocation)
    return state


# ── scoring ───────────────────────────────────────────────────────────────────


def _achieved(episode: ep.Episode, state: State) -> tuple[str, ...]:
    names = episode.workstream_ids
    out: list[str] = []
    for index, objective in enumerate(episode.objectives):
        deadline = state.deadlines[index]
        done = all(
            0 <= state.done_at[_index(names, need)] <= deadline for need in objective.requires
        )
        if done:
            out.append(objective.id)
    return tuple(out)


def _penalty(episode: ep.Episode, state: State) -> int:
    total = 0
    for constraint in episode.constraints:
        breached = constraint.id in state.breached
        if constraint.kind == ep.CONSTRAINT_SPEND_CEILING:
            breached = state.spend > constraint.bound
        if breached:
            total += constraint.penalty
    return total


def utility(episode: ep.Episode, state: State) -> int:
    """Net strategic utility: achieved value minus every constraint penalty."""
    gross = 0
    for index, objective in enumerate(episode.objectives):
        if objective.id in _achieved(episode, state):
            gross += state.values[index]
    return gross - _penalty(episode, state)


def _run(episode: ep.Episode, plan: Plan) -> State:
    state = initial_state(episode)
    for review, _tick in enumerate(episode.review_ticks):
        allocation = plan[review] if review < len(plan) else plan[-1]
        state = advance(episode, state, allocation, window_end(episode, review))
    return state


def score_plan(episode: ep.Episode, plan: Plan) -> int:
    """The net utility this plan actually scores. The whole outcome metric."""
    return utility(episode, _run(episode, plan))


def spend_of(episode: ep.Episode, plan: Plan) -> int:
    """What this plan spent. The frontier's second axis."""
    return _run(episode, plan).spend


def state_after(episode: ep.Episode, plan: Plan, review: int) -> State:
    """The world at review *review*, having played *plan*'s earlier allocations."""
    state = initial_state(episode)
    for index in range(review):
        allocation = plan[index] if index < len(plan) else plan[-1]
        state = advance(episode, state, allocation, window_end(episode, index))
    return state


# ── the search ────────────────────────────────────────────────────────────────


def feasible(episode: ep.Episode, state: State) -> tuple[ep.Allocation, ...]:
    """Every **physically executable** allocation from this state.

    Executable, not permitted: a forbidden-tag assignment is in here, because
    the subordinate really would carry it out. Constraints are priced, never
    hidden — see :mod:`examples.scope.episodes`.
    """
    names = episode.workstream_ids
    per_actor: list[tuple[str, ...]] = []
    for actor in episode.actors:
        if actor.id in state.lost:
            per_actor.append((ep.IDLE,))
            continue
        targets = [ep.IDLE]
        for index, stream in enumerate(episode.workstreams):
            if stream.kind in actor.skills and state.done_at[index] < 0:
                targets.append(names[index])
        per_actor.append(tuple(targets))
    ids = episode.actor_ids
    return tuple(tuple(zip(ids, combination)) for combination in itertools.product(*per_actor))


class _Solver:
    """One episode's memo tables. Constructed per solve, never shared."""

    def __init__(self, episode: ep.Episode) -> None:
        self.episode = episode
        self._best: dict[tuple[int, State], int] = {}
        self._points: dict[tuple[int, State], frozenset[tuple[int, int]]] = {}

    def best(self, state: State, review: int) -> int:
        if review >= len(self.episode.review_ticks):
            return utility(self.episode, state)
        key = (review, state)
        cached = self._best.get(key)
        if cached is not None:
            return cached
        found = max(
            self.best(
                advance(self.episode, state, allocation, window_end(self.episode, review)),
                review + 1,
            )
            for allocation in feasible(self.episode, state)
        )
        self._best[key] = found
        return found

    def plan(self, state: State, review: int, previous: Optional[ep.Allocation] = None) -> Plan:
        """The **least-churning** optimal plan from here.

        Ties are broken first toward keeping what is already standing, then
        toward the locally most productive move. That is not cosmetic. An episode
        usually admits many optimal plans, and one picked by raw iteration order
        both churns gratuitously and idles wherever idling happens to be free —
        which would make the do-not-intervene state impossible to find on the
        very trajectory an optimal decision-maker would follow, and would make
        "the optimal plan" an artifact of a loop order. Least churn, then most
        work, makes it one canonical object.
        """
        if review >= len(self.episode.review_ticks):
            return ()
        target = self.best(state, review)
        options = feasible(self.episode, state)
        standing = _standing(self.episode, state, previous or self.episode.default_allocation)
        rest = sorted(options, key=lambda entry: local_score(self.episode, state, entry, review))
        rest.reverse()
        ordered = [standing, *rest] if standing in options else rest
        for allocation in ordered:
            following = advance(self.episode, state, allocation, window_end(self.episode, review))
            if self.best(following, review + 1) == target:
                return (allocation, *self.plan(following, review + 1, allocation))
        return ()

    def points(self, state: State, review: int) -> frozenset[tuple[int, int]]:
        if review >= len(self.episode.review_ticks):
            return frozenset({(utility(self.episode, state), state.spend)})
        key = (review, state)
        cached = self._points.get(key)
        if cached is not None:
            return cached
        found: set[tuple[int, int]] = set()
        for allocation in feasible(self.episode, state):
            following = advance(self.episode, state, allocation, window_end(self.episode, review))
            found |= self.points(following, review + 1)
        frozen = frozenset(found)
        self._points[key] = frozen
        return frozen


#: One live solver per distinct episode, keyed by the episode's own content.
#: Memo tables are the whole reason the exact search is affordable, and the
#: verification passes ask for thousands of continuations from the same handful
#: of states — a fresh solver per question would recompute all of it. Keyed by
#: content rather than by id so a mutated episode can never inherit another's
#: table, which is the bug this kind of cache usually ships with.
_SOLVERS: dict[str, _Solver] = {}


def _solver_for(episode: ep.Episode) -> _Solver:
    key = hashlib.sha256(
        json.dumps(episode.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    solver = _SOLVERS.get(key)
    if solver is None:
        solver = _Solver(episode)
        _SOLVERS[key] = solver
    return solver


def best_from(episode: ep.Episode, state: State, review: int) -> int:
    """The best net utility reachable from *state* at review *review*."""
    return _solver_for(episode).best(state, review)


def best_after(
    episode: ep.Episode,
    state: State,
    review: int,
    allocation: ep.Allocation,
) -> int:
    """The best reachable utility when *allocation* is forced at this review."""
    following = advance(episode, state, allocation, window_end(episode, review))
    return _solver_for(episode).best(following, review + 1)


def plan_from(
    episode: ep.Episode,
    state: State,
    review: int,
    previous: Optional[ep.Allocation] = None,
) -> Plan:
    """The least-churning optimal plan **from here**, under this episode's beliefs.

    The seam the scripted planners use. Handing it a :func:`naive` episode is
    what makes a planner non-clairvoyant: the events it cannot yet have seen are
    simply not in the world it plans against, while the ones that already fired
    are already in *state*.
    """
    return _solver_for(episode).plan(state, review, previous)


def every_plan(episode: ep.Episode) -> Iterator[Plan]:
    """Enumerate every plan. The brute-force path — used by tests, not by solve."""

    def walk(state: State, review: int, prefix: Plan) -> Iterator[Plan]:
        if review >= len(episode.review_ticks):
            yield prefix
            return
        for allocation in feasible(episode, state):
            following = advance(episode, state, allocation, window_end(episode, review))
            yield from walk(following, review + 1, (*prefix, allocation))

    yield from walk(initial_state(episode), 0, ())


def brute_force_optimum(episode: ep.Episode) -> int:
    """The optimum, computed with no memo table at all. The test-of-the-test."""
    return max(score_plan(episode, plan) for plan in every_plan(episode))


# ── the locally attractive play ───────────────────────────────────────────────


def local_score(
    episode: ep.Episode,
    state: State,
    allocation: ep.Allocation,
    review: int,
) -> tuple[int, int, int, int]:
    """How attractive this allocation looks **right now**, and only right now.

    Lexicographic, four terms:

    1. **completions before the next review** — visible finishing;
    2. **how many distinct productive workstreams are covered** — nobody doubles
       up while something sits untouched, and nobody idles;
    3. **minus the total remaining effort on the assigned workstreams** — the
       shortest job first, which is the canonical local heuristic and the one
       that produces the canonical local failure;
    4. **minus the spend** — a tie-break that prefers not paying for a blocked
       workstream over paying for one.

    Nothing in it looks past the next review, reads a deadline, or knows what an
    objective is *worth*. That is the whole point: its argmax is a genuinely
    local play, so a state where the local argmax forgoes value is a trap rather
    than a mistake somebody made.
    """
    end = window_end(episode, review)
    after = advance(episode, state, allocation, end)
    names = episode.workstream_ids
    completions = sum(
        1
        for index in range(len(episode.workstreams))
        if state.done_at[index] < 0 <= after.done_at[index]
    )
    productive = {
        target
        for _actor_id, target in allocation
        if target != ep.IDLE
        and state.done_at[_index(names, target)] < 0
        and not _blocked(episode, state, episode.workstream(target))
    }
    weight = sum(state.remaining[_index(names, target)] for target in productive)
    return (completions, len(productive), -weight, -(after.spend - state.spend))


def greedy_allocation(episode: ep.Episode, state: State, review: int) -> ep.Allocation:
    """The locally attractive play: the argmax of :func:`local_score`.

    Deterministic — ties break on :func:`feasible`'s own settled order, which is
    the episode's declaration order, so "the greedy move" is one move rather
    than a family of them.
    """
    options = feasible(episode, state)
    best = options[0]
    best_score = local_score(episode, state, best, review)
    for allocation in options[1:]:
        score = local_score(episode, state, allocation, review)
        if score > best_score:
            best, best_score = allocation, score
    return best


def greedy_plan(episode: ep.Episode) -> Plan:
    """Greedy, recomputed at every review. ScopeBench's actor-only baseline."""
    state = initial_state(episode)
    plan: list[ep.Allocation] = []
    for review, _tick in enumerate(episode.review_ticks):
        allocation = greedy_allocation(episode, state, review)
        plan.append(allocation)
        state = advance(episode, state, allocation, window_end(episode, review))
    return tuple(plan)


# ── the two verified schema properties ────────────────────────────────────────


@dataclass(frozen=True)
class Trap:
    """A locally-attractive-but-globally-wrong action, **verified**.

    ``forgone`` is what playing it costs against the best continuation available
    from the same state — a clairvoyant-against-clairvoyant comparison, so the
    oracle's own foresight cancels out and the number is genuinely the cost of
    the move.
    """

    at_review: int
    allocation: ep.Allocation
    forgone: int
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_review": self.at_review,
            "allocation": [list(pair) for pair in self.allocation],
            "forgone": self.forgone,
            "why": self.why,
        }


@dataclass(frozen=True)
class NonIntervention:
    """A state where holding is optimal and intervening **can** cost real value.

    Read the definition precisely, because the obvious stronger one is not what
    is claimed. This is **not** "every change is worse": in a world with any
    slack at all there are usually several optimal moves, so demanding
    uniqueness would only find degenerate episodes. What is claimed and checked
    is the pair that makes non-intervention a *gradable* answer:

    * holding the standing allocation reaches the optimum — so ``[hold]`` is a
      **valid** answer, which is what the deliverable asks for; and
    * at least one available change is strictly worse, by ``margin`` at the
      worst — so churning is not free and an architecture that improves by
      re-allocating at every opportunity is measurably not an improvement.

    ``alternatives_optimal`` records how many changes would *also* have reached
    the optimum. It is reported rather than gated: a state where many moves are
    fine is a weak non-intervention test, and hiding that would overstate what
    condition 3 of the verdict rule is measuring.
    """

    at_review: int
    allocation: ep.Allocation
    margin: int
    alternatives_optimal: int
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_review": self.at_review,
            "allocation": [list(pair) for pair in self.allocation],
            "margin": self.margin,
            "alternatives_optimal": self.alternatives_optimal,
            "why": self.why,
        }


def _find_trap(episode: ep.Episode, solver: _Solver) -> Optional[Trap]:
    state = initial_state(episode)
    for review, _tick in enumerate(episode.review_ticks):
        allocation = greedy_allocation(episode, state, review)
        here = solver.best(state, review)
        following = advance(episode, state, allocation, window_end(episode, review))
        forgone = here - solver.best(following, review + 1)
        if forgone > 0:
            return Trap(
                at_review=review,
                allocation=allocation,
                forgone=forgone,
                why=(
                    f"at review {review} the locally best play forgoes {forgone} of the "
                    "value still available from this exact state"
                ),
            )
        state = following
    return None


def _standing(episode: ep.Episode, state: State, held: ep.Allocation) -> ep.Allocation:
    """*held*, with any assignment to a finished workstream collapsed to idle.

    Holding a stale allocation is not a *decision* to keep working on something
    finished — there is nothing left to work on. Collapsing it is what makes
    "the standing allocation" a well-defined thing to hold at a later review.
    """
    names = episode.workstream_ids
    pairs = []
    for actor_id, target in held:
        stale = target != ep.IDLE and (
            target not in names or state.done_at[_index(names, target)] >= 0
        )
        pairs.append((actor_id, ep.IDLE if stale else target))
    return tuple(pairs)


def _find_non_intervention(
    episode: ep.Episode,
    solver: _Solver,
    plan: Plan,
) -> Optional[NonIntervention]:
    for review in range(len(episode.review_ticks)):
        state = state_after(episode, plan, review)
        standing = episode.default_allocation if review == 0 else plan[review - 1]
        keep = _standing(episode, state, standing)
        options = feasible(episode, state)
        if keep not in options:
            continue
        here = solver.best(state, review)
        following = advance(episode, state, keep, window_end(episode, review))
        if solver.best(following, review + 1) != here:
            continue
        alternatives = [
            solver.best(advance(episode, state, other, window_end(episode, review)), review + 1)
            for other in options
            if other != keep
        ]
        if not alternatives or min(alternatives) >= here:
            continue
        return NonIntervention(
            at_review=review,
            allocation=keep,
            margin=here - min(alternatives),
            alternatives_optimal=sum(1 for value in alternatives if value == here),
            why=(
                f"at review {review} holding the standing allocation reaches the optimum, "
                f"and the worst available change costs {here - min(alternatives)}"
            ),
        )
    return None


# ── the solution ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Solution:
    """Everything the oracle knows about one episode. Pure data, pinned in CI."""

    episode_id: str
    optimum: int
    optimal_plan: Plan
    floor: int
    greedy_score: int
    greedy_plan: Plan
    frontier: tuple[tuple[int, int], ...]
    trap: Optional[Trap]
    non_intervention: Optional[NonIntervention]

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "optimum": self.optimum,
            "optimal_plan": [[list(pair) for pair in step] for step in self.optimal_plan],
            "floor": self.floor,
            "greedy_score": self.greedy_score,
            "greedy_plan": [[list(pair) for pair in step] for step in self.greedy_plan],
            "frontier": [list(point) for point in self.frontier],
            "trap": self.trap.to_dict() if self.trap is not None else None,
            "non_intervention": (
                self.non_intervention.to_dict() if self.non_intervention is not None else None
            ),
        }


def _frontier(points: frozenset[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Non-dominated ``(utility, spend)`` points: more utility, less spend."""
    kept = [
        point
        for point in points
        if not any(
            other != point and other[0] >= point[0] and other[1] <= point[1] for other in points
        )
    ]
    return tuple(sorted(kept, key=lambda point: (-point[0], point[1])))


def solve(episode: ep.Episode) -> Solution:
    """Solve *episode* exactly. Deterministic; no clock, no network, no thread."""
    solver = _solver_for(episode)
    start = initial_state(episode)
    optimum = solver.best(start, 0)
    optimal = solver.plan(start, 0)
    held = tuple(episode.default_allocation for _ in episode.review_ticks)
    greedy = greedy_plan(episode)
    return Solution(
        episode_id=episode.id,
        optimum=optimum,
        optimal_plan=optimal,
        floor=score_plan(episode, held),
        greedy_score=score_plan(episode, greedy),
        greedy_plan=greedy,
        frontier=_frontier(solver.points(start, 0)),
        trap=_find_trap(episode, solver),
        non_intervention=_find_non_intervention(episode, solver, optimal),
    )


def schema_report(episode: ep.Episode, solution: Solution) -> dict[str, bool]:
    """All eight required properties: six structural, two the solver verified."""
    report = dict(ep.structural_report(episode))
    report[ep.PROPERTY_TRAP] = solution.trap is not None and solution.trap.forgone > 0
    report[ep.PROPERTY_NON_INTERVENTION] = solution.non_intervention is not None
    return report


def invalidity(episode: ep.Episode, solution: Solution) -> tuple[str, ...]:
    """Why this episode may not be scored, or ``()``. Reported, never dropped.

    Condition 7 of the verdict rule requires every invalid episode to be
    reported. An episode that fails here is not quietly regenerated with a
    different seed — it is named.
    """
    reasons: list[str] = []
    for name, ok in schema_report(episode, solution).items():
        if not ok:
            reasons.append(f"schema property {name!r} does not hold: {ep.SCHEMA_WHY[name]}")
    if solution.optimum <= solution.floor:
        reasons.append(
            "no headroom over the default scope: the host default already scores the "
            "optimum, so no strategic decision could improve on it"
        )
    if solution.optimum <= solution.greedy_score:
        reasons.append(
            "no headroom over the local play: the locally attractive plan already scores "
            "the optimum, so the episode cannot separate strategy from local competence"
        )
    return tuple(reasons)


def naive(episode: ep.Episode, from_tick: int) -> ep.Episode:
    """*episode* as it looks to somebody who cannot see past ``from_tick``."""
    raw = episode.to_dict()
    raw["events"] = [event for event in raw["events"] if int(event["tick"]) < from_tick]
    return ep.Episode.from_dict(raw)


def trivial_episode() -> ep.Episode:
    """An episode with no trap — the negative case the invalidity guard must catch.

    One actor, one workstream, one objective it can finish comfortably: the
    locally attractive play *is* the optimum, so there is nothing to fall for.
    Built here rather than in a test so the guard and its counter-example ship
    together.
    """
    return ep.Episode(
        id="trivial-0",
        family="trivial",
        seed=0,
        horizon=4,
        review_ticks=(0, 2),
        budget=99,
        actors=(ep.Actor(id="solo", skills=("build",), rate=3, cost=1),),
        workstreams=(ep.Workstream(id="task", kind="build", effort=6),),
        objectives=(ep.Objective(id="done", value=10, deadline=4, requires=("task",)),),
        constraints=(),
        events=(),
        default_allocation=(("solo", "task"),),
        brief="a world with one right answer and no way to miss it",
    )


# ── the committed fixture ─────────────────────────────────────────────────────


def fixture_for(episode: ep.Episode, solution: Solution) -> dict[str, Any]:
    """The pinned numbers for one episode. Plans are hashed, not spelled out.

    Hashing the plans keeps the fixture readable while still failing on a
    changed decision: a reviewer sees which episode moved and re-pins on
    purpose, rather than skimming past a page of allocation tuples.
    """
    canonical = json.dumps(solution.to_dict(), sort_keys=True, separators=(",", ":"))
    return {
        "optimum": solution.optimum,
        "floor": solution.floor,
        "greedy_score": solution.greedy_score,
        "frontier": [list(point) for point in solution.frontier],
        "trap_at_review": None if solution.trap is None else solution.trap.at_review,
        "trap_forgone": None if solution.trap is None else solution.trap.forgone,
        "hold_at_review": (
            None if solution.non_intervention is None else solution.non_intervention.at_review
        ),
        "hold_margin": (
            None if solution.non_intervention is None else solution.non_intervention.margin
        ),
        "solution_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def load_fixtures(path: Optional[Path] = None) -> Mapping[str, Mapping[str, Any]]:
    """Read the committed oracle fixture. Eager, total, no fallback."""
    resolved = Path(path) if path is not None else FIXTURES_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ep.EpisodeError(f"no ScopeBench fixtures at {resolved}") from missing
    except ValueError as broken:
        raise ep.EpisodeError(f"{resolved} is not readable JSON: {broken}") from broken
    table = raw.get("episodes") if isinstance(raw, Mapping) else None
    if not isinstance(table, Mapping):
        raise ep.EpisodeError(f"{resolved}: 'episodes' must be an object keyed by episode id")
    return {str(name): dict(entry) for name, entry in table.items()}
