"""The three-tier host, and the seam traps it was wired around (task ``t12``).

Every test here is **hermetic**. The host's only transport is
``examples/worker_seam.py``'s :class:`~examples.worker_seam.WorkerSeam`, and the
default arm never constructs one at all — the scripted seams stand in — so
``uv run pytest -n auto`` stays green on a machine that has never heard of a
lobes gateway. The live dial happens only when a person or an agent runs the CLI
with ``--live``, or under the ``EMBODIMENT_LIVE_RIG=1`` class at the bottom.

Two halves, and the second is the one ``t12`` actually owes.

The first half is ordinary: the host wires the three tiers, the arms are matched,
a degraded seat still leaves a running session, and the host introduces no clock.

The second half is :class:`TestTheSeamTraps`. ``t12``'s instruction was to wire
the tier *the way a stranger would* — from README, docstrings and ``pydoc``
alone — and to record every place that surface was not enough. Eight such places
came up, and each one is **reproduced against the real package here** rather than
asserted in a paragraph. That distinction is the whole point: issue #62 was a
documented-nowhere requirement that made a correct-looking wiring produce an
incapable tier, and the reason it cost a live session is that nothing executable
disagreed with the wiring. A trap nobody can demonstrate is a trap nobody has.
"""

from __future__ import annotations

import ast
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.capability import CapabilityCatalog
from embodiment.config_lifecycle import (
    ConfigLifecycle,
    PromptSection,
    SeatConfig,
    VerificationResult,
    compose_prompt,
)
from embodiment.config_report import build_config_report
from embodiment.config_review import ConfigSnapshot
from embodiment.config_run import ConfigGovernor, run_configured
from embodiment.config_runner import ConfigLimits, ConfigRunner
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.knowledge import knowledge_scope
from embodiment.loop import ToolOutcome
from embodiment.senses_text import KNOWLEDGE_ATTRIBUTION, SENSES_GROUNDING
from examples import three_tier as tt
from examples import worker_seam as ws

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = REPO_ROOT / "examples" / "three_tier.py"
SCRIPT = REPO_ROOT / "examples" / "three_tier_script.txt"


# ── shared fakes ─────────────────────────────────────────────────────────────


class StepActor:
    """An actor that makes *steps* tool calls and then finishes."""

    def __init__(self, steps: int = 2) -> None:
        self.steps = steps
        self.turn = 0
        self.prompts: list[str] = []

    def __call__(self, messages: list[dict[str, Any]], **_: Any) -> ModelResponse:
        for message in messages:
            if message.get("role") == "system":
                self.prompts.append(str(message.get("content") or ""))
        self.turn += 1
        if self.turn <= self.steps:
            return ModelResponse(
                content="working",
                tool_calls=[ToolCall(id=f"c{self.turn}", name="read_sensor", arguments={})],
            )
        return ModelResponse(content="done", tool_calls=[])


class OneTurnActor:
    """An actor that answers without ever calling a tool. Trap T1's subject."""

    def __init__(self) -> None:
        self.turn = 0

    def __call__(self, messages: list[dict[str, Any]], **_: Any) -> ModelResponse:
        self.turn += 1
        return ModelResponse(content="42% — no need to water.", tool_calls=[])


class NullExecutor:
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(result="ok")


def passing_verifier(_request: Any) -> VerificationResult:
    return VerificationResult(passed=True, summary="ok", suite="test", checks_run=1)


def prompt_writer(section: str = "hint") -> Any:
    """A strategist seam that proposes one real prompt change per review."""
    state = {"n": 0}

    def complete(_messages: list[dict[str, Any]], **_: Any) -> str:
        state["n"] += 1
        return json.dumps(
            {
                "changes": [
                    {
                        "change_id": f"probe-{state['n']}",
                        "target": "worker.prompts",
                        "origin": "strategist",
                        "section": section,
                        "text": f"Section written by review {state['n']}.",
                        "reason": "probe",
                    }
                ]
            }
        )

    return complete


def lane(
    *,
    reviewer: Optional[ConfigRunner] = None,
    projector: Any = None,
    seats: Any = None,
) -> tuple[ConfigLifecycle, ConfigGovernor]:
    catalog = tt.build_catalog()
    life = ConfigLifecycle(verifier=passing_verifier, catalog=catalog, seats=seats)
    governor = ConfigGovernor(lifecycle=life, reviewer=reviewer, projector=projector, seat="worker")
    return life, governor


def varying_projector(context: Any) -> ConfigSnapshot:
    return ConfigSnapshot(
        snapshot_id=f"s{context.step_count}-{id(context) % 997}",
        summary="probe",
        observations=(f"step {context.step_count}",),
        seats=(context.config,),
    )


def run_session(tmp_path: Path, *extra: str) -> dict[str, Any]:
    """Replay the shipped script through the CLI and return the JSON report."""
    report = tmp_path / "report.json"
    code = tt.main(["talk", "--script", str(SCRIPT), "--report", str(report), *extra])
    assert code == 0
    return json.loads(report.read_text(encoding="utf-8"))


# ── half one: the host ───────────────────────────────────────────────────────


