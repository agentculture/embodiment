"""One spoken turn, proved against the task's three acceptance criteria.

The criteria, and where each is proved:

1. *a turn with an empty registry terminates in one model call; registering a
   fake tool makes the same code path execute it, with no edit to* ``turn.py``
   — :class:`TestCriterion1EmptyRegistryAndAdditiveTools`.
2. ``loop.py`` *is byte-identical to the archived-core version and its AST
   termination tests pass* — :class:`TestCriterion2LoopIsZeroDiff`.
3. *a truncated or empty completion yields a degradation record and a spoken
   fallback, never silence* — :class:`TestCriterion3NeverSilence`.

Everything else here guards the constraints the criteria assume: the verbatim
invariant, the unconfigured-identity byte-identity rule, and never-raise.
"""

from __future__ import annotations

import ast
import subprocess  # nosec B404 - fixed argv, no shell, test-only git probe
from pathlib import Path
from typing import Any, Callable

import pytest

from embodiment.contract import ModelResponse, ToolCall
from embodiment.loop import EXIT_BUDGET, EXIT_FINISHED, EXIT_STOPPED
from embodiment.tools import DEGRADED_TOOL_FAILED, ToolRegistry, bind_tools
from embodiment.turn import (
    DEGRADED_BUDGET_EXHAUSTED,
    DEGRADED_EMPTY_COMPLETION,
    DEGRADED_FALLBACK_BLANK,
    DEGRADED_SEAM_ABORTED,
    DEGRADED_TOOLS_UNBOUND,
    DEGRADED_TRUNCATION_SUSPECTED,
    DEGRADED_TRUNCATION_UNDETECTABLE,
    FALLBACK_TEXT,
    SYSTEM_PROMPT,
    TurnConfig,
    TurnResult,
    turn,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_TAG = "archive/pre-realtime-0.14.0"
LOOP_PATH = "embodiment/loop.py"


# ── fake seams ────────────────────────────────────────────────────────────────


class Scripted:
    """A ``complete`` that returns a scripted response per call and counts them."""

    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        if not self.responses:
            raise AssertionError("the seam was called more times than the script allows")
        return self.responses.pop(0)

    @property
    def count(self) -> int:
        return len(self.calls)


def _says(text: str, *, completion_tokens: int = 7) -> ModelResponse:
    return ModelResponse(content=text, completion_tokens=completion_tokens)


def _calls(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name=name, arguments=arguments)],
        completion_tokens=5,
    )


# ── criterion 1 ───────────────────────────────────────────────────────────────


class TestCriterion1EmptyRegistryAndAdditiveTools:
    """An empty registry costs one completion; a registered tool just runs."""

    def test_an_empty_registry_terminates_in_exactly_one_model_call(self) -> None:
        seam = Scripted(_says("שלום, אני כאן."))
        result = turn("שלום", seam)
        assert seam.count == 1
        assert result.steps == 1
        assert result.spoken == "שלום, אני כאן."
        assert result.exit_reason == EXIT_STOPPED
        assert result.degradations == ()
        assert result.tool_calls == ()

    def test_the_default_registry_is_empty(self) -> None:
        assert ToolRegistry().empty

    def test_registering_a_tool_makes_the_same_code_path_execute_it(self) -> None:
        """The additive-growth criterion: no edit to ``turn.py`` is involved."""
        ran: list[dict[str, Any]] = []

        def weather(city: str) -> str:
            ran.append({"city": city})
            return f"{city}: 24 מעלות"

        registry = ToolRegistry()
        registry.register("weather", {"type": "object"}, weather)

        seam = Scripted(_calls("weather", city="חיפה"), _says("בחיפה 24 מעלות."))
        bound = bind_tools(lambda messages, *, tools: seam(messages), registry)
        result = turn("מה מזג האוויר בחיפה?", bound, tools=registry)

        assert ran == [{"city": "חיפה"}]
        assert result.tool_calls == ("weather",)
        assert result.spoken == "בחיפה 24 מעלות."
        assert seam.count == 2
        assert result.degradations == ()

    def test_a_finishing_tool_ends_the_turn_through_the_same_path(self) -> None:
        registry = ToolRegistry()
        registry.register("answer", {}, lambda text: text, finishes=True)
        seam = Scripted(_calls("answer", text="כן."))
        result = turn("אפשר?", seam, tools=registry)
        assert result.exit_reason == EXIT_FINISHED
        assert result.spoken == "כן."
        assert seam.count == 1

    def test_turn_py_names_no_tool_of_its_own(self) -> None:
        """Growth is additive because the turn module knows no tool by name."""
        source = (REPO_ROOT / "embodiment" / "turn.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructed = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "ToolSpec" not in constructed
        registrations = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"register", "add"}
        ]
        assert registrations == []

    def test_a_tool_that_raises_is_recorded_and_the_turn_still_speaks(self) -> None:
        def boom() -> str:
            raise RuntimeError("kaboom")

        registry = ToolRegistry()
        registry.register("boom", {}, boom)
        seam = Scripted(_calls("boom"), _says("משהו השתבש, אבל אני כאן."))
        result = turn("תנסי", seam, tools=registry)

        assert DEGRADED_TOOL_FAILED in {d.code for d in result.degradations}
        assert result.spoken == "משהו השתבש, אבל אני כאן."


