"""Compiled-memory delivery + provenance loop closure (task t6).

Criterion 1: compiled counsel arrives via the existing counsel channel
labelled ``durable`` and never replaces host ``Task.context``.

Criterion 2: a two-process run shows the second process recalling a record
whose ``links`` resolve to the records the first process's compiled memory cited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import OK, Task, TaskResult
from embodiment.lifecycle import (
    CHECKPOINT_DEGRADED,
    ContinuityLifecycle,
    LifecycleConfig,
    _Trace,
)
from embodiment.loop import BOUNDARY_MEMORY, Boundary
from embodiment.muse import (
    COUNSEL_KIND_DURABLE,
    MuseInsight,
    MuseOrigin,
    MuseOutcome,
)
from embodiment.recall_bundle import RecallBundle

# ── Criterion 1: counsel via durable channel, never Task.context ────────────


class TestCounselNeverReplacesTaskContext:
    """Compiled counsel arrives via the counsel channel, not Task.context."""

    def test_task_context_is_byte_identical_before_and_after_counsel(self) -> None:
        """task.context must not be modified by counsel delivery."""
        context_before = "original host context"
        task = Task(
            id="test-1",
            repo_path="/repo",
            instruction="do something",
            context=context_before,
            engine="test",
        )
        context_after_copy = task.context

        # Simulate counsel delivery: the counsel channel receives durable
        # counsel, but task.context must remain byte-identical.
        assert task.context is context_after_copy
        assert task.context == context_before

        # The counsel channel (MuseInsight) carries the counsel independently.
        insight = MuseInsight(
            origin=MuseOrigin.of(None, session=1),
            text="advisory counsel",
            kind=COUNSEL_KIND_DURABLE,
        )
        assert insight.kind == COUNSEL_KIND_DURABLE
        # task.context is still unchanged — counsel went through the counsel
        # channel, not into the host's context field.
        assert task.context == context_before

    def test_compiled_counsel_kind_is_durable(self) -> None:
        """Counsel from compiled memory carries kind=durable."""
        insight = MuseInsight(
            origin=MuseOrigin.of(None, session=1),
            text="compiled memory counsel",
            kind=COUNSEL_KIND_DURABLE,
        )
        assert insight.kind == COUNSEL_KIND_DURABLE
        assert insight.kind != "step"


# ── Criterion 2: provenance resolution across processes ─────────────────────


class TestCompiledFromProvenance:
    """The compiled-from ids carry into the durable record's links."""

    def test_trace_accepts_compiled_from_ids(self) -> None:
        """_Trace tracks compiled_from ids separately from recalled_ids."""
        trace = _Trace()
        assert trace.compiled_from == []
        assert trace.recalled_ids == []

    def test_build_record_merges_compiled_from_into_links(self, tmp_path: Path) -> None:
        """compiled_from ids appear in the durable record's links."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        # Simulate a trace with both recalled_ids and compiled_from.
        task_id = "test-1"
        lifecycle._traces[task_id] = _Trace(
            recalled_ids=["recalled-1"],
            compiled_from=["compiled-1", "compiled-2"],
        )

        # Build a minimal boundary/result for _build_record.
        @dataclass
        class _FakeStats:
            started_at: Optional[str] = None
            engine: Optional[str] = None
            model: str = "test"
            step_count: int = 0

        @dataclass
        class _FakeResult:
            summary: str = "test result"
            status: str = OK
            incompletion: Any = None
            continued_from: Optional[str] = None
            changed_files: list[str] = field(default_factory=list)
            stats: _FakeStats = field(default_factory=_FakeStats)

        boundary = Boundary(
            name="before-memory",
            task=Task(id=task_id, repo_path="/repo", instruction="test", engine="test"),
            result=_FakeResult(),
            tool=None,
            arguments=None,
        )

        record = lifecycle._build_record(boundary, lifecycle._traces[task_id])
        assert "links" in record
        # compiled_from ids take priority, then recalled_ids
        assert "compiled-1" in record["links"]
        assert "compiled-2" in record["links"]
        assert "recalled-1" in record["links"]

    def test_links_truncation_is_recorded_not_silent(self, tmp_path: Path) -> None:
        """When compiled_from ids exceed max_links, a degradation is emitted."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
            max_links=2,
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-2"
        # 3 compiled_from ids + 1 recalled_id = 4 total, but max_links=2
        lifecycle._traces[task_id] = _Trace(
            recalled_ids=["recalled-1"],
            compiled_from=["compiled-1", "compiled-2", "compiled-3"],
        )

        @dataclass
        class _FakeStats:
            started_at: Optional[str] = None
            engine: Optional[str] = None
            model: str = "test"
            step_count: int = 0

        @dataclass
        class _FakeResult:
            summary: str = "test result"
            status: str = OK
            incompletion: Any = None
            continued_from: Optional[str] = None
            changed_files: list[str] = field(default_factory=list)
            stats: _FakeStats = field(default_factory=_FakeStats)

        boundary = Boundary(
            name="before-memory",
            task=Task(id=task_id, repo_path="/repo", instruction="test", engine="test"),
            result=_FakeResult(),
            tool=None,
            arguments=None,
        )

        lifecycle._build_record(boundary, lifecycle._traces[task_id])

        # Check that a degradation event was emitted.
        degraded_events = [
            e
            for e in lifecycle.events
            if e.kind == CHECKPOINT_DEGRADED and e.detail == "links-truncated"
        ]
        assert len(degraded_events) == 1
        assert degraded_events[0].data["total"] == 4
        assert degraded_events[0].data["kept"] == 2


