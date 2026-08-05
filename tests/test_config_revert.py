"""Revert-to-baseline and the ratchet re-check (task ``t6``).

This is the contract test for :mod:`embodiment.config_revert`. Two acceptance
criteria, each pinned by a named class or group:

1. **Reverting to baseline is always possible and exercised by test.**
   → :class:`TestRevertRestoresBaselineExactly`,
   :class:`TestRevertIsGatedLikeAnyOtherChange`,
   :class:`TestRevertIsAlwaysExpressible`, :class:`TestHonestGaps`.
2. **Successive gate-passing changes can be re-evaluated against a FIXED
   baseline, so drift each individual gate misses is detectable.**
   → :class:`TestRatchetCatchesWhatIncrementalGatesMiss`,
   :class:`TestRatchetGuardBookkeeping`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.capability import CAPABILITY_KIND_TOOL, Capability, CapabilityCatalog
from embodiment.config_change import (
    CHANGE_NO_CATALOG,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    SEAT_SENSES,
    SEAT_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    ConfigDegradation,
)
from embodiment.config_lifecycle import (
    ConfigLifecycle,
    PromptSection,
    SeatConfig,
    VerificationRequest,
    VerificationResult,
    compose_prompt,
)
from embodiment.config_revert import (
    CONFIG_RATCHET_CODES,
    CONFIG_RATCHET_FAILED,
    CONFIG_RATCHET_NO_BASELINE,
    CONFIG_RATCHET_NO_VERIFIER,
    CONFIG_REVERT_CODES,
    CONFIG_REVERT_SEAT_MISMATCH,
    ConfigBaseline,
    RatchetGuard,
    RatchetResult,
    RevertOutcome,
    compute_revert_changes,
    revert_to_baseline,
)

MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "config_revert.py"


# ── fixtures ──────────────────────────────────────────────────────────────────


def _catalog(*, extra: bool = False) -> CapabilityCatalog:
    entries = [
        Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL),
        Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
    ]
    if extra:
        entries.append(Capability(capability_id="fs.delete", kind=CAPABILITY_KIND_TOOL))
    return CapabilityCatalog(catalog_id="greenhouse-1", entries=tuple(entries))


class _AlwaysPass:
    def __init__(self) -> None:
        self.requests: list[VerificationRequest] = []

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        self.requests.append(request)
        return VerificationResult(passed=True, summary="ok", suite="always-pass", checks_run=1)


def _lifecycle(
    *, verifier: Any = None, catalog: Optional[CapabilityCatalog] = None
) -> ConfigLifecycle:
    return ConfigLifecycle(
        verifier=verifier if verifier is not None else _AlwaysPass(),
        catalog=catalog if catalog is not None else _catalog(),
    )


def _drift(life: ConfigLifecycle, seat: str, target: str, **payload: Any) -> None:
    """Propose-verify-apply one change, in one line."""
    base = {"target": target, "origin": ORIGIN_STRATEGIST, "reason": "drift"}
    base.update(payload)
    proposal = life.propose(base)
    assert proposal is not None, life.degradations[-1:] if life.degradations else "refused"
    life.verify(proposal.change_id)
    outcome = life.apply(proposal.change_id)
    assert outcome.applied, outcome.refusal or outcome.deferral


# ── 1. revert restores configuration state exactly (the common case) ────────


class TestRevertRestoresBaselineExactly:
    def test_a_fresh_seat_already_matches_an_empty_baseline(self):
        life = _lifecycle()
        baseline = SeatConfig(seat=SEAT_WORKER)
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.proposed == ()
        assert outcome.matches_baseline is True

    def test_reverting_a_capability_drift_restores_the_exact_set(self):
        life = _lifecycle()
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_TOOLS,
            change_id="d1",
            capability_ids=["fs.read", "fs.write"],
        )
        assert life.effective(SEAT_WORKER).tools == ("fs.read", "fs.write")
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline, catalog=_catalog())
        assert outcome.applied != ()
        assert life.effective(SEAT_WORKER).tools == ()
        assert outcome.matches_baseline is True
        assert life.effective(SEAT_WORKER).config_sha == baseline.config_sha

    def test_reverting_a_prompt_edit_to_an_existing_baseline_section_is_exact(self):
        life = _lifecycle()
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_PROMPTS,
            change_id="d0",
            section="style",
            text="baseline text",
        )
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_PROMPTS,
            change_id="d1",
            section="style",
            text="drifted text",
        )
        assert compose_prompt(life.effective(SEAT_WORKER)) == "drifted text"
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.matches_baseline is True
        assert life.effective(SEAT_WORKER).config_sha == baseline.config_sha
        assert compose_prompt(life.effective(SEAT_WORKER)) == "baseline text"

    def test_reverting_multiple_target_families_at_once_is_exact(self):
        life = _lifecycle()
        _drift(
            life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d0", section="style", text="base"
        )
        _drift(life, SEAT_WORKER, TARGET_WORKER_TOOLS, change_id="d0t", capability_ids=["fs.read"])
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_PROMPTS,
            change_id="d1",
            section="style",
            text="drifted",
        )
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_TOOLS,
            change_id="d1t",
            capability_ids=["fs.read", "fs.write"],
        )
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline, catalog=_catalog())
        assert outcome.matches_baseline is True
        assert life.effective(SEAT_WORKER).config_sha == baseline.config_sha

    def test_knowledge_attribution_is_restored_exactly_when_still_authorized(self):
        """senses.knowledge is worker-writable, so the original worker attribution
        survives a host-authored revert of the surrounding drift."""
        life = _lifecycle()
        _drift(
            life,
            SEAT_SENSES,
            TARGET_SENSES_KNOWLEDGE,
            change_id="d0",
            origin=ORIGIN_WORKER,
            entry_id="k1",
            text="orchid bed reads 12%",
        )
        baseline = life.effective(SEAT_SENSES)
        _drift(
            life,
            SEAT_SENSES,
            TARGET_SENSES_KNOWLEDGE,
            change_id="d1",
            origin=ORIGIN_WORKER,
            entry_id="k1",
            text="orchid bed reads 40%",
        )
        outcome = revert_to_baseline(life, SEAT_SENSES, baseline)
        assert outcome.matches_baseline is True
        assert life.effective(SEAT_SENSES).config_sha == baseline.config_sha
        assert life.effective(SEAT_SENSES).entry("k1").origin == ORIGIN_WORKER

    def test_a_second_revert_after_the_first_lands_has_nothing_left_to_do(self):
        life = _lifecycle()
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d0", section="a", text="base")
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d1", section="a", text="drifted"
        )
        first = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert first.matches_baseline is True
        second = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert second.proposed == ()
        assert second.matches_baseline is True


# ── 2. revert uses the SAME gate as any other change ─────────────────────────


class TestRevertIsGatedLikeAnyOtherChange:
    def test_revert_defers_while_the_seat_is_running(self):
        life = _lifecycle()
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d0", section="a", text="base")
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d1", section="a", text="drifted"
        )
        run = life.begin_run(SEAT_WORKER)
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.applied == ()
        assert outcome.deferred != ()
        assert life.effective(SEAT_WORKER).config_sha != baseline.config_sha
        life.end_run(run)
        # Not applied automatically — the caller pumps the lifecycle, exactly
        # as with any other proposal left pending mid-run.
        assert life.advance().applied == outcome.proposed
        assert life.effective(SEAT_WORKER).config_sha == baseline.config_sha

    def test_revert_never_touches_an_unrelated_pending_proposal(self):
        life = _lifecycle()
        baseline = life.effective(SEAT_WORKER)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d1", section="a", text="x")
        unrelated = life.propose(
            {
                "target": TARGET_WORKER_PROMPTS,
                "change_id": "unrelated",
                "origin": ORIGIN_STRATEGIST,
                "section": "b",
                "text": "someone else's proposal",
            }
        )
        assert unrelated is not None
        revert_to_baseline(life, SEAT_WORKER, baseline)
        # Still sitting exactly where it was: proposed, untouched by the revert.
        assert life.proposal("unrelated").state == "proposed"

    def test_revert_with_no_verifier_is_recorded_not_raised(self):
        life = ConfigLifecycle(catalog=_catalog())
        baseline = life.effective(SEAT_WORKER)
        _drift(
            _lifecycle(), SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="x", section="a", text="x"
        )
        life._configs[SEAT_WORKER] = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection("a", "drifted"),)
        )
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.applied == ()
        assert outcome.refused != ()
        assert outcome.matches_baseline is False


# ── 3. revert is always EXPRESSIBLE, regardless of history shape ────────────


class TestRevertIsAlwaysExpressible:
    def test_revert_is_expressible_after_many_intervening_changes(self):
        life = _lifecycle()
        _drift(
            life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d-base", section="a", text="base"
        )
        baseline = life.effective(SEAT_WORKER)
        for step in range(6):
            _drift(
                life,
                SEAT_WORKER,
                TARGET_WORKER_PROMPTS,
                change_id=f"d{step}",
                section="a",
                text=f"revision {step}",
            )
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.matches_baseline is True

    def test_revert_is_expressible_regardless_of_which_origin_drifted_it(self):
        """Every drifting origin's mess is undoable by the SAME host-authored revert."""
        life = _lifecycle()
        _drift(
            life,
            SEAT_SENSES,
            TARGET_SENSES_KNOWLEDGE,
            change_id="w0",
            origin=ORIGIN_WORKER,
            entry_id="k",
            text="baseline claim",
        )
        baseline = life.effective(SEAT_SENSES)
        _drift(
            life,
            SEAT_SENSES,
            TARGET_SENSES_KNOWLEDGE,
            change_id="w1",
            origin=ORIGIN_WORKER,
            entry_id="k",
            text="worker-written",
        )
        outcome = revert_to_baseline(life, SEAT_SENSES, baseline)
        assert outcome.matches_baseline is True

    def test_a_mismatched_baseline_seat_is_refused_before_proposing_anything(self):
        life = _lifecycle()
        baseline = SeatConfig(seat=SEAT_SENSES)
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        assert outcome.proposed == ()
        assert len(outcome.degradations) == 1
        assert outcome.degradations[0].code == CONFIG_REVERT_SEAT_MISMATCH

    def test_compute_revert_changes_is_pure_and_needs_no_lifecycle(self):
        current = SeatConfig(seat=SEAT_WORKER, tools=("fs.read",))
        baseline = SeatConfig(seat=SEAT_WORKER)
        payloads = compute_revert_changes(current, baseline)
        assert len(payloads) == 1
        assert payloads[0]["target"] == TARGET_WORKER_TOOLS
        assert payloads[0]["capability_ids"] == []

    def test_an_unknown_seat_computes_no_changes(self):
        assert compute_revert_changes(SeatConfig(seat="mystery"), SeatConfig(seat="mystery")) == ()


