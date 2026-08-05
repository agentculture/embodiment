"""The host-declared capability catalog — the seam that makes "never mint" checkable.

This file pins the answer to plan risk ``r2`` (capability-id enumeration). The
loop's :class:`~embodiment.loop.ToolExecutor` protocol is deliberately *not*
enumerable — one method, ``execute(name, arguments)``, and a docstring that says
"the loop never constructs an executor and never enumerates tools". So a
configuration tier that must "select among host-offered capability ids, never
mint new ones" (spec ``h11``) had nowhere to read those ids from. The catalog is
that place, and these tests hold its four load-bearing properties:

1. **The catalog is a host DECLARATION, never a discovery.** There is no
   constructor that reads a ``ToolExecutor``, and the module imports nothing
   that could give it one.
2. **A capability is a NAME, never a definition.** No field on
   :class:`~embodiment.capability.Capability` can carry a schema, a parameter
   list, a callable or an endpoint — so a catalog cannot be the vehicle for
   minting either.
3. **Ids are stable and their stability is measurable.** A catalog fingerprints
   to a content hash over exactly the fields authority depends on (id and kind),
   so drift across a restart is detectable rather than assumed away.
4. **An ambiguous or malformed catalog is visible, never silently normalised**
   (constraint C3).
"""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from embodiment.capability import (
    CAPABILITY_KIND_PERMISSION,
    CAPABILITY_KIND_TOOL,
    CAPABILITY_KINDS,
    CATALOG_SCHEMA_VERSION,
    EMPTY_CATALOG,
    Capability,
    CapabilityCatalog,
    catalog_fingerprint,
)

MODULE = Path(__file__).resolve().parents[1] / "embodiment" / "capability.py"


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        catalog_id="greenhouse-1",
        entries=(
            Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL, label="read a file"),
            Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
            Capability(capability_id="net.egress", kind=CAPABILITY_KIND_PERMISSION),
        ),
    )


class TestACapabilityIsANameNotADefinition:
    """Layer one of "never mint": there is no field a definition could land in."""

    def test_capability_is_a_frozen_dataclass(self) -> None:
        assert is_dataclass(Capability)
        cap = Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL)
        with pytest.raises(Exception):  # noqa: B017  # FrozenInstanceError is an Exception
            cap.capability_id = "other"  # type: ignore[misc]

    def test_no_field_can_carry_an_implementation(self) -> None:
        banned = {
            "schema",
            "parameters",
            "definition",
            "function",
            "handler",
            "callable",
            "implementation",
            "endpoint",
            "url",
            "command",
            "argv",
            "code",
            "execute",
        }
        names = {f.name for f in fields(Capability)}
        assert not (names & banned), f"a capability can carry an implementation: {names & banned}"

    def test_the_declared_field_set_is_exactly_four_names(self) -> None:
        assert [f.name for f in fields(Capability)] == [
            "capability_id",
            "kind",
            "label",
            "description",
        ]

    def test_kinds_are_the_two_the_lattice_needs(self) -> None:
        assert CAPABILITY_KINDS == (CAPABILITY_KIND_TOOL, CAPABILITY_KIND_PERMISSION)

    def test_round_trips_through_a_dict(self) -> None:
        cap = Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL, label="read")
        assert Capability.from_dict(cap.to_dict()) == cap

    def test_from_dict_never_raises_on_junk(self) -> None:
        assert Capability.from_dict(None) == Capability()
        assert Capability.from_dict(["not", "a", "mapping"]) == Capability()

    def test_from_dict_ignores_an_undeclared_key(self) -> None:
        cap = Capability.from_dict(
            {"capability_id": "fs.read", "kind": "tool", "command": "rm -rf /"}
        )
        assert cap == Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL)
        assert not hasattr(cap, "command")


