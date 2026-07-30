"""The announcement checklist — task t20's integration verification.

Run it::

    uv run python -m tests.announcement_checklist          # resolve only (hermetic, fast)
    uv run python -m tests.announcement_checklist --run    # execute the cited tests
    uv run python -m tests.announcement_checklist --online # also probe GitHub via `gh`
    uv run python -m tests.announcement_checklist --json   # the same report, machine-readable

**What this is.** One place where each clause of the shipping announcement is
tied to the test that backs it, so the question "is the announcement true today?"
is answered by *running something* rather than by reading prose. It aggregates;
it never re-implements. Every citation points at a test another task already
wrote.

**What it refuses to do.** It never counts anything green that it did not run.
Three things in the announcement's success signals live outside a hermetic
checkout — the GitHub Actions job status and the two issue links — and they are
reported as ``external`` every time, with the exact ``gh`` command that would
settle them. The live-rig acceptance bar (deviation ``d4``) is *recorded and not
met*, and prints as a caveat on every run. A checklist that can only say "yes"
is decoration.

**How it fails.** Citations are pytest node ids resolved against the real test
files by :func:`resolve` before anything runs. Rename or delete a backing test
and the clause turns ``MISSING`` and the exit code is 1 — the clause is never
quietly reported as still-checked. Caveats carry probes for the same reason: a
caveat that has silently become untrue fails rather than lingering.

``tests/test_announcement.py`` guards all of the above, so CI enforces this file
without needing a workflow step of its own.

Exit codes: ``0`` everything cited resolves (and passed, under ``--run``);
``1`` something is missing, failed, or has drifted; ``2`` the checklist could
not run at all.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess  # nosec B404 - opt-in `gh` probes and the --run pytest child
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── statuses ──────────────────────────────────────────────────────────────────
#: The citation names a test that exists. Nothing was executed.
RESOLVED = "resolved"
#: The citation names a test that does NOT exist — drift, and a failure.
MISSING = "missing"
#: The citation was executed here and passed.
VERIFIED = "verified"
#: The citation was executed here and failed.
FAILED = "failed"
#: Not checkable in a hermetic run. Never counted green.
EXTERNAL = "external"
#: An online probe was attempted and could not complete (no `gh`, no auth, …).
UNAVAILABLE = "unavailable"
#: An online probe completed and the thing genuinely is not there yet.
OUTSTANDING = "outstanding"
#: An online probe completed and found something a human must read.
REPORTED = "reported"
#: The provenance quote no longer matches the frame it was taken from.
DRIFTED = "drifted"

#: Signal kinds.
INTERNAL = "internal"

_SYMBOL = {
    RESOLVED: "ok",
    VERIFIED: "PASS",
    FAILED: "FAIL",
    MISSING: "MISSING",
    EXTERNAL: "external",
    UNAVAILABLE: "unavailable",
    OUTSTANDING: "outstanding",
    REPORTED: "reported",
    DRIFTED: "DRIFTED",
}

#: Statuses that make the whole run red.
_RED = frozenset({MISSING, FAILED, DRIFTED})


# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """One pytest node id, and what citing it is meant to prove."""

    node_id: str
    proves: str


@dataclass(frozen=True)
class Clause:
    """One slice of the announcement, and the tests standing behind it."""

    id: str
    title: str
    quote: str
    evidence: tuple[Evidence, ...]
    limit: str = ""
    """What the evidence does NOT establish. Rendered under the clause."""


@dataclass(frozen=True)
class Probe:
    """An opt-in ``gh`` query. Never runs unless ``--online`` is passed."""

    id: str
    description: str
    argv: tuple[str, ...]
    expect: Optional[str] = None
    """Substring that makes the probe :data:`VERIFIED`. ``None`` means no
    machine verdict is possible — a human reads what the probe found."""
    absent: tuple[str, ...] = ("", "[]", "null")
    """Outputs that mean "genuinely not there yet" (:data:`OUTSTANDING`)."""


@dataclass(frozen=True)
class Signal:
    """One of the announcement's three observable success signals."""

    id: str
    text: str
    kind: str
    evidence: tuple[Evidence, ...] = ()
    probes: tuple[Probe, ...] = ()
    why_external: str = ""


def unprobed(_root: Path) -> Optional[str]:
    """The default caveat probe: no mechanical way to tell if it went stale."""
    return None


@dataclass(frozen=True)
class Caveat:
    """Something outstanding, reported rather than fixed.

    ``check`` returns ``None`` while the caveat still stands, or a sentence
    saying why it may be stale — which fails the checklist, because a report
    that keeps warning about a fixed problem stops being read.
    """

    id: str
    title: str
    detail: str
    state: str
    check: Callable[[Path], Optional[str]] = unprobed


@dataclass(frozen=True)
class Divergence:
    """A place where the shipped announcement differs from the frame's claim."""

    was: str
    now: str
    deviation: str
    why: str


# ── the announcement, as shipped ──────────────────────────────────────────────

#: The text this checklist verifies, verbatim.
ANNOUNCEMENT = (
    "embodiment ships colleague's bounded tool loop as a reusable package — one loop, "
    "usable as a library or CLI — where Qwen is the working cortex and an optional "
    "Gemma 4 31B muse injects guidance and critique as a subconsciousness; configured "
    "identity makes it Gwen per colleague#352 (byte-identical prompts when absent), and "
    "an eidetic/coherence adapter makes its presence continuous across sessions with "
    "every degradation observable."
)

