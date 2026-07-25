"""The muse's bounded thinking loop (task t10a).

Six properties are load-bearing and every one of them is pinned here:

1. **Pure and deterministic** — no thread, no clock, no wall-time anywhere.
2. **Bounded**, with a *structural* termination proof (AST, not just scenarios).
3. **Tools-off** — the seam is a completion callable and nothing else.
4. **Advisory only** — an insight is text; it can never become a tool decision.
5. **Insights carry the boundary they reasoned about** — the staleness key that
   deviation d1's parallel loop makes essential.
6. **Degrade, never raise** — a failing seam records and stops cleanly.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from embodiment.contract import ContextPacket, ModelResponse, ToolCall
from embodiment.muse import (
    DEFAULT_STALE_LAG,
    DEGRADED_SINK,
    DEGRADED_THINKING,
    DEGRADED_UNREADABLE,
    MARKER_DONE,
    MUSE_AUTHORITY,
    MUSE_EXIT_BUDGET,
    MUSE_EXIT_CONCLUDED,
    MUSE_EXIT_DEGRADED,
    MUSE_EXIT_QUIET,
    MUSE_EXIT_REASONS,
    MuseControls,
    MuseDegradation,
    MuseInsight,
    MuseLoop,
    MuseOrigin,
    MuseOutcome,
    insight_lag,
    is_stale,
)
from embodiment.presence_engine import (
    BOUNDARY_CADENCE_TICK,
    BOUNDARY_INTAKE,
    BoundaryContext,
    MuseComment,
    MusePullSeam,
    PresenceEngine,
    PresenceIO,
)

_MUSE_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "muse.py"


# ── doubles ───────────────────────────────────────────────────────────────────


def _resp(content: str = "", *, prompt: int = 0, completion: int = 0, **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, prompt_tokens=prompt, completion_tokens=completion, **kw)


class Scripted:
    """A scripted tools-off completion seam that records what it was shown.

    Replies are consumed in order; once exhausted the LAST one repeats, so a
    "muse that never concludes" is one short line rather than a generator.
    """

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies) or [_resp("thinking")]
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        self.calls.append([dict(m) for m in messages])
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def turns(self) -> int:
        return len(self.calls)


def _boundary(**kw: Any) -> BoundaryContext:
    fields_ = {"kind": BOUNDARY_CADENCE_TICK, "step_count": 3}
    fields_.update(kw)
    return BoundaryContext(**fields_)


def _loop(*replies: Any, **kw: Any) -> tuple[MuseLoop, Scripted]:
    complete = Scripted(*replies)
    return MuseLoop(complete, **kw), complete


def _muse_tree() -> ast.Module:
    return ast.parse(_MUSE_SRC.read_text(encoding="utf-8"))


def _think_loop_node() -> ast.FunctionDef:
    for node in ast.walk(_muse_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == "_think_loop":
            return node
    raise AssertionError("_think_loop is the bounded loop; it must exist by that name")


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_muse_tree()):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ── 1. a session, end to end ──────────────────────────────────────────────────


class TestOneThinkingSession:
    """The nominal path: iterative turns, one insight per turn that said something."""

    def test_think_returns_an_outcome(self):
        loop, _ = _loop(_resp("the diff touches two seams"), _resp(MARKER_DONE))
        outcome = loop.think(_boundary())
        assert isinstance(outcome, MuseOutcome)
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.turns == 2

    def test_each_thinking_turn_yields_one_insight(self):
        loop, _ = _loop(_resp("first thought"), _resp("second thought"), _resp(MARKER_DONE))
        outcome = loop.think(_boundary())
        assert [i.text for i in outcome.insights] == ["first thought", "second thought"]
        assert [i.turn_index for i in outcome.insights] == [1, 2]

    def test_guidance_lines_split_out_of_the_narration(self):
        loop, _ = _loop(_resp("narration line\nGUIDANCE: check the null case\n" + MARKER_DONE))
        insight = loop.think(_boundary()).insights[0]
        assert insight.text == "narration line"
        assert insight.guidance == "check the null case"

    def test_the_loop_is_iterative_the_muse_reads_its_own_prior_turn(self):
        loop, complete = _loop(_resp("thought one"), _resp("thought two"), _resp(MARKER_DONE))
        loop.think(_boundary())
        second = complete.calls[1]
        assert {"role": "assistant", "content": "thought one"} in second
        assert second[-1]["role"] == "user"

    def test_a_silent_turn_produces_no_insight(self):
        loop, _ = _loop(_resp("   "))
        outcome = loop.think(_boundary())
        assert outcome.insights == []
        assert outcome.exit_reason == MUSE_EXIT_QUIET

    def test_the_seam_cannot_mutate_the_running_history(self):
        seen: list[list[dict[str, Any]]] = []

        def complete(messages: list[dict[str, Any]]) -> ModelResponse:
            seen.append([dict(m) for m in messages])
            messages.clear()
            messages.append({"role": "user", "content": "hijacked"})
            return _resp("thought")

        loop = MuseLoop(complete, controls=MuseControls(max_turns=2))
        loop.think(_boundary())
        assert len(seen[1]) >= 2
        assert seen[1][0]["role"] == "system"

    def test_defaults_are_conservative(self):
        controls = MuseControls()
        assert controls.max_turns == 4
        assert controls.max_quiet_turns == 1
        assert MuseLoop(Scripted()).controls == controls

    def test_sessions_are_counted(self):
        loop, _ = _loop(_resp(MARKER_DONE))
        assert loop.sessions == 0
        loop.think(_boundary())
        loop.think(_boundary())
        assert loop.sessions == 2

    def test_a_missing_boundary_is_survivable(self):
        loop, _ = _loop(_resp("thinking about nothing in particular"), _resp(MARKER_DONE))
        outcome = loop.think(None)  # type: ignore[arg-type]
        assert outcome.exit_reason in MUSE_EXIT_REASONS
        assert outcome.origin.kind == ""


# ── 2. termination ────────────────────────────────────────────────────────────


class TestTerminationMatrix:
    """Four exits, no fifth — the honesty condition, proved structurally."""

    def test_concluded(self):
        loop, complete = _loop(_resp("done thinking " + MARKER_DONE))
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert complete.turns == 1

    def test_the_done_marker_is_case_insensitive_and_stripped_from_the_text(self):
        loop, _ = _loop(_resp("that is all [DONE]"))
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.insights[0].text == "that is all"

    def test_quiet(self):
        loop, complete = _loop(_resp(""))
        assert loop.think(_boundary()).exit_reason == MUSE_EXIT_QUIET
        assert complete.turns == 1

    def test_quiet_tolerance_is_configurable(self):
        loop, complete = _loop(_resp(""), controls=MuseControls(max_turns=5, max_quiet_turns=3))
        assert loop.think(_boundary()).exit_reason == MUSE_EXIT_QUIET
        assert complete.turns == 3

    def test_budget(self):
        loop, complete = _loop(_resp("still thinking"), controls=MuseControls(max_turns=3))
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_BUDGET
        assert outcome.turns == 3
        assert complete.turns == 3

    def test_degraded(self):
        loop, _ = _loop(RuntimeError("muse endpoint down"))
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_DEGRADED

    @pytest.mark.parametrize("max_turns", [0, 1, 2, 3, 9])
    def test_turns_never_exceed_the_budget(self, max_turns):
        loop, complete = _loop(_resp("never done"), controls=MuseControls(max_turns=max_turns))
        outcome = loop.think(_boundary())
        assert complete.turns <= max(1, max_turns)
        assert outcome.turns == complete.turns

    def test_a_budget_of_zero_still_thinks_once(self):
        loop, complete = _loop(_resp("one thought"), controls=MuseControls(max_turns=0))
        assert loop.think(_boundary()).exit_reason == MUSE_EXIT_BUDGET
        assert complete.turns == 1

    def test_a_muse_that_never_concludes_still_stops(self):
        loop, complete = _loop(_resp("and another thing"), controls=MuseControls(max_turns=25))
        loop.think(_boundary())
        assert complete.turns == 25

    @pytest.mark.parametrize(
        "reply,max_turns",
        [
            (_resp(MARKER_DONE), 4),
            (_resp(""), 4),
            (_resp("endless"), 2),
            (_resp("GUIDANCE: only guidance"), 2),
            (RuntimeError("boom"), 4),
            (None, 4),
            ("a bare string reply", 2),
        ],
    )
    def test_every_scenario_exits_through_one_of_the_declared(self, reply, max_turns):
        loop, _ = _loop(reply, controls=MuseControls(max_turns=max_turns))
        assert loop.think(_boundary()).exit_reason in MUSE_EXIT_REASONS

    # ── the structural proofs ────────────────────────────────────────────────

    def test_exit_reasons_has_exactly_four_members(self):
        assert MUSE_EXIT_REASONS == (
            MUSE_EXIT_CONCLUDED,
            MUSE_EXIT_QUIET,
            MUSE_EXIT_BUDGET,
            MUSE_EXIT_DEGRADED,
        )
        assert len(set(MUSE_EXIT_REASONS)) == 4

    def test_module_declares_exactly_four_exit_constants(self):
        names = set()
        for node in _muse_tree().body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("MUSE_EXIT_"):
                        names.add(target.id)
        assert names == {
            "MUSE_EXIT_CONCLUDED",
            "MUSE_EXIT_QUIET",
            "MUSE_EXIT_BUDGET",
            "MUSE_EXIT_DEGRADED",
            "MUSE_EXIT_REASONS",
        }

    def test_think_loop_returns_only_the_declared_exit_constants(self):
        """Structural proof that no fifth exit path exists."""
        returned = set()
        for node in ast.walk(_think_loop_node()):
            if isinstance(node, ast.Return):
                assert isinstance(node.value, ast.Name), ast.dump(node)
                returned.add(node.value.id)
        assert returned == {
            "MUSE_EXIT_CONCLUDED",
            "MUSE_EXIT_QUIET",
            "MUSE_EXIT_BUDGET",
            "MUSE_EXIT_DEGRADED",
        }

    def test_think_loop_raises_nothing_of_its_own(self):
        assert not [n for n in ast.walk(_think_loop_node()) if isinstance(n, ast.Raise)]

    def test_think_loop_catches_nothing_of_its_own(self):
        """Every fault is handled in a helper that RECORDS it; the loop just exits."""
        assert not [n for n in ast.walk(_think_loop_node()) if isinstance(n, ast.Try)]

    def test_think_loop_is_one_counter_bounded_while(self):
        node = _think_loop_node()
        whiles = [n for n in ast.walk(node) if isinstance(n, ast.While)]
        fors = [n for n in ast.walk(node) if isinstance(n, ast.For)]
        assert len(whiles) == 1 and not fors
        # ``while True:`` would be an ast.Constant test — the bound must be a
        # comparison against the budget, so exhausting it is the only outcome.
        assert isinstance(whiles[0].test, ast.Compare), ast.dump(whiles[0].test)

    def test_a_base_exception_still_interrupts_the_host(self):
        """Everything derived from ``Exception`` degrades; a Ctrl-C must not."""

        def hostile(_messages: Any) -> Any:
            raise KeyboardInterrupt

        loop = MuseLoop(hostile)
        boundary = _boundary()
        with pytest.raises(KeyboardInterrupt):
            loop.think(boundary)

    def test_every_exception_class_degrades_rather_than_propagates(self):
        for exc in (RuntimeError("x"), ValueError("y"), OSError("z"), TypeError("w")):
            loop, _ = _loop(exc)
            outcome = loop.think(_boundary())
            assert outcome.exit_reason == MUSE_EXIT_DEGRADED
            assert outcome.degradations[0].code == DEGRADED_THINKING


# ── 3. tools-off ──────────────────────────────────────────────────────────────


class TestToolsOff:
    """The muse reasons; it does not act. The seam is a completion, full stop."""

    def test_the_module_names_no_tool_surface(self):
        source = _MUSE_SRC.read_text(encoding="utf-8")
        for token in (
            "tool_calls",
            "ToolCall",
            "ToolExecutor",
            "tool_executor",
            "subprocess",
            "shlex",
            "os.system",
            "shell_cli",
            "shell-cli",
        ):
            assert token not in source, token

    def test_the_constructor_accepts_no_acting_seam(self):
        import inspect

        params = set(inspect.signature(MuseLoop.__init__).parameters)
        assert params == {"self", "complete", "controls", "system", "sink", "clock"}

    def test_the_loop_exposes_no_acting_surface(self):
        forbidden = {
            "execute",
            "run_tool",
            "dispatch_to_cortex",
            "guide_cortex",
            "tools",
            "executor",
            "approve",
            "deny",
            "rewrite",
        }
        assert not (set(dir(MuseLoop)) & forbidden)

    def test_tool_calls_on_a_response_are_never_read(self):
        """A seam that hands back tool calls gets them ignored, not executed."""
        reply = _resp(
            "I would like to write a file " + MARKER_DONE,
            tool_calls=[ToolCall(id="1", name="write_file", arguments={"path": "/etc/passwd"})],
        )
        loop, _ = _loop(reply)
        outcome = loop.think(_boundary())
        assert outcome.insights[0].text == "I would like to write a file"
        assert not hasattr(outcome.insights[0], "tool_calls")
        assert "write_file" not in outcome.insights[0].guidance

    def test_an_insight_never_carries_a_callable(self):
        loop, _ = _loop(_resp("thought\nGUIDANCE: advice " + MARKER_DONE))
        insight = loop.think(_boundary()).insights[0]
        for f in fields(insight):
            value = getattr(insight, f.name)
            assert not callable(value), f.name
            assert isinstance(value, (str, int, float, MuseOrigin, type(None))), f.name


# ── 4. advisory only — proposes, never decides ────────────────────────────────


class TestAdvisoryOnly:
    """colleague#352's promise, held by the mechanism rather than the prose."""

    def test_no_insight_field_names_a_decision(self):
        decision_vocabulary = {
            "decision",
            "deny",
            "denied",
            "rewrite",
            "allow",
            "approve",
            "approved",
            "permit",
            "veto",
            "block",
            "arguments",
            "tool",
        }
        for shape in (MuseInsight, MuseOutcome, MuseOrigin, MuseDegradation, MuseControls):
            names = {f.name for f in fields(shape)}
            assert not (names & decision_vocabulary), shape.__name__

    def test_the_module_imports_no_decision_type(self):
        """It cannot mint a tool decision: no decision type is even in scope."""
        modules = _imported_modules()
        assert "embodiment.loop" not in modules
        import embodiment.muse as mod

        forbidden = {
            "HookDecision",
            "HookEvent",
            "DECISION_ALLOW",
            "DECISION_DENY",
            "DECISION_REWRITE",
            "PresenceExecutor",
        }
        assert not (set(vars(mod)) & forbidden)

    def test_a_muse_demanding_a_denial_produces_only_text(self):
        loop, _ = _loop(_resp("GUIDANCE: deny every write_file call " + MARKER_DONE))
        insight = loop.think(_boundary()).insights[0]
        assert insight.guidance == "deny every write_file call"
        assert isinstance(insight.guidance, str)

    def test_an_insight_is_a_muse_comment(self):
        """Extends the pump's existing output shape rather than paralleling it."""
        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        insight = loop.think(_boundary()).insights[0]
        assert isinstance(insight, MuseComment)

    def test_runaway_output_is_capped_before_it_reaches_the_acting_loop(self):
        loop, _ = _loop(
            _resp("x" * 5000 + "\n" + MARKER_DONE),
            controls=MuseControls(max_insight_chars=100),
        )
        assert len(loop.think(_boundary()).insights[0].text) == 100