class TestTheHostIntroducesNoClock:
    """``t12``'s clock criterion, met by REUSE rather than by a new constant.

    The proxied acting path streams, and both of its bounds are derived — but
    they are derived in ``worker_seam``, where ``tests/test_timeout_bounds.py``
    already pins them against the committed rate config. The strongest way to
    pass a clock audit is to add nothing it has to audit.
    """

    def test_the_module_declares_no_timeout_deadline_or_interval_constant(self) -> None:
        tree = ast.parse(HOST_SOURCE.read_text(encoding="utf-8"))
        hints = ("TIMEOUT", "DEADLINE", "INTERVAL", "WAIT")
        stray = [
            name.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for name in node.targets
            if isinstance(name, ast.Name) and any(hint in name.id for hint in hints)
        ]
        assert stray == [], f"three_tier introduces its own clock(s): {stray}"

    def test_the_capabilities_fetch_borrows_a_derived_bound(self) -> None:
        """Even the one non-model HTTP call takes a bound this repo derived."""
        source = HOST_SOURCE.read_text(encoding="utf-8")
        assert "timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT" in source

    def test_streaming_is_the_default_transport(self) -> None:
        parser = tt.build_parser()
        args = parser.parse_args(["talk"])
        assert args.stream is ws.DEFAULT_STREAM is True
        assert parser.parse_args(["talk", "--no-stream"]).stream is False

    def test_a_built_seam_streams_and_carries_both_derived_bounds(self) -> None:
        dial = tt.SeatDial(seat="actor", role=tt.WORKER_ROLE, model="m")
        seam = tt.build_seam(
            dial, gateway="http://localhost:8001", api_key="k", max_tokens=tt.ACTOR_MAX_TOKENS
        )
        assert seam.stream is True
        assert seam.transport == ws.TRANSPORT_STREAM
        expected = ws.StreamBounds.derived(dialled_width=tt.STREAM_QUEUE_WIDTH)
        assert seam.stream_bounds == expected
        # Phase one grows with the width actually dialled; phase two does not,
        # because an inter-chunk gap is a cadence and a queue is not.
        assert expected.first_chunk_s > ws.STREAM_FIRST_CHUNK_TIMEOUT
        assert expected.idle_s == ws.STREAM_IDLE_TIMEOUT

    def test_the_actor_budget_is_d16s_floor(self) -> None:
        """#37: no ``finish_reason`` reaches the loop, so truncation is invisible."""
        assert tt.ACTOR_MAX_TOKENS == 16000

    def test_the_senses_budget_is_deliberately_small_and_that_is_not_an_oversight(self) -> None:
        assert tt.SENSES_MAX_TOKENS < tt.ACTOR_MAX_TOKENS
        assert "dead air" in tt.__doc__ or "dead air" in HOST_SOURCE.read_text(encoding="utf-8")


class TestTheThreeTiers:
    def test_a_scripted_session_runs_all_three_tiers(self, tmp_path: Path) -> None:
        report = run_session(tmp_path)
        assert report["arm"].startswith("governed")
        assert len(report["drives"]) == 3
        assert all(drive["exit_reason"] == "finished" for drive in report["drives"])
        # tier 3 actually changed tier 2's configuration
        assert any(drive["applied"] for drive in report["drives"])
        assert report["strategist"]["counts"]["reviews_started"] > 0

    def test_the_worker_writes_senses_knowledge_and_every_entry_is_attributed(
        self, tmp_path: Path
    ) -> None:
        report = run_session(tmp_path)
        entries = report["senses_knowledge"]
        assert entries, "the worker never reached the knowledge channel"
        assert all(entry["origin"] == "worker" for entry in entries)

    def test_the_worker_cannot_reach_the_senses_prompt(self) -> None:
        """The authority lattice, refused whole rather than filtered."""
        life, _ = lane()
        refused = life.propose(
            {
                "change_id": "w1",
                "target": "senses.prompts",
                "origin": "worker",
                "section": "voice",
                "text": "speak differently",
                "reason": "probe",
            }
        )
        assert refused is None
        codes = [degradation.code for degradation in life.degradations]
        assert "config-change-origin-forbidden" in codes

    def test_the_senses_prompt_carries_the_measured_grounding_clause_verbatim(self) -> None:
        config = SeatConfig(seat="senses", prompt=(PromptSection(section="base", text="Voice."),))
        prompt = tt.compose_senses_prompt(config)
        assert SENSES_GROUNDING in prompt
        # 0/16 vs 16/16 was measured on THESE words, not on the idea behind them.
        assert KNOWLEDGE_ATTRIBUTION not in prompt, "no block, so no attribution clause"

    def test_the_attribution_clause_arrives_with_the_knowledge_block(self) -> None:
        from embodiment.config_lifecycle import KnowledgeEntry

        config = SeatConfig(seat="senses", prompt=(PromptSection(section="base", text="Voice."),))
        prompt = tt.compose_senses_prompt(
            config, knowledge=(KnowledgeEntry(entry_id="k", text="42%", origin="worker"),)
        )
        assert KNOWLEDGE_ATTRIBUTION in prompt
        assert "written by: worker" in prompt

    def test_the_inner_state_reports_system_facts_and_never_a_feeling(self) -> None:
        """Constraint C2: what is relayed outward is degradations, budget, work."""
        session = _session()
        record = tt.DriveRecord(instruction="q", exit_reason="finished", summary="s", steps=2)
        state = session._inner_state(record)
        for banned in ("feel", "happy", "worried", "excited", "sad"):
            assert banned not in state.lower()
        assert "drive:" in state
        assert "result:" in state


