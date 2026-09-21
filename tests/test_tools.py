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
from embodiment.tools import (
    BOUND_REGISTRY_ATTR,
    DEGRADED_TOOL_FAILED,
    DEGRADED_TOOL_UNKNOWN,
    ToolRegistry,
    ToolSpec,
    bind_tools,
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
        assert wire is not None and len(wire) == 1
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
        with pytest.raises(ValueError):
            ToolRegistry().register("  ", {}, lambda: "x")

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
        assert "kaboom" in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert [d.code for d in registry.degradations] == [DEGRADED_TOOL_FAILED]
        assert "boom" in registry.degradations[0].reason

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