#: Where the frame's confirmed announcement claim lives, and what it says.
FRAME_PATH = ".devague/frames/gwen-loop-presence-continuity.json"
FRAME_CLAIM_ID = "c45"
FRAME_CLAIM_TEXT = (
    "embodiment ships colleague's bounded tool loop as a reusable stdlib-only package — "
    "one loop, usable as a library or CLI — where Qwen is the working cortex and an "
    "optional Gemma 4 31B muse injects guidance and critique as a subconsciousness; "
    "configured identity makes it Gwen per colleague#352 (byte-identical prompts when "
    "absent), and an eidetic/coherence subprocess adapter makes its presence continuous "
    "across sessions with every degradation observable"
)

#: How the shipped text got from there to here. Both are deviation d2.
DIVERGENCES = (
    Divergence(
        was="reusable stdlib-only package",
        now="reusable package",
        deviation="d2",
        why=(
            "eidetic-cli, coherence-cli and events-cli are base dependencies; the "
            "pure-stdlib core (constraint C1) was traded for direct imports, and "
            "tests/test_zero_deps.py became the human gate on the set"
        ),
    ),
    Divergence(
        was="eidetic/coherence subprocess adapter",
        now="eidetic/coherence adapter",
        deviation="d2",
        why="the planned subprocess boundary is gone — both CLIs are imported directly",
    ),
)


# ── the clauses ───────────────────────────────────────────────────────────────

