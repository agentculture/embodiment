"""Tools-off byte-identity: the degrade floor and the rollback path (task t11).

Colleague#352's acceptance criterion is the model this task inherits: *absent
identity implies byte-identical prompts*. Task t10 grafted a tool seam onto the
muse's thinking loop (:class:`~embodiment.muse.MuseToolBench`,
``MuseLoop(..., tools=None, depth=0)``) and claims (claim c37) that with no
bench wired nothing changed: the tools-off call is still
``complete(list(messages))`` — one argument, no schema — and
:data:`~embodiment.muse.MUSE_AUTHORITY` is still the whole system message.
That claim is the *rollback path* for shipping tools default-on: if a
validation pass ever says tools hurt, turning them off has to reproduce the
pre-seam behaviour exactly, not approximately.

This file holds that claim to account from the outside, deliberately disjoint
from ``tests/test_muse.py`` (its own doubles, its own classes):

1. **Byte-identity of the whole wire payload.** A multi-turn tools-off
   session's recorded messages, compared against the same session replayed
   through the ACTUAL pre-seam module — ``git show ffaaf48:embodiment/
   muse.py``, loaded dynamically, never checked out — rather than against a
   hand-written expectation. A future edit to prompt assembly cannot pass by
   editing both sides of a hand-derived comparison; there is nothing here to
   edit but the one production module.
2. ``MUSE_AUTHORITY`` is present and FIRST on every path: tools-off,
   tools-wired, and depth-withheld.
3. A response carrying ``tool_calls`` changes nothing when no bench is
   wired — the identity angle on the floor, distinct from ``test_muse.py``'s
   ``TestToolsOff.test_tool_calls_are_ignored_when_no_bench_is_wired`` (which
   checks the insight never leaks the call; this checks the recorded WIRE
   PAYLOAD is bit-for-bit the same with or without one).
4. Depth-withheld is byte-identical to tools-off across a WHOLE session, and
   the withholding is recorded (``DEGRADED_TOOLS_WITHHELD``), never silent.
5. The arity distinction holds: the tools-off seam is called with exactly one
   argument, the tool seam with exactly two — proved with strict-arity
   doubles a wrong call shape would raise (and degrade) against, not merely
   counted after the fact.

Everything here is deterministic: no sleep, no network. The one subprocess
call (``git show``) reads local history, not a network fetch, and every test
that depends on it SKIPS cleanly — never errors — when the commit is not
reachable (e.g. a shallow clone with a truncated history).
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import ModelResponse, ToolCall
from embodiment.muse import (
    DEGRADED_TOOLS_WITHHELD,
    MARKER_DONE,
    MUSE_AUTHORITY,
    MUSE_EXIT_CONCLUDED,
    MUSE_TOOL_AUTHORITY,
    MuseControls,
    MuseLoop,
    MuseToolBench,
)
from embodiment.presence_engine import BOUNDARY_CADENCE_TICK, BoundaryContext

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The commit immediately before task t10 grafted the tool seam onto
#: ``embodiment/muse.py`` — the pre-seam release this task's acceptance
#: criterion is measured against. Read with ``git show`` only; nothing here
#: is ever checked out.
_PRE_SEAM_SHA = "ffaaf48"

#: A host-supplied schema, used only to prove tools reached (or did not reach)
#: the wire. Nothing in ``embodiment.muse`` ships one of its own.
_SCHEMA: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "peek",
            "description": "A thinking-only test tool.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
)


def _resp(content: str = "", *, prompt: int = 0, completion: int = 0, **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, prompt_tokens=prompt, completion_tokens=completion, **kw)


def _boundary(**kw: Any) -> BoundaryContext:
    fields_: dict[str, Any] = {"kind": BOUNDARY_CADENCE_TICK, "step_count": 3}
    fields_.update(kw)
    return BoundaryContext(**fields_)


def _never(*_args: Any, **_kwargs: Any) -> Any:
    """A double that must never be called. Failing loudly beats failing quietly."""
    raise AssertionError("this seam must never reach the wire on this path")


class RecordingSeam:
    """A tools-off completion double: scripted replies, every call recorded.

    A local double rather than an import from ``test_muse.py`` — this file's
    doubles stay disjoint from that suite's, exactly as its tests do.
    """

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies) or [_resp(MARKER_DONE)]
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


# ── the pre-seam release, read from git history and executed live ────────────


def _pre_seam_source() -> Optional[str]:
    """The pre-seam ``muse.py``, read straight from git history via ``git show``.

    Never raises: any failure (git missing, the commit unreachable, a shallow
    clone) yields ``None`` so the dependent tests skip rather than error.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{_PRE_SEAM_SHA}:embodiment/muse.py"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _load_pre_seam_module() -> Optional[types.ModuleType]:
    """Exec the pre-seam source into a throwaway module. ``None`` if unreachable.

    The pre-seam module imports ``embodiment.contract`` and
    ``embodiment.presence_engine`` at module scope. Both are untouched between
    the pre-seam commit and today (``git diff ffaaf48 HEAD --
    embodiment/contract.py embodiment/presence_engine.py`` is empty), so this
    runs the real historical ``muse.py`` against the real current
    dependencies — a replay, not a reimplementation.

    Registered in ``sys.modules`` under a private name BEFORE exec, not after:
    ``dataclasses`` resolves ``cls.__module__`` back through ``sys.modules``
    while processing every ``@dataclass`` in the source (``MuseOrigin``,
    ``MuseInsight``, ...), so an unregistered module fails there with an
    ``AttributeError`` that has nothing to do with the muse code being tested.
    """
    source = _pre_seam_source()
    if source is None:
        return None
    name = "embodiment._pre_seam_muse_t11"
    module = types.ModuleType(name)
    filename = f"git show {_PRE_SEAM_SHA}:embodiment/muse.py"
    module.__dict__["__file__"] = filename
    sys.modules[name] = module
    try:
        exec(compile(source, filename, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        return None
    return module


@pytest.fixture(scope="module")
def pre_seam() -> types.ModuleType:
    module = _load_pre_seam_module()
    if module is None:
        pytest.skip(
            f"pre-seam commit {_PRE_SEAM_SHA}:embodiment/muse.py is not reachable via "
            "`git show` in this checkout (e.g. a shallow clone)"
        )
    return module


# ── 1. byte-identity of the whole wire payload ────────────────────────────────


class TestWholeWirePayloadByteIdentity:
    """Every argument handed to ``complete``, compared — never re-derived."""

    def test_tools_off_is_identical_regardless_of_how_it_is_reached(self):
        """Untouched defaults vs. explicit ``tools=None``/``depth=`` — same wire.

        There is exactly one tools-off code path in the module; this pins that
        touching the new parameters without ever wiring a bench can never
        diverge from not touching them at all.
        """
        controls = MuseControls(max_turns=3, max_quiet_turns=2)
        boundary = _boundary(operator_input="what should the loop do next?")

        def _script() -> tuple[ModelResponse, ...]:
            return (
                _resp("first narration\nGUIDANCE[step]: watch the budget"),
                _resp(""),
                _resp("final narration " + MARKER_DONE),
            )

        untouched = RecordingSeam(*_script())
        explicit_default = RecordingSeam(*_script())
        explicit_other_depth = RecordingSeam(*_script())

        MuseLoop(untouched, controls=controls).think(boundary)
        MuseLoop(explicit_default, controls=controls, tools=None, depth=0).think(boundary)
        MuseLoop(explicit_other_depth, controls=controls, tools=None, depth=9).think(boundary)

        assert untouched.calls == explicit_default.calls
        assert untouched.calls == explicit_other_depth.calls

    def test_matches_the_pre_seam_release_across_a_multi_turn_session(self, pre_seam):
        """The strongest form: replayed against the actual historical module.

        A response carrying ``tool_calls`` is folded into the script too, so
        this also demonstrates property 3 (ignored identically) as a
        byproduct of proving the payload matches byte for byte.
        """
        controls = MuseControls(max_turns=3, max_quiet_turns=2)
        boundary = _boundary(operator_input="ship the muse tool seam", reason="operator-message")

        def _script() -> tuple[ModelResponse, ...]:
            return (
                _resp(
                    "first narration\nGUIDANCE[step]: watch the budget",
                    tool_calls=[ToolCall(id="ignored-1", name="peek", arguments={"a": 1})],
                ),
                _resp(""),
                _resp("final narration " + MARKER_DONE),
            )

        current_seam = RecordingSeam(*_script())
        historical_seam = RecordingSeam(*_script())

        current_outcome = MuseLoop(current_seam, controls=controls).think(boundary)
        historical_outcome = pre_seam.MuseLoop(historical_seam, controls=controls).think(boundary)

        assert current_seam.calls == historical_seam.calls
        assert current_outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert current_outcome.exit_reason == historical_outcome.exit_reason
        assert current_outcome.turns == historical_outcome.turns
        assert [i.text for i in current_outcome.insights] == [
            i.text for i in historical_outcome.insights
        ]
        assert [i.guidance for i in current_outcome.insights] == [
            i.guidance for i in historical_outcome.insights
        ]
        assert current_outcome.degradations == []
        assert historical_outcome.degradations == []

    def test_muse_authority_is_unchanged_from_the_pre_seam_release(self, pre_seam):
        """The one string every path depends on, pinned against real history.

        ``MUSE_AUTHORITY`` reaches the tools-off path too, so any edit to it
        would break byte-identity there. This is the strongest available form
        of "unchanged": the literal historical assignment, executed, not a
        hand-copied expectation of what it used to say.
        """
        assert pre_seam.MUSE_AUTHORITY == MUSE_AUTHORITY


# ── 2. MUSE_AUTHORITY first, on every path ────────────────────────────────────


class TestAuthorityIsFirstOnEveryPath:
    """``MUSE_AUTHORITY`` is present and FIRST: tools-off, tools-wired, withheld."""

    def test_tools_off(self):
        seam = RecordingSeam(_resp(MARKER_DONE))
        MuseLoop(seam).think(_boundary())
        system = seam.calls[0][0]
        assert system["role"] == "system"
        assert system["content"] == MUSE_AUTHORITY

    def test_tools_wired(self):
        seen: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

        def tool_complete(messages, tools):
            seen.append((messages, tools))
            return _resp(MARKER_DONE)

        bench = MuseToolBench(schema=_SCHEMA, complete=tool_complete, execute=_never)
        off = RecordingSeam(_resp(MARKER_DONE))
        MuseLoop(off, tools=bench).think(_boundary())

        system = seen[0][0][0]["content"]
        assert system.startswith(MUSE_AUTHORITY)
        assert system.index(MUSE_AUTHORITY) == 0
        assert MUSE_TOOL_AUTHORITY in system

    def test_depth_withheld(self):
        bench = MuseToolBench(schema=_SCHEMA, complete=_never, execute=_never)
        off = RecordingSeam(_resp(MARKER_DONE))
        MuseLoop(off, tools=bench, depth=1).think(_boundary())

        system = off.calls[0][0]
        assert system["role"] == "system"
        assert system["content"] == MUSE_AUTHORITY


# ── 3. tool_calls ignored identically — the identity angle ───────────────────


class TestToolCallsIgnoredIdentically:
    """Property 3, the identity angle: ``tool_calls`` changes NOTHING recorded.

    ``test_muse.py``'s ``TestToolsOff.test_tool_calls_are_ignored_when_no_
    bench_is_wired`` proves the insight text/guidance never leak a requested
    call. This proves something disjoint from it: the recorded WIRE PAYLOAD
    across a whole session is bit-for-bit the same whether or not the
    response object carries ``tool_calls`` at all.
    """

    def test_the_recorded_session_is_identical_with_and_without_tool_calls(self):
        controls = MuseControls(max_turns=2, max_quiet_turns=2)
        boundary = _boundary()
        call = [ToolCall(id="1", name="write_file", arguments={"path": "/etc/passwd"})]

        with_calls = RecordingSeam(
            _resp("I would like to write a file", tool_calls=call),
            _resp("final " + MARKER_DONE),
        )
        without_calls = RecordingSeam(
            _resp("I would like to write a file"),
            _resp("final " + MARKER_DONE),
        )

        MuseLoop(with_calls, controls=controls).think(boundary)
        MuseLoop(without_calls, controls=controls).think(boundary)

        assert with_calls.calls == without_calls.calls


# ── 4. depth-withheld == tools-off, recorded rather than silent ──────────────


class TestDepthWithheldMatchesToolsOffAcrossTheWholeSession:
    """A bench withheld for depth must cost nothing on the wire — and say so."""

    def test_byte_identical_across_every_turn_and_recorded_not_silent(self):
        controls = MuseControls(max_turns=3, max_quiet_turns=2)
        boundary = _boundary(operator_input="what next?")

        def _script() -> tuple[ModelResponse, ...]:
            return (
                _resp("first narration"),
                _resp(""),
                _resp("final narration " + MARKER_DONE),
            )

        floor = RecordingSeam(*_script())
        withheld_off = RecordingSeam(*_script())
        bench = MuseToolBench(schema=_SCHEMA, complete=_never, execute=_never)

        MuseLoop(floor, controls=controls).think(boundary)
        outcome = MuseLoop(withheld_off, tools=bench, depth=4, controls=controls).think(boundary)

        assert floor.calls == withheld_off.calls
        codes = [d.code for d in outcome.degradations]
        assert DEGRADED_TOOLS_WITHHELD in codes, codes


# ── 5. the arity distinction ──────────────────────────────────────────────────


class TestArityDistinctionHolds:
    """Tools-off calls with exactly one argument; the tool seam with exactly two.

    Each double accepts ONLY the arity it should be handed. A regression that
    called the wrong shape would raise ``TypeError`` inside the loop's own
    guarded call — which the loop swallows into a degradation — so a CLEAN,
    CONCLUDED outcome is itself the proof the call shape was exactly right,
    not an incidental detail this test happens to also check.
    """

    def test_the_tools_off_seam_is_called_with_exactly_one_argument(self):
        seen: list[int] = []

        def strict_complete(messages):
            seen.append(len(messages))
            return _resp(MARKER_DONE)

        outcome = MuseLoop(strict_complete).think(_boundary())

        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.degradations == []
        assert seen == [2]

    def test_the_tool_seam_is_called_with_exactly_two_arguments(self):
        seen: list[tuple[int, int]] = []

        def strict_tool_complete(messages, tools):
            seen.append((len(messages), len(tools)))
            return _resp(MARKER_DONE)

        bench = MuseToolBench(schema=_SCHEMA, complete=strict_tool_complete, execute=_never)
        off = RecordingSeam(_resp("unused " + MARKER_DONE))
        outcome = MuseLoop(off, tools=bench).think(_boundary())

        assert outcome.exit_reason == MUSE_EXIT_CONCLUDED
        assert outcome.degradations == []
        assert seen == [(2, len(_SCHEMA))]
        assert (
            off.turns == 0
        ), "the tools-off floor must not be called once a bench reaches the wire"
