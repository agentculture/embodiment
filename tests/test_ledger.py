"""Tests for :mod:`embodiment.ledger` — the degradation fold (task t9).

Three things are proved here, and the first is the acceptance criterion.

**1. The enumeration is exhaustive by construction.** Every degradation code
embodiment can record is provoked through the REAL module that mints it, and the
resulting container is folded through the ledger and asserted to carry a
host-visible record. "Every" is not a hand-written list: the expected set comes
from :func:`embodiment.ledger.known_codes`, which reads each lane's own exported
``DEGRADED_*`` / ``DROPPED_*`` / ``CODE_*`` constants at runtime. Add a code to a
lane and :meth:`TestEveryCodeIsCovered.test_no_code_lacks_a_covering_path` fails
until someone writes a path that provokes it. That is the difference between a
test and a checklist — and it has already paid: the task brief listed 36 codes
and the derivation found 37 (``continuity.CODE_INVALID_RECORD`` was missed by
hand).

**2. Every covering path is a REAL one.** Exhaustive-by-construction is worth
exactly what the provokers do, and two of them satisfied it by calling the
runner's own ``_record`` with the code they were named for — which proves a
code can appear in a record and nothing about any path producing it
(embodiment#18). :class:`TestNoProvokerTakesThePrivateDoor` reads this file's
own AST and fails when a provoker touches a private attribute on an object;
its docstring holds the exact rule, its three exemptions, and the one thing it
deliberately does not claim.

**3. Nothing is fabricated.** ``continuity.Degradation`` carries no step index,
so a continuity-sourced record's ``step_index`` is ``None`` and its ``to_dict``
omits the key — never a ``0`` a host could read as "step zero". A genuine zero
from a lane that does stamp one survives unchanged.

Hermetic throughout, following the shape the sibling suites established: every
eidetic call is anchored at a throwaway ``tmp_path`` store, every coherence call
injects an ``embed_fn``, no socket is opened, and every wait on the muse's
thread is bounded so a broken implementation fails rather than hangs.
"""

from __future__ import annotations

import ast
import json
import threading
from dataclasses import FrozenInstanceError, dataclass, fields
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

import pytest

from embodiment import continuity, ledger, lifecycle, loop, muse, muse_runner, subagent
from embodiment.contract import (
    OK,
    ContextPacket,
    ModelResponse,
    SubResult,
    Task,
    TaskResult,
    ToolCall,
)
from embodiment.events import EventEmitter
from embodiment.lifecycle import CHECKPOINT_DEGRADED, ContinuityLifecycle, LifecycleConfig
from embodiment.loop import (
    Boundary,
    LoopAborted,
    LoopControls,
    LoopDegradation,
    ToolOutcome,
    run,
)
from embodiment.muse import MARKER_DONE, MuseControls, MuseDegradation, MuseLoop
from embodiment.muse_runner import ThreadedMuseRunner
from embodiment.presence_engine import BoundaryContext, PresenceEngine

#: Every wait on the muse's thread is bounded by this: a broken implementation
#: fails an assertion instead of hanging CI.
_TIMEOUT = 5.0


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
    """A ``complete`` seam replaying fixed turns; an ``Exception`` item raises."""

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
            summary = arguments.get("summary") or "done"
            return ToolOutcome(result="done", finished=True, finish_summary=summary)
        return ToolOutcome(result=f"{name} ok")


def _drive(*responses: Any, **kw: Any) -> Any:
    """Run the real loop over *responses*, returning the ``LoopOutcome``."""
    task = kw.pop("task", None) or _task()
    max_steps = kw.pop("max_steps", 4)
    executor = kw.pop("executor", None) or Executor()
    return run(Scripted(*responses), task, executor=executor, max_steps=max_steps, **kw)


def _muse_boundary(*, step: int = 0, **kw: Any) -> BoundaryContext:
    return BoundaryContext(kind="cadence-tick", step_count=step, reason="every-n", **kw)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic, offline stand-in for coherence's embedding endpoint."""
    return [[float(len(text) % 7) / 7.0] * 16 for text in texts]


def _lifecycle_config(tmp_path: Path, **kw: Any) -> LifecycleConfig:
    fields_: dict[str, Any] = {
        "data_dir": tmp_path / "store",
        "workdir": tmp_path,
        "embed_fn": _fake_embed,
        "recall_mode": "keyword",
    }
    fields_.update(kw)
    return LifecycleConfig(**fields_)


def _lifecycle_boundary(name: str, **kw: Any) -> Boundary:
    task = kw.pop("task", None) or _task()
    result = kw.pop("result", None)
    if result is None and "result" not in kw:
        result = TaskResult(task_id=task.id, status=OK, summary="did the thing")
    return Boundary(name=name, task=task, result=result, **kw)


class _AssessStub:
    """Stands in for ``continuity.assess`` so no engine or embedder is dialled."""

    def __call__(self, path: Any, **kwargs: Any) -> continuity.AssessOutcome:
        return continuity.AssessOutcome(
            ok=True,
            domains={"quality": {"scores": {}}},
            unavailable={},
            degradation=None,
        )


class _RememberStub:
    """Stands in for ``continuity.remember`` so the durable write touches no store.

    The lifecycle provokers below need the ``before-memory`` boundary to run to
    completion — that is where the record is built and its links are counted —
    but the eidetic write itself belongs to a different lane, with its own
    provokers. Isolating it here keeps these two paths hermetic and fast, the
    same reason :class:`_AssessStub` stands in for coherence.
    """

    def __call__(self, record: Any, **_kwargs: Any) -> continuity.RememberOutcome:
        return continuity.RememberOutcome(
            ok=True,
            record_id=str(record.get("id")) if isinstance(record, dict) else None,
            degradation=None,
        )


class _FakeResult:
    def __init__(self, ok: bool, reason: str = "no_conn") -> None:
        self.ok = ok
        self.reason = reason
        self.connected = ok
        self.mid = 1


class FakeClient:
    """A fake events transport: never touches a socket."""

    def __init__(self, *, ok: bool = True, raises: Optional[BaseException] = None) -> None:
        self._ok = ok
        self._raises = raises

    def publish_event(self, envelope: Any, topic: str, **_kw: Any) -> _FakeResult:
        if self._raises is not None:
            raise self._raises
        return _FakeResult(ok=self._ok)

    def close(self) -> None:
        return None


def _loop_event() -> Any:
    return loop.LoopEvent(kind="step", detail="read_file", data={"step_index": 0})


# ── the degradation paths, one per code ───────────────────────────────────────
#
# Each provoker drives the REAL module onto one degradation path and returns
# whatever the ledger should be able to read. Signature is uniform so the
# enumeration can call every one of them the same way.

Provoker = Callable[[Path, pytest.MonkeyPatch], list[ledger.LedgerRecord]]

#: The delivery stream's provoker shape (task t5). It takes nothing and returns
#: the runner's own delivery records: a delivery is not a degradation and does
#: not fold through :mod:`embodiment.ledger` at all, so there is no store to
#: anchor and no fault to inject — only a real drive to run.
DeliveryProvoker = Callable[[], list["muse_runner.MuseDelivery"]]


# -- loop ---------------------------------------------------------------------


