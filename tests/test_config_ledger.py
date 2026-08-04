"""embodiment.config_ledger — the applied-change ledger + fail-closed persistence.

Task ``t5`` (issue #75, spec claims ``c8``/``h8``, ``c31``/``h21``). The
load-bearing acceptance criterion this file pins:

    "the persisted payload carries a schema version; an advisory-era or
    unknown-version payload is refused with one recorded degradation naming
    the fix, proven against a real advisory-era fixture — never a silent
    reinterpretation, never a crash."

``TestFailClosedAgainstARealAdvisoryFixture`` below is that proof. The fixture
is NOT hand-written: it is
``docs/live-test-results/scope-live-session-1-state.json``, the actual
``--state`` payload ``examples/scope_live_session.py`` wrote during live
session 1 (``examples/scope_live_session.py:1924`` wires ``--state`` to exactly
the :class:`~embodiment.scoped_run.ScopePersistence` port this module's
:class:`~embodiment.config_ledger.ConfigPersistence` is modelled on), loaded
from disk and handed to :class:`~embodiment.config_ledger.ConfigLedger`
through its ``load`` callable exactly as a host would.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.config_change import ConfigDegradation, WorkerPromptChange
from embodiment.config_events import ConfigEvent
from embodiment.config_ledger import (
    CONFIG_LEDGER_DEGRADATION_CODES,
    CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED,
    CONFIG_LEDGER_DEGRADED_LOAD_FAILED,
    CONFIG_LEDGER_DEGRADED_SAVE_FAILED,
    CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT,
    CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION,
    CONFIG_LEDGER_KIND,
    CONFIG_LEDGER_SCHEMA_VERSION,
    LEDGER_STATE_APPLIED,
    LEDGER_STATE_REVERTED,
    LEDGER_STATES,
    ConfigLedger,
    ConfigPersistence,
    LedgerEntry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = _REPO_ROOT / "embodiment" / "config_ledger.py"

#: The REAL advisory-era ``--state`` fixture: a live session's own persisted
#: durable-lane payload, not a hand-written stub. See the module docstring.
ADVISORY_FIXTURE = _REPO_ROOT / "docs" / "live-test-results" / "scope-live-session-1-state.json"


def _change(**over: Any) -> WorkerPromptChange:
    base: dict[str, Any] = {
        "change_id": "chg-1",
        "origin": "strategist",
        "reason": "the worker keeps re-reading the same file",
        "section": "working-style",
        "text": "Read a file once and keep what you read.",
    }
    base.update(over)
    return WorkerPromptChange(**base)


class _Store:
    """A minimal in-memory stand-in for a host's durable store."""

    def __init__(self, initial: Optional[dict[str, Any]] = None) -> None:
        self.payload: Optional[dict[str, Any]] = initial
        self.save_calls = 0
        self.load_calls = 0

    def load(self) -> Any:
        self.load_calls += 1
        return self.payload

    def save(self, payload: dict[str, Any]) -> None:
        self.save_calls += 1
        self.payload = payload

    def port(self) -> ConfigPersistence:
        return ConfigPersistence(load=self.load, save=self.save)


# ── the fixture actually exists and is what the module docstring claims ────────


