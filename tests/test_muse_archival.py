"""The muse is ARCHIVED BUT CITABLE — the disposition, pinned (task t15).

embodiment#53 asked what "archive the muse lane" means and listed three answers:
delete the module, deprecate it in place, or change only the prose. The operator
resolved it on 2026-08-03 with a fourth the issue had not listed —
**archived but citable** — recorded as deviations ``d2`` (the archival, blast
radius measured) and ``d3`` (the disposition and the replacement module's name).
It supersedes confirmed claims ``c12`` and ``c32``, both of which pinned that
``muse.py`` / ``muse_runner.py`` ship unchanged.

The mechanic chosen, and what each half of it means
---------------------------------------------------
*Archived* is enforced on the **curated package surface**. ``muse`` and
``muse_runner`` left :data:`embodiment._SUBMODULES` for
:data:`embodiment.ARCHIVED_SUBMODULES`, and the eighteen ``Muse*`` names they
owned left :data:`embodiment._LAZY_NAMES` entirely. So ``from embodiment import
ThreadedMuseRunner`` — the way the shipped reference architecture reached the
muse — no longer resolves, and neither ``muse`` nor ``muse_runner`` appears in
``embodiment.__all__``.

*Citable* is enforced by everything the archival deliberately did **not** do.
Both files stay in the package, stay importable by their own dotted path, and
stay green under their own test suites, because
:mod:`embodiment.strategist_runner` was copied from ``muse_runner.py`` verbatim
under the cite-don't-import policy and that citation has to remain traceable to
a file a reader can actually open.

Why this is not a deprecation warning
--------------------------------------
A ``DeprecationWarning`` says *stop using this, it is going away*. Neither is
true: the muse is not going away (the citation needs it) and a host that wants
counsel is still free to wire one. What changed is that it is no longer part of
the architecture this repo ships and documents. So the marker is a declarative
:data:`embodiment.muse.ARCHIVED` string a host or a test can read, not a runtime
warning fired at an importer who has done nothing wrong.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import embodiment

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two modules the disposition names. Nothing else was archived.
ARCHIVED = ("muse", "muse_runner")

#: Every name the muse lane used to hoist onto the curated surface. All eighteen
#: left; this list is what stops one quietly coming back.
RETIRED_NAMES = (
    "MUSE_AUTHORITY",
    "MUSE_TOOL_AUTHORITY",
    "MuseCompleteFn",
    "MuseControls",
    "MuseDegradation",
    "MuseDelivery",
    "MuseInsight",
    "MuseLoop",
    "MuseOrigin",
    "MuseOutcome",
    "MuseSink",
    "MuseToolBench",
    "MuseToolCompleteFn",
    "MuseToolExecuteFn",
    "ThreadFactory",
    "ThreadedMuseRunner",
    "insight_lag",
    "is_stale",
)


# ══════════════════════════════════════════════════════════════════════════════
# archived: the muse left the curated surface
# ══════════════════════════════════════════════════════════════════════════════


class TestTheMuseLeftTheCuratedSurface:
    """``embodiment.__all__`` is the shipped reference architecture's surface."""

    def test_archived_submodules_names_exactly_the_two_modules(self) -> None:
        assert embodiment.ARCHIVED_SUBMODULES == ARCHIVED

    def test_the_archived_roster_is_itself_on_the_surface(self) -> None:
        """The archival is host-visible, in the C3 spirit: nothing goes quietly."""
        assert "ARCHIVED_SUBMODULES" in embodiment.__all__

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_is_not_a_curated_submodule(self, name: str) -> None:
        assert name not in embodiment._SUBMODULES

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_is_not_advertised_in_all(self, name: str) -> None:
        assert name not in embodiment.__all__

    @pytest.mark.parametrize("name", RETIRED_NAMES)
    def test_the_hoisted_name_is_gone_from_the_lazy_map(self, name: str) -> None:
        assert name not in embodiment._LAZY_NAMES

    @pytest.mark.parametrize("name", RETIRED_NAMES)
    def test_the_hoisted_name_is_gone_from_all(self, name: str) -> None:
        assert name not in embodiment.__all__

    def test_the_package_refuses_the_retired_name(self) -> None:
        with pytest.raises(AttributeError, match="ThreadedMuseRunner"):
            embodiment.ThreadedMuseRunner  # noqa: B018  # access IS the assertion


# ══════════════════════════════════════════════════════════════════════════════
# citable: both files stay readable, importable and marked
# ══════════════════════════════════════════════════════════════════════════════


