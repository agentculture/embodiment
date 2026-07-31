"""The muse's pad: reused from the actor's, wired top-level only, never recalled.

Task t12 wires :mod:`embodiment.scratchpad` — the actor loop's working memory —
as the muse's first thinking tool. Four things have to be true and none of them
is self-evident from reading the module, so all four are pinned here:

1. **The protocol is reused, not forked.** ``KINDS`` and the tool schemas are
   :mod:`embodiment.scratchpad`'s own objects, asserted by *identity*. The one
   divergence — ``finish`` is not offered — is declared in
   :data:`~embodiment.muse_pad.OMITTED_TOOLS` and pinned by name, so a second
   silent protocol cannot appear.
2. **Tools are top-level only.** A bench handed to a subagent-depth muse is
   withheld: the pad receives nothing, the schema never reaches the wire, and
   the withholding is recorded.
3. **No recall surface includes pad entries.** Held structurally (no memory
   module reaches the pad and the pad reaches no memory module), by construction
   (a pad pointed inside a memory store is refused), and by observation (a full
   session writes exactly one file and the store stays empty).
4. **Protocol adherence is measurable.** The counters t18 needs — one per kind,
   plus the open-intent count — read off a finished session. The measured
   failure (five intents, zero observations) is reproduced here as a fixture so
   the metric is known to detect it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import ledger, muse_pad, scratchpad
from embodiment.contract import ModelResponse, ToolCall
from embodiment.muse import (
    DEGRADED_TOOL,
    DEGRADED_TOOLS_WITHHELD,
    MARKER_DONE,
    MuseControls,
    MuseLoop,
    MuseToolBench,
)
from embodiment.muse_pad import (
    MUSE_PAD_BANNER,
    MUSE_PAD_FILENAME,
    MUSE_PAD_LANE,
    MUSE_PAD_PROTOCOL,
    MUSE_PAD_TOOL_NAMES,
    MUSE_PAD_TOOLS,
    OMITTED_TOOLS,
    MusePad,
    MusePadCounts,
    MusePadRefused,
)
from embodiment.presence_engine import BoundaryContext
from embodiment.scratchpad import KINDS, SCRATCHPAD_TOOLS

PACKAGE_ROOT = Path(muse_pad.__file__).resolve().parent


# ── doubles ───────────────────────────────────────────────────────────────────


def _resp(content: str = "", *calls: ToolCall) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


def _call(name: str, call_id: str = "c1", **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=dict(arguments))


def _boundary(*, step: int = 0) -> BoundaryContext:
    return BoundaryContext(kind="cadence-tick", step_count=step, reason="every-n")


class Scripted:
    """The tools-off seam: replays fixed turns, records what it was sent."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        item = self.responses[index] if self.responses else _resp(MARKER_DONE)
        if isinstance(item, BaseException):
            raise item
        return item