# ── 4. the honest gaps: structurally undeletable rows, made observable ──────


class TestHonestGaps:
    def test_a_prompt_section_added_after_baseline_is_functionally_cleared(self):
        life = _lifecycle()
        baseline = life.effective(SEAT_WORKER)
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_PROMPTS,
            change_id="d1",
            section="new-section",
            text="unbaselined",
        )
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        # compose_prompt filters empty sections, so behaviour matches baseline...
        assert compose_prompt(life.effective(SEAT_WORKER)) == compose_prompt(baseline)
        # ...but the module says so explicitly rather than claiming an exact match.
        assert outcome.residual_prompt_sections == ("new-section",)
        assert life.effective(SEAT_WORKER).section("new-section").text == ""

    def test_a_knowledge_entry_added_after_baseline_cannot_be_removed_and_is_reported(self):
        life = _lifecycle()
        baseline = life.effective(SEAT_SENSES)
        _drift(
            life,
            SEAT_SENSES,
            TARGET_SENSES_KNOWLEDGE,
            change_id="d1",
            origin=ORIGIN_WORKER,
            entry_id="new-entry",
            text="added after baseline",
        )
        outcome = revert_to_baseline(life, SEAT_SENSES, baseline)
        assert outcome.residual_knowledge_entries == ("new-entry",)
        assert outcome.matches_baseline is False
        # The entry is still there — genuinely undeletable, not silently dropped.
        assert life.effective(SEAT_SENSES).entry("new-entry") is not None

    def test_a_capability_shortfall_with_no_catalog_is_recorded_and_reported(self):
        life = ConfigLifecycle(verifier=_AlwaysPass(), catalog=_catalog())
        _drift(
            life,
            SEAT_WORKER,
            TARGET_WORKER_TOOLS,
            change_id="d0",
            capability_ids=["fs.read", "fs.write"],
        )
        baseline = life.effective(SEAT_WORKER)
        # The host's capability declaration becomes unavailable and something
        # else clears the seat's tools out from under it (standing in for
        # whatever drifted it — the point under test is the revert, not how
        # the drift happened).
        life._configs[SEAT_WORKER] = SeatConfig(seat=SEAT_WORKER)
        life.catalog = None
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline, catalog=None)
        assert outcome.matches_baseline is False
        assert outcome.refused_at_propose >= 1
        codes = [d.code for d in life.degradations]
        assert CHANGE_NO_CATALOG in codes