# ── 5. an insight carries the boundary it reasoned about ──────────────────────


class TestOriginAndStaleness:
    """d1: an insight computed against step 3 can land at step 40."""

    def test_every_insight_stamps_its_boundary(self):
        loop, _ = _loop(_resp("a"), _resp("b"), _resp(MARKER_DONE))
        outcome = loop.think(_boundary(kind=BOUNDARY_INTAKE, step_count=7, reason="phase-change"))
        for insight in outcome.insights:
            assert insight.origin.kind == BOUNDARY_INTAKE
            assert insight.origin.step_count == 7
            assert insight.origin.reason == "phase-change"

    def test_the_outcome_and_its_insights_share_one_origin(self):
        loop, _ = _loop(_resp("a " + MARKER_DONE))
        outcome = loop.think(_boundary(step_count=11))
        assert outcome.origin == outcome.insights[0].origin

    def test_the_origin_session_orders_insights_across_sessions(self):
        loop, _ = _loop(_resp("a " + MARKER_DONE))
        first = loop.think(_boundary(step_count=1))
        second = loop.think(_boundary(step_count=2))
        assert first.origin.session == 1
        assert second.origin.session == 2

    def test_an_insight_records_the_step_it_reasoned_about_not_where_it_lands(self):
        """The whole point of the stamp: the acting loop races ahead meanwhile."""
        acting = {"step": 3}

        def complete(_messages: list[dict[str, Any]]) -> ModelResponse:
            acting["step"] += 20  # Qwen kept working while the muse thought
            return _resp("still pondering")

        loop = MuseLoop(complete, controls=MuseControls(max_turns=2))
        outcome = loop.think(_boundary(step_count=3))
        assert [i.origin.step_count for i in outcome.insights] == [3, 3]
        assert insight_lag(outcome.insights[0], step_count=acting["step"]) == 40
        assert is_stale(outcome.insights[0], step_count=acting["step"]) is True

    def test_lag_is_zero_for_a_fresh_insight(self):
        loop, _ = _loop(_resp("fresh " + MARKER_DONE))
        insight = loop.think(_boundary(step_count=5)).insights[0]
        assert insight_lag(insight, step_count=5) == 0
        assert is_stale(insight, step_count=5) is False

    def test_an_insight_never_reads_as_stale_from_behind(self):
        loop, _ = _loop(_resp("fresh " + MARKER_DONE))
        insight = loop.think(_boundary(step_count=9)).insights[0]
        assert insight_lag(insight, step_count=2) == 0
        assert is_stale(insight, step_count=2) is False

    def test_the_staleness_threshold_is_the_consumers_to_state(self):
        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        insight = loop.think(_boundary(step_count=0)).insights[0]
        assert is_stale(insight, step_count=DEFAULT_STALE_LAG) is False
        assert is_stale(insight, step_count=DEFAULT_STALE_LAG + 1) is True
        assert is_stale(insight, step_count=100, max_lag=1000) is False

    def test_staleness_helpers_never_raise_on_junk(self):
        junk = MuseInsight(text="t", origin=MuseOrigin(step_count=2))
        assert insight_lag(junk, step_count="not a number") == 0  # type: ignore[arg-type]
        assert is_stale(junk, step_count=None, max_lag=None) is False  # type: ignore[arg-type]

    def test_a_boundary_with_a_junk_step_count_still_yields_an_origin(self):
        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        outcome = loop.think(_boundary(step_count="七"))  # type: ignore[arg-type]
        assert outcome.origin.step_count == 0


