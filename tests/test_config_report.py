"""embodiment.config_report — effective-config introspection (task ``t7``).

This is the contract test for :mod:`embodiment.config_report`, which pins the
acceptance criteria of spec claims ``c32``/``h22``:

1. **A host-callable report renders each seat's current effective
   configuration with provenance** — which change produced it, when, under
   which gate verdict — **derived from the applied-change ledger and nothing
   else.** → :class:`TestFoldingAppliedChanges`, :class:`TestLastAppliedWins`,
   :class:`TestKnowledgeSupersedes`, :class:`TestRevertNarratesNotUndoes`, and
   the load-bearing proof, :class:`TestParityWithALiveLifecycle` — replaying
   the ledger reconstructs the exact digest a *live*
   :class:`~embodiment.config_lifecycle.ConfigLifecycle` computed while
   actually driving the same seats.
2. **A config state the ledger cannot explain is itself a recorded
   degradation (C3).** → :class:`TestUnknownSeat`, :class:`TestUnknownTarget`,
   :class:`TestMalformedUnit`, :class:`TestMalformedRawEntries`,
   :class:`TestOrphanRevert`, :class:`TestNeverRaisesOnAHostileLedger`.

:class:`TestCitedNotCoupled` pins the import closure this module's own
docstring argues for: the pure half of ``config_lifecycle`` is reused as
computation, the ADVISORY lane and ``config_lifecycle``'s stateful
``ConfigLifecycle``/``ConfigTransition`` stream are never imported at all.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from embodiment.capability import (
    CAPABILITY_KIND_PERMISSION,
    CAPABILITY_KIND_TOOL,
    Capability,
    CapabilityCatalog,
)
from embodiment.config_change import (
    CHANGE_SEATS,
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    SEAT_SENSES,
    SEAT_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_WORKER_KNOWLEDGE,
    TARGET_WORKER_PERMISSIONS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    SensesKnowledgeChange,
    WorkerKnowledgeChange,
    WorkerPermissionsChange,
    WorkerPromptChange,
    WorkerToolsChange,
    change_from_payload,
)
from embodiment.config_ledger import (
    LEDGER_STATE_APPLIED,
    LEDGER_STATE_REVERTED,
    ConfigLedger,
    LedgerEntry,
)
from embodiment.config_lifecycle import STATE_VERIFIED, ConfigLifecycle, VerificationResult
from embodiment.config_report import (
    CONFIG_REPORT_DEGRADATION_CODES,
    CONFIG_REPORT_MALFORMED_ENTRY,
    CONFIG_REPORT_MALFORMED_UNIT,
    CONFIG_REPORT_ORPHAN_REVERT,
    CONFIG_REPORT_UNKNOWN_SEAT,
    CONFIG_REPORT_UNKNOWN_TARGET,
    ConfigReport,
    build_config_report,
    effective_config,
    render_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = _REPO_ROOT / "embodiment" / "config_report.py"


# ── fixtures: typed units, never a live seat ─────────────────────────────────


def _worker_prompt(
    change_id: str = "p1", section: str = "style", text: str = "Be terse.", **over: Any
):
    base: dict[str, Any] = dict(change_id=change_id, origin=ORIGIN_STRATEGIST, reason="why")
    base.update(over)
    return WorkerPromptChange(section=section, text=text, **base)


def _worker_knowledge(
    change_id: str = "k1",
    entry_id: str = "e1",
    text: str = "a fact",
    supersedes: Any = None,
    **over: Any,
):
    base: dict[str, Any] = dict(change_id=change_id, origin=ORIGIN_STRATEGIST, reason="why")
    base.update(over)
    return WorkerKnowledgeChange(entry_id=entry_id, text=text, supersedes=supersedes, **base)


def _senses_knowledge(
    change_id: str = "sk1",
    entry_id: str = "s-e1",
    text: str = "a senses fact",
    supersedes: Any = None,
    **over: Any,
):
    base: dict[str, Any] = dict(change_id=change_id, origin=ORIGIN_WORKER, reason="why")
    base.update(over)
    return SensesKnowledgeChange(entry_id=entry_id, text=text, supersedes=supersedes, **base)


def _worker_tools(change_id: str = "t1", ids: Any = ("fs.read",), **over: Any):
    base: dict[str, Any] = dict(change_id=change_id, origin=ORIGIN_STRATEGIST, reason="narrow")
    base.update(over)
    return WorkerToolsChange(capability_ids=tuple(ids), **base)


def _worker_permissions(change_id: str = "perm1", ids: Any = ("net.egress",), **over: Any):
    base: dict[str, Any] = dict(change_id=change_id, origin=ORIGIN_STRATEGIST, reason="grant")
    base.update(over)
    return WorkerPermissionsChange(capability_ids=tuple(ids), **base)


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        catalog_id="parity-1",
        entries=(
            Capability(capability_id="fs.read", kind=CAPABILITY_KIND_TOOL),
            Capability(capability_id="fs.write", kind=CAPABILITY_KIND_TOOL),
            Capability(capability_id="net.egress", kind=CAPABILITY_KIND_PERMISSION),
        ),
    )


def _entry(**over: Any) -> LedgerEntry:
    base: dict[str, Any] = dict(
        state=LEDGER_STATE_APPLIED,
        change_id="chg-x",
        seat=SEAT_WORKER,
        target=TARGET_WORKER_PROMPTS,
        origin=ORIGIN_STRATEGIST,
        reason="",
        step_index=0,
        unit={},
    )
    base.update(over)
    return LedgerEntry(**base)


# ── folding applied changes ──────────────────────────────────────────────────


class TestEmptyLedger:
    def test_both_seats_present_and_empty(self) -> None:
        report = build_config_report(ConfigLedger())
        assert {seat.seat for seat in report.seats} == set(CHANGE_SEATS)
        for seat in report.seats:
            assert seat.prompt == ()
            assert seat.knowledge == ()
            assert seat.tools.capability_ids == ()
            assert seat.tools.provenance is None
            assert seat.permissions.capability_ids == ()
            assert seat.permissions.provenance is None
            assert seat.history == ()
            assert seat.unexplained == ()
        assert report.unexplained == ()


class TestFoldingAppliedChanges:
    def test_a_prompt_change_is_folded_with_provenance(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt(section="style", text="Be terse."), step_index=7)
        report = build_config_report(ledger)
        seat = report.seat(SEAT_WORKER)
        assert seat is not None
        assert len(seat.prompt) == 1
        entry = seat.prompt[0]
        assert entry.section == "style"
        assert entry.text == "Be terse."
        assert entry.provenance.change_id == "p1"
        assert entry.provenance.origin == ORIGIN_STRATEGIST
        assert entry.provenance.ledger_index == 0
        assert entry.provenance.step_index == 7
        assert entry.provenance.state == LEDGER_STATE_APPLIED

    def test_a_knowledge_change_is_folded_with_provenance(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_senses_knowledge(entry_id="s-e1", text="orchid bed reads 12%"))
        report = build_config_report(ledger)
        seat = report.seat(SEAT_SENSES)
        assert seat is not None
        assert len(seat.knowledge) == 1
        entry = seat.knowledge[0]
        assert entry.entry_id == "s-e1"
        assert entry.text == "orchid bed reads 12%"
        assert entry.origin == ORIGIN_WORKER
        assert entry.provenance.change_id == "sk1"

    def test_a_capability_selection_is_folded_as_a_whole_surface(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_tools(ids=("fs.read", "fs.write")))
        ledger.record_applied(_worker_permissions(ids=("net.egress",)))
        report = build_config_report(ledger)
        seat = report.seat(SEAT_WORKER)
        assert seat is not None
        assert set(seat.tools.capability_ids) == {"fs.read", "fs.write"}
        assert seat.tools.kind == CAPABILITY_KIND_TOOL
        assert seat.tools.provenance is not None
        assert seat.tools.provenance.change_id == "t1"
        assert set(seat.permissions.capability_ids) == {"net.egress"}
        assert seat.permissions.kind == CAPABILITY_KIND_PERMISSION
        assert seat.permissions.provenance is not None
        assert seat.permissions.provenance.change_id == "perm1"


class TestLastAppliedWins:
    def test_a_later_applied_change_replaces_provenance_for_the_same_section(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt(change_id="p1", section="s", text="v1"))
        ledger.record_applied(_worker_prompt(change_id="p2", section="s", text="v2"))
        seat = build_config_report(ledger).seat(SEAT_WORKER)
        assert seat is not None
        assert len(seat.prompt) == 1  # replaced in place, not appended again
        assert seat.prompt[0].text == "v2"
        assert seat.prompt[0].provenance.change_id == "p2"
        assert seat.prompt[0].provenance.ledger_index == 1
        assert len(seat.history) == 2  # both facts stay narrated


class TestKnowledgeSupersedes:
    def test_a_superseding_entry_drops_the_superseded_ones_provenance(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_knowledge(change_id="k1", entry_id="e1", text="first"))
        ledger.record_applied(
            _worker_knowledge(change_id="k2", entry_id="e2", text="second", supersedes="e1")
        )
        seat = build_config_report(ledger).seat(SEAT_WORKER)
        assert seat is not None
        entry_ids = {entry.entry_id for entry in seat.knowledge}
        assert entry_ids == {"e2"}
        assert seat.knowledge[0].provenance.change_id == "k2"
        # the ledger still narrates that e1 was once applied
        assert {entry.change_id for entry in seat.history} == {"k1", "k2"}


class TestRevertNarratesNotUndoes:
    """ "revert restores configuration, it does not unsay what was already
    said" — proven against this module's own fold, not just asserted."""

    def test_a_reverted_change_stays_the_current_value_until_something_replaces_it(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt(change_id="p1", section="s", text="v1"))
        ledger.record_reverted("p1", reason="no longer needed")
        seat = build_config_report(ledger).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.prompt[0].text == "v1"  # narrated, not undone
        assert seat.prompt[0].provenance.change_id == "p1"
        assert seat.prompt[0].provenance.state == LEDGER_STATE_APPLIED
        assert len(seat.history) == 2
        assert [entry.state for entry in seat.history] == [
            LEDGER_STATE_APPLIED,
            LEDGER_STATE_REVERTED,
        ]
        assert seat.unexplained == ()

    def test_a_new_applied_change_after_a_revert_replaces_the_current_value(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt(change_id="p1", section="s", text="v1"))
        ledger.record_reverted("p1")
        ledger.record_applied(
            _worker_prompt(change_id="p2", section="s", text="v2", origin=ORIGIN_HOST)
        )
        seat = build_config_report(ledger).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.prompt[0].text == "v2"
        assert seat.prompt[0].provenance.change_id == "p2"
        assert len(seat.history) == 3