class TestTheMuseStaysCitable:
    """Archived is not deleted. The citation must resolve to a real file."""

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_source_file_still_exists(self, name: str) -> None:
        assert (REPO_ROOT / "embodiment" / f"{name}.py").is_file()

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_still_imports_by_its_own_path(self, name: str) -> None:
        module = __import__(f"embodiment.{name}", fromlist=["ARCHIVED"])
        assert module.__name__ == f"embodiment.{name}"

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_still_resolves_through_the_package(self, name: str) -> None:
        """Reachable by NAMING it — just never advertised. That is the whole seam."""
        assert getattr(embodiment, name) is not None

    def test_the_thinking_loop_is_still_constructible(self) -> None:
        """A host that wants counsel wires one; archival did not break that."""
        from embodiment.muse import MuseLoop

        assert MuseLoop is not None

    def test_the_runner_is_still_constructible(self) -> None:
        from embodiment.muse_runner import ThreadedMuseRunner

        assert ThreadedMuseRunner is not None


class TestTheArchivalMarkerIsDeclarative:
    """A readable constant, not a runtime warning — see the module docstring."""

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_declares_its_archival(self, name: str) -> None:
        module = __import__(f"embodiment.{name}", fromlist=["ARCHIVED"])
        assert isinstance(module.ARCHIVED, str)

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_marker_is_exported(self, name: str) -> None:
        module = __import__(f"embodiment.{name}", fromlist=["ARCHIVED"])
        assert "ARCHIVED" in module.__all__

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_marker_cites_the_issue_that_settled_it(self, name: str) -> None:
        module = __import__(f"embodiment.{name}", fromlist=["ARCHIVED"])
        assert "#53" in module.ARCHIVED

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_marker_names_the_replacement(self, name: str) -> None:
        module = __import__(f"embodiment.{name}", fromlist=["ARCHIVED"])
        assert "strategist" in module.ARCHIVED

    @pytest.mark.parametrize("name", ARCHIVED)
    def test_the_module_warns_nobody(self, name: str) -> None:
        """Importing an archived module warns nobody: it is cited on purpose.

        Checked structurally rather than by importing, because the module is
        already in ``sys.modules`` by the time any test runs — an import-time
        warning fires once, for whoever imported it first, and would be
        invisible here.
        """
        tree = ast.parse((REPO_ROOT / "embodiment" / f"{name}.py").read_text(encoding="utf-8"))
        warns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("warn")
        ]
        assert warns == []


# ══════════════════════════════════════════════════════════════════════════════
# the citation the archival exists to protect
# ══════════════════════════════════════════════════════════════════════════════


class TestTheCitationStaysTraceable:
    """``strategist_runner.py`` was copied from ``muse_runner.py`` (t2, ``d3``).

    Cite-don't-import means the citation is prose, so nothing but a test keeps it
    pointing at something real. These are what make "citable" more than a claim.
    """

    @staticmethod
    def _source(name: str) -> str:
        return (REPO_ROOT / "embodiment" / f"{name}.py").read_text(encoding="utf-8")

    def test_the_replacement_names_its_source_file(self) -> None:
        assert "muse_runner.py" in self._source("strategist_runner")

    def test_the_replacement_names_the_archival_deviation(self) -> None:
        assert "``d2``" in self._source("strategist_runner")

    def test_the_replacement_imports_nothing_from_the_archived_lane(self) -> None:
        """Cited, not imported — the archived lane must not become a dependency."""
        tree = ast.parse(self._source("strategist_runner"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        forbidden = {f"embodiment.{name}" for name in ARCHIVED}
        assert not (imported & forbidden)


# ══════════════════════════════════════════════════════════════════════════════
# what replaced it: the scope lane joined the surface in the same pass
# ══════════════════════════════════════════════════════════════════════════════


class TestTheScopeLaneTookItsPlace:
    """t15's other half. The muse left the surface; the strategist joined it."""

    @pytest.mark.parametrize("name", ("scope", "scoped_run", "strategist_runner"))
    def test_the_module_is_a_curated_submodule(self, name: str) -> None:
        assert name in embodiment._SUBMODULES

    @pytest.mark.parametrize("name", ("scope", "scoped_run", "strategist_runner"))
    def test_the_module_is_advertised(self, name: str) -> None:
        assert name in embodiment.__all__

    @pytest.mark.parametrize(
        "name",
        ("ScopeDirective", "ScopeResponsibility", "ScopeSnapshot", "ScopeGovernor"),
    )
    def test_the_shape_resolves_from_the_package(self, name: str) -> None:
        assert getattr(embodiment, name) is not None