# ── 6. cost without a clock ───────────────────────────────────────────────────


class TestCostWithoutAClock:
    """A multi-turn loop with no clock reports cost in TURNS and TOKENS.

    ``latency`` is only ever a measurement, never an estimate: absent a clock it
    stays ``None`` on every insight and on the outcome. A fabricated ``0.0``
    would be exactly the silent dishonesty C3 forbids.
    """

    def test_turns_are_the_loops_own_cost_unit(self):
        loop, _ = _loop(_resp("a"), _resp("b"), _resp("c " + MARKER_DONE))
        assert loop.think(_boundary()).turns == 3

    def test_no_clock_means_latency_is_none_not_zero(self):
        loop, _ = _loop(_resp("a"), _resp(MARKER_DONE))
        outcome = loop.think(_boundary())
        assert outcome.latency is None
        assert all(i.latency is None for i in outcome.insights)

    def test_an_injected_clock_is_the_only_source_of_latency(self):
        ticks = iter([100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5])
        loop, _ = _loop(_resp("a"), _resp(MARKER_DONE), clock=lambda: next(ticks))
        outcome = loop.think(_boundary())
        assert outcome.latency is not None and outcome.latency > 0
        assert all(i.latency is not None for i in outcome.insights)

    def test_a_raising_clock_degrades_the_measurement_not_the_thinking(self):
        def broken() -> float:
            raise OSError("no clock here")

        loop, _ = _loop(_resp("a thought " + MARKER_DONE), clock=broken)
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.latency is None
        assert outcome.insights[0].text == "a thought"

    def test_tokens_are_summed_from_the_seams_own_report(self):
        loop, _ = _loop(
            _resp("a", prompt=10, completion=5),
            _resp(MARKER_DONE, prompt=20, completion=1),
        )
        outcome = loop.think(_boundary())
        assert outcome.insights[0].tokens == 15
        assert outcome.tokens == 36

    def test_an_unreported_count_stays_none_rather_than_a_fabricated_zero(self):
        loop, _ = _loop(_resp("a thought " + MARKER_DONE))
        outcome = loop.think(_boundary())
        assert outcome.tokens is None
        assert outcome.insights[0].tokens is None

    def test_a_bare_string_reply_reports_no_tokens_at_all(self):
        loop, _ = _loop("just a string " + MARKER_DONE)
        outcome = loop.think(_boundary())
        assert outcome.insights[0].text == "just a string"
        assert outcome.tokens is None

    def test_a_partly_reporting_seam_still_sums_honestly(self):
        loop, _ = _loop(_resp("a", prompt=7), _resp(MARKER_DONE))
        outcome = loop.think(_boundary())
        assert outcome.tokens == 7


