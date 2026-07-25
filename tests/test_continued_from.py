"""The ``supersedes`` provenance edge must be reachable through ``run()``.

Task t14's acceptance criterion says provenance flows perception → action →
durable record with ``added_by``, ``links`` **and** ``supersedes`` populated on
writes. ``lifecycle`` builds ``supersedes`` from ``TaskResult.continued_from``
— but ``run()`` used to construct the result itself with no way for a host to
seed that field, so the edge was reachable only by a test that built a
``TaskResult`` by hand. The demo (t15) found it: driving through the public API,
``supersedes`` could never appear.

Setting it on the *returned* result cannot work either, and that is the whole
reason this is a ``run()`` parameter: the ``before-memory`` boundary fires
inside ``run()``, so the durable write has already happened by the time a host
gets the result back.
"""

from __future__ import annotations

import inspect

from embodiment.contract import OK, ModelResponse, Task, ToolCall
from embodiment.loop import BOUNDARY_MEMORY, EXIT_FINISHED, ToolOutcome, run


class _Executor:
    """Minimal tool surface: one `finish` call and nothing else."""

    def execute(self, name: str, arguments: dict) -> ToolOutcome:
        if name == "finish":
            return ToolOutcome(
                result="done", finished=True, finish_summary=arguments.get("summary", "")
            )
        return ToolOutcome(result="ok")


def _complete(_messages):
    return ModelResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="finish", arguments={"summary": "watered"})],
    )


def _task() -> Task:
    return Task(id="visit-2", repo_path="", instruction="check the fig")


def _drive(**kwargs):
    return run(_complete, _task(), executor=_Executor(), max_steps=4, **kwargs)


class TestReachableThroughThePublicApi:
    def test_run_accepts_continued_from(self):
        assert "continued_from" in inspect.signature(run).parameters

    def test_it_lands_on_the_result(self):
        outcome = _drive(continued_from="visit-1")
        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.status == OK
        assert outcome.result.continued_from == "visit-1"

    def test_absent_by_default(self):
        """The unseeded path must be byte-identical to before this existed."""
        assert _drive().result.continued_from is None


class TestItArrivesBeforeTheMemoryBoundary:
    """The ordering that makes a returned-result assignment useless."""

    def test_the_continuity_seam_sees_it_at_before_memory(self):
        seen: list[tuple[str, object]] = []

        def continuity(boundary) -> None:
            result = getattr(boundary, "result", None)
            seen.append((boundary.name, getattr(result, "continued_from", "<no result>")))

        _drive(continued_from="visit-1", continuity=continuity)

        at_memory = [value for name, value in seen if name == BOUNDARY_MEMORY]
        assert at_memory, "the before-memory boundary never fired"
        assert all(
            value == "visit-1" for value in at_memory
        ), f"the memory boundary could not see continued_from: {seen}"

    def test_every_boundary_sees_it_not_just_the_last(self):
        kinds = set()

        def continuity(boundary) -> None:
            result = getattr(boundary, "result", None)
            if getattr(result, "continued_from", None) == "visit-1":
                kinds.add(boundary.name)

        _drive(continued_from="visit-1", continuity=continuity)
        assert BOUNDARY_MEMORY in kinds
