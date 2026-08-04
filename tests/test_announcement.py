"""Tests for the announcement checklist (task t20).

The checklist itself lives in :mod:`tests.announcement_checklist` and is meant
to be *run* — by a human before a release, and by this suite in CI. These tests
guard the three ways a checklist rots:

**1. It drifts away from its evidence.** Every clause cites pytest node ids.
:class:`TestEveryCitedTestExists` resolves each one against the real test files
through the checklist's own resolver, so renaming or deleting a backing test
turns the clause red instead of quietly leaving it unbacked. The resolver's own
negative controls live in :class:`TestTheResolverIsNotVacuous` — a guard that
cannot fail is worth nothing.

**2. It drifts away from the announcement.** The clause quotes must *partition*
the announcement text: consecutive, gap-free apart from punctuation. A sentence
added to the announcement with no clause behind it fails the build.

**3. It claims more than was run.** The GitHub links and the live-rig bar are
not checkable in a hermetic run. They are reported as ``external`` and never
counted green, and the caveats — deviation ``d4`` above all — are rendered every
time. :class:`TestNoCaveatIsStale` goes further: each caveat carries a probe, so
a caveat that has quietly become untrue fails rather than lingering as prose.

Hermetic throughout: no pytest subprocess, no ``gh``, no socket. The runners are
injected, and :class:`TestHermetic` proves the offline path spawns nothing.
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - referenced only to bomb it in the hermetic test
import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

from tests import announcement_checklist as checklist

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "tests" / "announcement_checklist.py"

#: Every node id the manifest cites, deduped, in declaration order.
ALL_NODE_IDS = tuple(
    dict.fromkeys(
        evidence.node_id
        for holder in (*checklist.CLAUSES, *checklist.SIGNALS)
        for evidence in holder.evidence
    )
)


def _green(_node_ids: Sequence[str]) -> int:
    """A pytest runner that passes everything."""
    return 0


def _offline_gh(_argv: Sequence[str]) -> tuple[int, str]:
    """A ``gh`` runner standing in for a machine with no CLI installed."""
    return 127, ""


# ── 1. the manifest covers the announcement ───────────────────────────────────


class TestTheClausesPartitionTheAnnouncement:
    """Nothing in the announcement may go unclaimed."""

    def test_the_quotes_are_consecutive_and_gap_free(self) -> None:
        text = checklist.ANNOUNCEMENT
        cursor = 0
        for clause in checklist.CLAUSES:
            index = text.find(clause.quote, cursor)
            assert index >= 0, f"{clause.id}: quote is not in the announcement (or is out of order)"
            gap = text[cursor:index]
            assert checklist.is_separator(gap), f"{clause.id}: unclaimed announcement text {gap!r}"
            cursor = index + len(clause.quote)
        assert checklist.is_separator(text[cursor:]), f"unclaimed tail {text[cursor:]!r}"

    def test_every_clause_cites_evidence(self) -> None:
        for clause in checklist.CLAUSES:
            assert clause.evidence, f"{clause.id} claims a clause with nothing behind it"

    def test_every_piece_of_evidence_says_what_it_proves(self) -> None:
        for holder in (*checklist.CLAUSES, *checklist.SIGNALS):
            for evidence in holder.evidence:
                assert evidence.proves.strip(), f"{evidence.node_id} is cited without a reason"

    def test_clause_ids_are_unique(self) -> None:
        ids = [clause.id for clause in checklist.CLAUSES]
        assert len(ids) == len(set(ids))

    def test_the_acceptance_families_are_all_cited(self) -> None:
        """t20's acceptance names six families by file — every one must appear."""
        files = {node_id.split("::")[0] for node_id in ALL_NODE_IDS}
        for required in (
            "tests/test_zero_deps.py",
            "tests/test_framing.py",
            "tests/test_muse_runner.py",
            "tests/test_no_shell_host.py",
            "tests/test_ledger.py",
            "tests/test_demo_greenhouse.py",
        ):
            assert required in files, f"{required} is an acceptance family and is not cited"


# ── 2. the drift guard: every cited test still exists ─────────────────────────


class TestEveryCitedTestExists:
    """The anti-drift guard. Rename a backing test and this goes red."""

    @pytest.mark.parametrize("node_id", ALL_NODE_IDS)
    def test_the_node_id_resolves(self, node_id: str) -> None:
        resolution = checklist.resolve(node_id, root=REPO_ROOT)
        assert resolution.status == checklist.RESOLVED, resolution.detail

    def test_the_checklist_agrees(self) -> None:
        report = checklist.check(root=REPO_ROOT)
        assert report.ok, report.render()
        assert report.exit_code == 0

    def test_there_is_evidence_to_check(self) -> None:
        """A manifest that emptied itself would pass everything above."""
        assert len(ALL_NODE_IDS) >= 30


