"""The strategist's typed change units — the authority lattice, held by the data shape.

This is task ``t3``'s contract test. Four properties are load-bearing and every
one of them is pinned here, because the whole safety story of the config-change
redesign rests on them (spec claims ``c2``/``h6``, ``c15``/``h3``,
``c29``/``h13``):

1. **Refuse whole, never strip-and-keep.** An unknown key, an extra key, or a
   key that reaches past configuration authority into *action* authority refuses
   the entire unit and mints a :class:`~embodiment.config_change.ConfigRefusal`.
   The pattern is ``embodiment/scope.py``'s ``directive_from_payload``
   (scope.py:1288-1314), reused **by citation** — this module imports nothing
   from the advisory lane, and ``TestCitedNotCoupled`` proves it.
2. **``origin`` is required, and the lattice is structural.** A worker-originated
   unit aimed at a prompt-shaped target is a refused *shape*, not a policy
   check — senses' prompt text is unreachable from any worker-writable surface.
3. **Selection, never minting.** A tools or permissions unit names
   host-declared capability ids and nothing else. There is no field a tool
   *definition* could land in, a definition-shaped key refuses the unit whole,
   and an id the host never declared refuses it too.
4. **Nothing is silently dropped.** Every payload offered to
   :func:`~embodiment.config_change.admit_changes` comes back either as an
   accepted unit or as a recorded refusal; the count is conserved.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from embodiment.capability import (
    CAPABILITY_KIND_PERMISSION,
    CAPABILITY_KIND_TOOL,
    Capability,
    CapabilityCatalog,
)
from embodiment.config_change import (
    CAPABILITY_TARGETS,
    CHANGE_AUTHORITY,
    CHANGE_AUTHORITY_VIOLATION,
    CHANGE_INCOMPLETE,
    CHANGE_MALFORMED,
    CHANGE_NO_CATALOG,
    CHANGE_NO_ORIGIN,
    CHANGE_ORIGIN_FORBIDDEN,
    CHANGE_ORIGINS,
    CHANGE_REFUSAL_CODES,
    CHANGE_SEATS,
    CHANGE_STALE_CATALOG,
    CHANGE_TARGETS,
    CHANGE_UNITS,
    CHANGE_UNKNOWN_CAPABILITY,
    CHANGE_UNKNOWN_KEY,
    CHANGE_UNKNOWN_TARGET,
    FORBIDDEN_CHANGE_KEYS,
    KNOWLEDGE_TARGETS,
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    PROMPT_TARGETS,
    SEAT_SENSES,
    SEAT_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_SENSES_PERMISSIONS,
    TARGET_SENSES_PROMPTS,
    TARGET_WORKER_KNOWLEDGE,
    TARGET_WORKER_PERMISSIONS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    CapabilitySelection,
    ConfigChange,
    ConfigDegradation,
    ConfigRefusal,
    KnowledgeChange,
    PromptChange,
    SensesKnowledgeChange,
    SensesPermissionsChange,
    SensesPromptChange,
    WorkerKnowledgeChange,
    WorkerPermissionsChange,
    WorkerPromptChange,
    WorkerToolsChange,
    admit_changes,
    change_from_payload,
    declared_keys,
    revalidate,
)

MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "config_change.py"
CAPABILITY_MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "capability.py"


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        catalog_id="greenhouse-1",
        entries=(
            Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL),
            Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
            Capability(capability_id="net.egress", kind=CAPABILITY_KIND_PERMISSION),
        ),
    )


def _prompt_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target": TARGET_WORKER_PROMPTS,
        "change_id": "chg-1",
        "origin": ORIGIN_STRATEGIST,
        "reason": "the worker keeps re-reading the same file",
        "section": "working-style",
        "text": "Read a file once and keep what you read.",
    }
    payload.update(over)
    return payload


def _tools_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target": TARGET_WORKER_TOOLS,
        "change_id": "chg-2",
        "origin": ORIGIN_STRATEGIST,
        "reason": "writing is not needed for this objective",
        "capability_ids": ["fs.read"],
    }
    payload.update(over)
    return payload


def _knowledge_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target": TARGET_SENSES_KNOWLEDGE,
        "change_id": "chg-3",
        "origin": ORIGIN_WORKER,
        "reason": "the operator asked about sensor 7 twice",
        "entry_id": "sensor-7",
        "text": "Sensor 7 has been reporting since 09:12.",
    }
    payload.update(over)
    return payload


# ── 1. the vocabulary is closed, complete, and every code has a producer ──────


class TestTheVocabularyIsClosed:
    def test_the_seven_targets_are_the_operator_lattice(self) -> None:
        assert CHANGE_TARGETS == (
            TARGET_WORKER_TOOLS,
            TARGET_WORKER_PROMPTS,
            TARGET_WORKER_KNOWLEDGE,
            TARGET_WORKER_PERMISSIONS,
            TARGET_SENSES_PROMPTS,
            TARGET_SENSES_PERMISSIONS,
            TARGET_SENSES_KNOWLEDGE,
        )

    def test_every_target_has_exactly_one_typed_dataclass(self) -> None:
        assert set(CHANGE_UNITS) == set(CHANGE_TARGETS)
        assert len({id(unit) for unit in CHANGE_UNITS.values()}) == len(CHANGE_TARGETS)

    def test_every_unit_is_a_frozen_dataclass_naming_its_target(self) -> None:
        for target, unit in CHANGE_UNITS.items():
            assert is_dataclass(unit), target
            assert unit.TARGET == target
            with pytest.raises(FrozenInstanceError):
                unit().change_id = "x"  # type: ignore[misc]

    def test_the_three_shape_families_partition_the_targets(self) -> None:
        families = (PROMPT_TARGETS, CAPABILITY_TARGETS, KNOWLEDGE_TARGETS)
        flat = [target for family in families for target in family]
        assert sorted(flat) == sorted(CHANGE_TARGETS)
        assert len(flat) == len(set(flat))

    def test_seats_are_derived_from_the_target_not_declared_twice(self) -> None:
        assert CHANGE_SEATS == (SEAT_WORKER, SEAT_SENSES)
        assert WorkerToolsChange().seat == SEAT_WORKER
        assert SensesPromptChange().seat == SEAT_SENSES

    def test_every_refusal_code_is_prefixed_for_the_ledger(self) -> None:
        for code in CHANGE_REFUSAL_CODES:
            assert code.startswith("config-change-"), code

    def test_every_refusal_code_has_a_producer(self) -> None:
        """embodiment#18's lesson: a code nothing can mint is a lie in the ledger."""
        catalog = _catalog()
        produced = {
            change_from_payload("not a mapping")[1],
            change_from_payload({"target": TARGET_WORKER_PROMPTS, "command": "rm"})[1],
            change_from_payload({"target": "worker.souls", "origin": ORIGIN_STRATEGIST})[1],
            change_from_payload(_prompt_payload(urgency="high"))[1],
            change_from_payload(_prompt_payload(origin="root"))[1],
            change_from_payload(_prompt_payload(origin=ORIGIN_WORKER))[1],
            change_from_payload(_prompt_payload(change_id=""))[1],
            change_from_payload(_tools_payload())[1],
            change_from_payload(_tools_payload(capability_ids=["shell.exec"]), catalog=catalog)[1],
            revalidate(
                change_from_payload(_tools_payload(), catalog=catalog)[0],
                CapabilityCatalog(catalog_id="greenhouse-1", entries=catalog.entries[:1]),
            ),
        }
        codes = {refusal.code for refusal in produced if refusal is not None}
        assert codes == set(CHANGE_REFUSAL_CODES), sorted(set(CHANGE_REFUSAL_CODES) - codes)

    def test_a_refusal_IS_a_degradation_so_one_ledger_stream_folds_it(self) -> None:
        assert issubclass(ConfigRefusal, ConfigDegradation)
        refusal = change_from_payload(_prompt_payload(origin=ORIGIN_WORKER))[1]
        assert isinstance(refusal, ConfigDegradation)
        payload = refusal.to_dict()
        for key in ("code", "reason", "step_index", "model_turns", "seat", "target"):
            assert key in payload, key


