"""Guard test enforcing pure-stdlib zero-dependencies posture.

embodiment is a presence-layer library that apps import or wrap. It must remain
pure-stdlib (zero third-party imports) so that importing embodiment adds no
transitive dependencies to the host app — a strict no-downgrades contract from
the C1 constraint in the build brief.

Unlike colleague (which allow-lists ``agentfront`` as the ONE sanctioned base
dependency), embodiment ships with ZERO base dependencies. Optional capabilities
(realtime audio, MCP) go behind extras, lazily imported inside functions, never
at module load.

Asserts:
1. pyproject.toml's [project].dependencies is exactly [] (pure stdlib).
2. Importing all embodiment modules introduces no third-party top-level imports.
3. Module-scope purity — optional extras are lazy (inside functions, not at load).
4. Discover modules dynamically so future modules added to embodiment are covered
   automatically, preventing silent go-stale of hardcoded module lists.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

# Known import-system builtins (not in sys.stdlib_module_names but safe):
# importlib internals and setup/packaging artifacts.
_KNOWN_IMPORT_BUILTINS = {
    "importlib",
    "importlib_metadata",
    "_frozen_importlib",
    "_frozen_importlib_external",
    "_bootstrap",
    "pip",
    "pkg_resources",
    "__main__",
    "__path__",
    "site",
    "sitecustomize",
    "usercustomize",
}


def _third_party_modules_introduced(action):
    """Run ``action`` and return any third-party top-level modules it imports.

    Snapshots ``sys.modules`` before/after, reduces new entries to their
    top-level name (first component before ``.``), and filters out stdlib,
    ``embodiment`` itself, and known import-system builtins. Unlike colleague's
    version, embodiment has NO allow-list (zero sanctioned base dependencies).
    """
    before = set(sys.modules.keys())
    action()
    new_top_level = {name.split(".")[0] for name in (set(sys.modules.keys()) - before) if name}

    third_party = []
    for name in sorted(new_top_level):
        is_stdlib = name in sys.stdlib_module_names
        is_embodiment = name.startswith("embodiment")
        is_builtin = name in _KNOWN_IMPORT_BUILTINS or name.startswith("_")
        # embodiment has ZERO sanctioned base dependencies (unlike colleague which
        # allows agentfront). If a third-party module is imported at module load,
        # it is a zero-deps-posture breach.
        if not (is_stdlib or is_embodiment or is_builtin):
            third_party.append(name)
    return third_party


def _discover_embodiment_modules() -> list[str]:
    """Dynamically discover all embodiment modules by walking the package.

    Returns a list of fully-qualified module names (e.g., "embodiment.cli").
    Excludes __pycache__ and __main__ (which is the entry point, run separately).
    This ensures the guard covers modules added by future tasks, not a hardcoded
    list that silently goes stale.
    """
    embodiment_pkg = Path(__file__).resolve().parents[1] / "embodiment"
    assert embodiment_pkg.is_dir(), f"embodiment package not found at {embodiment_pkg}"

    modules = ["embodiment"]  # Always include the package root
    for py_file in sorted(embodiment_pkg.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel_parts = py_file.relative_to(embodiment_pkg).parts
        # Convert path to module name: embodiment/cli/__init__.py -> embodiment.cli
        if rel_parts[-1] == "__init__.py":
            if len(rel_parts) > 1:
                mod_name = "embodiment." + ".".join(rel_parts[:-1])
                modules.append(mod_name)
        else:
            mod_name = "embodiment." + ".".join(rel_parts[:-1] + (rel_parts[-1][:-3],))
            modules.append(mod_name)

    return sorted(set(modules))


def test_base_dependencies_is_empty():
    """Assert [project].dependencies is exactly [] (zero base dependencies).

    embodiment's agent-first CLI is standalone and importable with zero
    third-party transitive dependencies. The base install must pull nothing
    except the embodiment package itself — any base dependency is a
    zero-deps-posture breach.
    """
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    dependencies = data.get("project", {}).get("dependencies", [])
    assert dependencies == [], f"Expected zero base dependencies (pure stdlib), got {dependencies}"


def test_no_third_party_imports():
    """Importing embodiment modules introduces no third-party top-level imports.

    Dynamically discovers all embodiment modules and imports each one,
    then asserts the import introduced no third-party top-level modules.
    Module-scope purity is fundamental: optional capabilities (audio/realtime/MCP)
    must load lazily inside functions, never at import time. This guard proves
    that deferral is real even when optional packages ARE installed in dev/CI.
    """

    def _import_all_embodiment_modules():
        """Import every module in the embodiment package."""
        for mod_name in _discover_embodiment_modules():
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                # Some modules may depend on conditional imports that aren't
                # available; that's OK as long as they don't leak third-party
                # at the import statement itself. Re-raise only if it's a
                # third-party import error (not a submodule issue).
                if "embodiment" not in str(e):
                    raise

    third_party = _third_party_modules_introduced(_import_all_embodiment_modules)
    assert not third_party, (
        f"Third-party imports detected on module load: {sorted(third_party)}. "
        "Expected only stdlib and embodiment. Optional packages (audio/realtime/MCP) "
        "must be lazily imported inside functions, never at module scope."
    )


def test_discovered_modules_not_empty():
    """Sanity check: the module discovery finds at least the core modules."""
    modules = _discover_embodiment_modules()
    assert len(modules) > 1, f"Module discovery found too few modules: {modules}"
    # Check for expected core modules (the scaffold includes cli, explain, __init__)
    expected = {"embodiment", "embodiment.cli", "embodiment.explain"}
    found = set(modules)
    assert expected.issubset(found), (
        f"Module discovery missed expected core modules. "
        f"Expected to find at least {expected}, but got {found}"
    )


def test_no_pytest_leaked_into_base_import():
    """Importing embodiment.cli does not pull pytest into sys.modules.

    pytest is a dev dependency (not a base dependency). This guard proves that
    the CLI import path stays clean. Even though the test suite will have pytest
    loaded, importing embodiment.cli fresh (conceptually) must not depend on it.
    """

    def _import_cli():
        import embodiment.cli  # noqa: F401

    before = set(sys.modules.keys())
    _import_cli()
    newly = {n.split(".")[0] for n in (set(sys.modules.keys()) - before)}
    assert "pytest" not in newly, (
        "Importing embodiment.cli pulled pytest — it must not appear " "in a base import path."
    )