def _loop_attachment(tmp_path: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    task = _task(attachments=[{"path": str(tmp_path / "gone.png"), "media_type": "image/png"}])
    return ledger.from_loop(_drive(_turn(_call("finish")), task=task))


def _loop_tool_arguments(tmp_path: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A seam hands back a ``Path`` where the wire format needs JSON.

    The degradation fires when the turn is REPLAYED into the message list, so
    the drive needs a second turn — a single finishing turn is never serialized
    back and would provoke nothing.
    """
    unserializable = _turn(_call("read_file", path=tmp_path / "somewhere.txt"))
    return ledger.from_loop(_drive(unserializable, _turn(_call("finish"))))


def _loop_overflow_retry(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    attempts: list[int] = []

    def flaky(messages: list[dict[str, Any]]) -> ModelResponse:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("This model's maximum context length is 4096 tokens")
        return _turn(_call("finish"))

    return ledger.from_loop(_drive(flaky, controls=LoopControls(context_budget=200)))


def _loop_overflow_exhausted(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    controls = LoopControls(context_budget=50, max_overflow_retries=2)
    error = RuntimeError("maximum context length exceeded")
    with pytest.raises(LoopAborted) as excinfo:
        _drive(
            error,
            controls=controls,
        )
    # The partial's ledger rides the exception; the reader unwraps it.
    return ledger.from_loop(excinfo.value)


def _loop_media_rejected(tmp_path: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    png = tmp_path / "fixture.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    attempts: list[Any] = []

    def refusing(messages: list[dict[str, Any]]) -> ModelResponse:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("HTTP 400: At most 0 image(s) may be provided in one prompt")
        return _turn(_call("finish"))

    task = _task(attachments=[{"path": str(png), "media_type": "image/png"}])
    return ledger.from_loop(_drive(refusing, task=task))


def _loop_hook_error(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def hooks(event: Any) -> None:
        raise RuntimeError("hook registry down")

    return ledger.from_loop(_drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks))


def _loop_progress(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def boom(*_args: Any) -> None:
        raise RuntimeError("sink down")

    return ledger.from_loop(_drive(_turn(_call("finish")), progress=boom))


def _loop_observer(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def boom(_event: Any) -> None:
        raise RuntimeError("observer down")

    return ledger.from_loop(_drive(_turn(_call("finish")), observer=boom))


class _RaisingSink:
    """The full ``PresenceSink`` protocol, with every callback raising."""

    @property
    def active(self) -> bool:
        return True

    def acknowledge(self, packet: Any) -> Any:
        raise RuntimeError("presence down")

    def on_operator_message(self, text: str) -> Any:
        raise RuntimeError("presence down")

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False) -> Any:
        raise RuntimeError("presence down")


def _loop_presence(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    return ledger.from_loop(_drive(_turn(_call("finish")), presence=_RaisingSink()))


def _loop_continuity(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def boom(_boundary: Boundary) -> None:
        raise RuntimeError("eidetic down")

    return ledger.from_loop(_drive(_turn(_call("finish")), continuity=boom))


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


def _loop_spawn_unavailable(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    # A tool asks to delegate and no ``SubagentFn`` is wired: the model asked
    # for help and got none, which must not be visible only as absent work.
    return ledger.from_loop(
        _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DelegatingExecutor(),
            spawn_allowance=1,
        )
    )


def _loop_spawn_failed(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def boom(_call: Any) -> Any:
        raise RuntimeError("child harness down")

    return ledger.from_loop(
        _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DelegatingExecutor(),
            subagent=boom,
            spawn_allowance=1,
        )
    )


def _loop_spawn_duplicate(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """An executor that ledgers the very child it delegated to the loop."""
    child = SubResult(task_id="child-1", engine="e", model="m", status="ok", summary="dupe")

    class _DoubleLedgering(_DelegatingExecutor):
        def __init__(self) -> None:
            self.sub_results: list[Any] = [child]

    def seam(_call: Any) -> Any:
        return subagent.SubagentResult(
            sub_result=child,
            model_turns=1,
            result="child done",
            exit_reason=loop.EXIT_FINISHED,
        )

    return ledger.from_loop(
        _drive(
            _turn(_call("delegate")),
            max_steps=4,
            executor=_DoubleLedgering(),
            subagent=seam,
            spawn_allowance=1,
        )
    )


def _loop_synthesis(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    # The budget runs out, the forced final synthesis turn is attempted, and the
    # seam fails on it. THIS is the degraded budget exit; a clean one records
    # nothing (see TestABudgetExitIsNotADegradation).
    return ledger.from_loop(
        _drive(_turn(_call("read_file", path="a")), RuntimeError("dead"), max_steps=2)
    )


# -- muse ---------------------------------------------------------------------


def _muse_thinking(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    thinking = MuseLoop(Scripted(RuntimeError("connection refused")))
    return ledger.from_muse(thinking.think(_muse_boundary(step=4)))


def _muse_bundle_truncated(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A recall bundle larger than its OWN budget — not the snapshot budget.

    The two budgets are deliberately independent: a compiled bundle of notes and
    traversal results exceeds the 600-char boundary snapshot by construction, and
    clipping it through that limit would destroy exactly the material the muse is
    meant to compile. Clipping it through its own budget is legitimate; doing so
    silently is not.
    """

    class _Item:
        record_id = "r1"
        source = "eidetic-recall"
        text = "x" * 5000

    class _Bundle:
        items = (_Item(),)

    thinking = MuseLoop(
        Scripted(_resp(MARKER_DONE)),
        controls=MuseControls(max_bundle_chars=100),
    )
    return ledger.from_muse(thinking.think(_muse_boundary(), recall_bundle=_Bundle()))


def _muse_sink(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def sink(_insight: Any) -> None:
        raise RuntimeError("queue is closed")

    thinking = MuseLoop(Scripted(_resp("a"), _resp(MARKER_DONE)), sink=sink)
    return ledger.from_muse(thinking.think(_muse_boundary()))


def _muse_unreadable(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    class Landmine:
        def __str__(self) -> str:
            raise RuntimeError("cannot render")

    thinking = MuseLoop(Scripted(_resp("thought " + MARKER_DONE)))
    return ledger.from_muse(thinking.think(_muse_boundary(task_state=Landmine())))


def _muse_marker_unreadable(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """Provokes DEGRADED_MARKER_UNREADABLE via a malformed counsel-kind marker."""
    thinking = MuseLoop(
        Scripted(_resp("GUIDANCE[wharrgarbl]: still useful advice\n" + MARKER_DONE))
    )
    return ledger.from_muse(thinking.think(_muse_boundary()))


# -- muse tool seam (task t10) ------------------------------------------------
#
# Every one of these drives a real ``MuseLoop`` with a real ``MuseToolBench``
# through ``think`` — the seam a host wires — so the codes below are covered by
# the path that actually mints them, not by a hand-built record.


class _ScriptedTools:
    """A tool-CARRYING muse seam: messages AND a schema in, one response out."""

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies) or [_resp(MARKER_DONE)]

    def __call__(self, _messages: list[dict[str, Any]], _tools: list[dict[str, Any]]) -> Any:
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


#: A host-supplied thinking-tool schema. ``embodiment.muse`` ships none.
_PAD_SCHEMA: tuple[dict[str, Any], ...] = ({"type": "function", "function": {"name": "intend"}},)


def _pad_call(text: str = "x") -> ModelResponse:
    return ModelResponse(
        content="calling the pad",
        tool_calls=[ToolCall(id="c1", name="intend", arguments={"text": text})],
    )


def _bench(*replies: Any, execute: Any = None) -> muse.MuseToolBench:
    return muse.MuseToolBench(
        schema=_PAD_SCHEMA,
        complete=_ScriptedTools(*replies),
        execute=execute if execute is not None else (lambda _n, _a: "n1 recorded"),
    )


def _muse_tool_failed(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A wired thinking tool that raises: readable text to the muse, a record to the host."""

    def explode(_name: str, _arguments: dict[str, Any]) -> Any:
        raise RuntimeError("the pad is on fire")

    thinking = MuseLoop(
        Scripted(_resp(MARKER_DONE)),
        tools=_bench(_pad_call(), _resp(MARKER_DONE), execute=explode),
    )
    return ledger.from_muse(thinking.think(_muse_boundary()))


def _muse_tool_rounds(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A muse that keeps calling tools until its round allowance runs out."""
    thinking = MuseLoop(
        Scripted(_resp(MARKER_DONE)),
        controls=MuseControls(max_turns=3, max_tool_rounds=1),
        tools=_bench(_pad_call("again")),
    )
    return ledger.from_muse(thinking.think(_muse_boundary()))


def _muse_tools_withheld(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A bench wired to a subagent-depth muse: withheld, and the host is told."""
    thinking = MuseLoop(
        Scripted(_resp(MARKER_DONE)),
        tools=_bench(_resp(MARKER_DONE)),
        depth=2,
    )
    return ledger.from_muse(thinking.think(_muse_boundary()))


# -- muse_runner --------------------------------------------------------------


class _Gated:
    """A muse seam that announces it started and waits to be released."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.started.set()
        assert self.release.wait(_TIMEOUT), "the gated muse seam was never released"
        reply = self.replies.pop(0) if self.replies else _resp(MARKER_DONE)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _runner_thread(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def refuse(**_kwargs: Any) -> Any:
        raise RuntimeError("can't start new thread")

    runner = ThreadedMuseRunner(Scripted(_resp(MARKER_DONE)), thread_factory=refuse)
    try:
        runner.consider(_muse_boundary(step=1))
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


class _HostileControls:
    """A host's controls object whose first read explodes — a harness bug.

    Carries every field :class:`~embodiment.muse.MuseControls` does so nothing
    fails for the wrong reason; only ``max_context_chars`` detonates, and it
    detonates on the WORKER thread, inside the muse's prompt building.
    """

    max_turns = 4
    max_quiet_turns = 1
    max_insight_chars = 2000
    max_bundle_chars = 2000

    @property
    def max_context_chars(self) -> int:
        raise RuntimeError("the controls exploded")


def _runner_worker(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """The 'should be unreachable' rung: the worker itself dies.

    ``MuseLoop.think`` is documented never to raise, so the runner's outer guard
    exists for bugs *around* it — and a host handing in a controls object whose
    read explodes is exactly that bug. It arrives through the public
    ``controls=`` seam, so the guard is provoked rather than simulated: the
    record is the whole point, because without it a dead worker is an absent
    mind a host mistakes for a quiet one.
    """
    runner = ThreadedMuseRunner(Scripted(_resp(MARKER_DONE)), controls=_HostileControls())
    try:
        runner.consider(_muse_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


def _runner_endpoint(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    runner = ThreadedMuseRunner(Scripted(ConnectionRefusedError("dead port")))
    try:
        runner.consider(_muse_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


def _runner_stale(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    # The marker is load-bearing (task t3). An UNLABELLED `GUIDANCE:` line is
    # durable-kind by default, and durable counsel is never dropped for
    # loop-distance staleness alone — so a bare line can no longer provoke this
    # code at all. Only step-sensitive counsel ages out, which is the point of
    # the kind split rather than an inconvenience to work around here.
    runner = ThreadedMuseRunner(Scripted(_resp("GUIDANCE[step]: about step one " + MARKER_DONE)))
    try:
        runner.consider(_muse_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)
        assert runner.drain(step_count=400) == []
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


def _runner_late(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    runner = ThreadedMuseRunner(Scripted(_resp("GUIDANCE: unread " + MARKER_DONE)))
    runner.consider(_muse_boundary(step=1))
    assert runner.wait_idle(_TIMEOUT)
    runner.close(timeout=_TIMEOUT)  # never drained; the buffered insight is late
    return ledger.from_muse_runner(runner)


def _runner_overflow(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    seam = Scripted(
        _resp("GUIDANCE: one"),
        _resp("GUIDANCE: two"),
        _resp("GUIDANCE: three"),
        _resp("GUIDANCE: four " + MARKER_DONE),
    )
    runner = ThreadedMuseRunner(seam, max_pending=2, controls=MuseControls(max_turns=4))
    try:
        runner.consider(_muse_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)
        runner.drain(step_count=1)
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


def _runner_boundary(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    seam = _Gated(_resp("a " + MARKER_DONE), _resp("c " + MARKER_DONE))
    runner = ThreadedMuseRunner(seam)
    try:
        runner.consider(_muse_boundary(step=1))
        assert seam.started.wait(_TIMEOUT)
        runner.consider(_muse_boundary(step=2))  # queued
        runner.consider(_muse_boundary(step=3))  # supersedes step 2
        seam.release.set()
        assert runner.wait_idle(_TIMEOUT)
        return ledger.from_muse_runner(runner)
    finally:
        seam.release.set()
        runner.close(timeout=_TIMEOUT)


def _runner_compilation_starved(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """Background compilation loses the muse's ONE thread to boundary counsel.

    The real path (embodiment#18), driven entirely through the public seam: a
    gated boundary session holds the thread, ``compile`` queues behind it, and a
    second boundary outranks the queued compilation and takes the slot. The
    compilation never runs, and saying so is what this code is for.
    """
    seam = _Gated(_resp("a " + MARKER_DONE), _resp("b " + MARKER_DONE))
    runner = ThreadedMuseRunner(seam)
    try:
        runner.consider(_muse_boundary(step=1))
        assert seam.started.wait(_TIMEOUT)
        runner.compile()  # queued behind the session in flight
        runner.consider(_muse_boundary(step=2))  # boundary counsel outranks it
        seam.release.set()
        assert runner.wait_idle(_TIMEOUT)
        return ledger.from_muse_runner(runner)
    finally:
        seam.release.set()
        runner.close(timeout=_TIMEOUT)


def _runner_closer(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """A host-wired teardown raises at close (task t15).

    ``closers`` is how a host ties something it owns — the muse's workspace is
    the motivating case — to this lane's close. The runner runs each one and
    lets none of them raise, so a teardown that failed would be invisible
    without this record, and what it failed to close is state the host now has
    to deal with by hand. Public seam only: the callable goes in through the
    constructor and comes out through ``close``.
    """

    def explode() -> None:
        raise OSError("the container engine refused the teardown")

    runner = ThreadedMuseRunner(Scripted(_resp(MARKER_DONE)), closers=(explode,))
    runner.close(timeout=_TIMEOUT)
    return ledger.from_muse_runner(runner)


def _runner_counsel_displaced(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """Compiled counsel evicts undrained boundary counsel — the priority inversion.

    Also public-seam only: a one-slot drain buffer holds boundary counsel nobody
    drained, ``compile`` produces the next insight, and the lower-ranked work
    displaces the higher-ranked. Distinct from ``DROPPED_OVERFLOW``, which is
    the same-class backpressure a host answers differently.
    """
    seam = Scripted(
        _resp("GUIDANCE: boundary counsel " + MARKER_DONE),
        _resp("GUIDANCE: compiled counsel " + MARKER_DONE),
    )
    runner = ThreadedMuseRunner(seam, max_pending=1)
    try:
        runner.consider(_muse_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)  # buffered, deliberately never drained
        runner.compile()
        assert runner.wait_idle(_TIMEOUT)
        return ledger.from_muse_runner(runner)
    finally:
        runner.close(timeout=_TIMEOUT)


# -- muse_runner, the DELIVERY vocabulary (task t5) ----------------------------
#
# Not a degradation path — see :class:`TestADeliveryIsNeverADegradation`. It is
# held to the same producer rule for the same reason (embodiment#18): a
# vocabulary entry whose only producer is a test is dead vocabulary, whichever
# stream it belongs to.


def _terminal_delivery() -> list[muse_runner.MuseDelivery]:
    """The terminal beat of a REAL drive mints the delivery record.

    Nothing here records anything by hand. :func:`embodiment.loop.run` fires
    its one terminal beat at drive end (task t25), the pump drains without
    starting a session (task t4), and the runner mints the record on the way
    through. The muse's seam is GATED and released by the drive's own
    ``observer`` on the ``exit`` event — which the loop fires after the last
    per-step presence boundary and before the terminal beat — so the counsel is
    provably still buffered when that beat runs, with no sleep and no race.
    """
    seam = _Gated(_resp("GUIDANCE: check the empty case " + MARKER_DONE))
    runner = ThreadedMuseRunner(seam)
    packet = ContextPacket(original="do the thing", ack="on it")

    def observer(event: Any) -> None:
        if event.kind == "exit":
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)

    try:
        outcome = _drive(
            _turn(_call("finish")),
            task=_task(context_packet=packet),
            presence=PresenceEngine(muse=runner),
            observer=observer,
        )
        assert outcome.exit_reason == loop.EXIT_FINISHED
        return list(runner.deliveries)
    finally:
        seam.release.set()
        runner.close(timeout=_TIMEOUT)


# -- events -------------------------------------------------------------------


def _events_unavailable(_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    import embodiment.events as events_mod

    def boom() -> Any:
        raise ImportError("no module named events_cli")

    monkeypatch.setattr(events_mod, "_load_envelope_core", boom)
    emitter = EventEmitter()
    emitter(_loop_event())
    return ledger.from_events(emitter)


def _events_connect(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    def factory() -> Any:
        raise ConnectionRefusedError("broker refused")

    emitter = EventEmitter(client_factory=factory)
    emitter(_loop_event())
    return ledger.from_events(emitter)


def _events_publish(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    emitter = EventEmitter(client_factory=lambda: FakeClient(ok=False))
    emitter(_loop_event())
    return ledger.from_events(emitter)


def _events_emit(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    emitter = EventEmitter(client_factory=lambda: FakeClient(raises=RuntimeError("paho blew up")))
    emitter(_loop_event())
    return ledger.from_events(emitter)


# -- continuity ---------------------------------------------------------------


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "rec-1", "text": "hello world", "type": "note"}
    base.update(overrides)
    return base


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "artifact.md"
    path.write_text("The team migrated the store after data loss.\n", encoding="utf-8")
    return path


def _continuity_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "No module named 'eidetic'")
    return ledger.from_continuity(continuity.remember(_record(), data_dir=tmp_path))


def _continuity_anchor(_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.delenv("DR_DATA_DIR", raising=False)
    return ledger.from_continuity(continuity.remember(_record()))


def _continuity_subsystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    class _Exploding:
        def upsert(self, record: Any) -> None:
            raise RuntimeError("mongo is down")

    monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Exploding())
    return ledger.from_continuity(continuity.remember(_record(), data_dir=tmp_path))


def _continuity_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    class _Wrong:
        def search(self, *args: Any, **kwargs: Any) -> Any:
            return ["not a record"]

    monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Wrong())
    return ledger.from_continuity(continuity.recall("hi", data_dir=tmp_path))


def _continuity_invalid(tmp_path: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    return ledger.from_continuity(continuity.remember({"id": "x"}, data_dir=tmp_path))


def _continuity_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    payload = {
        "domains": {"quality": {}},
        "unavailable": {"meaning": {"code": "embed_endpoint_unreachable", "reason": "down"}},
    }
    monkeypatch.setattr(continuity, "_coherence_assess", lambda *a, **k: payload)
    return ledger.from_continuity(continuity.assess(_artifact(tmp_path)))


def _continuity_artifact(tmp_path: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    return ledger.from_continuity(continuity.assess(tmp_path / "nope.md", embed_fn=_fake_embed))


def _continuity_reinforce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    class _ReadOnly:
        def search(self, *args: Any, **kwargs: Any) -> list[Any]:
            from eidetic.memory.record import Record
            from eidetic.memory.scope import Scope

            hit = Record(
                id="r1",
                text="iceland",
                type="note",
                hash="",
                metadata={},
                scope=Scope("default", "public"),
            )
            hit.score = 1.0
            return [hit]

        def upsert(self, record: Any) -> None:
            raise OSError("read-only file system")

    monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _ReadOnly())
    return ledger.from_continuity(continuity.recall("iceland", data_dir=tmp_path, mode="exact"))


# -- lifecycle ----------------------------------------------------------------


def _lifecycle_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    monkeypatch.setattr(continuity, "assess", _AssessStub())
    checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path))
    # A malformed boundary detonates inside the checkpoint's own attribute
    # access — proof the outer guard catches ANY exception, not the anticipated
    # ones — and the fault is recorded rather than raised.
    checkpoints(Boundary(name="before-memory", task=_task(), result=None))  # type: ignore[arg-type]
    return ledger.from_lifecycle(checkpoints)


def _lifecycle_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    monkeypatch.setattr(continuity, "assess", _AssessStub())

    def boom(_event: Any) -> None:
        raise RuntimeError("sink exploded")

    checkpoints = ContinuityLifecycle(
        _lifecycle_config(tmp_path, consider_every_action=True), on_event=boom
    )
    checkpoints(_lifecycle_boundary("before-action", tool="a", arguments={}))
    return ledger.from_lifecycle(checkpoints)


def _lifecycle_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    monkeypatch.setattr(continuity, "assess", _AssessStub())
    checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path, max_tracked_tasks=2))
    for index in range(5):
        checkpoints(
            _lifecycle_boundary("before-action", task=_task(id=f"t{index}"), tool="w", arguments={})
        )
    return ledger.from_lifecycle(checkpoints)


def _lifecycle_consequential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    monkeypatch.setattr(continuity, "assess", _AssessStub())

    def boom(_boundary: Boundary) -> bool:
        raise RuntimeError("host predicate exploded")

    checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path, consequential=boom))
    checkpoints(_lifecycle_boundary("before-action", tool="write_file", arguments={}))
    return ledger.from_lifecycle(checkpoints)


def _lifecycle_links_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    """More cited ids than ``max_links``, over the REAL ``before-memory`` boundary.

    The muse's citation surface is the public source of those ids: a wired muse
    reports two, ``max_links`` is one, and the checkpoint builds the durable
    record itself — so the truncation is the lifecycle's own, not a hand-called
    ``_build_record``.
    """
    monkeypatch.setattr(continuity, "assess", _AssessStub())
    monkeypatch.setattr(continuity, "remember", _RememberStub())

    class _CitingMuse:
        """Anything exposing ``compiled_from`` — the duck type lifecycle documents."""

        compiled_from = ("id-a", "id-b")

    checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path, max_links=1), muse=_CitingMuse())
    checkpoints(_lifecycle_boundary("before-memory"))
    return ledger.from_lifecycle(checkpoints)


def _lifecycle_compiled_from_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[ledger.LedgerRecord]:
    """A muse whose citation surface cannot be read costs links, not the record.

    Driven over the same real boundary: the checkpoint reaches for the muse's
    ``compiled_from`` on its own, the read explodes, and the write goes ahead
    without the links rather than failing with them.
    """
    monkeypatch.setattr(continuity, "assess", _AssessStub())
    monkeypatch.setattr(continuity, "remember", _RememberStub())

    class _HostileMuse:
        @property
        def compiled_from(self) -> tuple[str, ...]:
            raise RuntimeError("the provenance source is broken")

    checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path), muse=_HostileMuse())
    checkpoints(_lifecycle_boundary("before-memory"))
    return ledger.from_lifecycle(checkpoints)


# -- the ledger's own rung ----------------------------------------------------


def _ledger_unreadable(_tmp: Path, _mp: pytest.MonkeyPatch) -> list[ledger.LedgerRecord]:
    """C3 applies to the observability surface too: it cannot go quiet either."""
    return ledger.from_loop(object())


# ── the coverage map ──────────────────────────────────────────────────────────
#
# Keyed by (source, code) — the SAME key ``known_codes()`` produces, so the two
# are compared directly and neither can drift silently past the other.

PROVOKERS: dict[tuple[str, str], Provoker] = {
    (ledger.SOURCE_LOOP, loop.DEGRADED_ATTACHMENT): _loop_attachment,
    (ledger.SOURCE_LOOP, loop.DEGRADED_TOOL_ARGUMENTS): _loop_tool_arguments,
    (ledger.SOURCE_LOOP, loop.DEGRADED_CONTEXT_OVERFLOW): _loop_overflow_retry,
    (ledger.SOURCE_LOOP, loop.DEGRADED_OVERFLOW_EXHAUSTED): _loop_overflow_exhausted,
    (ledger.SOURCE_LOOP, loop.DEGRADED_MEDIA_REJECTED): _loop_media_rejected,
    (ledger.SOURCE_LOOP, loop.DEGRADED_HOOK_ERROR): _loop_hook_error,
    (ledger.SOURCE_LOOP, loop.DEGRADED_PROGRESS): _loop_progress,
    (ledger.SOURCE_LOOP, loop.DEGRADED_OBSERVER): _loop_observer,
    (ledger.SOURCE_LOOP, loop.DEGRADED_PRESENCE): _loop_presence,
    (ledger.SOURCE_LOOP, loop.DEGRADED_CONTINUITY): _loop_continuity,
    (ledger.SOURCE_LOOP, loop.DEGRADED_SPAWN_UNAVAILABLE): _loop_spawn_unavailable,
    (ledger.SOURCE_LOOP, loop.DEGRADED_SPAWN_FAILED): _loop_spawn_failed,
    (ledger.SOURCE_LOOP, loop.DEGRADED_SPAWN_DUPLICATE): _loop_spawn_duplicate,
    (ledger.SOURCE_LOOP, loop.DEGRADED_SYNTHESIS): _loop_synthesis,
    (ledger.SOURCE_MUSE, muse.DEGRADED_THINKING): _muse_thinking,
    (ledger.SOURCE_MUSE, muse.DEGRADED_BUNDLE_TRUNCATED): _muse_bundle_truncated,
    (ledger.SOURCE_MUSE, muse.DEGRADED_SINK): _muse_sink,
    (ledger.SOURCE_MUSE, muse.DEGRADED_UNREADABLE): _muse_unreadable,
    (ledger.SOURCE_MUSE, muse.DEGRADED_MARKER_UNREADABLE): _muse_marker_unreadable,
    (ledger.SOURCE_MUSE, muse.DEGRADED_TOOL): _muse_tool_failed,
    (ledger.SOURCE_MUSE, muse.DEGRADED_TOOL_ROUNDS): _muse_tool_rounds,
    (ledger.SOURCE_MUSE, muse.DEGRADED_TOOLS_WITHHELD): _muse_tools_withheld,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DEGRADED_THREAD): _runner_thread,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DEGRADED_WORKER): _runner_worker,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DEGRADED_ENDPOINT): _runner_endpoint,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DEGRADED_CLOSER): _runner_closer,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DROPPED_STALE): _runner_stale,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DROPPED_LATE): _runner_late,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DROPPED_OVERFLOW): _runner_overflow,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DROPPED_BOUNDARY): _runner_boundary,
    (
        ledger.SOURCE_MUSE_RUNNER,
        muse_runner.DROPPED_COMPILATION_STARVED,
    ): _runner_compilation_starved,
    (ledger.SOURCE_MUSE_RUNNER, muse_runner.DROPPED_COUNSEL_DISPLACED): _runner_counsel_displaced,
    (ledger.SOURCE_EVENTS, "events-cli-unavailable"): _events_unavailable,
    (ledger.SOURCE_EVENTS, "connect-failed"): _events_connect,
    (ledger.SOURCE_EVENTS, "publish-failed"): _events_publish,
    (ledger.SOURCE_EVENTS, "emit-failed"): _events_emit,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_IMPORT_FAILED): _continuity_import,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_NO_STORAGE_ANCHOR): _continuity_anchor,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_SUBSYSTEM_ERROR): _continuity_subsystem,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_MALFORMED_RESULT): _continuity_malformed,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_INVALID_RECORD): _continuity_invalid,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_DOMAIN_UNAVAILABLE): _continuity_unavailable,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_ARTIFACT_UNREADABLE): _continuity_artifact,
    (ledger.SOURCE_CONTINUITY, continuity.CODE_REINFORCE_FAILED): _continuity_reinforce,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_INTERNAL): _lifecycle_internal,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_SINK): _lifecycle_sink,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_TRACE_LOST): _lifecycle_trace,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_CONSEQUENTIAL): _lifecycle_consequential,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_LINKS_TRUNCATED): _lifecycle_links_truncated,
    (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_COMPILED_FROM_LOST): _lifecycle_compiled_from_lost,
    (ledger.SOURCE_LEDGER, ledger.DEGRADED_UNREADABLE_SOURCE): _ledger_unreadable,
}