# ── 2. refuse whole, never strip-and-keep ────────────────────────────────────


class TestRefuseWhole:
    def test_a_clean_payload_is_accepted(self) -> None:
        change, refusal = change_from_payload(_prompt_payload())
        assert refusal is None
        assert isinstance(change, WorkerPromptChange)
        assert change.section == "working-style"
        assert change.origin == ORIGIN_STRATEGIST

    def test_a_non_mapping_payload_is_refused(self) -> None:
        change, refusal = change_from_payload(["target", "worker.prompts"])
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_MALFORMED

    def test_an_extra_key_refuses_the_WHOLE_unit(self) -> None:
        change, refusal = change_from_payload(_prompt_payload(urgency="high"))
        assert change is None, "the unit survived with the extra key stripped"
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_KEY
        assert "urgency" in refusal.reason

    def test_an_unknown_target_refuses(self) -> None:
        change, refusal = change_from_payload(
            {"target": "worker.souls", "origin": ORIGIN_STRATEGIST}
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_TARGET
        assert "worker.souls" in refusal.reason

    def test_a_missing_target_refuses(self) -> None:
        change, refusal = change_from_payload({"origin": ORIGIN_STRATEGIST, "change_id": "c"})
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_TARGET

    @pytest.mark.parametrize("key", ["command", "shell", "code", "approve", "patch", "argv"])
    def test_an_action_authority_key_refuses_the_whole_unit(self, key: str) -> None:
        change, refusal = change_from_payload(_prompt_payload(**{key: "anything"}))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_AUTHORITY_VIOLATION
        assert key in refusal.reason

    def test_a_forbidden_key_is_caught_at_DEPTH(self) -> None:
        """A declared top-level key cannot smuggle an action key inside its value."""
        change, refusal = change_from_payload(
            _prompt_payload(text={"prose": {"nested": {"command": "rm -rf /"}}})
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_AUTHORITY_VIOLATION
        assert "command" in refusal.reason

    def test_a_forbidden_key_is_caught_inside_a_LIST(self) -> None:
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=[{"exec": "shell"}]),
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_AUTHORITY_VIOLATION

    def test_the_forbidden_key_check_runs_BEFORE_the_unknown_key_check(self) -> None:
        """A smuggled command reads as an authority violation, never as a typo."""
        change, refusal = change_from_payload(_prompt_payload(command="rm -rf /"))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_AUTHORITY_VIOLATION

    def test_the_ban_is_on_KEYS_not_on_prose(self) -> None:
        """A prompt that *reads* like an instruction is a legitimate prompt change."""
        change, refusal = change_from_payload(
            _prompt_payload(text="Run the full test suite before you finish. Approve nothing.")
        )
        assert refusal is None
        assert change is not None

    def test_a_pathologically_nested_payload_terminates(self) -> None:
        nested: Any = {"command": "rm"}
        for _ in range(50):
            nested = {"deeper": nested}
        change, refusal = change_from_payload(_prompt_payload(text=nested))
        assert (change is None) != (refusal is None)

    def test_a_stamped_provenance_key_cannot_be_supplied_by_the_payload(self) -> None:
        """``catalog_fingerprint`` is stamped by validation, never model-supplied."""
        change, refusal = change_from_payload(
            _tools_payload(catalog_fingerprint="0" * 64), catalog=_catalog()
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_KEY

    def test_declared_keys_excludes_the_stamped_ones(self) -> None:
        keys = declared_keys(TARGET_WORKER_TOOLS)
        assert "capability_ids" in keys
        assert "target" in keys
        assert "catalog_id" not in keys
        assert "catalog_fingerprint" not in keys

    def test_declared_keys_of_an_unknown_target_is_empty(self) -> None:
        assert declared_keys("worker.souls") == ()

    def test_nothing_raises_on_any_junk(self) -> None:
        for payload in (None, 0, "", [], {}, {"target": None}, {"target": {"a": 1}}):
            change, refusal = change_from_payload(payload)
            assert (change is None) != (refusal is None)


class TestCompleteness:
    def test_a_change_with_no_id_cannot_be_reverted_so_it_is_refused(self) -> None:
        change, refusal = change_from_payload(_prompt_payload(change_id=""))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_INCOMPLETE

    def test_a_prompt_change_naming_no_section_is_refused(self) -> None:
        change, refusal = change_from_payload(_prompt_payload(section=""))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_INCOMPLETE

    def test_a_prompt_change_may_clear_a_section(self) -> None:
        """An empty ``text`` is a real change — revert has to be expressible."""
        change, refusal = change_from_payload(_prompt_payload(text=""))
        assert refusal is None
        assert change is not None

    def test_a_knowledge_entry_with_no_text_says_nothing_so_it_is_refused(self) -> None:
        change, refusal = change_from_payload(_knowledge_payload(text=""))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_INCOMPLETE

    def test_a_knowledge_entry_with_no_entry_id_is_refused(self) -> None:
        change, refusal = change_from_payload(_knowledge_payload(entry_id=""))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_INCOMPLETE

    def test_an_empty_capability_selection_is_accepted(self) -> None:
        """Revoking everything must be expressible — it is the SAFE selection."""
        change, refusal = change_from_payload(_tools_payload(capability_ids=[]), catalog=_catalog())
        assert refusal is None
        assert change is not None
        assert change.capability_ids == ()


# ── 3. origin is required and the lattice is structural ──────────────────────


class TestOriginIsRequired:
    def test_a_missing_origin_is_refused(self) -> None:
        payload = _prompt_payload()
        del payload["origin"]
        change, refusal = change_from_payload(payload)
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_NO_ORIGIN

    def test_an_empty_origin_is_refused(self) -> None:
        change, refusal = change_from_payload(_prompt_payload(origin="  "))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_NO_ORIGIN

    def test_an_unknown_origin_is_refused(self) -> None:
        change, refusal = change_from_payload(_prompt_payload(origin="root"))
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_NO_ORIGIN
        assert "root" in refusal.reason

    def test_an_unattributed_knowledge_write_is_refused_whole(self) -> None:
        """c30/h20: an anonymous channel into the operator's ear is a refused shape."""
        payload = _knowledge_payload()
        del payload["origin"]
        change, refusal = change_from_payload(payload)
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_NO_ORIGIN

    def test_origin_is_a_field_of_every_unit(self) -> None:
        for unit in CHANGE_UNITS.values():
            assert "origin" in {f.name for f in fields(unit)}


class TestTheAuthorityLattice:
    """c29/h13 — the operator's lattice, enforced by shape rather than convention."""

    def test_the_lattice_covers_every_origin(self) -> None:
        assert set(CHANGE_AUTHORITY) == set(CHANGE_ORIGINS)

    def test_the_strategist_owns_all_seven_targets(self) -> None:
        assert CHANGE_AUTHORITY[ORIGIN_STRATEGIST] == frozenset(CHANGE_TARGETS)

    def test_the_worker_owns_exactly_the_senses_knowledge_block(self) -> None:
        assert CHANGE_AUTHORITY[ORIGIN_WORKER] == frozenset({TARGET_SENSES_KNOWLEDGE})

    def test_the_host_is_the_ground_authority_so_revert_is_expressible(self) -> None:
        assert CHANGE_AUTHORITY[ORIGIN_HOST] == frozenset(CHANGE_TARGETS)

    def test_no_prompt_target_is_reachable_from_the_worker(self) -> None:
        """The headline acceptance criterion, stated as the lattice states it."""
        for target in PROMPT_TARGETS:
            assert target not in CHANGE_AUTHORITY[ORIGIN_WORKER]

    @pytest.mark.parametrize("target", [TARGET_SENSES_PROMPTS, TARGET_WORKER_PROMPTS])
    def test_a_worker_originated_prompt_write_is_refused_whole(self, target: str) -> None:
        change, refusal = change_from_payload(_prompt_payload(target=target, origin=ORIGIN_WORKER))
        assert change is None, "the worker reached a prompt-shaped target"
        assert refusal is not None
        assert refusal.code == CHANGE_ORIGIN_FORBIDDEN
        assert ORIGIN_WORKER in refusal.reason
        assert target in refusal.reason
        assert refusal.target == target
        assert refusal.origin == ORIGIN_WORKER

    def test_a_worker_originated_senses_knowledge_write_is_ACCEPTED(self) -> None:
        change, refusal = change_from_payload(_knowledge_payload())
        assert refusal is None
        assert isinstance(change, SensesKnowledgeChange)
        assert change.origin == ORIGIN_WORKER

    @pytest.mark.parametrize(
        "target", [TARGET_WORKER_TOOLS, TARGET_WORKER_PERMISSIONS, TARGET_SENSES_PERMISSIONS]
    )
    def test_a_worker_cannot_grant_itself_or_senses_a_capability(self, target: str) -> None:
        change, refusal = change_from_payload(
            _tools_payload(target=target, origin=ORIGIN_WORKER), catalog=_catalog()
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_ORIGIN_FORBIDDEN

    def test_senses_prompt_text_is_unreachable_from_a_worker_writable_unit(self) -> None:
        """Structural, not conventional: no worker-writable unit has a prompt field."""
        worker_writable = [
            CHANGE_UNITS[target] for target in sorted(CHANGE_AUTHORITY[ORIGIN_WORKER])
        ]
        for unit in worker_writable:
            assert not issubclass(unit, PromptChange), unit.__name__
            assert "section" not in {f.name for f in fields(unit)}


# ── 4. selection, never minting ──────────────────────────────────────────────


class TestSelectionNeverMinting:
    """h11 — no configuration path grants an ability the host has not wired."""

    def test_no_capability_unit_has_a_field_a_definition_could_land_in(self) -> None:
        banned = {"schema", "parameters", "definition", "function", "handler", "implementation"}
        for target in CAPABILITY_TARGETS:
            names = {f.name for f in fields(CHANGE_UNITS[target])}
            assert not (names & banned), f"{target} can carry a definition: {names & banned}"

    def test_a_capability_unit_declares_only_ids(self) -> None:
        assert declared_keys(TARGET_WORKER_TOOLS) == (
            "target",
            "change_id",
            "origin",
            "reason",
            "capability_ids",
        )

    def test_a_free_form_tool_definition_is_a_refused_SHAPE(self) -> None:
        change, refusal = change_from_payload(
            {
                "target": TARGET_WORKER_TOOLS,
                "change_id": "chg-9",
                "origin": ORIGIN_STRATEGIST,
                "reason": "the worker needs a shell",
                "definition": {"name": "shell", "parameters": {"cmd": "string"}},
            },
            catalog=_catalog(),
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_AUTHORITY_VIOLATION

    def test_an_undeclared_capability_id_is_refused_whole(self) -> None:
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=["fs.read", "shell.exec"]), catalog=_catalog()
        )
        assert change is None, "an undeclared id survived alongside a declared one"
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_CAPABILITY
        assert "shell.exec" in refusal.reason

    def test_a_capability_of_the_WRONG_KIND_is_refused(self) -> None:
        """A permission id cannot be selected as a tool, or the reverse."""
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=["net.egress"]), catalog=_catalog()
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_CAPABILITY

    def test_a_permissions_unit_selects_permission_kinds(self) -> None:
        change, refusal = change_from_payload(
            {
                "target": TARGET_SENSES_PERMISSIONS,
                "change_id": "chg-7",
                "origin": ORIGIN_STRATEGIST,
                "reason": "senses does not need egress",
                "capability_ids": ["net.egress"],
            },
            catalog=_catalog(),
        )
        assert refusal is None
        assert isinstance(change, SensesPermissionsChange)

    def test_with_NO_catalog_a_capability_unit_is_refused_fail_closed(self) -> None:
        change, refusal = change_from_payload(_tools_payload())
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_NO_CATALOG

    def test_with_an_EMPTY_catalog_every_id_is_undeclared(self) -> None:
        change, refusal = change_from_payload(_tools_payload(), catalog=CapabilityCatalog())
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_CAPABILITY

    def test_a_prompt_or_knowledge_unit_needs_no_catalog(self) -> None:
        for payload in (_prompt_payload(), _knowledge_payload()):
            change, refusal = change_from_payload(payload)
            assert refusal is None
            assert change is not None

    def test_an_accepted_capability_unit_is_STAMPED_with_its_catalog(self) -> None:
        catalog = _catalog()
        change, refusal = change_from_payload(_tools_payload(), catalog=catalog)
        assert refusal is None
        assert isinstance(change, CapabilitySelection)
        assert change.catalog_id == catalog.catalog_id
        assert change.catalog_fingerprint == catalog.fingerprint


class TestRevalidationCatchesCatalogDrift:
    """The "stable across restarts" half of r2, handed to t4's apply gate."""

    def test_revalidating_against_the_same_catalog_passes(self) -> None:
        catalog = _catalog()
        change, _ = change_from_payload(_tools_payload(), catalog=catalog)
        assert revalidate(change, catalog) is None

    def test_a_changed_catalog_is_a_recorded_staleness_refusal(self) -> None:
        catalog = _catalog()
        change, _ = change_from_payload(_tools_payload(), catalog=catalog)
        shrunk = CapabilityCatalog(catalog_id="greenhouse-1", entries=catalog.entries[:1])
        refusal = revalidate(change, shrunk)
        assert refusal is not None
        assert refusal.code == CHANGE_STALE_CATALOG

    def test_a_vanished_capability_is_named(self) -> None:
        catalog = _catalog()
        change, _ = change_from_payload(
            _tools_payload(capability_ids=["fs.write"]), catalog=catalog
        )
        without = CapabilityCatalog(
            catalog_id="greenhouse-1",
            entries=tuple(c for c in catalog.entries if c.capability_id != "fs.write"),
        )
        refusal = revalidate(change, without)
        assert refusal is not None
        assert "fs.write" in refusal.reason

    def test_a_DIFFERENT_HOSTS_catalog_is_stale_even_at_the_same_fingerprint(self) -> None:
        """An id is only meaningful relative to the catalog that declared it."""
        catalog = _catalog()
        change, _ = change_from_payload(_tools_payload(), catalog=catalog)
        other_host = CapabilityCatalog(catalog_id="reachy-1", entries=catalog.entries)
        refusal = revalidate(change, other_host)
        assert refusal is not None
        assert refusal.code == CHANGE_STALE_CATALOG

    def test_revalidating_a_non_capability_unit_is_a_no_op(self) -> None:
        change, _ = change_from_payload(_prompt_payload())
        assert revalidate(change, _catalog()) is None

    def test_revalidate_never_raises_on_junk(self) -> None:
        assert revalidate(None, _catalog()) is None
        change, _ = change_from_payload(_tools_payload(), catalog=_catalog())
        assert revalidate(change, None) is not None


# ── 5. nothing is silently dropped ───────────────────────────────────────────


class TestNothingIsSilentlyDropped:
    def test_every_payload_comes_back_accepted_or_refused(self) -> None:
        payloads = [
            _prompt_payload(),
            _prompt_payload(change_id="chg-1b", urgency="high"),
            _knowledge_payload(),
            _prompt_payload(change_id="chg-1c", origin=ORIGIN_WORKER),
            "not a mapping",
            _tools_payload(),
        ]
        result = admit_changes(payloads, catalog=_catalog())
        assert len(result.accepted) + len(result.refusals) == len(payloads)
        assert len(result.accepted) == 3
        assert len(result.refusals) == 3

    def test_the_refusals_name_what_was_refused(self) -> None:
        result = admit_changes([_prompt_payload(urgency="high")], catalog=_catalog())
        assert result.accepted == ()
        assert result.refusals[0].change_id == "chg-1"
        assert result.refusals[0].target == TARGET_WORKER_PROMPTS

    def test_an_empty_offer_is_an_empty_result(self) -> None:
        result = admit_changes([], catalog=_catalog())
        assert result.accepted == ()
        assert result.refusals == ()

    def test_a_non_iterable_offer_degrades_rather_than_raising(self) -> None:
        result = admit_changes(None, catalog=_catalog())
        assert result.accepted == ()
        assert result.refusals == ()

    def test_the_result_is_frozen(self) -> None:
        result = admit_changes([], catalog=_catalog())
        with pytest.raises(FrozenInstanceError):
            result.accepted = ()  # type: ignore[misc]


class TestCoercionNeverRaises:
    """Degrade, never raise — a malformed field is a blank, never an exception."""

    def test_a_single_capability_id_written_as_a_string_is_wrapped(self) -> None:
        """Not split into characters — ``contract._coerce_omissions``' lesson."""
        change, refusal = change_from_payload(
            _tools_payload(capability_ids="fs.read"), catalog=_catalog()
        )
        assert refusal is None
        assert change is not None
        assert change.capability_ids == ("fs.read",)

    def test_a_null_capability_id_list_is_an_empty_selection(self) -> None:
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=None), catalog=_catalog()
        )
        assert refusal is None
        assert change is not None
        assert change.capability_ids == ()

    def test_a_scalar_capability_id_payload_is_coerced_and_then_refused(self) -> None:
        change, refusal = change_from_payload(_tools_payload(capability_ids=7), catalog=_catalog())
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_CAPABILITY
        assert "'7'" in refusal.reason

    def test_a_blank_capability_id_is_refused_rather_than_dropped(self) -> None:
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=["fs.read", "  "]), catalog=_catalog()
        )
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_CAPABILITY

    def test_more_than_one_undeclared_id_is_counted_in_the_reason(self) -> None:
        change, refusal = change_from_payload(
            _tools_payload(capability_ids=["a", "b", "c"]), catalog=_catalog()
        )
        assert change is None
        assert refusal is not None
        assert "and 2 more" in refusal.reason

    def test_a_knowledge_entry_may_supersede_another(self) -> None:
        change, refusal = change_from_payload(_knowledge_payload(supersedes="sensor-7-old"))
        assert refusal is None
        assert isinstance(change, KnowledgeChange)
        assert change.supersedes == "sensor-7-old"

    def test_a_field_whose_str_raises_becomes_a_blank_not_a_crash(self) -> None:
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

        change, refusal = change_from_payload(_prompt_payload(text=Hostile()))
        assert refusal is None
        assert change is not None
        assert change.text == ""

    def test_a_hostile_KEY_is_read_as_a_blank_and_refused(self) -> None:
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

            def __hash__(self) -> int:
                return 0

        change, refusal = change_from_payload(_prompt_payload(**{"x": 1}) | {Hostile(): 1})
        assert change is None
        assert refusal is not None
        assert refusal.code == CHANGE_UNKNOWN_KEY