class ScriptedTools:
    """The tool-carrying seam: records the schema it was handed, per turn."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.schemas: list[list[dict[str, Any]]] = []

    def __call__(
        self, messages: list[dict[str, Any]], schema: list[dict[str, Any]]
    ) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        self.schemas.append([dict(tool) for tool in schema])
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        item = self.responses[index] if self.responses else _resp(MARKER_DONE)
        if isinstance(item, BaseException):
            raise item
        return item


def _drive(
    pad: MusePad,
    *replies: Any,
    depth: Any = 0,
    controls: Optional[MuseControls] = None,
) -> tuple[Any, ScriptedTools, Scripted]:
    """One thinking session with *pad* wired. Returns (outcome, tool seam, floor seam)."""
    tools = ScriptedTools(*replies)
    floor = Scripted(_resp(MARKER_DONE))
    loop = MuseLoop(
        floor,
        controls=controls if controls is not None else MuseControls(max_turns=8, max_tool_rounds=8),
        tools=pad.bench(tools),
        depth=depth,
    )
    return loop.think(_boundary()), tools, floor


# ── 1. the protocol is REUSED, and the one divergence is declared ─────────────


class TestTheActorPadProtocolIsReusedUnchanged:
    """The acceptance criterion's first half, asserted by identity, not equality.

    Equality would pass on a hand-copied schema that happened to match today and
    would drift the moment ``scratchpad.py`` moved. Identity cannot: these are
    the very dicts :data:`~embodiment.scratchpad.SCRATCHPAD_TOOLS` holds.
    """

    def test_every_offered_tool_is_the_scratchpads_own_object(self) -> None:
        for tool in MUSE_PAD_TOOLS:
            assert any(tool is original for original in SCRATCHPAD_TOOLS), tool

    def test_the_offered_tools_keep_the_scratchpads_own_order(self) -> None:
        expected = [t for t in SCRATCHPAD_TOOLS if t["function"]["name"] not in OMITTED_TOOLS]
        assert list(MUSE_PAD_TOOLS) == expected

    def test_the_only_divergence_is_the_declared_omission(self) -> None:
        offered = set(MUSE_PAD_TOOL_NAMES)
        actor = {tool["function"]["name"] for tool in SCRATCHPAD_TOOLS}
        assert actor - offered == set(OMITTED_TOOLS)
        assert offered - actor == set()

    def test_finish_is_the_omission_and_it_is_named(self) -> None:
        assert OMITTED_TOOLS == ("finish",)

    def test_every_kind_the_actor_pad_has_is_still_offered(self) -> None:
        """The four KINDS are the protocol; none of them may be dropped."""
        assert set(KINDS) <= set(MUSE_PAD_TOOL_NAMES)

    def test_the_written_protocol_reuses_the_scratchpads_verbatim(self) -> None:
        assert MUSE_PAD_PROTOCOL.startswith(scratchpad.PROTOCOL)

    def test_the_muse_addendum_says_the_pad_is_private_and_unremembered(self) -> None:
        addendum = MUSE_PAD_PROTOCOL[len(scratchpad.PROTOCOL) :]
        assert "remember" in addendum.lower()
        assert "finish" in addendum.lower()


class TestFinishIsRefusedRatherThanQuietlyHonoured:
    """The divergence has to bite, or it is only a docstring.

    ``Scratchpad.execute`` still *has* a ``finish`` arm — it is the actor's
    submit verb. Delegating a hallucinated ``finish`` to it would stamp an
    ``answer`` on the muse's pad that nothing reads, and would persist it. So the
    muse pad refuses the name at its own boundary instead.
    """

    def test_a_finish_call_raises_rather_than_recording_an_answer(self, tmp_path: Path) -> None:
        pad = MusePad.in_directory(tmp_path)
        with pytest.raises(Exception) as excinfo:
            pad.execute("finish", {"answer": "76"})
        assert "finish" in str(excinfo.value)
        assert pad.pad.answer is None

    def test_a_refused_finish_is_counted_as_off_protocol(self, tmp_path: Path) -> None:
        pad = MusePad.in_directory(tmp_path)
        with pytest.raises(Exception):
            pad.execute("finish", {"answer": "76"})
        assert pad.counts().off_protocol_calls == 1

    def test_a_finish_call_through_the_real_seam_degrades_and_keeps_thinking(self) -> None:
        pad = MusePad()
        outcome, _tools, _floor = _drive(
            pad,
            _resp("submitting", _call("finish", answer="76")),
            _resp("GUIDANCE: think again " + MARKER_DONE),
        )
        assert [d.code for d in outcome.degradations] == [DEGRADED_TOOL]
        assert pad.pad.answer is None
        assert outcome.insights, "a refused tool never ends the session"


# ── 2. top-level only ─────────────────────────────────────────────────────────


class TestASubagentDepthMuseReceivesNoTools:
    """The acceptance criterion's second half.

    ``_bench_for`` is the gate and t10 pinned it; what is pinned *here* is the
    composition — that a real pad wired to a real muse at depth is untouched.
    """

    @pytest.mark.parametrize("depth", [1, 2, 7, "1", None, object(), float("nan")])
    def test_the_pad_is_never_written_below_the_top(self, depth: Any) -> None:
        pad = MusePad()
        outcome, tools, floor = _drive(
            pad,
            _resp("writing", _call("intend", text="split by parity")),
            depth=depth,
        )
        assert pad.counts().entries == 0, "a subagent-depth muse reached the pad"
        assert tools.schemas == [], "the pad schema reached the wire below the top"
        assert tools.calls == [], "the tool-carrying seam was called below the top"
        assert floor.calls, "the tools-off floor is what a withheld session runs on"
        assert DEGRADED_TOOLS_WITHHELD in {d.code for d in outcome.degradations}

    def test_the_withholding_is_visible_in_the_one_degradation_stream(self) -> None:
        pad = MusePad()
        outcome, _tools, _floor = _drive(pad, _resp(MARKER_DONE), depth=1)
        codes = {record.code for record in ledger.from_muse(outcome)}
        assert DEGRADED_TOOLS_WITHHELD in codes

    def test_the_top_level_muse_does_get_the_pad(self) -> None:
        pad = MusePad()
        outcome, tools, _floor = _drive(
            pad,
            _resp("writing", _call("intend", text="split by parity")),
            _resp(MARKER_DONE),
            depth=0,
        )
        assert pad.counts().entries == 1
        assert tools.schemas, "the top-level muse gets the schema on the wire"
        assert [t["function"]["name"] for t in tools.schemas[0]] == list(MUSE_PAD_TOOL_NAMES)
        assert not outcome.degradations

    def test_a_depth_that_cannot_be_read_fails_closed(self) -> None:
        """An unestablished position is not provably the top one."""

        class Hostile:
            def __int__(self) -> int:
                raise RuntimeError("no")

        pad = MusePad()
        outcome, tools, _floor = _drive(pad, _resp(MARKER_DONE), depth=Hostile())
        assert tools.schemas == []
        assert DEGRADED_TOOLS_WITHHELD in {d.code for d in outcome.degradations}


# ── 3. no recall surface includes pad entries (claim c10) ─────────────────────


def _imports_of(path: Path) -> set[str]:
    """Every module imported anywhere in *path* — module scope or inside a def."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