# ── criterion 2 ───────────────────────────────────────────────────────────────


def _archived_loop() -> bytes:
    """``loop.py`` as of the archived-core tag, or skip when the tag is absent."""
    tags = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        ["git", "tag", "--list", ARCHIVE_TAG],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if tags.returncode != 0 or not tags.stdout.strip():
        pytest.skip(f"archive tag {ARCHIVE_TAG} is not present in this checkout")
    show = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        ["git", "show", f"{ARCHIVE_TAG}:{LOOP_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if show.returncode != 0:
        pytest.skip(f"{LOOP_PATH} is not readable at {ARCHIVE_TAG}")
    return show.stdout


class TestCriterion2LoopIsZeroDiff:
    """``loop.py`` is untouched by this task — proved, not asserted in prose."""

    def test_loop_py_is_byte_identical_to_the_archived_core(self) -> None:
        archived = _archived_loop()
        current = (REPO_ROOT / LOOP_PATH).read_bytes()
        assert current == archived, "loop.py diverged from the archived core"

    def test_the_ast_termination_guards_are_still_in_the_suite(self) -> None:
        """Criterion 2's second half: the guards exist and are collected here."""
        from tests import test_loop

        assert hasattr(test_loop, "TestTerminationMatrix")
        names = dir(test_loop.TestTerminationMatrix)
        assert "test_work_loop_returns_only_the_three_exit_constants" in names
        assert "test_work_loop_raises_nothing_of_its_own" in names

    def test_the_work_loop_still_has_exactly_three_returns_and_no_raise(self) -> None:
        """A local re-read, so this file fails too if the loop is edited."""
        tree = ast.parse((REPO_ROOT / LOOP_PATH).read_text(encoding="utf-8"))
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_work_loop"
        )
        assert len([n for n in ast.walk(node) if isinstance(n, ast.Return)]) == 3
        assert not [n for n in ast.walk(node) if isinstance(n, ast.Raise)]


# ── criterion 3 ───────────────────────────────────────────────────────────────