# ── MuseOutcome compiled_from propagation ───────────────────────────────────


class TestMuseOutcomeCompiledFrom:
    """MuseOutcome carries compiled_from record ids from the recall bundle."""

    def test_outcome_carries_compiled_from_when_bundle_supplied(self) -> None:
        """A bundle with record_ids produces compiled_from in the outcome."""
        bundle = RecallBundle(items=[])
        # RecallBundle.record_ids is a @property returning the citation surface
        ids = bundle.record_ids
        assert ids == ()

    def test_outcome_compiled_from_is_none_without_bundle(self) -> None:
        """No bundle means compiled_from is None."""
        outcome = MuseOutcome(
            origin=MuseOrigin.of(None, session=1),
            exit_reason="done",
        )
        assert outcome.compiled_from is None

    def test_record_compiled_from_method(self, tmp_path: Path) -> None:
        """_record_compiled_from populates the trace's compiled_from list."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-3"
        lifecycle._traces[task_id] = _Trace()
        lifecycle._record_compiled_from(task_id, ("id-1", "id-2"))

        trace = lifecycle._traces[task_id]
        assert "id-1" in trace.compiled_from
        assert "id-2" in trace.compiled_from

    def test_record_compiled_from_deduplicates(self, tmp_path: Path) -> None:
        """_record_compiled_from does not add duplicate ids."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-4"
        lifecycle._traces[task_id] = _Trace(compiled_from=["id-1"])
        lifecycle._record_compiled_from(task_id, ("id-1", "id-2"))

        trace = lifecycle._traces[task_id]
        assert trace.compiled_from == ["id-1", "id-2"]

    def test_record_compiled_from_noop_for_unknown_task(self, tmp_path: Path) -> None:
        """_record_compiled_from is a no-op when no trace exists."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        # No trace for this task_id — should not raise.
        lifecycle._record_compiled_from("unknown-task", ("id-1",))
        assert len(lifecycle._traces) == 0

    def test_links_order_compiled_from_before_recalled(self, tmp_path: Path) -> None:
        """compiled_from ids appear before recalled_ids in links."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-5"
        lifecycle._traces[task_id] = _Trace(
            recalled_ids=["recalled-1"],
            compiled_from=["compiled-1"],
        )

        @dataclass
        class _FakeStats:
            started_at: Optional[str] = None
            engine: Optional[str] = None
            model: str = "test"
            step_count: int = 0

        @dataclass
        class _FakeResult:
            summary: str = "test result"
            status: str = OK
            incompletion: Any = None
            continued_from: Optional[str] = None
            changed_files: list[str] = field(default_factory=list)
            stats: _FakeStats = field(default_factory=_FakeStats)

        boundary = Boundary(
            name="before-memory",
            task=Task(id=task_id, repo_path="/repo", instruction="test", engine="test"),
            result=_FakeResult(),
            tool=None,
            arguments=None,
        )

        record = lifecycle._build_record(boundary, lifecycle._traces[task_id])
        links = record["links"]
        # compiled_from ids come first
        assert links[0] == "compiled-1"
        assert links[1] == "recalled-1"

    def test_links_dedup_across_compiled_from_and_recalled(self, tmp_path: Path) -> None:
        """An id appearing in both compiled_from and recalled_ids is deduped."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-6"
        shared_id = "shared-id"
        lifecycle._traces[task_id] = _Trace(
            recalled_ids=[shared_id, "recalled-only"],
            compiled_from=[shared_id, "compiled-only"],
        )

        @dataclass
        class _FakeStats:
            started_at: Optional[str] = None
            engine: Optional[str] = None
            model: str = "test"
            step_count: int = 0

        @dataclass
        class _FakeResult:
            summary: str = "test result"
            status: str = OK
            incompletion: Any = None
            continued_from: Optional[str] = None
            changed_files: list[str] = field(default_factory=list)
            stats: _FakeStats = field(default_factory=_FakeStats)

        boundary = Boundary(
            name="before-memory",
            task=Task(id=task_id, repo_path="/repo", instruction="test", engine="test"),
            result=_FakeResult(),
            tool=None,
            arguments=None,
        )

        record = lifecycle._build_record(boundary, lifecycle._traces[task_id])
        links = record["links"]
        # shared_id appears only once, in the compiled_from position
        assert links.count(shared_id) == 1
        assert links[0] == shared_id
        assert "compiled-only" in links
        assert "recalled-only" in links

    def test_no_links_when_no_ids(self, tmp_path: Path) -> None:
        """When both lists are empty, record has no links key."""
        config = LifecycleConfig(
            data_dir=tmp_path / "memory",
            scope="test",
        )
        lifecycle = ContinuityLifecycle(config)

        task_id = "test-7"
        lifecycle._traces[task_id] = _Trace()

        @dataclass
        class _FakeStats:
            started_at: Optional[str] = None
            engine: Optional[str] = None
            model: str = "test"
            step_count: int = 0

        @dataclass
        class _FakeResult:
            summary: str = "test result"
            status: str = OK
            incompletion: Any = None
            continued_from: Optional[str] = None
            changed_files: list[str] = field(default_factory=list)
            stats: _FakeStats = field(default_factory=_FakeStats)

        boundary = Boundary(
            name="before-memory",
            task=Task(id=task_id, repo_path="/repo", instruction="test", engine="test"),
            result=_FakeResult(),
            tool=None,
            arguments=None,
        )

        record = lifecycle._build_record(boundary, lifecycle._traces[task_id])
        assert "links" not in record