class TestTheFixtureIsReal:
    def test_the_fixture_file_exists(self) -> None:
        assert ADVISORY_FIXTURE.is_file(), (
            f"{ADVISORY_FIXTURE} is missing — task t5's fail-closed proof depends on "
            "the real live-session --state payload, not a hand-written stub"
        )

    def test_the_fixture_is_the_advisory_lanes_own_shape(self) -> None:
        """Confirms this is genuinely an advisory-era ScopeRegister payload, not
        something that happens to look like one."""
        data = json.loads(ADVISORY_FIXTURE.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["lane"] == "durable"
        assert isinstance(data["accepted"], list) and len(data["accepted"]) >= 1
        # An advisory ScopeDirective entry, never a config-change unit.
        first = data["accepted"][0]
        assert "objective" in first
        assert "scope_id" in first
        assert "target" not in first  # the config-change vocabulary this is NOT


# ── the load-bearing proof ──────────────────────────────────────────────────────


class TestFailClosedAgainstARealAdvisoryFixture:
    """The acceptance criterion, proven against the real fixture."""

    @staticmethod
    def _load_fixture_ledger() -> tuple[ConfigLedger, dict[str, Any]]:
        raw = json.loads(ADVISORY_FIXTURE.read_text(encoding="utf-8"))
        store = _Store(initial=raw)
        ledger = ConfigLedger(persistence=store.port())
        return ledger, raw

    def test_construction_never_raises(self) -> None:
        # If this failed to construct, the test session itself would blow up —
        # so its mere presence in a green suite is part of the proof.
        ledger, _ = self._load_fixture_ledger()
        assert isinstance(ledger, ConfigLedger)

    def test_the_ledger_loads_empty_not_reinterpreted(self) -> None:
        ledger, _ = self._load_fixture_ledger()
        assert ledger.entries == ()

    def test_exactly_one_degradation_is_recorded(self) -> None:
        ledger, _ = self._load_fixture_ledger()
        assert len(ledger.degradations) == 1

    def test_the_degradation_names_the_right_code(self) -> None:
        ledger, _ = self._load_fixture_ledger()
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION

    def test_the_degradation_names_the_fix(self) -> None:
        ledger, raw = self._load_fixture_ledger()
        reason = ledger.degradations[0].reason
        # Names what was actually found (the advisory shape, its real values)...
        assert "advisory" in reason.lower()
        assert "ScopeRegister" in reason or "ScopePersistence" in reason
        assert str(raw["lane"]) in reason
        # ...and names the fix, not just the diagnosis.
        assert "fresh store" in reason or "migrate" in reason

    def test_writing_is_disabled_so_the_advisory_file_is_never_clobbered(self) -> None:
        ledger, _ = self._load_fixture_ledger()
        assert ledger.write_disabled is True

    def test_the_advisory_payload_on_disk_is_untouched(self) -> None:
        """The store's payload is never overwritten by this drive."""
        raw = json.loads(ADVISORY_FIXTURE.read_text(encoding="utf-8"))
        store = _Store(initial=dict(raw))
        ledger = ConfigLedger(persistence=store.port())
        # Attempting to apply a change must not silently start writing again.
        ledger.record_applied(_change())
        assert store.save_calls == 0
        assert store.payload == raw

    def test_a_later_apply_is_still_recorded_in_memory_even_though_unpersisted(self) -> None:
        """Degrading durability never degrades delivery (the C3 split this
        whole tier inherits from the advisory lane's own ScopePersistence)."""
        ledger, _ = self._load_fixture_ledger()
        event = ledger.record_applied(_change())
        assert isinstance(event, ConfigEvent)
        assert len(ledger.entries) == 1
        assert ledger.entries[0].state == LEDGER_STATE_APPLIED


# ── unknown-version, the general case (not only the advisory shape) ────────────


class TestFailClosedOnAnyUnknownVersion:
    def test_a_future_version_number_is_refused(self) -> None:
        store = _Store(
            initial={"kind": CONFIG_LEDGER_KIND, "config_schema_version": 999, "entries": []}
        )
        ledger = ConfigLedger(persistence=store.port())
        assert ledger.entries == ()
        assert len(ledger.degradations) == 1
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION
        assert "advisory" not in ledger.degradations[0].reason.lower()

    def test_a_missing_kind_with_no_advisory_markers_is_refused_generically(self) -> None:
        store = _Store(initial={"config_schema_version": CONFIG_LEDGER_SCHEMA_VERSION})
        ledger = ConfigLedger(persistence=store.port())
        assert ledger.entries == ()
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION

    def test_an_empty_mapping_is_refused_not_reinterpreted_as_empty_ledger(self) -> None:
        store = _Store(initial={})
        ledger = ConfigLedger(persistence=store.port())
        assert ledger.entries == ()
        assert len(ledger.degradations) == 1

    def test_a_non_mapping_payload_is_refused_never_raises(self) -> None:
        for junk in ("a string", 42, ["a", "list"], True):
            store = _Store(initial=junk)
            ledger = ConfigLedger(persistence=store.port())
            assert ledger.entries == ()
            assert len(ledger.degradations) == 1
            assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION

    def test_none_is_the_default_no_persistence_case_stays_a_no_op(self) -> None:
        """``load`` returning ``None`` and no ``load`` at all both mean 'nothing
        persisted yet' — the same leniency ``ScopeRegister.from_dict`` already
        extends to it, so this is an empty ledger, never a degradation. A fresh
        file-backed store returning ``None`` on its very first run must not
        read as something already gone wrong."""
        store = _Store(initial=None)
        ledger = ConfigLedger(persistence=store.port())
        assert ledger.entries == ()
        assert ledger.degradations == ()
        assert ledger.write_disabled is False

    def test_no_persistence_port_at_all_is_a_clean_empty_ledger(self) -> None:
        ledger = ConfigLedger()
        assert ledger.entries == ()
        assert ledger.degradations == ()
        assert ledger.write_disabled is False

    def test_a_port_with_no_load_callable_is_a_clean_empty_ledger(self) -> None:
        ledger = ConfigLedger(persistence=ConfigPersistence(save=lambda payload: None))
        assert ledger.entries == ()
        assert ledger.degradations == ()


# ── a valid, current-schema payload actually round-trips ───────────────────────


class TestValidPayloadRoundTrips:
    def test_a_correctly_versioned_empty_ledger_loads_clean(self) -> None:
        store = _Store(
            initial={"kind": CONFIG_LEDGER_KIND, "config_schema_version": 1, "entries": []}
        )
        ledger = ConfigLedger(persistence=store.port())
        assert ledger.entries == ()
        assert ledger.degradations == ()
        assert ledger.write_disabled is False

    def test_apply_then_persist_then_reload_reconstructs_the_entry(self) -> None:
        store = _Store()
        ledger = ConfigLedger(persistence=store.port())
        ledger.record_applied(_change(), step_index=4)
        assert store.save_calls == 1

        reloaded = ConfigLedger(persistence=store.port())
        assert len(reloaded.entries) == 1
        entry = reloaded.entries[0]
        assert entry.state == LEDGER_STATE_APPLIED
        assert entry.change_id == "chg-1"
        assert entry.seat == "worker"
        assert entry.target == "worker.prompts"
        assert entry.step_index == 4
        assert entry.unit["text"] == "Read a file once and keep what you read."

    def test_an_unreadable_single_entry_is_dropped_not_fatal(self) -> None:
        store = _Store(
            initial={
                "kind": CONFIG_LEDGER_KIND,
                "config_schema_version": 1,
                "entries": [
                    {"state": "applied", "change_id": "chg-good", "seat": "worker"},
                    {"state": "not-a-real-state", "change_id": "chg-bad"},
                    "not even a mapping",
                    {"state": "applied"},  # missing change_id
                ],
            }
        )
        ledger = ConfigLedger(persistence=store.port())
        assert len(ledger.entries) == 1
        assert ledger.entries[0].change_id == "chg-good"
        # The top-level schema matched, so this is NOT a ledger-level degradation
        # — dropping one bad row is not the same failure as an unversioned payload.
        assert ledger.degradations == ()


# ── revert ───────────────────────────────────────────────────────────────────


class TestRevert:
    def test_reverting_an_applied_change_records_both_facts(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_change())
        event = ledger.record_reverted("chg-1", reason="superseded by a better prompt")
        assert isinstance(event, ConfigEvent)
        assert event.kind == "config.change.reverted"
        assert len(ledger.entries) == 2
        assert ledger.entries[0].state == LEDGER_STATE_APPLIED
        assert ledger.entries[1].state == LEDGER_STATE_REVERTED
        assert ledger.entries[1].reason == "superseded by a better prompt"
        # The reverted entry still names what it reverted — the ledger alone
        # explains the whole history, not only the current state.
        assert ledger.entries[1].seat == "worker"
        assert ledger.entries[1].target == "worker.prompts"

    def test_reverting_an_unknown_change_id_is_a_recorded_degradation(self) -> None:
        ledger = ConfigLedger()
        event = ledger.record_reverted("never-applied")
        assert event.kind == "config.degradation"
        assert len(ledger.degradations) == 1
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT
        assert ledger.entries == ()

    def test_reverting_twice_is_a_recorded_no_op_the_second_time(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_change())
        ledger.record_reverted("chg-1")
        event = ledger.record_reverted("chg-1")
        assert event.kind == "config.degradation"
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED
        # Still only the original two entries — a repeated revert adds nothing.
        assert len(ledger.entries) == 2

    def test_revert_persists_like_an_apply_does(self) -> None:
        store = _Store()
        ledger = ConfigLedger(persistence=store.port())
        ledger.record_applied(_change())
        saves_after_apply = store.save_calls
        ledger.record_reverted("chg-1")
        assert store.save_calls == saves_after_apply + 1

    def test_a_degraded_revert_does_not_persist(self) -> None:
        store = _Store()
        ledger = ConfigLedger(persistence=store.port())
        ledger.record_reverted("never-applied")
        assert store.save_calls == 0


# ── load raising ─────────────────────────────────────────────────────────────


class TestLoadRaises:
    def test_a_raising_load_never_crashes_construction(self) -> None:
        def _boom() -> Any:
            raise RuntimeError("disk on fire")

        ledger = ConfigLedger(persistence=ConfigPersistence(load=_boom))
        assert ledger.entries == ()
        assert len(ledger.degradations) == 1
        assert ledger.degradations[0].code == CONFIG_LEDGER_DEGRADED_LOAD_FAILED

    def test_a_raising_load_disables_writing_for_the_drive(self) -> None:
        def _boom() -> Any:
            raise RuntimeError("disk on fire")

        saved: list[dict[str, Any]] = []
        ledger = ConfigLedger(
            persistence=ConfigPersistence(load=_boom, save=lambda payload: saved.append(payload))
        )
        assert ledger.write_disabled is True
        ledger.record_applied(_change())
        assert saved == []


# ── save raising ─────────────────────────────────────────────────────────────


class TestSaveRaises:
    def test_a_raising_save_is_recorded_once(self) -> None:
        calls = {"n": 0}

        def _boom(payload: dict[str, Any]) -> None:
            calls["n"] += 1
            raise RuntimeError("write failed")

        ledger = ConfigLedger(persistence=ConfigPersistence(save=_boom))
        ledger.record_applied(_change(change_id="chg-1"))
        ledger.record_applied(_change(change_id="chg-2"))
        assert calls["n"] == 1  # further writes are disabled, not retried
        codes = [d.code for d in ledger.degradations]
        assert codes.count(CONFIG_LEDGER_DEGRADED_SAVE_FAILED) == 1

    def test_delivery_survives_even_though_durability_did_not(self) -> None:
        def _boom(payload: dict[str, Any]) -> None:
            raise RuntimeError("write failed")

        ledger = ConfigLedger(persistence=ConfigPersistence(save=_boom))
        event = ledger.record_applied(_change())
        assert event.kind == "config.change.applied"
        assert len(ledger.entries) == 1


# ── the port itself ──────────────────────────────────────────────────────────


class TestConfigPersistence:
    def test_unwired_with_neither_callable(self) -> None:
        assert ConfigPersistence().wired is False

    def test_wired_with_only_load(self) -> None:
        assert ConfigPersistence(load=lambda: {}).wired is True

    def test_wired_with_only_save(self) -> None:
        assert ConfigPersistence(save=lambda payload: None).wired is True

    def test_frozen(self) -> None:
        port = ConfigPersistence()
        with pytest.raises(Exception):
            port.load = lambda: {}  # type: ignore[misc]

    def test_structurally_identical_to_scope_persistence(self) -> None:
        """Re-declared, not imported (see the module docstring) — but the same
        shape, so a host's mental model of one port transfers to the other."""
        from embodiment.scoped_run import ScopePersistence

        config_fields = {f.name for f in ConfigPersistence.__dataclass_fields__.values()}
        scope_fields = {f.name for f in ScopePersistence.__dataclass_fields__.values()}
        assert config_fields == scope_fields == {"load", "save"}


# ── LedgerEntry ──────────────────────────────────────────────────────────────


class TestLedgerEntry:
    def test_round_trips(self) -> None:
        entry = LedgerEntry(
            state=LEDGER_STATE_APPLIED,
            change_id="chg-1",
            seat="worker",
            target="worker.prompts",
            origin="strategist",
            reason="why",
            step_index=2,
            unit={"text": "hello"},
        )
        rebuilt = LedgerEntry.from_dict(entry.to_dict())
        assert rebuilt == entry

    def test_from_dict_rejects_non_mapping(self) -> None:
        assert LedgerEntry.from_dict("not a dict") is None
        assert LedgerEntry.from_dict(None) is None
        assert LedgerEntry.from_dict(["a", "list"]) is None

    def test_from_dict_rejects_unknown_state(self) -> None:
        assert LedgerEntry.from_dict({"state": "pending", "change_id": "x"}) is None

    def test_from_dict_rejects_blank_change_id(self) -> None:
        assert LedgerEntry.from_dict({"state": "applied", "change_id": ""}) is None
        assert LedgerEntry.from_dict({"state": "applied"}) is None

    def test_from_dict_tolerates_a_non_mapping_unit(self) -> None:
        entry = LedgerEntry.from_dict(
            {"state": "applied", "change_id": "x", "unit": "not a mapping"}
        )
        assert entry is not None
        assert entry.unit == {}

    def test_ledger_states_is_exactly_two(self) -> None:
        assert LEDGER_STATES == (LEDGER_STATE_APPLIED, LEDGER_STATE_REVERTED)


# ── the degradation vocabulary ───────────────────────────────────────────────


class TestDegradationVocabulary:
    def test_every_code_is_prefixed_for_the_ledger(self) -> None:
        for code in CONFIG_LEDGER_DEGRADATION_CODES:
            assert code.startswith("config-ledger-"), code

    def test_no_collision_with_config_changes_own_codes(self) -> None:
        from embodiment.config_change import CHANGE_REFUSAL_CODES

        assert not set(CONFIG_LEDGER_DEGRADATION_CODES) & set(CHANGE_REFUSAL_CODES)

    def test_codes_are_unique(self) -> None:
        assert len(CONFIG_LEDGER_DEGRADATION_CODES) == len(set(CONFIG_LEDGER_DEGRADATION_CODES))

    def test_every_degradation_this_module_records_is_a_config_degradation(self) -> None:
        ledger = ConfigLedger(persistence=ConfigPersistence(load=lambda: "junk"))
        assert all(isinstance(d, ConfigDegradation) for d in ledger.degradations)


# ── never raises, even on a hostile caller ──────────────────────────────────


class TestNeverRaisesOnAHostileChange:
    """``record_applied`` reads defensively — task t4 has not landed in this
    wave, so nothing here can assume it will only ever be handed a real,
    well-formed :class:`~embodiment.config_change.ConfigChange`."""

    class _Hostile:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"reading {name} explodes")

    def test_record_applied_survives_a_hostile_change(self) -> None:
        ledger = ConfigLedger()
        event = ledger.record_applied(self._Hostile())  # type: ignore[arg-type]
        assert event.kind == "config.change.applied"
        assert len(ledger.entries) == 1
        assert ledger.entries[0].unit == {}

    def test_record_applied_survives_a_change_whose_to_dict_raises(self) -> None:
        class _BadToDict:
            change_id = "chg-1"
            seat = "worker"
            target = "worker.prompts"
            origin = "strategist"
            reason = "why"

            def to_dict(self) -> dict[str, Any]:
                raise RuntimeError("nope")

        ledger = ConfigLedger()
        event = ledger.record_applied(_BadToDict())  # type: ignore[arg-type]
        assert event.kind == "config.change.applied"
        assert ledger.entries[0].unit == {}
        assert ledger.entries[0].change_id == "chg-1"

    def test_record_applied_survives_a_change_whose_to_dict_returns_junk(self) -> None:
        class _JunkToDict:
            change_id = "chg-1"
            seat = "worker"
            target = "worker.prompts"
            origin = "strategist"
            reason = "why"

            def to_dict(self) -> Any:
                return "not a mapping"

        ledger = ConfigLedger()
        ledger.record_applied(_JunkToDict())  # type: ignore[arg-type]
        assert ledger.entries[0].unit == {}