class TestTheArmsAreMatched:
    """The control criterion: one script, two arms, one difference."""

    def test_the_matched_control_starts_from_the_identical_baseline(self, tmp_path: Path) -> None:
        governed = run_session(tmp_path / "a")
        control = run_session(tmp_path / "b", "--no-strategist")
        assert control["arm"].startswith("matched control")
        assert control["strategist"] is None
        # the ONE difference: the governed arm's prompt carries what the
        # strategist added, and the control's is exactly the seeded baseline.
        assert tt.WORKER_BASE_PROMPT in governed["worker_prompt"]
        assert control["worker_prompt"].strip() == tt.WORKER_BASE_PROMPT
        assert len(governed["worker_prompt"]) > len(control["worker_prompt"])

    def test_the_control_still_keeps_the_lane_the_ledger_and_the_channel(
        self, tmp_path: Path
    ) -> None:
        """Matched means ONE difference. Removing the lane too is the other control."""
        control = run_session(tmp_path, "--no-strategist")
        assert control["senses_knowledge"], "the worker→senses channel is part of the baseline"
        assert control["effective_config"]["seats"], "the ledger still explains the baseline"

    def test_the_ungoverned_arm_removes_the_layer_as_well(self, tmp_path: Path) -> None:
        ungoverned = run_session(tmp_path, "--ungoverned")
        assert ungoverned["arm"].startswith("ungoverned")
        assert ungoverned["worker_prompt"] == ""
        assert ungoverned["senses_knowledge"] == []

    def test_every_arm_replays_the_same_script_to_the_same_answers(self, tmp_path: Path) -> None:
        """The scripted world is deterministic, so a difference is attributable."""
        arms = [
            run_session(tmp_path / "g"),
            run_session(tmp_path / "c", "--no-strategist"),
            run_session(tmp_path / "u", "--ungoverned"),
        ]
        summaries = [[drive["summary"] for drive in arm["drives"]] for arm in arms]
        assert summaries[0] == summaries[1] == summaries[2]