# ── 7. degrade, never raise (C3) ──────────────────────────────────────────────


class TestDegradeNeverRaise:
    """Every non-nominal path RECORDS a host-visible transition."""

    def test_a_failing_seam_records_and_stops_cleanly(self):
        loop, complete = _loop(RuntimeError("connection refused"))
        outcome = loop.think(_boundary(step_count=4))
        assert outcome.exit_reason == MUSE_EXIT_DEGRADED
        assert len(outcome.degradations) == 1
        record = outcome.degradations[0]
        assert record.code == DEGRADED_THINKING
        assert "connection refused" in record.reason
        assert record.step_index == 4
        assert record.model_turns == 1
        assert complete.turns == 1

    def test_partial_thinking_survives_a_mid_session_failure(self):
        loop, _ = _loop(_resp("a useful half-thought"), RuntimeError("died"))
        outcome = loop.think(_boundary())
        assert [i.text for i in outcome.insights] == ["a useful half-thought"]
        assert outcome.exit_reason == MUSE_EXIT_DEGRADED

    def test_a_seam_returning_none_is_a_recorded_degradation(self):
        loop, _ = _loop(None)
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_DEGRADED
        assert outcome.degradations[0].code == DEGRADED_THINKING

    def test_a_response_whose_content_explodes_degrades(self):
        class Hostile:
            @property
            def content(self) -> str:
                raise RuntimeError("content is a landmine")

        loop, _ = _loop(Hostile())
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_DEGRADED
        assert outcome.degradations[0].code == DEGRADED_THINKING

    def test_a_raising_sink_is_recorded_and_disabled_but_never_fatal(self):
        seen: list[MuseInsight] = []

        def sink(insight: MuseInsight) -> None:
            seen.append(insight)
            raise RuntimeError("queue is closed")

        loop, _ = _loop(_resp("a"), _resp("b"), _resp(MARKER_DONE), sink=sink)
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert len(seen) == 1  # disabled after the first failure, never retried
        assert len(outcome.insights) == 2  # the ledger still has both
        assert [d.code for d in outcome.degradations] == [DEGRADED_SINK]

    def test_a_working_sink_sees_every_insight_as_it_is_produced(self):
        seen: list[MuseInsight] = []
        loop, _ = _loop(_resp("a"), _resp("b"), _resp(MARKER_DONE), sink=seen.append)
        outcome = loop.think(_boundary())
        assert [i.text for i in seen] == ["a", "b"]
        assert seen == outcome.insights

    def test_an_unreadable_boundary_field_is_named_not_swallowed(self):
        class Landmine:
            def __str__(self) -> str:
                raise RuntimeError("cannot render")

        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        outcome = loop.think(_boundary(task_state=Landmine()))
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        codes = [d.code for d in outcome.degradations]
        assert codes == [DEGRADED_UNREADABLE]
        assert "task_state" in outcome.degradations[0].reason

    def test_the_degradation_shape_matches_the_loops(self):
        """t9 folds one ledger, not two: same field names, same ``to_dict``."""
        from embodiment.loop import LoopDegradation

        assert [f.name for f in fields(MuseDegradation)] == [
            f.name for f in fields(LoopDegradation)
        ]
        record = MuseDegradation(code="c", reason="r", step_index=2, model_turns=3)
        assert (
            record.to_dict()
            == LoopDegradation(code="c", reason="r", step_index=2, model_turns=3).to_dict()
        )

    def test_a_runaway_reason_is_capped(self):
        loop, _ = _loop(RuntimeError("x" * 5000))
        assert len(loop.think(_boundary()).degradations[0].reason) <= 500

    def test_no_bare_suppress_of_everything(self):
        """C3: nothing degrades silently — no ``except: pass``."""
        for node in ast.walk(_muse_tree()):
            if isinstance(node, ast.ExceptHandler):
                body = node.body
                assert not (len(body) == 1 and isinstance(body[0], ast.Pass)), ast.dump(node)

    def test_the_outcome_reports_whether_it_degraded(self):
        clean, _ = _loop(_resp(MARKER_DONE))
        assert clean.think(_boundary()).degraded is False
        broken, _ = _loop(RuntimeError("x"))
        assert broken.think(_boundary()).degraded is True

    def test_a_boundary_attribute_that_explodes_is_named_too(self):
        """The other unreadable path: access raises, rather than ``str()``."""

        class Landmine:
            kind = BOUNDARY_CADENCE_TICK
            step_count = 2
            reason = ""
            packet = None
            operator_input = None
            flight = ""
            feed_tail = ""
            history = None

            @property
            def task_state(self) -> str:
                raise RuntimeError("state is not readable")

        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        outcome = loop.think(Landmine())  # type: ignore[arg-type]
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert [d.code for d in outcome.degradations] == [DEGRADED_UNREADABLE]
        assert "task_state" in outcome.degradations[0].reason

    def test_a_history_entry_that_refuses_to_be_read_is_named(self):
        class Hostile(dict):
            def get(self, *_args: Any, **_kw: Any) -> Any:
                raise RuntimeError("no")

        loop, _ = _loop(_resp("thought " + MARKER_DONE))
        outcome = loop.think(_boundary(history=[Hostile()]))
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.degradations[0].code == DEGRADED_UNREADABLE

    def test_staleness_survives_an_insight_whose_origin_explodes(self):
        class Hostile:
            @property
            def origin(self) -> MuseOrigin:
                raise RuntimeError("gone")

        assert insight_lag(Hostile(), step_count=9) == 9  # type: ignore[arg-type]
        assert is_stale(Hostile(), step_count=9) is True  # type: ignore[arg-type]

    def test_a_clock_that_dies_mid_session_reports_no_latency(self):
        state = {"calls": 0}

        def flaky() -> float:
            state["calls"] += 1
            if state["calls"] > 1:
                raise OSError("clock died")
            return 10.0

        loop, _ = _loop(_resp("a thought " + MARKER_DONE), clock=flaky)
        outcome = loop.think(_boundary())
        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.latency is None
        assert outcome.insights[0].latency is None