CLAUSES: tuple[Clause, ...] = (
    Clause(
        id="a1",
        title="a reusable package, not a fork of colleague's loop",
        quote="embodiment ships colleague's bounded tool loop as a reusable package",
        evidence=(
            Evidence(
                "tests/test_loop.py::TestTerminationMatrix::"
                "test_every_scenario_exits_through_one_of_the_three",
                "bounded: every drive exits through one of exactly three reasons",
            ),
            Evidence(
                "tests/test_loop.py::TestTerminationMatrix::"
                "test_module_declares_exactly_three_exit_constants",
                "the three exits are structural, not a test's opinion",
            ),
            Evidence(
                "tests/test_loop.py::TestImportPosture::test_imports_no_colleague",
                "extracted, not re-imported: the loop stands on its own",
            ),
            Evidence(
                "tests/test_loop.py::TestImportPosture::"
                "test_module_scope_imports_are_stdlib_or_embodiment",
                "no colleague-policy module rode along in the extraction",
            ),
            Evidence(
                "tests/test_no_shell_host.py::TestNoShellHost::"
                "test_multi_step_domain_workflow_completes_normally",
                "reusable by a host with no shell tools at all (the no-shell fixture)",
            ),
            Evidence(
                "tests/test_no_shell_host.py::TestNoShellCoupling::"
                "test_no_module_references_shell_cli_in_code",
                "no shell-cli coupling smuggled in through an import",
            ),
            Evidence(
                "tests/test_no_shell_host.py::TestNoShellCoupling::"
                "test_the_guard_still_catches_a_real_import",
                "that guard is not vacuous — it catches a planted import",
            ),
            Evidence(
                "tests/test_zero_deps.py::test_declared_dependencies_match_the_approved_set",
                "the install footprint a consumer inherits is pinned and human-gated",
            ),
            Evidence(
                "tests/test_zero_deps.py::test_runtime_imports_match_the_approved_set",
                "what importing embodiment actually pulls is pinned too",
            ),
            Evidence(
                "tests/test_zero_deps.py::test_install_footprint_is_wider_than_import_footprint",
                "the difference between installing and importing is stated, not blurred",
            ),
        ),
        limit=(
            "'reusable' is proved against this repo's hosts. colleague itself cannot adopt "
            "the package until it relaxes its one-base-dependency rule (C1b) — see caveat t19."
        ),
    ),
    Clause(
        id="a2",
        title="one loop, reachable as a library or from a command line",
        quote="one loop, usable as a library or CLI",
        evidence=(
            Evidence(
                "tests/test_package_surface.py::TestResolutionIsCorrect::test_run_is_the_loop_run",
                "the library seam: embodiment.run IS the loop's run",
            ),
            Evidence(
                "tests/test_package_surface.py::TestEveryAdvertisedNameResolves::"
                "test_all_matches_dir",
                "one curated public surface, and every advertised name resolves",
            ),
            Evidence(
                "tests/test_package_surface.py::TestImportStaysLazy::"
                "test_bare_import_pulls_in_no_heavy_module",
                "importing the library costs a host nothing it did not ask for",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestPublicApiOnly::"
                "test_every_embodiment_name_is_on_the_curated_surface",
                "a command-line host drives the loop through the public surface only",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestOutputDiscipline::"
                "test_results_go_to_stdout_and_presence_to_stderr",
                "that host behaves like a CLI: results on stdout, presence on stderr",
            ),
            Evidence(
                "tests/test_cli.py::test_every_catalog_path_resolves",
                "embodiment's own console script is complete and agent-first",
            ),
        ),
        limit=(
            "the CLI half is a HOST cli (examples/greenhouse.py), not an embodiment verb: "
            "the shipped console script carries introspection only — see caveat cli1."
        ),
    ),
    Clause(
        id="a3",
        title="the cortex is the acting mind, and its model is the host's to name",
        quote="where Qwen is the working cortex",
        evidence=(
            Evidence(
                "tests/test_muse_runner.py::TestExplicitConfiguration::"
                "test_the_module_names_no_model_and_sniffs_no_name",
                "roles resolve by name from the host; no model name is ever parsed",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestExplicitConfiguration::"
                "test_the_role_is_a_name_the_host_supplies",
                "the cortex/muse split is configuration, not inference",
            ),
            Evidence(
                "tests/test_framing.py::TestResolutionIsTheSharedIdentitySeam::"
                "test_a_model_name_is_never_an_identity",
                "swapping model names changes no identity — colleague#352's rule",
            ),
            Evidence(
                "tests/test_framing.py::TestCortexFramingStaysOnTheTopLevelLoop::"
                "test_the_subagent_block_carries_no_cortex_framing",
                "only the top-level acting loop is the cortex; subagents are not",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestSelfDescribing::"
                "test_the_demo_names_no_model_it_is_not_running",
                "the demo never claims a model it did not actually call",
            ),
        ),
        limit=(
            "'Qwen' is a rig fact, not a code fact — no hermetic test can prove which model "
            "answered. The live pair is exercised only by tests/test_demo_greenhouse.py::"
            "TestLiveRig, which SKIPS without EMBODIMENT_LIVE_RIG=1 and COLLEAGUE_API_KEY. "
            "See caveat d4."
        ),
    ),
    Clause(
        id="a4",
        title="the muse is optional, advisory, and never in the acting path",
        quote=("an optional Gemma 4 31B muse injects guidance and critique as a subconsciousness"),
        evidence=(
            Evidence(
                "tests/test_muse_runner.py::TestMuselessIsTheDefault::"
                "test_a_museless_run_starts_no_thread_at_all",
                "the museless default fixture: no muse, no thread — never a stopped one",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestMuselessIsTheDefault::"
                "test_a_museless_run_still_feels_present",
                "presence does not depend on the second mind existing",
            ),
            Evidence(
                "tests/test_presence_engine.py::TestMuselessBeats::"
                "test_default_mode_is_cortex_only_and_not_a_degradation",
                "cortex-only is the default MODE, and is not itself a degradation",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestEngineIntegration::"
                "test_the_engine_renders_and_injects_a_drained_insight",
                "when configured, muse guidance does reach the operator-visible stream",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestAuthorityBoundary::"
                "test_no_muse_sourced_value_reaches_the_pre_tool_hook_registry",
                "advisory only: nothing muse-sourced can deny or rewrite a tool call",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestDegradation::"
                "test_a_dead_endpoint_degrades_and_stops_dialling",
                "a configured-but-dead muse degrades visibly instead of hanging the loop",
            ),
            Evidence(
                "tests/test_muse_runner.py::TestDegradation::"
                "test_a_mid_run_failure_degrades_visibly",
                "a muse that dies mid-run is recorded, not silently absent",
            ),
            Evidence(
                "tests/test_framing.py::TestAMuselessRunClaimsNoSecondMind::"
                "test_the_museless_cortex_block_mentions_none_of_it",
                "a single-model run never claims another mind exists",
            ),
        ),
        limit=(
            "the guards stop the muse SEIZING authority; whether a live cortex SURRENDERS to "
            "confidently wrong advice is the echo-chamber probe in caveat d4, still unrun."
        ),
    ),
    Clause(
        id="a5",
        title="Gwen only when configured — byte-identical prompts when absent",
        quote=(
            "configured identity makes it Gwen per colleague#352 "
            "(byte-identical prompts when absent)"
        ),
        evidence=(
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_the_loop_transcript_is_byte_identical",
                "the golden: an unconfigured run's whole transcript is unchanged",
            ),
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_the_loops_own_default_system_prompt_survives",
                "the loop's own default prompt survives byte for byte",
            ),
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_a_host_supplied_prompt_reaches_the_wire_unchanged",
                "a host's prompt is not touched on the way to the seam",
            ),
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_the_muse_system_message_is_byte_identical",
                "the muse lane is covered by the same golden",
            ),
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_the_presence_lines_are_byte_identical",
                "so is the presence pump's rendered text",
            ),
            Evidence(
                "tests/test_framing.py::TestPassThroughIsStructural::"
                "test_the_composer_hands_the_base_back_by_name",
                "pass-through is structural (an AST guard), not merely observed",
            ),
            Evidence(
                "tests/test_framing.py::TestCompositionDiffLeavesAuthorityUntouched::"
                "test_hook_events_and_approval_decisions_are_identical",
                "identity renames the speaker, never what it may do",
            ),
        ),
    ),
    Clause(
        id="a6",
        title="continuity across sessions, through eidetic and coherence",
        quote="an eidetic/coherence adapter makes its presence continuous across sessions",
        evidence=(
            Evidence(
                "tests/test_demo_greenhouse.py::TestContinuityAcrossTwoProcesses::"
                "test_second_process_recalls_and_acts_on_the_first",
                "two real interpreters over one store; run 2 acts on run 1's fact",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestContinuityAcrossTwoProcesses::"
                "test_the_control_experiment_an_empty_store_cannot_answer",
                "the control: with an empty store the same utterance fails honestly",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestContinuityAcrossTwoProcesses::"
                "test_the_store_is_a_real_file_the_second_process_reads",
                "continuity rides the filesystem, not a shared in-process object",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestContinuityAcrossTwoProcesses::"
                "test_provenance_survives_the_process_boundary",
                "the operator's words reach the durable record verbatim",
            ),
            Evidence(
                "tests/test_lifecycle.py::TestBeforeMemory::"
                "test_coherence_is_consulted_before_the_write",
                "coherence is consulted at the boundary, not sprinkled anywhere",
            ),
            Evidence(
                "tests/test_lifecycle.py::TestPermissionAndCoherenceCannotInfluenceEachOther::"
                "test_an_explicit_allow_is_not_second_guessed_by_coherence",
                "permission and coherence stay separate concerns",
            ),
        ),
        limit=(
            "'adapter' now means a direct import, not the planned subprocess boundary "
            "(deviation d2) — which is why a consumer inherits eidetic's and coherence's "
            "own dependency trees."
        ),
    ),
    Clause(
        id="a7",
        title="every degradation observable",
        quote="with every degradation observable",
        evidence=(
            Evidence(
                "tests/test_ledger.py::TestEveryCodeIsCovered::test_no_code_lacks_a_covering_path",
                "the enumeration is exhaustive BY CONSTRUCTION — derived from each lane",
            ),
            Evidence(
                "tests/test_ledger.py::TestEveryCodeIsCovered::"
                "test_a_newly_added_code_appears_uncovered_without_being_written_down",
                "a new degradation code cannot arrive unnoticed",
            ),
            Evidence(
                "tests/test_ledger.py::TestNeverRaisesIntoTheHost::"
                "test_junk_degrades_to_a_record_rather_than_an_exception",
                "the ledger never fails loudly into the host's main path",
            ),
            Evidence(
                "tests/test_no_silent_degradation.py::TestNoSilentSwallow::"
                "test_every_silent_swallow_is_sanctioned_by_name",
                "every silent except-pass in the package is named and justified",
            ),
            Evidence(
                "tests/test_no_silent_degradation.py::TestTheGuardItself::"
                "test_a_real_silent_swallow_is_caught",
                "that guard catches a planted silent swallow — it is not vacuous",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestObservableDegradation::"
                "test_coherence_without_an_embedder_degrades_visibly",
                "a real host sees the degradation, not just the library's tests",
            ),
        ),
    ),
)