# ── a config state the ledger cannot explain (C3) ────────────────────────────


class TestUnknownSeat:
    def test_a_row_naming_an_unknown_seat_is_a_ledger_wide_degradation(self) -> None:
        report = build_config_report([_entry(seat="mystery-seat", change_id="ghost")])
        assert {seat.seat for seat in report.seats} == set(CHANGE_SEATS)
        for seat in report.seats:
            assert seat.prompt == ()
            assert seat.unexplained == ()
        assert len(report.unexplained) == 1
        assert report.unexplained[0].code == CONFIG_REPORT_UNKNOWN_SEAT
        assert report.unexplained[0].change_id == "ghost"


class TestUnknownTarget:
    def test_an_applied_row_naming_an_unknown_target_is_a_seat_degradation(self) -> None:
        report = build_config_report([_entry(target="worker.mystery", change_id="chg-x")])
        seat = report.seat(SEAT_WORKER)
        assert seat is not None
        assert seat.prompt == ()
        assert seat.knowledge == ()
        assert len(seat.unexplained) == 1
        assert seat.unexplained[0].code == CONFIG_REPORT_UNKNOWN_TARGET
        assert seat.unexplained[0].change_id == "chg-x"


class TestMalformedUnit:
    def test_a_prompt_unit_with_no_section_is_unexplained(self) -> None:
        row = _entry(target=TARGET_WORKER_PROMPTS, unit={"text": "no section here"})
        seat = build_config_report([row]).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.prompt == ()
        assert seat.unexplained[0].code == CONFIG_REPORT_MALFORMED_UNIT

    def test_a_knowledge_unit_with_no_entry_id_is_unexplained(self) -> None:
        row = _entry(target=TARGET_WORKER_KNOWLEDGE, unit={"text": "orphaned fact"})
        seat = build_config_report([row]).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.knowledge == ()
        assert seat.unexplained[0].code == CONFIG_REPORT_MALFORMED_UNIT

    def test_a_capability_unit_with_no_capability_ids_key_is_unexplained(self) -> None:
        row = _entry(target=TARGET_WORKER_TOOLS, unit={"catalog_id": "x"})
        seat = build_config_report([row]).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.tools.capability_ids == ()
        assert seat.tools.provenance is None
        assert seat.unexplained[0].code == CONFIG_REPORT_MALFORMED_UNIT

    def test_an_explicitly_empty_capability_selection_is_NOT_malformed(self) -> None:
        """A legitimate clearing change (t3: 'a set, not a delta') must not be
        confused with a payload that never named its surface at all."""
        row = _entry(
            target=TARGET_WORKER_TOOLS,
            change_id="chg-clear",
            origin=ORIGIN_HOST,
            unit={"capability_ids": []},
        )
        seat = build_config_report([row]).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.tools.capability_ids == ()
        assert seat.tools.provenance is not None
        assert seat.tools.provenance.change_id == "chg-clear"
        assert seat.unexplained == ()