class TestTheResolverIsNotVacuous:
    """Negative controls — each way a citation can rot is actually caught."""

    def test_a_renamed_test_is_missing(self) -> None:
        resolution = checklist.resolve(
            "tests/test_loop.py::TestTerminationMatrix::test_renamed_away", root=REPO_ROOT
        )
        assert resolution.status == checklist.MISSING
        assert "test_renamed_away" in resolution.detail

    def test_a_renamed_class_is_missing(self) -> None:
        resolution = checklist.resolve(
            "tests/test_loop.py::TestGoneAway::test_model_finish", root=REPO_ROOT
        )
        assert resolution.status == checklist.MISSING
        assert "TestGoneAway" in resolution.detail

    def test_a_deleted_file_is_missing(self) -> None:
        resolution = checklist.resolve("tests/test_deleted.py::test_x", root=REPO_ROOT)
        assert resolution.status == checklist.MISSING
        assert "no such file" in resolution.detail

    def test_a_module_level_test_resolves(self) -> None:
        resolution = checklist.resolve("tests/test_cli.py::test_whoami_json", root=REPO_ROOT)
        assert resolution.status == checklist.RESOLVED

    def test_a_malformed_node_id_is_missing_not_an_exception(self) -> None:
        resolution = checklist.resolve("tests/test_cli.py", root=REPO_ROOT)
        assert resolution.status == checklist.MISSING

    def test_a_class_is_not_mistaken_for_a_test_function(self) -> None:
        """``Scripted`` is a helper class in test_loop.py, not a test."""
        resolution = checklist.resolve("tests/test_loop.py::Scripted", root=REPO_ROOT)
        assert resolution.status == checklist.MISSING

    def test_a_lost_citation_turns_its_clause_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = checklist.Clause(
            id="x1",
            title="a clause whose test was renamed",
            quote="embodiment",
            evidence=(checklist.Evidence("tests/test_loop.py::test_vanished", "nothing, now"),),
        )
        monkeypatch.setattr(checklist, "CLAUSES", (broken,))
        report = checklist.check(root=REPO_ROOT)

        assert not report.ok
        assert report.exit_code == 1
        assert report.clauses[0].status == checklist.MISSING
        rendered = report.render()
        # Legible drift: the report names the citation that rotted.
        assert "tests/test_loop.py::test_vanished" in rendered
        assert "MISSING" in rendered


# ── 3. running the evidence ───────────────────────────────────────────────────


