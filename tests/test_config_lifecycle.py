"""Propose → verify → apply, and the per-seat quiescence gate (task ``t4``).

This is the contract test for :mod:`embodiment.config_lifecycle`, which encodes
one operator decision verbatim — *"mid drive — code makes a prompt change a
proposal, a test suite verifies it, and the proposal is in when the affected
subagent is available for test suite + not in a run and test suite passed"* — and
one plan instruction: *the drone stage→smoke→save flow is the pattern, moved to
runtime.*

Three acceptance criteria, each pinned by a named class:

1. **propose / verified / applied are distinct recorded states, and apply is
   gated on seat-idle AND suite-pass.**
   → :class:`TestDistinctRecordedStates`, :class:`TestSeatQuiescenceGate`.
2. **A mid-run seat never observes a config change: config identity is constant
   within any single run.**
   → :class:`TestPerSeatConstancy`, which is deliberately built as the per-seat
   analogue of the way live session 1 proved the constant system-prompt sha: a
   probe hashes the bytes the seat actually receives on every turn of a drive
   and counts rewrites. :class:`TestTheConstancyProbeCanFire` is the
   test-of-the-test — a deliberately miswired seat, and the probe goes red.
3. **A proposal whose verification suite fails is never applied and the failure
   is recorded, like a failed drone smoke.**
   → :class:`TestVerificationGate`.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from embodiment.capability import (
    CAPABILITY_KIND_PERMISSION,
    CAPABILITY_KIND_TOOL,
    Capability,
    CapabilityCatalog,
)
from embodiment.config_change import (
    CHANGE_STALE_CATALOG,
    CHANGE_UNITS,
    CHANGE_UNKNOWN_CAPABILITY,
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    SEAT_SENSES,
    SEAT_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_WORKER_PERMISSIONS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    ConfigChange,
    ConfigDegradation,
    ConfigRefusal,
    KnowledgeChange,
    PromptChange,
    SensesKnowledgeChange,
    WorkerPromptChange,
    WorkerToolsChange,
    change_from_payload,
)
from embodiment.config_lifecycle import (
    CHANGE_ALREADY_APPLIED,
    CHANGE_DUPLICATE_PROPOSAL,
    CHANGE_SEAT_BUSY,
    CHANGE_STALE_VERIFICATION,
    CHANGE_STATES,
    CHANGE_TERMINAL,
    CHANGE_UNKNOWN_PROPOSAL,
    CHANGE_UNKNOWN_RUN,
    CHANGE_UNKNOWN_SEAT,
    CHANGE_UNVERIFIED,
    CHANGE_VERIFICATION_FAILED,
    CHANGE_VERIFIER_UNAVAILABLE,
    CHANGE_VERIFIER_UNREADABLE,
    LIFECYCLE_CODES,
    LIFECYCLE_DEFERRAL_CODES,
    LIFECYCLE_REFUSAL_CODES,
    LIFECYCLE_RULES,
    STATE_APPLIED,
    STATE_PROPOSED,
    STATE_REJECTED,
    STATE_VERIFIED,
    TERMINAL_STATES,
    ConfigDeferral,
    ConfigLifecycle,
    ConfigTransition,
    KnowledgeEntry,
    PromptSection,
    SeatConfig,
    SeatRun,
    VerificationRequest,
    VerificationResult,
    apply_change,
    compose_prompt,
)

MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "config_lifecycle.py"


# ── fixtures: payloads and verifiers, never a live seat ──────────────────────


def _catalog(*, catalog_id: str = "greenhouse-1", extra: bool = False) -> CapabilityCatalog:
    entries = [
        Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL),
        Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
        Capability(capability_id="net.egress", kind=CAPABILITY_KIND_PERMISSION),
    ]
    if extra:
        entries.append(Capability(capability_id="fs.delete", kind=CAPABILITY_KIND_TOOL))
    return CapabilityCatalog(catalog_id=catalog_id, entries=tuple(entries))


def _prompt(change_id: str = "chg-1", text: str = "Read a file once.", **over: Any) -> dict:
    payload = {
        "target": TARGET_WORKER_PROMPTS,
        "change_id": change_id,
        "origin": ORIGIN_STRATEGIST,
        "reason": "the worker keeps re-reading the same file",
        "section": "working-style",
        "text": text,
    }
    payload.update(over)
    return payload


def _tools(change_id: str = "chg-tools", ids: Any = ("fs.read",), **over: Any) -> dict:
    payload = {
        "target": TARGET_WORKER_TOOLS,
        "change_id": change_id,
        "origin": ORIGIN_STRATEGIST,
        "reason": "narrow the surface",
        "capability_ids": list(ids),
    }
    payload.update(over)
    return payload


def _knowledge(change_id: str = "chg-know", **over: Any) -> dict:
    payload = {
        "target": TARGET_SENSES_KNOWLEDGE,
        "change_id": change_id,
        "origin": ORIGIN_WORKER,
        "reason": "the operator should hear this",
        "entry_id": "bed-3-moisture",
        "text": "orchid-bed read 12% at the last sweep",
    }
    payload.update(over)
    return payload


class _Verifier:
    """An injected suite. Records what it was asked, answers what it was told to."""

    def __init__(self, *, passed: bool = True, raises: bool = False, returns: Any = None):
        self.passed = passed
        self.raises = raises
        self.returns = returns
        self.requests: list[VerificationRequest] = []

    def __call__(self, request: VerificationRequest) -> Any:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("the suite harness fell over")
        if self.returns is not None:
            return self.returns
        return VerificationResult(
            passed=self.passed,
            summary="3 checks, 0 failed" if self.passed else "3 checks, 1 failed",
            suite="worker-prompt-suite",
            checks_run=3,
            checks_failed=0 if self.passed else 1,
        )


def _lifecycle(**over: Any) -> ConfigLifecycle:
    kwargs: dict[str, Any] = {"verifier": _Verifier(), "catalog": _catalog()}
    kwargs.update(over)
    return ConfigLifecycle(**kwargs)


def _accepted(payload: dict, *, catalog: Optional[CapabilityCatalog] = None) -> ConfigChange:
    change, refusal = change_from_payload(payload, catalog=catalog or _catalog())
    assert refusal is None, refusal
    assert change is not None
    return change


# ── the seat-configuration identity ──────────────────────────────────────────


class TestSeatConfigIdentity:
    """``config_sha`` is the per-seat analogue of the system-prompt sha."""

    def test_an_empty_config_has_a_stable_sha(self):
        assert SeatConfig(seat=SEAT_WORKER).config_sha == SeatConfig(seat=SEAT_WORKER).config_sha

    def test_two_seats_do_not_share_an_identity(self):
        assert SeatConfig(seat=SEAT_WORKER).config_sha != SeatConfig(seat=SEAT_SENSES).config_sha

    def test_the_sha_is_a_full_sha256(self):
        assert len(SeatConfig(seat=SEAT_WORKER).config_sha) == 64

    def test_prompt_order_is_part_of_the_identity(self):
        one = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection("a", "A"), PromptSection("b", "B"))
        )
        other = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection("b", "B"), PromptSection("a", "A"))
        )
        assert one.config_sha != other.config_sha

    def test_capability_order_is_not_part_of_the_identity(self):
        """``capability_ids`` is a SET (t3's decision), so ordering it must not matter."""
        one = SeatConfig(seat=SEAT_WORKER, tools=("fs.read", "fs.write"))
        other = SeatConfig(seat=SEAT_WORKER, tools=("fs.write", "fs.read"))
        assert one.config_sha == other.config_sha
        assert one.tools == other.tools == ("fs.read", "fs.write")

    def test_duplicate_capability_ids_collapse(self):
        assert SeatConfig(seat=SEAT_WORKER, tools=("a", "a", "b")).tools == ("a", "b")

    def test_a_tool_and_a_permission_of_the_same_name_are_distinguishable(self):
        tool = SeatConfig(seat=SEAT_WORKER, tools=("x",))
        perm = SeatConfig(seat=SEAT_WORKER, permissions=("x",))
        assert tool.config_sha != perm.config_sha

    def test_text_containing_the_row_separators_cannot_forge_a_row(self):
        """A prompt whose text contains tabs and newlines must not collide."""
        one = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("a", "x\tprompt\tb\ty"),))
        other = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection("a", "x"), PromptSection("b", "y"))
        )
        assert one.config_sha != other.config_sha

    def test_canonical_text_is_one_row_per_element(self):
        config = SeatConfig(
            seat=SEAT_WORKER,
            prompt=(PromptSection("a", "A"),),
            knowledge=(KnowledgeEntry("k", "K", ORIGIN_WORKER),),
            tools=("t",),
            permissions=("p",),
        )
        assert len(config.canonical_text().splitlines()) == 5

    def test_knowledge_attribution_is_part_of_the_identity(self):
        one = SeatConfig(seat=SEAT_SENSES, knowledge=(KnowledgeEntry("k", "K", ORIGIN_WORKER),))
        other = SeatConfig(
            seat=SEAT_SENSES, knowledge=(KnowledgeEntry("k", "K", ORIGIN_STRATEGIST),)
        )
        assert one.config_sha != other.config_sha

    def test_construction_never_raises_on_junk(self):
        config = SeatConfig(seat=None, prompt="nonsense", knowledge=7, tools=None, permissions=[1])
        assert config.prompt == ()
        assert config.knowledge == ()
        assert config.tools == ()
        assert config.permissions == ("1",)

    def test_compose_prompt_is_a_pure_function_of_the_config(self):
        config = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("a", "A"),))
        twin = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("a", "A"),))
        assert compose_prompt(config) == compose_prompt(twin)
        assert compose_prompt(None) == ""