# ── the three success signals ─────────────────────────────────────────────────

SIGNALS: tuple[Signal, ...] = (
    Signal(
        id="s1",
        text=(
            "embodiment's CI is green with the zero-deps guard, the byte-identical-prompt "
            "golden test and the degradation-record tests"
        ),
        kind=EXTERNAL,
        evidence=(
            Evidence(
                "tests/test_zero_deps.py::test_declared_dependencies_match_the_approved_set",
                "family 1 of 3 — the zero-deps guard CI runs",
            ),
            Evidence(
                "tests/test_framing.py::TestGoldenAgainstTheRealSeams::"
                "test_an_unconfigured_framing_object_changes_nothing_anywhere",
                "family 2 of 3 — the byte-identical-prompt golden CI runs",
            ),
            Evidence(
                "tests/test_ledger.py::TestEveryCodeIsCovered::"
                "test_the_derivation_reaches_every_lane",
                "family 3 of 3 — the degradation-record tests CI runs",
            ),
        ),
        probes=(
            Probe(
                id="ci-status",
                description="the Tests workflow's own conclusion on main (with the run's URL)",
                argv=(
                    "run",
                    "list",
                    "--repo",
                    "agentculture/embodiment",
                    "--workflow",
                    "Tests",
                    "--branch",
                    "main",
                    "--limit",
                    "1",
                    "--json",
                    "conclusion,url",
                    "--jq",
                    ".[0]",
                ),
                expect='"conclusion":"success"',
            ),
        ),
        why_external=(
            "the three families run here, but the CI JOB STATUS lives on GitHub Actions and "
            "no hermetic run can read it"
        ),
    ),
    Signal(
        id="s2",
        text="a minimal non-colleague demo app in-repo shows continuity across two interactions",
        kind=INTERNAL,
        evidence=(
            Evidence(
                "tests/test_demo_greenhouse.py::TestContinuityAcrossTwoProcesses::"
                "test_second_process_recalls_and_acts_on_the_first",
                "the demo, driven as two real processes over one store",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestPublicApiOnly::"
                "test_nothing_colleague_shaped_appears_as_code",
                "and it is genuinely non-colleague",
            ),
            Evidence(
                "tests/test_demo_greenhouse.py::TestReadmeTeachesTheDemo::"
                "test_every_readme_command_parses_with_the_demo_s_own_parser",
                "an app author can follow it from the README alone",
            ),
        ),
    ),
    Signal(
        id="s3",
        text=(
            "the seam proposal is filed on agentculture/colleague and colleague#352 carries "
            "the recorded framing divergence"
        ),
        kind=EXTERNAL,
        probes=(
            Probe(
                id="divergence-comment",
                description="colleague#352 comment 5073964358, signed by this agent",
                argv=(
                    "api",
                    "repos/agentculture/colleague/issues/comments/5073964358",
                    "--jq",
                    ".body",
                ),
                expect="- embodiment (Claude)",
            ),
            Probe(
                id="seam-proposal",
                description="colleague#358, the filed seam proposal",
                argv=("api", "repos/agentculture/colleague/issues/358", "--jq", ".title"),
                expect="embodiment",
            ),
            Probe(
                id="seam-proposal-search",
                description="any other seam-proposal issue on agentculture/colleague",
                argv=(
                    "issue",
                    "list",
                    "--repo",
                    "agentculture/colleague",
                    "--state",
                    "all",
                    "--search",
                    "embodiment in:title",
                    "--json",
                    "number,title,url",
                ),
                expect=None,
            ),
        ),
        why_external=(
            "both links live on github.com; nothing in a hermetic run can prove an issue "
            "exists, and neither link says whether colleague has ANSWERED the proposal"
        ),
    ),
)


# ── caveat probes ─────────────────────────────────────────────────────────────


def _read(root: Path, relative: str) -> Optional[str]:
    """Read a repo file, or ``None`` when it is not there."""
    path = root / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _d4_still_outstanding(root: Path) -> Optional[str]:
    """Deviation d4 stands while its own record still says ``needs-follow-up``."""
    raw = _read(root, ".devague/deliveries/gwen-loop-presence-continuity.json")
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    for deviation in record.get("deviations", []):
        if deviation.get("id") != "d4":
            continue
        if deviation.get("classification") != "needs-follow-up":
            return (
                "deviation d4 is no longer classified needs-follow-up "
                f"({deviation.get('classification')!r}) — has the live bar been met?"
            )
        return None
    return "deviation d4 is no longer in the delivery record — re-read it"


def _record_id_for_still_unhoisted(root: Path) -> Optional[str]:
    """The rough edge stands while ``record_id_for`` is not on the package root."""
    source = _read(root, "embodiment/__init__.py")
    if source is None:
        return None
    if "record_id_for" in source:
        return "record_id_for now appears in embodiment/__init__.py — the rough edge is fixed"
    return None