# ── 5. the ratchet re-check catches what per-change gates miss ──────────────


def _growth_limited_verifier(limit: int):
    def verifier(request: VerificationRequest) -> VerificationResult:
        before = len(compose_prompt(request.baseline))
        after = len(compose_prompt(request.candidate))
        growth = after - before
        passed = growth <= limit
        return VerificationResult(
            passed=passed,
            summary=f"grew by {growth} chars against a {limit}-char budget",
            suite="growth-guard",
            checks_run=1,
            checks_failed=0 if passed else 1,
        )

    return verifier


class TestRatchetCatchesWhatIncrementalGatesMiss:
    """The headline demonstration: three individually-passing changes, one failing sum."""

    def test_three_small_increments_each_pass_but_the_cumulative_drift_fails(self):
        life = _lifecycle(verifier=_growth_limited_verifier(limit=40))
        baseline = life.effective(SEAT_WORKER)  # 0 chars

        # Each step grows the prompt by 20 chars against what came immediately
        # before it (config_lifecycle.verify()'s own baseline argument) — every
        # individual gate passes (20 <= 40).
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s1", section="a", text="x" * 20)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s2", section="a", text="x" * 40)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s3", section="a", text="x" * 60)
        assert len(compose_prompt(life.effective(SEAT_WORKER))) == 60

        # Every incremental verify() call in the drive above passed:
        assert all(
            t.verdict != "failed" for t in life.transitions if t.change_id in ("s1", "s2", "s3")
        )

        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, baseline)
        result = guard.check(life, SEAT_WORKER)
        assert result.checked is True
        assert result.passed is False
        assert result.drifted is True
        assert guard.degradations[-1].code == CONFIG_RATCHET_FAILED

    def test_a_cumulative_drift_within_budget_passes_the_ratchet_check_too(self):
        life = _lifecycle(verifier=_growth_limited_verifier(limit=100))
        baseline = life.effective(SEAT_WORKER)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s1", section="a", text="x" * 20)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s2", section="a", text="x" * 40)

        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, baseline)
        result = guard.check(life, SEAT_WORKER)
        assert result.passed is True
        assert guard.degradations == ()

    def test_reverting_to_baseline_makes_the_ratchet_check_pass_again(self):
        life = _lifecycle(verifier=_growth_limited_verifier(limit=10))
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s0", section="a", text="")
        baseline = life.effective(SEAT_WORKER)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s1", section="a", text="x" * 5)
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, baseline)
        assert guard.check(life, SEAT_WORKER).passed is True

        # Push it over budget relative to baseline (growth is still <=10 per
        # step against the rolling prior state, so each individual gate keeps
        # passing) but now compare against the FIXED baseline again.
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s2", section="a", text="x" * 15)
        assert guard.check(life, SEAT_WORKER).passed is False

        revert_to_baseline(life, SEAT_WORKER, baseline)
        result = guard.check(life, SEAT_WORKER)
        assert result.passed is True
        assert result.drifted is False