class TestOrphanRevert:
    def test_a_revert_with_no_prior_applied_record_is_unexplained(self) -> None:
        row = _entry(state=LEDGER_STATE_REVERTED, change_id="ghost")
        seat = build_config_report([row]).seat(SEAT_WORKER)
        assert seat is not None
        assert seat.prompt == ()
        assert len(seat.history) == 1
        assert seat.unexplained[0].code == CONFIG_REPORT_ORPHAN_REVERT
        assert seat.unexplained[0].change_id == "ghost"

    def test_reverting_an_applied_entry_that_could_not_be_interpreted_is_NOT_orphaned(self) -> None:
        """The ledger DID record ``chg-x`` applied, even though this module
        could not fold its effect (an unknown target here). A later revert of
        it is explained by that history, not a second, unrelated degradation —
        ``seen_applied`` must be marked the moment an ``applied`` row is read,
        not only once folding it fully succeeds."""
        rows = [
            _entry(state=LEDGER_STATE_APPLIED, change_id="chg-x", target="worker.mystery"),
            _entry(state=LEDGER_STATE_REVERTED, change_id="chg-x"),
        ]
        seat = build_config_report(rows).seat(SEAT_WORKER)
        assert seat is not None
        assert len(seat.history) == 2
        codes = [entry.code for entry in seat.unexplained]
        assert codes == [CONFIG_REPORT_UNKNOWN_TARGET]  # exactly one, not also an orphan revert