def _repo_path_still_required(root: Path) -> Optional[str]:
    """The rough edge stands while ``Task.repo_path`` has no default."""
    source = _read(root, "embodiment/contract.py")
    if source is None:
        return None
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or node.name != "Task":
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "repo_path"
            ):
                if statement.value is not None:
                    return "Task.repo_path now has a default — the rough edge is fixed"
                return None
        return "Task no longer declares repo_path — the rough edge is fixed"
    return None


def _snapshot_still_returns_dataclasses(root: Path) -> Optional[str]:
    """The rough edge stands while ``snapshot()['degradations']`` hands back raw records.

    Scoped to the ``degradations`` VALUE, not to the whole method. The first
    spelling asked whether ``to_dict`` appeared anywhere inside ``snapshot`` at
    all, and so declared the edge fixed the moment an unrelated key started
    serialising itself — which task t5's ``deliveries`` key promptly did, while
    ``degradations`` went on handing back dataclasses and the caveat went on
    being true. A false STALE is the one failure mode a staleness probe must not
    have: it retires a caveat that still stands, which is how an honest list
    turns into a stale one.
    """
    source = _read(root, "embodiment/muse_runner.py")
    if source is None:
        return None
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == "snapshot"):
            continue
        values = _dict_values_for(node, "degradations")
        if not values:
            return "muse_runner.snapshot() no longer reports degradations — re-read this caveat"
        if any("to_dict" in ast.unparse(value) for value in values):
            return "muse_runner.snapshot()['degradations'] now serialises — the edge is fixed"
        return None
    return "muse_runner no longer defines snapshot() — re-read this caveat"


def _dict_values_for(node: ast.AST, key: str) -> list[ast.expr]:
    """Every dict-literal value bound to *key* anywhere inside *node*."""
    found: list[ast.expr] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Dict):
            continue
        for literal, value in zip(child.keys, child.values):
            if isinstance(literal, ast.Constant) and literal.value == key:
                found.append(value)
    return found


#: The introspection verbs the console script ships. None of them drives the loop.
_CLI_VERBS = frozenset({"cli", "doctor", "explain", "learn", "overview", "whoami"})


def _cli_still_introspection_only(root: Path) -> Optional[str]:
    """The caveat stands while the CLI ships exactly the introspection verbs."""
    commands = root / "embodiment" / "cli" / "_commands"
    if not commands.is_dir():
        return None
    verbs = {path.stem for path in commands.glob("*.py") if not path.stem.startswith("__")}
    if verbs != _CLI_VERBS:
        return f"the CLI's verbs changed to {sorted(verbs)} — does one drive the loop now?"
    return None


# ── the caveats ───────────────────────────────────────────────────────────────

CAVEATS: tuple[Caveat, ...] = (
    Caveat(
        id="d4",
        title="live testing is a recorded acceptance bar that is NOT met",
        detail=(
            "the demo has been run against the real rig, but the full bar is outstanding: a "
            "museless baseline run against the live cortex, a two-mind run with the Gemma 31B "
            "muse, and an echo-chamber probe that feeds the muse confidently wrong advice and "
            "checks whether the cortex follows it. Today's guards stop the muse SEIZING "
            "authority, not the cortex SURRENDERING it. tests/test_demo_greenhouse.py::"
            "TestLiveRig skips unless EMBODIMENT_LIVE_RIG=1 and COLLEAGUE_API_KEY are set."
        ),
        state="recorded, not met (classification: needs-follow-up)",
        check=_d4_still_outstanding,
    ),
    Caveat(
        id="api1",
        title="lifecycle.record_id_for is not hoisted to the package root",
        detail=(
            "a host reading back what it remembered must import embodiment.lifecycle rather "
            "than reach embodiment.record_id_for — found while writing the demo"
        ),
        state="open rough edge in the public API",
        check=_record_id_for_still_unhoisted,
    ),
    Caveat(
        id="api2",
        title="Task.repo_path is required and colleague-flavoured",
        detail=(
            "a non-code host (the greenhouse demo has no repository at all) must still supply "
            "Task.repo_path — a required field named for the extraction's origin"
        ),
        state="open rough edge in the public API",
        check=_repo_path_still_required,
    ),
    Caveat(
        id="api3",
        title="ThreadedMuseRunner.snapshot()['degradations'] returns dataclasses",
        detail=(
            "sibling ledgers hand a host dicts; the muse runner's snapshot hands back records, "
            "so a host that json-dumps one ledger cannot json-dump the other"
        ),
        state="open rough edge in the public API",
        check=_snapshot_still_returns_dataclasses,
    ),
    Caveat(
        id="cli1",
        title="the shipped console script carries no loop-driving verb",
        detail=(
            "embodiment's own CLI is introspection only (whoami / learn / explain / overview / "
            "doctor / cli). 'usable as a library or CLI' means an app builds its CLI on the "
            "library — examples/greenhouse.py is that host — not that embodiment ships a verb "
            "that runs your loop"
        ),
        state="a boundary of clause a2, stated rather than implied",
        check=_cli_still_introspection_only,
    ),
    Caveat(
        id="t19",
        title="the seam proposal is filed, and the decision it asks for is not made",
        detail=(
            "colleague#358 proposes importing the extracted loop; adoption is blocked on C1b "
            "— may colleague hold three base dependencies, or must embodiment compose them? "
            "Deviation d2 turned that from an open question inside the proposal into a "
            "prerequisite of it, and the answer is colleague's alone. Nothing in a hermetic "
            "run can see either the issue or its outcome: probe it with --online."
        ),
        state="filed; the decision is outstanding and outside this repo's control",
    ),
)


