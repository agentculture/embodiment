"""The four drone safeguards (task t12), and the proof each one can actually fail.

Task t11 shipped the drone core and deliberately left these four as seams. They
are not decoration: each is a named failure mode from issue #45, and the design
they guard is **unvalidated** — #44's experiment has not run.

1. **Opt-in and off** (c25) — a fresh checkout with no explicit opt-in evokes
   nothing.
2. **Staleness refusal** — ``list`` re-checks each drone's assumed surface, so
   a stale drone is visible *without being executed*; ``evoke`` refuses to run
   one rather than let it report confidently on a check that no longer means
   anything.
3. **The audit trail** (c45) — every evocation, including refusals and
   failures, records the drone name, the sha256 of the bytes that ran, the
   capability set and call-acceptance. *A run leaving no record is a test
   failure*, asserted here for every outcome the harness can produce.
4. **No escalation** (c46) — an undecidable case returns "I cannot". There is
   no escalate-to-cortex path in v1.

**Every guard here is mutation-proven.** A safeguard nobody proved can fail is
a safeguard nobody has, so :class:`TestTheGuardsCanFail` disables each one — by
rewriting the guard out of a copy of ``embodiment/drone.py`` and executing
that, or by neutering the check an assertion rests on — and asserts the
behaviour the guard was holding back returns. Without those, a green suite here
would be equally consistent with four guards that never fire.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import socket
import subprocess
import sys
import types
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

import pytest

from embodiment import drone as drone_lib
from embodiment.cli import main
from tests.test_drone import DESCRIPTION, DRAFT, GOOD_SOURCE, author

_DRONE_SOURCE_PATH = Path(drone_lib.__file__)

#: A drone that leaves a trace on disk when its code runs. The point of the
#: refusal tests is not that ``invoke`` *returned* a refusal — it is that
#: nothing executed. A returned refusal is consistent with the drone having run
#: and then been suppressed; an absent marker is not.
MARKER_SOURCE = """\
def run(request):
    (request.root / "DRONE-RAN").write_text("yes", encoding="utf-8")
    return {"answer": "ran"}