#: The modules that put material in front of a mind, or put material in a store.
_RECALL_SURFACES = ("recall_bundle", "continuity", "lifecycle")

#: What a pad must never reach. ``eidetic``/``coherence`` are the sibling
#: packages behind :mod:`embodiment.continuity`.
_MEMORY_MODULES = (
    "embodiment.continuity",
    "embodiment.recall_bundle",
    "embodiment.lifecycle",
    "eidetic",
    "eidetic_cli",
    "coherence",
    "coherence_cli",
    "data_refinery",
)


class TestThePadIsStructurallyOutOfEveryRecallSurface:
    """c10 is a boundary, so it is held by what the code *cannot* do.

    A hostile record arriving wearing remembered authority beat the cortex 6/6
    (memory-echo-chamber.md, n=6 per arm). Until the echo probe re-runs clean,
    nothing the muse writes on its pad may come back through a recall path — and
    the cheapest proof of that is that no such path is even in scope.
    """

    @pytest.mark.parametrize("surface", _RECALL_SURFACES)
    def test_no_recall_surface_reaches_the_pad(self, surface: str) -> None:
        imported = _imports_of(PACKAGE_ROOT / f"{surface}.py")
        assert "embodiment.muse_pad" not in imported
        assert "embodiment.scratchpad" not in imported

    def test_the_pad_module_reaches_no_memory_surface(self) -> None:
        imported = _imports_of(PACKAGE_ROOT / "muse_pad.py")
        for forbidden in _MEMORY_MODULES:
            assert forbidden not in imported, forbidden
            assert not any(name.startswith(forbidden + ".") for name in imported), forbidden

    def test_the_pad_module_exposes_no_memory_verb(self) -> None:
        public = {name for name in vars(muse_pad) if not name.startswith("_")}
        for verb in ("remember", "recall", "fetch_bundle", "RecallBundle", "assess"):
            assert verb not in public, verb

    def test_the_pad_object_exposes_no_memory_verb(self) -> None:
        surface = {name for name in dir(MusePad) if not name.startswith("_")}
        assert not (surface & {"remember", "recall", "store", "assess", "consolidate"})


class TestAPadPointedAtAMemoryStoreIsRefused:
    """Construction is a host-side act, so it fails LOUDLY rather than degrading.

    The never-raise rule governs the muse's thinking, not a host wiring itself
    up wrong. A pad quietly written into a committed store is the one failure
    this boundary exists to prevent, so it is refused where the mistake is made.
    """

    @pytest.mark.parametrize(
        "relative",
        [
            ".eidetic/memory/pad.jsonl",
            ".eidetic/pad.jsonl",
            ".coherence/pad.jsonl",
            "nested/.eidetic/memory/deep/pad.jsonl",
        ],
    )
    def test_a_store_path_is_refused(self, tmp_path: Path, relative: str) -> None:
        with pytest.raises(MusePadRefused):
            MusePad(tmp_path / relative)

    def test_the_refusal_names_the_store_directory(self, tmp_path: Path) -> None:
        with pytest.raises(MusePadRefused, match=r"\.eidetic"):
            MusePad(tmp_path / ".eidetic" / "memory" / "pad.jsonl")

    def test_a_directory_inside_a_store_is_refused_too(self, tmp_path: Path) -> None:
        with pytest.raises(MusePadRefused):
            MusePad.in_directory(tmp_path / ".eidetic" / "memory")

    def test_an_ordinary_path_is_not_refused(self, tmp_path: Path) -> None:
        pad = MusePad.in_directory(tmp_path / "pads")
        assert pad.path == tmp_path / "pads" / MUSE_PAD_FILENAME


