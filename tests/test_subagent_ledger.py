"""Tests for the subagent ledger lane (task t10).

Acceptance criteria:
- a child degradation read back through the ledger names the subagent lane
  AND the child task id, never silently the parent (headline test);
- a nested child (grandchild) is attributed to its own id;
- the exhaustive code enumeration still passes with the new lane;
- a drive that spawns nothing produces a byte-identical ledger to before;
- regression test for the SpawnRecord.to_dict key asymmetry;
- regression test for the sub_results double-count concern.
"""

from __future__ import annotations

from typing import Any, Optional

from embodiment import ledger, loop, subagent
from embodiment.contract import ModelResponse, SubResult, Task, TaskResult, ToolCall
from embodiment.loop import LoopDegradation, ToolOutcome, run

# ── shared doubles ────────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    fields_: dict[str, Any] = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    fields_.update(kw)
    return Task(**fields_)


def _call(name: str = "read_file", **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


def _resp(content: str = "") -> ModelResponse:
    return ModelResponse(content=content)


class Scripted:
    """A ``complete`` seam replaying fixed turns."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(messages)
        return item


class Executor:
    """A tool surface with no shell and no filesystem."""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        return ToolOutcome(result=f"{name} ok")


def _drive(*responses: Any, **kw: Any) -> Any:
    """Run the real loop over *responses*, returning the ``LoopOutcome``."""
    task = kw.pop("task", None) or _task()
    max_steps = kw.pop("max_steps", 4)
    executor = kw.pop("executor", None) or Executor()
    return run(Scripted(*responses), task, executor=executor, max_steps=max_steps, **kw)


# ── subagent helpers ──────────────────────────────────────────────────────────


class _DelegatingExecutor(Executor):
    """An executor whose ``delegate`` tool asks the loop for a child drive."""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name != "delegate":
            return super().execute(name, arguments)
        child = Task(id="child-1", repo_path="/repo", instruction="the sub-task")
        return ToolOutcome(
            result="delegating",
            spawn=loop.SpawnRequest(task=child, executor=Executor()),
        )


def _child_degradation() -> LoopDegradation:
    """A synthetic child degradation for testing."""
    return LoopDegradation(
        code=loop.DEGRADED_TOOL_ARGUMENTS,
        reason="child tool argument error",
        step_index=1,
        model_turns=1,
    )


def _seam_with_degradation(_call: subagent.SubagentCall) -> Optional[subagent.SubagentResult]:
    """A subagent seam that returns a child result with a degradation."""
    return subagent.SubagentResult(
        sub_result=None,
        model_turns=1,
        degradations=[_child_degradation()],
        result="child done",
        exit_reason=loop.EXIT_FINISHED,
    )


# ── headline test: child degradation names subagent lane and child task id ───


class TestChildDegradationAttribution:
    """A child's degradation must reach the parent's ledger wearing the child's name."""

    def test_child_degradation_names_subagent_lane_and_child_task_id(self) -> None:
        """Headline test: child degradation read back through ledger.read() names
        the subagent lane and the child task id, never silently the parent."""

        outcome = _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DelegatingExecutor(),
            subagent=_seam_with_degradation,
            spawn_allowance=1,
        )

        records = ledger.read(loop=outcome)

        # Find the child's degradation record
        child_records = [r for r in records if r.child_task_id is not None]
        assert len(child_records) == 1, f"expected 1 child record, got {len(child_records)}"

        record = child_records[0]
        # The source is the minting lane (loop), not subagent
        assert record.source == ledger.SOURCE_LOOP
        assert record.code == loop.DEGRADED_TOOL_ARGUMENTS
        # The child_task_id identifies the child
        assert record.child_task_id == "child-1"

    def test_from_subagent_with_spawn_record(self) -> None:
        """from_subagent() reads child_task_id from a SpawnRecord automatically."""
        spawn = subagent.SpawnRecord(
            outcome=subagent.SPAWN_GRANTED,
            child_task_id="child-1",
            degradations=(_child_degradation(),),
        )
        records = ledger.from_subagent(spawn)
        assert len(records) == 1
        assert records[0].child_task_id == "child-1"
        assert records[0].source == ledger.SOURCE_LOOP

    def test_from_subagent_with_explicit_child_task_id(self) -> None:
        """from_subagent() accepts an explicit child_task_id for raw sequences."""
        records = ledger.from_subagent(
            [_child_degradation()],
            child_task_id="explicit-child",
        )
        assert len(records) == 1
        assert records[0].child_task_id == "explicit-child"

    def test_from_subagent_none_returns_empty(self) -> None:
        """from_subagent(None) returns an empty list."""
        assert ledger.from_subagent(None) == []

    def test_from_subagent_empty_list_returns_empty(self) -> None:
        """from_subagent([]) returns an empty list."""
        assert ledger.from_subagent([]) == []