# ── applying one unit is whole-surface replacement, never a merge ────────────


class TestApplyChangeIsWholeSurface:
    """t3 made capability selection a SET, not a delta. The apply path must match."""

    def test_a_tools_selection_replaces_the_whole_surface(self):
        config = SeatConfig(seat=SEAT_WORKER, tools=("fs.read", "fs.write"))
        change = _accepted(_tools(ids=("fs.read",)))
        assert apply_change(config, change).tools == ("fs.read",)

    def test_a_tools_selection_does_not_touch_permissions(self):
        config = SeatConfig(seat=SEAT_WORKER, tools=("fs.write",), permissions=("net.egress",))
        after = apply_change(config, _accepted(_tools(ids=("fs.read",))))
        assert after.permissions == ("net.egress",)

    def test_a_permissions_selection_writes_permissions_not_tools(self):
        change = _accepted(
            {
                "target": TARGET_WORKER_PERMISSIONS,
                "change_id": "p1",
                "origin": ORIGIN_STRATEGIST,
                "capability_ids": ["net.egress"],
            }
        )
        after = apply_change(SeatConfig(seat=SEAT_WORKER, tools=("fs.read",)), change)
        assert after.permissions == ("net.egress",)
        assert after.tools == ("fs.read",)

    def test_an_empty_selection_clears_the_surface(self):
        config = SeatConfig(seat=SEAT_WORKER, tools=("fs.read",))
        change = WorkerToolsChange(change_id="c", origin=ORIGIN_HOST, capability_ids=())
        assert apply_change(config, change).tools == ()

    def test_a_prompt_change_replaces_a_section_in_place(self):
        config = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection("a", "A"), PromptSection("b", "B"))
        )
        change = WorkerPromptChange(change_id="c", origin=ORIGIN_STRATEGIST, section="a", text="A2")
        after = apply_change(config, change)
        assert [(s.section, s.text) for s in after.prompt] == [("a", "A2"), ("b", "B")]

    def test_a_new_section_appends(self):
        config = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("a", "A"),))
        change = WorkerPromptChange(change_id="c", origin=ORIGIN_STRATEGIST, section="z", text="Z")
        assert [s.section for s in apply_change(config, change).prompt] == ["a", "z"]

    def test_clearing_a_section_keeps_it_declared(self):
        """Empty text is a real change; the section stays so revert stays expressible."""
        config = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("a", "A"),))
        change = WorkerPromptChange(change_id="c", origin=ORIGIN_STRATEGIST, section="a", text="")
        after = apply_change(config, change)
        assert [(s.section, s.text) for s in after.prompt] == [("a", "")]

    def test_a_knowledge_change_carries_its_attribution(self):
        change = _accepted(_knowledge())
        after = apply_change(SeatConfig(seat=SEAT_SENSES), change)
        assert after.knowledge[0].origin == ORIGIN_WORKER
        assert after.knowledge[0].entry_id == "bed-3-moisture"

    def test_a_knowledge_change_replaces_its_own_entry_id_in_place(self):
        config = SeatConfig(seat=SEAT_SENSES, knowledge=(KnowledgeEntry("k", "old", ORIGIN_HOST),))
        change = SensesKnowledgeChange(
            change_id="c", origin=ORIGIN_WORKER, entry_id="k", text="new"
        )
        after = apply_change(config, change)
        assert [(e.entry_id, e.text) for e in after.knowledge] == [("k", "new")]

    def test_supersedes_drops_the_named_entry(self):
        config = SeatConfig(
            seat=SEAT_SENSES,
            knowledge=(KnowledgeEntry("old", "O", ORIGIN_HOST), KnowledgeEntry("keep", "K", "")),
        )
        change = SensesKnowledgeChange(
            change_id="c", origin=ORIGIN_WORKER, entry_id="new", text="N", supersedes="old"
        )
        after = apply_change(config, change)
        assert [e.entry_id for e in after.knowledge] == ["keep", "new"]

    def test_every_shipped_change_unit_is_dispatched(self):
        """No target may fall through to a silent no-op."""
        for target, unit in CHANGE_UNITS.items():
            seat = target.split(".")[0]
            if issubclass(unit, PromptChange):
                change: ConfigChange = unit(
                    change_id="c", origin=ORIGIN_HOST, section="s", text="T"
                )
            elif issubclass(unit, KnowledgeChange):
                change = unit(change_id="c", origin=ORIGIN_HOST, entry_id="e", text="T")
            else:
                change = unit(change_id="c", origin=ORIGIN_HOST, capability_ids=("x",))
            before = SeatConfig(seat=seat)
            assert apply_change(before, change).config_sha != before.config_sha, target

    def test_an_unknown_change_family_leaves_the_config_alone(self):
        config = SeatConfig(seat=SEAT_WORKER)
        assert apply_change(config, ConfigChange(change_id="c", origin=ORIGIN_HOST)) is config
        assert apply_change(config, None) is config


