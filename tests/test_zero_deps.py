"""The dependency gate: nothing enters this package's install footprint by accident.

This file used to assert ``[project].dependencies == []`` — embodiment was
pure-stdlib by constraint C1, with no allow-list because nothing was allowed.
**Deviation d2 reversed that**, deliberately: ``eidetic-cli`` and
``coherence-cli`` became base dependencies imported at module scope (plus
``events-cli`` for a sibling task), so the continuity seam could call real
Python instead of parsing a subprocess's JSON.

So the question this file answers changed. It is no longer *"are there zero
dependencies?"* — the answer is knowably no. It is:

    **Did a human decide this?**

Every dependency and every transitive import is pinned below as an explicit,
commented constant. Any delta — an addition **or a removal** — fails. A removal
matters as much as an addition: a dependency quietly vanishing means either the
feature that needed it was dropped or someone edited the pin to make a red test
green. Both deserve a human's attention.

Updating a pinned set **is** the approval. That is the whole mechanism, and it
only works if updating it is a considered act with a recorded reason, never a
reflex to get CI green.

Why this matters more for embodiment than for most packages
------------------------------------------------------------
embodiment is a **library that host apps import**. Every entry in
``[project].dependencies`` is therefore a dependency of every host that adopts
it. colleague — the first intended consumer — asserts in its own
``tests/test_zero_deps.py`` that its dependencies are *exactly*
``["agentfront>=…"]`` and that importing colleague adds no third-party
top-level import. Both of those assertions now fail if colleague adds
embodiment. That is a known, accepted consequence of d2 (see
``embodiment/continuity.py``'s module docstring), and it makes C1b — whether
colleague relaxes its one-base-dependency rule — a hard prerequisite for the
seam proposal rather than an open question.

Asserts:

1. ``[project].dependencies`` matches :data:`_APPROVED_DEPENDENCIES` exactly.
2. The third-party top-level modules that importing embodiment introduces match
   :data:`_REQUIRED_RUNTIME_IMPORTS` (plus, optionally, the enumerated
   :data:`_INCIDENTAL_RUNTIME_IMPORTS`) — so an *unintended* transitive import
   fails even when ``pyproject.toml`` is untouched.
3. ``import embodiment`` on its own still introduces nothing: the package root
   is lazy (:pep:`562`), so only a host that actually reaches
   ``embodiment.continuity`` pays d2's cost.
4. Modules are discovered dynamically, so a module added by a future task is
   covered automatically rather than silently escaping a hardcoded list.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed argv, no shell, test-only interpreter probe
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# THE APPROVED SET — edit only as a deliberate, recorded decision
# ---------------------------------------------------------------------------

#: Exactly what ``pip install embodiment`` is approved to pull, with what each
#: entry actually costs. Approved under deviation d2; the rationale lives in
#: ``pyproject.toml``'s comment block and in CHANGELOG.md.
_APPROVED_DEPENDENCIES: dict[str, str] = {
    # -> data-refinery-cli[store] -> neo4j + pymongo. A graph driver and a
    #    Mongo driver land in every host, even though the default `files`
    #    backend uses neither.
    "eidetic-cli>=0.12": "pulls data-refinery-cli[store] -> neo4j + pymongo",
    # -> numpy + httpx (and httpx's own anyio/certifi/httpcore/idna).
    "coherence-cli>=0.6": "pulls numpy + httpx",
    # -> paho-mqtt. `embodiment/events.py` ships and is the consumer, but it
    #    imports `events_cli` LAZILY (inside the emit path), so paho-mqtt stays
    #    out of the measured top-level import set below.
    "events-cli>=0.10": "pulls paho-mqtt",
}

#: Third-party top-level modules that importing **every** embodiment module
#: introduces. Measured, not guessed — see :func:`_measure_runtime_imports`.
#:
#: Note what is deliberately ABSENT: ``neo4j``, ``pymongo`` and ``paho`` are
#: *installed* by the approved dependencies but never *imported* at module
#: scope (data-refinery resolves its store backends lazily, and nothing here
#: touches events yet). Install footprint and import footprint are different
#: costs and this file measures both separately — do not "fix" one to match
#: the other.
_REQUIRED_RUNTIME_IMPORTS: frozenset[str] = frozenset(
    {
        "coherence",  # the assess engine
        "data_refinery",  # eidetic's storage substrate
        "eidetic",  # the memory store
        "httpx",  # coherence's embedding client
        "idna",  # hard dependency of httpx._urls
        "numpy",  # coherence's scoring
    }
)

#: Modules that arrive only because httpx opportunistically imports its own
#: optional ``cli`` extra when it happens to be installed::
#:
#:     try:
#:         from ._main import main      # httpx/__init__.py
#:     except ImportError:
#:         ...
#:
#: They are present in this repo's dev environment (``rich``/``pygments`` come
#: via the dev toolchain) and absent from a bare production install. They are
#: enumerated rather than waved through: an unlisted module still fails. They
#: are kept in a separate tier only because their presence is decided by a
#: third party's optional extra, not by anything embodiment declares — so
#: pinning them as *required* would fail for a reason that has nothing to do
#: with this package's dependency posture.
_INCIDENTAL_RUNTIME_IMPORTS: frozenset[str] = frozenset({"click", "pygments", "rich"})

# Import-system builtins and packaging artifacts — never third party.
_KNOWN_IMPORT_BUILTINS = frozenset(
    {
        "importlib",
        "importlib_metadata",
        "pip",
        "pkg_resources",
        "__main__",
        "site",
        "sitecustomize",
        "usercustomize",
    }
)


# ---------------------------------------------------------------------------
# discovery + measurement
# ---------------------------------------------------------------------------


def _discover_embodiment_modules() -> list[str]:
    """Dynamically discover every module in the embodiment package.

    Returns fully-qualified names (e.g. ``embodiment.cli``). Kept dynamic so a
    module added by a future task is covered by these guards automatically,
    rather than escaping a hardcoded list that silently goes stale.
    """
    package_root = REPO_ROOT / "embodiment"
    assert package_root.is_dir(), f"embodiment package not found at {package_root}"

    modules = ["embodiment"]
    for py_file in sorted(package_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        parts = py_file.relative_to(package_root).parts
        if parts[-1] == "__init__.py":
            if len(parts) > 1:
                modules.append("embodiment." + ".".join(parts[:-1]))
        else:
            modules.append("embodiment." + ".".join(parts[:-1] + (parts[-1][:-3],)))
    return sorted(set(modules))


# Run in a *clean* interpreter. Diffing ``sys.modules`` inside the pytest
# process would measure whatever pytest, its plugins and previously-run tests
# already dragged in — making the result depend on test ordering and on which
# xdist worker happened to run first. A subprocess makes the measurement a
# property of the package instead of a property of the run.
_PROBE = """
import importlib, json, sys