# ── resolution ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolution:
    """What became of one citation."""

    node_id: str
    status: str
    detail: str = ""
    proves: str = ""


def resolve(node_id: str, root: Path = REPO_ROOT) -> Resolution:
    """Resolve a pytest node id against the source, without importing anything.

    ``file::function`` and ``file::Class::method`` are the two supported shapes;
    anything else is :data:`MISSING` rather than an exception, because a
    checklist that crashes teaches nobody anything.
    """
    parts = node_id.split("::")
    if len(parts) not in (2, 3):
        return Resolution(node_id, MISSING, f"not a test node id: {node_id!r}")

    path = root / parts[0]
    if not path.is_file():
        return Resolution(node_id, MISSING, f"no such file: {parts[0]}")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - a broken test file fails louder elsewhere
        return Resolution(node_id, MISSING, f"{parts[0]} does not parse: {exc}")

    if len(parts) == 2:
        if _find_function(tree.body, parts[1]) is None:
            return Resolution(node_id, MISSING, f"{parts[0]} has no test function {parts[1]}")
        return Resolution(node_id, RESOLVED)

    holder = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parts[1]), None
    )
    if holder is None:
        return Resolution(node_id, MISSING, f"{parts[0]} has no class {parts[1]}")
    if _find_function(holder.body, parts[2]) is None:
        return Resolution(node_id, MISSING, f"{parts[1]} has no test method {parts[2]}")
    return Resolution(node_id, RESOLVED)


def _find_function(body: Sequence[ast.stmt], name: str) -> Optional[ast.stmt]:
    """Find a ``def``/``async def`` named ``name`` that pytest would collect."""
    if not name.startswith("test"):
        return None
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def is_separator(text: str) -> bool:
    """True when ``text`` is only punctuation joining two announcement clauses."""
    return text.strip(" \t\n—-–,;:.()").strip() in ("", "and", "or")


# ── results ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClauseResult:
    clause: Clause
    status: str
    evidence: tuple[Resolution, ...]


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    status: str
    detail: str = ""


@dataclass(frozen=True)
class SignalResult:
    signal: Signal
    status: str
    evidence: tuple[Resolution, ...] = ()
    probes: tuple[ProbeResult, ...] = ()


@dataclass(frozen=True)
class CaveatResult:
    caveat: Caveat
    stale_reason: Optional[str] = None