# ── the three states, recorded ───────────────────────────────────────────────


class TestDistinctRecordedStates:
    """propose / verified / applied are distinct, and every transition is recorded."""

    def test_the_states_are_distinct_names(self):
        assert len({STATE_PROPOSED, STATE_VERIFIED, STATE_APPLIED, STATE_REJECTED}) == 4
        assert set(CHANGE_STATES) == {STATE_PROPOSED, STATE_VERIFIED, STATE_APPLIED, STATE_REJECTED}
        assert set(TERMINAL_STATES) == {STATE_APPLIED, STATE_REJECTED}

    def test_a_proposal_starts_proposed(self):
        life = _lifecycle()
        proposal = life.propose(_prompt())
        assert proposal is not None
        assert proposal.state == STATE_PROPOSED

    def test_the_three_states_are_reached_in_order_and_each_is_recorded(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.apply("chg-1")
        assert [(t.from_state, t.to_state) for t in life.transitions] == [
            ("", STATE_PROPOSED),
            (STATE_PROPOSED, STATE_VERIFIED),
            (STATE_VERIFIED, STATE_APPLIED),
        ]

    def test_each_transition_names_the_change_seat_target_and_origin(self):
        life = _lifecycle()
        life.propose(_prompt())
        transition = life.transitions[0]
        assert transition.change_id == "chg-1"
        assert transition.seat == SEAT_WORKER
        assert transition.target == TARGET_WORKER_PROMPTS
        assert transition.origin == ORIGIN_STRATEGIST

    def test_transitions_are_sequenced_rather_than_timestamped(self):
        """No clock in this layer: order is recorded, time is t5's to stamp."""
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        assert [t.sequence for t in life.transitions] == [0, 1]
        assert not any("time" in key for key in life.transitions[0].to_dict())

    def test_propose_order_is_counted_separately_from_transition_order(self):
        """Two counters: one means "the nth proposal", the other "the nth transition"."""
        life = _lifecycle()
        life.propose(_prompt("chg-1"))
        life.verify("chg-1")
        life.propose(_prompt("chg-2", section="other"))
        assert [p.sequence for p in life.proposals()] == [0, 1]
        assert [t.sequence for t in life.transitions] == [0, 1, 2]

    def test_the_applied_transition_carries_the_new_config_sha(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.apply("chg-1")
        assert life.transitions[-1].config_sha == life.effective(SEAT_WORKER).config_sha

    def test_the_verified_transition_carries_the_gate_verdict(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.transitions[-1].verdict == "passed"

    def test_a_transition_serialises(self):
        life = _lifecycle()
        life.propose(_prompt())
        data = life.transitions[0].to_dict()
        assert data["to_state"] == STATE_PROPOSED
        assert data["change_id"] == "chg-1"

    def test_applying_moves_the_effective_configuration(self):
        life = _lifecycle()
        before = life.effective(SEAT_WORKER).config_sha
        life.propose(_prompt())
        life.verify("chg-1")
        life.apply("chg-1")
        after = life.effective(SEAT_WORKER)
        assert after.config_sha != before
        assert compose_prompt(after) == "Read a file once."

    def test_a_duplicate_change_id_is_refused_and_recorded(self):
        life = _lifecycle()
        life.propose(_prompt())
        assert life.propose(_prompt(text="something else")) is None
        assert life.degradations[-1].code == CHANGE_DUPLICATE_PROPOSAL

    def test_the_admission_rules_are_stated_where_an_author_can_read_them(self):
        """#58's lesson: a rule the gate grades on must be stated, not implied."""
        assert LIFECYCLE_RULES
        joined = "\n".join(LIFECYCLE_RULES).lower()
        assert "change_id" in joined
        assert "verif" in joined
        assert "run" in joined

    def test_a_refused_payload_never_becomes_a_proposal(self):
        life = _lifecycle()
        assert life.propose(_prompt(origin=ORIGIN_WORKER)) is None
        assert life.proposals() == ()
        assert life.degradations[-1].code == "config-change-origin-forbidden"


# ── the verification half of the gate ────────────────────────────────────────


class TestVerificationGate:
    """A failed suite is a failed drone smoke: never applied, always recorded."""

    def test_the_verifier_is_handed_the_candidate_not_only_the_change(self):
        """drone: the smoke runs against the STAGED copy, not the source handed in."""
        verifier = _Verifier()
        life = _lifecycle(verifier=verifier)
        life.propose(_prompt())
        life.verify("chg-1")
        request = verifier.requests[0]
        assert request.seat == SEAT_WORKER
        assert request.baseline.config_sha == SeatConfig(seat=SEAT_WORKER).config_sha
        assert compose_prompt(request.candidate) == "Read a file once."

    def test_the_candidate_is_not_installed_by_verifying(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.effective(SEAT_WORKER).prompt == ()

    def test_a_failing_suite_rejects_and_never_applies(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.proposal("chg-1").state == STATE_REJECTED
        outcome = life.apply("chg-1")
        assert outcome.applied is False
        assert life.effective(SEAT_WORKER).prompt == ()

    def test_a_failing_suite_records_the_failure(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        life.propose(_prompt())
        life.verify("chg-1")
        codes = [d.code for d in life.degradations]
        assert CHANGE_VERIFICATION_FAILED in codes
        assert [(t.from_state, t.to_state) for t in life.transitions][-1] == (
            STATE_PROPOSED,
            STATE_REJECTED,
        )
        assert life.transitions[-1].verdict == "failed"

    def test_the_recorded_failure_carries_the_suite_summary(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        life.propose(_prompt())
        life.verify("chg-1")
        assert "1 failed" in life.degradations[-1].reason

    def test_a_rejected_proposal_stays_rejected(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        life.propose(_prompt())
        life.verify("chg-1")
        life.verify("chg-1")
        assert life.proposal("chg-1").state == STATE_REJECTED
        assert life.degradations[-1].code == CHANGE_TERMINAL

    def test_no_verifier_fails_closed_and_leaves_the_proposal_proposable(self):
        """Absence of evidence never approves — and never condemns either."""
        life = ConfigLifecycle(catalog=_catalog())
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.proposal("chg-1").state == STATE_PROPOSED
        assert life.degradations[-1].code == CHANGE_VERIFIER_UNAVAILABLE

    def test_a_verifier_that_raises_never_reaches_the_host(self):
        life = _lifecycle(verifier=_Verifier(raises=True))
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.proposal("chg-1").state == STATE_PROPOSED
        assert life.degradations[-1].code == CHANGE_VERIFIER_UNREADABLE
        assert "RuntimeError" in life.degradations[-1].reason

    @pytest.mark.parametrize("returned", [True, "passed", {"passed": True}, 1, object()])
    def test_a_verifier_that_answers_in_the_wrong_shape_fails_closed(self, returned: Any):
        life = _lifecycle(verifier=_Verifier(returns=returned))
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.proposal("chg-1").state == STATE_PROPOSED
        assert life.degradations[-1].code == CHANGE_VERIFIER_UNREADABLE

    def test_the_verifier_cannot_claim_which_config_it_tested(self):
        """``candidate_sha`` is STAMPED by the gate — t3's ``_stamp`` discipline."""
        lied = VerificationResult(passed=True, candidate_sha="deadbeef", baseline_sha="deadbeef")
        life = _lifecycle(verifier=_Verifier(returns=lied))
        life.propose(_prompt())
        life.verify("chg-1")
        verification = life.proposal("chg-1").verification
        assert verification.candidate_sha != "deadbeef"
        assert verification.baseline_sha == SeatConfig(seat=SEAT_WORKER).config_sha

    def test_an_unverified_proposal_cannot_be_applied(self):
        life = _lifecycle()
        life.propose(_prompt())
        outcome = life.apply("chg-1")
        assert outcome.applied is False
        assert outcome.refusal.code == CHANGE_UNVERIFIED
        assert life.effective(SEAT_WORKER).prompt == ()

    def test_applying_twice_is_refused_not_repeated(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.apply("chg-1")
        outcome = life.apply("chg-1")
        assert outcome.applied is False
        assert outcome.refusal.code == CHANGE_ALREADY_APPLIED

    def test_an_unknown_proposal_is_refused_and_recorded(self):
        life = _lifecycle()
        outcome = life.apply("never-proposed")
        assert outcome.refusal.code == CHANGE_UNKNOWN_PROPOSAL
        assert life.degradations[-1].code == CHANGE_UNKNOWN_PROPOSAL


# ── the quiescence half of the gate ──────────────────────────────────────────


class TestSeatQuiescenceGate:
    """No seat has its configuration changed under it — apply waits for idle."""

    def test_a_fresh_seat_is_idle(self):
        assert _lifecycle().is_idle(SEAT_WORKER) is True

    def test_a_seat_in_a_run_is_not_idle(self):
        life = _lifecycle()
        life.begin_run(SEAT_WORKER)
        assert life.is_idle(SEAT_WORKER) is False
        assert life.is_idle(SEAT_SENSES) is True

    def test_apply_defers_while_the_seat_is_running(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER)
        outcome = life.apply("chg-1")
        assert outcome.applied is False
        assert outcome.deferred is True
        assert outcome.deferral.code == CHANGE_SEAT_BUSY
        assert life.effective(SEAT_WORKER).prompt == ()

    def test_a_deferral_leaves_the_proposal_verified(self):
        """A busy seat is a timing fact; it must not spend the proposal."""
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER)
        life.apply("chg-1")
        assert life.proposal("chg-1").state == STATE_VERIFIED

    def test_a_deferral_is_recorded_but_is_not_a_degradation(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER)
        life.apply("chg-1")
        assert [d.code for d in life.deferrals] == [CHANGE_SEAT_BUSY]
        assert life.degradations == ()
        assert not isinstance(life.deferrals[0], ConfigDegradation)

    def test_a_deferral_names_the_run_holding_the_seat(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER, run_id="drive-7")
        life.apply("chg-1")
        assert life.deferrals[0].blocking_runs == ("drive-7",)

    def test_verification_waits_for_the_idle_seat_too(self):
        """The operator's words: available for test suite AND not in a run."""
        verifier = _Verifier()
        life = _lifecycle(verifier=verifier)
        life.propose(_prompt())
        life.begin_run(SEAT_WORKER)
        life.verify("chg-1")
        assert verifier.requests == []
        assert life.proposal("chg-1").state == STATE_PROPOSED
        assert life.deferrals[-1].code == CHANGE_SEAT_BUSY

    def test_the_change_lands_once_the_seat_goes_idle(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        run = life.begin_run(SEAT_WORKER)
        assert life.apply("chg-1").deferred is True
        life.end_run(run)
        assert life.apply("chg-1").applied is True
        assert compose_prompt(life.effective(SEAT_WORKER)) == "Read a file once."

    def test_a_run_on_another_seat_does_not_block(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_SENSES)
        assert life.apply("chg-1").applied is True

    def test_overlapping_runs_hold_the_seat_until_the_last_one_closes(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        outer = life.begin_run(SEAT_WORKER, run_id="outer")
        inner = life.begin_run(SEAT_WORKER, run_id="inner")
        life.end_run(inner)
        assert life.is_idle(SEAT_WORKER) is False
        life.end_run(outer)
        assert life.is_idle(SEAT_WORKER) is True

    def test_an_overlapping_run_sees_the_same_configuration(self):
        life = _lifecycle()
        outer = life.begin_run(SEAT_WORKER, run_id="outer")
        inner = life.begin_run(SEAT_WORKER, run_id="inner")
        assert inner.config_sha == outer.config_sha

    def test_advance_applies_everything_the_gate_allows(self):
        life = _lifecycle()
        life.propose(_prompt("chg-1"))
        life.propose(_tools("chg-2"))
        report = life.advance()
        assert set(report.applied) == {"chg-1", "chg-2"}
        assert life.effective(SEAT_WORKER).tools == ("fs.read",)

    def test_advance_defers_a_busy_seat_and_lands_it_on_the_next_call(self):
        life = _lifecycle()
        life.propose(_prompt())
        run = life.begin_run(SEAT_WORKER)
        first = life.advance()
        assert first.applied == ()
        assert first.deferred == ("chg-1",)
        life.end_run(run)
        assert life.advance().applied == ("chg-1",)

    def test_advance_never_leaves_a_stale_verification_behind(self):
        """Two units for one seat: the second is verified against the first's result."""
        verifier = _Verifier()
        life = _lifecycle(verifier=verifier)
        life.propose(_prompt("chg-1", text="one"))
        life.propose(_prompt("chg-2", text="two", section="other"))
        report = life.advance()
        assert report.applied == ("chg-1", "chg-2")
        second = verifier.requests[-1]
        assert compose_prompt(second.baseline) == "one"
        assert compose_prompt(second.candidate) == "one\n\ntwo"


# ── the headline invariant ───────────────────────────────────────────────────


class _PromptProbe:
    """Live session 1's ``_ScopeProbe._check_system``, made per-seat.

    The live host hashed ``messages[0]`` on every turn of a drive and counted
    rewrites; this hashes the system prompt the seat is handed on every step and
    does the same. The claim it measures is the same claim, one level down:
    **the configuration a run started under is the configuration it finishes
    under.**
    """

    def __init__(self) -> None:
        self.sha: Optional[str] = None
        self.rewrites = 0
        self.turns = 0
        self.seen: list[str] = []
        # The *seat's* effective configuration, sampled beside the prompt bytes.
        # Sampling only what the run handle serves would measure one of the two
        # layers holding this invariant — the frozen handle — and would stay
        # green with the apply gate deleted, which the mutation proof caught.
        self.effective: list[str] = []

    def turn(self, system_prompt: str, effective_sha: str = "") -> None:
        self.turns += 1
        digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
        self.seen.append(digest)
        self.effective.append(effective_sha)
        if self.sha is None:
            self.sha = digest
            return
        if digest != self.sha:
            self.rewrites += 1
            self.sha = digest

    @property
    def effective_rewrites(self) -> int:
        """How many times the seat's own configuration moved while it was running."""
        return len([entry for entry in set(self.effective) if entry]) - 1 if self.effective else 0


def _drive(
    life: ConfigLifecycle,
    probe: _PromptProbe,
    *,
    steps: int = 8,
    read: Optional[Callable[[SeatRun], str]] = None,
    between: Optional[Callable[[int], None]] = None,
    run_id: str = "drive-1",
) -> SeatRun:
    """One bounded fake drive of the worker seat, probed on every step."""
    run = life.begin_run(SEAT_WORKER, run_id=run_id)
    reader = read or (lambda handle: compose_prompt(handle.config))
    for step in range(steps):
        probe.turn(reader(run), life.effective(SEAT_WORKER).config_sha)
        if between is not None:
            between(step)
    life.end_run(run)
    return run


class TestPerSeatConstancy:
    """The per-seat analogue of live session 1's constant system-prompt sha."""

    def test_the_prompt_is_byte_identical_on_every_step_of_a_drive(self):
        life = _lifecycle()
        life.propose(_prompt("chg-0", text="baseline"))
        life.advance()
        probe = _PromptProbe()

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step + 1}", text=f"rewrite {step}"))
            life.advance()

        _drive(life, probe, steps=8, between=strategist)
        assert probe.turns == 8
        assert probe.rewrites == 0
        assert len(set(probe.seen)) == 1

    def test_the_seats_own_configuration_never_moves_while_it_is_running(self):
        """The gate, not only the handle: the effective config is constant too.

        The frozen run handle alone would keep the prompt bytes constant even if
        the gate were deleted — so this samples what the *lifecycle* holds for
        the seat on every step, which is the thing the gate protects.
        """
        life = _lifecycle()
        probe = _PromptProbe()

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step}", text=f"rewrite {step}"))
            life.advance()

        _drive(life, probe, steps=8, between=strategist)
        assert len(set(probe.effective)) == 1
        assert probe.effective_rewrites == 0

    def test_a_host_calling_apply_directly_mid_run_changes_nothing_either(self):
        """The gate lives in ``apply`` itself, not only in the ``advance`` pump.

        ``advance`` checks seat-idle before it reaches ``apply``, so a drive
        pumped through ``advance`` alone would stay green with ``apply``'s own
        check deleted — the mutation proof caught exactly that. This drives the
        other entry point: verified up front, applied by hand on every step.
        """
        life = _lifecycle()
        life.propose(_prompt("chg-1", text="mid-drive rewrite"))
        life.verify("chg-1")
        probe = _PromptProbe()

        def strategist(step: int) -> None:
            life.apply("chg-1")

        _drive(life, probe, steps=6, between=strategist)
        assert probe.rewrites == 0
        assert len(set(probe.effective)) == 1
        assert life.effective(SEAT_WORKER).prompt == ()
        assert len(life.deferrals) == 6
        # …and it lands the moment the seat is idle, so nothing was lost.
        assert life.apply("chg-1").applied is True

    def test_the_configuration_identity_is_constant_across_the_drive(self):
        life = _lifecycle()
        shas: list[str] = []

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step}", text=f"rewrite {step}"))
            life.advance()

        run = _drive(life, _PromptProbe(), steps=6, between=strategist)
        for _ in range(6):
            shas.append(run.config_sha)
        assert len(set(shas)) == 1

    def test_every_blocked_apply_is_recorded_rather_than_lost(self):
        life = _lifecycle()
        reports = []

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step}", text=f"rewrite {step}"))
            reports.append(life.advance())

        _drive(life, _PromptProbe(), steps=5, between=strategist)
        # One deferral per pending proposal per advance: the strategist proposes
        # on every step and nothing lands, so pass n sees n proposals — 1+2+3+4+5.
        assert len(life.deferrals) == 15
        assert {d.code for d in life.deferrals} == {CHANGE_SEAT_BUSY}
        assert {d.change_id for d in life.deferrals} == {f"chg-{step}" for step in range(5)}
        assert life.proposals(state=STATE_PROPOSED) != ()

    def test_a_busy_seat_is_reported_as_deferred_and_never_as_refused(self):
        """``advance``'s own idle check is a *recording* guarantee, not a safety one.

        ``verify`` and ``apply`` each hold the gate independently, so deleting
        ``advance``'s check changes nothing about what is applied — it changes
        what a host is told: a seat that is merely working would be reported as
        a refusal. The distinction is the whole reason deferrals are a separate
        stream, so it is asserted rather than assumed.
        """
        life = _lifecycle()
        reports = []

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step}", text=f"rewrite {step}"))
            reports.append(life.advance())

        _drive(life, _PromptProbe(), steps=4, between=strategist)
        assert [len(report.deferred) for report in reports] == [1, 2, 3, 4]
        assert all(report.refused == () for report in reports)
        assert all(report.applied == () for report in reports)

    def test_the_changes_land_between_runs_not_during_one(self):
        life = _lifecycle()

        def strategist(step: int) -> None:
            life.propose(_prompt(f"chg-{step}", text=f"rewrite {step}"))
            life.advance()

        first = _PromptProbe()
        _drive(life, first, steps=3, between=strategist, run_id="drive-1")
        life.advance()
        second = _PromptProbe()
        _drive(life, second, steps=3, run_id="drive-2")
        assert first.rewrites == 0
        assert second.rewrites == 0
        assert first.sha != second.sha

    def test_the_run_handle_cannot_be_repointed(self):
        """Layer one of the invariant: the handed-out config is frozen."""
        life = _lifecycle()
        run = life.begin_run(SEAT_WORKER)
        with pytest.raises(Exception):
            run.config = SeatConfig(seat=SEAT_WORKER, prompt=(PromptSection("x", "X"),))
        with pytest.raises(Exception):
            run.config.prompt = ()


class TestTheConstancyProbeCanFire:
    """The test-of-the-test: a green probe is only worth something if it can go red."""

    def test_a_seat_that_re_reads_the_live_config_mid_run_is_caught(self):
        life = _lifecycle()
        probe = _PromptProbe()

        def miswired(run: SeatRun) -> str:
            # The bug this whole gate exists to prevent, injected deliberately:
            # a seat that reads the lifecycle's live configuration on every turn
            # instead of the configuration its run was pinned to.
            return compose_prompt(life.effective(SEAT_WORKER))

        def rewrite_under_the_seat(step: int) -> None:
            if step == 2:
                # Bypass the gate entirely — this is the mutation, not an API.
                life._configs[SEAT_WORKER] = SeatConfig(
                    seat=SEAT_WORKER, prompt=(PromptSection("a", "changed"),)
                )

        _drive(life, probe, steps=5, read=miswired, between=rewrite_under_the_seat)
        assert probe.rewrites == 1
        assert len(set(probe.seen)) == 2


# ── staleness: the drone's refusal, moved to runtime ─────────────────────────


class TestStaleVerification:
    """A suite result is evidence about the baseline it ran against, and no other."""

    def test_a_verification_overtaken_by_another_change_is_refused_at_apply(self):
        life = _lifecycle()
        life.propose(_prompt("chg-1", text="one"))
        life.propose(_prompt("chg-2", text="two", section="other"))
        life.verify("chg-1")
        life.verify("chg-2")
        assert life.apply("chg-1").applied is True
        outcome = life.apply("chg-2")
        assert outcome.applied is False
        assert outcome.refusal.code == CHANGE_STALE_VERIFICATION

    def test_a_stale_verification_demotes_rather_than_rejects(self):
        life = _lifecycle()
        life.propose(_prompt("chg-1", text="one"))
        life.propose(_prompt("chg-2", text="two", section="other"))
        life.verify("chg-1")
        life.verify("chg-2")
        life.apply("chg-1")
        life.apply("chg-2")
        assert life.proposal("chg-2").state == STATE_PROPOSED
        assert (STATE_VERIFIED, STATE_PROPOSED) in [
            (t.from_state, t.to_state) for t in life.transitions
        ]

    def test_re_verifying_against_the_new_baseline_lets_it_land(self):
        life = _lifecycle()
        life.propose(_prompt("chg-1", text="one"))
        life.propose(_prompt("chg-2", text="two", section="other"))
        life.verify("chg-1")
        life.verify("chg-2")
        life.apply("chg-1")
        life.apply("chg-2")
        life.verify("chg-2")
        assert life.apply("chg-2").applied is True
        assert compose_prompt(life.effective(SEAT_WORKER)) == "one\n\ntwo"


class TestCatalogStaleness:
    """t3 left ``revalidate()`` as the hook into this gate. It is wired here."""

    def test_a_catalog_that_moved_refuses_the_apply(self):
        life = _lifecycle()
        life.propose(_tools("chg-tools"))
        life.verify("chg-tools")
        outcome = life.apply("chg-tools", catalog=_catalog(extra=True))
        assert outcome.applied is False
        assert outcome.refusal.code == CHANGE_STALE_CATALOG

    def test_a_revoked_capability_refuses_the_apply(self):
        life = _lifecycle()
        life.propose(_tools("chg-tools"))
        life.verify("chg-tools")
        outcome = life.apply("chg-tools", catalog=CapabilityCatalog(catalog_id="greenhouse-1"))
        assert outcome.applied is False
        assert outcome.refusal.code == CHANGE_UNKNOWN_CAPABILITY

    def test_catalog_staleness_is_terminal_because_re_verifying_cannot_fix_it(self):
        life = _lifecycle()
        life.propose(_tools("chg-tools"))
        life.verify("chg-tools")
        life.apply("chg-tools", catalog=_catalog(extra=True))
        assert life.proposal("chg-tools").state == STATE_REJECTED

    def test_a_prompt_unit_has_nothing_to_go_stale(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        assert life.apply("chg-1", catalog=CapabilityCatalog()).applied is True

    def test_the_terminal_catalog_refusal_is_recorded_once(self):
        life = _lifecycle()
        life.propose(_tools("chg-tools"))
        life.verify("chg-tools")
        life.apply("chg-tools", catalog=_catalog(extra=True))
        assert [d.code for d in life.degradations] == [CHANGE_STALE_CATALOG]


# ── seat-run bookkeeping degrades, never raises ──────────────────────────────


class TestSeatRunBookkeeping:
    def test_ending_an_unknown_run_is_recorded_not_raised(self):
        life = _lifecycle()
        life.end_run("never-began")
        assert life.degradations[-1].code == CHANGE_UNKNOWN_RUN

    def test_ending_a_run_twice_is_recorded(self):
        life = _lifecycle()
        run = life.begin_run(SEAT_WORKER)
        life.end_run(run)
        life.end_run(run)
        assert life.degradations[-1].code == CHANGE_UNKNOWN_RUN

    def test_a_seat_outside_the_lattice_is_named(self):
        life = _lifecycle()
        life.begin_run("strategist")
        assert life.degradations[-1].code == CHANGE_UNKNOWN_SEAT

    def test_a_run_still_gets_a_handle_when_its_seat_is_unknown(self):
        life = _lifecycle()
        run = life.begin_run("strategist")
        assert isinstance(run, SeatRun)
        assert run.seat == "strategist"

    def test_run_ids_are_generated_without_a_clock(self):
        life = _lifecycle()
        first = life.begin_run(SEAT_WORKER)
        second = life.begin_run(SEAT_SENSES)
        assert first.run_id != second.run_id
        assert first.run_id.startswith(SEAT_WORKER)

    def test_open_runs_are_readable(self):
        life = _lifecycle()
        life.begin_run(SEAT_WORKER, run_id="a")
        life.begin_run(SEAT_SENSES, run_id="b")
        assert {run.run_id for run in life.open_runs()} == {"a", "b"}
        assert {run.run_id for run in life.open_runs(SEAT_WORKER)} == {"a"}


class TestDegradeNeverRaise:
    @pytest.mark.parametrize(
        "payload", [None, "", 7, [], {"target": "nope"}, {"target": TARGET_WORKER_PROMPTS}]
    )
    def test_a_hostile_payload_is_refused_not_raised(self, payload: Any):
        life = _lifecycle()
        assert life.propose(payload) is None
        assert life.degradations

    @pytest.mark.parametrize("change_id", [None, "", 7, object()])
    def test_a_hostile_change_id_never_raises(self, change_id: Any):
        life = _lifecycle()
        assert life.verify(change_id) is None
        assert life.apply(change_id).applied is False

    def test_reading_an_unknown_seat_returns_an_empty_configuration(self):
        life = _lifecycle()
        assert life.effective("nobody").config_sha == SeatConfig(seat="nobody").config_sha

    def test_an_already_accepted_change_may_be_proposed_directly(self):
        life = _lifecycle()
        proposal = life.propose(_accepted(_prompt()))
        assert proposal is not None
        assert proposal.state == STATE_PROPOSED

    def test_baselines_may_be_supplied_at_construction(self):
        life = ConfigLifecycle(
            seats={SEAT_WORKER: SeatConfig(seat=SEAT_WORKER, tools=("fs.read",))}
        )
        assert life.effective(SEAT_WORKER).tools == ("fs.read",)

    def test_a_junk_baseline_map_is_ignored_rather_than_fatal(self):
        life = ConfigLifecycle(seats={SEAT_WORKER: "not a config", 7: None})
        assert life.effective(SEAT_WORKER).tools == ()


# ── nothing is dropped, and the vocabulary is honest ─────────────────────────


class TestNothingIsDropped:
    def test_every_declared_code_has_a_producer_in_this_module(self):
        source = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for constant in (
            "CHANGE_SEAT_BUSY",
            "CHANGE_UNVERIFIED",
            "CHANGE_VERIFICATION_FAILED",
            "CHANGE_VERIFIER_UNAVAILABLE",
            "CHANGE_VERIFIER_UNREADABLE",
            "CHANGE_STALE_VERIFICATION",
            "CHANGE_UNKNOWN_PROPOSAL",
            "CHANGE_DUPLICATE_PROPOSAL",
            "CHANGE_ALREADY_APPLIED",
            "CHANGE_TERMINAL",
            "CHANGE_UNKNOWN_RUN",
            "CHANGE_UNKNOWN_SEAT",
        ):
            assert constant in assigned, constant
            assert constant in used, f"{constant} is declared but nothing mints it"

    def test_the_code_families_do_not_overlap(self):
        assert not set(LIFECYCLE_REFUSAL_CODES) & set(LIFECYCLE_DEFERRAL_CODES)
        assert set(LIFECYCLE_CODES) == set(LIFECYCLE_REFUSAL_CODES) | set(LIFECYCLE_DEFERRAL_CODES)

    def test_every_code_is_namespaced_to_the_config_lane(self):
        for code in LIFECYCLE_CODES:
            assert code.startswith("config-"), code

    def test_a_refusal_carries_the_scope_degradation_shape(self):
        life = _lifecycle()
        life.apply("nope")
        refusal = life.degradations[-1]
        assert isinstance(refusal, ConfigRefusal)
        assert set(refusal.to_dict()) == {
            "code",
            "reason",
            "step_index",
            "model_turns",
            "seat",
            "target",
            "change_id",
            "origin",
        }

    def test_a_deferral_folds_onto_the_same_reader(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER)
        life.apply("chg-1")
        deferral = life.deferrals[-1]
        assert isinstance(deferral, ConfigDeferral)
        assert {"code", "reason", "seat", "target", "change_id", "origin"} <= set(
            deferral.to_dict()
        )

    def test_every_proposal_ends_in_exactly_one_stream(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        for index in range(4):
            life.propose(_prompt(f"chg-{index}", section=f"s{index}"))
        life.advance()
        states = [p.state for p in life.proposals()]
        assert len(states) == 4
        assert set(states) == {STATE_REJECTED}
        assert len(life.proposals(state=STATE_REJECTED)) == 4

    def test_proposals_can_be_filtered_by_seat(self):
        life = _lifecycle()
        life.propose(_prompt("chg-w"))
        life.propose(_knowledge("chg-s"))
        assert [p.change_id for p in life.proposals(seat=SEAT_SENSES)] == ["chg-s"]

    def test_the_transition_stream_is_append_only_from_outside(self):
        life = _lifecycle()
        life.propose(_prompt())
        stream = life.transitions
        assert isinstance(stream, tuple)
        assert all(isinstance(entry, ConfigTransition) for entry in stream)


# ── structure: cited, not coupled; no thread, no clock, no IO ────────────────


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class TestCitedNotCoupled:
    """The advisory lane stays byte-stable: an import edge is a reason to edit it."""

    @pytest.mark.parametrize(
        "forbidden",
        [
            "embodiment.scope",
            "embodiment.scoped_run",
            "embodiment.strategist_runner",
            "embodiment.scope_events",
            "embodiment.loop",
            "embodiment.ledger",
        ],
    )
    def test_the_lifecycle_reaches_no_advisory_module(self, forbidden: str):
        assert forbidden not in _imported_modules(MODULE)

    def test_it_imports_exactly_the_two_schema_modules(self):
        embodiment_imports = {
            name for name in _imported_modules(MODULE) if name.startswith("embodiment")
        }
        assert embodiment_imports == {"embodiment.capability", "embodiment.config_change"}


class TestNoThreadNoClockNoIO:
    """Pure policy — the ``presence.py`` half, not the ``presence_engine.py`` half."""

    @pytest.mark.parametrize(
        "banned",
        [
            "threading",
            "time",
            "datetime",
            "os",
            "pathlib",
            "subprocess",
            "socket",
            "shutil",
            "tempfile",
            "random",
            "uuid",
            "asyncio",
            "logging",
        ],
    )
    def test_the_module_imports_no_mechanics(self, banned: str):
        assert banned not in _imported_modules(MODULE)

    def test_the_module_opens_nothing_and_prints_nothing(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not {"open", "print", "input"} & called

    def test_the_verifier_is_injected_never_constructed(self):
        """Nothing in the module builds a suite; the host supplies one."""
        life = ConfigLifecycle()
        assert life.verifier is None
        sentinel = _Verifier()
        assert ConfigLifecycle(verifier=sentinel).verifier is sentinel


class TestEveryRecordSerialises:
    """t5 translates these into events, so each one has to render without help."""

    def test_a_prompt_section_and_a_knowledge_entry_render(self):
        assert PromptSection("a", "A").to_dict() == {"section": "a", "text": "A"}
        assert KnowledgeEntry("k", "K", ORIGIN_WORKER).to_dict() == {
            "entry_id": "k",
            "text": "K",
            "origin": ORIGIN_WORKER,
        }

    def test_a_seat_config_renders_with_its_digest(self):
        data = SeatConfig(seat=SEAT_WORKER, tools=("fs.read",)).to_dict()
        assert data["tools"] == ["fs.read"]
        assert len(data["config_sha"]) == 64

    def test_a_run_handle_renders(self):
        life = _lifecycle()
        run = life.begin_run(SEAT_WORKER, run_id="drive-1")
        assert run.to_dict() == {
            "seat": SEAT_WORKER,
            "run_id": "drive-1",
            "config_sha": run.config_sha,
        }
        assert run.prompt == ""

    def test_a_proposal_renders_with_its_verification(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.verify("chg-1")
        data = life.proposal("chg-1").to_dict()
        assert data["state"] == STATE_VERIFIED
        assert data["verification"]["passed"] is True
        assert data["change"]["target"] == TARGET_WORKER_PROMPTS

    def test_an_unverified_proposal_renders_a_null_verification(self):
        life = _lifecycle()
        life.propose(_prompt())
        assert life.proposal("chg-1").to_dict()["verification"] is None

    def test_an_apply_outcome_renders_both_slots(self):
        life = _lifecycle()
        life.propose(_prompt())
        applied = life.apply("chg-1").to_dict()
        assert applied["refusal"]["code"] == CHANGE_UNVERIFIED
        assert applied["deferral"] is None
        life.verify("chg-1")
        life.begin_run(SEAT_WORKER)
        deferred = life.apply("chg-1").to_dict()
        assert deferred["deferral"]["code"] == CHANGE_SEAT_BUSY
        assert deferred["refusal"] is None

    def test_an_advance_report_renders(self):
        life = _lifecycle()
        life.propose(_prompt())
        assert life.advance().to_dict()["applied"] == ["chg-1"]

    def test_a_verification_request_names_its_change(self):
        verifier = _Verifier()
        life = _lifecycle(verifier=verifier)
        life.propose(_prompt())
        life.verify("chg-1")
        assert verifier.requests[0].target == TARGET_WORKER_PROMPTS
        assert verifier.requests[0].change_id == "chg-1"
        assert VerificationRequest().target == ""
        assert VerificationRequest().change_id == ""

    def test_a_verification_result_renders(self):
        assert VerificationResult(passed=True, suite="s").to_dict()["suite"] == "s"


class TestCoercionNeverRaises:
    def test_an_unrenderable_value_becomes_a_blank(self):
        class Hostile:
            def __str__(self) -> str:
                raise ValueError("no")

        assert SeatConfig(seat=Hostile()).seat == ""

    def test_a_bare_string_capability_surface_is_one_id(self):
        assert SeatConfig(seat=SEAT_WORKER, tools="fs.read").tools == ("fs.read",)

    def test_a_scalar_capability_surface_is_coerced(self):
        assert SeatConfig(seat=SEAT_WORKER, tools=7).tools == ("7",)

    def test_a_pair_is_read_as_a_prompt_section(self):
        config = SeatConfig(seat=SEAT_WORKER, prompt=[("a", "A"), "b"])
        assert [(s.section, s.text) for s in config.prompt] == [("a", "A"), ("b", "")]

    def test_applying_to_a_non_config_starts_from_an_empty_one(self):
        after = apply_change("nonsense", WorkerToolsChange(change_id="c", capability_ids=("x",)))
        assert isinstance(after, SeatConfig)
        assert after.tools == ("x",)

    def test_lookups_on_an_absent_section_or_entry_return_none(self):
        config = SeatConfig(seat=SEAT_WORKER)
        assert config.section("nope") is None
        assert config.entry("nope") is None

    def test_seat_configs_builds_a_map_from_a_sequence(self):
        from embodiment.config_lifecycle import seat_configs

        built = seat_configs(
            [SeatConfig(seat=SEAT_WORKER, tools=("a",)), SeatConfig(seat=SEAT_SENSES), "junk"]
        )
        assert set(built) == {SEAT_WORKER, SEAT_SENSES}
        assert seat_configs() == {}


class TestAdvanceBranches:
    def test_a_proposal_that_cannot_be_verified_is_reported_as_refused(self):
        life = ConfigLifecycle()
        life.propose(_prompt())
        report = life.advance()
        assert report.refused == ("chg-1",)
        assert report.applied == ()
        assert life.degradations[-1].code == CHANGE_VERIFIER_UNAVAILABLE

    def test_a_proposal_rejected_by_its_suite_is_reported_as_rejected(self):
        life = _lifecycle(verifier=_Verifier(passed=False))
        life.propose(_prompt())
        assert life.advance().rejected == ("chg-1",)

    def test_a_fresh_verification_is_reused_rather_than_re_run(self):
        verifier = _Verifier()
        life = _lifecycle(verifier=verifier)
        life.propose(_prompt())
        life.verify("chg-1")
        life.advance()
        assert len(verifier.requests) == 1

    def test_a_catalog_that_moved_between_verify_and_advance_rejects(self):
        life = _lifecycle()
        life.propose(_tools("chg-tools"))
        life.verify("chg-tools")
        report = life.advance(catalog=_catalog(extra=True))
        assert report.rejected == ("chg-tools",)
        assert life.proposal("chg-tools").state == STATE_REJECTED

    def test_a_terminal_proposal_is_skipped_by_a_later_pass(self):
        life = _lifecycle()
        life.propose(_prompt())
        life.advance()
        assert life.advance() == life.advance().__class__()

    def test_a_verifier_with_side_effects_on_the_live_config_cannot_smuggle_a_change(self):
        """The stamped candidate is computed BEFORE the suite runs, so a suite that
        moves the seat's configuration while it grades is caught by the freshness
        check rather than having its own side effect quietly ratified."""
        life = _lifecycle()

        def meddling(request: VerificationRequest) -> VerificationResult:
            life._configs[SEAT_WORKER] = SeatConfig(
                seat=SEAT_WORKER, prompt=(PromptSection("smuggled", "S"),)
            )
            return VerificationResult(passed=True, summary="ok")

        life.verifier = meddling
        life.propose(_prompt())
        report = life.advance()
        assert report.applied == ()
        assert report.refused == ("chg-1",)
        assert life.proposal("chg-1").state == STATE_PROPOSED
        assert life.degradations[-1].code == CHANGE_STALE_VERIFICATION

    def test_ending_a_run_by_id_finds_its_seat(self):
        life = _lifecycle()
        life.begin_run(SEAT_WORKER, run_id="drive-1")
        life.end_run("drive-1")
        assert life.is_idle(SEAT_WORKER) is True
        assert life.degradations == ()


class TestPackageSurface:
    def test_the_module_is_reachable_from_the_package(self):
        import embodiment

        assert embodiment.config_lifecycle is not None
        assert "config_lifecycle" in embodiment._SUBMODULES

    def test_no_name_is_hoisted_onto_the_package(self):
        """t3's precedent: reach without advertisement while the value is unmeasured."""
        import embodiment

        hoisted = {
            name for name, module in embodiment._LAZY_NAMES.items() if module == "config_lifecycle"
        }
        assert hoisted == set()
