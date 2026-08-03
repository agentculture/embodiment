"""Seat wiring — explicit seat-to-role config, resolved by role name (task ``t7``).

Covers spec claim ``c2`` / honesty condition ``h2`` and plan task ``t7``'s three
acceptance criteria:

1. **The example config names lobes roles only; no scope module or example
   parses a model name string to infer a seat.**
   → :class:`TestNoModelNameParsing` — a grep-level absence check (mirroring
   ``tests/test_scopebench.py::test_no_seat_is_resolved_from_a_model_name``)
   plus a *behavioural* proof: swapping which role's ``model`` field looks like
   which does not move a seat (:meth:`TestNoModelNameParsing.
   test_swapping_model_strings_between_roles_does_not_move_a_seat`).
2. **A missing role on the gateway degrades to actor-only with a recorded
   degradation, never a raise.**
   → :class:`TestMissingRoleDegrades`, :class:`TestNotReadyRoleDegrades`,
   :class:`TestMalformedInputNeverRaises`, and
   :class:`TestGovernedByDegradesToActorOnly` (the composition-level proof).
3. **The wiring lives under ``examples/scope/``.**
   → this file imports :mod:`examples.scope.seats`; nowhere else does the
   module exist (:data:`examples.scope.seats.__file__`'s own parent directory
   is asserted against).

The fixture payloads below follow the shape of the committed
``docs/live-test-results/*/capabilities.json`` probes, extended with the
``worker`` role per the live gateway advert probed 2026-08-03 (CLAUDE.md,
this plan's own frame s8): ``cortex`` local and ready, ``worker`` proxied via
``hosted_by`` and ready, ``senses`` proxied and ready, ``muse`` absent here
entirely (this module never resolves a muse seat -- v1 composition is
strategist-only, spec decision, confirmed claim ``c32``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from embodiment.scoped_run import ScopeGovernor
from embodiment.strategist_runner import STRATEGIST_ROLE
from examples.scope import seats

MODULE_PATH = Path(seats.__file__)
SOURCE = MODULE_PATH.read_text(encoding="utf-8")


def _tree(path: Path = MODULE_PATH) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ── fixtures: /capabilities-shaped payloads, never dialled ───────────────────


def _role(model: str, *, ready: bool = True, endpoint: str = "http://localhost:8001", **extra: Any):
    entry = {"role": "unused-field", "model": model, "endpoint": endpoint, "ready": ready}
    entry.update(extra)
    return entry


def full_capabilities() -> dict[str, Any]:
    """cortex local+ready, worker proxied+ready, senses proxied+ready -- the
    2026-08-03 live advert this plan's frame recorded (s8)."""
    return {
        "cortex": _role("unsloth/Qwen3.6-27B-NVFP4", ready=True),
        "worker": _role(
            "unsloth/Qwen3.6-35B-A3B-NVFP4",
            ready=True,
            hosted_by="http://thor.tail0be7e0.ts.net:8000",
            proxied=True,
        ),
        "senses": _role(
            "coolthor/gemma-4-12B-it-NVFP4A16",
            ready=True,
            hosted_by="http://orin.tail0be7e0.ts.net:8000",
            proxied=True,
        ),
        "muse": _role("nvidia/Gemma-4-31B-IT-NVFP4", ready=False),
    }


# ── 1. no model-name parsing ──────────────────────────────────────────────────