class TestRatchetGuardBookkeeping:
    def test_a_check_with_no_registered_baseline_is_recorded_not_raised(self):
        life = _lifecycle()
        guard = RatchetGuard()
        result = guard.check(life, SEAT_WORKER)
        assert result.checked is False
        assert result.passed is None
        assert guard.degradations[-1].code == CONFIG_RATCHET_NO_BASELINE

    def test_a_check_with_no_verifier_wired_is_recorded_not_raised(self):
        life = ConfigLifecycle(catalog=_catalog())
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, life.effective(SEAT_WORKER))
        result = guard.check(life, SEAT_WORKER)
        assert result.checked is False
        assert result.passed is None
        assert guard.degradations[-1].code == CONFIG_RATCHET_NO_VERIFIER

    def test_a_verifier_that_raises_is_recorded_not_raised(self):
        def boom(request: VerificationRequest) -> VerificationResult:
            raise RuntimeError("suite harness fell over")

        life = _lifecycle(verifier=boom)
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, life.effective(SEAT_WORKER))
        result = guard.check(life, SEAT_WORKER)
        assert result.checked is False
        assert result.passed is None
        assert guard.degradations[-1].code == CONFIG_RATCHET_NO_VERIFIER
        assert "RuntimeError" in guard.degradations[-1].reason

    @pytest.mark.parametrize("returned", [True, "passed", {"passed": True}, object()])
    def test_a_verifier_answering_the_wrong_shape_is_recorded_not_raised(self, returned: Any):
        life = _lifecycle(verifier=lambda request: returned)
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, life.effective(SEAT_WORKER))
        result = guard.check(life, SEAT_WORKER)
        assert result.checked is False
        assert guard.degradations[-1].code == CONFIG_RATCHET_NO_VERIFIER

    def test_a_supplied_verifier_overrides_the_lifecycles_own(self):
        life = _lifecycle(verifier=_growth_limited_verifier(limit=1000))
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, life.effective(SEAT_WORKER))
        override = _growth_limited_verifier(limit=0)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="s1", section="a", text="x")
        assert guard.check(life, SEAT_WORKER, verifier=override).passed is False

    def test_results_and_degradations_are_append_only_and_sequenced(self):
        life = _lifecycle()
        guard = RatchetGuard()
        guard.baseline.set(SEAT_WORKER, life.effective(SEAT_WORKER))
        guard.check(life, SEAT_WORKER)
        guard.check(life, SEAT_WORKER)
        assert [r.sequence for r in guard.results] == [0, 1]

    def test_a_ratchet_result_serialises(self):
        data = RatchetResult(seat="worker", passed=True, suite="s").to_dict()
        assert data["seat"] == "worker"
        assert data["passed"] is True