class TestMalformedRawEntries:
    def test_unreadable_rows_are_recorded_and_never_raise(self) -> None:
        rows = [
            None,
            "garbage",
            42,
            {},
            {"state": "not-a-real-state"},
            {"state": LEDGER_STATE_APPLIED},
        ]
        report = build_config_report(rows)
        assert len(report.unexplained) == len(rows)
        assert all(entry.code == CONFIG_REPORT_MALFORMED_ENTRY for entry in report.unexplained)


class TestNeverRaisesOnAHostileLedger:
    def test_a_hostile_ledger_argument_reads_as_empty(self) -> None:
        for hostile in (None, 12345, "not-a-ledger", object()):
            report = build_config_report(hostile)
            assert isinstance(report, ConfigReport)
            assert {seat.seat for seat in report.seats} == set(CHANGE_SEATS)

    def test_a_ledger_whose_entries_accessor_raises_reads_as_empty(self) -> None:
        class _Hostile:
            @property
            def entries(self) -> Any:
                raise RuntimeError("boom")

        report = build_config_report(_Hostile())
        assert isinstance(report, ConfigReport)


class TestAcceptsARawSequenceOrAConfigLedger:
    def test_a_bare_list_of_entries_matches_a_config_ledger(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt())
        via_ledger = build_config_report(ledger)
        via_entries = build_config_report(list(ledger.entries))
        assert via_ledger.seat(SEAT_WORKER).config_sha == via_entries.seat(SEAT_WORKER).config_sha


class TestSeatsFilter:
    def test_narrowing_seats_skips_the_rest_without_degrading(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt())
        ledger.record_applied(_senses_knowledge())
        report = build_config_report(ledger, seats=(SEAT_WORKER,))
        assert [seat.seat for seat in report.seats] == [SEAT_WORKER]
        assert report.unexplained == ()