KNOWN = {(entry.source, entry.code) for entry in ledger.known_codes()}


# ── the DELIVERY coverage map (task t5) ──────────────────────────────────────
#
# A second, separate table for a second, separate stream. The runner's delivery
# vocabulary is not a degradation vocabulary (see
# :class:`TestADeliveryIsNeverADegradation`), so it cannot ride ``PROVOKERS`` —
# but it is held to exactly the same producer rule, because dead vocabulary is
# dead vocabulary wherever it lives. Keyed by the delivery POINT, which is what
# ``muse_runner.DELIVERY_POINTS`` enumerates.

DELIVERY_PROVOKERS: dict[str, DeliveryProvoker] = {
    muse_runner.DELIVERY_TERMINAL: _terminal_delivery,
}


def _declared_delivery_points() -> set[str]:
    """The runner's own claim to completeness, read at call time.

    Read from the module rather than transcribed, for the reason ``KNOWN`` is:
    adding a point to :data:`embodiment.muse_runner.DELIVERY_POINTS` must make
    the coverage test go red with no edit to this line.
    """
    return set(muse_runner.DELIVERY_POINTS)


DELIVERY_KNOWN = _declared_delivery_points()


# ── the provoker contract, enforced over this file's OWN source ───────────────
#
# See :class:`TestNoProvokerTakesThePrivateDoor` for the rule and the reasoning.