# ── the fixed baseline registry ──────────────────────────────────────────────


class TestConfigBaseline:
    def test_a_fresh_registry_has_no_baseline(self):
        assert ConfigBaseline().get(SEAT_WORKER) is None

    def test_set_and_get_round_trip(self):
        registry = ConfigBaseline()
        config = SeatConfig(seat=SEAT_WORKER, tools=("a",))
        registry.set(SEAT_WORKER, config)
        assert registry.get(SEAT_WORKER) == config

    def test_junk_is_ignored_rather_than_fatal(self):
        registry = ConfigBaseline()
        registry.set(SEAT_WORKER, "not a config")
        assert registry.get(SEAT_WORKER) is None

    def test_capture_fixes_the_current_effective_config(self):
        life = _lifecycle()
        _drift(life, SEAT_WORKER, TARGET_WORKER_TOOLS, change_id="d1", capability_ids=["fs.read"])
        registry = ConfigBaseline()
        captured = registry.capture(life, SEAT_WORKER)
        assert captured.tools == ("fs.read",)
        assert registry.get(SEAT_WORKER).config_sha == captured.config_sha

    def test_capture_does_not_move_on_later_drift(self):
        """FIXED means fixed: later drift must never retroactively move it."""
        life = _lifecycle()
        registry = ConfigBaseline()
        registry.capture(life, SEAT_WORKER)
        anchor_sha = registry.get(SEAT_WORKER).config_sha
        _drift(life, SEAT_WORKER, TARGET_WORKER_TOOLS, change_id="d1", capability_ids=["fs.read"])
        assert registry.get(SEAT_WORKER).config_sha == anchor_sha

    def test_constructed_from_a_seats_mapping(self):
        config = SeatConfig(seat=SEAT_WORKER, tools=("x",))
        registry = ConfigBaseline({SEAT_WORKER: config, "junk": "nope"})
        assert registry.get(SEAT_WORKER) == config
        assert registry.get("junk") is None

    def test_seats_lists_every_registered_seat(self):
        registry = ConfigBaseline()
        registry.set(SEAT_WORKER, SeatConfig(seat=SEAT_WORKER))
        registry.set(SEAT_SENSES, SeatConfig(seat=SEAT_SENSES))
        assert set(registry.seats()) == {SEAT_WORKER, SEAT_SENSES}


# ── degrade-never-raise, on hostile input ────────────────────────────────────


class TestDegradeNeverRaise:
    def test_revert_to_baseline_never_raises_on_a_hostile_seat(self):
        life = _lifecycle()
        outcome = revert_to_baseline(life, None, SeatConfig())
        assert isinstance(outcome, RevertOutcome)

    def test_revert_to_baseline_never_raises_on_a_hostile_baseline(self):
        life = _lifecycle()
        outcome = revert_to_baseline(life, SEAT_WORKER, "not a config")  # type: ignore[arg-type]
        assert isinstance(outcome, RevertOutcome)

    def test_compute_revert_changes_never_raises_on_hostile_input(self):
        assert compute_revert_changes(None, None) == ()
        assert compute_revert_changes("nope", 7) == ()

    def test_ratchet_check_never_raises_on_a_hostile_seat(self):
        life = _lifecycle()
        guard = RatchetGuard()
        result = guard.check(life, None)
        assert isinstance(result, RatchetResult)


