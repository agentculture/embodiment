"""The tool registry: a valid executor that starts empty and stays honest.

Three properties this file exists to pin:

* an empty registry advertises **nothing** — ``wire_tools()`` is ``None``, not
  ``[]``, because an empty list is still a ``tools`` field on the wire;
* the registry satisfies :class:`embodiment.loop.ToolExecutor` structurally, so
  :func:`embodiment.loop.run` accepts it with no adapter;
* a failing or unknown tool is recorded (constraint C3) *and* re-shaped into the
  executor contract the loop contains as one self-correcting step.
"""

from __future__ import annotations

from typing import Any

import pytest

from embodiment.loop import ToolError, ToolExecutor, ToolOutcome, UnknownToolError
from embodiment.safe_reason import UNSAFE_ENV
from embodiment.tools import (
    BOUND_REGISTRY_ATTR,
    DEGRADED_TOOL_FAILED,
    DEGRADED_TOOL_UNKNOWN,
    ToolRegistry,
    ToolSpec,
    bind_tools,
)
from tests.test_safe_reason import (
    MARKER,
    assert_no_speech,
    assert_speech_present,
    hostile_exception,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


class TestEmptyIsTheDefault:
    def test_a_fresh_registry_is_empty(self) -> None:
        registry = ToolRegistry()
        assert registry.empty
        assert len(registry) == 0
        assert registry.names() == ()

    def test_an_empty_registry_puts_no_schema_on_the_wire(self) -> None:
        registry = ToolRegistry()
        assert registry.schemas() == []
        assert registry.wire_tools() is None

    def test_a_registered_tool_reaches_the_wire(self) -> None:
        registry = ToolRegistry()
        registry.register("weather", _SCHEMA, lambda city: f"{city}: clear", description="d")
        wire = registry.wire_tools()
        assert wire is not None
        assert len(wire) == 1
        assert wire[0] == {
            "type": "function",
            "function": {"name": "weather", "description": "d", "parameters": _SCHEMA},
        }


class TestItIsAValidExecutor:
    def test_the_registry_satisfies_the_tool_executor_protocol(self) -> None:
        assert isinstance(ToolRegistry(), ToolExecutor)

    def test_executing_a_registered_tool_returns_a_tool_outcome(self) -> None:
        registry = ToolRegistry()
        registry.register("weather", _SCHEMA, lambda city: f"{city}: clear")
        outcome = registry.execute("weather", {"city": "חיפה"})
        assert isinstance(outcome, ToolOutcome)
        assert outcome.result == "חיפה: clear"
        assert outcome.finished is False

    def test_a_finishing_tool_says_so(self) -> None:
        registry = ToolRegistry()
        registry.register("done", {}, lambda: "all done", finishes=True)
        outcome = registry.execute("done", {})
        assert outcome.finished is True
        assert outcome.finish_summary == "all done"

    def test_a_tool_returning_none_yields_an_empty_result_not_the_word_none(self) -> None:
        registry = ToolRegistry()
        registry.register("quiet", {}, lambda: None)
        assert registry.execute("quiet", {}).result == ""


class TestRegistration:
    def test_a_spec_can_be_added_whole(self) -> None:
        registry = ToolRegistry()
        registry.add(ToolSpec(name="ping", parameters={}, fn=lambda: "pong"))
        assert registry.names() == ("ping",)
        assert "ping" in registry

    def test_a_blank_name_is_refused(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError):
            registry.register("  ", {}, lambda: "x")

    def test_a_duplicate_name_is_refused_rather_than_silently_replaced(self) -> None:
        registry = ToolRegistry()
        registry.register("ping", {}, lambda: "pong")
        with pytest.raises(ValueError):
            registry.register("ping", {}, lambda: "other")

    def test_the_schema_is_carried_verbatim_and_copied(self) -> None:
        registry = ToolRegistry()
        spec = registry.register("weather", _SCHEMA, lambda city: city)
        assert spec.parameters == _SCHEMA
        spec.parameters["properties"] = {}
        assert _SCHEMA["properties"] == {"city": {"type": "string"}}


class TestFailuresAreRecordedAndContained:
    def test_an_unknown_tool_raises_the_loops_own_error_and_is_recorded(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(UnknownToolError):
            registry.execute("nope", {})
        assert [d.code for d in registry.degradations] == [DEGRADED_TOOL_UNKNOWN]

    def test_a_raising_tool_becomes_a_tool_error_and_is_recorded(self) -> None:
        def boom() -> str:
            raise RuntimeError("kaboom")

        registry = ToolRegistry()
        registry.register("boom", {}, boom)
        with pytest.raises(ToolError) as caught:
            registry.execute("boom", {})
        # The tool's MESSAGE used to be interpolated into both of these. A
        # tool's message routinely quotes its arguments, which are the user's
        # words, so the class name is asserted instead and the message absent.
        assert "RuntimeError" in str(caught.value)
        assert "kaboom" not in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert [d.code for d in registry.degradations] == [DEGRADED_TOOL_FAILED]
        assert "boom" in registry.degradations[0].reason
        assert "kaboom" not in registry.degradations[0].reason

    def test_bad_arguments_surface_as_a_recorded_failure(self) -> None:
        registry = ToolRegistry()
        registry.register("weather", _SCHEMA, lambda city: city)
        with pytest.raises(ToolError):
            registry.execute("weather", {"town": "חיפה"})
        assert registry.degradations[0].code == DEGRADED_TOOL_FAILED

    def test_a_degradation_serializes_to_code_and_reason(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(UnknownToolError):
            registry.execute("nope", {})
        assert set(registry.degradations[0].to_dict()) == {"code", "reason"}


class TestBindTools:
    def test_binding_an_empty_registry_sends_no_tools(self) -> None:
        seen: list[Any] = []

        def seam(messages: list[dict[str, Any]], *, tools: Any) -> str:
            seen.append(tools)
            return "ok"

        complete = bind_tools(seam, ToolRegistry())
        assert complete([{"role": "user", "content": "hi"}]) == "ok"
        assert seen == [None]

    def test_binding_no_registry_at_all_also_sends_no_tools(self) -> None:
        seen: list[Any] = []

        bind_tools(lambda messages, *, tools: seen.append(tools), None)([])
        assert seen == [None]

    def test_the_bound_callable_names_the_registry_it_was_bound_to(self) -> None:
        """The marker ``turn()`` reads to catch a registry the model never saw."""
        registry = ToolRegistry()
        complete = bind_tools(lambda messages, *, tools: "ok", registry)
        assert getattr(complete, BOUND_REGISTRY_ATTR) is registry

    def test_binding_without_a_registry_marks_the_one_it_made(self) -> None:
        complete = bind_tools(lambda messages, *, tools: "ok")
        bound = getattr(complete, BOUND_REGISTRY_ATTR)
        assert isinstance(bound, ToolRegistry)
        assert bound.empty

    def test_an_unbound_callable_carries_no_marker(self) -> None:
        assert getattr(lambda messages: "ok", BOUND_REGISTRY_ATTR, None) is None

    def test_binding_a_populated_registry_sends_its_schemas(self) -> None:
        seen: list[Any] = []
        registry = ToolRegistry()
        registry.register("weather", _SCHEMA, lambda city: city)

        bind_tools(lambda messages, *, tools: seen.append(tools), registry)([])
        assert seen == [registry.schemas()]


class TestNoSpeechReachesAToolRecord:
    """A tool's arguments ARE the user's words, and a failing tool quotes them.

    ``ValueError(f"cannot handle {kwargs}")`` is ordinary defensive code in a
    host's tool. Interpolating it put the arguments into ``tool-failed``'s
    reason and into the ``ToolError`` message.
    """

    @staticmethod
    def _registry_with_an_echoing_tool():
        registry = ToolRegistry()

        def boom(**kwargs):
            raise ValueError(f"cannot handle {kwargs}")

        registry.register("echo", {"type": "object", "properties": {}}, boom)
        return registry

    def test_a_failing_tool_leaks_neither_to_the_record_nor_the_error(self) -> None:
        registry = self._registry_with_an_echoing_tool()
        raised: list[BaseException] = []
        try:
            registry.execute("echo", {"said": MARKER})
        except Exception as exc:  # noqa: BLE001  # the assertion IS about this object
            raised.append(exc)

        assert raised
        assert_no_speech(MARKER, registry.degradations, str(raised[0]), raised[0])

    def test_the_tool_failure_is_still_named(self) -> None:
        registry = self._registry_with_an_echoing_tool()
        with pytest.raises(ToolError):
            registry.execute("echo", {"said": MARKER})

        assert [d.code for d in registry.degradations] == [DEGRADED_TOOL_FAILED]
        reason = registry.degradations[0].reason
        assert "echo" in reason
        assert "ValueError" in reason

    def test_a_hostile_exception_leaks_through_no_corner(self) -> None:
        registry = ToolRegistry()

        def boom(**kwargs):
            raise hostile_exception(MARKER)

        registry.register("h", {"type": "object", "properties": {}}, boom)
        with pytest.raises(ToolError):
            registry.execute("h", {"said": MARKER})
        assert_no_speech(MARKER, registry.degradations)

    def test_an_unknown_tool_name_is_restricted_not_interpolated(self) -> None:
        """The model chose this name, so it is attacker-controlled too."""
        registry = ToolRegistry()
        with pytest.raises(UnknownToolError):
            registry.execute(f"<<<{MARKER} evil", {})

        assert [d.code for d in registry.degradations] == [DEGRADED_TOOL_UNKNOWN]
        assert_no_speech(MARKER, registry.degradations)
        assert "<<<" not in registry.degradations[0].reason

    def test_a_declared_fault_code_still_reaches_the_model(self) -> None:
        """Self-correction is the cost of this change; a DECLARED code is the remedy.

        This test used to set a free-text ``safe_detail``. Review showed that
        channel could not hold: ``f"bad-{city}"`` is already charset-clean, so
        restriction removed nothing. The vocabulary is now fixed at
        registration instead.
        """
        registry = ToolRegistry()

        def boom(**kwargs: Any) -> str:
            exc = ValueError(f"raw {MARKER}")
            exc.code = "missing-required-argument"
            raise exc

        registry.register(
            "s",
            {"type": "object", "properties": {}},
            boom,
            codes={"missing-required-argument"},
        )
        raised: list[BaseException] = []
        try:
            registry.execute("s", {"said": MARKER})
        except Exception as exc:  # noqa: BLE001  # the assertion IS about this object
            raised.append(exc)

        assert "missing-required-argument" in str(raised[0])
        assert_no_speech(MARKER, str(raised[0]), registry.degradations)

    def test_the_marker_appears_when_the_unsafe_hatch_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(UNSAFE_ENV, "1")
        registry = self._registry_with_an_echoing_tool()
        with pytest.raises(ToolError):
            registry.execute("echo", {"said": MARKER})
        assert_speech_present(MARKER, registry.degradations)


class TestDeclaredFaultCodes:
    """A tool names its fault from a vocabulary it fixed at registration."""

    @staticmethod
    def _registry(codes: Any = frozenset({"missing-city"})) -> Any:
        registry = ToolRegistry()

        def boom(**kwargs: Any) -> str:
            exc = ValueError(f"cannot handle {kwargs}")
            exc.code = kwargs.get("fault", "missing-city")
            raise exc

        registry.register("weather", _SCHEMA, boom, codes=codes)
        return registry

    def test_a_declared_code_reaches_the_model_and_the_record(self) -> None:
        registry = self._registry()
        raised: list[BaseException] = []
        try:
            registry.execute("weather", {"city": MARKER})
        except Exception as exc:  # noqa: BLE001  # the assertion IS about this object
            raised.append(exc)

        assert "missing-city" in str(raised[0])
        assert "missing-city" in registry.degradations[0].reason
        assert_no_speech(MARKER, str(raised[0]), registry.degradations)

    def test_an_undeclared_code_is_refused(self) -> None:
        registry = self._registry(codes=frozenset({"something-else"}))
        with pytest.raises(ToolError):
            registry.execute("weather", {"city": MARKER, "fault": f"leak-{MARKER}"})

        assert "undeclared-code" in registry.degradations[0].reason
        assert_no_speech(MARKER, registry.degradations)

    def test_a_tool_that_declared_nothing_gets_no_code_channel(self) -> None:
        registry = self._registry(codes=frozenset())
        with pytest.raises(ToolError):
            registry.execute("weather", {"city": MARKER})
        assert_no_speech(MARKER, registry.degradations)

    def test_codes_default_to_empty(self) -> None:
        registry = ToolRegistry()
        spec = registry.register("plain", _SCHEMA, lambda city: city)
        assert spec.codes == frozenset()

    def test_add_round_trips_the_declared_codes(self) -> None:
        source = ToolRegistry()
        spec = source.register("weather", _SCHEMA, lambda city: city, codes=frozenset({"a"}))
        target = ToolRegistry()
        assert target.add(spec).codes == frozenset({"a"})

    def test_declared_codes_are_restricted_at_registration(self) -> None:
        """A declared vocabulary is host text, but it is still rendered."""
        registry = ToolRegistry()
        spec = registry.register("t", _SCHEMA, lambda city: city, codes={"a b<<<c"})
        assert all("<<<" not in code and " " not in code for code in spec.codes)