# ── nested child (grandchild) attribution ─────────────────────────────────────


class TestNestedChildAttribution:
    """A grandchild is attributed to its own id, not its parent's."""

    def test_grandchild_attributed_to_own_id(self) -> None:
        """A nested child's degradation carries its own task id."""

        grandchild_degradation = LoopDegradation(
            code=loop.DEGRADED_PROGRESS,
            reason="grandchild progress error",
            step_index=0,
            model_turns=0,
        )

        # Simulate a grandchild result folded into the parent's ledger
        records = ledger.from_subagent(
            [grandchild_degradation],
            child_task_id="grandchild-1",
        )
        assert len(records) == 1
        assert records[0].child_task_id == "grandchild-1"
        # The source is the minting lane, not the parent's
        assert records[0].source == ledger.SOURCE_LOOP

    def test_two_children_different_ids(self) -> None:
        """Two children's degradations carry distinct child_task_ids."""
        child_a = LoopDegradation(
            code=loop.DEGRADED_TOOL_ARGUMENTS,
            reason="child A error",
            step_index=0,
            model_turns=0,
        )
        child_b = LoopDegradation(
            code=loop.DEGRADED_PROGRESS,
            reason="child B error",
            step_index=1,
            model_turns=1,
        )

        records_a = ledger.from_subagent([child_a], child_task_id="child-a")
        records_b = ledger.from_subagent([child_b], child_task_id="child-b")

        assert records_a[0].child_task_id == "child-a"
        assert records_b[0].child_task_id == "child-b"
        assert records_a[0].child_task_id != records_b[0].child_task_id


# ── exhaustive code enumeration still passes ─────────────────────────────────


class TestExhaustiveEnumeration:
    """The exhaustive code enumeration still works with the new lane."""

    def test_known_codes_does_not_crash_on_subagent_lane(self) -> None:
        """known_codes() iterates SOURCES only; subagent is not in SOURCES."""
        codes = ledger.known_codes()
        # All codes should have sources in SOURCES
        sources = {c.source for c in codes}
        assert sources <= set(ledger.SOURCES)

    def test_source_subagent_not_in_sources(self) -> None:
        """SOURCE_SUBAGENT is deliberately excluded from SOURCES."""
        assert ledger.SOURCE_SUBAGENT not in ledger.SOURCES

    def test_source_subagent_not_in_modules(self) -> None:
        """SOURCE_SUBAGENT is not in _MODULES — it has no codes of its own."""
        assert ledger.SOURCE_SUBAGENT not in ledger._MODULES

    def test_source_subagent_in_relevant(self) -> None:
        """SOURCE_SUBAGENT is in _RELEVANT for relay attribution."""
        assert ledger.SOURCE_SUBAGENT in ledger._RELEVANT

    def test_relevant_does_not_include_self(self) -> None:
        """_RELEVANT[SOURCE_SUBAGENT] does not include SOURCE_SUBAGENT itself."""
        relevant = ledger._RELEVANT[ledger.SOURCE_SUBAGENT]
        assert ledger.SOURCE_SUBAGENT not in relevant


# ── byte-identical ledger for spawn-free drive ───────────────────────────────


class TestSpawnFreeDrive:
    """A drive that spawns nothing produces a byte-identical ledger to before."""

    def test_spawn_free_drive_ledger_is_identical(self) -> None:
        """A drive with no spawns produces the same ledger as before t10."""
        outcome = _drive(_turn(_call("finish")))
        records = ledger.read(loop=outcome)
        # No spawns, no subagent — the ledger should be empty
        assert records == []

    def test_spawn_free_drive_serialisation_is_identical(self) -> None:
        """Serialising a spawn-free ledger produces no child_task_id keys."""
        outcome = _drive(_turn(_call("finish")))
        records = ledger.read(loop=outcome)
        for record in records:
            d = record.to_dict()
            assert "child_task_id" not in d

    def test_normal_degradation_has_no_child_task_id(self) -> None:
        """A non-subagent degradation has child_task_id=None and omits it."""
        outcome = _drive(
            _turn(_call("read_file", path="nonexistent.txt")),
            max_steps=1,
        )
        records = ledger.read(loop=outcome)
        for record in records:
            assert record.child_task_id is None
            d = record.to_dict()
            assert "child_task_id" not in d


# ── regression: SpawnRecord.to_dict key asymmetry ────────────────────────────