class TestEffectiveConfigConvenience:
    def test_matches_the_full_report(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt())
        assert effective_config(ledger, SEAT_WORKER) == build_config_report(ledger).seat(
            SEAT_WORKER
        )


# ── the load-bearing proof: replay reconstructs the live digest ─────────────


class _PassingVerifier:
    def __call__(self, request: Any) -> VerificationResult:
        return VerificationResult(passed=True, summary="ok", suite="report-parity")


class TestParityWithALiveLifecycle:
    """Replaying the ledger must reconstruct EXACTLY the digest a live
    ConfigLifecycle computed while actually driving the same seats — the
    strongest available proof that this module is "derived from the ledger
    alone" and correct, not merely plausible."""

    def test_reconstructs_the_same_config_sha_and_content(self) -> None:
        catalog = _catalog()
        lifecycle = ConfigLifecycle(verifier=_PassingVerifier(), catalog=catalog)
        ledger = ConfigLedger()

        payloads = [
            {
                "target": TARGET_WORKER_PROMPTS,
                "change_id": "p1",
                "origin": ORIGIN_STRATEGIST,
                "reason": "why",
                "section": "style",
                "text": "Be terse.",
            },
            {
                "target": TARGET_WORKER_TOOLS,
                "change_id": "t1",
                "origin": ORIGIN_STRATEGIST,
                "reason": "narrow",
                "capability_ids": ["fs.read"],
            },
            {
                "target": TARGET_WORKER_KNOWLEDGE,
                "change_id": "k1",
                "origin": ORIGIN_STRATEGIST,
                "reason": "why",
                "entry_id": "e1",
                "text": "first fact",
            },
            {
                "target": TARGET_WORKER_KNOWLEDGE,
                "change_id": "k2",
                "origin": ORIGIN_STRATEGIST,
                "reason": "why",
                "entry_id": "e2",
                "text": "second fact",
                "supersedes": "e1",
            },
            {
                "target": TARGET_WORKER_PERMISSIONS,
                "change_id": "perm1",
                "origin": ORIGIN_STRATEGIST,
                "reason": "grant",
                "capability_ids": ["net.egress"],
            },
            {
                "target": TARGET_SENSES_KNOWLEDGE,
                "change_id": "sk1",
                "origin": ORIGIN_WORKER,
                "reason": "why",
                "entry_id": "s-e1",
                "text": "senses fact",
            },
        ]

        for index, payload in enumerate(payloads):
            change, refusal = change_from_payload(payload, catalog=catalog)
            assert refusal is None, refusal
            assert change is not None
            proposal = lifecycle.propose(change)
            assert proposal is not None, lifecycle.degradations
            verified = lifecycle.verify(change.change_id)
            assert verified is not None, lifecycle.degradations
            assert verified.state == STATE_VERIFIED, lifecycle.degradations
            outcome = lifecycle.apply(change.change_id)
            assert outcome.applied, (outcome.refusal, outcome.deferral)
            applied_change = lifecycle.proposal(change.change_id).change
            ledger.record_applied(applied_change, step_index=index)

        report = build_config_report(ledger)
        for seat_name in CHANGE_SEATS:
            live = lifecycle.effective(seat_name)
            derived = report.seat(seat_name)
            assert derived is not None
            assert derived.unexplained == ()
            assert derived.config_sha == live.config_sha
            assert tuple(derived.tools.capability_ids) == live.tools
            assert tuple(derived.permissions.capability_ids) == live.permissions
            assert [(p.section, p.text) for p in derived.prompt] == [
                (s.section, s.text) for s in live.prompt
            ]
            assert [(k.entry_id, k.text, k.origin) for k in derived.knowledge] == [
                (k.entry_id, k.text, k.origin) for k in live.knowledge
            ]


# ── rendering ─────────────────────────────────────────────────────────────────


class TestRenderText:
    def test_contains_the_provenance_of_a_folded_change(self) -> None:
        ledger = ConfigLedger()
        ledger.record_applied(_worker_prompt(change_id="p1", section="style", text="Be terse."))
        text = render_text(build_config_report(ledger))
        assert "style" in text
        assert "p1" in text
        assert SEAT_WORKER in text

    def test_never_raises_on_a_report_full_of_degradations(self) -> None:
        report = build_config_report([_entry(seat="mystery"), _entry(target="worker.mystery")])
        text = render_text(report)
        assert isinstance(text, str)
        assert text

    def test_never_raises_on_garbage(self) -> None:
        for hostile in (None, 12345, "nope", object()):
            assert render_text(hostile) == ""  # type: ignore[arg-type]