class TestDegradationIsVisibleAndNeverFatal:
    """Constraint C3, on the seat the operator's instruction singled out."""

    def test_an_unready_senses_role_is_recorded_and_the_host_still_runs(self) -> None:
        resolution = tt.resolve_seats(
            {
                "senses": {"ready": False, "model": "gemma"},
                "worker": {"ready": True, "model": "qwen-a3b"},
                "cortex": {"ready": True, "model": "qwen-27b"},
            }
        )
        assert not resolution.senses_ready
        assert resolution.actor_ready
        assert resolution.strategist_ready
        codes = [code for code, _ in resolution.degradations]
        assert "seat-senses-not-ready" in codes

    def test_an_absent_role_is_a_named_degradation_not_an_exception(self) -> None:
        resolution = tt.resolve_seats({"worker": {"ready": True, "model": "m"}})
        assert [code for code, _ in resolution.degradations] == [
            "seat-senses-absent",
            "seat-strategist-absent",
        ]

    def test_a_malformed_advert_degrades_rather_than_raises(self) -> None:
        resolution = tt.resolve_seats({"senses": "not a mapping", "worker": {"ready": True}})
        assert "seat-senses-malformed" in [code for code, _ in resolution.degradations]

    def test_hostile_capabilities_payloads_never_raise(self) -> None:
        for payload in ({}, {"roles": None}, {"roles": {"worker": None}}):
            assert isinstance(tt.resolve_seats(payload), tt.SeatResolution)

    def test_a_session_with_no_senses_seat_still_answers(self) -> None:
        session = _session(senses=None)
        record = tt.DriveRecord(instruction="q", exit_reason="finished", summary="42% moisture")
        assert session.say(record) == "42% moisture"

    def test_a_failing_senses_seat_falls_back_and_records_once(self) -> None:
        def dead(_messages: list[dict[str, Any]]) -> ModelResponse:
            raise ws.WorkerTransportError("gateway down")

        session = _session(senses=dead)
        record = tt.DriveRecord(instruction="q", exit_reason="finished", summary="42% moisture")
        assert session.say(record) == "42% moisture"
        assert session.senses_degraded is True
        kinds = [(entry.stream, entry.kind) for entry in session.timeline.entries]
        assert ("senses", "say-fallback") in kinds

    def test_an_advisory_era_state_file_degrades_visibly_and_the_session_runs(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        """``c31``/``h21`` through a host, against the REAL committed payload.

        A host pointed at an advisory-era ``--state`` file must get one recorded
        degradation naming the fix — never a silent reinterpretation of a
        directive chain under configuration semantics, and never a crash.
        """
        advisory = REPO_ROOT / "docs" / "live-test-results" / "scope-live-session-1-state.json"
        state = tmp_path / "advisory.json"
        state.write_text(advisory.read_text(encoding="utf-8"), encoding="utf-8")

        report = run_session(tmp_path, "--state", str(state))
        captured = capsys.readouterr()
        assert "config-ledger-unknown-schema-version" in captured.err
        assert "advisory-era" in captured.err
        assert report["drives"], "and the session still ran"

    def test_a_fresh_state_file_carries_this_schemas_own_marker(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        run_session(tmp_path, "--state", str(state))
        payload = json.loads(state.read_text(encoding="utf-8"))
        assert payload["kind"] == "config-ledger"
        assert [entry["change_id"] for entry in payload["entries"]][:1] == ["seed-worker-prompt"]

    def test_the_operators_words_survive_a_failed_intake_verbatim(self) -> None:
        def dead(_messages: list[dict[str, Any]]) -> ModelResponse:
            raise ws.WorkerTransportError("gateway down")

        session = _session(senses=dead)
        text = "  Does fern-bed need water?  "
        packet = session.hear(text)
        assert packet.original == text, "the verbatim invariant is the whole point"


class TestTheGateIsRealPerChangeType:
    def test_a_failing_suite_never_applies_and_is_recorded(self) -> None:
        catalog = tt.build_catalog()
        verify = tt.build_verifier(catalog)
        life = ConfigLifecycle(verifier=verify, catalog=catalog)
        life.propose(
            {
                "change_id": "seed",
                "target": "worker.prompts",
                "origin": "host",
                "section": "base",
                "text": tt.WORKER_BASE_PROMPT,
                "reason": "seed",
            }
        )
        life.advance()
        # a tools selection that drops `finish` leaves a seat that cannot end a drive
        life.propose(
            {
                "change_id": "no-finish",
                "target": "worker.tools",
                "origin": "strategist",
                "capability_ids": ["read_sensor"],
                "reason": "probe",
            }
        )
        life.advance()
        proposal = life.proposal("no-finish")
        assert proposal is not None
        assert proposal.state == "rejected"
        assert life.effective("worker").tools != ("read_sensor",)

    def test_a_runaway_prompt_is_refused_by_the_growth_bound(self) -> None:
        catalog = tt.build_catalog()
        life = ConfigLifecycle(verifier=tt.build_verifier(catalog), catalog=catalog)
        life.propose(
            {
                "change_id": "seed",
                "target": "worker.prompts",
                "origin": "host",
                "section": "base",
                "text": tt.WORKER_BASE_PROMPT,
                "reason": "seed",
            }
        )
        life.advance()
        life.propose(
            {
                "change_id": "huge",
                "target": "worker.prompts",
                "origin": "strategist",
                "section": "essay",
                "text": "x" * (tt.PROMPT_GROWTH_LIMIT + 50),
                "reason": "probe",
            }
        )
        life.advance()
        assert (life.proposal("huge") or SeatConfig()).state == "rejected"

    def test_the_ratchet_sees_drift_no_individual_gate_could(self) -> None:
        """Three changes each within the bound, cumulatively past it."""
        catalog = tt.build_catalog()
        life = ConfigLifecycle(verifier=tt.build_verifier(catalog), catalog=catalog)
        life.propose(
            {
                "change_id": "seed",
                "target": "worker.prompts",
                "origin": "host",
                "section": "base",
                "text": tt.WORKER_BASE_PROMPT,
                "reason": "seed",
            }
        )
        life.advance()
        from embodiment.config_revert import ConfigBaseline, RatchetGuard

        baseline = ConfigBaseline()
        baseline.capture(life, "worker")
        guard = RatchetGuard(baseline)
        chunk = tt.PROMPT_GROWTH_LIMIT // 2
        for index in range(3):
            life.propose(
                {
                    "change_id": f"grow-{index}",
                    "target": "worker.prompts",
                    "origin": "strategist",
                    "section": f"s{index}",
                    "text": "y" * chunk,
                    "reason": "probe",
                }
            )
            life.advance()
            assert (life.proposal(f"grow-{index}") or SeatConfig()).state == "applied"
        result = guard.check(life, "worker")
        assert result.passed is False, "the ratchet must catch what the per-change gate cannot"
        assert guard.degradations

    def test_revert_restores_the_baseline(self, tmp_path: Path) -> None:
        session = _session()
        session.seed()
        before = compose_prompt(session.lifecycle.effective("worker"))
        session.lifecycle.propose(
            {
                "change_id": "drift",
                "target": "worker.prompts",
                "origin": "strategist",
                "section": "drift",
                "text": "Some drift.",
                "reason": "probe",
            }
        )
        session.lifecycle.advance()
        assert compose_prompt(session.lifecycle.effective("worker")) != before
        outcome = session.revert()
        assert outcome is not None
        assert compose_prompt(session.lifecycle.effective("worker")) == before


class TestTheReportIsLedgerDerived:
    def test_the_report_explains_the_whole_configuration(self, tmp_path: Path) -> None:
        report = run_session(tmp_path)
        seats = {seat["seat"]: seat for seat in report["effective_config"]["seats"]}
        assert "base" in [section["section"] for section in seats["worker"]["prompt"]]
        assert report["effective_config"]["unexplained"] == []

    def test_every_element_names_the_change_that_produced_it(self, tmp_path: Path) -> None:
        report = run_session(tmp_path)
        seats = {seat["seat"]: seat for seat in report["effective_config"]["seats"]}
        for section in seats["worker"]["prompt"]:
            assert section["provenance"]["change_id"]
            assert section["provenance"]["state"] == "applied"

    def test_the_ledger_digest_matches_the_live_lifecycle(self) -> None:
        """T8's fix, stated as the property the report's own docstring claims."""
        session = _session()
        report = build_config_report(session.ledger, seats=("worker", "senses"))
        for seat in ("worker", "senses"):
            assert report.seat(seat) is not None
            assert report.seat(seat).config_sha == session.lifecycle.effective(seat).config_sha


class TestTheCli:
    def test_seats_reports_without_dialling_anything(self, capsys: Any) -> None:
        assert tt.main(["seats"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["dials"]) == {"senses", "actor", "strategist"}

    def test_traps_is_a_first_class_verb(self, capsys: Any) -> None:
        assert tt.main(["traps"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert {trap["id"] for trap in payload} == {trap.id for trap in tt.SEAM_TRAPS}

    def test_live_is_opt_in_with_no_default_anywhere(self) -> None:
        assert tt.build_parser().parse_args(["talk"]).live is False
        source = HOST_SOURCE.read_text(encoding="utf-8")
        assert "default=True" not in source.replace("default=ws.DEFAULT_STREAM", "")

    def test_a_dead_gateway_exits_two_with_a_hint_and_no_traceback(
        self, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ws.API_KEY_ENV, "k")
        code = tt.main(["seats", "--live", "--gateway", "http://127.0.0.1:1"])
        assert code == 2
        captured = capsys.readouterr()
        assert captured.err.startswith("error: ")
        assert "hint:" in captured.err
        assert "Traceback" not in captured.err

    def test_the_shipped_script_is_replayable(self) -> None:
        assert SCRIPT.exists()
        lines = list(tt._script_lines(SCRIPT))
        assert lines
        assert all(not line.startswith("#") for line in lines)


class TestTheHostShipsNoSecondTransportAndNoShell:
    def test_the_tool_surface_offers_nothing_dangerous(self) -> None:
        assert set(tt.TOOL_IDS) == {"read_sensor", "log_care", "tell_senses", "finish"}

    def test_the_only_transport_is_worker_seam(self) -> None:
        tree = ast.parse(HOST_SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert "subprocess" not in imported
        assert "socket" not in imported
        assert "http.client" not in imported
        # urllib is present for exactly one thing: the /capabilities advert.
        source = HOST_SOURCE.read_text(encoding="utf-8")
        assert source.count("urlopen") == 1

    def test_the_host_does_not_import_the_sibling_scope_lane(self) -> None:
        """t13 owns examples/scope/. A live host coupling to it would break on merge."""
        source = HOST_SOURCE.read_text(encoding="utf-8")
        assert "examples.scope" not in source
        assert "from examples import scope" not in source


# ── half two: the seam traps, reproduced ─────────────────────────────────────


class TestTheSeamTraps:
    """Each recorded trap, demonstrated against the real package.

    These are not tests of ``three_tier``. They are tests of the *claim* each
    :class:`~examples.three_tier.SeamTrap` makes about the package's documented
    seam — so if a later release fixes one, the test goes red and the trap comes
    off the list instead of quietly outliving the problem it names.
    """

    def test_the_module_docstring_states_the_number_of_traps_it_holds(self) -> None:
        """The prose count and the data cannot drift apart.

        They already did once: the docstring said six while ``SEAM_TRAPS`` held
        eight, both landing in the same commit. Nothing checked the number, so
        the only reader who would have caught it is one who counted.
        """
        spelled = {
            1: "One",
            2: "Two",
            3: "Three",
            4: "Four",
            5: "Five",
            6: "Six",
            7: "Seven",
            8: "Eight",
            9: "Nine",
            10: "Ten",
        }
        count = len(tt.SEAM_TRAPS)
        # The docstring is wrapped, so the sentence spans a line break.
        docstring = " ".join((tt.__doc__ or "").split())
        expected = f"{spelled[count]} such questions came up."
        assert (
            expected in docstring
        ), f"the module docstring must say {expected!r} — SEAM_TRAPS holds {count}"

    def test_every_trap_is_reproduced_by_a_test_in_this_class(self) -> None:
        """The list and the proofs cannot drift apart."""
        proved = {
            name.split("_")[1].upper()
            for name in dir(self)
            if name.startswith("test_t") and name.split("_")[0] == "test"
        }
        recorded = {trap.id for trap in tt.SEAM_TRAPS}
        assert recorded <= proved, f"traps with no reproduction: {sorted(recorded - proved)}"

    def test_t1_a_drive_with_no_tool_call_still_reviews_at_the_drive_end(self) -> None:
        """T1, FIXED — and this is the regression test that replaced the trap.

        The trap was: ``_offer`` was reached only from ``_note_step``, the
        per-TOOL-step observer, so a drive whose actor answered in one turn
        without calling a tool projected nothing, reviewed nothing and proposed
        nothing — every counter zero while ``governor.armed`` read ``True`` and
        no degradation was recorded anywhere. A conversational host answers many
        turns exactly that way, so the tier was dead for a whole class of host.

        ``finish`` now takes the drive's end as a boundary. One tool-less drive
        is one boundary, not zero.
        """
        reviewer = ConfigRunner(prompt_writer(), role="cortex", limits=ConfigLimits(review_gap=0))
        life, governor = lane(reviewer=reviewer, projector=varying_projector)
        assert governor.armed is True

        outcome = run_configured(
            OneTurnActor(),
            Task(id="t", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=6,
            governor=governor,
        )
        reviewer.wait_idle(10.0)
        assert outcome.counts["steps_observed"] == 0, "still no tool step — that is the point"
        assert outcome.counts["boundaries_projected"] == 1, "the drive's end IS a boundary"
        assert outcome.counts["snapshots_offered"] == 1
        assert reviewer.counts["reviews_started"] == 1, "and the reviewer actually looked"
        assert outcome.degradations == ()
        reviewer.close()
        assert life is not None

    def test_t1_a_purely_conversational_host_actually_gets_configured(self) -> None:
        """The claim T1 is really about: a projection nobody applies is still dead.

        A counter that increments is not proof a tier is alive — that is this
        cycle's own lesson. So this drives a tool-less actor five turns, as a
        chat host would, and asks whether configuration LANDS. It does, one turn
        later than it is proposed: the review is asynchronous and the gate runs
        before it finishes, so turn N's projection configures turn N+1. Turn 0
        therefore applies nothing, and that lag is asserted rather than hidden.
        """
        reviewer = ConfigRunner(prompt_writer(), role="cortex", limits=ConfigLimits(review_gap=0))
        life, governor = lane(reviewer=reviewer, projector=varying_projector)
        actor = OneTurnActor()

        applied_per_turn = []
        for index in range(5):
            outcome = run_configured(
                actor,
                Task(id=f"turn{index}", repo_path=".", instruction="q"),
                executor=NullExecutor(),
                max_steps=6,
                governor=governor,
            )
            reviewer.wait_idle(10.0)
            applied_per_turn.append(len(outcome.applied))

        assert applied_per_turn[0] == 0, "turn 0's review cannot have finished before its own gate"
        assert sum(applied_per_turn) >= 3, (
            f"a conversational host must actually get configured; applied per turn "
            f"was {applied_per_turn}"
        )
        assert len(life.effective("worker").prompt) >= 1, "and the configuration is EFFECTIVE"
        reviewer.close()

    def test_the_default_cadence_no_longer_starves_a_multi_drive_host(self) -> None:
        """T2, FIXED — and this is the regression test that replaced the trap.

        The trap was: ``review_gap`` defaults to 2, ``run_configured`` supplies a
        per-drive step index that restarts, and the runner's cadence memory did
        not — so ``step_index - _last_review_step`` went negative and blocked
        every review after the first. Measured at six drives producing ONE
        review, 11 of 12 snapshots skipped (embodiment#79, found independently
        by review on PR #81).

        The runner now reads a counter going BACKWARDS as a restarted sequence
        rather than as "no steps have passed". A host that wires everything
        correctly and passes no limits gets a review per drive.
        """

        def drive_six(limits: Optional[ConfigLimits]) -> dict[str, int]:
            reviewer = ConfigRunner(prompt_writer(), role="cortex", limits=limits)
            _, governor = lane(reviewer=reviewer, projector=varying_projector)
            for index in range(6):
                run_configured(
                    StepActor(steps=2),
                    Task(id=f"t{index}", repo_path=".", instruction="q"),
                    executor=NullExecutor(),
                    max_steps=6,
                    governor=governor,
                )
                reviewer.wait_idle(10.0)
            counts = dict(reviewer.counts)
            reviewer.close()
            return counts

        # The DEFAULT limits — the wiring a stranger writes.
        default = drive_six(None)

        # The cadence DECISION is synchronous (it happens in consider(), on the
        # actor's thread), so this count is deterministic and is the assertion
        # that carries the claim. The trap was skipping ACROSS drives, and that
        # is what is gone.
        #
        # That "deterministic" was FALSE when first written and this assertion
        # was ~27% flaky (11 of 40 unloaded trials returned 5, and it went red
        # on CI): the gate read `reviews_started` and `_last_review_step`, both
        # written by the REVIEW thread in `_take`, so drive 0's second snapshot
        # was only skipped when the worker happened to have dequeued already.
        # `_cadence_blocks` now reads actor-thread state only, which is what
        # makes the sentence above true rather than aspirational. The guard in
        # tests/test_config_runner.py pins that structurally.
        #
        # The expected value is DERIVED, not observed-and-pasted. Six drives now
        # produce three boundaries each — step 1, step 2, and the drive's end
        # (T1's fix) — so 18 offers, of which 7 are skipped:
        #
        #   drive 0: step 1 is the first offer ever, so it is ungated and sets
        #            the reference to 1; step 2 (2-1=1 < gap 2) and the drive-end
        #            offer (also index 2) are both skipped         -> 2 skips
        #   drives 1-5: the previous drive's reference sits at index 2, so this
        #            drive's step-1 offer restarts it to 0 and is skipped
        #            (1-0=1 < 2); step 2 and the drive-end offer then both clear
        #            the gap                                       -> 1 skip each
        #
        # 2 + 5x1 = 7, measured at 40 of 40 trials. Under the trap it was 11 of
        # 12 — starvation across drives, which is what these numbers refute.
        assert default["snapshots_skipped_cadence"] == 7, (
            "11 of 12 would be the T2 starvation back; 7 of 18 is one skipped "
            "snapshot per drive plus drive 0's extra, which is the cadence working"
        )
        # reviews_started is incremented on the REVIEW thread, so it is timing
        # sensitive under a loaded parallel run — asserted as a floor rather
        # than an equality, because the claim is "every drive gets reviewed",
        # not "the last one had finished starting when we looked".
        assert (
            default["reviews_started"] >= 5
        ), "one review for six drives would be the T2 starvation back"

        # The old mitigation still works and is now merely redundant: gap=0
        # reviews every snapshot, so nothing is skipped at all.
        explicit = drive_six(ConfigLimits(review_gap=0))
        assert explicit["reviews_started"] >= 6
        assert explicit["snapshots_skipped_cadence"] == 0

    def test_t3_a_prompt_change_replaces_the_loops_own_default_prompt(self) -> None:
        """The acting seat silently loses its base framing."""
        reviewer = ConfigRunner(prompt_writer(), role="cortex", limits=ConfigLimits(review_gap=0))
        life, governor = lane(reviewer=reviewer, projector=varying_projector)
        actor = StepActor(steps=2)
        run_configured(
            actor,
            Task(id="t0", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=6,
            governor=governor,
        )
        first = actor.prompts[0]
        assert "agent working through a task" in first, "the loop's own default prompt"

        # Settle deterministically rather than racing the reviewer thread: this
        # is exactly what ThreeTierSession.settle does, and it is what makes the
        # NEXT drive's prompt a fact about configuration rather than about timing.
        reviewer.wait_idle(10.0)
        for outcome in reviewer.drain():
            for change in outcome.changes:
                life.propose(change)
        life.advance()
        assert compose_prompt(life.effective("worker")), "a prompt section is now in force"

        actor.prompts.clear()
        run_configured(
            actor,
            Task(id="t1", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=6,
            governor=governor,
        )
        second = actor.prompts[0]
        assert "Section written by review" in second
        assert (
            "agent working through a task" not in second
        ), "the trap: one prompt section, and the loop's whole default framing is gone"
        reviewer.close()

        # the mitigation: seed a base section, and it survives every later change.
        assert tt.WORKER_BASE_PROMPT in compose_prompt(_session().lifecycle.effective("worker"))

    def test_t4_a_host_system_prompt_is_concatenated_not_forwarded(self) -> None:
        life, governor = lane(
            seats={
                "worker": SeatConfig(
                    seat="worker", prompt=(PromptSection(section="cfg", text="CONFIG TEXT"),)
                )
            }
        )
        actor = StepActor(steps=1)
        run_configured(
            actor,
            Task(id="t", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=4,
            governor=governor,
            system_prompt="HOST TEXT",
        )
        seen = actor.prompts[0]
        assert (
            seen == "HOST TEXT\n\nCONFIG TEXT"
        ), "'forwarded verbatim' reads as 'the host's prompt is what the actor sees'"
        assert life is not None

    def test_t5_the_knowledge_scope_agent_defaults_to_default(self) -> None:
        assert knowledge_scope("senses.knowledge") == "default.knowledge.senses"
        assert knowledge_scope("senses.knowledge", agent="embodiment") == (
            "embodiment.knowledge.senses"
        )
        # the host resolves it explicitly rather than taking the default.
        assert "resolve_identity(" in HOST_SOURCE.read_text(encoding="utf-8")

    def test_t6_the_last_review_of_a_session_is_never_applied(self) -> None:
        """A review that lands after the drive is the one most likely to be lost.

        Gated with an :class:`threading.Event` rather than raced against a
        thread: the reviewer cannot answer until this test says so, and it says
        so only after ``run_configured`` has returned. What is proved is then a
        fact about the composition's ordering rather than about scheduling luck.
        """
        released = threading.Event()
        inner = prompt_writer()

        def gated(messages: list[dict[str, Any]], **kwargs: Any) -> str:
            released.wait(20.0)
            return inner(messages, **kwargs)

        reviewer = ConfigRunner(gated, role="cortex", limits=ConfigLimits(review_gap=0))
        life, governor = lane(reviewer=reviewer, projector=varying_projector)
        run_configured(
            StepActor(steps=2),
            Task(id="t", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=6,
            governor=governor,
        )
        assert life.proposals() == (), "no review could possibly have answered yet"

        # the review answers only now — after the drive's trailing advance
        released.set()
        reviewer.wait_idle(20.0)
        pending = reviewer.drain()
        assert pending, "a completed review nothing has yet proposed"
        stranded = {change.change_id for outcome in pending for change in outcome.changes}
        assert stranded, "the trap's subject: changes decided after the last advance"
        assert life.proposals() == (), (
            "these are changes the drive never saw — close() would record them as late "
            "drops and the last decision of a session would be the one lost"
        )

        # the mitigation: settle by hand.
        for outcome in pending:
            for change in outcome.changes:
                life.propose(change)
        life.advance()
        landed = {proposal.change.change_id for proposal in life.proposals()}
        assert stranded <= landed, "settling is what makes the last decision count"
        reviewer.close()

    def test_t7_an_unchanged_snapshot_is_skipped_rather_than_reviewed(self) -> None:
        reviewer = ConfigRunner(prompt_writer(), role="cortex", limits=ConfigLimits(review_gap=0))

        def stable(context: Any) -> ConfigSnapshot:
            return ConfigSnapshot(snapshot_id="always-the-same", summary="stable")

        _, governor = lane(reviewer=reviewer, projector=stable)
        outcome = run_configured(
            StepActor(steps=3),
            Task(id="t", repo_path=".", instruction="q"),
            executor=NullExecutor(),
            max_steps=8,
            governor=governor,
        )
        # Four boundaries for three tool steps: the drive's end is the fourth
        # (T1's fix). A stable projector still costs exactly one review — which
        # is the whole claim of T7, and the drive-end boundary does not weaken it.
        assert outcome.counts["boundaries_projected"] == 4
        assert outcome.counts["snapshots_unchanged"] >= 3
        assert outcome.counts["snapshots_offered"] == 1
        reviewer.close()

    def test_t8_a_constructor_seeded_baseline_is_invisible_to_the_report(self) -> None:
        """T3's documented mitigation breaks the report's documented parity claim."""
        catalog: CapabilityCatalog = tt.build_catalog()
        seeded = ConfigLifecycle(
            verifier=passing_verifier,
            catalog=catalog,
            seats={
                "worker": SeatConfig(
                    seat="worker",
                    prompt=(PromptSection(section="base", text=tt.WORKER_BASE_PROMPT),),
                )
            },
        )
        from embodiment.config_ledger import ConfigLedger

        ledger = ConfigLedger()
        report = build_config_report(ledger, seats=("worker",))
        assert (
            report.seat("worker").config_sha != seeded.effective("worker").config_sha
        ), "the report claims its digest is the same one a live lifecycle computes"
        assert (
            report.seat("worker").unexplained == ()
        ), "and nothing is recorded as unexplained — the gap is silent"

        # the mitigation: seed through the gate, and the parity claim holds.
        session = _session()
        gated = build_config_report(session.ledger, seats=("worker",))
        assert gated.seat("worker").config_sha == session.lifecycle.effective("worker").config_sha

    def test_every_trap_names_a_seam_a_symptom_and_a_fix(self) -> None:
        for trap in tt.SEAM_TRAPS:
            assert trap.id
            assert trap.seam.strip()
            assert trap.symptom.strip()
            assert trap.fix.strip()
        incapable = [trap.id for trap in tt.SEAM_TRAPS if trap.incapable_tier]
        assert incapable == [], (
            "no recorded trap yields an incapable tier any more — T2 was FIXED on "
            "PR #81 and T1 after it, and both came off the list. What is left are "
            f"seam GAPS, which are under-documented rather than dead: {incapable}. "
            "A new trap that makes the tier propose nothing belongs here with "
            "incapable_tier=True, and this assertion is what forces that choice."
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _session(senses: Any = "scripted") -> tt.ThreeTierSession:
    """A seeded session with no strategist, for unit-level assertions."""
    from embodiment.config_ledger import ConfigLedger

    shed = tt.Shed()
    catalog = tt.build_catalog()
    timeline = tt.Timeline()
    ledger = ConfigLedger()
    lifecycle = ConfigLifecycle(verifier=tt.build_verifier(catalog), catalog=catalog)
    return tt.ThreeTierSession(
        shed=shed,
        lifecycle=lifecycle,
        ledger=ledger,
        strategist=None,
        catalog=catalog,
        timeline=timeline,
        actor_seam=tt.scripted_actor(shed),
        senses_seam=tt.scripted_senses() if senses == "scripted" else senses,
        max_steps=8,
        agent="embodiment",
    )


# ── the live rig, opt-in and skipped by default ──────────────────────────────

LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get(ws.API_KEY_ENV, "")


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{ws.API_KEY_ENV} is not set")
class TestLiveThreeTier:
    """The real dial. Small and cheap, and it says what it found either way."""

    def test_the_gateway_declares_the_three_roles_this_host_needs(self) -> None:
        capabilities = tt.fetch_capabilities(
            tt.DEFAULT_GATEWAY, timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT
        )
        resolution = tt.resolve_seats(capabilities)
        assert (
            resolution.actor_ready
        ), f"the worker role is the acting seat and did not resolve: {resolution.to_dict()}"
        # senses was ready=false at last check. That is reported, never asserted.
        if not resolution.senses_ready:
            pytest.skip(f"the senses seat is unavailable: {resolution.degradations}")

    def test_a_governed_session_runs_against_the_real_rig(self, tmp_path: Path) -> None:
        report = tmp_path / "live.json"
        code = tt.main(
            [
                "talk",
                "--live",
                "--script",
                str(SCRIPT),
                "--report",
                str(report),
                "--max-steps",
                "6",
            ]
        )
        assert code == 0
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["drives"]
        assert payload["strategist"] is None or payload["strategist"]["degradation"] is None