class TestSpawnRecordToDictKeyAsymmetry:
    """Regression: child_task_id must always be present in to_dict()."""

    def test_to_dict_always_has_child_task_id_key(self) -> None:
        """SpawnRecord.to_dict() always includes child_task_id, even when None."""
        record = subagent.SpawnRecord(
            outcome=subagent.SPAWN_REFUSED_ALLOWANCE,
        )
        d = record.to_dict()
        assert "child_task_id" in d
        assert d["child_task_id"] is None

    def test_to_dict_with_child_task_id(self) -> None:
        """SpawnRecord.to_dict() includes child_task_id when set."""
        record = subagent.SpawnRecord(
            outcome=subagent.SPAWN_GRANTED,
            child_task_id="child-1",
        )
        d = record.to_dict()
        assert "child_task_id" in d
        assert d["child_task_id"] == "child-1"

    def test_to_dict_consistency_across_outcomes(self) -> None:
        """All outcomes produce dicts with the same keys for child_task_id."""
        for outcome in subagent.SPAWN_OUTCOMES:
            record = subagent.SpawnRecord(outcome=outcome)
            d = record.to_dict()
            assert "child_task_id" in d, f"missing child_task_id for outcome {outcome}"


# ── regression: sub_results double-count ─────────────────────────────────────


class TestSubResultsNoDoubleCount:
    """Regression: the same child must not be recorded twice.

    The review flagged a possible double-count in ``_snapshot_executor_ledger``.
    The lists are indeed separate — it reads ``ctx.executor.sub_results`` while
    the subagent path appends to ``ctx.result.sub_results`` — but separate lists
    were never the risk. The risk is one child landing in BOTH: an executor that
    delegates a spawn to the loop and also ledgers that child itself. Extending
    one list with the other then duplicates it, and ``SubResult`` carries
    ``usage`` whose contract has the reader summing children explicitly, so a
    duplicate silently doubles that child's cost.

    The loop now keeps its own record (it stamped the lineage) and drops the
    executor's copy with a ``DEGRADED_SPAWN_DUPLICATE`` record — never quietly.
    An executor entry for a child the loop did NOT mint is still kept, which is
    the property the extend-never-replace comment exists to protect.
    """

    def test_executor_sub_results_and_loop_sub_results_are_separate(self) -> None:
        """The loop's sub_results and the executor's sub_results are independent."""

        class _TrackingExecutor(Executor):
            """An executor that maintains its own sub_results list."""

            def __init__(self) -> None:
                self.sub_results: list[Any] = []

            def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
                if name == "finish":
                    return ToolOutcome(result="done", finished=True, finish_summary="done")
                return ToolOutcome(result=f"{name} ok")

        executor = _TrackingExecutor()
        outcome = _drive(_turn(_call("finish")), executor=executor)

        # The executor's sub_results is independent of the loop's
        assert executor.sub_results == []
        assert outcome.result.sub_results == []

    def test_an_executor_that_also_ledgers_the_child_does_not_duplicate_it(self) -> None:
        """The case the original reasoning missed: BOTH lists name the same child."""
        child = SubResult(task_id="child-1", engine="e", model="m", status="ok", summary="dupe")

        class _DoubleLedgering(_DelegatingExecutor):
            def __init__(self) -> None:
                self.sub_results: list[Any] = [child]

        def _seam(_call: subagent.SubagentCall) -> Optional[subagent.SubagentResult]:
            return subagent.SubagentResult(
                sub_result=child,
                model_turns=1,
                result="child done",
                exit_reason=loop.EXIT_FINISHED,
            )

        outcome = _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DoubleLedgering(),
            subagent=_seam,
            spawn_allowance=1,
        )

        ids = [sub.task_id for sub in outcome.result.sub_results]
        assert ids == ["child-1"], f"the child was recorded {len(ids)} times: {ids}"
        # Dropped, but never silently (C3).
        codes = [d.code for d in outcome.degradations]
        assert loop.DEGRADED_SPAWN_DUPLICATE in codes

    def test_an_unrelated_executor_sub_result_is_still_kept(self) -> None:
        """Dedupe must not become "drop the executor's ledger"."""
        mine = SubResult(task_id="child-1", engine="e", model="m", status="ok", summary="seam")
        theirs = SubResult(task_id="other", engine="e", model="m", status="ok", summary="theirs")

        class _AlsoLedgering(_DelegatingExecutor):
            def __init__(self) -> None:
                self.sub_results: list[Any] = [theirs]

        def _seam(_call: subagent.SubagentCall) -> Optional[subagent.SubagentResult]:
            return subagent.SubagentResult(
                sub_result=mine,
                model_turns=1,
                result="child done",
                exit_reason=loop.EXIT_FINISHED,
            )

        outcome = _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_AlsoLedgering(),
            subagent=_seam,
            spawn_allowance=1,
        )

        assert sorted(sub.task_id for sub in outcome.result.sub_results) == ["child-1", "other"]
        assert loop.DEGRADED_SPAWN_DUPLICATE not in [d.code for d in outcome.degradations]

    def test_spawned_child_does_not_double_count(self) -> None:
        """A spawned child's SubResult appears once in the result.

        The drive makes two turns: delegate (granted) and finish. The second
        turn does NOT delegate, so there is exactly one spawn record.
        """

        def _seam(_call: subagent.SubagentCall) -> Optional[subagent.SubagentResult]:
            return subagent.SubagentResult(
                sub_result=None,
                model_turns=1,
                result="child done",
                exit_reason=loop.EXIT_FINISHED,
            )

        outcome = _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DelegatingExecutor(),
            subagent=_seam,
            spawn_allowance=1,
        )

        # The loop's sub_results should have exactly one entry
        # (from _stamp_sub_result), not doubled by _snapshot_executor_ledger
        # because the executor has no sub_results attribute.
        spawns = outcome.spawns
        granted_spawns = [s for s in spawns if s.granted]
        assert len(granted_spawns) == 1