# ── 6. cited, not coupled ────────────────────────────────────────────────────


class TestCitedNotCoupled:
    """The operator instruction, made checkable: reuse by citation, not by import."""

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
        for path in (MODULE, CAPABILITY_MODULE):
            leaked = self._imported(path) & banned
            assert not leaked, f"{path.name} couples to the advisory lane: {sorted(leaked)}"

    def test_the_import_closure_is_capability_plus_stdlib(self) -> None:
        """Both files are leaves, so the AST walk IS the transitive closure."""
        assert not {n for n in self._imported(CAPABILITY_MODULE) if n.startswith("embodiment")}
        internal = {n for n in self._imported(MODULE) if n.startswith("embodiment")}
        assert internal == {"embodiment.capability"}

    def test_scope_py_is_cited_in_prose(self) -> None:
        source = MODULE.read_text(encoding="utf-8")
        assert "scope.py" in source, "the refuse-whole pattern's origin is uncited"

    def test_the_forbidden_vocabulary_is_this_lanes_own(self) -> None:
        """Not imported from scope.py — a config unit bans a different set."""
        assert "definition" in FORBIDDEN_CHANGE_KEYS
        assert "parameters" in FORBIDDEN_CHANGE_KEYS
        assert "command" in FORBIDDEN_CHANGE_KEYS
        assert all(key == key.lower() for key in FORBIDDEN_CHANGE_KEYS)
        assert len(FORBIDDEN_CHANGE_KEYS) == len(set(FORBIDDEN_CHANGE_KEYS))

    def test_no_declared_key_is_also_a_forbidden_key(self) -> None:
        """A schema that bans its own vocabulary would refuse every clean unit."""
        for target in CHANGE_TARGETS:
            for key in declared_keys(target):
                assert key not in FORBIDDEN_CHANGE_KEYS, f"{target}.{key}"


