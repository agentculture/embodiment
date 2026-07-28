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

from embodiment.contract import OK, ModelResponse, Task
from embodiment.lifecycle import (
    CHECKPOINT_DEGRADED,
    ContinuityLifecycle,
    LifecycleConfig,
    _Trace,
)
from embodiment.loop import Boundary
from embodiment.muse import (
    COUNSEL_KIND_DURABLE,
    MARKER_DONE,
    MuseOrigin,
    MuseOutcome,
)
from embodiment.muse_runner import ThreadedMuseRunner
from embodiment.presence_engine import BoundaryContext
from embodiment.recall_bundle import (
    ADAPTER_FLAT_RECALL,
    LEVEL_FLAT,
    SOURCE_RECALL,
    BundleItem,
    BundleProvenance,
    RecallBundle,
)

# ── Criterion 1: counsel via durable channel, never Task.context ────────────


def _bundle(*ids: str) -> RecallBundle:
    """A bundle citing *ids*, shaped as a flat recall returns them."""
    return RecallBundle(
        items=[
            BundleItem(record_id=rid, text=f"remembered: {rid}", source=SOURCE_RECALL)
            for rid in ids
        ],
        provenance=BundleProvenance(adapter=ADAPTER_FLAT_RECALL, level=LEVEL_FLAT),
    )


def _muse_saying(text: str) -> Any:
    """A muse seam that answers once with *text*, then finishes."""
    calls = {"n": 0}

    def complete(messages: list[dict[str, Any]], **_kw: Any) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(content=text)
        return ModelResponse(content=MARKER_DONE)

    return complete