class TestCriterion3NeverSilence:
    """A truncated or empty completion degrades visibly and still speaks."""

    def test_a_completion_at_the_ceiling_degrades_and_still_speaks(self) -> None:
        cfg = TurnConfig(max_tokens=64)
        seam = Scripted(_says("התחלתי לענות ואז", completion_tokens=64))
        result = turn("ספרי לי", seam, config=cfg)

        codes = {d.code for d in result.degradations}
        assert DEGRADED_TRUNCATION_SUSPECTED in codes
        assert result.spoken.startswith("התחלתי לענות ואז")
        assert result.spoken.endswith(cfg.truncation_suffix)
        assert result.spoken.strip()

    def test_the_truncation_reason_names_the_missing_finish_reason(self) -> None:
        seam = Scripted(_says("חצי משפט", completion_tokens=32))
        result = turn("ספרי", seam, config=TurnConfig(max_tokens=32))
        reason = next(
            d.reason for d in result.degradations if d.code == DEGRADED_TRUNCATION_SUSPECTED
        )
        assert "finish_reason" in reason
        assert "proxy" in reason

    def test_truncation_with_no_prose_at_all_speaks_the_fallback(self) -> None:
        seam = Scripted(_says("", completion_tokens=32))
        result = turn("ספרי", seam, config=TurnConfig(max_tokens=32))
        codes = {d.code for d in result.degradations}
        assert DEGRADED_TRUNCATION_SUSPECTED in codes
        assert DEGRADED_EMPTY_COMPLETION in codes
        assert result.spoken == FALLBACK_TEXT

    def test_an_empty_completion_degrades_and_speaks_the_fallback(self) -> None:
        seam = Scripted(_says(""))
        result = turn("שלום", seam)
        assert [d.code for d in result.degradations] == [DEGRADED_EMPTY_COMPLETION]
        assert result.spoken == FALLBACK_TEXT
        assert result.spoken != ""

    def test_a_clean_turn_below_the_ceiling_records_nothing(self) -> None:
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=8)))
        assert result.degradations == ()
        assert result.spoken.strip()

    def test_an_unreported_token_count_is_not_read_as_truncation(self) -> None:
        """``completion_tokens=0`` means unreported, never "hit a zero ceiling"."""
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=0)))
        assert DEGRADED_TRUNCATION_SUSPECTED not in {d.code for d in result.degradations}
        assert result.spoken.strip()

    def test_a_zero_max_tokens_config_disables_the_proxy_rather_than_firing_always(self) -> None:
        result = turn(
            "שלום",
            Scripted(_says("שלום לך.", completion_tokens=99)),
            config=TurnConfig(max_tokens=0),
        )
        assert result.degradations == ()
        assert result.spoken.strip()

    def test_a_custom_fallback_is_honoured(self) -> None:
        cfg = TurnConfig(fallback_text="אין לי מה לומר.")
        result = turn("שלום", Scripted(_says("")), config=cfg)
        assert result.spoken == "אין לי מה לומר."
        assert result.spoken.strip()


# ── fix 1: an undetectable truncation is still a recorded one ─────────────────