# ── serialization ─────────────────────────────────────────────────────────────


class TestSerialization:
    def test_a_revert_outcome_serialises(self):
        life = _lifecycle()
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d0", section="a", text="base")
        baseline = life.effective(SEAT_WORKER)
        _drift(life, SEAT_WORKER, TARGET_WORKER_PROMPTS, change_id="d1", section="a", text="x")
        outcome = revert_to_baseline(life, SEAT_WORKER, baseline)
        data = outcome.to_dict()
        assert data["seat"] == SEAT_WORKER
        assert data["matches_baseline"] is True
        assert isinstance(data["applied"], list)

    def test_a_degradation_carrying_outcome_serialises_its_degradations(self):
        outcome = revert_to_baseline(_lifecycle(), SEAT_WORKER, SeatConfig(seat=SEAT_SENSES))
        data = outcome.to_dict()
        assert data["degradations"][0]["code"] == CONFIG_REVERT_SEAT_MISMATCH


# ── the vocabulary is honest and non-overlapping ─────────────────────────────


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
            "CONFIG_REVERT_SEAT_MISMATCH",
            "CONFIG_RATCHET_NO_BASELINE",
            "CONFIG_RATCHET_NO_VERIFIER",
            "CONFIG_RATCHET_FAILED",
        ):
            assert constant in assigned, constant
            assert constant in used, f"{constant} is declared but nothing mints it"

    def test_every_code_is_namespaced_to_this_module(self):
        for code in CONFIG_REVERT_CODES + CONFIG_RATCHET_CODES:
            assert code.startswith("config-revert-") or code.startswith("config-ratchet-"), code

    def test_no_code_collides_with_a_sibling_moduels_vocabulary(self):
        from embodiment.config_change import CHANGE_REFUSAL_CODES
        from embodiment.config_ledger import CONFIG_LEDGER_DEGRADATION_CODES
        from embodiment.config_lifecycle import LIFECYCLE_CODES

        mine = set(CONFIG_REVERT_CODES) | set(CONFIG_RATCHET_CODES)
        others = (
            set(CHANGE_REFUSAL_CODES) | set(LIFECYCLE_CODES) | set(CONFIG_LEDGER_DEGRADATION_CODES)
        )
        assert not (mine & others)

    def test_a_revert_seat_mismatch_carries_the_shared_degradation_shape(self):
        outcome = revert_to_baseline(_lifecycle(), SEAT_WORKER, SeatConfig(seat=SEAT_SENSES))
        assert isinstance(outcome.degradations[0], ConfigDegradation)
        assert set(outcome.degradations[0].to_dict()) == {
            "code",
            "reason",
            "step_index",
            "model_turns",
            "seat",
            "target",
        }


# ── structure: cited, not coupled; no thread, no clock, no IO of its own ────


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
            "embodiment.config_ledger",
            "embodiment.config_events",
        ],
    )
    def test_the_module_reaches_no_forbidden_module(self, forbidden: str):
        assert forbidden not in _imported_modules(MODULE)

    def test_it_imports_exactly_the_two_schema_modules(self):
        embodiment_imports = {
            name for name in _imported_modules(MODULE) if name.startswith("embodiment")
        }
        assert embodiment_imports == {
            "embodiment.capability",
            "embodiment.config_change",
            "embodiment.config_lifecycle",
        }


class TestNoThreadNoClockNoIOOfItsOwn:
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


# ── package registration ──────────────────────────────────────────────────────


class TestPackageSurface:
    def test_the_module_is_reachable_from_the_package(self):
        import embodiment

        assert embodiment.config_revert is not None
        assert "config_revert" in embodiment._SUBMODULES

    def test_no_name_is_hoisted_onto_the_package(self):
        """t3's precedent: reach without advertisement while the value is unmeasured."""
        import embodiment

        hoisted = {
            name for name, module in embodiment._LAZY_NAMES.items() if module == "config_revert"
        }
        assert hoisted == set()