# ── LedgerRecord child_task_id field ─────────────────────────────────────────


class TestLedgerRecordChildTaskId:
    """LedgerRecord.child_task_id is None for non-subagent records."""

    def test_normal_record_has_none_child_task_id(self) -> None:
        """A record from a normal lane has child_task_id=None."""
        record = ledger.LedgerRecord(
            source=ledger.SOURCE_LOOP,
            code=loop.DEGRADED_TOOL_ARGUMENTS,
            reason="test",
        )
        assert record.child_task_id is None

    def test_to_dict_omits_none_child_task_id(self) -> None:
        """to_dict() omits child_task_id when it is None."""
        record = ledger.LedgerRecord(
            source=ledger.SOURCE_LOOP,
            code=loop.DEGRADED_TOOL_ARGUMENTS,
            reason="test",
        )
        d = record.to_dict()
        assert "child_task_id" not in d

    def test_to_dict_includes_non_none_child_task_id(self) -> None:
        """to_dict() includes child_task_id when it is set."""
        record = ledger.LedgerRecord(
            source=ledger.SOURCE_LOOP,
            code=loop.DEGRADED_TOOL_ARGUMENTS,
            reason="test",
            child_task_id="child-1",
        )
        d = record.to_dict()
        assert "child_task_id" in d
        assert d["child_task_id"] == "child-1"


# ── read() integration ───────────────────────────────────────────────────────


class TestReadIntegration:
    """The read() function handles subagent correctly."""

    def test_read_with_subagent_includes_child_records(self) -> None:
        """read(subagent=...) includes child-attributed records."""
        child_deg = _child_degradation()
        records = ledger.read(subagent=[child_deg])
        # from_subagent needs child_task_id; without it, records have None
        # but the records should still be present
        assert len(records) == 1
        assert records[0].code == loop.DEGRADED_TOOL_ARGUMENTS

    def test_read_with_subagent_spawn_record(self) -> None:
        """read(subagent=SpawnRecord) extracts child_task_id automatically."""
        spawn = subagent.SpawnRecord(
            outcome=subagent.SPAWN_GRANTED,
            child_task_id="child-1",
            degradations=(_child_degradation(),),
        )
        records = ledger.read(subagent=spawn)
        assert len(records) == 1
        assert records[0].child_task_id == "child-1"

    def test_read_without_subagent_is_empty(self) -> None:
        """read() with no arguments returns an empty list."""
        assert ledger.read() == []

    def test_read_subagent_after_standard_lanes(self) -> None:
        """Subagent records appear after standard lane records in output."""
        # A loop degradation and a subagent degradation
        loop_deg = LoopDegradation(
            code=loop.DEGRADED_PROGRESS,
            reason="loop error",
            step_index=0,
            model_turns=0,
        )
        child_deg = _child_degradation()

        outcome = loop.LoopOutcome(
            result=TaskResult(task_id="t1", status=loop.OK, summary="done"),
            exit_reason=loop.EXIT_FINISHED,
            hook_firings=[],
            degradations=[loop_deg],
            spawns=[],
        )

        records = ledger.read(loop=outcome, subagent=[child_deg])
        # Loop records come first (SOURCES order), subagent after
        assert len(records) == 2
        assert records[0].source == ledger.SOURCE_LOOP
        assert records[0].code == loop.DEGRADED_PROGRESS
        assert records[1].code == loop.DEGRADED_TOOL_ARGUMENTS