class TestAWholeSessionTouchesNothingButItsOwnPad:
    """The observed half of c10: drive it and look at the disk.

    Structure says the memory modules are not in scope; this says a real session
    with a real pad wrote one file and no other, with an eidetic-shaped store
    sitting right beside it.
    """

    @staticmethod
    def _tree(root: Path) -> set[Path]:
        return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}

    def test_only_the_pad_file_is_written(self, tmp_path: Path) -> None:
        store = tmp_path / "store" / "memory"
        store.mkdir(parents=True)
        pad = MusePad.in_directory(tmp_path / "pads")
        _drive(
            pad,
            _resp("planning", _call("intend", text="check n=5 against the recurrence")),
            _resp("looking", _call("observe", call_id="c2", text="n=5 gives 3 even of 5")),
            _resp("GUIDANCE: the recurrence holds " + MARKER_DONE),
        )
        assert self._tree(tmp_path) == {Path("pads") / MUSE_PAD_FILENAME}

    def test_a_store_beside_the_pad_stays_empty(self, tmp_path: Path) -> None:
        store = tmp_path / "store" / "memory"
        store.mkdir(parents=True)
        pad = MusePad.in_directory(tmp_path / "pads")
        _drive(
            pad, _resp("planning", _call("intend", text="secret-pad-marker")), _resp(MARKER_DONE)
        )
        assert list(store.rglob("*")) == []

    def test_no_recall_fetch_can_return_pad_material(self, tmp_path: Path) -> None:
        """The muse's own recall surface, driven, after a pad session.

        ``fetch_bundle`` returns exactly what the injected store seam hands it.
        The pad never reaches a store, so the marker cannot be in the bundle —
        and the store seam is never called during the session either.
        """
        from embodiment.recall_bundle import BundleRequest, fetch_bundle, flat_fetcher

        marker = "secret-pad-marker-8f21"
        seen: list[str] = []

        class _StoreOutcome:
            ok = True
            degradation = None
            records = ({"id": "r1", "text": "an ordinary stored record"},)

        def recall_fn(query: str, **_kw: Any) -> Any:
            seen.append(query)
            return _StoreOutcome()

        pad = MusePad.in_directory(tmp_path / "pads")
        _drive(
            pad,
            _resp("planning", _call("intend", text=marker)),
            _resp(MARKER_DONE),
        )
        assert seen == [], "the pad session reached the store"
        assert marker in pad.render(), "the marker really is on the pad"

        bundle = fetch_bundle(
            BundleRequest(queries="what did we decide?", data_dir=tmp_path / "store"),
            fetch=flat_fetcher(recall_fn),
        )
        assert seen == ["what did we decide?"]
        assert bundle.items, "the bundle really did fetch something"
        assert all(marker not in item.text for item in bundle.items)


# ── 4. protocol adherence is measurable (t18's dependent variables) ───────────


class TestTheCountersReadOffTheKindsUnchanged:
    def test_every_kind_has_a_counter_and_a_zero_is_reported(self) -> None:
        counts = MusePad().counts()
        assert list(counts.kinds) == list(KINDS)
        assert set(counts.kinds.values()) == {0}

    def test_the_counter_dict_is_keyed_by_the_scratchpads_own_kinds(self) -> None:
        """Derived from ``KINDS``, so a fifth kind there arrives here for free."""
        assert tuple(MusePad().counts().kinds) == KINDS

    def test_the_lane_is_labelled(self) -> None:
        assert MusePad().counts().lane == MUSE_PAD_LANE == "muse"

    def test_counts_are_json_safe(self) -> None:
        payload = MusePad().counts().to_dict()
        assert json.loads(json.dumps(payload)) == payload