# ── degradation-code conservation (embodiment#18's lesson) ──────────────────


class TestDegradationVocabulary:
    """Every code this module can mint has a producer — a code nothing can
    mint is a lie in the ledger (config_change.py's own stated lesson)."""

    def test_every_code_has_a_producer(self) -> None:
        produced: set[str] = set()

        produced.add(build_config_report([None]).unexplained[0].code)
        produced.add(build_config_report([_entry(seat="nope")]).unexplained[0].code)
        seat = build_config_report([_entry(target="worker.mystery")]).seat(SEAT_WORKER)
        produced.add(seat.unexplained[0].code)
        seat = build_config_report(
            [_entry(target=TARGET_WORKER_PROMPTS, unit={"text": "no section"})]
        ).seat(SEAT_WORKER)
        produced.add(seat.unexplained[0].code)
        seat = build_config_report([_entry(state=LEDGER_STATE_REVERTED)]).seat(SEAT_WORKER)
        produced.add(seat.unexplained[0].code)

        assert produced == set(CONFIG_REPORT_DEGRADATION_CODES)


# ── import discipline ─────────────────────────────────────────────────────────


class TestCitedNotCoupled:
    """Follows ``embodiment/config_ledger.py``'s ``TestCitedNotCoupled`` precedent."""

    @staticmethod
    def _imported(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                reached.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                reached.add(node.module)
        return reached

    def test_the_advisory_lane_is_not_imported(self) -> None:
        banned = {
            "embodiment.scope",
            "embodiment.scoped_run",
            "embodiment.strategist_runner",
            "embodiment.loop",
        }
        leaked = self._imported(MODULE) & banned
        assert not leaked, f"config_report.py couples to the advisory lane: {sorted(leaked)}"

    @staticmethod
    def _imported_names(path: Path, module: str) -> set[str]:
        """Every name pulled in via ``from module import ...``."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                found.update(alias.name for alias in node.names)
        return found

    def test_config_events_and_the_stateful_lifecycle_are_not_imported(self) -> None:
        """This module reads ``config_ledger``'s DATA and reuses
        ``config_lifecycle``'s PURE fold function; it has no business emitting
        events (``t5``'s job) or holding a stateful
        :class:`~embodiment.config_lifecycle.ConfigLifecycle` of its own."""
        assert "config_events" not in self._imported(MODULE)
        lifecycle_names = self._imported_names(MODULE, "embodiment.config_lifecycle")
        assert lifecycle_names == {"SeatConfig", "apply_change"}, lifecycle_names

    def test_the_import_closure_is_exactly_the_four_named_modules_plus_stdlib(self) -> None:
        internal = {name for name in self._imported(MODULE) if name.startswith("embodiment")}
        assert internal == {
            "embodiment.capability",
            "embodiment.config_change",
            "embodiment.config_ledger",
            "embodiment.config_lifecycle",
        }


class TestPackageRegistration:
    def test_reachable_as_a_submodule(self) -> None:
        import embodiment

        assert "config_report" in embodiment._SUBMODULES

    def test_no_name_hoisted_into_lazy_names(self) -> None:
        import embodiment

        names = {
            "ConfigReport",
            "SeatEffectiveConfig",
            "ChangeProvenance",
            "build_config_report",
            "effective_config",
            "render_text",
        }
        assert not (names & set(embodiment._LAZY_NAMES))

    def test_importable_by_name(self) -> None:
        import embodiment

        module = embodiment.config_report  # noqa: F841 - resolves via __getattr__ or not at all
        assert hasattr(module, "build_config_report")


class TestNotReachableFromTheCli:
    """Mirrors ``tests/announcement_checklist.py``'s ``cli1`` caveat: nothing
    under ``cli/_commands/`` may reach the loop or this introspection tier yet
    — the plan says "a CLI verb later if warranted", not now."""

    def test_no_cli_command_module_imports_config_report(self) -> None:
        commands_dir = _REPO_ROOT / "embodiment" / "cli" / "_commands"
        for path in commands_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "config_report" not in source, f"{path} reaches into config_report"