class TestCounselNeverReplacesTaskContext:
    """Compiled counsel arrives via the counsel channel, not ``Task.context``.

    These drive a REAL runner over a REAL bundle. The first version of this
    file constructed a :class:`MuseInsight` inline and then asserted
    ``task.context`` was unchanged — which it always is when nothing delivers
    anything. A test that never invokes delivery cannot detect delivery
    writing to the wrong place: it passes just as happily on broken code.
    """

    def test_task_context_is_byte_identical_after_real_counsel_delivery(self) -> None:
        context_before = "original host context"
        task = Task(
            id="test-1",
            repo_path="/repo",
            instruction="do something",
            context=context_before,
            engine="test",
        )
        runner = ThreadedMuseRunner(
            _muse_saying("GUIDANCE[durable]: reconsider the framing.\n" + MARKER_DONE),
            recall_bundle=_bundle("mem-1", "mem-2"),
        )
        with runner:
            runner.start()
            runner.consider(BoundaryContext(kind="cadence-tick", step_count=1, reason="a beat"))
            assert runner.wait_idle(5.0), "the muse never finished"
            comments = runner.drain(step_count=1)

        assert comments, "nothing was delivered — the assertion below would be vacuous"
        assert task.context == context_before

    def test_the_delivered_counsel_is_labelled_durable(self) -> None:
        runner = ThreadedMuseRunner(
            _muse_saying("GUIDANCE[durable]: the store is not the same as the truth.\n"),
            recall_bundle=_bundle("mem-1"),
        )
        with runner:
            runner.start()
            runner.consider(BoundaryContext(kind="cadence-tick", step_count=1, reason="a beat"))
            assert runner.wait_idle(5.0)
            runner.drain(step_count=1)

        assert runner.counts["insights_delivered"] >= 1
        assert runner.snapshot()["kind_delivered"].get(COUNSEL_KIND_DURABLE, 0) >= 1

    def test_the_bundle_reaches_the_muse_at_all(self) -> None:
        """Without this, the two above could pass on a bundle nobody ever read."""
        seen: list[str] = []

        def complete(messages: list[dict[str, Any]], **_kw: Any) -> ModelResponse:
            seen.append("\n".join(str(m.get("content", "")) for m in messages))
            return ModelResponse(content=MARKER_DONE)

        runner = ThreadedMuseRunner(complete, recall_bundle=_bundle("mem-unique-77"))
        with runner:
            runner.start()
            runner.consider(BoundaryContext(kind="cadence-tick", step_count=1, reason="a beat"))
            assert runner.wait_idle(5.0)

        assert seen, "the muse was never called"
        assert "mem-unique-77" in seen[0], "the recall bundle never reached the wire"

    def test_the_runner_carries_the_citation_surface_back(self) -> None:
        runner = ThreadedMuseRunner(
            _muse_saying("GUIDANCE[durable]: something.\n"),
            recall_bundle=_bundle("mem-a", "mem-b"),
        )
        with runner:
            runner.start()
            runner.consider(BoundaryContext(kind="cadence-tick", step_count=1, reason="a beat"))
            assert runner.wait_idle(5.0)

        assert runner.compiled_from == ("mem-a", "mem-b")

    def test_the_citation_surface_does_not_depend_on_a_session_finishing(self) -> None:
        """The determinism fix, pinned.

        The first implementation accumulated these ids in ``_absorb``, so the
        surface was populated only when a background session happened to
        complete before the host read it. Measured on the greenhouse demo, that
        varied run to run on identical input — 3 completed sessions one run, 0
        the next — which left a provenance field intermittently empty for
        timing reasons alone. Here the worker thread is never even started.
        """
        runner = ThreadedMuseRunner(
            _muse_saying("GUIDANCE[durable]: x.\n"), recall_bundle=_bundle("mem-a", "mem-b")
        )
        assert runner.counts["sessions_completed"] == 0
        assert runner.compiled_from == ("mem-a", "mem-b")

    def test_a_museless_runner_cites_nothing_rather_than_guessing(self) -> None:
        runner = ThreadedMuseRunner(_muse_saying("GUIDANCE[durable]: x.\n"))
        with runner:
            runner.start()
            runner.consider(BoundaryContext(kind="cadence-tick", step_count=1, reason="a beat"))
            assert runner.wait_idle(5.0)

        assert runner.compiled_from == ()


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

    def test_the_lifecycle_pulls_the_citation_surface_from_a_wired_muse(
        self, tmp_path: Path
    ) -> None:
        """The join: a muse's ids reach the trace without the host pushing them."""

        class _Muse:
            compiled_from = ("id-1", "id-2")

        checkpoints = ContinuityLifecycle(
            LifecycleConfig(data_dir=tmp_path / "memory", scope="test"), muse=_Muse()
        )
        trace = _Trace()
        checkpoints._gather_compiled_from(trace)
        assert trace.compiled_from == ["id-1", "id-2"]

    def test_gathering_deduplicates_against_what_the_trace_already_holds(
        self, tmp_path: Path
    ) -> None:
        class _Muse:
            compiled_from = ("id-1", "id-2")

        checkpoints = ContinuityLifecycle(
            LifecycleConfig(data_dir=tmp_path / "memory", scope="test"), muse=_Muse()
        )
        trace = _Trace(compiled_from=["id-1"])
        checkpoints._gather_compiled_from(trace)
        assert trace.compiled_from == ["id-1", "id-2"]

    def test_no_muse_means_no_ids_and_no_degradation(self, tmp_path: Path) -> None:
        """A museless host is the primary path, not a degraded one."""
        checkpoints = ContinuityLifecycle(
            LifecycleConfig(data_dir=tmp_path / "memory", scope="test")
        )
        trace = _Trace()
        checkpoints._gather_compiled_from(trace)
        assert trace.compiled_from == []
        assert checkpoints.events == ()

    def test_a_broken_provenance_source_costs_links_not_the_record(self, tmp_path: Path) -> None:
        """Losing links must never cost the record they belong to (C3)."""

        class _Hostile:
            @property
            def compiled_from(self) -> tuple[str, ...]:
                raise RuntimeError("broken")

        checkpoints = ContinuityLifecycle(
            LifecycleConfig(data_dir=tmp_path / "memory", scope="test"), muse=_Hostile()
        )
        trace = _Trace()
        checkpoints._gather_compiled_from(trace)  # must not raise
        assert trace.compiled_from == []
        details = [event.detail for event in checkpoints.events]
        assert "compiled-from-lost" in details, "the loss must be recorded, not silent"

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
