"""Hermetic governance guards: the standing rulings, enforced instead of remembered.

Three operator rulings are stated as prose in ``CLAUDE.md`` and none of them is
self-enforcing — prose can drift silently the moment nobody is reading it
closely. This file makes all three CI-checkable:

1. **The muse ships as opt-in code, off by default, NEVER deleted**
   (deviation ``d15`` — see ``CLAUDE.md``, "Identity — embodiment, and the
   Gwen it ships"). The reference rig runs muse-off, but "off by default" is
   not "gone": :mod:`embodiment.muse`, :mod:`embodiment.muse_runner`,
   :mod:`embodiment.muse_pad` and :mod:`embodiment.workspace` — plus their
   tests — are a host's opt-in seam and must stay importable and collectible.
   :class:`TestMuseSurfaceIsNeverDeleted` guards this.

2. **A new role only enters a shipped reference-rig table with a supporting
   measured verdict; an INCONCLUSIVE result leaves the rig untouched.** This
   mirrors how ``d15`` itself was decided — the muse-arms series
   (``docs/live-test-results/muse-arms.md``) returned ``INCONCLUSIVE`` and the
   muse stayed out of the reference rig rather than being promoted on a maybe.
   The same decision rule now governs the `worker` role a pre-registered
   experiment series is evaluating. :class:`TestWorkerRolePromotionGate` guards
   this by parsing the ACTUAL rig tables in ``CLAUDE.md`` **and** ``README.md``
   — not a substring search over the whole file, so an unrelated mention of the
   word "worker" in prose elsewhere (``README.md`` already carries several,
   describing the drone tier) cannot trip it.

3. **Drones ship opt-in and OFF** (claim ``c25``). The standing rule is *the
   measured failure mode never ships as default behaviour*; this is its mirror
   image — the drone tier implements issue #44's worker-harness caste, whose
   validating experiment has **not run**, so an *unmeasured* design does not
   ship on either. :class:`TestDronesShipOptInAndOff` guards this without
   authoring, saving, evoking or otherwise touching a drone: it reads the
   shipped constant, resolves an empty environment, and inspects
   ``embodiment/drone.py`` structurally.

Guards 1 and 2 landed with the orchestrator-worker-architectures plan (task
t15); guard 3 joined them under the error-derived-timeouts plan (task t13),
once t11 and t12 had built the switch there was previously nothing to assert.

All three are meant to be *guards*, not landmines: each failure message below
says exactly what changed and exactly what a deliberate promotion looks like,
mirroring the existing convention in ``tests/test_zero_deps.py`` ("updating a
pinned set IS the approval" — a considered, reviewable edit, never a reflex to
get CI green).

**And every guard here is proven able to fail.** :class:`TestTheseGuardsCanFail`
feeds each detector the mutation it exists to catch and asserts the detector
fires; ``tests/prove_governance_guards.py`` goes further and mutates the real
committed files, re-running this module against each one to watch it go red. A
guard nobody proved can fail is a guard nobody has.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from embodiment import drone as drone_lib

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_README_MD = _REPO_ROOT / "README.md"


# ── guard 1: the muse surface is never deleted ──────────────────────────────
#
# "A test that merely checks a file exists is weak" — so every module below is
# actually imported and checked for the public names a host's opt-in seam
# depends on, and every test file is actually imported and checked for at
# least one collectible test. A module quietly reduced to an empty stub would
# still "exist" as a file; it would not pass either check.

#: The muse's opt-in modules, and the public names a host wiring the seam (or
#: a test pinning it) depends on. Not exhaustive of each module's ``__all__``
#: — just enough that a gutted module fails here even if the file survives.
_MUSE_MODULES: dict[str, tuple[str, ...]] = {
    "embodiment.muse": (
        "MuseLoop",
        "MuseInsight",
        "MuseOutcome",
        "MuseOrigin",
        "MuseControls",
        "MuseToolBench",
        "MUSE_AUTHORITY",
        "MUSE_EXIT_REASONS",
    ),
    "embodiment.muse_runner": (
        "ThreadedMuseRunner",
        "MuseDelivery",
        "MUSE_ROLE",
        "RUNNER_CODES",
    ),
    "embodiment.muse_pad": (
        "MusePad",
        "MusePadCounts",
        "MusePadRefused",
        "MUSE_PAD_LANE",
    ),
    "embodiment.workspace": (
        "MuseWorkspace",
        "WorkspaceCounts",
        "WorkspaceDegradation",
        "WORKSPACE_LANE",
    ),
}

#: The test file that pins each module above, one-to-one — named explicitly in
#: this task's acceptance criteria.
_MUSE_TEST_MODULES: tuple[str, ...] = (
    "tests.test_muse",
    "tests.test_muse_runner",
    "tests.test_muse_pad",
    "tests.test_workspace",
)

#: The rest of the muse-adjacent test/harness suite — task t10/t12/t13/t18's
#: measurement and protocol pins. "The cycle's changes delete no muse module,
#: test or harness" covers these too, not only the four modules' direct pins.
_MUSE_HARNESS_TEST_MODULES: tuple[str, ...] = (
    "tests.test_muse_arms",
    "tests.test_muse_challenge",
    "tests.test_muse_latency_preregistration",
    "tests.test_muse_tool_identity",
    "tests.test_muse_tool_loop_ast",
    "tests.test_echo_probe_workspace",
)

_DELETION_CONTEXT = (
    "The operator's ruling (deviation d15, CLAUDE.md 'Identity — embodiment, "
    "and the Gwen it ships') is explicit: the muse ships as opt-in code, off "
    "by default, and is NEVER deleted. The reference rig running muse-off "
    "changes no code — the muse was already opt-in — and does not retire "
    "this seam."
)


def _collectible_test_count(module: object) -> int:
    """How many pytest-collectible tests *module* defines.

    Counts top-level ``test_*`` functions and methods on ``Test*`` classes —
    this suite's two collection shapes (see e.g. ``tests/test_muse.py`` and
    ``tests/test_muse_tool_loop_ast.py``). Zero means a file that imports
    cleanly but has been emptied of its actual pins — the "still exists,
    quietly gutted" failure mode a bare file-exists check would miss.
    """
    count = 0
    for name, value in vars(module).items():
        if name.startswith("test_") and callable(value):
            count += 1
        elif name.startswith("Test") and isinstance(value, type):
            count += sum(
                1
                for attr_name, attr in vars(value).items()
                if attr_name.startswith("test_") and callable(attr)
            )
    return count


class TestMuseSurfaceIsNeverDeleted:
    """The muse's opt-in modules and their tests must stay importable and non-empty."""

    @pytest.mark.parametrize("module_name", sorted(_MUSE_MODULES))
    def test_muse_module_importable_with_expected_public_surface(self, module_name: str):
        # An ImportError HERE is the failure, so it is left to surface on its own
        # rather than being caught and re-raised as a prettier message. Read
        # _DELETION_CONTEXT above for why a failure on this line matters: this
        # module is part of the muse's opt-in surface and must stay importable
        # even though the reference rig does not dial it.
        module = importlib.import_module(module_name)
        missing = [name for name in _MUSE_MODULES[module_name] if not hasattr(module, name)]
        assert not missing, (
            f"{module_name} imports, but is missing expected public name(s) "
            f"{missing!r}. {_DELETION_CONTEXT} A module reduced to an empty or "
            f"near-empty stub still 'exists' as a file, which is why this checks "
            f"its public surface rather than only its presence on disk."
        )

    @pytest.mark.parametrize("module_name", _MUSE_TEST_MODULES)
    def test_muse_primary_test_file_importable_and_non_empty(self, module_name: str):
        self._assert_test_module_survives(module_name, harness=False)

    @pytest.mark.parametrize("module_name", _MUSE_HARNESS_TEST_MODULES)
    def test_muse_harness_test_file_importable_and_non_empty(self, module_name: str):
        self._assert_test_module_survives(module_name, harness=True)

    @staticmethod
    def _assert_test_module_survives(module_name: str, *, harness: bool) -> None:
        kind = "harness/measurement" if harness else "primary"
        # As above: an ImportError here IS the assertion, so it is left to
        # surface. _DELETION_CONTEXT explains the stake — "the cycle's changes
        # delete no muse module, test or harness" names these files explicitly.
        module = importlib.import_module(module_name)
        count = _collectible_test_count(module)
        assert count > 0, (
            f"{module_name} ({kind} muse test) imports but defines no collectible "
            f"test — it has effectively been deleted even though the file "
            f"survives. {_DELETION_CONTEXT}"
        )