class TestTheMeasuredFailureIsDetectable:
    """Handed the pad, the muse wrote five intents, zero observations.

    That is the exact shape the pad exists to prevent — an open intent means "I
    was interrupted here", and five stacked ones make the record lie about where
    the mind got to. Fixing it is t17/t18's measurement, not this task's prompt
    engineering. Making it *visible* is this task's job, so the failure is
    reproduced here and the metric is asserted to name it.
    """

    def test_five_intents_and_no_observation_report_five_open_intents(self) -> None:
        pad = MusePad()
        _drive(
            pad,
            *[
                _resp(f"turn {n}", _call("intend", call_id=f"c{n}", text=f"step {n}"))
                for n in range(1, 6)
            ],
            _resp(MARKER_DONE),
            controls=MuseControls(max_turns=12, max_tool_rounds=12),
        )
        counts = pad.counts()
        assert counts.kinds["intend"] == 5
        assert counts.kinds["observe"] == 0
        assert counts.kinds["conclude"] == 0
        assert counts.open_intents == 5

    def test_an_intent_answered_by_an_observation_is_not_open(self) -> None:
        pad = MusePad()
        pad.execute("intend", {"text": "split by parity"})
        pad.execute("observe", {"text": "n=3 gives 3 even of 5"})
        assert pad.counts().open_intents == 0

    def test_an_observation_closes_every_intent_before_it(self) -> None:
        """The generalisation of ``Scratchpad.open_intent``, which scans back to
        the first ``observe`` and stops there."""
        pad = MusePad()
        pad.execute("intend", {"text": "one"})
        pad.execute("intend", {"text": "two"})
        pad.execute("observe", {"text": "both done"})
        pad.execute("intend", {"text": "three"})
        assert pad.counts().open_intents == 1

    @pytest.mark.parametrize(
        "sequence",
        [
            ("intend",),
            ("intend", "intend"),
            ("intend", "observe"),
            ("intend", "observe", "intend"),
            ("conclude",),
            ("intend", "conclude"),
        ],
    )
    def test_the_count_agrees_with_the_scratchpads_own_open_intent(
        self, sequence: tuple[str, ...]
    ) -> None:
        pad = MusePad()
        for kind in sequence:
            pad.execute(kind, {"text": f"a {kind}"})
        assert (pad.counts().open_intents > 0) is (pad.pad.open_intent is not None)

    def test_the_sequence_is_reported_in_order(self) -> None:
        pad = MusePad()
        for kind in ("intend", "observe", "conclude"):
            pad.execute(kind, {"text": f"a {kind}"})
        assert pad.counts().sequence == ("intend", "observe", "conclude")

    def test_a_rejected_empty_entry_is_counted_not_recorded(self) -> None:
        pad = MusePad()
        pad.execute("intend", {"text": "   "})
        counts = pad.counts()
        assert counts.entries == 0
        assert counts.rejected_calls == 1

    def test_a_revision_is_counted_under_its_own_kind(self) -> None:
        pad = MusePad()
        pad.execute("intend", {"text": "split by parity"})
        pad.execute("revise", {"id": "n1", "text": "split by residue instead"})
        counts = pad.counts()
        assert counts.kinds["revise"] == 1
        assert counts.kinds["intend"] == 1

    def test_counts_survive_the_whole_session_and_read_at_its_exit(self) -> None:
        """t18 reads the counters *after* ``think`` returns — the session seam."""
        pad = MusePad()
        outcome, _tools, _floor = _drive(
            pad,
            _resp("a", _call("intend", text="try the recurrence")),
            _resp("b", _call("observe", call_id="c2", text="it held at n=3")),
            _resp("GUIDANCE: keep going " + MARKER_DONE),
        )
        assert outcome.tool_rounds == 2
        assert pad.counts().to_dict() == {
            "lane": "muse",
            "entries": 2,
            "kinds": {"intend": 1, "observe": 1, "conclude": 0, "revise": 0},
            "open_intents": 0,
            "rejected_calls": 0,
            "off_protocol_calls": 0,
            "sequence": ["intend", "observe"],
            "persisted": False,
        }


# ── 5. the pad is a lane of its own, separately labelled ─────────────────────