class TestRunningTheEvidence:
    """``--run`` executes the backing tests; the verdict is theirs, not ours."""

    def test_a_green_run_marks_every_clause_verified(self) -> None:
        report = checklist.check(root=REPO_ROOT, run=True, pytest_runner=_green)
        assert report.ok
        assert report.exit_code == 0
        assert all(result.status == checklist.VERIFIED for result in report.clauses)
        assert "PASS" in report.render()

    def test_a_red_run_fails_and_pinpoints_the_test(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(node_ids: Sequence[str]) -> int:
            calls.append(tuple(node_ids))
            return 1

        report = checklist.check(root=REPO_ROOT, run=True, pytest_runner=runner)
        assert not report.ok
        assert report.exit_code == 1
        assert all(result.status == checklist.FAILED for result in report.clauses)
        # A failing clause is re-run one node at a time so the report can say
        # WHICH test failed, not just which clause.
        assert any(len(call) == 1 for call in calls)
        assert "FAIL" in report.render()

    def test_the_runner_receives_the_cited_node_ids(self) -> None:
        seen: list[tuple[str, ...]] = []

        def runner(node_ids: Sequence[str]) -> int:
            seen.append(tuple(node_ids))
            return 0

        checklist.check(root=REPO_ROOT, run=True, pytest_runner=runner)
        handed = {node_id for call in seen for node_id in call}
        assert handed == set(ALL_NODE_IDS)

    def test_a_missing_test_is_never_handed_to_pytest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resolution comes first: pytest is not asked about a test that is gone."""
        broken = checklist.Clause(
            id="x1",
            title="gone",
            quote="embodiment",
            evidence=(checklist.Evidence("tests/test_loop.py::test_vanished", "nothing"),),
        )
        monkeypatch.setattr(checklist, "CLAUSES", (broken,))
        calls: list[tuple[str, ...]] = []

        def runner(node_ids: Sequence[str]) -> int:
            calls.append(tuple(node_ids))
            return 0

        report = checklist.check(root=REPO_ROOT, run=True, pytest_runner=runner)
        assert calls == []
        assert report.clauses[0].status == checklist.MISSING

    def test_a_refused_run_says_why_instead_of_advertising_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = checklist.Clause(
            id="x1",
            title="gone",
            quote="embodiment",
            evidence=(checklist.Evidence("tests/test_loop.py::test_vanished", "nothing"),),
        )
        monkeypatch.setattr(checklist, "CLAUSES", (broken,))
        report = checklist.check(root=REPO_ROOT, run=True, pytest_runner=_green)

        assert report.run_requested
        assert not report.ran
        rendered = report.render()
        assert "re-run with --run" not in rendered
        assert "nothing was executed:" in rendered

    def test_resolve_mode_runs_nothing(self) -> None:
        def bomb(_node_ids: Sequence[str]) -> int:  # pragma: no cover - must not run
            raise AssertionError("resolve mode must not execute tests")

        report = checklist.check(root=REPO_ROOT, pytest_runner=bomb)
        assert report.ok
        assert all(result.status == checklist.RESOLVED for result in report.clauses)


# ── 4. externals are reported, never counted green ────────────────────────────


class TestExternalsAreNotCountedGreen:
    """The two GitHub links and the CI status cannot be proved offline."""

    def test_every_external_signal_is_marked_external_offline(self) -> None:
        report = checklist.check(root=REPO_ROOT)
        external = [s for s in report.signals if s.signal.kind == checklist.EXTERNAL]
        assert external, "the manifest must carry the external success signals"
        for result in external:
            assert result.status == checklist.EXTERNAL
            for probe in result.probes:
                assert probe.status == checklist.EXTERNAL

    def test_the_offline_report_prints_the_command_a_human_can_run(self) -> None:
        rendered = checklist.check(root=REPO_ROOT).render()
        assert "gh " in rendered

    def test_externals_never_change_the_exit_code(self) -> None:
        offline = checklist.check(root=REPO_ROOT)
        online = checklist.check(root=REPO_ROOT, online=True, gh_runner=_offline_gh)
        assert offline.exit_code == online.exit_code == 0

    def test_an_absent_gh_reports_unavailable_not_success(self) -> None:
        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=_offline_gh)
        probes = [p for s in report.signals for p in s.probes]
        assert probes
        assert all(p.status == checklist.UNAVAILABLE for p in probes)
        assert checklist.VERIFIED not in {p.status for p in probes}

    def test_a_resolving_link_is_verified(self) -> None:
        def gh(argv: Sequence[str]) -> tuple[int, str]:
            if "5073964358" in " ".join(argv):
                return 0, "the framing divergence, recorded.\n\n- embodiment (Claude)\n"
            return 0, "[]"

        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=gh)
        probes = {p.probe.id: p for s in report.signals for p in s.probes}
        assert probes["divergence-comment"].status == checklist.VERIFIED

    def test_an_empty_search_is_outstanding_not_verified(self) -> None:
        def gh(_argv: Sequence[str]) -> tuple[int, str]:
            return 0, "[]"

        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=gh)
        probes = {p.probe.id: p for s in report.signals for p in s.probes}
        assert probes["seam-proposal-search"].status == checklist.OUTSTANDING
        assert probes["seam-proposal"].status == checklist.OUTSTANDING

    def test_a_search_that_finds_something_still_needs_a_human(self) -> None:
        def gh(argv: Sequence[str]) -> tuple[int, str]:
            if "issue" in argv:
                return 0, '[{"number": 400, "title": "import embodiment"}]'
            return 0, ""

        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=gh)
        probes = {p.probe.id: p for s in report.signals for p in s.probes}
        assert probes["seam-proposal-search"].status == checklist.REPORTED
        assert "400" in probes["seam-proposal-search"].detail

    def test_the_filed_seam_proposal_is_a_link_that_can_resolve(self) -> None:
        def gh(argv: Sequence[str]) -> tuple[int, str]:
            if "repos/agentculture/colleague/issues/358" in argv:
                return 0, "Proposal: import embodiment's extracted loop"
            return 0, ""

        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=gh)
        probes = {p.probe.id: p for s in report.signals for p in s.probes}
        assert probes["seam-proposal"].status == checklist.VERIFIED

    def test_a_gh_error_is_unavailable_with_its_reason(self) -> None:
        def gh(_argv: Sequence[str]) -> tuple[int, str]:
            return 4, "gh: not authenticated"

        report = checklist.check(root=REPO_ROOT, online=True, gh_runner=gh)
        probe = next(p for s in report.signals for p in s.probes)
        assert probe.status == checklist.UNAVAILABLE
        assert "not authenticated" in probe.detail

    def test_the_internal_signals_carry_evidence_that_resolves(self) -> None:
        report = checklist.check(root=REPO_ROOT)
        internal = [s for s in report.signals if s.signal.kind == checklist.INTERNAL]
        assert internal
        for result in internal:
            assert result.status == checklist.RESOLVED
            assert result.evidence


# ── 5. the caveats ────────────────────────────────────────────────────────────


class TestCaveatsAreSurfaced:
    """Known-outstanding items are reported, not quietly omitted."""

    def test_d4_is_reported_as_recorded_but_unmet(self) -> None:
        caveat = next(c for c in checklist.CAVEATS if c.id == "d4")
        assert "not" in caveat.state.lower()
        for word in ("museless baseline", "two-mind", "echo-chamber"):
            assert word in caveat.detail

    def test_the_public_api_rough_edges_are_all_listed(self) -> None:
        detail = " ".join(c.detail for c in checklist.CAVEATS)
        for edge in ("record_id_for", "repo_path", "snapshot"):
            assert edge in detail

    def test_the_seam_proposal_is_named_as_outstanding(self) -> None:
        assert any("seam" in c.title for c in checklist.CAVEATS)

    def test_every_caveat_reaches_the_rendered_report(self) -> None:
        rendered = checklist.check(root=REPO_ROOT).render()
        for caveat in checklist.CAVEATS:
            assert caveat.id in rendered
            assert caveat.title in rendered

    def test_every_caveat_reaches_the_json_report(self) -> None:
        payload = checklist.check(root=REPO_ROOT).to_dict()
        assert {c["id"] for c in payload["caveats"]} == {c.id for c in checklist.CAVEATS}


class TestNoCaveatIsStale:
    """A caveat that has silently become untrue is its own kind of drift."""

    @pytest.mark.parametrize("caveat", checklist.CAVEATS, ids=lambda c: c.id)
    def test_the_caveat_still_stands(self, caveat: Any) -> None:
        assert caveat.check(REPO_ROOT) is None, f"{caveat.id} may be stale — revisit it"

    def test_a_stale_caveat_fails_the_checklist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stale = checklist.Caveat(
            id="x9",
            title="something already fixed",
            detail="…",
            state="outstanding",
            check=lambda _root: "this was fixed in 0.6.0",
        )
        monkeypatch.setattr(checklist, "CAVEATS", (stale,))
        report = checklist.check(root=REPO_ROOT)
        assert not report.ok
        assert "this was fixed in 0.6.0" in report.render()

    def test_at_least_one_caveat_is_genuinely_probed(self) -> None:
        """All-``None`` probes would make the class above vacuous."""
        probed = [c for c in checklist.CAVEATS if c.check is not checklist.unprobed]
        assert len(probed) >= 3


# ── 6. provenance: the shipped announcement vs the frame's claim ──────────────


class TestAnnouncementProvenance:
    """The frame confirmed c45; deviations changed it. Say so, don't paper it."""

    def test_the_frame_claim_is_quoted_as_the_frame_still_has_it(self) -> None:
        report = checklist.check(root=REPO_ROOT)
        assert report.provenance.status == checklist.RESOLVED, report.provenance.detail

    def test_the_shipped_text_differs_and_the_report_says_why(self) -> None:
        assert checklist.ANNOUNCEMENT != checklist.FRAME_CLAIM_TEXT
        rendered = checklist.check(root=REPO_ROOT).render()
        assert "c45" in rendered
        assert "d2" in rendered

    def test_a_reworded_frame_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(checklist, "FRAME_CLAIM_TEXT", "something else entirely")
        report = checklist.check(root=REPO_ROOT)
        assert report.provenance.status == checklist.DRIFTED
        assert not report.ok

    def test_a_missing_frame_is_unavailable_not_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(checklist, "FRAME_PATH", ".devague/frames/gone.json")
        report = checklist.check(root=REPO_ROOT)
        assert report.provenance.status == checklist.UNAVAILABLE
        assert report.ok


# ── 7. the report never claims more than it ran ───────────────────────────────


class TestTheVerdictIsHonest:
    def test_resolve_mode_says_nothing_was_executed(self) -> None:
        rendered = checklist.check(root=REPO_ROOT).render()
        assert "nothing was executed" in rendered

    def test_run_mode_says_what_ran_here(self) -> None:
        rendered = checklist.check(root=REPO_ROOT, run=True, pytest_runner=_green).render()
        assert "ran here" in rendered

    def test_the_verdict_never_claims_the_announcement_is_true(self) -> None:
        for report in (
            checklist.check(root=REPO_ROOT),
            checklist.check(root=REPO_ROOT, run=True, pytest_runner=_green),
        ):
            rendered = report.render().lower()
            assert "announcement is true" not in rendered
            assert "proves the announcement" not in rendered
            # The unverifiable half is always on screen.
            assert "external" in rendered
            assert "caveat" in rendered

    def test_the_json_report_is_serialisable_and_complete(self) -> None:
        payload = checklist.check(root=REPO_ROOT).to_dict()
        json.dumps(payload)  # must not raise
        assert payload["announcement"] == checklist.ANNOUNCEMENT
        assert payload["mode"] == "resolve"
        assert payload["ok"] is True
        assert len(payload["clauses"]) == len(checklist.CLAUSES)
        assert payload["signals"]
        assert payload["caveats"]


# ── 8. the command line ───────────────────────────────────────────────────────


class TestTheCommandLine:
    def test_it_exits_zero_and_prints_the_checklist(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = checklist.main([])
        out = capsys.readouterr().out
        assert code == 0
        assert checklist.ANNOUNCEMENT in out

    def test_json_mode_emits_exactly_one_object(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = checklist.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["ok"] is True

    def test_the_exit_code_follows_the_report(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        broken = checklist.Clause(
            id="x1",
            title="gone",
            quote="embodiment",
            evidence=(checklist.Evidence("tests/test_loop.py::test_vanished", "nothing"),),
        )
        monkeypatch.setattr(checklist, "CLAUSES", (broken,))
        assert checklist.main([]) == 1
        capsys.readouterr()

    def test_help_mentions_the_run_and_online_flags(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            checklist.main(["--help"])
        assert exit_info.value.code == 0
        out = capsys.readouterr().out
        assert "--run" in out
        assert "--online" in out


# ── 9. hermetic, and no colleague ─────────────────────────────────────────────


class TestHermetic:
    @staticmethod
    def _module_scope_imports() -> set[str]:
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_the_checklist_imports_only_stdlib(self) -> None:
        assert self._module_scope_imports() <= set(sys.stdlib_module_names)

    def test_the_checklist_imports_no_colleague(self) -> None:
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("colleague") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("colleague")

    def test_the_checklist_does_not_import_embodiment(self) -> None:
        """It verifies from the outside: the package under test is never loaded."""
        assert "embodiment" not in self._module_scope_imports()

    def test_the_offline_check_spawns_no_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bomb(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("the offline checklist must not spawn anything")

        monkeypatch.setattr(subprocess, "run", bomb)
        report = checklist.check(root=REPO_ROOT)
        report.render()
        assert report.ok

    def test_the_offline_check_reads_only_the_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened: list[str] = []
        real_read = Path.read_text

        def spy(self: Path, *args: Any, **kwargs: Any) -> str:
            opened.append(str(self))
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", spy)
        checklist.check(root=REPO_ROOT)
        assert opened
        assert all(path.startswith(str(REPO_ROOT)) for path in opened)


# ── 10. the doc stays in step ─────────────────────────────────────────────────


class TestTheDocDescribesTheChecklist:
    DOC = REPO_ROOT / "docs" / "announcement-checklist.md"

    def test_the_doc_exists(self) -> None:
        assert self.DOC.is_file()

    def test_the_doc_shows_the_command(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        assert "python -m tests.announcement_checklist" in text

    def test_the_doc_names_every_clause(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for clause in checklist.CLAUSES:
            assert clause.id in text, f"{clause.id} is not described in the doc"

    def test_the_doc_names_every_caveat(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for caveat in checklist.CAVEATS:
            assert caveat.id in text

    def test_the_doc_states_what_is_not_verified(self) -> None:
        text = self.DOC.read_text(encoding="utf-8").lower()
        assert "external" in text
        assert "d4" in text


def test_the_manifest_covers_every_module_under_test() -> None:
    """A cheap tripwire: a new top-level guard family should be cited or refused."""
    cited = {node_id.split("::")[0] for node_id in ALL_NODE_IDS}
    uncited = {
        "tests/test_loop.py",
        "tests/test_framing.py",
        "tests/test_ledger.py",
        "tests/test_zero_deps.py",
        "tests/test_no_shell_host.py",
        "tests/test_no_silent_degradation.py",
        "tests/test_demo_greenhouse.py",
        "tests/test_muse_runner.py",
        "tests/test_presence_engine.py",
        "tests/test_package_surface.py",
        "tests/test_lifecycle.py",
        "tests/test_cli.py",
    } - cited
    assert not uncited, f"announcement families with no citation: {sorted(uncited)}"
