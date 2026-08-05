"""embodiment.config_events — translating config-lane activity (task t5, c8/h8).

The acceptance criterion this file pins: "proposed/verified/applied/rejected/
reverted events emitted through the ``scope_events`` translation pattern" —
read as five builders, each a pure function, each defensive against a hostile
or malformed input, sharing one envelope builder, exactly the shape
``embodiment/scope_events.py`` already holds for the advisory lane. Plus the
citation discipline ``embodiment/config_change.py`` established for this whole
tier: no import of the advisory lane's modules.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from embodiment.config_change import (
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_WORKER_PROMPTS,
    ConfigDegradation,
    ConfigRefusal,
    WorkerPromptChange,
)
from embodiment.config_events import (
    CONFIG_EVENT_APPLIED,
    CONFIG_EVENT_DEGRADATION,
    CONFIG_EVENT_KINDS,
    CONFIG_EVENT_PROPOSED,
    CONFIG_EVENT_REJECTED,
    CONFIG_EVENT_REVERTED,
    CONFIG_EVENT_VERIFIED,
    ConfigEvent,
    applied_event,
    degradation_event,
    proposed_event,
    rejected_event,
    reverted_event,
    verified_event,
)

MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "config_events.py"


def _change(**over: Any) -> WorkerPromptChange:
    base: dict[str, Any] = {
        "change_id": "chg-1",
        "origin": ORIGIN_STRATEGIST,
        "reason": "the worker keeps re-reading the same file",
        "section": "working-style",
        "text": "Read a file once and keep what you read.",
    }
    base.update(over)
    return WorkerPromptChange(**base)


class TestTheVocabularyIsClosed:
    def test_five_lifecycle_kinds_plus_one_degradation_kind(self) -> None:
        assert CONFIG_EVENT_KINDS == (
            CONFIG_EVENT_PROPOSED,
            CONFIG_EVENT_VERIFIED,
            CONFIG_EVENT_APPLIED,
            CONFIG_EVENT_REJECTED,
            CONFIG_EVENT_REVERTED,
            CONFIG_EVENT_DEGRADATION,
        )

    def test_every_kind_is_namespaced_config_dot(self) -> None:
        for kind in CONFIG_EVENT_KINDS:
            assert kind.startswith("config.")

    def test_kinds_are_unique(self) -> None:
        assert len(CONFIG_EVENT_KINDS) == len(set(CONFIG_EVENT_KINDS))


class TestProposedEvent:
    def test_carries_the_changes_own_identity(self) -> None:
        change = _change()
        event = proposed_event(change, step_index=3)
        assert isinstance(event, ConfigEvent)
        assert event.kind == CONFIG_EVENT_PROPOSED
        assert event.data["change_id"] == "chg-1"
        assert event.data["seat"] == "worker"
        assert event.data["target"] == TARGET_WORKER_PROMPTS
        assert event.data["origin"] == ORIGIN_STRATEGIST
        assert event.data["step_index"] == 3
        assert event.data["applied"] is False

    def test_defaults_the_reason_when_the_change_gave_none(self) -> None:
        change = _change(reason="")
        event = proposed_event(change)
        assert "proposed" in event.detail

    def test_carries_the_strategist_identity_when_given(self) -> None:
        event = proposed_event(_change(), model="unsloth/Qwen3.6-27B-NVFP4", role="cortex")
        assert event.data["model"] == "unsloth/Qwen3.6-27B-NVFP4"
        assert event.data["role"] == "cortex"

    def test_absent_identity_is_byte_identical_empty(self) -> None:
        """No model/role supplied ⇒ empty, never a fabricated default."""
        event = proposed_event(_change())
        assert event.data["model"] == ""
        assert event.data["role"] == ""


class TestVerifiedEvent:
    def test_fires_on_a_pass(self) -> None:
        event = verified_event(_change(), passed=True, suite="worker-prompts")
        assert event.kind == CONFIG_EVENT_VERIFIED
        assert event.data["passed"] is True
        assert "passed" in event.detail

    def test_fires_on_a_fail_too(self) -> None:
        """A failed suite still completed a verification — same kind, passed=False."""
        event = verified_event(_change(), passed=False, suite="worker-prompts")
        assert event.kind == CONFIG_EVENT_VERIFIED
        assert event.data["passed"] is False
        assert "failed" in event.detail

    def test_an_explicit_reason_overrides_the_generated_one(self) -> None:
        event = verified_event(_change(), passed=False, reason="the suite timed out")
        assert event.detail == "the suite timed out"


class TestAppliedEvent:
    def test_applied_is_true(self) -> None:
        event = applied_event(_change(), step_index=5)
        assert event.kind == CONFIG_EVENT_APPLIED
        assert event.data["applied"] is True
        assert event.data["step_index"] == 5
        assert event.data["change_id"] == "chg-1"


class TestRejectedEvent:
    def test_translates_a_real_config_refusal(self) -> None:
        refusal = ConfigRefusal(
            code="config-change-origin-forbidden",
            reason="worker does not own senses.prompts",
            seat="senses",
            target=TARGET_WORKER_PROMPTS,
            change_id="chg-9",
            origin=ORIGIN_WORKER,
        )
        event = rejected_event(refusal, step_index=2)
        assert event.kind == CONFIG_EVENT_REJECTED
        assert event.data["code"] == "config-change-origin-forbidden"
        assert event.data["reason"] == "worker does not own senses.prompts"
        assert event.data["seat"] == "senses"
        assert event.data["change_id"] == "chg-9"
        assert event.data["origin"] == ORIGIN_WORKER
        assert event.data["applied"] is False
        assert event.data["step_index"] == 2


class TestRevertedEvent:
    def test_takes_plain_scalars_no_object_required(self) -> None:
        event = reverted_event(
            "chg-1",
            seat="worker",
            target=TARGET_WORKER_PROMPTS,
            origin=ORIGIN_HOST,
            reason="reverted to baseline",
            step_index=7,
        )
        assert event.kind == CONFIG_EVENT_REVERTED
        assert event.data["change_id"] == "chg-1"
        assert event.data["origin"] == ORIGIN_HOST
        assert event.detail == "reverted to baseline"
        assert event.data["step_index"] == 7
        assert event.data["applied"] is False

    def test_default_reason_when_none_given(self) -> None:
        event = reverted_event("chg-1")
        assert "reverted" in event.detail


class TestDegradationEvent:
    def test_translates_a_ledger_level_degradation(self) -> None:
        entry = ConfigDegradation(
            code="config-ledger-unknown-schema-version",
            reason="the persisted payload has no recognizable schema",
            seat="",
            target="",
        )
        event = degradation_event(entry, step_index=1)
        assert event.kind == CONFIG_EVENT_DEGRADATION
        assert event.data["code"] == "config-ledger-unknown-schema-version"
        assert event.data["change_id"] == ""
        assert event.data["origin"] == ""
        assert event.data["applied"] is False

    def test_a_refusal_translates_too_since_it_IS_a_degradation(self) -> None:
        refusal = ConfigRefusal(
            code="config-change-unattributed",
            reason="no origin",
            change_id="chg-2",
            origin="",
            seat=TARGET_SENSES_KNOWLEDGE.split(".")[0],
            target=TARGET_SENSES_KNOWLEDGE,
        )
        event = degradation_event(refusal)
        assert event.kind == CONFIG_EVENT_DEGRADATION
        assert event.data["change_id"] == "chg-2"


class TestNeverRaises:
    """Every builder is fed a hostile object and must still return an event."""

    class _Hostile:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"reading {name} explodes")

    def test_proposed_event_survives_a_hostile_change(self) -> None:
        event = proposed_event(self._Hostile())  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)
        assert event.kind == CONFIG_EVENT_PROPOSED

    def test_applied_event_survives_a_hostile_change(self) -> None:
        event = applied_event(self._Hostile())  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)

    def test_rejected_event_survives_a_hostile_refusal(self) -> None:
        event = rejected_event(self._Hostile())  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)

    def test_degradation_event_survives_a_hostile_entry(self) -> None:
        event = degradation_event(self._Hostile())  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)

    def test_verified_event_survives_a_hostile_change(self) -> None:
        event = verified_event(self._Hostile(), passed=False)  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)

    def test_an_unstringable_reason_renders_blank_not_a_crash(self) -> None:
        class _Unstringable:
            def __str__(self) -> str:  # pragma: no cover - exercised via __str__
                raise RuntimeError("nope")

        class _Change:
            change_id = "chg-1"
            seat = "worker"
            target = TARGET_WORKER_PROMPTS
            origin = ORIGIN_STRATEGIST
            reason = _Unstringable()

        event = proposed_event(_Change())  # type: ignore[arg-type]
        assert isinstance(event, ConfigEvent)


class TestEventShape:
    def test_config_event_is_field_for_field_the_scope_event_shape(self) -> None:
        from embodiment.scope_events import ScopeEvent

        config_fields = {f.name for f in ConfigEvent.__dataclass_fields__.values()}
        scope_fields = {f.name for f in ScopeEvent.__dataclass_fields__.values()}
        assert config_fields == scope_fields == {"kind", "detail", "data"}

    def test_frozen(self) -> None:
        event = proposed_event(_change())
        with pytest.raises(FrozenInstanceError):
            event.kind = "tampered"  # type: ignore[misc]


class TestCitedNotCoupled:
    """The advisory lane stays byte-stable: this module imports it from nowhere."""

    @staticmethod
    def _imported(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                reached.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                reached.add(node.module)
        return reached

    def test_the_advisory_lane_is_not_imported(self) -> None:
        banned = {
            "embodiment.scope",
            "embodiment.scoped_run",
            "embodiment.strategist_runner",
            "embodiment.scope_events",
            "embodiment.loop",
        }
        leaked = self._imported(MODULE) & banned
        assert not leaked, f"config_events.py couples to the advisory lane: {sorted(leaked)}"

    def test_the_import_closure_is_config_change_plus_stdlib(self) -> None:
        internal = {n for n in self._imported(MODULE) if n.startswith("embodiment")}
        assert internal == {"embodiment.config_change"}
