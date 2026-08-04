"""The live conversational host, tested with the gateway down (plan task ``t14``).

Every test here is hermetic. The host's only transport is
``examples/worker_seam.py``'s :class:`~examples.worker_seam.WorkerSeam`, which is
injected as a **fake seam** throughout, so ``uv run pytest -n auto`` stays green
on a machine that has never heard of a lobes gateway. The live dial happens only
when a person or an agent runs the CLI.

What is actually asserted, in the order the session will probe it:

* the containment the module docstring claims for the tool surface — including
  the symlink case, because ``resolve()`` following a link out of the tree is
  exactly the bug a path check gets wrong;
* that a directive reaches the worker as **one appended message at a turn
  boundary**, with the system prompt byte-identical across the whole drive. That
  is checked from the actual message list the acting loop was about to send,
  never from a claim;
* that what the host reports as governing the actor is ``ScopedOutcome.active``
  — drain's answer — and not the strategist's register (embodiment#54);
* both persistence lanes, including the part that matters: a durable directive
  survives a process boundary and a session-scoped one does not;
* the strategist kill: the lane stops, the degradation is recorded, and the
  actor keeps working under the last valid directive;
* per-seat accounting carrying ``finish_reason`` **per call** (issue #59), and
  felt latency measured as time-to-first-chunk rather than whole-call latency.

The strategist stand-in is ``examples/scope/greenhouse_scope.py``'s
``ScriptedStrategist``, which is the repo's existing scripted implementation of
:class:`~embodiment.strategist_runner.StrategistRunner`'s public surface. It is
reused rather than re-written: a second stand-in is a second thing to keep in
step with the real runner.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

import embodiment.scope_events as se
from embodiment import ModelResponse, ToolCall, ToolError, UnknownToolError
from embodiment.scope import LANE_DURABLE, LANE_SESSION, ScopeDirective, ScopeRegister
from embodiment.scoped_run import ScopeGovernor, ScopeSession, render_directive, run_scoped
from examples import scope_live_session as host
from examples import worker_seam as ws
from examples.scope import greenhouse_scope as gs

MODULE_PATH = Path(host.__file__)


# ══════════════════════════════════════════════════════════════════════════════
# fakes: one seam shape, used everywhere a model would be
# ══════════════════════════════════════════════════════════════════════════════


class FakeSeam:
    """A scripted stand-in for :class:`~examples.scope_live_session.TimedSeam`.

    It carries a real :class:`~examples.worker_seam.Meter`, so every accounting
    assertion below runs against the shape the live host actually folds — the
    fake supplies the *answers*, never the record structure.
    """

    def __init__(
        self,
        replies: list[ModelResponse],
        *,
        model: str = "fake-model",
        role: str = "fake",
        finish: str = "stop",
        first_chunk: Optional[float] = 0.25,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.endpoint = "http://fake/v1"
        self.meter = ws.Meter(role=role, model=model)
        self.replies = list(replies)
        self.calls = 0
        self.finish = finish
        self.last_first_chunk_at: Optional[float] = None
        self._first_chunk = first_chunk

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        self.meter.calls += 1
        self.meter.finish_reasons[self.finish] = self.meter.finish_reasons.get(self.finish, 0) + 1
        self.meter.record_turn(
            reply, finish_reason=self.finish, seconds=0.1, messages=len(messages)
        )
        self.meter.transcript[-1]["first_chunk_seconds"] = self._first_chunk
        self.last_first_chunk_at = None if self._first_chunk is None else 1000.0
        return reply


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=[ToolCall(id=f"c-{name}", name=name, arguments=dict(arguments))],
        completion_tokens=7,
        prompt_tokens=11,
    )


def _tools(tmp_path: Path) -> host.WorkspaceTools:
    root = host.seed_corpus(tmp_path / "root")
    return host.WorkspaceTools(root, tmp_path / "scratch")


def _state(tmp_path: Path, **kwargs: Any) -> host.SessionState:
    timeline = host.Timeline(echo=_Sink())
    return host.SessionState(_tools(tmp_path), timeline, **kwargs)


class _Sink:
    """A stderr stand-in, so a test run is not littered with host notices."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        self.lines.append(text)
        return len(text)

    def flush(self) -> None:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# the cited vocabulary cannot drift from the package
# ══════════════════════════════════════════════════════════════════════════════


class TestTheCitedVocabularyIsPinned:
    """``embodiment.scope_events`` is off the curated surface, so the host mirrors it.

    That mirror is the whole reason this class exists: a copied constant that
    nothing checks is the staleness defect ``tests/rate_config.py`` was built to
    stop one layer down. A test MAY import the module the host may not.
    """

    def test_the_event_kind_mirror_is_byte_identical(self) -> None:
        assert tuple(host.SCOPE_EVENT_KINDS) == tuple(se.SCOPE_EVENT_KINDS)

    def test_the_mirror_is_not_empty(self) -> None:
        assert len(host.SCOPE_EVENT_KINDS) == 10

    def test_the_host_cannot_import_the_module_it_mirrors(self) -> None:
        """The reason for the mirror, asserted so it is not mistaken for a habit."""
        import embodiment

        citable = set(embodiment.ARCHIVED_SUBMODULES) | set(embodiment.__all__)
        assert "scope_events" not in citable

    def test_the_directive_marker_is_derived_from_the_package(self) -> None:
        rendered = render_directive(ScopeDirective(scope_id="abc", objective="x", version=2))
        assert rendered.startswith(host.DIRECTIVE_MARKER)

    def test_the_directive_marker_carries_no_probe_residue(self) -> None:
        assert "probe" not in host.DIRECTIVE_MARKER


# ══════════════════════════════════════════════════════════════════════════════
# the tool surface: the threat model, asserted
# ══════════════════════════════════════════════════════════════════════════════