class TestLedgerSerialization:
    """Task t9 folds these; every shape serializes to plain JSON-able data."""

    def test_an_outcome_serializes_whole(self):
        loop, _ = _loop(
            _resp("a thought\nGUIDANCE: an advisory line", prompt=3, completion=4),
            _resp(MARKER_DONE),
        )
        data = loop.think(_boundary(kind=BOUNDARY_INTAKE, step_count=6)).to_dict()
        assert data["exit_reason"] == MUSE_EXIT_CONCLUDED
        assert data["turns"] == 2
        assert data["tokens"] == 7
        assert data["latency"] is None
        assert data["origin"] == {
            "kind": BOUNDARY_INTAKE,
            "step_count": 6,
            "session": 1,
            "reason": "",
        }
        assert data["insights"] == [
            {
                "text": "a thought",
                "guidance": "an advisory line",
                "tokens": 7,
                "latency": None,
                "turn_index": 1,
                "origin": data["origin"],
            }
        ]
        assert data["degradations"] == []

    def test_a_degraded_outcome_serializes_its_ledger(self):
        loop, _ = _loop(RuntimeError("endpoint refused"))
        data = loop.think(_boundary(step_count=4)).to_dict()
        assert data["degradations"] == [
            {
                "code": DEGRADED_THINKING,
                "reason": "endpoint refused",
                "step_index": 4,
                "model_turns": 1,
            }
        ]