# ── guard 2: the worker role's promotion gate ───────────────────────────────
#
# A new `worker` role is under evaluation by a pre-registered experiment
# series (the orchestrator-worker-architectures plan, carried into the
# error-derived-timeouts plan as task t10). Its decision rule mirrors the one
# that already governs the muse: `worker` may enter a SHIPPED reference-rig
# table only once that series returns a SUPPORTING measured verdict. An
# INCONCLUSIVE result — exactly what t18's muse-arms series returned for the
# muse — must leave the shipped rig untouched.
#
# This flag is the deliberate promotion switch, in the spirit of
# tests/test_zero_deps.py's pinned-dependency mechanism: "updating a pinned
# set IS the approval." It must stay False until a supporting verdict exists
# AND is cited below (a docs/live-test-results/*.md path, the same convention
# `d15` uses for the muse-arms and arena-budget series). Flipping it to True
# with no such citation is exactly the silent promotion this guard exists to
# stop — so flipping it should come with filling in the citation, not just
# deleting the assertion beneath it.
_WORKER_ROLE_HAS_SUPPORTING_VERDICT = False
#: Populated together with the flag above — a path (relative to the repo
#: root, under :data:`_VERDICT_DIRNAME`) to the measured verdict that
#: justifies the promotion.
_WORKER_ROLE_VERDICT_CITATION = ""

