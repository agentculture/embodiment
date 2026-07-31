"""The package's public surface: curated, lazy, and provably so.

``embodiment/__init__.py`` re-exports a curated API through :pep:`562`
``__getattr__``. Two properties matter and neither is self-evident from reading
the module, so both are pinned here:

1. **Every advertised name resolves.** A lazy re-export map is a second place a
   name can be wrong — a typo lands as an ``AttributeError`` at a consumer's
   first call rather than at import. The whole of ``__all__`` is walked.
2. **Importing the package stays cheap.** The point of the laziness is that
   ``import embodiment`` does not drag in the loop, the presence engine, or
   ``importlib.metadata``. That is only true until someone adds a convenience
   import at module scope, so it is asserted in a subprocess with a clean
   interpreter.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed argv, no shell, test-only interpreter probe
import sys

import pytest

import embodiment

# Modules whose absence from a bare ``import embodiment`` is the whole point.
_HEAVY = (
    "embodiment.loop",
    "embodiment.presence_engine",
    "embodiment.continuity",
    "embodiment.perception",
    "embodiment.context",
    "embodiment.contract",
    # t13's workspace tool imports ``headspace.api`` at module scope. Cheap in
    # itself (stdlib only, measured), but it is still a third-party package a
    # host that only wants the loop must not pay for.
    "embodiment.workspace",
)


def _probe(source: str) -> str:
    """Run *source* in a clean interpreter and return its stdout, stripped."""
    proc = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


class TestEveryAdvertisedNameResolves:
    """``__all__`` is a promise; walk all of it."""

    @pytest.mark.parametrize("name", sorted(embodiment.__all__))
    def test_name_resolves(self, name: str) -> None:
        assert getattr(embodiment, name) is not None or name in {"__version__"}

    def test_all_matches_dir(self) -> None:
        assert sorted(embodiment.__all__) == embodiment.__dir__()

    def test_no_duplicate_entries(self) -> None:
        assert len(embodiment.__all__) == len(set(embodiment.__all__))

    def test_every_lazy_name_maps_to_a_real_submodule(self) -> None:
        for name, module in embodiment._LAZY_NAMES.items():
            assert module in embodiment._SUBMODULES, f"{name} -> unknown module {module}"

    def test_submodules_and_names_do_not_collide(self) -> None:
        assert not (embodiment._SUBMODULES & set(embodiment._LAZY_NAMES))

    def test_an_unknown_attribute_still_raises(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'definitely_not_here'"):
            embodiment.definitely_not_here  # noqa: B018  # attribute access IS the assertion


class TestResolutionIsCorrect:
    """A lazy name must be the *same object* as the eager import."""

    def test_run_is_the_loop_run(self) -> None:
        from embodiment.loop import run

        assert embodiment.run is run

    def test_presence_sink_resolves_from_the_contract_owner(self) -> None:
        from embodiment.presence_engine import PresenceSink

        assert embodiment.PresenceSink is PresenceSink

    def test_submodule_access_returns_the_module(self) -> None:
        import embodiment.continuity as continuity

        assert embodiment.continuity is continuity

    def test_version_is_a_string(self) -> None:
        assert isinstance(embodiment.__version__, str)
        assert embodiment.__version__

    def test_repeated_access_is_cached_in_globals(self) -> None:
        # Touch it, then assert the name is now a real module global — proof the
        # second lookup never re-enters __getattr__.
        _ = embodiment.perceive
        assert "perceive" in vars(embodiment)


class TestImportStaysLazy:
    """The guarantee, checked in a clean interpreter rather than this one.

    This test suite has already imported most of the package, so ``sys.modules``
    here proves nothing — each probe runs in a fresh subprocess.
    """

    def test_bare_import_pulls_in_no_heavy_module(self) -> None:
        loaded = _probe(
            "import embodiment, sys;"
            "print(','.join(sorted(m for m in sys.modules if m.startswith('embodiment.'))))"
        )
        assert loaded == "", f"import embodiment eagerly loaded: {loaded}"

    def test_bare_import_does_not_resolve_the_version(self) -> None:
        # importlib.metadata is the single largest cost in the package; a bare
        # import must not pay it.
        loaded = _probe("import embodiment, sys;print('importlib.metadata' in sys.modules)")
        assert loaded == "False"

    @pytest.mark.parametrize("heavy", _HEAVY)
    def test_touching_one_name_does_not_load_everything(self, heavy: str) -> None:
        # Resolving a presence-policy name must drag in NOTHING else — not the
        # loop, not the contract. ``presence`` is the pure, IO-free policy half
        # (stdlib imports only), so ``embodiment.presence`` is the complete set.
        loaded = _probe(
            "import embodiment;"
            "embodiment.UpdateCadence;"
            f"import sys;print({heavy!r} in sys.modules)"
        )
        assert loaded == "False", f"resolving UpdateCadence pulled in {heavy}"

    def test_resolving_a_pure_policy_name_loads_exactly_one_module(self) -> None:
        loaded = _probe(
            "import embodiment;embodiment.UpdateCadence;import sys;"
            "print(','.join(sorted(m for m in sys.modules if m.startswith('embodiment.'))))"
        )
        assert loaded == "embodiment.presence"

    def test_resolving_a_name_does_load_its_own_module(self) -> None:
        loaded = _probe(
            "import embodiment;embodiment.run;import sys;print('embodiment.loop' in sys.modules)"
        )
        assert loaded == "True"


class TestTypeCheckingBlockCannotDrift:
    """The ``if TYPE_CHECKING:`` stub is a second copy of the export map.

    It exists so a *consumer's* type-checker can see names that only appear at
    runtime through ``__getattr__`` — this repo does not run mypy itself, so
    nothing else would catch it going stale. Two copies of one list is exactly
    the shape that drifts, so the stub is parsed and compared rather than
    trusted. Add a name to ``_LAZY_NAMES`` and forget the stub, and this fails.
    """

    @staticmethod
    def _stub_names() -> set[str]:
        """Every name imported inside the module's ``if TYPE_CHECKING:`` block."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path(embodiment.__file__).read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"):
                continue
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.ImportFrom):
                    found.update(alias.asname or alias.name for alias in stmt.names)
        return found

    def test_stub_covers_every_lazy_name(self) -> None:
        missing = set(embodiment._LAZY_NAMES) - self._stub_names()
        assert not missing, f"missing from the TYPE_CHECKING stub: {sorted(missing)}"

    def test_stub_covers_every_submodule(self) -> None:
        missing = set(embodiment._SUBMODULES) - self._stub_names()
        assert not missing, f"missing from the TYPE_CHECKING stub: {sorted(missing)}"

    def test_stub_advertises_nothing_extra(self) -> None:
        known = set(embodiment._LAZY_NAMES) | set(embodiment._SUBMODULES)
        extra = self._stub_names() - known
        assert not extra, f"stub declares names the runtime cannot resolve: {sorted(extra)}"