# ── 8. the authority boundary rides every turn ────────────────────────────────


class TestAuthorityFraming:
    """colleague#352: the muse's authority boundary is on EVERY thinking path."""

    def test_every_turn_carries_the_authority_system_message(self):
        loop, complete = _loop(_resp("a"), _resp("b"), _resp("c"), _resp(MARKER_DONE))
        loop.think(_boundary())
        assert complete.turns == 4
        for call in complete.calls:
            assert call[0]["role"] == "system"
            assert MUSE_AUTHORITY in call[0]["content"]

    def test_host_framing_is_appended_and_cannot_replace_the_boundary(self):
        loop, complete = _loop(_resp(MARKER_DONE), system="You are Gwen's inner voice.")
        loop.think(_boundary())
        system = complete.calls[0][0]["content"]
        assert MUSE_AUTHORITY in system
        assert "You are Gwen's inner voice." in system

    def test_the_authority_text_states_the_three_limits(self):
        lowered = MUSE_AUTHORITY.lower()
        assert "no tools" in lowered
        assert "propose" in lowered
        assert "final authority" in lowered

    def test_the_default_framing_claims_no_identity_and_no_second_mind(self):
        """t12 owns identity; an unconfigured muse names nobody."""
        lowered = MUSE_AUTHORITY.lower()
        for token in ("gwen", "qwen", "gemma", "colleague", "claude"):
            assert token not in lowered, token

    def test_the_boundary_is_rendered_into_the_first_user_message(self):
        loop, complete = _loop(_resp(MARKER_DONE))
        loop.think(
            _boundary(
                kind=BOUNDARY_INTAKE,
                step_count=12,
                operator_input="please hurry",
                task_state="running lint",
            )
        )
        rendered = complete.calls[0][1]["content"]
        assert complete.calls[0][1]["role"] == "user"
        for fragment in (BOUNDARY_INTAKE, "12", "please hurry", "running lint"):
            assert fragment in rendered

    def test_the_operators_verbatim_request_reaches_the_muse_unmodified(self):
        original = "  Fix   the\tBUG in Parser.py  "
        loop, complete = _loop(_resp(MARKER_DONE))
        loop.think(_boundary(packet=ContextPacket(original=original, interpretation="fix parser")))
        rendered = complete.calls[0][1]["content"]
        assert original in rendered
        assert "fix parser" in rendered

    def test_rolling_history_is_rendered_when_the_pump_supplies_it(self):
        loop, complete = _loop(_resp(MARKER_DONE))
        loop.think(
            _boundary(history=[{"role": "user", "content": "earlier"}, "a bare line"]),
        )
        rendered = complete.calls[0][1]["content"]
        assert "earlier" in rendered and "a bare line" in rendered

    def test_context_fields_are_capped(self):
        loop, complete = _loop(_resp(MARKER_DONE), controls=MuseControls(max_context_chars=20))
        loop.think(_boundary(task_state="y" * 500))
        assert "y" * 21 not in complete.calls[0][1]["content"]