"""

MARKER_DRAFT: dict[str, Any] = {
    "purpose": "Writes a marker file so a test can tell whether it executed.",
    "assumed_surface": [{"kind": "path", "value": "embodiment/cli/_commands/"}],
    "capabilities": ["write_repo"],
    "questions": [],
    "smoke": {"args": {}, "answers": {}},
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway repo root whose surface satisfies :data:`MARKER_DRAFT`."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "embodiment" / "cli" / "_commands").mkdir(parents=True)
    return root


@pytest.fixture()
def drones_dir(repo: Path) -> Path:
    return repo / drone_lib.DRONES_DIRNAME


@pytest.fixture()
def marker(drones_dir: Path, repo: Path) -> Path:
    """A saved marker drone, with the trace its own smoke run left cleaned up."""
    author(
        drones_dir,
        MARKER_SOURCE,
        name="marker",
        draft=MARKER_DRAFT,
        description="writes a marker file when it runs",
    )
    trace = repo / "DRONE-RAN"
    if trace.exists():
        trace.unlink()
    return trace


ENABLED = drone_lib.DroneOptIn(enabled=True, source="explicit", detail="test opt-in")


def load_marker(drones_dir: Path) -> drone_lib.Drone:
    return drone_lib.load("marker", drones_dir)


# ── safeguard 1: opt-in, and off (claim c25) ────────────────────────────────


class TestAFreshCheckoutEvokesNothing:
    """The acceptance criterion, and the constant a governance guard reads.

    The standing rule is that a *measured* failure mode never ships as default
    behaviour. This is its mirror image: the drone design is **unmeasured**, so
    it does not ship on either.
    """

    def test_the_shipped_default_is_off(self) -> None:
        """What ``tests/test_governance.py`` (task t13) asserts without running a drone."""
        assert drone_lib.DRONES_ENABLED_BY_DEFAULT is False

    def test_an_empty_environment_resolves_to_disabled(self) -> None:
        resolved = drone_lib.resolve_opt_in(env={})
        assert resolved.enabled is False
        assert resolved.source == "default"
        assert resolved.detail, "a refusal has to say why, not just say no"

    def test_a_fresh_checkout_does_not_run_the_drone(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        record = drone_lib.invoke(load_marker(drones_dir), root=repo)
        assert record.outcome == drone_lib.OUTCOME_REFUSED_OPT_IN
        assert record.refused is True
        assert record.ok is False
        assert record.answer is None
        # THE assertion: not "it returned a refusal" but "nothing executed".
        assert not marker.exists(), "the drone's code ran despite the opt-in guard"
        assert record.ran is False

    def test_the_cli_refuses_and_names_the_switch(
        self, drones_dir: Path, marker: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["drone", "evoke", "marker", "--drones-dir", str(drones_dir)])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == "", "errors must never reach stdout"
        assert captured.err.startswith("error:")
        assert "hint:" in captured.err
        assert drone_lib.DRONES_ENABLED_ENV in captured.err
        assert not marker.exists()

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " enabled "])
    def test_recognised_values_turn_it_on(self, value: str) -> None:
        resolved = drone_lib.opt_in_from_env({drone_lib.DRONES_ENABLED_ENV: value})
        assert resolved.enabled is True
        assert resolved.source == "env"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe", "banana"])
    def test_everything_else_fails_closed_with_a_reason(self, value: str) -> None:
        """An opt-in guard that resolves ambiguity in favour of running is not a guard."""
        resolved = drone_lib.opt_in_from_env({drone_lib.DRONES_ENABLED_ENV: value})
        assert resolved.enabled is False
        assert resolved.detail

    def test_the_env_switch_runs_it(self, drones_dir: Path, repo: Path, marker: Path) -> None:
        record = drone_lib.invoke(
            load_marker(drones_dir),
            root=repo,
            opt_in=drone_lib.opt_in_from_env({drone_lib.DRONES_ENABLED_ENV: "1"}),
        )
        assert record.outcome == drone_lib.OUTCOME_ANSWERED
        assert record.ran is True
        assert marker.exists()

    def test_an_explicit_host_decision_beats_the_environment(self) -> None:
        off_env = {drone_lib.DRONES_ENABLED_ENV: "0"}
        assert drone_lib.resolve_opt_in(ENABLED, env=off_env).enabled is True
        assert drone_lib.resolve_opt_in(None, env=off_env).enabled is False

    def test_the_authorising_decision_is_on_the_record(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        """The audit trail answers *who turned this on*, not only *what ran*."""
        record = drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED)
        assert record.opt_in.source == "explicit"
        assert record.to_json()["opt_in"]["source"] == "explicit"

    def test_create_still_works_with_drones_off(self, drones_dir: Path) -> None:
        """Authoring is not the default-on execution path c25 forbids.

        ``create`` proves its own candidate runs, against a copy staged in a
        temporary directory, from source the caller handed in this second. If
        this needed the switch, a fresh checkout could not author a drone at
        all — and the guard would be aimed at the wrong thing.
        """
        created = author(drones_dir, GOOD_SOURCE)
        assert created.manifest["smoke"]["passed"] is True

    def test_smoke_on_a_saved_drone_is_not_a_way_round_the_switch(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        """``create``'s bypass must not generalise into a public one."""
        result = drone_lib.smoke(load_marker(drones_dir), root=repo)
        assert result.passed is False
        assert not marker.exists()

    def test_the_authoring_bypass_is_used_once_and_only_by_create(self) -> None:
        """The one named way the ambient switch is skipped, pinned structurally.

        ``create`` hands ``_AUTHORING_OPT_IN`` to the smoke run of the copy it
        is staging. A second use anywhere else in ``drone.py`` would be a
        second way drone execution enables itself, and it would not be visible
        by reading ``invoke``. Adding one has to update this pin.
        """
        tree = ast.parse(_DRONE_SOURCE_PATH.read_text(encoding="utf-8"))
        users = sorted(
            {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(
                    isinstance(inner, ast.Name) and inner.id == "_AUTHORING_OPT_IN"
                    for inner in ast.walk(node)
                )
            }
        )
        assert users == ["create"], (
            f"_AUTHORING_OPT_IN is used by {users} in {_DRONE_SOURCE_PATH.name}. It "
            f"bypasses the c25 opt-in switch and belongs to `create` alone — where it "
            f"applies to a staged copy in a temp directory, from source the caller just "
            f"handed in. Anywhere else it is a default-on execution path."
        )


# ── safeguard 2: the assumed-surface re-check ───────────────────────────────


def surface_draft(*entries: dict[str, Any]) -> dict[str, Any]:
    draft = json.loads(json.dumps(MARKER_DRAFT))
    draft["assumed_surface"] = list(entries)
    return draft