class TestTheToolSurfaceIsContained:
    """Constraint C2: the boundary is stated in the docstring and held here."""

    def test_reading_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        with pytest.raises(ToolError):
            tools.execute("read_file", {"path": "../secret.txt"})

    def test_listing_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        with pytest.raises(ToolError):
            tools.execute("list_files", {"path": "../.."})

    def test_a_symlink_pointing_out_of_the_tree_is_refused(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        (tools.root / "escape.txt").symlink_to(outside)
        with pytest.raises(ToolError):
            tools.execute("read_file", {"path": "escape.txt"})

    def test_writing_outside_the_scratch_is_refused(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        with pytest.raises(ToolError):
            tools.execute("write_note", {"name": "../../pwned.txt", "text": "x"})

    def test_a_write_never_reaches_the_read_only_root(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        tools.execute("write_note", {"name": "note.md", "text": "hello"})
        assert not (tools.root / "note.md").exists()

    def test_a_write_lands_in_the_scratch(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        tools.execute("write_note", {"name": "note.md", "text": "hello"})
        assert (tools.scratch / "note.md").read_text(encoding="utf-8") == "hello"

    def test_an_oversized_read_is_refused_rather_than_clipped(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        (tools.root / "big.txt").write_text("x" * (host.MAX_READ_BYTES + 1), encoding="utf-8")
        with pytest.raises(ToolError):
            tools.execute("read_file", {"path": "big.txt"})

    def test_grep_finds_the_one_cross_file_fact(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        outcome = tools.execute("grep", {"pattern": "s-fig-01"})
        assert outcome.result.count("\n") >= 1

    def test_grep_reports_a_miss_rather_than_an_empty_string(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        outcome = tools.execute("grep", {"pattern": "no-such-token"})
        assert "no line contains" in outcome.result

    def test_an_unknown_tool_is_an_unknown_tool_error(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        with pytest.raises(UnknownToolError):
            tools.execute("rm", {"path": "/"})

    def test_finish_is_the_only_terminal_tool(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        outcome = tools.execute("finish", {"summary": "done"})
        assert outcome.finished is True

    def test_a_refusal_is_counted_so_repeated_failure_is_material(self, tmp_path: Path) -> None:
        tools = _tools(tmp_path)
        for _ in range(3):
            with pytest.raises(ToolError):
                tools.execute("read_file", {"path": "nope.md"})
        assert tools.failures["read_file"] == 3


class TestTheHostShipsNoShellAndNoSecondTransport:
    """Asserted by AST, because "there is no shell tool" is a claim about code."""

    @staticmethod
    def _tree() -> ast.AST:
        return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))

    def test_no_process_spawning_module_is_imported(self) -> None:
        banned = {"subprocess", "shutil", "multiprocessing", "socket", "asyncio", "pty"}
        found = {
            (node.module or "").split(".")[0]
            for node in ast.walk(self._tree())
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not (found & banned), sorted(found & banned)

    def test_the_only_transport_call_is_the_capabilities_fetch(self) -> None:
        opens = [
            node
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Attribute) and node.attr == "urlopen"
        ]
        assert len(opens) == 1

    def test_the_tool_schema_names_exactly_the_executed_tools(self, tmp_path: Path) -> None:
        declared = {entry["function"]["name"] for entry in host.TOOL_SCHEMA}
        assert declared == {"list_files", "read_file", "grep", "write_note", "finish"}


# ══════════════════════════════════════════════════════════════════════════════
# the interaction tier
# ══════════════════════════════════════════════════════════════════════════════


class TestTheVoice:
    def test_a_reply_with_no_marker_starts_no_work(self) -> None:
        spoken, work = host.split_work_marker("Morning. Nothing needs doing yet.")
        assert work is None

    def test_the_marker_is_stripped_from_what_the_operator_sees(self) -> None:
        spoken, _work = host.split_work_marker("On it.\n[work] read the readings file")
        assert spoken == "On it."

    def test_the_marker_carries_the_instruction(self) -> None:
        _spoken, work = host.split_work_marker("On it.\n[work] read the readings file")
        assert work == "read the readings file"

    def test_an_empty_marker_is_not_a_task(self) -> None:
        _spoken, work = host.split_work_marker("Sure.\n[work]   ")
        assert work is None

    @pytest.mark.parametrize("token", sorted(host._NO_WORK_TOKENS))
    def test_a_placeholder_marker_is_not_a_task(self, token: str) -> None:
        """The first live run's actual failure: the voice wrote ``[work] None``.

        The prompt now says to omit the line, but a host that trusts a model to
        never write a placeholder is a host that starts work nobody asked for —
        and it did, with an instruction of literally ``"None"``.
        """
        _spoken, work = host.split_work_marker(f"Nothing to do.\n[work] {token}")
        assert work is None

    def test_a_placeholder_with_a_full_stop_is_still_not_a_task(self) -> None:
        _spoken, work = host.split_work_marker("Nothing to do.\n[work] None.")
        assert work is None

    def test_a_real_instruction_survives_the_placeholder_guard(self) -> None:
        _spoken, work = host.split_work_marker("Sure.\n[work] read notes/orchid-bed.md")
        assert work == "read notes/orchid-bed.md"

    def test_the_prompt_tells_the_voice_to_omit_the_line(self) -> None:
        assert "leave the line out entirely" in host.SENSES_SYSTEM

    def test_the_prompt_forbids_describing_the_hosts_own_plumbing(self) -> None:
        """#52's "one coherent teammate", measured coming apart.

        Asked which of two objectives came first, the voice answered "the
        'work' block specifies the action" — narrating the host's handoff
        protocol at the operator.
        """
        assert "Never mention it, the status block" in host.SENSES_SYSTEM

    def test_the_senses_prompt_forbids_naming_a_second_mind(self) -> None:
        assert "never speak as, quote, or relay another mind" in host.SENSES_SYSTEM

    def test_the_status_block_is_labelled_data_not_instruction(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        assert host.senses_status(state).startswith("STATUS — data about this system")

    def test_the_prompt_states_what_the_system_can_actually_do(self) -> None:
        """The first live run's real failure was a refusal, not a crash.

        Asked "which bed is driest today", the voice answered "I don't have
        access to any information about beds or moisture levels in your files"
        — with an acting loop behind it that could have read the answer in two
        tool calls. A partner that refuses work it can do is the failure this
        host is judged on, so the capability is stated as a fact.
        """
        assert "Never tell the person you have no access" in host.SENSES_SYSTEM

    def test_the_status_block_names_the_files_rather_than_counting_them(
        self, tmp_path: Path
    ) -> None:
        state = _state(tmp_path)
        assert "readings/2026-08-03.csv" in host.senses_status(state)

    def test_the_status_block_names_what_can_be_done_to_them(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        assert "list, read, search" in host.senses_status(state)

    def test_an_operator_supplied_tree_is_not_enumerated_into_the_prompt(
        self, tmp_path: Path
    ) -> None:
        """A real repo's file list is not something to paste into every turn."""
        state = _state(tmp_path, seeded=False)
        assert "(operator-supplied tree)" in host.senses_status(state)

    def test_a_dead_senses_seat_degrades_instead_of_raising(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seams = host.SeatSeams(resolution=_resolution())
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        spoken, work = session._senses("hello", submitted=0.0)
        assert work is None

    def test_the_interaction_tier_is_bounded_smaller_than_the_acting_one(self) -> None:
        """The asymmetry a live run cost 22 minutes to establish.

        Too small on this tier is a cut-off reply, which this host counts and
        announces. Too large is minutes of silence on the seat whose entire job
        is presence, which nothing counts and nothing recovers.
        """
        assert host.SENSES_MAX_TOKENS < host.ACTOR_MAX_TOKENS

    def test_the_acting_and_strategic_tiers_keep_the_d16_budget(self) -> None:
        assert (host.ACTOR_MAX_TOKENS, host.STRATEGIST_MAX_TOKENS) == (16000, 16000)

    def test_a_truncated_reply_is_announced_to_the_operator(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seam = FakeSeam([ModelResponse(content="I was saying that")], finish="length")
        seams = host.SeatSeams(resolution=_resolution(), senses=seam)
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        spoken, _work = session._senses("hello", submitted=0.0)
        assert "cut off at this turn's token budget" in spoken

    def test_a_truncated_reply_is_also_recorded(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seam = FakeSeam([ModelResponse(content="I was saying that")], finish="length")
        seams = host.SeatSeams(resolution=_resolution(), senses=seam)
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        session._senses("hello", submitted=0.0)
        notices = [e for e in state.timeline.entries if e.kind == "notice"]
        assert len(notices) == 1

    def test_an_untruncated_reply_carries_no_such_marker(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seam = FakeSeam([ModelResponse(content="all done")], finish="stop")
        seams = host.SeatSeams(resolution=_resolution(), senses=seam)
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        spoken, _work = session._senses("hello", submitted=0.0)
        assert spoken == "all done"

    def test_a_dead_senses_seat_records_a_notice(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seams = host.SeatSeams(resolution=_resolution())
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        session._senses("hello", submitted=0.0)
        notices = [e for e in state.timeline.entries if e.kind == "notice"]
        assert len(notices) == 1


class TestTheStrategistFramingIsAppendedNeverSubstituted:
    """The authority boundary is the package's; the vocabulary is the host's."""

    def test_the_framing_restates_no_part_of_the_authority_text(self) -> None:
        """The amendment-1 lesson: state the facts, never re-state the rule.

        ``scopebench_live``'s first wording repeated ``SCOPE_AUTHORITY``'s
        version rule inside a sentence naming the active scope, and the worker
        seat echoed the id it had just been shown into its own — every directive
        refused as a duplicate, published as a model property. So this framing
        must not contain the rule.
        """
        from embodiment.scope import SCOPE_AUTHORITY

        for phrase in ("strictly greater", "DIRECTIVE:", "[hold]", "must carry"):
            assert phrase not in host.STRATEGIST_FRAMING, (phrase, SCOPE_AUTHORITY[:0])

    def test_the_framing_names_the_actual_tool_surface(self) -> None:
        for name in ("list_files", "read_file", "grep", "write_note", "finish"):
            assert name in host.STRATEGIST_FRAMING

    def test_the_framing_states_that_the_objective_order_is_undecided(self) -> None:
        assert "not an order of importance" in host.STRATEGIST_FRAMING

    def test_the_framing_names_no_good_answer(self) -> None:
        """A framing that hinted at what to decide would be marking its own homework."""
        for leak in ("orchid", "fern", "moss", "threshold first", "prioriti"):
            assert leak not in host.STRATEGIST_FRAMING


def _resolution() -> Any:
    from examples.scope import seats as st

    return st.resolve_seats(gs.capabilities_fixture())


def _observer(state: host.SessionState) -> host.HostObserver:
    return host.HostObserver(state.timeline, echo=_Sink())


# ══════════════════════════════════════════════════════════════════════════════
# the projector: the host's own reading, and the non-intervention property
# ══════════════════════════════════════════════════════════════════════════════


class _Context:
    """The two attributes the projector reads off a ``ScopeContext``."""

    def __init__(self, report: Any = None, active: Any = None) -> None:
        self.report = report
        self.active = active


class TestTheProjector:
    def test_an_unchanged_world_projects_an_identical_snapshot(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        project = host.session_projector(state)
        first = project(_Context())
        second = project(_Context())
        assert first.snapshot_id == second.snapshot_id

    def test_a_changed_world_moves_the_snapshot_id(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        project = host.session_projector(state)
        before = project(_Context()).snapshot_id
        state.objectives.append("keep the orchid alive")
        assert project(_Context()).snapshot_id != before

    def test_one_objective_declares_no_conflict(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        state.objectives.append("keep the orchid alive")
        assert host.session_projector(state)(_Context()).conflicts == ()

    def test_two_objectives_declare_a_conflict(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        state.objectives += ["keep the orchid alive", "never water anything twice"]
        snapshot = host.session_projector(state)(_Context())
        assert len(snapshot.conflicts) == 1

    def test_two_objectives_ask_for_a_decision(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        state.objectives += ["a", "b"]
        snapshot = host.session_projector(state)(_Context())
        assert snapshot.requested_decision is not None

    def test_the_snapshot_names_the_tools_it_can_see(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        snapshot = host.session_projector(state)(_Context())
        assert "read_file" in snapshot.resource_state["tools"]


class TestTheHostDefaultScope:
    def test_the_default_is_admitted_by_a_real_register(self) -> None:
        register = ScopeRegister(default=host.default_scope())
        assert register.active is not None

    def test_the_default_is_version_zero_so_a_directive_can_supersede_it(self) -> None:
        assert host.default_scope().version == 0

    def test_the_default_carries_no_action_bearing_field(self) -> None:
        payload = host.default_scope().to_dict()
        assert not ({"tool", "command", "approve"} & set(payload))


# ══════════════════════════════════════════════════════════════════════════════
# the drive: delivery is event-shaped, and the system prompt is never rewritten
# ══════════════════════════════════════════════════════════════════════════════


def _directive(scope_id: str, version: int, supersedes: Optional[str] = None) -> ScopeDirective:
    return ScopeDirective(
        scope_id=scope_id,
        supersedes=supersedes,
        objective="check the orchid readings first",
        priorities=("put orchid-bed first",),
        decision_summary="the orchid bed is the one below threshold",
        version=version,
    )


def _actor_replies() -> list[ModelResponse]:
    return [
        _call("list_files", path=""),
        _call("grep", pattern="s-fig-01"),
        _call("read_file", path="readings/2026-08-03.csv"),
        _call("finish", summary="orchid-bed reads 12%, below its 25% threshold"),
    ]


def _run_drive(
    tmp_path: Path,
    *,
    strategist: Any = None,
    session: Optional[ScopeSession] = None,
    persistence: Any = None,
    max_steps: int = 6,
) -> tuple[host.SessionState, Any, host.ContextWatcher]:
    state = _state(tmp_path, max_steps=max_steps)
    observer = _observer(state)
    governor = ScopeGovernor(
        strategist=strategist,
        projector=host.session_projector(state),
        default_scope=host.default_scope(),
        session=session,
        persistence=persistence,
    )
    seam = FakeSeam(_actor_replies(), role="actor")
    watcher = host.ContextWatcher(seam, state.timeline)
    from embodiment import Task

    outcome = run_scoped(
        watcher,
        Task(id="t", repo_path=str(state.tools.root), instruction="check the beds", context=""),
        executor=state.tools,
        max_steps=max_steps,
        governor=governor,
        system_prompt=host.WORKER_SYSTEM,
        observer=observer,
    )
    state.active_directive = outcome.active
    return state, outcome, watcher


class TestDeliveryIsEventShaped:
    """Confirmed decision ``c35``, measured off the acting loop's own messages."""

    def test_the_host_default_reaches_the_actor_as_an_inserted_message(
        self, tmp_path: Path
    ) -> None:
        state, _outcome, _watcher = _run_drive(tmp_path)
        inserted = [e for e in state.timeline.entries if e.kind == "scope-inserted"]
        assert len(inserted) >= 1

    def test_the_inserted_message_is_not_a_system_message(self, tmp_path: Path) -> None:
        state, _outcome, _watcher = _run_drive(tmp_path)
        inserted = [e for e in state.timeline.entries if e.kind == "scope-inserted"]
        assert inserted[0].data["role"] == "user"

    def test_the_system_prompt_is_byte_identical_all_drive(self, tmp_path: Path) -> None:
        _state_, _outcome, watcher = _run_drive(tmp_path)
        assert watcher.system_rewrites == 0

    def test_the_watcher_actually_saw_several_turns(self, tmp_path: Path) -> None:
        _state_, _outcome, watcher = _run_drive(tmp_path)
        assert watcher.turns >= 3

    def test_a_strategist_directive_is_applied_at_a_boundary(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        assert outcome.active.scope_id == "focus-orchid"

    def test_the_applied_directive_appears_as_a_scope_event(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        state, _outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        kinds = [e.kind for e in state.timeline.entries if e.stream == "scope"]
        assert "scope.directive.applied" in kinds

    def test_the_directive_bytes_reach_the_actor_context(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        state, _outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        texts = [e.text for e in state.timeline.entries if e.kind == "scope-inserted"]
        assert any("focus-orchid" in text for text in texts)

    def test_what_governs_is_read_from_drain_not_from_the_register(self, tmp_path: Path) -> None:
        """embodiment#54: the two can disagree, and only one of them is honest."""
        withheld = _directive("never-received", 0, "host-default")
        strategist = gs.ScriptedStrategist([withheld])
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        # version 0 does not advance on the applied default's 0, so it is withheld.
        assert outcome.active.scope_id == "host-default"

    def test_the_withheld_directive_is_recorded_rather_than_dropped(self, tmp_path: Path) -> None:
        withheld = _directive("never-received", 0, "host-default")
        strategist = gs.ScriptedStrategist([withheld])
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        kinds = [entry.kind for entry in outcome.transitions]
        assert "scope-withheld" in kinds

    def test_the_strategist_still_believes_it_issued_it(self, tmp_path: Path) -> None:
        """The contrast that makes the previous test mean something."""
        withheld = _directive("never-received", 0, "host-default")
        strategist = gs.ScriptedStrategist([withheld])
        _run_drive(tmp_path, strategist=strategist)
        assert strategist.issued[-1].scope_id == "never-received"


class TestOrdinaryToolStepsAreNotStrategicReports:
    def test_a_quiet_drive_offers_at_most_one_snapshot_per_material_change(
        self, tmp_path: Path
    ) -> None:
        strategist = gs.ScriptedStrategist([None])
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        assert outcome.counts["snapshots_offered"] < outcome.counts["boundaries"]

    def test_a_hold_is_recorded_as_a_real_answer(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist([None])
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        assert outcome.counts["holds_recorded"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# the strategist kill
# ══════════════════════════════════════════════════════════════════════════════


class TestTheStrategistKill:
    def test_the_first_n_dials_go_through(self) -> None:
        seam = host.KillableSeam(inner=FakeSeam([ModelResponse(content="[hold]")]), kill_after=2)
        seam([])
        seam([])
        assert seam.calls == 2

    def test_the_next_dial_refuses_like_a_dead_endpoint(self) -> None:
        seam = host.KillableSeam(inner=FakeSeam([ModelResponse(content="[hold]")]), kill_after=1)
        seam([])
        with pytest.raises(ws.WorkerTransportError):
            seam([])

    def test_the_kill_is_recorded_on_the_gate(self) -> None:
        seam = host.KillableSeam(inner=FakeSeam([ModelResponse(content="[hold]")]), kill_after=0)
        with pytest.raises(ws.WorkerTransportError):
            seam([])
        assert seam.killed is True

    def test_no_kill_configured_never_fires(self) -> None:
        seam = host.KillableSeam(inner=FakeSeam([ModelResponse(content="[hold]")]))
        for _ in range(5):
            seam([])
        assert seam.killed is False

    def test_a_stopped_lane_leaves_the_actor_under_the_last_valid_directive(
        self, tmp_path: Path
    ) -> None:
        """The whole point of ``--kill-strategist-after``, on the scripted lane."""
        strategist = gs.ScriptedStrategist(
            [_directive("focus-orchid", 1, "host-default")], degrade_after=1
        )
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        assert outcome.active.scope_id == "focus-orchid"

    def test_the_stopped_lane_is_recorded(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist(
            [_directive("focus-orchid", 1, "host-default")], degrade_after=1
        )
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        kinds = [entry.kind for entry in outcome.transitions]
        assert "scope-lane-degraded" in kinds

    def test_the_degradation_says_what_the_actor_continues_under(self, tmp_path: Path) -> None:
        strategist = gs.ScriptedStrategist(
            [_directive("focus-orchid", 1, "host-default")], degrade_after=1
        )
        _state_, outcome, _watcher = _run_drive(tmp_path, strategist=strategist)
        degraded = [e for e in outcome.transitions if e.kind == "scope-lane-degraded"]
        assert "focus-orchid" in degraded[0].reason


# ══════════════════════════════════════════════════════════════════════════════
# both persistence lanes
# ══════════════════════════════════════════════════════════════════════════════


class TestThePersistenceLanes:
    def test_a_durable_directive_survives_a_process_boundary(self, tmp_path: Path) -> None:
        store = tmp_path / "scope-state.json"
        timeline = host.Timeline(echo=_Sink())
        port = host._file_persistence(store, timeline)
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        _run_drive(tmp_path / "a", strategist=strategist, persistence=port)
        # A second host, reading the same store, with no strategist at all.
        _state_, outcome, _watcher = _run_drive(
            tmp_path / "b", persistence=host._file_persistence(store, timeline)
        )
        assert outcome.active.scope_id == "focus-orchid"

    def test_the_durable_drive_names_its_lane(self, tmp_path: Path) -> None:
        store = tmp_path / "scope-state.json"
        timeline = host.Timeline(echo=_Sink())
        _state_, outcome, _watcher = _run_drive(
            tmp_path / "a", persistence=host._file_persistence(store, timeline)
        )
        assert outcome.lane == LANE_DURABLE

    def test_a_session_scoped_directive_does_not_outlive_its_session(self, tmp_path: Path) -> None:
        session = ScopeSession(session_id="s1")
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        _run_drive(tmp_path / "a", strategist=strategist, session=session)
        session.close()
        _state_, outcome, _watcher = _run_drive(tmp_path / "b", session=session)
        assert outcome.active.scope_id == "host-default"

    def test_a_session_survives_across_drives_while_it_is_open(self, tmp_path: Path) -> None:
        session = ScopeSession(session_id="s1")
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        _run_drive(tmp_path / "a", strategist=strategist, session=session)
        _state_, outcome, _watcher = _run_drive(tmp_path / "b", session=session)
        assert outcome.active.scope_id == "focus-orchid"

    def test_the_session_drive_names_its_lane(self, tmp_path: Path) -> None:
        _state_, outcome, _watcher = _run_drive(tmp_path, session=ScopeSession(session_id="s1"))
        assert outcome.lane == LANE_SESSION

    def test_every_transition_names_a_lane(self, tmp_path: Path) -> None:
        _state_, outcome, _watcher = _run_drive(tmp_path, session=ScopeSession(session_id="s1"))
        laneless = [entry.kind for entry in outcome.transitions if not entry.lane]
        assert laneless == []

    def test_the_store_holds_the_packages_own_payload_shape(self, tmp_path: Path) -> None:
        store = tmp_path / "scope-state.json"
        timeline = host.Timeline(echo=_Sink())
        strategist = gs.ScriptedStrategist([_directive("focus-orchid", 1, "host-default")])
        _run_drive(
            tmp_path / "a",
            strategist=strategist,
            persistence=host._file_persistence(store, timeline),
        )
        payload = json.loads(store.read_text(encoding="utf-8"))
        assert payload["lane"] == LANE_DURABLE

    def test_an_unreadable_store_degrades_rather_than_raising(self, tmp_path: Path) -> None:
        store = tmp_path / "broken.json"
        store.write_text("{not json", encoding="utf-8")
        timeline = host.Timeline(echo=_Sink())
        _state_, outcome, _watcher = _run_drive(
            tmp_path / "a", persistence=host._file_persistence(store, timeline)
        )
        kinds = [entry.kind for entry in outcome.transitions]
        assert "scope-lane-degraded" in kinds


# ══════════════════════════════════════════════════════════════════════════════
# accounting: finish_reason per call, and felt latency
# ══════════════════════════════════════════════════════════════════════════════


class TestPerSeatAccounting:
    def test_an_undialled_seat_says_so_rather_than_reporting_zeroes(self) -> None:
        assert host.seat_account(None) == {"dialled": False}

    def test_every_call_carries_its_own_finish_reason(self) -> None:
        seam = FakeSeam([ModelResponse(content="ok")], finish="length")
        seam([])
        seam([])
        account = host.seat_account(seam)
        assert [entry["finish_reason"] for entry in account["per_call"]] == ["length", "length"]

    def test_the_histogram_and_the_per_call_list_agree(self) -> None:
        seam = FakeSeam([ModelResponse(content="ok")], finish="length")
        seam([])
        account = host.seat_account(seam)
        assert account["finish_reasons"]["length"] == len(account["per_call"])

    def test_the_per_call_record_carries_time_to_first_chunk(self) -> None:
        seam = FakeSeam([ModelResponse(content="ok")], first_chunk=0.42)
        seam([])
        account = host.seat_account(seam)
        assert account["per_call"][0]["first_chunk_seconds"] == 0.42

    def test_a_blocking_dial_records_no_first_chunk_rather_than_a_zero(self) -> None:
        seam = FakeSeam([ModelResponse(content="ok")], first_chunk=None)
        seam([])
        account = host.seat_account(seam)
        assert account["per_call"][0]["first_chunk_seconds"] is None


class TestFeltLatencyIsMeasuredAtTheFirstChunk:
    """A transparent proxy over the response, so no transport logic is copied."""

    def test_the_first_line_stamps_and_the_rest_do_not(self) -> None:
        stamps: list[int] = []
        proxy = host._FirstByteResponse(iter([b"a", b"b", b"c"]), lambda: stamps.append(1))
        list(proxy)
        assert stamps == [1]

    def test_every_line_still_reaches_the_reader(self) -> None:
        proxy = host._FirstByteResponse(iter([b"a", b"b"]), lambda: None)
        assert list(proxy) == [b"a", b"b"]

    def test_attribute_access_is_forwarded_so_the_idle_bound_stays_armed(self) -> None:
        class _Inner:
            def set_read_timeout(self, value: float) -> None:
                self.value = value

        inner = _Inner()
        proxy = host._FirstByteResponse(inner, lambda: None)
        assert ws.read_timeout_setter(proxy) is not None

    def test_an_empty_stream_stamps_nothing(self) -> None:
        stamps: list[int] = []
        proxy = host._FirstByteResponse(iter([]), lambda: stamps.append(1))
        list(proxy)
        assert stamps == []


# ══════════════════════════════════════════════════════════════════════════════
# the record a person reads
# ══════════════════════════════════════════════════════════════════════════════


class TestTheTranscript:
    def test_streams_are_interleaved_in_time_order(self, tmp_path: Path) -> None:
        timeline = host.Timeline(echo=_Sink())
        timeline.add("operator", "line", "hello")
        timeline.add("senses", "reply", "hi", felt_seconds=1.5)
        timeline.add("scope", "scope.snapshot", "offered")
        stamps = [entry.at for entry in timeline.entries]
        assert stamps == sorted(stamps)

    def test_the_felt_latency_rides_the_line_the_operator_read(self, tmp_path: Path) -> None:
        timeline = host.Timeline(echo=_Sink())
        timeline.add("senses", "reply", "hi", felt_seconds=1.5)
        rendered = host.render_transcript(timeline, {"started": "now"})
        assert "felt 1.5s to first token" in rendered

    def test_a_scope_event_is_rendered_with_its_kind(self, tmp_path: Path) -> None:
        timeline = host.Timeline(echo=_Sink())
        timeline.add("scope", "scope.directive.applied", "focus-orchid v1")
        rendered = host.render_transcript(timeline, {"started": "now"})
        assert "`scope.directive.applied`" in rendered

    def test_the_inserted_directive_is_shown_verbatim(self, tmp_path: Path) -> None:
        timeline = host.Timeline(echo=_Sink())
        timeline.add(
            "worker",
            "scope-inserted",
            "[scope directive — focus-orchid, version 1]",
            turn=2,
            message_index=5,
            role="user",
            system_prompt_sha="abc",
        )
        rendered = host.render_transcript(timeline, {"started": "now"})
        assert "[scope directive — focus-orchid, version 1]" in rendered

    def test_the_jsonl_is_written_line_by_line(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        timeline = host.Timeline(path, echo=_Sink())
        timeline.add("operator", "line", "hello")
        timeline.add("scope", "scope.snapshot", "offered")
        timeline.close()
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_every_jsonl_line_is_a_json_object(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        timeline = host.Timeline(path, echo=_Sink())
        timeline.add("operator", "line", "hello")
        timeline.close()
        parsed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert parsed[0]["stream"] == "operator"


class TestTheCostTable:
    """``/cost`` prints a table into a conversation; the JSON goes to the record."""

    @staticmethod
    def _payload(tmp_path: Path) -> dict[str, Any]:
        state = _state(tmp_path)
        seam = FakeSeam([ModelResponse(content="hi")], role="senses", finish="length")
        seam([])
        seams = host.SeatSeams(resolution=_resolution(), senses=seam)
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        session.felt.append(1.9)
        return session.cost()

    def test_every_seat_gets_a_row(self, tmp_path: Path) -> None:
        rendered = host.render_cost(self._payload(tmp_path))
        for seat in ("senses", "actor", "strategist"):
            assert seat in rendered

    def test_an_undialled_seat_says_so_rather_than_showing_zeroes(self, tmp_path: Path) -> None:
        rendered = host.render_cost(self._payload(tmp_path))
        assert "(not dialled)" in rendered

    def test_the_felt_latency_is_on_the_table(self, tmp_path: Path) -> None:
        rendered = host.render_cost(self._payload(tmp_path))
        assert "felt latency to first token" in rendered

    def test_truncation_is_countable_from_the_table(self, tmp_path: Path) -> None:
        rendered = host.render_cost(self._payload(tmp_path))
        assert "length" in rendered

    def test_the_table_is_not_json(self, tmp_path: Path) -> None:
        """The defect this renderer exists for: /cost used to dump the record."""
        rendered = host.render_cost(self._payload(tmp_path))
        assert not rendered.lstrip().startswith("{")


class TestTheObserverIsOneSeamForBothStreams:
    def test_scope_events_are_collected(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        observer = _observer(state)
        observer(se.ScopeEvent(kind="scope.snapshot", detail="offered"))
        assert len(observer.scope_events) == 1

    def test_loop_events_are_recorded_but_not_collected_as_scope(self, tmp_path: Path) -> None:
        from embodiment import LoopEvent

        state = _state(tmp_path)
        observer = _observer(state)
        observer(LoopEvent(kind="step", detail="read_file"))
        assert observer.scope_events == []

    def test_loop_events_still_reach_the_record(self, tmp_path: Path) -> None:
        from embodiment import LoopEvent

        state = _state(tmp_path)
        observer = _observer(state)
        observer(LoopEvent(kind="step", detail="read_file"))
        assert [e.stream for e in state.timeline.entries] == ["loop"]


# ══════════════════════════════════════════════════════════════════════════════
# the CLI: no traceback ever reaches an operator
# ══════════════════════════════════════════════════════════════════════════════


class TestTheCli:
    def test_the_parser_offers_both_verbs(self) -> None:
        parser = host.build_parser()
        args = parser.parse_args(["seats"])
        assert args.verb == "seats"

    def test_the_ungoverned_control_is_its_own_flag(self) -> None:
        args = host.build_parser().parse_args(["talk", "--no-strategist"])
        assert args.no_strategist is True

    def test_the_kill_flag_takes_a_count(self) -> None:
        args = host.build_parser().parse_args(["talk", "--kill-strategist-after", "2"])
        assert args.kill_strategist_after == 2

    def test_identity_is_absent_by_default(self) -> None:
        args = host.build_parser().parse_args(["talk"])
        assert args.identity == ""

    def test_a_dead_gateway_returns_an_environment_code(self, capsys: Any) -> None:
        code = host.main(["seats", "--gateway", "http://127.0.0.1:1"])
        assert code == 2

    def test_a_dead_gateway_prints_no_traceback(self, capsys: Any) -> None:
        host.main(["seats", "--gateway", "http://127.0.0.1:1"])
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err

    def test_a_dead_gateway_prints_a_hint(self, capsys: Any) -> None:
        host.main(["seats", "--gateway", "http://127.0.0.1:1"])
        captured = capsys.readouterr()
        assert "hint:" in captured.err

    def test_an_unarmed_governor_is_the_pass_through_path(self) -> None:
        assert ScopeGovernor().armed is False


class TestTheHeaderDescribesTheInstrumentThatRan:
    """A record that reports a module default rather than the dialled value lies."""

    @staticmethod
    def _header(**overrides: Any) -> dict[str, Any]:
        argv = ["talk"]
        for key, value in overrides.items():
            argv.append(f"--{key.replace('_', '-')}")
            if value is not True:
                argv.append(str(value))
        args = host.build_parser().parse_args(argv)
        seams = host.SeatSeams(resolution=_resolution())
        return host._header(args, seams, ScopeGovernor(), Path("/root"), Path("/scratch"))

    def test_the_first_chunk_bound_is_derived_at_the_dialled_width(self) -> None:
        bounds = ws.StreamBounds.derived(dialled_width=host.STREAM_QUEUE_WIDTH)
        assert self._header()["stream_first_chunk_timeout_s"] == round(bounds.first_chunk_s, 1)

    def test_that_bound_is_not_the_width_one_module_constant(self) -> None:
        """The contrast: they differ, so reporting the constant would be wrong."""
        assert self._header()["stream_first_chunk_timeout_s"] != round(
            ws.STREAM_FIRST_CHUNK_TIMEOUT, 1
        )

    def test_the_transport_named_is_the_one_dialled(self) -> None:
        assert self._header(no_stream=True)["transport"] == ws.TRANSPORT_BLOCKING

    def test_an_absent_identity_says_so_rather_than_showing_blank(self) -> None:
        assert "byte-identical" in self._header()["identity"]

    def test_the_ungoverned_control_is_named_in_the_header(self) -> None:
        assert "DISARMED" in self._header(no_strategist=True)["strategy (cortex)"]

    def test_the_help_text_names_every_command_the_repl_answers(self) -> None:
        verbs = ("/work", "/await", "/settle", "/objective", "/scope", "/state", "/cost", "/quit")
        for verb in verbs:
            assert verb in host._HELP

    def test_the_help_text_explains_the_pacing_that_decides_what_is_seen(self) -> None:
        assert "delivered at a boundary of a LATER one" in host._HELP


class TestTheDriveGuards:
    """One drive at a time, and never a drive nobody asked for."""

    @staticmethod
    def _session(tmp_path: Path) -> host.LiveSession:
        state = _state(tmp_path)
        seams = host.SeatSeams(resolution=_resolution(), actor=FakeSeam(_actor_replies()))
        return host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))

    def test_a_blank_instruction_starts_nothing(self, tmp_path: Path) -> None:
        assert self._session(tmp_path).start_drive("   ") is False

    def test_a_none_instruction_starts_nothing(self, tmp_path: Path) -> None:
        assert self._session(tmp_path).start_drive(None) is False

    def test_a_refused_drive_is_recorded_as_a_notice(self, tmp_path: Path) -> None:
        session = self._session(tmp_path)
        session.start_drive(None)
        notices = [e for e in session.timeline.entries if e.kind == "notice"]
        assert len(notices) == 1

    def test_an_unwired_actor_starts_nothing(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        seams = host.SeatSeams(resolution=_resolution())
        session = host.LiveSession(state, seams, ScopeGovernor(), observer=_observer(state))
        assert session.start_drive("do a thing") is False

    def test_awaiting_nothing_says_so_rather_than_hanging(self, tmp_path: Path) -> None:
        assert self._session(tmp_path).await_drive() == "nothing is running."

    def test_a_finished_drive_is_reported_by_await(self, tmp_path: Path) -> None:
        session = self._session(tmp_path)
        session.start_drive("check the beds")
        assert "done (" in session.await_drive()

    def test_awaiting_a_drive_that_already_ended_still_reports_it(self, tmp_path: Path) -> None:
        """The race a parallel test run found, and the operator would have felt.

        A short drive can finish between the ask and the wait. Answering
        "nothing is running" to somebody who just asked for that drive's result
        reads as the host having lost their work.
        """
        session = self._session(tmp_path)
        session.start_drive("check the beds")
        session.await_drive()
        assert "done (" in session.await_drive()

    def test_the_drive_actually_used_the_tools(self, tmp_path: Path) -> None:
        session = self._session(tmp_path)
        session.start_drive("check the beds")
        session.await_drive()
        assert [name for name, _ in session.state.tools.calls][0] == "list_files"


class TestSettlingTheStrategicTier:
    """``/settle`` is the operator waiting, never the loop being made to."""

    @staticmethod
    def _session(tmp_path: Path, strategist: Any = None) -> host.LiveSession:
        state = _state(tmp_path)
        seams = host.SeatSeams(resolution=_resolution(), actor=FakeSeam(_actor_replies()))
        governor = ScopeGovernor(strategist=strategist)
        return host.LiveSession(state, seams, governor, observer=_observer(state))

    def test_an_ungoverned_session_says_there_is_nothing_to_wait_for(self, tmp_path: Path) -> None:
        assert "no strategic tier" in self._session(tmp_path).settle_strategist()

    def test_a_lane_with_no_wait_surface_says_so_rather_than_hanging(self, tmp_path: Path) -> None:
        session = self._session(tmp_path, strategist=gs.ScriptedStrategist([]))
        assert "cannot be waited on" in session.settle_strategist()

    def test_an_idle_real_lane_settles_immediately(self, tmp_path: Path) -> None:
        """Uses the real runner, with a seam that is never reached because no
        snapshot is ever offered — so this exercises ``wait_idle``, not a model."""
        from embodiment.strategist_runner import StrategistRunner

        runner = StrategistRunner(FakeSeam([ModelResponse(content="[hold]")]))
        try:
            session = self._session(tmp_path, strategist=runner)
            assert "settled after" in session.settle_strategist()
        finally:
            runner.close()

    def test_settling_is_recorded_on_the_timeline(self, tmp_path: Path) -> None:
        from embodiment.strategist_runner import StrategistRunner

        runner = StrategistRunner(FakeSeam([ModelResponse(content="[hold]")]))
        try:
            session = self._session(tmp_path, strategist=runner)
            session.settle_strategist()
            assert [e.kind for e in session.timeline.entries if e.kind == "settled"] == ["settled"]
        finally:
            runner.close()

    def test_the_poll_interval_is_the_packages_own(self) -> None:
        """No clock of this host's: the poll is the runner's already-exempt one."""
        from embodiment import strategist_runner as sr

        assert host.DEFAULT_POLL_INTERVAL is sr.DEFAULT_POLL_INTERVAL


class TestTheIssuedChainIsSeeded:
    """A fresh register knows no scope_id, so every ``supersedes`` would be refused."""

    @staticmethod
    def _args(**kwargs: Any) -> Any:
        argv = ["talk"]
        for key, value in kwargs.items():
            argv += [f"--{key.replace('_', '-')}", str(value)]
        return host.build_parser().parse_args(argv)

    def test_the_host_default_is_seated_in_the_issued_chain(self, tmp_path: Path) -> None:
        register = host.issued_chain(self._args(), host.Timeline(echo=_Sink()))
        assert register.active.scope_id == "host-default"

    def test_a_directive_superseding_the_default_is_admitted(self, tmp_path: Path) -> None:
        register = host.issued_chain(self._args(), host.Timeline(echo=_Sink()))
        assert register.offer(_directive("focus-orchid", 1, "host-default")) is None

    def test_the_same_directive_is_refused_by_an_unseeded_chain(self) -> None:
        """The contrast that makes the seeding load-bearing rather than tidy."""
        bare = ScopeRegister()
        rejection = bare.offer(_directive("focus-orchid", 1, "host-default"))
        assert rejection.code == "scope-directive-unknown-supersedes"

    def test_a_durable_store_is_replayed_onto_the_issued_chain(self, tmp_path: Path) -> None:
        store = tmp_path / "state.json"
        seeded = ScopeRegister(default=host.default_scope(), lane=LANE_DURABLE)
        seeded.offer(_directive("focus-orchid", 1, "host-default"))
        store.write_text(json.dumps(seeded.to_dict()), encoding="utf-8")
        register = host.issued_chain(self._args(state=store), host.Timeline(echo=_Sink()))
        assert register.active.scope_id == "focus-orchid"

    def test_a_broken_store_degrades_to_the_default_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        store = tmp_path / "state.json"
        store.write_text("{not json", encoding="utf-8")
        register = host.issued_chain(self._args(state=store), host.Timeline(echo=_Sink()))
        assert register.active.scope_id == "host-default"

    def test_the_session_lane_is_never_seeded_from_a_store(self, tmp_path: Path) -> None:
        store = tmp_path / "state.json"
        seeded = ScopeRegister(default=host.default_scope(), lane=LANE_DURABLE)
        seeded.offer(_directive("focus-orchid", 1, "host-default"))
        store.write_text(json.dumps(seeded.to_dict()), encoding="utf-8")
        argv = ["talk", "--state", str(store), "--session-scope"]
        args = host.build_parser().parse_args(argv)
        register = host.issued_chain(args, host.Timeline(echo=_Sink()))
        assert register.active.scope_id == "host-default"


class TestTheSnapshotCarriesTheVersionTheAuthorityAsksFor:
    """Without it a strategist must guess the number it has to beat."""

    def test_the_active_version_rides_the_resource_state(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        snapshot = host.session_projector(state)(_Context(active=host.default_scope()))
        assert snapshot.resource_state["active_scope_version"] == 0

    def test_no_active_scope_reports_minus_one_rather_than_zero(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        snapshot = host.session_projector(state)(_Context())
        assert snapshot.resource_state["active_scope_version"] == -1

    def test_a_version_bump_alone_moves_the_snapshot_id(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        project = host.session_projector(state)
        first = project(_Context(active=_directive("s", 1))).snapshot_id
        second = project(_Context(active=_directive("s", 2))).snapshot_id
        assert first != second