class TestSerialization:
    """A unit round-trips, so t5's ledger and t4's apply record the same object."""

    @pytest.mark.parametrize(
        "payload", ["prompt", "tools", "knowledge"], ids=["prompt", "tools", "knowledge"]
    )
    def test_round_trips_through_a_dict(self, payload: str) -> None:
        catalog = _catalog()
        raw = {
            "prompt": _prompt_payload(),
            "tools": _tools_payload(),
            "knowledge": _knowledge_payload(),
        }[payload]
        change, refusal = change_from_payload(raw, catalog=catalog)
        assert refusal is None
        assert change is not None
        restored, again = change_from_payload(
            {k: v for k, v in change.to_dict().items() if k in declared_keys(change.target)},
            catalog=catalog,
        )
        assert again is None
        assert restored == change

    def test_to_dict_names_the_target_and_the_origin(self) -> None:
        change, _ = change_from_payload(_prompt_payload())
        assert change is not None
        data = change.to_dict()
        assert data["target"] == TARGET_WORKER_PROMPTS
        assert data["origin"] == ORIGIN_STRATEGIST
        assert data["seat"] == SEAT_WORKER

    def test_a_capability_units_payload_carries_its_catalog_stamp(self) -> None:
        catalog = _catalog()
        change, _ = change_from_payload(_tools_payload(), catalog=catalog)
        assert change is not None
        assert change.to_dict()["catalog_fingerprint"] == catalog.fingerprint

    def test_the_base_class_is_not_a_target(self) -> None:
        """``ConfigChange`` is an envelope; only its concrete subclasses are units."""
        assert ConfigChange.TARGET == ""
        assert ConfigChange not in CHANGE_UNITS.values()
        for base in (PromptChange, KnowledgeChange, CapabilitySelection):
            assert base not in CHANGE_UNITS.values()

    def test_every_concrete_unit_is_reachable_by_name(self) -> None:
        assert CHANGE_UNITS[TARGET_WORKER_TOOLS] is WorkerToolsChange
        assert CHANGE_UNITS[TARGET_WORKER_PROMPTS] is WorkerPromptChange
        assert CHANGE_UNITS[TARGET_WORKER_KNOWLEDGE] is WorkerKnowledgeChange
        assert CHANGE_UNITS[TARGET_WORKER_PERMISSIONS] is WorkerPermissionsChange
        assert CHANGE_UNITS[TARGET_SENSES_PROMPTS] is SensesPromptChange
        assert CHANGE_UNITS[TARGET_SENSES_PERMISSIONS] is SensesPermissionsChange
        assert CHANGE_UNITS[TARGET_SENSES_KNOWLEDGE] is SensesKnowledgeChange