class TestTruncationUndetectableIsRecorded:
    """An inert detector is a silent degradation. Say so, once, per turn.

    A docstring saying "not detected" is not a record: a host reading
    :class:`~embodiment.turn.TurnResult` would see a clean turn and conclude the
    words were complete. If the rig's seam never reports usage, the ceiling
    proxy is dead — and this is what makes that visible.
    """

    def test_absent_usage_on_a_spoken_turn_is_recorded(self) -> None:
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=0)))
        codes = [d.code for d in result.degradations]
        assert codes == [DEGRADED_TRUNCATION_UNDETECTABLE]

    def test_the_record_does_not_change_what_is_spoken(self) -> None:
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=0)))
        assert result.spoken == "שלום לך."

    def test_the_reason_names_the_missing_count(self) -> None:
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=0)))
        reason = result.degradations[0].reason
        assert "completion_tokens" in reason
        assert "truncation" in reason

    def test_reported_usage_below_the_ceiling_records_nothing(self) -> None:
        result = turn("שלום", Scripted(_says("שלום לך.", completion_tokens=8)))
        assert result.degradations == ()

    def test_a_multi_step_turn_with_no_usage_throughout_records_exactly_one(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        pinged = _calls("ping")
        pinged.completion_tokens = 0
        seam = bind_tools(
            lambda messages, *, tools: seam_script.pop(0),
            registry,
        )
        seam_script = [pinged, _says("פינג.", completion_tokens=0)]
        result = turn("שלום", seam, tools=registry)
        codes = [d.code for d in result.degradations]
        assert codes.count(DEGRADED_TRUNCATION_UNDETECTABLE) == 1
        assert result.steps == 2

    def test_an_empty_completion_with_no_usage_is_not_double_reported(self) -> None:
        """Nothing was generated, so there was nothing to truncate."""
        result = turn("שלום", Scripted(_says("", completion_tokens=0)))
        assert [d.code for d in result.degradations] == [DEGRADED_EMPTY_COMPLETION]
        assert result.spoken == FALLBACK_TEXT


# ── fix 2: registered tools that never reached the wire ───────────────────────


def _bound_seam(registry: ToolRegistry, *responses: ModelResponse) -> Any:
    """A schema-aware seam wrapped through :func:`bind_tools`."""
    script = list(responses)
    return bind_tools(lambda messages, *, tools: script.pop(0), registry)


class TestUnboundToolsAreRecorded:
    """A registry the model was never shown is a Gwen who silently has no tools."""

    def test_non_empty_and_unbound_is_recorded(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        result = turn("שלום", Scripted(_says("כן.")), tools=registry)
        assert DEGRADED_TOOLS_UNBOUND in {d.code for d in result.degradations}

    def test_the_reason_counts_the_tools_the_model_never_saw(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        registry.register("pong", {}, lambda: "ping")
        result = turn("שלום", Scripted(_says("כן.")), tools=registry)
        reason = next(d.reason for d in result.degradations if d.code == DEGRADED_TOOLS_UNBOUND)
        assert "2" in reason
        assert "bind_tools" in reason

    def test_an_unbound_registry_does_not_refuse_to_run(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        result = turn("שלום", Scripted(_says("כן.")), tools=registry)
        assert result.spoken == "כן."

    def test_non_empty_and_bound_records_nothing(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        result = turn("שלום", _bound_seam(registry, _says("כן.")), tools=registry)
        assert result.degradations == ()

    def test_non_empty_but_bound_to_a_different_registry_is_recorded(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        other = ToolRegistry()
        other.register("elsewhere", {}, lambda: "x")
        result = turn("שלום", _bound_seam(other, _says("כן.")), tools=registry)
        reason = next(d.reason for d in result.degradations if d.code == DEGRADED_TOOLS_UNBOUND)
        assert "another registry" in reason

    def test_an_empty_registry_unbound_records_nothing(self) -> None:
        result = turn("שלום", Scripted(_says("כן.")), tools=ToolRegistry())
        assert result.degradations == ()

    def test_an_empty_registry_bound_records_nothing(self) -> None:
        registry = ToolRegistry()
        result = turn("שלום", _bound_seam(registry, _says("כן.")), tools=registry)
        assert result.degradations == ()

    def test_no_registry_at_all_records_nothing(self) -> None:
        assert turn("שלום", Scripted(_says("כן."))).degradations == ()


# ── fix 3: a budget exit is not an empty completion ───────────────────────────


class TestBudgetExhaustionHasItsOwnCode:
    """Nothing was empty — the budget ran out mid-tool-use. Say the right thing."""

    @staticmethod
    def _tool_looping_turn(max_steps: int = 8) -> TurnResult:
        registry = ToolRegistry()
        registry.register("clock", {"type": "object"}, lambda: "12:00")
        calls = {"n": 0}

        def always_calls_a_tool(messages: list[dict[str, Any]], *, tools: Any) -> ModelResponse:
            calls["n"] += 1
            return ModelResponse(
                content="",
                tool_calls=[ToolCall(id=f"c{calls['n']}", name="clock", arguments={})],
                completion_tokens=3,
            )

        seam = bind_tools(always_calls_a_tool, registry)
        return turn(
            "מה השעה?",
            seam,
            tools=registry,
            config=TurnConfig(max_steps=max_steps),
        )

    def test_a_budget_exit_records_budget_exhausted_not_empty_completion(self) -> None:
        result = self._tool_looping_turn()
        codes = {d.code for d in result.degradations}
        assert DEGRADED_BUDGET_EXHAUSTED in codes
        assert DEGRADED_EMPTY_COMPLETION not in codes

    def test_the_reason_carries_the_steps_and_the_tool_calls(self) -> None:
        result = self._tool_looping_turn()
        reason = next(d.reason for d in result.degradations if d.code == DEGRADED_BUDGET_EXHAUSTED)
        assert "8" in reason
        assert "tool call" in reason

    def test_a_budget_exit_still_speaks_the_fallback(self) -> None:
        result = self._tool_looping_turn()
        assert result.exit_reason == EXIT_BUDGET
        assert result.spoken == FALLBACK_TEXT
        assert result.tool_calls == ("clock",) * 8

    def test_a_budget_exit_that_did_produce_prose_records_nothing_about_it(self) -> None:
        """The model answered on the way; the budget running out is not a fault."""
        registry = ToolRegistry()
        registry.register("clock", {}, lambda: "12:00")
        calls = {"n": 0}

        def seam(messages: list[dict[str, Any]], *, tools: Any) -> ModelResponse:
            calls["n"] += 1
            return ModelResponse(
                content="השעה 12.",
                tool_calls=[ToolCall(id=f"c{calls['n']}", name="clock", arguments={})],
                completion_tokens=3,
            )

        result = turn(
            "מה השעה?",
            bind_tools(seam, registry),
            tools=registry,
            config=TurnConfig(max_steps=3),
        )
        assert result.exit_reason == EXIT_BUDGET
        assert result.spoken == "השעה 12."
        assert result.degradations == ()

    def test_a_genuinely_empty_completion_keeps_the_empty_code(self) -> None:
        result = turn("שלום", Scripted(_says("")))
        assert [d.code for d in result.degradations] == [DEGRADED_EMPTY_COMPLETION]


# ── fix 4: a blank configured fallback must not become silence ────────────────


class TestABlankFallbackNeverBecomesSilence:
    """``cfg.fallback_text or FALLBACK_TEXT`` let a whitespace string through.

    A whitespace string is truthy, so a host that configured ``"   "`` got a
    turn that spoke three spaces — silence, which is the one thing this module
    promises never to produce.
    """

    @pytest.mark.parametrize("blank", ["   ", "\n", "\t\t", "", " \n "])
    def test_a_blank_configured_fallback_speaks_the_builtin_one(self, blank: str) -> None:
        result = turn("שלום", Scripted(_says("")), config=TurnConfig(fallback_text=blank))
        assert result.spoken == FALLBACK_TEXT
        assert result.spoken.strip()

    @pytest.mark.parametrize("blank", ["   ", "\n", ""])
    def test_a_blank_configured_fallback_is_recorded_once(self, blank: str) -> None:
        result = turn("שלום", Scripted(_says("")), config=TurnConfig(fallback_text=blank))
        codes = [d.code for d in result.degradations]
        assert codes.count(DEGRADED_FALLBACK_BLANK) == 1
        assert DEGRADED_EMPTY_COMPLETION in codes

    def test_the_reason_says_the_configured_fallback_was_blank(self) -> None:
        result = turn("שלום", Scripted(_says("")), config=TurnConfig(fallback_text="  "))
        reason = next(d.reason for d in result.degradations if d.code == DEGRADED_FALLBACK_BLANK)
        assert "fallback_text" in reason
        assert "blank" in reason

    def test_a_usable_configured_fallback_records_nothing_about_being_blank(self) -> None:
        cfg = TurnConfig(fallback_text="אין לי מה לומר.")
        result = turn("שלום", Scripted(_says("")), config=cfg)
        assert DEGRADED_FALLBACK_BLANK not in {d.code for d in result.degradations}
        assert result.spoken == "אין לי מה לומר."

    def test_a_whitespace_only_completion_is_not_spoken_as_words(self) -> None:
        result = turn("שלום", Scripted(_says("   \n  ")))
        assert result.spoken == FALLBACK_TEXT
        assert DEGRADED_EMPTY_COMPLETION in {d.code for d in result.degradations}

    def test_a_whitespace_only_completion_with_a_blank_fallback_still_speaks(self) -> None:
        result = turn("שלום", Scripted(_says("  ")), config=TurnConfig(fallback_text="\t"))
        assert result.spoken == FALLBACK_TEXT

    def test_a_truncated_whitespace_only_completion_still_speaks(self) -> None:
        result = turn(
            "שלום",
            Scripted(_says("   ", completion_tokens=32)),
            config=TurnConfig(max_tokens=32, fallback_text=" "),
        )
        assert result.spoken == FALLBACK_TEXT
        assert DEGRADED_TRUNCATION_SUSPECTED in {d.code for d in result.degradations}


# ── the structural never-silent guard ─────────────────────────────────────────


def _exit_clean() -> TurnResult:
    return turn("שלום", Scripted(_says("שלום לך.")))


def _exit_empty_completion() -> TurnResult:
    return turn("שלום", Scripted(_says("")))


def _exit_whitespace_only_completion() -> TurnResult:
    return turn("שלום", Scripted(_says("  \n\t ")))


def _exit_truncated_with_prose() -> TurnResult:
    return turn(
        "שלום", Scripted(_says("חצי", completion_tokens=16)), config=TurnConfig(max_tokens=16)
    )


def _exit_truncated_without_prose() -> TurnResult:
    return turn("שלום", Scripted(_says("", completion_tokens=16)), config=TurnConfig(max_tokens=16))


def _exit_truncated_whitespace_prose() -> TurnResult:
    return turn(
        "שלום", Scripted(_says(" ", completion_tokens=16)), config=TurnConfig(max_tokens=16)
    )


def _exit_seam_raises() -> TurnResult:
    def dead(messages: list[dict[str, Any]]) -> ModelResponse:
        raise ConnectionError("gateway is down")

    return turn("שלום", dead)


def _exit_seam_raises_after_speaking() -> TurnResult:
    state = {"n": 0}

    def flaky(messages: list[dict[str, Any]]) -> ModelResponse:
        state["n"] += 1
        if state["n"] == 1:
            said = _calls("ping")
            said.content = "רגע."
            return said
        raise ConnectionError("died mid-turn")

    registry = ToolRegistry()
    registry.register("ping", {}, lambda: "pong")
    return turn("שלום", bind_tools(lambda m, *, tools: flaky(m), registry), tools=registry)


def _exit_budget_exhausted() -> TurnResult:
    registry = ToolRegistry()
    registry.register("clock", {}, lambda: "12:00")
    calls = {"n": 0}

    def seam(messages: list[dict[str, Any]], *, tools: Any) -> ModelResponse:
        calls["n"] += 1
        return ModelResponse(
            content="",
            tool_calls=[ToolCall(id=f"c{calls['n']}", name="clock", arguments={})],
            completion_tokens=3,
        )

    return turn(
        "שלום",
        bind_tools(seam, registry),
        tools=registry,
        config=TurnConfig(max_steps=3),
    )


def _exit_tool_raises() -> TurnResult:
    def boom() -> str:
        raise RuntimeError("kaboom")

    registry = ToolRegistry()
    registry.register("boom", {}, boom)
    seam = Scripted(_calls("boom"), _says(""))
    return turn("שלום", bind_tools(lambda m, *, tools: seam(m), registry), tools=registry)


def _exit_blank_configured_fallback() -> TurnResult:
    return turn("שלום", Scripted(_says("")), config=TurnConfig(fallback_text="   "))


def _exit_blank_everything() -> TurnResult:
    cfg = TurnConfig(fallback_text="\n", truncation_suffix=" ", max_tokens=16)
    return turn("שלום", Scripted(_says("  ", completion_tokens=16)), config=cfg)


EVERY_REACHABLE_EXIT = {
    "clean": _exit_clean,
    "empty-completion": _exit_empty_completion,
    "whitespace-only-completion": _exit_whitespace_only_completion,
    "truncated-with-prose": _exit_truncated_with_prose,
    "truncated-without-prose": _exit_truncated_without_prose,
    "truncated-whitespace-prose": _exit_truncated_whitespace_prose,
    "seam-raises": _exit_seam_raises,
    "seam-raises-after-speaking": _exit_seam_raises_after_speaking,
    "budget-exhausted": _exit_budget_exhausted,
    "tool-raises": _exit_tool_raises,
    "blank-configured-fallback": _exit_blank_configured_fallback,
    "blank-everything": _exit_blank_everything,
}


class TestSpokenIsNeverBlankOnAnyReachableExit:
    """The module's one headline promise, swept across every exit it has.

    Named for what it guards rather than for a mechanism, because the mechanism
    is allowed to change and the promise is not: whatever happened, there are
    words to say. A new exit belongs in :data:`EVERY_REACHABLE_EXIT`.
    """

    @pytest.mark.parametrize("name", sorted(EVERY_REACHABLE_EXIT))
    def test_this_exit_still_speaks(self, name: str) -> None:
        result = EVERY_REACHABLE_EXIT[name]()
        assert result.spoken.strip(), f"{name} produced silence: {result.spoken!r}"

    @pytest.mark.parametrize("name", sorted(EVERY_REACHABLE_EXIT))
    def test_this_exit_never_raised(self, name: str) -> None:
        assert isinstance(EVERY_REACHABLE_EXIT[name](), TurnResult)

    def test_every_degrading_exit_recorded_something(self) -> None:
        """Never-silence is worth nothing if the silence goes unexplained."""
        for name, build in sorted(EVERY_REACHABLE_EXIT.items()):
            if name == "clean":
                continue
            assert build().degradations, f"{name} degraded without a record"


# ── never-raise ───────────────────────────────────────────────────────────────


class TestTheTurnNeverRaises:
    def test_a_seam_that_explodes_becomes_a_degradation_and_a_fallback(self) -> None:
        def dead(messages: list[dict[str, Any]]) -> ModelResponse:
            raise ConnectionError("gateway is down")

        result = turn("שלום", dead)
        codes = {d.code for d in result.degradations}
        assert DEGRADED_SEAM_ABORTED in codes
        assert result.spoken == FALLBACK_TEXT
        assert "gateway is down" in " ".join(d.reason for d in result.degradations)

    def test_a_seam_that_dies_after_speaking_still_speaks_what_it_said(self) -> None:
        state = {"n": 0}

        def flaky(messages: list[dict[str, Any]]) -> ModelResponse:
            state["n"] += 1
            if state["n"] == 1:
                said = _calls("ping")
                said.content = "רגע, בודקת."
                return said
            raise ConnectionError("died mid-turn")

        seam: Callable[[list[dict[str, Any]]], ModelResponse] = flaky
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        result = turn("שלום", seam, tools=registry)
        assert DEGRADED_SEAM_ABORTED in {d.code for d in result.degradations}
        assert result.spoken == "רגע, בודקת."
        assert result.tool_calls == ("ping",)

    def test_an_aborted_turn_never_speaks_the_loops_diagnostic_summary(self) -> None:
        def dead(messages: list[dict[str, Any]]) -> ModelResponse:
            raise ConnectionError("gateway is down")

        result = turn("שלום", dead)
        assert "aborted after" not in result.spoken

    def test_a_result_is_always_a_frozen_turn_result_with_spoken_text(self) -> None:
        result = turn("שלום", Scripted(_says("כן.")))
        assert isinstance(result, TurnResult)
        with pytest.raises(Exception):
            result.spoken = "no"  # type: ignore[misc]
        assert set(result.to_dict()) == {
            "spoken",
            "degradations",
            "tool_calls",
            "steps",
            "exit_reason",
        }


# ── the invariants the criteria assume ────────────────────────────────────────


class TestTheVerbatimInvariant:
    def test_the_model_sees_the_callers_words_exactly(self) -> None:
        text = "  שלום,\n\tמה השעה?  "
        seam = Scripted(_says("עכשיו שלוש."))
        turn(text, seam)
        user = [m for m in seam.calls[0] if m["role"] == "user"]
        assert len(user) == 1
        assert user[0]["content"] == text

    def test_the_packet_carries_the_original_verbatim(self) -> None:
        text = "שלום\n\nמה נשמע?   "
        result = turn(text, Scripted(_says("טוב.")))
        assert result.packet is not None
        assert result.packet.original == text

    def test_no_perception_seam_is_dialled(self) -> None:
        """Intake costs zero model calls: the seam sees exactly the loop's turns."""
        seam = Scripted(_says("כן."))
        turn("שלום", seam)
        assert seam.count == 1


class TestIdentityFraming:
    def test_an_unconfigured_identity_leaves_the_prompt_byte_identical(self) -> None:
        seam = Scripted(_says("כן."))
        turn("שלום", seam)
        system = [m for m in seam.calls[0] if m["role"] == "system"][0]
        assert system["content"] == SYSTEM_PROMPT

    def test_a_custom_base_prompt_also_survives_unframed(self) -> None:
        seam = Scripted(_says("כן."))
        turn("שלום", seam, config=TurnConfig(system_prompt="BASE"))
        assert [m for m in seam.calls[0] if m["role"] == "system"][0]["content"] == "BASE"

    def test_a_configured_identity_frames_the_prompt_without_losing_the_base(self) -> None:
        seam = Scripted(_says("כן."))
        turn("שלום", seam, config=TurnConfig(identity="Gwen"))
        system = [m for m in seam.calls[0] if m["role"] == "system"][0]["content"]
        assert system != SYSTEM_PROMPT
        assert SYSTEM_PROMPT in system
        assert "Gwen" in system


class TestConfigDefaults:
    def test_the_role_defaults_to_senses(self) -> None:
        assert TurnConfig().role == "senses"

    def test_max_tokens_is_generous_because_truncation_is_silence(self) -> None:
        assert TurnConfig().max_tokens == 16000

    def test_the_system_prompt_is_hebrew(self) -> None:
        assert any("֐" <= ch <= "ת" for ch in TurnConfig().system_prompt)

    def test_the_fallback_is_never_empty(self) -> None:
        assert TurnConfig().fallback_text.strip()

    def test_the_budget_leaves_room_for_a_tool_and_a_reply(self) -> None:
        assert TurnConfig().max_steps >= 2