# ── citation discipline ──────────────────────────────────────────────────────


class TestCitedNotCoupled:
    """Follows ``embodiment/config_change.py``'s ``TestCitedNotCoupled`` precedent."""

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
            "embodiment.loop",
        }
        leaked = self._imported(MODULE) & banned
        assert not leaked, f"config_ledger.py couples to the advisory lane: {sorted(leaked)}"

    def test_the_import_closure_is_config_change_and_config_events_plus_stdlib(self) -> None:
        internal = {n for n in self._imported(MODULE) if n.startswith("embodiment")}
        assert internal == {"embodiment.config_change", "embodiment.config_events"}


# ── package registration (following t3's precedent) ─────────────────────────


class TestPackageRegistration:
    def test_reachable_as_a_submodule(self) -> None:
        import embodiment

        assert "config_ledger" in embodiment._SUBMODULES
        assert "config_events" in embodiment._SUBMODULES

    def test_no_name_hoisted_into_lazy_names(self) -> None:
        """t3's precedent: reachable, not advertised, until the ScopeBench
        re-run says otherwise (task t13/t14)."""
        import embodiment

        ledger_names = {"ConfigLedger", "ConfigPersistence", "LedgerEntry"}
        event_names = {"ConfigEvent", "proposed_event", "applied_event"}
        assert not (ledger_names & set(embodiment._LAZY_NAMES))
        assert not (event_names & set(embodiment._LAZY_NAMES))