@dataclass(frozen=True)
class ProvenanceResult:
    status: str
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """Everything the checklist found, in one renderable object."""

    root: Path
    ran: bool
    online: bool
    provenance: ProvenanceResult
    clauses: tuple[ClauseResult, ...]
    signals: tuple[SignalResult, ...]
    caveats: tuple[CaveatResult, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    run_requested: bool = False
    """``--run`` was asked for. It differs from ``ran`` when execution was
    refused because the manifest had already drifted."""

    @property
    def ok(self) -> bool:
        """True when nothing cited is missing, failed, or drifted."""
        if self.provenance.status in _RED:
            return False
        if any(result.stale_reason for result in self.caveats):
            return False
        for holder in (*self.clauses, *self.signals):
            if holder.status in _RED:
                return False
            if any(item.status in _RED for item in holder.evidence):
                return False
        return True

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    # ── rendering ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """The same report, machine-readable."""
        return {
            "announcement": ANNOUNCEMENT,
            "mode": "run" if self.ran else "resolve",
            "run_requested": self.run_requested,
            "online": self.online,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "provenance": {
                "frame": FRAME_PATH,
                "claim": FRAME_CLAIM_ID,
                "status": self.provenance.status,
                "detail": self.provenance.detail,
                "divergences": [
                    {
                        "was": d.was,
                        "now": d.now,
                        "deviation": d.deviation,
                        "why": d.why,
                    }
                    for d in DIVERGENCES
                ],
            },
            "clauses": [
                {
                    "id": result.clause.id,
                    "title": result.clause.title,
                    "quote": result.clause.quote,
                    "status": result.status,
                    "limit": result.clause.limit,
                    "evidence": [_evidence_dict(item) for item in result.evidence],
                }
                for result in self.clauses
            ],
            "signals": [
                {
                    "id": result.signal.id,
                    "text": result.signal.text,
                    "kind": result.signal.kind,
                    "status": result.status,
                    "why_external": result.signal.why_external,
                    "evidence": [_evidence_dict(item) for item in result.evidence],
                    "probes": [
                        {
                            "id": probe.probe.id,
                            "description": probe.probe.description,
                            "command": _gh_command(probe.probe),
                            "status": probe.status,
                            "detail": probe.detail,
                        }
                        for probe in result.probes
                    ],
                }
                for result in self.signals
            ],
            "caveats": [
                {
                    "id": result.caveat.id,
                    "title": result.caveat.title,
                    "state": result.caveat.state,
                    "detail": result.caveat.detail,
                    "stale_reason": result.stale_reason,
                }
                for result in self.caveats
            ],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        """The human-readable checklist."""
        rule = "─" * 78
        lines: list[str] = [
            "embodiment — announcement checklist",
            f"repo: {self.root}",
            f"mode: {'run' if self.ran else 'resolve'}"
            f"{' + online probes' if self.online else ''}",
            rule,
            "",
            "the announcement, as shipped:",
            f"  {ANNOUNCEMENT}",
            "",
            *self._provenance_lines(),
            "",
            "clauses",
            rule,
        ]
        for result in self.clauses:
            lines.extend(self._clause_lines(result))
        lines.extend(["", "success signals", rule])
        for signal in self.signals:
            lines.extend(self._signal_lines(signal))
        lines.extend(["", "caveats — outstanding, reported here rather than fixed", rule])
        for caveat in self.caveats:
            lines.extend(self._caveat_lines(caveat))
        lines.extend(["", "verdict", rule, *self._verdict_lines()])
        return "\n".join(lines)

    # ── rendering helpers ────────────────────────────────────────────────────

    def _provenance_lines(self) -> list[str]:
        lines = [
            "provenance — the frame's confirmed claim vs the shipped text:",
            f"  [{_SYMBOL[self.provenance.status]}] {FRAME_PATH} {FRAME_CLAIM_ID}"
            f"{': ' + self.provenance.detail if self.provenance.detail else ''}",
        ]
        for divergence in DIVERGENCES:
            lines.append(
                f"    - {FRAME_CLAIM_ID} said {divergence.was!r}, the shipped text says "
                f"{divergence.now!r} — deviation {divergence.deviation}"
            )
            lines.append(f"      {divergence.why}")
        return lines

    def _clause_lines(self, result: ClauseResult) -> list[str]:
        counted = sum(1 for item in result.evidence if item.status not in _RED)
        lines = [
            "",
            f"  {result.clause.id}  {result.clause.title}"
            f"   [{_SYMBOL[result.status]}] {counted}/{len(result.evidence)}",
            f'      "{result.clause.quote}"',
        ]
        for item in result.evidence:
            lines.extend(_evidence_lines(item))
        if result.clause.limit:
            lines.append(f"      limit: {result.clause.limit}")
        return lines

    def _signal_lines(self, result: SignalResult) -> list[str]:
        lines = [
            "",
            f"  {result.signal.id}  [{_SYMBOL[result.status]}] {result.signal.text}",
        ]
        if result.signal.kind == EXTERNAL:
            lines.append(f"      external because: {result.signal.why_external}")
        for item in result.evidence:
            lines.extend(_evidence_lines(item))
        for probe in result.probes:
            lines.append(f"      [{_SYMBOL[probe.status]}] {probe.probe.description}")
            lines.append(f"          $ {_gh_command(probe.probe)}")
            if probe.detail:
                lines.append(f"          {probe.detail}")
        return lines

    def _caveat_lines(self, result: CaveatResult) -> list[str]:
        lines = [
            "",
            f"  {result.caveat.id}  {result.caveat.title}",
            f"      state: {result.caveat.state}",
            f"      {result.caveat.detail}",
        ]
        if result.stale_reason:
            lines.append(f"      [STALE] {result.stale_reason}")
        return lines

    def _verdict_lines(self) -> list[str]:
        total = len(self.clauses)
        good = sum(1 for result in self.clauses if result.status not in _RED)
        cited = sum(len(result.evidence) for result in (*self.clauses, *self.signals))
        external = sum(1 for result in self.signals if result.signal.kind == EXTERNAL)
        probes = sum(len(result.probes) for result in self.signals)
        if self.ran:
            head = f"  {good}/{total} announcement clauses are backed by tests that ran here."
        else:
            head = (
                f"  {good}/{total} announcement clauses have backing that exists — "
                "nothing was executed."
            )
        lines = [head]
        if not self.ran and not self.run_requested:
            lines.append(f"  re-run with --run to execute the {cited} cited tests.")
        lines.append(
            f"  {external} success signal(s) and {probes} link(s) are external: "
            f"{'probed with gh above' if self.online else 'NOT verified in this run'}."
        )
        lines.append(f"  {len(self.caveats)} caveat(s) stand, listed above.")
        lines.append("  this report states what was checked here — never that the work is done.")
        for note in self.notes:
            lines.append(f"  note: {note}")
        lines.append(f"  exit {self.exit_code}")
        return lines


def _evidence_dict(item: Resolution) -> dict[str, str]:
    return {
        "node_id": item.node_id,
        "status": item.status,
        "proves": item.proves,
        "detail": item.detail,
    }


def _evidence_lines(item: Resolution) -> list[str]:
    lines = [f"      [{_SYMBOL[item.status]}] {item.node_id}"]
    if item.proves:
        lines.append(f"          {item.proves}")
    if item.detail:
        lines.append(f"          {item.detail}")
    return lines


def _gh_command(probe: Probe) -> str:
    return "gh " + " ".join(_quote(arg) for arg in probe.argv)


def _quote(argument: str) -> str:
    return f'"{argument}"' if " " in argument else argument


# ── the runners ───────────────────────────────────────────────────────────────

PytestRunner = Callable[[Sequence[str]], int]
GhRunner = Callable[[Sequence[str]], "tuple[int, str]"]


def make_pytest_runner(root: Path) -> PytestRunner:
    """A runner that executes node ids in a child pytest rooted at ``root``."""

    def runner(node_ids: Sequence[str]) -> int:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode

    return runner


def default_gh_runner(argv: Sequence[str]) -> tuple[int, str]:
    """Run a ``gh`` query. Never raises: a missing CLI is an exit code."""
    try:
        completed = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["gh", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return completed.returncode, (completed.stdout or completed.stderr or "")


# ── the check ─────────────────────────────────────────────────────────────────


def check(
    root: Path = REPO_ROOT,
    run: bool = False,
    online: bool = False,
    pytest_runner: Optional[PytestRunner] = None,
    gh_runner: Optional[GhRunner] = None,
) -> Report:
    """Resolve every citation, optionally execute it, and report honestly."""
    clause_evidence = {
        clause.id: tuple(_resolved(item, root) for item in clause.evidence) for clause in CLAUSES
    }
    signal_evidence = {
        signal.id: tuple(_resolved(item, root) for item in signal.evidence) for signal in SIGNALS
    }

    fully_resolved = all(
        item.status == RESOLVED
        for group in (*clause_evidence.values(), *signal_evidence.values())
        for item in group
    )
    notes: list[str] = []
    ran = False

    if run and not fully_resolved:
        notes.append(
            "nothing was executed: a citation does not resolve, and running a manifest "
            "that has already drifted would report a comforting half-truth"
        )
    elif run:
        runner = pytest_runner or make_pytest_runner(root)
        ran = True
        clause_evidence = {key: _execute(group, runner) for key, group in clause_evidence.items()}
        signal_evidence = {key: _execute(group, runner) for key, group in signal_evidence.items()}

    clauses = tuple(
        ClauseResult(clause, _worst(clause_evidence[clause.id]), clause_evidence[clause.id])
        for clause in CLAUSES
    )

    signals: list[SignalResult] = []
    for signal in SIGNALS:
        evidence = signal_evidence[signal.id]
        probes = tuple(
            _probe(probe, online, gh_runner or default_gh_runner) for probe in signal.probes
        )
        signals.append(
            SignalResult(signal, _signal_status(signal, evidence, probes, online), evidence, probes)
        )

    caveats = tuple(CaveatResult(caveat, caveat.check(root)) for caveat in CAVEATS)

    return Report(
        root=root,
        ran=ran,
        online=online,
        provenance=_provenance(root),
        clauses=clauses,
        signals=tuple(signals),
        caveats=caveats,
        notes=tuple(notes),
        run_requested=run,
    )


def _resolved(evidence: Evidence, root: Path) -> Resolution:
    resolution = resolve(evidence.node_id, root=root)
    return Resolution(resolution.node_id, resolution.status, resolution.detail, evidence.proves)


def _execute(group: tuple[Resolution, ...], runner: PytestRunner) -> tuple[Resolution, ...]:
    """Run a group in one pytest; on failure, re-run singly to name the culprit."""
    if not group:
        return group
    node_ids = [item.node_id for item in group]
    if runner(node_ids) == 0:
        return tuple(
            Resolution(item.node_id, VERIFIED, "passed here", item.proves) for item in group
        )
    resolutions: list[Resolution] = []
    for item in group:
        code = runner([item.node_id])
        status = VERIFIED if code == 0 else FAILED
        detail = "passed here" if code == 0 else f"FAILED here (pytest exit {code})"
        resolutions.append(Resolution(item.node_id, status, detail, item.proves))
    return tuple(resolutions)


def _worst(group: Sequence[Resolution]) -> str:
    """A clause is as good as its weakest citation."""
    statuses = {item.status for item in group}
    for status in (MISSING, FAILED, VERIFIED):
        if status in statuses:
            return status
    return RESOLVED


def _probe(probe: Probe, online: bool, runner: GhRunner) -> ProbeResult:
    """Run one ``gh`` probe, or report it as external when offline."""
    if not online:
        return ProbeResult(probe, EXTERNAL, "not attempted — pass --online to probe GitHub")
    code, output = runner(probe.argv)
    if code != 0:
        return ProbeResult(probe, UNAVAILABLE, f"gh exit {code}: {output.strip()[:200]}")
    text = output.strip()
    if text in probe.absent:
        return ProbeResult(probe, OUTSTANDING, "gh answered, and found nothing")
    if probe.expect is None:
        return ProbeResult(probe, REPORTED, f"a human must read this: {text[:200]}")
    if probe.expect in text:
        return ProbeResult(probe, VERIFIED, f"found {probe.expect!r}")
    return ProbeResult(probe, OUTSTANDING, f"{probe.expect!r} not found in: {text[:200]}")


def _signal_status(
    signal: Signal,
    evidence: Sequence[Resolution],
    probes: Sequence[ProbeResult],
    online: bool,
) -> str:
    if signal.kind == EXTERNAL and not online:
        return EXTERNAL
    if signal.kind == EXTERNAL:
        statuses = [probe.status for probe in probes]
        for status in (UNAVAILABLE, OUTSTANDING, REPORTED):
            if status in statuses:
                return status
        return VERIFIED if statuses else EXTERNAL
    return _worst(evidence)


def _provenance(root: Path) -> ProvenanceResult:
    """Check the frame still says what this checklist quotes it as saying."""
    raw = _read(root, FRAME_PATH)
    if raw is None:
        return ProvenanceResult(UNAVAILABLE, f"{FRAME_PATH} is not in this checkout")
    try:
        frame = json.loads(raw)
    except ValueError as exc:
        return ProvenanceResult(UNAVAILABLE, f"{FRAME_PATH} does not parse: {exc}")
    claim = next((c for c in frame.get("claims", []) if c.get("id") == FRAME_CLAIM_ID), None)
    if claim is None:
        return ProvenanceResult(DRIFTED, f"{FRAME_CLAIM_ID} is no longer in the frame")
    if " ".join(claim.get("text", "").split()) != " ".join(FRAME_CLAIM_TEXT.split()):
        return ProvenanceResult(
            DRIFTED,
            f"{FRAME_CLAIM_ID} has been reworded since this checklist quoted it — "
            "re-read the frame and update the divergences below",
        )
    return ProvenanceResult(RESOLVED, "still reads as quoted; the shipped text differs as below")


# ── the command line ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.announcement_checklist",
        description=(
            "Verify each clause of embodiment's announcement against the test that backs it. "
            "Hermetic by default; nothing external is ever counted green."
        ),
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute the cited tests in a child pytest instead of only resolving them",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="also probe GitHub through `gh` for the CI status and the two issue links",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as one JSON object")
    parser.add_argument(
        "--root", default=str(REPO_ROOT), help="repository root to check (default: this one)"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Render the checklist and return its exit code."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "pyproject.toml").is_file():
        print(f"error: {root} is not an embodiment checkout", file=sys.stderr)
        print("hint: pass --root <path-to-repo>", file=sys.stderr)
        return 2
    report = check(root=root, run=args.run, online=args.online)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