class TestTheSurfaceCheckItself:
    """A drone that cannot detect its own obsolescence should not be shipped."""

    def _report(self, drones_dir: Path, repo: Path, *entries: dict[str, Any]):
        created = author(
            drones_dir,
            MARKER_SOURCE,
            name="marker",
            draft=surface_draft(*entries),
            description="d",
        )
        (repo / "DRONE-RAN").unlink(missing_ok=True)
        return drone_lib.check_surface(created, root=repo)

    def test_a_path_that_still_exists_holds(self, drones_dir: Path, repo: Path) -> None:
        report = self._report(drones_dir, repo, {"kind": "path", "value": "embodiment"})
        assert report.status == drone_lib.STATUS_OK
        assert report.stale is False

    def test_a_path_that_has_gone_is_stale(self, drones_dir: Path, repo: Path) -> None:
        report = self._report(drones_dir, repo, {"kind": "path", "value": "embodiment"})
        assert report.status == drone_lib.STATUS_OK
        shutil.rmtree(repo / "embodiment")
        created = drone_lib.load("marker", drones_dir)
        report = drone_lib.check_surface(created, root=repo)
        assert report.status == drone_lib.STATUS_STALE
        assert report.stale is True
        assert "embodiment" in report.summary
        assert "no longer exists" in report.summary

    def test_a_glob_with_no_matches_is_stale(self, drones_dir: Path, repo: Path) -> None:
        (repo / "embodiment" / "a.py").write_text("x", encoding="utf-8")
        report = self._report(drones_dir, repo, {"kind": "glob", "value": "embodiment/*.py"})
        assert report.status == drone_lib.STATUS_OK
        (repo / "embodiment" / "a.py").unlink()
        report = drone_lib.check_surface(drone_lib.load("marker", drones_dir), root=repo)
        assert report.status == drone_lib.STATUS_STALE

    def test_a_convention_that_moved_is_stale_though_the_file_still_exists(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """The headline staleness case, and the one an exists-check cannot catch.

        Issue #45's example is a ``review`` drone checking a convention that
        changes next month: the file is still there, the convention is not, and
        the drone keeps passing authoritatively on a check that no longer means
        anything. A ``path`` assumption would be perfectly happy here.
        """
        target = repo / "embodiment" / "cli" / "__init__.py"
        target.write_text("def register(sub):\n    pass\n", encoding="utf-8")
        entry = {
            "kind": "contains",
            "value": "embodiment/cli/__init__.py",
            "text": "def register(",
        }
        assert self._report(drones_dir, repo, entry).status == drone_lib.STATUS_OK

        target.write_text("def add_verb(sub):\n    pass\n", encoding="utf-8")
        report = drone_lib.check_surface(drone_lib.load("marker", drones_dir), root=repo)
        assert target.exists(), "the file is still there; only the convention moved"
        assert report.status == drone_lib.STATUS_STALE
        assert "no longer contains" in report.summary

    def test_a_kind_this_build_cannot_check_is_unverifiable_never_ok(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """Absence of a check must never render as ``ok`` — t11's honesty, extended."""
        report = self._report(
            drones_dir, repo, {"kind": "vibes", "value": "the codebase feels fine"}
        )
        assert report.status == drone_lib.STATUS_UNVERIFIABLE
        assert report.status != drone_lib.STATUS_OK
        assert report.checks[0].held is None
        assert "not one this build can re-check" in report.summary

    def test_declaring_no_surface_at_all_is_unverifiable(
        self, drones_dir: Path, repo: Path
    ) -> None:
        report = self._report(drones_dir, repo)
        assert report.status == drone_lib.STATUS_UNVERIFIABLE
        assert "no assumed surface declared" in report.summary

    def test_an_assumption_pointing_outside_the_root_is_not_checked(
        self, drones_dir: Path, repo: Path
    ) -> None:
        report = self._report(drones_dir, repo, {"kind": "path", "value": "../../etc"})
        assert report.status == drone_lib.STATUS_UNVERIFIABLE
        assert "outside the root" in report.summary

    def test_one_failed_assumption_makes_the_whole_drone_stale(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """A drone is not partly trustworthy."""
        report = self._report(
            drones_dir,
            repo,
            {"kind": "path", "value": "embodiment"},
            {"kind": "path", "value": "does/not/exist"},
        )
        assert report.status == drone_lib.STATUS_STALE

    def test_a_check_that_cannot_run_never_takes_down_the_listing(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """Never-raise, three ways. A staleness check that explodes makes the
        stale drone *less* visible, not more — so each unrunnable check reports
        itself and the rest still run.
        """
        # Built directly rather than through `load`: `validate_manifest` refuses
        # a text-less `contains` at create time, so the only way these shapes
        # reach the checker is a host composing a Drone itself — which
        # `check_surface`'s signature permits, so it has to survive them.
        drone = drone_lib.Drone(
            name="marker",
            home=drones_dir / "marker",
            manifest={
                "assumed_surface": [
                    {"kind": "glob", "value": ""},
                    {"kind": "contains", "value": "embodiment"},
                    {"kind": "contains", "value": "embodiment/cli", "text": "def register("},
                ]
            },
        )
        report = drone_lib.check_surface(drone, root=repo)

        by_reason = [check.reason for check in report.checks]
        assert "could not be evaluated" in by_reason[0]
        assert "no non-empty 'text'" in by_reason[1]
        assert "cannot be read any more" in by_reason[2]
        # The unreadable target is a refutation, not an unknown: the drone said
        # a file would be there with something in it, and it is not.
        assert [check.held for check in report.checks] == [None, None, False]
        assert report.status == drone_lib.STATUS_STALE

    def test_a_contains_assumption_with_no_text_is_refused_at_create(
        self, drones_dir: Path
    ) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(
                drones_dir,
                MARKER_SOURCE,
                name="marker",
                draft=surface_draft({"kind": "contains", "value": "embodiment"}),
                description="d",
            )
        assert "no non-blank 'text'" in exc.value.message


class TestStalenessIsVisibleWithoutRunning:
    """``list`` is the natural home: you see it while *choosing* a drone."""

    def test_list_renders_the_verdict_and_never_executes(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        records = drone_lib.catalog(drones_dir, status_fn=drone_lib.surface_status_fn(repo))
        assert [r.status for r in records] == [drone_lib.STATUS_OK]

        shutil.rmtree(repo / "embodiment")
        (record,) = drone_lib.catalog(drones_dir, status_fn=drone_lib.surface_status_fn(repo))
        assert record.status == drone_lib.STATUS_STALE
        assert "no longer exists" in record.problem
        assert not marker.exists(), "`list` executed a drone; it must only read manifests"

    def test_the_list_verb_wires_the_check(
        self, drones_dir: Path, repo: Path, marker: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        shutil.rmtree(repo / "embodiment")
        assert main(["drone", "list", "--drones-dir", str(drones_dir)]) == 0
        captured = capsys.readouterr()
        assert drone_lib.STATUS_STALE in captured.out
        assert captured.err.startswith("warning:")
        assert not marker.exists()

    def test_an_ok_row_carries_no_problem_line(self, drones_dir: Path, repo: Path) -> None:
        author(drones_dir, MARKER_SOURCE, name="marker", draft=MARKER_DRAFT, description="d")
        (record,) = drone_lib.catalog(drones_dir, status_fn=drone_lib.surface_status_fn(repo))
        assert record.status == drone_lib.STATUS_OK
        assert record.problem == ""


class TestEvokeRefusesAStaleDrone:
    """ "Refuses rather than reports" — the failure mode is a confident answer."""

    def test_a_stale_drone_is_not_run(self, drones_dir: Path, repo: Path, marker: Path) -> None:
        shutil.rmtree(repo / "embodiment")
        record = drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED)
        assert record.outcome == drone_lib.OUTCOME_REFUSED_STALE
        assert record.refused is True
        assert record.answer is None
        assert record.ran is False
        assert not marker.exists(), "a stale drone executed and could have reported"
        assert record.surface is not None
        assert record.surface.status == drone_lib.STATUS_STALE

    def test_stale_ok_runs_it_and_the_record_still_says_so(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        """An override that hides what it overrode would be worse than none."""
        shutil.rmtree(repo / "embodiment")
        record = drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED, stale_ok=True)
        assert record.outcome == drone_lib.OUTCOME_ANSWERED
        assert marker.exists()
        assert record.surface is None, "the check was skipped, and says so rather than 'ok'"

    def test_the_cli_refuses_and_offers_the_override(
        self, drones_dir: Path, repo: Path, marker: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(drone_lib.DRONES_ENABLED_ENV, "1")
        shutil.rmtree(repo / "embodiment")
        rc = main(["drone", "evoke", "marker", "--drones-dir", str(drones_dir)])
        assert rc == 1
        assert not marker.exists()

        assert (
            main(["drone", "evoke", "marker", "--drones-dir", str(drones_dir), "--stale-ok"]) == 0
        )
        assert marker.exists()

    def test_an_unverifiable_surface_does_not_block(self, drones_dir: Path, repo: Path) -> None:
        """Unverifiable is honest, not an accusation — it must not become a landmine."""
        author(
            drones_dir,
            MARKER_SOURCE,
            name="marker",
            draft=surface_draft({"kind": "vibes", "value": "unknowable"}),
            description="d",
        )
        record = drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED)
        assert record.outcome == drone_lib.OUTCOME_ANSWERED
        assert record.surface is not None
        assert record.surface.status == drone_lib.STATUS_UNVERIFIABLE


# ── safeguard 3: the audit trail (claim c45) ────────────────────────────────


def _evoke_for(outcome: str, drones_dir: Path, repo: Path) -> drone_lib.Evocation:
    """Produce a real evocation of each outcome the harness can reach."""
    if outcome == drone_lib.OUTCOME_ANSWERED:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        return drone_lib.invoke(
            created,
            root=repo,
            opt_in=ENABLED,
            ask=drone_lib.mapping_ask({"is_cycle_intentional": "yes"}),
        )
    if outcome == drone_lib.OUTCOME_CANNOT:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        return drone_lib.invoke(created, root=repo, opt_in=ENABLED)
    if outcome == drone_lib.OUTCOME_FAILED:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        (created.home / "drone.py").write_text(
            "def run(request):\n    raise RuntimeError('rot')\n", encoding="utf-8"
        )
        return drone_lib.invoke(created, root=repo, opt_in=ENABLED)
    if outcome == drone_lib.OUTCOME_REFUSED_OPT_IN:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        return drone_lib.invoke(created, root=repo)
    if outcome == drone_lib.OUTCOME_REFUSED_STALE:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        shutil.rmtree(repo / "embodiment")
        return drone_lib.invoke(created, root=repo, opt_in=ENABLED)
    raise AssertionError(f"no fixture for outcome {outcome!r}")  # pragma: no cover


def assert_left_a_record(record: drone_lib.Evocation, drones_dir: Path) -> None:
    """The c45 assertion, extracted so the mutation test can drive it too."""
    ledger = drones_dir / drone_lib.EVOCATIONS_FILENAME
    lines = drone_lib.read_ledger(ledger)
    assert lines, f"the {record.outcome} run left NO record in {ledger}"
    written = lines[-1]
    assert written["name"] == record.name
    assert len(written["source_sha256"]) == 64, "no content hash of the drone source"
    assert isinstance(written["capabilities"], list)
    assert "call_acceptance" in written and "calls_accepted" in written
    assert written["outcome"] == record.outcome


class TestEveryEvocationLeavesARecord:
    """Including refusals and failures — a run leaving no record is a failure here."""

    @pytest.mark.parametrize("outcome", drone_lib.EVOCATION_OUTCOMES)
    def test_a_run_that_leaves_no_record_is_a_failure(
        self, outcome: str, drones_dir: Path, repo: Path
    ) -> None:
        record = _evoke_for(outcome, drones_dir, repo)
        assert record.outcome == outcome
        assert record.recorded is True, record.record_error
        assert_left_a_record(record, drones_dir)

    @pytest.mark.parametrize("outcome", drone_lib.EVOCATION_OUTCOMES)
    def test_the_four_required_fields_are_on_every_record(
        self, outcome: str, drones_dir: Path, repo: Path
    ) -> None:
        """Name, content hash, capability set, call-acceptance. All four, always."""
        record = _evoke_for(outcome, drones_dir, repo)
        assert record.name == "import-graph"
        assert len(record.source_sha256) == 64
        assert record.capabilities == ("read_repo",)
        # `call_acceptance` is None only when nothing was asked — which is a
        # value, not an absence, and is exactly what the refusals report.
        assert record.call_acceptance is None or 0.0 <= record.call_acceptance <= 1.0

    def test_the_record_is_json_serialisable(self, drones_dir: Path, repo: Path) -> None:
        record = _evoke_for(drone_lib.OUTCOME_ANSWERED, drones_dir, repo)
        round_tripped = json.loads(json.dumps(record.to_json()))
        assert round_tripped["ok"] is True
        assert round_tripped["ran"] is True
        assert round_tripped["opt_in"]["enabled"] is True
        assert round_tripped["recorded_at"]

    def test_n_evocations_leave_n_records(self, drones_dir: Path, repo: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        for _ in range(4):
            drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        assert len(drone_lib.read_ledger(drones_dir / drone_lib.EVOCATIONS_FILENAME)) == 4

    def test_the_ledger_is_not_mistaken_for_a_drone(self, drones_dir: Path, repo: Path) -> None:
        _evoke_for(drone_lib.OUTCOME_ANSWERED, drones_dir, repo)
        assert [r.name for r in drone_lib.catalog(drones_dir)] == ["import-graph"]

    def test_a_ledger_that_cannot_be_written_degrades_visibly(
        self, drones_dir: Path, repo: Path, tmp_path: Path
    ) -> None:
        """C3: a failure to record is a degradation the host sees, not an exception.

        A harness that dies while recording a run that already happened is
        strictly worse than one that reports it could not record.
        """
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        blocked = tmp_path / "blocked"
        blocked.mkdir()  # a directory where the ledger file must go
        record = drone_lib.invoke(created, root=repo, opt_in=ENABLED, ledger=blocked)
        assert record.ok is True, "the run itself must still complete and report"
        assert record.recorded is False
        assert record.record_error

    def test_the_cli_warns_when_a_record_could_not_be_written(
        self,
        drones_dir: Path,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        monkeypatch.setenv(drone_lib.DRONES_ENABLED_ENV, "1")
        monkeypatch.setenv(drone_lib.DRONE_LEDGER_ENV, str(blocked))
        capsys.readouterr()
        assert main(["drone", "evoke", "import-graph", "--drones-dir", str(drones_dir)]) == 0
        assert "NOT recorded" in capsys.readouterr().err

    def test_read_ledger_survives_a_corrupt_line(self, tmp_path: Path) -> None:
        ledger = tmp_path / "l.jsonl"
        ledger.write_text('{"name": "a"}\nnot json\n\n{"name": "b"}\n', encoding="utf-8")
        assert [r["name"] for r in drone_lib.read_ledger(ledger)] == ["a", "b"]

    def test_read_ledger_of_a_missing_file_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert drone_lib.read_ledger(tmp_path / "nope.jsonl") == []

    def test_the_ledger_path_can_be_pointed_elsewhere(
        self, drones_dir: Path, repo: Path, tmp_path: Path
    ) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        elsewhere = tmp_path / "audit" / "records.jsonl"
        drone_lib.invoke(created, root=repo, opt_in=ENABLED, ledger=elsewhere)
        assert len(drone_lib.read_ledger(elsewhere)) == 1
        assert not (drones_dir / drone_lib.EVOCATIONS_FILENAME).exists()


class TestTheHashDescribesTheCodeThatRan:
    """t11's one-read property, now load-bearing for the audit trail."""

    def test_a_same_length_rewrite_in_the_same_second_changes_the_recorded_hash(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """The bytecode-cache bug, pinned from the record's side.

        ``SourceFileLoader`` validates a cached ``.pyc`` on *(mtime, size)*, so
        a same-second rewrite to the same byte length silently ran the OLD
        code. If that regressed, the ledger would faithfully record a hash of
        bytes that never executed — the audit trail lying in precisely the
        confidently-out-of-date way this feature exists to prevent.
        """
        first = "def run(request):\n    return {'answer': 'ok'}\n"
        second = "def run(request):\n    return {'answer': 'NO'}\n"
        assert len(first) == len(second) == 46, "the test's premise: identical size"
        created = author(drones_dir, first, name="marker", draft=MARKER_DRAFT, description="d")
        one = drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        (created.home / "drone.py").write_text(second, encoding="utf-8")
        two = drone_lib.invoke(created, root=repo, opt_in=ENABLED)

        assert (one.answer, two.answer) == ("ok", "NO")
        assert one.source_sha256 != two.source_sha256
        recorded = drone_lib.read_ledger(drones_dir / drone_lib.EVOCATIONS_FILENAME)
        assert [r["source_sha256"] for r in recorded] == [one.source_sha256, two.source_sha256]

    def test_a_saved_drone_matches_the_hash_its_manifest_recorded(
        self, drones_dir: Path, repo: Path
    ) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        assert len(created.declared_hash) == 64
        record = drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        assert record.source_matches_manifest is True

    def test_an_edited_drone_surfaces_as_a_mismatch(self, drones_dir: Path, repo: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        (created.home / "drone.py").write_text(
            "def run(request):\n    return {'answer': 'edited'}\n", encoding="utf-8"
        )
        record = drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        assert record.source_matches_manifest is False
        written = drone_lib.read_ledger(drones_dir / drone_lib.EVOCATIONS_FILENAME)[-1]
        assert written["source_matches_manifest"] is False

    def test_a_manifest_with_no_declared_hash_reports_unknown_not_a_match(
        self, drones_dir: Path, repo: Path
    ) -> None:
        """Absent is *unknown*. Reporting it as a match is the same lie as ``ok``."""
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        manifest = json.loads((created.home / "manifest.json").read_text(encoding="utf-8"))
        del manifest["source_sha256"]
        (created.home / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        record = drone_lib.invoke(
            drone_lib.load("import-graph", drones_dir), root=repo, opt_in=ENABLED
        )
        assert record.source_matches_manifest is None

    def test_ok_and_outcome_cannot_disagree(self) -> None:
        """``ok`` is derived, not stored — one fact, not two that can drift apart."""
        assert "ok" not in {f.name for f in fields(drone_lib.Evocation)}
        for outcome in drone_lib.EVOCATION_OUTCOMES:
            record = drone_lib.Evocation("n", "", (), (), outcome)
            assert record.ok is (outcome in ("answered", "cannot"))


# ── safeguard 4: no escalation (decision c46) ───────────────────────────────

_ESCALATION = re.compile("escalat", re.IGNORECASE)


def escalation_names(source: str) -> list[str]:
    """Every *name* in *source* that reads as an escalation path.

    Names only — a docstring saying "there is no escalate-to-cortex path" is
    the documentation of this rule, not a violation of it.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _ESCALATION.search(node.name):
                found.append(node.name)
        elif isinstance(node, ast.Name) and _ESCALATION.search(node.id):
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and _ESCALATION.search(node.attr):
            found.append(node.attr)
        elif isinstance(node, ast.arg) and _ESCALATION.search(node.arg):
            found.append(node.arg)
    return sorted(set(found))


class TestNoEscalationPathExistsInV1:
    """An undecidable case returns "I cannot". That is the whole cost model."""

    def test_an_undecidable_case_returns_i_cannot(self, drones_dir: Path, repo: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        record = drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        assert record.outcome == drone_lib.OUTCOME_CANNOT
        assert record.cannot
        assert record.answer is None
        assert record.ok is True, "refusing is working correctly, not failing"

    def test_the_record_shows_the_refusal(self, drones_dir: Path, repo: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)
        drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        (written,) = drone_lib.read_ledger(drones_dir / drone_lib.EVOCATIONS_FILENAME)
        assert written["outcome"] == drone_lib.OUTCOME_CANNOT
        assert written["cannot"]
        assert written["answer"] is None
        # Interface failure and task failure never share a number (#33): the
        # refused call is its own axis, not folded into the outcome.
        assert written["call_acceptance"] == 0.0
        assert written["calls"][0]["accepted"] is False

    def test_the_drones_only_outward_seam_is_the_bounded_ask(self) -> None:
        """Structural: there is no seam through which a drone *could* escalate.

        ``request.ask`` is bounded by the manifest's declared questions. If a
        sixth field appeared on the request, a drone would have something else
        to reach for — which is exactly how an escalation path gets added
        without anyone deciding to add one.
        """
        assert tuple(f.name for f in fields(drone_lib.DroneRequest)) == (
            "name",
            "args",
            "root",
            "home",
            "ask",
        )

    def test_no_escalation_name_exists_in_the_module(self) -> None:
        found = escalation_names(_DRONE_SOURCE_PATH.read_text(encoding="utf-8"))
        assert found == [], (
            f"{_DRONE_SOURCE_PATH.name} now defines escalation-shaped name(s) {found}. "
            f"v1 has no escalate-to-cortex path (decision c46) — it is what keeps the "
            f"success signal exact: a drone's second evocation makes ZERO cortex calls. "
            f"Adding one is #44's question to answer with a measurement, not a patch."
        )

    def test_no_escalation_name_is_exported(self) -> None:
        assert [name for name in drone_lib.__all__ if _ESCALATION.search(name)] == []

    def test_the_escalation_detector_actually_detects(self) -> None:
        """Test-of-the-test: a check that can never fire proves nothing."""
        assert escalation_names("def escalate_to_cortex(x):\n    return x\n") == [
            "escalate_to_cortex"
        ]
        assert escalation_names("class Escalation:\n    pass\n") == ["Escalation"]
        assert escalation_names("def f(request):\n    return request.escalate()\n") == ["escalate"]
        assert escalation_names('"""no escalate-to-cortex path exists."""\n') == []

    def test_evoking_a_drone_makes_no_call_off_this_process(
        self, drones_dir: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero cortex calls, enforced rather than assumed.

        The success signal is *a drone's second evocation makes 0 cortex calls
        at <=5% of authoring token cost*. With no worker seam wired there is
        nothing for a drone to reach: no subprocess, no socket.
        """
        created = author(drones_dir, GOOD_SOURCE, draft=DRAFT, description=DESCRIPTION)

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("evoke reached off-process")

        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(socket, "socket", _boom)
        record = drone_lib.invoke(created, root=repo, opt_in=ENABLED)
        assert record.cannot


# ── the proof that each guard can fail ──────────────────────────────────────


def module_with(replacement: tuple[str, str]) -> types.ModuleType:
    """``embodiment.drone`` with one guard rewritten out, executed in isolation.

    The same compile-the-bytes trick :func:`embodiment.drone._compile_source`
    uses, applied to the module itself. Mutating the real module in place would
    leak into every other test in the session; this copy is thrown away.
    """
    old, new = replacement
    source = _DRONE_SOURCE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the guard {old!r} is no longer where this test mutates it"
    mutated = types.ModuleType("_mutated_drone")
    mutated.__file__ = str(_DRONE_SOURCE_PATH)
    sys.modules["_mutated_drone"] = mutated
    try:
        exec(  # nosec B102 - a copy of this repo's own module, for a mutation proof
            compile(source.replace(old, new), str(_DRONE_SOURCE_PATH), "exec"),
            mutated.__dict__,
        )
    finally:
        sys.modules.pop("_mutated_drone", None)
    return mutated


def expect_red(assertion: Callable[[], None]) -> None:
    """Assert *assertion* fails: the test-of-the-test half of a guard."""
    with pytest.raises(AssertionError):
        assertion()


class TestTheGuardsCanFail:
    """Disable each guard; assert the behaviour it holds back comes right back.

    Without this class, every test above would pass just as happily against
    four guards that never fire — which is how a safeguard ships broken and
    nobody notices until the thing it prevents happens.
    """

    def test_removing_the_opt_in_check_lets_a_fresh_checkout_run_a_drone(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        mutated = module_with(("if not resolved.enabled:", "if False:  # mutated"))
        record = mutated.invoke(load_marker(drones_dir), root=repo)
        assert record.outcome == mutated.OUTCOME_ANSWERED
        assert marker.exists(), (
            "the opt-in check was removed and the drone STILL did not run — so "
            "something other than the guard is stopping it, and the guard is unproven"
        )

    def test_removing_the_staleness_check_lets_a_stale_drone_report(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        shutil.rmtree(repo / "embodiment")
        # The guard holds first, on the real module.
        held = drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED)
        assert held.outcome == drone_lib.OUTCOME_REFUSED_STALE
        assert not marker.exists()

        mutated = module_with(("if surface.stale:", "if False:  # mutated"))
        record = mutated.invoke(
            load_marker(drones_dir), root=repo, opt_in=mutated.DroneOptIn(True, "explicit", "")
        )
        assert record.outcome == mutated.OUTCOME_ANSWERED
        assert marker.exists(), "the staleness check was removed and the drone still refused"

    def test_the_record_assertion_goes_red_when_nothing_is_recorded(
        self, drones_dir: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Neuter the append; the c45 assertion must fail rather than pass empty."""
        monkeypatch.setattr(drone_lib, "append_evocation", lambda record, ledger: (True, ""))
        record = _evoke_for(drone_lib.OUTCOME_ANSWERED, drones_dir, repo)
        assert record.recorded is True, "the neutered append still claims success"
        # A run that leaves no record must be a test FAILURE, not a pass.
        expect_red(lambda: assert_left_a_record(record, drones_dir))

    def test_the_no_escalation_assertion_goes_red_on_a_real_escalation(self) -> None:
        source = _DRONE_SOURCE_PATH.read_text(encoding="utf-8")
        with_path = source.replace(
            "def no_worker_ask(",
            "def escalate_to_cortex(question_id, payload):\n"
            "    raise NotImplementedError\n\n\n"
            "def no_worker_ask(",
            1,
        )
        assert escalation_names(with_path) == ["escalate_to_cortex"]

    def test_the_marker_drone_really_records_that_it_ran(
        self, drones_dir: Path, repo: Path, marker: Path
    ) -> None:
        """The instrument the refusal tests depend on. If the marker never
        appeared under any conditions, every "nothing executed" assertion above
        would pass vacuously.
        """
        assert not marker.exists()
        drone_lib.invoke(load_marker(drones_dir), root=repo, opt_in=ENABLED)
        assert marker.exists()