class TestTheCatalogIsDeclaredNotDiscovered:
    """r2's decision, held structurally: nothing here can read an executor."""

    def test_the_module_imports_nothing_from_embodiment(self) -> None:
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        reached: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                reached.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                reached.add(node.module)
        leaked = {name for name in reached if name.split(".")[0] == "embodiment"}
        assert not leaked, f"the catalog is not a leaf any more: {sorted(leaked)}"

    def test_no_constructor_reads_an_executor(self) -> None:
        """Checked on the CODE, not the prose — the docstring cites the executor.

        It has to: the whole r2 decision is *why* an executor is unreachable
        here, and a reader who cannot see the thing being ruled out cannot
        check the ruling.
        """
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "ToolExecutor" not in names | attributes
        assert "execute" not in attributes, "the catalog calls something"
        assert not {name for name in functions if "executor" in name.lower()}

    def test_an_empty_catalog_declares_nothing(self) -> None:
        assert EMPTY_CATALOG.ids == ()
        assert not EMPTY_CATALOG.declares("fs.read")
        assert not EMPTY_CATALOG.declares("fs.read", kind=CAPABILITY_KIND_TOOL)

    def test_declares_is_kind_aware(self) -> None:
        catalog = _catalog()
        assert catalog.declares("fs.read", kind=CAPABILITY_KIND_TOOL)
        assert not catalog.declares("fs.read", kind=CAPABILITY_KIND_PERMISSION)
        assert catalog.declares("net.egress", kind=CAPABILITY_KIND_PERMISSION)
        assert not catalog.declares("net.egress", kind=CAPABILITY_KIND_TOOL)

    def test_an_undeclared_id_is_simply_not_there(self) -> None:
        assert not _catalog().declares("shell.exec")
        assert _catalog().get("shell.exec") is None

    def test_declares_without_a_kind_asks_only_whether_the_host_offers_it(self) -> None:
        assert _catalog().declares("fs.read")
        assert _catalog().declares("net.egress")

    def test_a_blank_id_declares_nothing_and_fetches_nothing(self) -> None:
        assert not _catalog().declares("")
        assert not _catalog().declares(None)
        assert _catalog().get("") is None

    def test_get_returns_the_declaration_itself(self) -> None:
        cap = _catalog().get("fs.read")
        assert cap is not None
        assert cap.kind == CAPABILITY_KIND_TOOL
        assert cap.label == "read a file"

    def test_a_field_whose_str_raises_becomes_a_blank_not_a_crash(self) -> None:
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("no text for you")

        assert Capability(capability_id=Hostile()).capability_id == ""  # type: ignore[arg-type]

    def test_ids_are_reported_in_declaration_order(self) -> None:
        assert _catalog().ids == ("fs.read", "fs.write", "net.egress")

    def test_ids_of_kind_filters(self) -> None:
        catalog = _catalog()
        assert catalog.ids_of_kind(CAPABILITY_KIND_TOOL) == ("fs.read", "fs.write")
        assert catalog.ids_of_kind(CAPABILITY_KIND_PERMISSION) == ("net.egress",)