#: Where a measured verdict lives in this repo. `d15` cites muse-arms.md and
#: arena-budget.md from here; a promotion citing anything else is citing
#: something that is not a committed measured result.
_VERDICT_DIRNAME = "docs/live-test-results"

#: What would open the gate — recorded here so it is a *checklist*, not a
#: memory. Quoted verbatim in the failure message below.
_WORKER_PROMOTION_OPENS_WHEN = """\
1. The pre-registered series (plan task t10 — arms E/W/M/H plus B and P, the
   width rung) has RUN and published a verdict document under
   docs/live-test-results/. At the time this guard was extended, arm B and arm
   P existed only as harnesses with hermetic tests: no measured dial had
   happened, so there was nothing to read yet. That is the state the closed
   flag below records.
2. That verdict SUPPORTS the worker tier on the pre-registered outcome metric.
   INCONCLUSIVE and CEILING are not support — leaving the shipped rig
   untouched on an INCONCLUSIVE is precisely the call d15 made for the muse
   after t18's muse-arms series.
3. The SAME pull request that adds a `worker` row to a shipped rig table also
   flips _WORKER_ROLE_HAS_SUPPORTING_VERDICT to True and fills
   _WORKER_ROLE_VERDICT_CITATION with that verdict's path. One reviewable
   change, so the row and its evidence are read together."""


@dataclass(frozen=True)
class _RigTable:
    """One shipped markdown table describing the reference rig's roles.

    Both files below carry the rig, in tables that differ only in their third
    column heading, so the guard is anchored per file rather than assuming one
    shape. ``sanity_row`` is a concept the table is known to carry today — the
    parser's own self-check, so a broken parse fails loudly instead of
    reporting "no worker role here" from an empty list.
    """

    path: Path
    columns: tuple[str, str, str]
    section: str
    sanity_row: str

    @property
    def heading(self) -> str:
        return " | ".join(self.columns)

    @property
    def header_pattern(self) -> re.Pattern[str]:
        return re.compile(r"^\|\s*" + r"\s*\|\s*".join(self.columns) + r"\s*\|\s*$")


#: Every shipped surface that presents the reference rig as a role table.
#: `culture.yaml` is deliberately NOT here: it declares this repo's own *mesh
#: agent* (one entry — suffix/backend/model), not the Gwen rig's cognitive
#: roles, and roles resolve by name from a lobes `/capabilities` contract
#: rather than from that file. A "no worker in culture.yaml" assertion would
#: not go red on the promotion this gate exists to stop, which makes it
#: decoration rather than a guard.
_RIG_TABLES: tuple[_RigTable, ...] = (
    _RigTable(
        path=_CLAUDE_MD,
        columns=("Concept", "Value", "Serves"),
        section="Identity — embodiment, and the Gwen it ships",
        sanity_row="Cortex",
    ),
    _RigTable(
        path=_README_MD,
        columns=("Concept", "Value", "Authority"),
        section="Gwen — the embodiment we ship",
        sanity_row="Cortex",
    ),
)


class _TableRow:
    """One parsed row of a markdown pipe table."""

    __slots__ = ("concept", "value", "serves")

    def __init__(self, concept: str, value: str, serves: str) -> None:
        self.concept = concept
        self.value = value
        self.serves = serves

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"_TableRow(concept={self.concept!r}, value={self.value!r})"


def _strip_markdown_emphasis(cell: str) -> str:
    """Drop ``**bold**``, ``*italic*`` and `` `code` `` markers.

    So the word check below reads the plain text a human sees, not the
    markup around it.
    """
    text = cell.strip()
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*(?!\*)|(?<!\*)\*(?!\w)", "", text)
    return text.strip()