before = set(sys.modules)
for name in {modules!r}:
    importlib.import_module(name)
new = {{n.split(".")[0] for n in set(sys.modules) - before if n}}
print(json.dumps(sorted(
    n for n in new
    if n not in sys.stdlib_module_names
    and not n.startswith("embodiment")
    and not n.startswith("_")
    and n not in {builtins!r}
)))
"""


def _measure_runtime_imports(modules: list[str]) -> set[str]:
    """Import *modules* in a fresh interpreter; return the third-party top-levels."""
    import json

    proc = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [
            sys.executable,
            "-c",
            _PROBE.format(modules=modules, builtins=set(_KNOWN_IMPORT_BUILTINS)),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return set(json.loads(proc.stdout))


def _declared_dependencies() -> list[str]:
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    return data.get("project", {}).get("dependencies", [])


def _gate_message(
    *, subject: str, added: set[str], removed: set[str], costs: dict[str, str], remedy: str
) -> str:
    """Build the failure message. **This message is the point of the file.**

    Someone hits this in CI without having read the test. It has to tell them
    exactly what approval is being requested, what it costs, and what to do —
    including that updating the pin is itself the act of approving.
    """
    lines = [
        "",
        "=" * 72,
        f"DEPENDENCY GATE — human approval required ({subject})",
        "=" * 72,
        "",
    ]
    if added:
        lines.append("  ADDED — present now, never approved:")
        for name in sorted(added):
            cost = costs.get(name)
            lines.append(f"    + {name}" + (f"   [cost: {cost}]" if cost else ""))
        lines.append("")
    if removed:
        lines.append("  REMOVED — approved before, gone now:")
        for name in sorted(removed):
            cost = costs.get(name)
            lines.append(f"    - {name}" + (f"   [was: {cost}]" if cost else ""))
        lines.append("")
    lines += [
        "embodiment is a library that host apps import, so everything here is",
        "also a dependency of every host that adopts it. That is why each entry",
        "is pinned by hand instead of allow-listed by pattern.",
        "",
        "A REMOVAL is not automatically good news: it means either a capability",
        "was dropped, or a pin was edited to turn a red test green. Both want a",
        "human to look.",
        "",
        "To proceed:",
        remedy,
        "",
        "Updating the pinned set IS the approval. Do not update it merely to",
        "make this test pass — record the reason first.",
        "=" * 72,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------


def test_declared_dependencies_match_the_approved_set():
    """``[project].dependencies`` is exactly the approved set — no drift either way."""
    declared = set(_declared_dependencies())
    approved = set(_APPROVED_DEPENDENCIES)

    assert declared == approved, _gate_message(
        subject="pyproject.toml [project].dependencies",
        added=declared - approved,
        removed=approved - declared,
        costs=_APPROVED_DEPENDENCIES,
        remedy=(
            "  1. Establish the FULL transitive cost of the change (`uv tree`),\n"
            "     not just the direct requirement.\n"
            "  2. Record WHY in pyproject.toml's dependency comment block and in\n"
            "     CHANGELOG.md — a new dependency changes every host's install.\n"
            "  3. Then update _APPROVED_DEPENDENCIES in tests/test_zero_deps.py."
        ),
    )


def test_declared_dependencies_are_not_silently_unbounded():
    """Every approved entry carries a version floor.

    An unpinned requirement is a dependency whose cost can change without any
    edit to this repo — which would route around this gate entirely.
    """
    for requirement in _APPROVED_DEPENDENCIES:
        assert ">=" in requirement or "==" in requirement, (
            f"{requirement!r} declares no version floor; a floorless requirement "
            "can change its own transitive cost with no edit here, bypassing this gate."
        )


def test_runtime_imports_match_the_approved_set():
    """Importing embodiment introduces exactly the approved third-party modules.

    The second half of the gate, and the half ``pyproject.toml`` cannot give
    you: a dependency already approved for one purpose can start dragging in
    something new after a version bump, with no edit to this repo at all. This
    measures what actually lands in ``sys.modules``.
    """
    observed = _measure_runtime_imports(_discover_embodiment_modules())

    unapproved = observed - _REQUIRED_RUNTIME_IMPORTS - _INCIDENTAL_RUNTIME_IMPORTS
    vanished = _REQUIRED_RUNTIME_IMPORTS - observed

    assert not unapproved and not vanished, _gate_message(
        subject="third-party modules imported by embodiment",
        added=unapproved,
        removed=vanished,
        costs={},
        remedy=(
            "  1. Find WHO imports it — a new module here usually means a sibling\n"
            "     package changed its own imports, not that this repo did.\n"
            "  2. Decide whether it belongs at module scope at all, or whether the\n"
            "     import should be deferred inside a function.\n"
            "  3. Then add it to _REQUIRED_RUNTIME_IMPORTS (or, if it arrives only\n"
            "     from a third party's optional extra, _INCIDENTAL_RUNTIME_IMPORTS)\n"
            "     in tests/test_zero_deps.py, with a comment saying which package\n"
            "     pulls it and why."
        ),
    )


def test_install_footprint_is_wider_than_import_footprint():
    """Guard the distinction the two pinned sets encode.

    ``neo4j``/``pymongo``/``paho-mqtt`` are installed by the approved
    dependencies but imported by nothing — data-refinery resolves its store
    backends lazily, and no events module exists yet. If one of them ever shows
    up in the runtime set, the gate above fails and *this* test explains why
    that is a real change rather than noise.
    """
    for name in ("neo4j", "pymongo", "paho"):
        assert name not in _REQUIRED_RUNTIME_IMPORTS
        assert name not in _INCIDENTAL_RUNTIME_IMPORTS


def test_bare_package_import_still_costs_nothing():
    """``import embodiment`` alone introduces no third-party module.

    d2 made the *continuity seam* expensive, not the package. ``embodiment``'s
    root resolves every public name lazily (:pep:`562`), so a host that only
    wants the loop never pays for eidetic, coherence, numpy or httpx. This is
    the strongest remaining piece of the original C1 posture and it is worth
    keeping — losing it would be a real regression, not a technicality.
    """
    observed = _measure_runtime_imports(["embodiment"])

    assert observed == set(), (
        f"`import embodiment` now pulls {sorted(observed)}. The package root must "
        "stay lazy: a convenience import at module scope in embodiment/__init__.py "
        "makes every host pay d2's cost whether or not it uses continuity."
    )


def test_continuity_is_what_costs():
    """...and the cost, when paid, is attributable to exactly one module.

    Stated positively so the gate above reads as a decision with a location,
    rather than a mystery about which import is expensive.
    """
    observed = _measure_runtime_imports(["embodiment.continuity"])

    assert _REQUIRED_RUNTIME_IMPORTS <= observed


def test_discovered_modules_not_empty():
    """Sanity check: discovery finds the core modules, so the gates cover them."""
    modules = _discover_embodiment_modules()

    assert len(modules) > 1, f"module discovery found too few modules: {modules}"
    expected = {"embodiment", "embodiment.cli", "embodiment.explain", "embodiment.continuity"}
    assert expected.issubset(set(modules)), (
        f"module discovery missed expected core modules. Expected at least "
        f"{sorted(expected)}, got {modules}"
    )


def test_no_pytest_leaked_into_base_import():
    """Importing embodiment.cli does not pull pytest.

    pytest is a dev dependency. The CLI import path must stay clean of it even
    though the suite running this assertion obviously has it loaded — hence the
    clean-interpreter probe.
    """
    observed = _measure_runtime_imports(["embodiment.cli"])

    assert "pytest" not in observed