_THIS_FILE = Path(__file__).resolve()

#: Every provoker table in this file. Both are held to the SAME rule: a
#: provoker drives its subject through the seam a host uses, or it covers
#: nothing. A new table that is not listed here would be an escape hatch, so
#: :class:`TestNoProvokerTakesThePrivateDoor`'s first test checks the resolved
#: count against the tables' own lengths.
_PROVOKER_TABLES = ("PROVOKERS", "DELIVERY_PROVOKERS")


def _provoker_definitions() -> dict[str, ast.FunctionDef]:
    """Resolve every provoker-table value to its ``def`` in this file's own source.

    Each table must name module-level functions by BARE NAME. A lambda, an
    attribute or a call would put the provoker's body somewhere this check
    cannot read, which is itself an escape hatch — so the shape is asserted
    here rather than assumed.
    """
    tree = ast.parse(_THIS_FILE.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    tables: dict[str, ast.Dict] = {}
    for node in tree.body:
        target = getattr(node, "target", None)
        name = getattr(target, "id", "")
        if isinstance(node, ast.AnnAssign) and name in _PROVOKER_TABLES:
            if isinstance(node.value, ast.Dict):
                tables[name] = node.value
    absent = [name for name in _PROVOKER_TABLES if name not in tables]
    assert not absent, f"no longer module-level annotated dict literals: {absent}"
    resolved: dict[str, ast.FunctionDef] = {}
    for table_name, table in tables.items():
        for value in table.values:
            assert isinstance(value, ast.Name), f"a provoker is not a plain name: {ast.dump(value)}"
            assert value.id in functions, f"{value.id} is not a module-level def in this file"
            assert value.id not in resolved, f"{value.id} appears twice in {table_name}"
            resolved[value.id] = functions[value.id]
    return resolved


def _private_doors(node: ast.AST) -> list[tuple[int, str]]:
    """Every private-attribute reach inside *node*, as ``(line, source)`` pairs."""
    found: list[tuple[int, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute) or not child.attr.startswith("_"):
            continue
        if child.attr.startswith("__") and child.attr.endswith("__"):
            continue  # a dunder is the public protocol surface, not a door
        base = child.value
        if isinstance(base, ast.Name):
            if base.id in {"self", "cls"}:
                continue  # a double the provoker itself defines owns its internals
            if isinstance(child.ctx, ast.Load) and isinstance(globals().get(base.id), ModuleType):
                continue  # reading a lane's own vocabulary constant off its module
        found.append((child.lineno, ast.unparse(child)))
    return found


_PROVOKER_DEFS = _provoker_definitions()


@pytest.fixture
def clean_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient eidetic store pin leaks into (or out of) a provoker."""
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.delenv("DR_DATA_DIR", raising=False)


# ── 1. THE enumeration: every code, every path, one host-visible record ───────


class TestEveryCodeIsCovered:
    """Exhaustive BY CONSTRUCTION — the expected set is derived, not written."""

    def test_no_code_lacks_a_covering_path(self) -> None:
        missing = sorted(KNOWN - set(PROVOKERS))
        assert not missing, (
            "these degradation codes have no covering path — add one to "
            f"PROVOKERS in this file: {missing}"
        )

    def test_no_path_names_a_code_no_module_mints(self) -> None:
        extra = sorted(set(PROVOKERS) - KNOWN)
        assert not extra, f"PROVOKERS names codes no lane exports (renamed? deleted?): {extra}"

    def test_the_derivation_reaches_every_lane(self) -> None:
        """A whole lane vanishing from the registry would make this test lie."""
        assert {entry.source for entry in ledger.known_codes()} == set(ledger.SOURCES)

    def test_the_runner_derivation_matches_its_own_declared_set(self) -> None:
        """``RUNNER_CODES`` is that module's own claim to completeness; pin to it."""
        derived = {e.code for e in ledger.known_codes() if e.source == ledger.SOURCE_MUSE_RUNNER}
        assert derived == set(muse_runner.RUNNER_CODES)

    def test_the_vocabularies_are_disjoint(self) -> None:
        """One code identifies its lane — which is what makes attribution safe."""
        entries = ledger.known_codes()
        assert len({e.code for e in entries}) == len(entries)

    def test_a_newly_added_code_appears_uncovered_without_being_written_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE property that makes this a test rather than a checklist.

        A lane gains a constant; nobody touches this file. The derivation finds
        it, the coverage set does not contain it, and
        ``test_no_code_lacks_a_covering_path`` goes red until someone writes the
        path that provokes it.
        """
        monkeypatch.setattr(loop, "DEGRADED_INVENTED", "invented-for-this-test", raising=False)
        monkeypatch.setattr(loop, "__all__", [*loop.__all__, "DEGRADED_INVENTED"])
        ledger._VOCABULARY_CACHE.pop(ledger.SOURCE_LOOP, None)
        try:
            found = {(e.source, e.code) for e in ledger.known_codes()}
            invented = (ledger.SOURCE_LOOP, "invented-for-this-test")
            assert invented in found, "the derivation missed a newly defined constant"
            assert sorted(found - set(PROVOKERS)) == [invented]
        finally:
            # The cache would otherwise hold the poisoned vocabulary after
            # monkeypatch restores the module.
            ledger._VOCABULARY_CACHE.pop(ledger.SOURCE_LOOP, None)

    @pytest.mark.parametrize("key", sorted(KNOWN))
    def test_the_path_appends_a_host_visible_record(
        self,
        key: tuple[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        clean_store_env: None,
    ) -> None:
        source, code = key
        records = PROVOKERS[key](tmp_path, monkeypatch)

        assert records, f"{source}/{code}: the path produced no ledger record at all"
        matched = [r for r in records if r.code == code]
        assert matched, f"{source}/{code}: not in {[r.code for r in records]}"
        record = matched[0]
        assert record.source == source
        assert record.reason, f"{source}/{code}: recorded with no reason"
        assert record.original is not None
        assert record.to_dict()["code"] == code


# ── 1b. …and every covering path is a REAL one ────────────────────────────────


class TestNoProvokerTakesThePrivateDoor:
    """The exhaustiveness check above is only worth what its provokers DO.

    :class:`TestEveryCodeIsCovered` proves every code is reachable *from this
    table*. That is a weaker claim than it looks. Two provokers satisfied it
    like this::

        with runner._lock:
            runner._record(muse_runner.DROPPED_COMPILATION_STARVED, "…")

    Recording a code directly proves a code can appear in a record. It proves
    nothing about any code path producing it — and both of those codes shipped
    in 0.8.0 with no producing path at all (embodiment#18), covered and green
    the whole time. An exhaustiveness check whose escape hatch is "record it
    yourself" can certify dead vocabulary indefinitely. This closes the hatch
    structurally, over this file's own AST.

    **The rule.** Inside a provoker, no underscore-prefixed attribute may be
    touched on an OBJECT — a local, a parameter, a call result, an attribute
    chain, in a read, a call or an assignment. A provoker drives the subject
    through the door its host uses, or it does not cover the code.

    Three exemptions, each narrow and each for a reason:

    * **dunders** (``__class__``, ``__str__``) — the public protocol surface,
      not a private door;
    * **``self`` / ``cls``** — no provoker is a method, so those names can only
      belong to a double the provoker itself defines, and a test's own stand-in
      owns its internals;
    * **reading** a private name off a MODULE (``lifecycle._FAULT_SINK``) — a
      lane's own degradation vocabulary is a constant, not a door. Reading
      only: a direct assignment to a module private outlives the test that
      made it, which is what ``monkeypatch`` exists to prevent.

    Bare ``_``-prefixed NAMES — this file's helpers (``_task``, ``_fake_embed``,
    ``_AssessStub``) — are :class:`ast.Name` nodes, never attributes, so the
    rule never touches them.

    **What this deliberately does not claim.**
    ``monkeypatch.setattr(module, "_private", …)`` names its target as a
    string and is invisible to any AST check. That is fault injection at a
    module seam — making eidetic unimportable, making a backend explode — and
    it is how several degradations are reachable at all. It is a different act
    from reaching through the subject's own back door, and this rule does not
    pretend to cover it.
    """

    def test_the_table_resolves_to_definitions_this_check_can_read(self) -> None:
        """A provoker this check cannot parse would be an escape hatch of its own."""
        assert len(_PROVOKER_DEFS) == len(PROVOKERS) + len(DELIVERY_PROVOKERS)

    @pytest.mark.parametrize("name", sorted(_PROVOKER_DEFS))
    def test_no_provoker_reaches_a_private_attribute(self, name: str) -> None:
        doors = _private_doors(_PROVOKER_DEFS[name])
        assert not doors, (
            f"{name} reaches past the public seam at "
            + ", ".join(f"line {line}: {source}" for line, source in doors)
            + " — a provoker must drive its code through the seam a host uses. "
            "Recording (or hand-calling) the path proves only that the code can "
            "appear in a record, never that any code path produces it; see "
            "embodiment#18 for what that costs."
        )

    def test_the_check_would_catch_the_shape_it_exists_to_ban(self) -> None:
        """The check's own red case, kept as a test instead of a memory.

        Demonstrating the guard red once during development proves it fires;
        keeping the demonstration proves it still does. The three exemptions are
        asserted here too, so narrowing them is a visible act.
        """
        offender = ast.parse(
            "def _p(tmp, mp):\n"
            "    runner = ThreadedMuseRunner(Scripted())\n"
            "    with runner._lock:\n"
            "        runner._record(muse_runner.DROPPED_STALE, 'x')\n"
        ).body[0]
        assert [source for _line, source in _private_doors(offender)] == [
            "runner._lock",
            "runner._record",
        ]

        allowed = ast.parse(
            "def _p(tmp, mp):\n"
            "    code = lifecycle._FAULT_SINK\n"
            "    kind = boundary.__class__\n"
            "    class _Double:\n"
            "        def go(self):\n"
            "            return self._own\n"
        ).body[0]
        assert _private_doors(allowed) == []


# ── 1c. the delivery stream: covered the same way, folded nowhere near here ───


class TestEveryDeliveryPointIsCovered:
    """The runner's delivery vocabulary gets #18's rule too (task t5).

    ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` shipped
    declared, exported, covered and green with no producing path anywhere
    (embodiment#18). Nothing about that failure was specific to *degradation*
    vocabulary — it was a declared constant nothing produced. So the delivery
    points get the same treatment from the start: derived expectations, a
    provoker per point, and the provoker held to the private-door rule by
    :class:`TestNoProvokerTakesThePrivateDoor` along with every other one.
    """

    def test_no_delivery_point_lacks_a_covering_path(self) -> None:
        missing = sorted(_declared_delivery_points() - set(DELIVERY_PROVOKERS))
        assert not missing, (
            "these delivery points have no covering path — add one to "
            f"DELIVERY_PROVOKERS in this file: {missing}"
        )

    def test_no_path_names_a_point_the_runner_does_not_declare(self) -> None:
        extra = sorted(set(DELIVERY_PROVOKERS) - _declared_delivery_points())
        assert not extra, f"DELIVERY_PROVOKERS names points the runner drops: {extra}"

    def test_a_newly_added_point_appears_uncovered_without_being_written_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property that makes this a test rather than a checklist."""
        monkeypatch.setattr(
            muse_runner,
            "DELIVERY_POINTS",
            (*muse_runner.DELIVERY_POINTS, "invented-for-this-test"),
        )
        missing = sorted(_declared_delivery_points() - set(DELIVERY_PROVOKERS))
        assert missing == ["invented-for-this-test"]

    @pytest.mark.parametrize("point", sorted(DELIVERY_KNOWN))
    def test_the_path_mints_a_record_carrying_a_count_and_the_delivered_ids(
        self, point: str
    ) -> None:
        records = DELIVERY_PROVOKERS[point]()

        assert records, f"{point}: the path produced no delivery record at all"
        matched = [r for r in records if r.point == point]
        assert matched, f"{point}: not in {[r.point for r in records]}"
        record = matched[0]
        assert record.count == len(record.insight_ids)
        assert record.count >= 1, "the gated provoker holds counsel back for this beat"
        assert set(record.to_dict()) == {"point", "count", "insight_ids", "step_index"}
        json.dumps(record.to_dict())


class TestADeliveryIsNeverADegradation:
    """Why the delivery record is not a ``DROPPED_*`` code, pinned structurally.

    This module's own rule for a budget exit is that folding it in "would make
    the stream claim breakage that did not happen". A terminal drain handing
    the actor three insights is this cycle **working**; a terminal drain
    handing it none is the muse having had nothing left, which is also not
    breakage. Minting a degradation code for either would make every healthy
    run report one, and a stream that cries wolf on success is worth less than
    no stream. The counsel that genuinely IS lost already has codes — stale,
    late, overflow, superseded — and those still fire.
    """

    def test_a_real_terminal_delivery_folds_to_no_ledger_record(self) -> None:
        seam = Scripted(_resp("GUIDANCE: noted " + MARKER_DONE))
        runner = ThreadedMuseRunner(seam)
        try:
            runner.consider(_muse_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert len(runner.drain_terminal(step_count=1)) == 1
            assert runner.deliveries  # it happened…
            assert ledger.from_muse_runner(runner) == []  # …and it was not breakage
            assert ledger.read(muse_runner=runner) == []
        finally:
            runner.close(timeout=_TIMEOUT)

    def test_no_delivery_point_is_a_known_code(self) -> None:
        codes = {entry.code for entry in ledger.known_codes()}
        assert not (_declared_delivery_points() & codes)

    def test_the_runners_vocabulary_registry_reads_only_degradation_prefixes(self) -> None:
        """``DELIVERY_*`` is outside the prefixes the registry harvests, on purpose."""
        _module, prefixes, _public = ledger._MODULES[ledger.SOURCE_MUSE_RUNNER]
        assert prefixes == ("DEGRADED_", "DROPPED_")
        assert not any("DELIVERY_".startswith(prefix) for prefix in prefixes)

    def test_the_runner_declares_the_two_streams_separately(self) -> None:
        assert set(muse_runner.RUNNER_CODES) & set(muse_runner.DELIVERY_POINTS) == set()


# ── 2. absent fields stay absent ──────────────────────────────────────────────


class TestNothingIsFabricated:
    """A field a source shape does not carry is ``None``, never ``0`` or ``""``."""

    def test_a_continuity_record_has_no_step_index(self) -> None:
        record = ledger.from_continuity(
            continuity.Degradation(
                subsystem="eidetic", stage="recall", code="subsystem-error", reason="down"
            )
        )[0]
        assert record.step_index is None
        assert record.model_turns is None

    def test_an_absent_field_is_omitted_from_to_dict_entirely(self) -> None:
        """Not ``null``, not ``0`` — the key is simply not there."""
        record = ledger.from_continuity(
            continuity.Degradation(
                subsystem="eidetic", stage="recall", code="subsystem-error", reason="down"
            )
        )[0]
        data = record.to_dict()
        assert "step_index" not in data
        assert "model_turns" not in data
        assert "boundary" not in data
        assert data["subsystem"] == "eidetic"
        assert data["stage"] == "recall"

    def test_an_events_record_carries_only_what_events_records(self) -> None:
        emitter = EventEmitter(client_factory=lambda: FakeClient(ok=False))
        emitter(_loop_event())
        data = ledger.from_events(emitter)[0].to_dict()
        assert set(data) == {"source", "code", "reason"}

    def test_a_genuine_zero_survives_as_a_zero(self) -> None:
        """The loop stamps a REAL step index; ``0`` there means step zero."""
        record = ledger.from_loop(LoopDegradation(code="hook-error", reason="x"))[0]
        assert record.step_index == 0
        assert record.model_turns == 0
        assert record.to_dict()["step_index"] == 0

    def test_an_exception_free_continuity_record_omits_the_exception(self) -> None:
        record = ledger.from_continuity(
            continuity.Degradation(
                subsystem="coherence", stage="assess", code="domain-unavailable", reason="partial"
            )
        )[0]
        assert record.exception is None
        assert "exception" not in record.to_dict()

    def test_an_instance_level_lifecycle_event_has_no_boundary(self) -> None:
        event = lifecycle.LifecycleEvent(
            boundary="", kind=CHECKPOINT_DEGRADED, detail="import-failed", data={"reason": "no"}
        )
        assert ledger.from_lifecycle([event])[0].boundary is None

    def test_one_bare_lifecycle_event_reads_the_same_as_a_sequence(self) -> None:
        event = lifecycle.LifecycleEvent(
            boundary="before-action",
            kind=CHECKPOINT_DEGRADED,
            detail=lifecycle._FAULT_SINK,
            data={"error": "RuntimeError: sink exploded"},
        )
        assert ledger.from_lifecycle(event) == ledger.from_lifecycle([event])

    def test_a_non_degraded_lifecycle_event_is_not_a_degradation(self) -> None:
        """The checkpoint ledger is the lived sequence; only faults fold here."""
        recalled = lifecycle.LifecycleEvent(
            boundary="before-action", kind=lifecycle.CHECKPOINT_RECALLED, data={"count": 2}
        )
        assert ledger.from_lifecycle([recalled]) == []

    def test_the_record_is_frozen(self) -> None:
        record = ledger.LedgerRecord(source="loop", code="x", reason="y")
        with pytest.raises(FrozenInstanceError):
            record.code = "z"  # type: ignore[misc]

    def test_the_shape_is_the_union_of_what_the_lanes_carry(self) -> None:
        names = {f.name for f in fields(ledger.LedgerRecord)}
        assert names == {
            "source",
            "code",
            "reason",
            "step_index",
            "model_turns",
            "boundary",
            "stage",
            "subsystem",
            "exception",
            "child_task_id",
            "original",
        }

    def test_to_dict_drops_original_so_the_fold_is_json_safe_unconditionally(self) -> None:
        """``original`` is a live foreign object; folding it would poison JSON.

        The field-set pin above passes whether or not ``to_dict`` emits
        ``original``, so on its own it does not protect a host that serialises
        the ledger. This does: the payload is a deliberately unserialisable
        object, and the assertion is that ``json.dumps`` still succeeds.
        """

        class Unserialisable:
            pass

        record = ledger.LedgerRecord(
            source=ledger.SOURCE_MUSE,
            code=muse.DEGRADED_THINKING,
            reason="a lane's own object rode along",
            original=Unserialisable(),
        )
        assert record.original is not None
        folded = record.to_dict()
        assert "original" not in folded
        # The point of the exclusion, stated as the assertion rather than as prose.
        assert json.loads(json.dumps(folded))["code"] == muse.DEGRADED_THINKING


# ── 3. attribution is looked up, never guessed ────────────────────────────────


class TestAttribution:
    def test_a_relayed_muse_code_is_attributed_to_the_muse(self) -> None:
        """The runner absorbs a session's codes verbatim; the fold un-mixes them."""
        runner = ThreadedMuseRunner(Scripted(ConnectionRefusedError("dead port")))
        try:
            runner.consider(_muse_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            records = ledger.from_muse_runner(runner)
        finally:
            runner.close(timeout=_TIMEOUT)
        by_code = {r.code: r.source for r in records}
        assert by_code[muse.DEGRADED_THINKING] == ledger.SOURCE_MUSE
        assert by_code[muse_runner.DEGRADED_ENDPOINT] == ledger.SOURCE_MUSE_RUNNER

    def test_a_relayed_continuity_code_is_attributed_to_continuity(self) -> None:
        event = lifecycle.LifecycleEvent(
            boundary="before-memory",
            kind=CHECKPOINT_DEGRADED,
            detail=continuity.CODE_SUBSYSTEM_ERROR,
            data={
                "subsystem": "eidetic",
                "stage": "remember",
                "code": continuity.CODE_SUBSYSTEM_ERROR,
                "reason": "store on fire",
                "exception": "RuntimeError",
            },
        )
        record = ledger.from_lifecycle([event])[0]
        assert record.source == ledger.SOURCE_CONTINUITY
        assert record.boundary == "before-memory"
        assert record.subsystem == "eidetic"
        assert record.exception == "RuntimeError"

    def test_an_unknown_code_falls_back_to_the_container_lane(self) -> None:
        """Honest fallback: the reader knows what it was handed, so it says so."""
        record = ledger.from_loop(LoopDegradation(code="something-new", reason="?"))[0]
        assert record.source == ledger.SOURCE_LOOP

    def test_source_for_code_reports_none_for_a_stranger(self) -> None:
        assert ledger.source_for_code("not-a-code") is None

    def test_source_for_code_can_be_narrowed(self) -> None:
        assert ledger.source_for_code(muse.DEGRADED_THINKING, within=(ledger.SOURCE_LOOP,)) is None
        assert ledger.source_for_code(muse.DEGRADED_THINKING) == ledger.SOURCE_MUSE

    def test_every_registry_entry_names_a_real_constant(self) -> None:
        import importlib

        for entry in ledger.known_codes():
            module_name = ledger._MODULES[entry.source][0]
            module = importlib.import_module(module_name)
            assert getattr(module, entry.constant) == entry.code

    def test_a_code_entry_serializes(self) -> None:
        entry = ledger.known_codes()[0]
        assert set(entry.to_dict()) == {"source", "code", "constant"}


# ── 4. the ledger never raises into the host ──────────────────────────────────


class TestNeverRaisesIntoTheHost:
    """An observability surface must not become a new failure source."""

    @pytest.mark.parametrize(
        "reader",
        [
            ledger.from_loop,
            ledger.from_muse,
            ledger.from_muse_runner,
            ledger.from_events,
            ledger.from_continuity,
            ledger.from_lifecycle,
        ],
    )
    @pytest.mark.parametrize("junk", [object(), 7, "a string", b"bytes", {"a": 1}])
    def test_junk_degrades_to_a_record_rather_than_an_exception(
        self, reader: Any, junk: Any
    ) -> None:
        records = reader(junk)
        assert all(isinstance(r, ledger.LedgerRecord) for r in records)
        assert all(r.code == ledger.DEGRADED_UNREADABLE_SOURCE for r in records)

    @pytest.mark.parametrize(
        "reader",
        [
            ledger.from_loop,
            ledger.from_muse,
            ledger.from_muse_runner,
            ledger.from_events,
            ledger.from_continuity,
            ledger.from_lifecycle,
        ],
    )
    def test_none_reads_as_an_empty_stream(self, reader: Any) -> None:
        assert reader(None) == []

    def test_a_hostile_record_is_named_and_the_rest_survive(self) -> None:
        class Landmine:
            @property
            def code(self) -> str:
                raise RuntimeError("code is a landmine")

        @dataclass
        class Holder:
            degradations: list[Any]

        good = LoopDegradation(code="hook-error", reason="fine")
        records = ledger.from_loop(Holder(degradations=[Landmine(), good]))
        assert [r.code for r in records] == [ledger.DEGRADED_UNREADABLE_SOURCE, "hook-error"]
        assert "RuntimeError" in records[0].reason

    def test_a_hostile_reason_is_rendered_rather_than_raised(self) -> None:
        class Unrenderable:
            def __str__(self) -> str:
                raise RuntimeError("nope")

        record = ledger.from_loop(
            LoopDegradation(code="hook-error", reason=Unrenderable())  # type: ignore[arg-type]
        )[0]
        assert "unrenderable" in record.reason

    def test_a_hostile_lifecycle_event_is_named(self) -> None:
        class Landmine:
            kind = CHECKPOINT_DEGRADED

            @property
            def detail(self) -> str:
                raise RuntimeError("detail is a landmine")

        records = ledger.from_lifecycle([Landmine()])
        assert [r.code for r in records] == [ledger.DEGRADED_UNREADABLE_SOURCE]

    def test_a_runaway_reason_is_capped(self) -> None:
        record = ledger.from_loop(LoopDegradation(code="hook-error", reason="x" * 5000))[0]
        assert len(record.reason) == 500


# ── 5. read(): one call, one stream ───────────────────────────────────────────


class TestRead:
    def test_lanes_are_grouped_in_declared_order(self) -> None:
        records = ledger.read(
            events=EventEmitter(client_factory=lambda: FakeClient(ok=False)),
            loop=LoopDegradation(code="hook-error", reason="a"),
            muse=MuseDegradation(code=muse.DEGRADED_SINK, reason="b"),
        )
        # The events emitter has not been called, so it holds nothing yet.
        assert [r.source for r in records] == [ledger.SOURCE_LOOP, ledger.SOURCE_MUSE]

    def test_nothing_handed_over_is_an_empty_stream(self) -> None:
        assert ledger.read() == []

    def test_a_sequence_per_lane_is_flattened(self) -> None:
        records = ledger.read(
            loop=[
                LoopDegradation(code="hook-error", reason="a"),
                LoopDegradation(code="observer-failed", reason="b"),
            ]
        )
        assert [r.code for r in records] == ["hook-error", "observer-failed"]

    def test_a_lane_left_out_is_never_touched(self) -> None:
        """Passing nothing for a lane must not import or read it."""
        assert ledger.read(loop=LoopDegradation(code="hook-error", reason="a")) != []

    def test_the_whole_stream_is_json_ready(self) -> None:
        import json

        records = ledger.read(
            loop=LoopDegradation(code="hook-error", reason="a"),
            continuity=continuity.Degradation(
                subsystem="eidetic", stage="recall", code="subsystem-error", reason="b"
            ),
        )
        payload = json.dumps([r.to_dict() for r in records])
        assert json.loads(payload)[1]["subsystem"] == "eidetic"

    def test_read_folds_a_real_drive_and_a_real_checkpoint_together(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_store_env: None
    ) -> None:
        """The whole point, end to end: two lanes, one question, one answer."""
        monkeypatch.setattr(continuity, "assess", _AssessStub())
        checkpoints = ContinuityLifecycle(_lifecycle_config(tmp_path, max_tracked_tasks=2))
        for index in range(5):
            checkpoints(
                _lifecycle_boundary(
                    "before-action", task=_task(id=f"t{index}"), tool="w", arguments={}
                )
            )

        def boom(*_args: Any) -> None:
            raise RuntimeError("sink down")

        outcome = _drive(_turn(_call("finish")), progress=boom)

        records = ledger.read(loop=outcome, lifecycle=checkpoints)
        codes = {(r.source, r.code) for r in records}
        assert (ledger.SOURCE_LOOP, loop.DEGRADED_PROGRESS) in codes
        assert (ledger.SOURCE_LIFECYCLE, lifecycle._FAULT_TRACE_LOST) in codes


# ── 6. degradation is not incompletion ────────────────────────────────────────


class TestABudgetExitIsNotADegradation:
    """A clean budget exit records NOTHING here — and that is the honest answer.

    The loop already reports it through ``exit_reason`` and
    ``TaskResult.incompletion``: an honest partial is a designed outcome, not
    something that broke. Folding it into a degradation stream would make the
    stream claim breakage that never happened — the same overclaim C3 exists to
    prevent, pointing the other way. The DEGRADED budget exit (the forced final
    synthesis turn failing) does record, and is covered by the enumeration.
    """

    def test_a_clean_budget_exit_folds_to_an_empty_stream(self) -> None:
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=1)
        assert outcome.exit_reason == loop.EXIT_BUDGET
        assert ledger.from_loop(outcome) == []

    def test_the_partial_is_still_reported_by_the_loop_itself(self) -> None:
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=1)
        assert outcome.result.not_finished is True

    def test_a_clean_finish_folds_to_an_empty_stream(self) -> None:
        outcome = _drive(_turn(_call("finish")))
        assert outcome.exit_reason == loop.EXIT_FINISHED
        assert ledger.read(loop=outcome) == []

    def test_a_degraded_budget_exit_does_record(self) -> None:
        records = _drive_synthesis_failure()
        assert [r.code for r in records if r.code == loop.DEGRADED_SYNTHESIS]


def _drive_synthesis_failure() -> list[ledger.LedgerRecord]:
    outcome = _drive(_turn(_call("read_file", path="a")), RuntimeError("dead"), max_steps=2)
    assert outcome.exit_reason == loop.EXIT_BUDGET
    return ledger.from_loop(outcome)


# ── 7. posture ────────────────────────────────────────────────────────────────


class TestPosture:
    """What the fold must not become."""

    def test_the_ledger_imports_no_colleague(self) -> None:
        source = Path(ledger.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "colleague" for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "colleague"

    def test_no_embodiment_submodule_is_imported_at_module_scope(self) -> None:
        """Keeps ``import embodiment.ledger`` cheap and cycle-free."""
        tree = ast.parse(Path(ledger.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.startswith("embodiment") for n in names), names

    def test_the_module_is_reachable_from_the_package(self) -> None:
        from embodiment import ledger as reached

        assert reached is ledger

    def test_every_advertised_name_resolves(self) -> None:
        for name in ledger.__all__:
            assert getattr(ledger, name) is not None