def _split_table_row(line: str) -> list[str]:
    """Split one ``| a | b | c |`` markdown table line into its cells."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-+:?", cell) for cell in cells if cell)


def _parse_rig_table(table: _RigTable, text: str) -> list[_TableRow]:
    """Parse *table*'s ACTUAL rows out of *text*.

    Deliberately anchored on the exact header row rather than a substring
    search over the whole document, so an unrelated mention of the word
    "worker" in prose elsewhere in the file can never trip the guard below —
    ``README.md`` genuinely carries several, describing the drone tier's
    scoped worker calls. Fails loudly (rather than returning an empty list) if
    the table cannot be found at all, because a silently-empty parse would
    make the promotion-gate test below pass vacuously — the worst possible
    failure mode for a guard.
    """
    lines = text.splitlines()
    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        if table.header_pattern.match(line):
            header_index = index
            break
    assert header_index is not None, (
        f"Could not locate the {table.heading!r} reference-rig table in "
        f"{table.path}. This test parses that table by its exact header row "
        f"rather than scanning the whole file, so if that file's "
        f"'{table.section}' section has been restructured, this guard needs to "
        f"be re-anchored deliberately (update _RIG_TABLES or the parser) — not "
        f"silently skipped."
    )
    separator_index = header_index + 1
    assert separator_index < len(lines), (
        f"{table.path} ends at line {len(lines)}, but the {table.heading!r} "
        f"table's '---' separator row was expected on line {separator_index + 1}; "
        f"the table's shape has changed and this parser needs a deliberate update."
    )
    assert _is_separator_row(_split_table_row(lines[separator_index])), (
        f"{table.path} line {separator_index + 1} was expected to be the "
        f"{table.heading!r} table's '---' separator row and was not; the table's "
        f"shape has changed and this parser needs a deliberate update."
    )
    rows: list[_TableRow] = []
    for line in lines[separator_index + 1 :]:
        if not line.strip().startswith("|"):
            break
        cells = _split_table_row(line)
        if len(cells) < 3:
            continue
        rows.append(_TableRow(cells[0], cells[1], cells[2]))
    assert rows, (
        f"The {table.heading!r} table in {table.path} parsed to zero data rows — "
        f"that table is known to carry Runtime/Cortex/Muse/Senses/Ears rows "
        f"today, so an empty parse means the parser broke, not that the table is "
        f"genuinely empty."
    )
    return rows


def _mentions_worker_role(concept_cell: str) -> bool:
    plain = _strip_markdown_emphasis(concept_cell)
    return re.search(r"\bworker\b", plain, re.IGNORECASE) is not None


def _worker_rows(table: _RigTable, text: str) -> list[_TableRow]:
    return [row for row in _parse_rig_table(table, text) if _mentions_worker_role(row.concept)]


class TestWorkerRolePromotionGate:
    """`worker` may not enter a shipped reference-rig table without a verdict."""

    @pytest.mark.parametrize("table", _RIG_TABLES, ids=lambda table: table.path.name)
    def test_reference_rig_table_is_parseable(self, table: _RigTable):
        """A sanity check on the parser itself, isolated from the promotion assertion.

        If THIS fails, the promotion-gate test below is not trustworthy either
        (it would either fail for the wrong reason or — worse — pass
        vacuously on an empty parse), so the two are kept as separate tests.
        """
        rows = _parse_rig_table(table, table.path.read_text(encoding="utf-8"))
        concepts = {_strip_markdown_emphasis(row.concept) for row in rows}
        assert table.sanity_row in concepts, (
            f"sanity check failed: the parsed {table.heading!r} table in "
            f"{table.path} does not contain the {table.sanity_row!r} row it is "
            f"known to carry today; the parser is reading the wrong table or "
            f"reading it wrong"
        )

    @pytest.mark.parametrize("table", _RIG_TABLES, ids=lambda table: table.path.name)
    def test_worker_role_not_in_reference_rig_table(self, table: _RigTable):
        """The promotion gate itself.

        No supporting measured verdict for `worker` exists yet
        (``_WORKER_ROLE_HAS_SUPPORTING_VERDICT`` is ``False``), so no shipped
        reference-rig table may list it as a role. This mirrors how `d15`
        treated the muse itself: t18's muse-arms series came back
        INCONCLUSIVE, and the muse stayed out of the reference rig rather than
        being promoted on a maybe.
        """
        if _WORKER_ROLE_HAS_SUPPORTING_VERDICT:
            pytest.skip(
                "promotion gate open (_WORKER_ROLE_HAS_SUPPORTING_VERDICT=True); "
                "see test_worker_role_promotion_flag_is_evidenced for the citation check"
            )
        found = _worker_rows(table, table.path.read_text(encoding="utf-8"))
        assert not found, (
            f"{table.path.name}'s reference-rig table ({table.heading!r}, under "
            f"'{table.section}') now lists a `worker` role ({found!r}), but no "
            f"supporting measured verdict has been recorded for it — this test's "
            f"_WORKER_ROLE_HAS_SUPPORTING_VERDICT flag in "
            f"tests/test_governance.py is still False. If you have that verdict, "
            f"this is a DELIBERATE promotion and not a bug to silence. What "
            f"opening the gate takes:\n\n{_WORKER_PROMOTION_OPENS_WHEN}"
        )

    def test_the_promotion_flag_and_its_citation_agree(self):
        """A half-flipped gate is caught in both directions.

        Closed-with-a-citation would read as "we have the evidence but did not
        promote", which is a claim nobody made; open-without-one is the silent
        promotion this class exists to stop.
        """
        if _WORKER_ROLE_HAS_SUPPORTING_VERDICT:
            assert _WORKER_ROLE_VERDICT_CITATION, (
                "_WORKER_ROLE_HAS_SUPPORTING_VERDICT is True but "
                "_WORKER_ROLE_VERDICT_CITATION is empty — the promotion gate must "
                "not open without a recorded verdict path to point at."
            )
        else:
            assert not _WORKER_ROLE_VERDICT_CITATION, (
                f"the promotion gate is closed "
                f"(_WORKER_ROLE_HAS_SUPPORTING_VERDICT=False) but a citation "
                f"({_WORKER_ROLE_VERDICT_CITATION!r}) has been filled in. Those two "
                f"say opposite things. Flip the flag in the same change, or clear "
                f"the citation.\n\n{_WORKER_PROMOTION_OPENS_WHEN}"
            )

    def test_worker_role_promotion_flag_is_evidenced(self):
        """If the promotion flag is ever flipped, it must point at real evidence.

        This does not — and cannot — replace a human judging whether the
        cited series actually SUPPORTS promoting the role; only a human can
        read a verdict and decide that. It only stops the cheapest failure
        mode: flipping the flag with an empty, misfiled or nonexistent
        citation, which would otherwise silently reopen the gate this class
        exists to hold shut.
        """
        if not _WORKER_ROLE_HAS_SUPPORTING_VERDICT:
            pytest.skip("promotion gate closed; nothing to verify yet")
        cited = _REPO_ROOT / _WORKER_ROLE_VERDICT_CITATION
        assert cited.is_file(), (
            f"_WORKER_ROLE_VERDICT_CITATION={_WORKER_ROLE_VERDICT_CITATION!r} does "
            f"not point at a real file ({cited}); a promotion needs a real, "
            f"committed measured result to cite, not a placeholder."
        )
        assert _WORKER_ROLE_VERDICT_CITATION.startswith(f"{_VERDICT_DIRNAME}/"), (
            f"_WORKER_ROLE_VERDICT_CITATION={_WORKER_ROLE_VERDICT_CITATION!r} is "
            f"not under {_VERDICT_DIRNAME}/, which is where this repo's measured "
            f"verdicts live (d15 cites muse-arms.md and arena-budget.md from "
            f"there). A design document, an issue thread or a plan file is not a "
            f"measured verdict."
        )


# ── guard 3: drones ship opt-in and off (claim c25) ─────────────────────────
#
# The standing rule is that a MEASURED failure mode never ships as default
# behaviour. This is its mirror image: the drone tier implements issue #44's
# worker-harness caste, whose validating experiment has not run, so an
# UNMEASURED design does not ship on either.
#
# Everything below is read off the shipped constant, the resolver with an
# explicit empty environment, or the module's AST. No drone is authored,
# saved or evoked — t11/t12 hoisted `DRONES_ENABLED_BY_DEFAULT` onto the
# package surface precisely so a governance guard would not have to.
# tests/test_drone_safeguards.py owns the behavioural half (a marker drone
# that leaves a trace on disk when it runs, and does not); this file owns the
# ruling.

_DRONE_SOURCE_PATH = Path(drone_lib.__file__)

#: The one named bypass of the ambient switch: `create`'s smoke run against
#: the copy it is staging in a temp directory, from source the caller handed
#: in this second. It is not a hole, and a SECOND one would be.
_AUTHORING_BYPASS = "_AUTHORING_OPT_IN"

#: Values that must never resolve to "run model-written code in this process".
#: A guard that resolves ambiguity toward running is not a guard, so the
#: unrecognised cases are enumerated rather than assumed. The near-misses are
#: the point: ``"2"`` and ``"true-ish"`` are the shapes a "treat anything
#: non-empty as on" rewrite would start accepting.
_MUST_NOT_ENABLE = ("", "0", "false", "no", "off", "maybe", "banana", "2", "true-ish", "onn")

#: Where shipped code lives. Nothing under these trees may turn the switch on
#: for the user; the switch is the user's to set.
_SHIPPED_TREES = ("embodiment", "examples")

_OPT_IN_CONTEXT = (
    "Drones ship opt-in and OFF (claim c25). The design is #44's "
    "worker-harness caste and its validating experiment has NOT run, so the "
    "standing rule's mirror image applies: an unmeasured behaviour does not "
    "ship as default behaviour either. Turning this on is a deliberate, "
    "reviewable act that needs #44's verdict behind it — exactly like "
    "tests/test_zero_deps.py's pinned dependency set."
)


def _call_enables(call: ast.Call) -> bool:
    """Does this ``DroneOptIn(...)`` construct an *enabled* opt-in?

    Errs toward saying yes: anything that is not a literal false — a name, a
    call, a ``**kwargs`` spread — counts as enabled, because a guard that
    cannot read a construction should flag it rather than wave it through.
    """
    for keyword in call.keywords:
        if keyword.arg is None:
            return True  # **kwargs — unreadable, so flag it
        if keyword.arg == "enabled":
            if isinstance(keyword.value, ast.Constant):
                return bool(keyword.value.value)
            return True
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant):
            return bool(first.value)
        return True
    return False


def _module_scope_enabled_optins(source: str) -> list[str]:
    """Names bound at MODULE scope to an enabled ``DroneOptIn``.

    Module scope only, and deliberately: ``opt_in_from_env`` legitimately
    builds an enabled opt-in *inside* a function when the operator has set the
    switch, and that is the intended path. What must stay singular is the set
    of standing constants that say "enabled" before anyone asked — today
    exactly :data:`_AUTHORING_BYPASS`.
    """
    found: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "DroneOptIn" or not _call_enables(value):
            continue
        found.extend(target.id for target in node.targets if isinstance(target, ast.Name))
    return sorted(found)


def _bypass_users(source: str) -> list[str]:
    """Which functions read :data:`_AUTHORING_BYPASS`."""
    tree = ast.parse(source)
    return sorted(
        {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(inner, ast.Name) and inner.id == _AUTHORING_BYPASS
                for inner in ast.walk(node)
            )
        }
    )


def _names_the_switch(node: ast.AST) -> bool:
    """Does this subtree name the drone opt-in environment variable?"""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and inner.value == drone_lib.DRONES_ENABLED_ENV:
            return True
        if isinstance(inner, ast.Name) and inner.id == "DRONES_ENABLED_ENV":
            return True
        if isinstance(inner, ast.Attribute) and inner.attr == "DRONES_ENABLED_ENV":
            return True
    return False


def _is_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"


def _switch_writes(source: str) -> list[int]:
    """Line numbers where *source* turns the drone switch on in the environment.

    Catches the direct, readable forms — ``os.environ[SWITCH] = ...``,
    ``os.environ.setdefault(SWITCH, ...)``, ``os.environ.update({SWITCH: ...})``
    and ``os.putenv(SWITCH, ...)``. It cannot see a switch smuggled through a
    mapping built elsewhere, and does not pretend to; those forms are the ones
    a convenience default would actually be written as.
    """
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _is_environ(target.value)
                    and _names_the_switch(target.slice)
                ):
                    found.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attribute = node.func
            writes_env = (
                attribute.attr in ("setdefault", "update") and _is_environ(attribute.value)
            ) or attribute.attr == "putenv"
            if writes_env and any(_names_the_switch(argument) for argument in node.args):
                found.append(node.lineno)
    return sorted(set(found))


def _shipped_python_files() -> list[Path]:
    files: list[Path] = []
    for tree in _SHIPPED_TREES:
        files.extend(sorted((_REPO_ROOT / tree).rglob("*.py")))
    return files


class TestDronesShipOptInAndOff:
    """The c25 ruling, enforced — without authoring, saving or evoking a drone."""

    def test_the_shipped_default_is_off(self):
        assert drone_lib.DRONES_ENABLED_BY_DEFAULT is False, _OPT_IN_CONTEXT

    def test_the_package_surface_says_the_same_thing_as_the_module(self):
        """A host reads the constant off ``embodiment``; the guard must too.

        `DRONES_ENABLED_BY_DEFAULT` is hoisted onto the package surface
        specifically so this ruling is assertable without importing the drone
        machinery. If the hoisted value and the module's ever disagreed, a host
        checking the documented surface would be reading a different answer
        from the one `invoke` acts on.
        """
        import embodiment

        assert embodiment.DRONES_ENABLED_BY_DEFAULT is drone_lib.DRONES_ENABLED_BY_DEFAULT
        assert embodiment.DRONES_ENABLED_BY_DEFAULT is False, _OPT_IN_CONTEXT

    def test_a_fresh_checkout_resolves_to_disabled(self):
        """``env={}`` IS a fresh checkout: nothing set, nothing passed."""
        resolved = drone_lib.resolve_opt_in(env={})
        assert resolved.enabled is False, _OPT_IN_CONTEXT
        assert resolved.source == "default"
        assert resolved.detail, "a refusal has to say why, not just say no"

    @pytest.mark.parametrize("value", _MUST_NOT_ENABLE)
    def test_an_unrecognised_value_fails_closed(self, value: str):
        """Ambiguity resolves toward *not* running model-written code.

        The behavioural half of this lives in tests/test_drone_safeguards.py;
        it is restated here because "fails closed" is the part of the ruling
        that a well-meaning usability fix ("treat anything non-empty as on")
        would quietly reverse.
        """
        resolved = drone_lib.resolve_opt_in(env={drone_lib.DRONES_ENABLED_ENV: value})
        assert resolved.enabled is False, (
            f"${drone_lib.DRONES_ENABLED_ENV}={value!r} resolved to ENABLED. "
            f"{_OPT_IN_CONTEXT} An opt-in switch that guesses is not a switch."
        )
        assert resolved.detail, "a refusal has to say why, not just say no"

    def test_exactly_one_standing_bypass_exists_and_it_is_the_authoring_one(self):
        """A second bypass appearing later is exactly the drift this file catches.

        tests/test_drone_safeguards.py pins who *uses* the bypass; this pins
        that no second one is ever *declared*. The overlap is deliberate: that
        file describes a feature and may be rewritten as the feature evolves,
        while this one records a ruling that outlives it.
        """
        source = _DRONE_SOURCE_PATH.read_text(encoding="utf-8")
        declared = _module_scope_enabled_optins(source)
        assert declared == [_AUTHORING_BYPASS], (
            f"{_DRONE_SOURCE_PATH.name} declares module-scope enabled opt-ins "
            f"{declared!r}; exactly [{_AUTHORING_BYPASS!r}] is allowed. "
            f"{_OPT_IN_CONTEXT} A standing constant that says 'enabled' before "
            f"anyone asked is a default-on execution path however it is named."
        )
        users = _bypass_users(source)
        assert users == ["create"], (
            f"{_AUTHORING_BYPASS} is used by {users!r} in "
            f"{_DRONE_SOURCE_PATH.name}. It belongs to `create` alone — where it "
            f"applies to a staged copy in a temp directory, from source the "
            f"caller just handed in. Anywhere else it is a second way drone "
            f"execution enables itself. {_OPT_IN_CONTEXT}"
        )

    def test_no_shipped_module_turns_the_switch_on(self):
        """The switch is the operator's to set — never ours to set for them.

        Flipping ``DRONES_ENABLED_BY_DEFAULT`` is the obvious way to revert
        this ruling and the test above catches it. Exporting
        ``$EMBODIMENT_DRONES_ENABLED`` from shipped code is the *quiet* way:
        every existing drone test passes an explicit env mapping, so none of
        them would notice.
        """
        offenders = {
            str(path.relative_to(_REPO_ROOT)): lines
            for path in _shipped_python_files()
            if (lines := _switch_writes(path.read_text(encoding="utf-8")))
        }
        assert not offenders, (
            f"shipped code writes ${drone_lib.DRONES_ENABLED_ENV} into the "
            f"environment at {offenders!r}. {_OPT_IN_CONTEXT} Documentation may "
            f"show the variable being set on the command line (it does, in "
            f"embodiment/explain/catalog.py); shipped code may not set it."
        )


# ── the proof that each guard can fail ──────────────────────────────────────
#
# Every guard above rests on a detector: a collectible-test count, a markdown
# table parse, or an AST scan. A green suite is equally consistent with three
# detectors that never fire, so each one is fed the mutation it exists to
# catch. These are hermetic and operate on synthetic inputs — the companion
# script tests/prove_governance_guards.py mutates the REAL committed files and
# re-runs this module against each mutation, which is the stronger proof and
# the one whose results are reported with the task.


class TestTheseGuardsCanFail:
    """Feed each detector its mutation; assert it fires."""

    def test_the_collectible_test_counter_reports_zero_for_a_gutted_module(self):
        import types

        gutted = types.ModuleType("_gutted")
        gutted.__doc__ = "a file that still imports and pins nothing"
        assert _collectible_test_count(gutted) == 0

        populated = types.ModuleType("_populated")
        populated.test_something = lambda: None  # type: ignore[attr-defined]
        assert _collectible_test_count(populated) == 1

    @pytest.mark.parametrize("table", _RIG_TABLES, ids=lambda table: table.path.name)
    def test_a_worker_row_added_to_a_rig_table_is_detected(self, table: _RigTable):
        """The promotion the gate exists to stop, applied to the real file's text."""
        text = table.path.read_text(encoding="utf-8")
        assert not _worker_rows(table, text), "precondition: no worker row today"
        promoted = text.replace(
            f"| {table.sanity_row} |",
            "| Worker | Qwen 3.6 4B | scoped typed questions |\n" f"| {table.sanity_row} |",
            1,
        )
        found = _worker_rows(table, promoted)
        assert [row.concept for row in found] == ["Worker"], (
            "a `worker` row was added to the rig table and the guard did not see "
            "it — the promotion gate is decoration"
        )

    @pytest.mark.parametrize("table", _RIG_TABLES, ids=lambda table: table.path.name)
    def test_prose_mentioning_workers_does_not_trip_the_gate(self, table: _RigTable):
        """The other half: the guard must be a guard, not a landmine.

        ``README.md`` already describes the drone tier's scoped worker calls in
        prose. A substring search over the file would be red today for reasons
        that have nothing to do with promoting a role.
        """
        text = table.path.read_text(encoding="utf-8")
        with_prose = text + "\n\nThe drone asks a worker for a typed answer.\n"
        assert not _worker_rows(table, with_prose)

    @pytest.mark.parametrize("table", _RIG_TABLES, ids=lambda table: table.path.name)
    def test_a_vanished_table_fails_loudly_rather_than_passing_empty(self, table: _RigTable):
        """A parser that returned ``[]`` here would make the gate pass vacuously."""
        text = table.path.read_text(encoding="utf-8")
        without = text.replace(f"| {table.columns[2]} |", "| Something Else |", 1)
        with pytest.raises(AssertionError, match="Could not locate"):
            _parse_rig_table(table, without)

    def test_a_second_standing_bypass_is_detected(self):
        source = _DRONE_SOURCE_PATH.read_text(encoding="utf-8")
        assert _module_scope_enabled_optins(source) == [_AUTHORING_BYPASS]
        smuggled = source.replace(
            f"{_AUTHORING_BYPASS} = DroneOptIn(",
            '_HOST_OPT_IN = DroneOptIn(\n    enabled=True,\n    source="host",\n)\n\n'
            f"{_AUTHORING_BYPASS} = DroneOptIn(",
            1,
        )
        assert _module_scope_enabled_optins(smuggled) == ["_AUTHORING_OPT_IN", "_HOST_OPT_IN"]

    def test_an_unreadable_enabled_argument_is_flagged_rather_than_waved_through(self):
        """``DroneOptIn(enabled=_SOME_FLAG)`` must count as enabled, not as unknown."""
        assert _module_scope_enabled_optins("X = DroneOptIn(enabled=_FLAG)") == ["X"]
        assert _module_scope_enabled_optins("X = DroneOptIn(**opts)") == ["X"]
        assert _module_scope_enabled_optins("X = DroneOptIn(enabled=False)") == []

    def test_the_bypass_user_scan_sees_a_new_user(self):
        source = _DRONE_SOURCE_PATH.read_text(encoding="utf-8")
        assert _bypass_users(source) == ["create"]
        widened = source.replace(
            "def smoke(",
            f"def _quietly_enable():\n    return {_AUTHORING_BYPASS}\n\n\ndef smoke(",
            1,
        )
        assert _bypass_users(widened) == ["_quietly_enable", "create"]

    @pytest.mark.parametrize(
        "line",
        (
            'os.environ["EMBODIMENT_DRONES_ENABLED"] = "1"',
            'os.environ[DRONES_ENABLED_ENV] = "1"',
            'os.environ.setdefault(DRONES_ENABLED_ENV, "1")',
            'os.environ.update({DRONES_ENABLED_ENV: "1"})',
            'os.putenv(DRONES_ENABLED_ENV, "1")',
            'environ.setdefault(drone.DRONES_ENABLED_ENV, "1")',
        ),
    )
    def test_a_shipped_module_flipping_the_switch_is_detected(self, line: str):
        assert _switch_writes(line) == [1], f"{line!r} slipped past the scan"

    @pytest.mark.parametrize(
        "line",
        (
            "os.environ.get(DRONES_ENABLED_ENV)",
            "raw = env.get(DRONES_ENABLED_ENV)",
            'os.environ.setdefault("EMBODIMENT_DRONES_DIR", "/tmp/d")',  # nosec B108
        ),
    )
    def test_reading_the_switch_and_writing_other_variables_are_not_flagged(self, line: str):
        """The guard must not go red on the code that legitimately reads it."""
        assert _switch_writes(line) == []