class TestTheMusePadIsSeparateFromTheActors:
    def test_the_filename_is_its_own(self) -> None:
        assert MUSE_PAD_FILENAME == "muse-pad.jsonl"

    def test_two_lanes_sharing_a_directory_do_not_share_a_file(self, tmp_path: Path) -> None:
        actor = scratchpad.Scratchpad(path=tmp_path / "pad.jsonl")
        muse = MusePad.in_directory(tmp_path)
        actor.execute("intend", {"text": "the actor's intent"})
        muse.execute("intend", {"text": "the muse's intent"})
        assert muse.path != actor.path
        assert "the actor's intent" not in muse.path.read_text(encoding="utf-8")
        assert "the muse's intent" not in actor.path.read_text(encoding="utf-8")

    def test_the_rendered_pad_is_labelled_as_the_muses(self) -> None:
        pad = MusePad()
        pad.execute("intend", {"text": "split by parity"})
        rendered = pad.render()
        assert rendered.startswith(MUSE_PAD_BANNER)
        assert "split by parity" in rendered

    def test_an_empty_pad_still_renders_its_label(self) -> None:
        assert MusePad().render().startswith(MUSE_PAD_BANNER)

    def test_a_pad_with_no_path_writes_nothing(self, tmp_path: Path) -> None:
        pad = MusePad()
        pad.execute("intend", {"text": "in memory only"})
        assert pad.path is None
        assert pad.counts().persisted is False
        assert list(tmp_path.rglob("*")) == []

    def test_a_persisted_pad_reopens_where_it_left_off(self, tmp_path: Path) -> None:
        """Working memory that dies with its process protects against nothing."""
        first = MusePad.in_directory(tmp_path)
        first.execute("intend", {"text": "check n=5 before trusting it"})
        second = MusePad.in_directory(tmp_path)
        assert second.counts().entries == 1
        assert second.counts().open_intents == 1
        assert "check n=5" in second.render()


# ── 6. the bench, and what it is allowed to be ───────────────────────────────


class TestTheBench:
    def test_the_bench_carries_the_pad_schema_and_the_pads_executor(self) -> None:
        pad = MusePad()

        def complete(_m: list[dict[str, Any]], _s: list[dict[str, Any]]) -> ModelResponse:
            return _resp(MARKER_DONE)

        bench = pad.bench(complete)
        assert isinstance(bench, MuseToolBench)
        assert bench.schema == MUSE_PAD_TOOLS
        assert bench.complete is complete
        assert bench.execute == pad.execute

    def test_a_pad_result_reaches_the_muse_as_readable_text(self) -> None:
        pad = MusePad()
        _outcome, tools, _floor = _drive(
            pad,
            _resp("writing", _call("intend", text="split by parity")),
            _resp(MARKER_DONE),
        )
        tool_messages = [m for m in tools.calls[-1] if m.get("role") == "tool"]
        assert tool_messages
        assert tool_messages[0]["content"] == "n1 recorded"

    def test_the_pad_never_ends_a_thinking_session(self) -> None:
        """The four MUSE_EXIT reasons are the only exits; a tool is not one."""
        pad = MusePad()
        outcome, _tools, _floor = _drive(
            pad,
            _resp("writing", _call("conclude", text="the count is 76")),
            _resp("GUIDANCE: 76 " + MARKER_DONE),
        )
        assert outcome.exit_reason == "concluded"
        assert pad.counts().kinds["conclude"] == 1

    def test_a_pad_that_cannot_persist_degrades_and_the_host_is_told(self, tmp_path: Path) -> None:
        """C3: a pad whose disk write fails is a recorded transition, never silent."""
        blocker = tmp_path / "pads"
        blocker.write_text("not a directory", encoding="utf-8")
        pad = MusePad.in_directory(blocker)
        outcome, _tools, _floor = _drive(
            pad,
            _resp("writing", _call("intend", text="split by parity")),
            _resp(MARKER_DONE),
        )
        assert DEGRADED_TOOL in {d.code for d in outcome.degradations}
        assert {r.code for r in ledger.from_muse(outcome)} >= {DEGRADED_TOOL}


# ── 7. the package surface ───────────────────────────────────────────────────


class TestThePackageSurface:
    def test_the_module_is_reachable_from_the_package(self) -> None:
        import embodiment

        assert embodiment.muse_pad is muse_pad

    @pytest.mark.parametrize("name", ["MusePad", "MusePadCounts", "MUSE_PAD_TOOLS"])
    def test_the_curated_names_resolve(self, name: str) -> None:
        import embodiment

        assert getattr(embodiment, name) is getattr(muse_pad, name)

    def test_the_counts_shape_is_exported(self) -> None:
        assert isinstance(MusePad().counts(), MusePadCounts)
