"""Hermetic governance guards for the orchestrator-worker-architectures plan (task t15).

Two operator rulings are stated as prose in ``CLAUDE.md`` and neither one is
self-enforcing — prose can drift silently the moment nobody is reading it
closely. This file makes both rulings CI-checkable instead:

1. **The muse ships as opt-in code, off by default, NEVER deleted**
   (deviation ``d15`` — see ``CLAUDE.md``, "Identity — embodiment, and the
   Gwen it ships"). The reference rig runs muse-off, but "off by default" is
   not "gone": :mod:`embodiment.muse`, :mod:`embodiment.muse_runner`,
   :mod:`embodiment.muse_pad` and :mod:`embodiment.workspace` — plus their
   tests — are a host's opt-in seam and must stay importable and collectible.
   :class:`TestMuseSurfaceIsNeverDeleted` guards this.

2. **A new role only enters the shipped reference-rig identity table with a
   supporting measured verdict; an INCONCLUSIVE result leaves the rig
   untouched.** This mirrors how ``d15`` itself was decided — the muse-arms
   series (``docs/live-test-results/muse-arms.md``) returned ``INCONCLUSIVE``
   and the muse stayed out of the reference rig rather than being promoted on
   a maybe. The same decision rule now governs the `worker` role a
   pre-registered experiment series is evaluating.
   :class:`TestWorkerRolePromotionGate` guards this, by parsing the ACTUAL
   "Concept | Value | Serves" identity table in ``CLAUDE.md`` — not a
   substring search over the whole file, so an unrelated mention of the word
   "worker" in prose elsewhere in the document cannot trip it.

Both guards are meant to be *guards*, not landmines: each failure message
below says exactly what changed and exactly what a deliberate promotion looks
like, mirroring the existing convention in ``tests/test_zero_deps.py``
("updating a pinned set IS the approval" — a considered, reviewable edit,
never a reflex to get CI green).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"


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
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - the failure itself is the assertion
            pytest.fail(
                f"{module_name} failed to import ({exc!r}). {_DELETION_CONTEXT} "
                f"This module is part of that opt-in surface and must stay "
                f"importable even though the reference rig does not dial it."
            )
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
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - the failure itself is the assertion
            pytest.fail(
                f"{module_name} ({kind} muse test) failed to import ({exc!r}). "
                f"{_DELETION_CONTEXT} 'The cycle's changes delete no muse module, "
                f"test or harness' names this file explicitly."
            )
        count = _collectible_test_count(module)
        assert count > 0, (
            f"{module_name} ({kind} muse test) imports but defines no collectible "
            f"test — it has effectively been deleted even though the file "
            f"survives. {_DELETION_CONTEXT}"
        )


# ── guard 2: the worker role's promotion gate ───────────────────────────────
#
# A new `worker` role is under evaluation by a pre-registered experiment
# series (the orchestrator-worker-architectures plan). Its decision rule
# mirrors the one that already governs the muse: `worker` may enter the
# SHIPPED reference-rig identity table in CLAUDE.md only once that series
# returns a SUPPORTING measured verdict. An INCONCLUSIVE result — exactly
# what t18's muse-arms series returned for the muse — must leave the shipped
# rig untouched.
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
#: root) to the measured verdict that justifies the promotion.
_WORKER_ROLE_VERDICT_CITATION = ""

_IDENTITY_TABLE_HEADING = "Concept | Value | Serves"
_IDENTITY_TABLE_SECTION_HINT = "Gwen — the embodiment we ship"


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


def _parse_reference_rig_identity_table(text: str) -> list[_TableRow]:
    """Parse the ACTUAL "Concept | Value | Serves" table out of *text*.

    Deliberately anchored on the exact header row rather than a substring
    search over the whole document, so an unrelated mention of the word
    "worker" in prose elsewhere in ``CLAUDE.md`` can never trip the guard
    below. Fails loudly (rather than returning an empty list) if the table
    cannot be found at all, because a silently-empty parse would make the
    promotion-gate test below pass vacuously — the worst possible failure
    mode for a guard.
    """
    lines = text.splitlines()
    header_pattern = re.compile(r"^\|\s*Concept\s*\|\s*Value\s*\|\s*Serves\s*\|\s*$")
    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        if header_pattern.match(line):
            header_index = index
            break
    assert header_index is not None, (
        f"Could not locate the {_IDENTITY_TABLE_HEADING!r} reference-rig identity "
        f"table in {_CLAUDE_MD}. This test parses that table by its exact header "
        f"row rather than scanning the whole file, so if CLAUDE.md's "
        f"'{_IDENTITY_TABLE_SECTION_HINT}' section has been restructured, this "
        f"guard needs to be re-anchored deliberately (update the header pattern "
        f"or the parser below) — not silently skipped."
    )
    separator_index = header_index + 1
    assert separator_index < len(lines) and _is_separator_row(
        _split_table_row(lines[separator_index])
    ), (
        f"CLAUDE.md line {separator_index + 1} was expected to be the "
        f"{_IDENTITY_TABLE_HEADING!r} table's '---' separator row and was not; "
        f"the table's shape has changed and this parser needs a deliberate update."
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
        f"The {_IDENTITY_TABLE_HEADING!r} table in {_CLAUDE_MD} parsed to zero "
        f"data rows — that table is known to carry Runtime/Cortex/Muse/Senses/"
        f"Ears rows today, so an empty parse means the parser broke, not that "
        f"the table is genuinely empty."
    )
    return rows


def _mentions_worker_role(concept_cell: str) -> bool:
    plain = _strip_markdown_emphasis(concept_cell)
    return re.search(r"\bworker\b", plain, re.IGNORECASE) is not None


class TestWorkerRolePromotionGate:
    """`worker` may not enter the shipped reference-rig identity table without a verdict."""

    def test_reference_rig_table_is_parseable(self):
        """A sanity check on the parser itself, isolated from the promotion assertion.

        If THIS fails, the promotion-gate test below is not trustworthy either
        (it would either fail for the wrong reason or — worse — pass
        vacuously on an empty parse), so the two are kept as separate tests.
        """
        rows = _parse_reference_rig_identity_table(_CLAUDE_MD.read_text(encoding="utf-8"))
        concepts = {_strip_markdown_emphasis(row.concept) for row in rows}
        assert "Cortex" in concepts, (
            "sanity check failed: the parsed reference-rig identity table does not "
            "contain the 'Cortex' row it is known to carry today; the parser is "
            "reading the wrong table or reading it wrong"
        )

    def test_worker_role_not_in_reference_rig_identity_table(self):
        """The promotion gate itself.

        No supporting measured verdict for `worker` exists yet
        (``_WORKER_ROLE_HAS_SUPPORTING_VERDICT`` is ``False``), so the
        reference-rig identity table in CLAUDE.md must not list it as a
        shipped role. This mirrors how `d15` treated the muse itself: t18's
        muse-arms series came back INCONCLUSIVE, and the muse stayed out of
        the reference rig rather than being promoted on a maybe.
        """
        rows = _parse_reference_rig_identity_table(_CLAUDE_MD.read_text(encoding="utf-8"))
        worker_rows = [row for row in rows if _mentions_worker_role(row.concept)]
        if _WORKER_ROLE_HAS_SUPPORTING_VERDICT:
            pytest.skip(
                "promotion gate open (_WORKER_ROLE_HAS_SUPPORTING_VERDICT=True); "
                "see test_worker_role_promotion_flag_is_evidenced for the citation check"
            )
        assert not worker_rows, (
            f"CLAUDE.md's reference-rig identity table ({_IDENTITY_TABLE_HEADING!r}, "
            f"under '{_IDENTITY_TABLE_SECTION_HINT}') now lists a `worker` role "
            f"({worker_rows!r}), but no supporting measured verdict has been "
            f"recorded for it — this test's _WORKER_ROLE_HAS_SUPPORTING_VERDICT "
            f"flag in tests/test_governance.py is still False. Per the "
            f"orchestrator-worker-architectures plan's decision rule, `worker` "
            f"may only ship in the reference rig once its pre-registered "
            f"experiment series returns a SUPPORTING verdict — an INCONCLUSIVE "
            f"result (as t18's muse-arms series returned for the muse; see "
            f"CLAUDE.md's d15 discussion) must leave the shipped rig untouched. "
            f"If you have that verdict: this is a DELIBERATE promotion, not a "
            f"bug to silence — cite the verdict (a docs/live-test-results/*.md "
            f"path) in _WORKER_ROLE_VERDICT_CITATION, flip "
            f"_WORKER_ROLE_HAS_SUPPORTING_VERDICT to True, and update this test "
            f"file accordingly as part of the same change that edits CLAUDE.md."
        )

    def test_worker_role_promotion_flag_is_evidenced(self):
        """If the promotion flag is ever flipped, it must point at real evidence.

        This does not — and cannot — replace a human judging whether the
        cited series actually SUPPORTS promoting the role; only a human can
        read a verdict and decide that. It only stops the cheapest failure
        mode: flipping the flag with an empty or nonexistent citation, which
        would otherwise silently reopen the gate this class exists to hold
        shut.
        """
        if not _WORKER_ROLE_HAS_SUPPORTING_VERDICT:
            pytest.skip("promotion gate closed; nothing to verify yet")
        assert _WORKER_ROLE_VERDICT_CITATION, (
            "_WORKER_ROLE_HAS_SUPPORTING_VERDICT is True but "
            "_WORKER_ROLE_VERDICT_CITATION is empty — the promotion gate must "
            "not open without a recorded verdict path to point at."
        )
        cited = _REPO_ROOT / _WORKER_ROLE_VERDICT_CITATION
        assert cited.is_file(), (
            f"_WORKER_ROLE_VERDICT_CITATION={_WORKER_ROLE_VERDICT_CITATION!r} does "
            f"not point at a real file ({cited}); a promotion needs a real, "
            f"committed measured result to cite, not a placeholder."
        )