class TestTheFingerprintMakesDriftDetectable:
    """The "stable across restarts" half of r2 — measurable, not asserted."""

    def test_a_fingerprint_is_a_hex_digest(self) -> None:
        digest = _catalog().fingerprint
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")

    def test_the_same_declaration_fingerprints_the_same(self) -> None:
        # Two INDEPENDENT catalogs built from the same declaration. Bound to
        # names so the claim is about the value rather than one object compared
        # with itself, and so a constant fingerprint could not satisfy it.
        first = _catalog()
        second = _catalog()
        assert first is not second
        assert first.fingerprint == second.fingerprint

    def test_declaration_ORDER_does_not_change_the_fingerprint(self) -> None:
        forward = _catalog()
        reversed_entries = CapabilityCatalog(
            catalog_id="greenhouse-1", entries=tuple(reversed(forward.entries))
        )
        assert reversed_entries.fingerprint == forward.fingerprint

    def test_a_cosmetic_label_change_does_not_change_the_fingerprint(self) -> None:
        relabelled = CapabilityCatalog(
            catalog_id="greenhouse-1",
            entries=tuple(
                Capability(capability_id=cap.capability_id, kind=cap.kind, label="renamed")
                for cap in _catalog().entries
            ),
        )
        assert relabelled.fingerprint == _catalog().fingerprint

    def test_removing_a_capability_changes_the_fingerprint(self) -> None:
        smaller = CapabilityCatalog(catalog_id="greenhouse-1", entries=_catalog().entries[:2])
        assert smaller.fingerprint != _catalog().fingerprint

    def test_changing_a_capability_KIND_changes_the_fingerprint(self) -> None:
        escalated = CapabilityCatalog(
            catalog_id="greenhouse-1",
            entries=(
                Capability(capability_id="fs.read", kind=CAPABILITY_KIND_PERMISSION),
                Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
                Capability(capability_id="net.egress", kind=CAPABILITY_KIND_PERMISSION),
            ),
        )
        assert escalated.fingerprint != _catalog().fingerprint

    def test_the_free_function_and_the_property_agree(self) -> None:
        assert catalog_fingerprint(_catalog().entries) == _catalog().fingerprint

    def test_the_fingerprint_never_raises_on_junk_entries(self) -> None:
        assert isinstance(catalog_fingerprint(("not-a-capability", None)), str)


class TestAMalformedCatalogIsVisible:
    """C3: a catalog the host got wrong is reported, never silently normalised."""

    def test_a_clean_catalog_has_no_problems(self) -> None:
        assert _catalog().problems == ()

    def test_a_blank_id_is_a_problem(self) -> None:
        catalog = CapabilityCatalog(entries=(Capability(kind=CAPABILITY_KIND_TOOL),))
        assert any("blank" in problem for problem in catalog.problems)

    def test_an_unknown_kind_is_a_problem(self) -> None:
        catalog = CapabilityCatalog(
            entries=(Capability(capability_id="fs.read", kind="superuser"),)
        )
        assert any("superuser" in problem for problem in catalog.problems)

    def test_a_duplicate_id_with_two_kinds_is_a_problem_and_declares_nothing(self) -> None:
        catalog = CapabilityCatalog(
            entries=(
                Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL),
                Capability(capability_id="fs.read", kind=CAPABILITY_KIND_PERMISSION),
            )
        )
        assert any("fs.read" in problem for problem in catalog.problems)
        assert not catalog.declares("fs.read", kind=CAPABILITY_KIND_TOOL)
        assert not catalog.declares("fs.read", kind=CAPABILITY_KIND_PERMISSION)

    def test_a_non_sequence_entries_payload_degrades_to_empty(self) -> None:
        assert CapabilityCatalog(entries="fs.read").entries == ()  # type: ignore[arg-type]

    def test_a_raw_mapping_entry_is_coerced_not_raised(self) -> None:
        raw = {"capability_id": "fs.read", "kind": "tool"}
        catalog = CapabilityCatalog(entries=(raw,))  # type: ignore[arg-type]
        assert catalog.declares("fs.read", kind=CAPABILITY_KIND_TOOL)


class TestTheCatalogRoundTrips:
    """A host that persists its declaration reads the same catalog back."""

    def test_round_trips_through_a_dict(self) -> None:
        catalog = _catalog()
        restored = CapabilityCatalog.from_dict(catalog.to_dict())
        assert restored == catalog
        assert restored.fingerprint == catalog.fingerprint

    def test_the_payload_carries_a_schema_version(self) -> None:
        assert _catalog().to_dict()["schema"] == CATALOG_SCHEMA_VERSION

    def test_from_dict_never_raises_on_junk(self) -> None:
        assert CapabilityCatalog.from_dict(None) == EMPTY_CATALOG
        assert CapabilityCatalog.from_dict(42) == EMPTY_CATALOG

    def test_the_catalog_is_frozen(self) -> None:
        with pytest.raises(Exception):  # noqa: B017  # FrozenInstanceError is an Exception
            _catalog().catalog_id = "other"  # type: ignore[misc]