class TestNoModelNameParsing:
    def test_the_module_names_no_model(self) -> None:
        for token in (
            "Qwen",
            "Gemma",
            "NVFP4",
            "unsloth/",
            "nvidia/",
            "coolthor/",
            "sakamakismile/",
        ):
            assert token not in SOURCE, f"seats.py names a model: {token}"

    def test_the_module_sniffs_no_model_string(self) -> None:
        lowered = SOURCE.lower()
        for token in ("startswith(", "model_name", "infer_role", "guess"):
            assert token not in lowered, token

    def test_no_module_reaches_the_actor_loop(self) -> None:
        for node in ast.walk(_tree()):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "embodiment.loop"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "embodiment.loop"

    def test_no_module_imports_a_transport(self) -> None:
        banned = {"socket", "http", "urllib", "httpx", "requests", "aiohttp", "ssl", "asyncio"}
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned

    def test_no_module_introduces_a_timeout_constant(self) -> None:
        for node in _tree().body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(
                node.value.value, (int, float)
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert not any(
                        hint in target.id for hint in ("TIMEOUT", "DEADLINE", "BACKOFF")
                    ), target.id

    def test_swapping_model_strings_between_roles_does_not_move_a_seat(self) -> None:
        """The behavioural proof honesty condition h2 actually asks for: resolution
        keys off the /capabilities dict key, never off what the model field says."""
        caps = full_capabilities()
        # give the worker entry the exact model string the cortex entry carries
        caps["worker"]["model"] = caps["cortex"]["model"]
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is not None
        assert resolution.strategist.role == seats.ROLE_CORTEX
        assert resolution.actor is not None
        assert resolution.actor.role == seats.ROLE_WORKER
        # the actor seat still carries the (now-identical) model as plain data
        assert resolution.actor.model == caps["cortex"]["model"]

    def test_the_seat_role_table_names_only_declared_lobes_roles(self) -> None:
        assert seats.SEAT_ROLES[seats.SEAT_STRATEGIST] == seats.ROLE_CORTEX
        assert seats.SEAT_ROLES[seats.SEAT_ACTOR] == seats.ROLE_WORKER
        assert seats.SEAT_ROLES[seats.SEAT_SENSES] == seats.ROLE_SENSES

    def test_the_strategist_seat_name_matches_the_runner_default_role(self) -> None:
        """Pinned by test rather than by import (this module's own convention,
        matching examples/arch_arms.py and examples/scope/scopebench.py's
        independent redeclaration of the same role-name triple)."""
        assert seats.SEAT_STRATEGIST == STRATEGIST_ROLE == "strategist"


# ── 2a. a fully-present gateway resolves every seat ───────────────────────────


class TestFullResolution:
    def test_every_seat_resolves(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        assert resolution.strategist is not None
        assert resolution.actor is not None
        assert resolution.senses is not None

    def test_no_degradations_when_every_role_is_ready(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        assert resolution.degradations == ()

    def test_actor_only_is_false(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        assert resolution.actor_only is False

    def test_strategist_dial_carries_the_advertised_fields(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        dial = resolution.strategist
        assert dial.seat == seats.SEAT_STRATEGIST
        assert dial.role == seats.ROLE_CORTEX
        assert dial.model == "unsloth/Qwen3.6-27B-NVFP4"
        assert dial.endpoint == "http://localhost:8001"

    def test_actor_dial_carries_the_proxied_fields(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        dial = resolution.actor
        assert dial.hosted_by == "http://thor.tail0be7e0.ts.net:8000"
        assert dial.proxied is True

    def test_to_dict_is_json_ready(self) -> None:
        import json

        resolution = seats.resolve_seats(full_capabilities())
        payload = resolution.to_dict()
        rendered = json.dumps(payload)
        assert "strategist" in rendered
        assert payload["actor_only"] is False


# ── 2b. missing / not-ready / malformed roles degrade, never raise ───────────


class TestMissingRoleDegrades:
    def test_absent_cortex_leaves_the_strategist_seat_unwired(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is None

    def test_absent_cortex_is_actor_only(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        assert resolution.actor_only is True

    def test_absent_cortex_records_one_degradation_naming_the_seat_and_role(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        codes = [entry.code for entry in resolution.degradations]
        assert codes == [seats.DEGRADED_ROLE_ABSENT]
        assert resolution.degradations[0].seat == seats.SEAT_STRATEGIST
        assert resolution.degradations[0].role == seats.ROLE_CORTEX

    def test_absent_cortex_does_not_disturb_the_other_seats(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        assert resolution.actor is not None
        assert resolution.senses is not None

    def test_absent_worker_leaves_the_actor_seat_unwired(self) -> None:
        caps = full_capabilities()
        del caps["worker"]
        resolution = seats.resolve_seats(caps)
        assert resolution.actor is None

    def test_an_entirely_empty_gateway_degrades_every_seat(self) -> None:
        resolution = seats.resolve_seats({})
        assert resolution.strategist is None
        assert resolution.actor is None
        assert resolution.senses is None
        assert len(resolution.degradations) == 3


class TestNotReadyRoleDegrades:
    def test_cortex_ready_false_is_a_distinct_code_from_absent(self) -> None:
        caps = full_capabilities()
        caps["cortex"]["ready"] = False
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is None
        assert resolution.degradations[0].code == seats.DEGRADED_ROLE_NOT_READY

    def test_the_muse_role_not_ready_is_never_resolved_at_all(self) -> None:
        """This module resolves no muse seat (c32); a not-ready muse in the
        payload must not appear anywhere in the resolution or its degradations."""
        resolution = seats.resolve_seats(full_capabilities())
        names = {entry.role for entry in resolution.degradations}
        assert "muse" not in names


class TestMalformedRoleEntryDegrades:
    def test_a_non_mapping_role_entry_is_malformed_not_absent(self) -> None:
        caps = full_capabilities()
        caps["cortex"] = "not-a-mapping"
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is None
        assert resolution.degradations[0].code == seats.DEGRADED_ROLE_MALFORMED

    def test_a_null_role_entry_is_malformed_not_absent(self) -> None:
        caps = full_capabilities()
        caps["worker"] = None
        resolution = seats.resolve_seats(caps)
        assert resolution.actor is None
        codes = {entry.code for entry in resolution.degradations}
        assert seats.DEGRADED_ROLE_MALFORMED in codes


# ── 2c. never raises, even under a hostile payload ────────────────────────────


class _HostileBool:
    def __bool__(self) -> bool:
        raise RuntimeError("nope")


class _HostileStr:
    def __str__(self) -> str:  # noqa: D105
        raise RuntimeError("nope")


class TestMalformedInputNeverRaises:
    @pytest.mark.parametrize("hostile", [None, [], "a string", 42, object()])
    def test_a_non_mapping_capabilities_argument_never_raises(self, hostile: Any) -> None:
        resolution = seats.resolve_seats(hostile)
        assert resolution.strategist is None
        assert resolution.actor is None
        assert resolution.senses is None
        assert len(resolution.degradations) == 3

    def test_a_hostile_ready_field_never_raises(self) -> None:
        caps = full_capabilities()
        caps["cortex"]["ready"] = _HostileBool()
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is None
        assert resolution.degradations[0].code == seats.DEGRADED_ROLE_NOT_READY

    def test_a_hostile_model_field_never_raises(self) -> None:
        caps = full_capabilities()
        caps["cortex"]["model"] = _HostileStr()
        resolution = seats.resolve_seats(caps)
        assert resolution.strategist is not None
        assert resolution.strategist.model == ""

    def test_a_hostile_mapping_subclass_never_raises(self) -> None:
        class HostileMapping(dict):
            def get(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("nope")

            def __contains__(self, key: Any) -> bool:
                raise RuntimeError("nope")

        resolution = seats.resolve_seats(HostileMapping(full_capabilities()))
        assert resolution.strategist is None
        assert resolution.actor is None
        assert resolution.senses is None


# ── 3. governed_by: degradation as a property of composition ────────────────


class TestGovernedByDegradesToActorOnly:
    """``governed_by`` returns a plain kwargs dict rather than constructing
    ``embodiment.scoped_run.ScopeGovernor`` itself: that module is not yet on
    ``embodiment``'s curated public surface, and
    ``tests/test_demo_greenhouse.py::TestPublicApiOnly`` refuses any
    ``examples/`` import of an undocumented submodule -- the same constraint
    ``examples/scope/subordinate.py`` already documents. This class proves both
    the returned mapping's own behaviour AND that it really does build the real
    class end to end, since only a *test* is free of that import restriction.
    """

    def test_a_present_strategist_is_seated(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        sentinel = object()
        kwargs = seats.governed_by(resolution, strategist=sentinel)
        assert kwargs["strategist"] is sentinel

    def test_a_present_strategist_governor_is_armed(self) -> None:
        resolution = seats.resolve_seats(full_capabilities())
        kwargs = seats.governed_by(resolution, strategist=object())
        governor = ScopeGovernor(**kwargs)
        assert governor.armed is True

    def test_missing_cortex_ignores_a_supplied_strategist_object(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        kwargs = seats.governed_by(resolution, strategist=object())
        assert kwargs["strategist"] is None

    def test_missing_cortex_governor_is_unarmed(self) -> None:
        caps = full_capabilities()
        del caps["cortex"]
        resolution = seats.resolve_seats(caps)
        kwargs = seats.governed_by(resolution, strategist=object())
        governor = ScopeGovernor(**kwargs)
        assert governor.armed is False

    def test_an_entirely_empty_gateway_yields_an_unarmed_governor_with_no_strategist_passed(
        self,
    ) -> None:
        resolution = seats.resolve_seats({})
        kwargs = seats.governed_by(resolution)
        governor = ScopeGovernor(**kwargs)
        assert isinstance(governor, ScopeGovernor)
        assert governor.armed is False

    def test_governed_by_never_raises_on_a_fully_degraded_resolution(self) -> None:
        resolution = seats.resolve_seats(None)
        kwargs = seats.governed_by(resolution, strategist=object())
        assert kwargs["strategist"] is None

    def test_the_kwargs_are_exactly_scopegovernors_constructor_keywords(self) -> None:
        import inspect

        resolution = seats.resolve_seats(full_capabilities())
        kwargs = seats.governed_by(resolution)
        accepted = set(inspect.signature(ScopeGovernor).parameters)
        assert set(kwargs) <= accepted


# ── 3b. placement ────────────────────────────────────────────────────────────


class TestPlacement:
    def test_the_module_lives_under_examples_scope(self) -> None:
        assert MODULE_PATH.parent.name == "scope"
        assert MODULE_PATH.parent.parent.name == "examples"