# ── 9. import posture (C1 / C2 / no threads, no clock) ────────────────────────


class TestImportPosture:
    """No thread, no clock, pure stdlib, no colleague, no front module."""

    def test_no_forbidden_imports(self):
        forbidden = {"time", "threading", "asyncio", "sched", "datetime", "subprocess", "signal"}
        for node in ast.walk(_muse_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, node.module

    def test_the_source_names_no_concurrency_primitive(self):
        source = _MUSE_SRC.read_text(encoding="utf-8")
        for token in ("Thread(", "Lock(", "Queue(", "Event(", "await ", "async def"):
            assert token not in source, token

    def test_imports_only_stdlib_and_embodiment(self):
        stdlib_ok = {"__future__", "dataclasses", "re", "typing"}
        for module in _imported_modules():
            assert module in stdlib_ok or module.split(".")[0] == "embodiment", module

    def test_no_colleague_import(self):
        assert not any(m.split(".")[0] == "colleague" for m in _imported_modules())

    def test_no_front_module_import(self):
        modules = _imported_modules()
        assert not any(m.startswith(("embodiment.cli", "embodiment.explain")) for m in modules)


# ── 10. the seam t10b will wrap ───────────────────────────────────────────────


class TestSeamCompatibility:
    """The loop is usable through the pump's synchronous PULL seam, unchanged.

    Task t10b reshaped the pump's primary ``MuseSeam`` into a drain
    (``consider`` / ``drain`` / ``degradation``) and kept t7's synchronous
    callable as ``MusePullSeam``. This shim is why that could happen without a
    flag day: a bare ``MuseLoop`` is still a working muse for any host that
    wants one thinking session per boundary on its own thread, and wrapping it
    in a ``ThreadedMuseRunner`` is what makes it parallel.
    """

    def test_a_muse_loop_satisfies_the_pumps_pull_seam(self):
        loop, _ = _loop(_resp(MARKER_DONE))
        assert isinstance(loop, MusePullSeam)

    def test_calling_it_folds_one_session_into_one_comment(self):
        loop, _ = _loop(
            _resp("first\nGUIDANCE: try the other branch"),
            _resp("second " + MARKER_DONE),
        )
        comment = loop(_boundary())
        assert isinstance(comment, MuseComment)
        assert comment.text == "first\nsecond"
        assert comment.guidance == "try the other branch"

    def test_calling_it_returns_none_when_the_muse_stayed_quiet(self):
        loop, _ = _loop(_resp(""))
        assert loop(_boundary()) is None

    def test_a_failed_session_reads_as_silence_to_a_synchronous_consumer(self):
        loop, _ = _loop(RuntimeError("down"))
        assert loop(_boundary()) is None

    def test_the_real_presence_engine_drives_it_end_to_end(self):
        rendered: list[str] = []
        guided: list[str] = []
        io = PresenceIO(render=rendered.append, append_guidance=guided.append)
        reply = "I notice the tests were never run\nGUIDANCE: run pytest\n" + MARKER_DONE
        loop, _ = _loop(_resp(reply))
        engine = PresenceEngine(io=io, muse=loop)
        engine.acknowledge(ContextPacket(original="ship it", ack="on it"))
        assert any("I notice the tests were never run" in line for line in rendered)
        assert guided == ["run pytest"]
        assert engine.muse_degraded is False
