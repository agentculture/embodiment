"""The surface check may not read outside the root it was handed.

`_resolve_under`'s own docstring names the threat — *"Manifests are
model-written, so an ``assumed_surface`` entry of ``/etc`` or ``../../secrets``
is a thing that can happen"* — and two of the three surface kinds routed through
it. The third, ``glob``, called ``root.glob(value)`` directly and returned
before containment was ever applied, so a manifest declaring
``kind: glob, value: "../*.pem"`` got a confident ``held=True`` **because a file
outside the repository existed**.

That is worth pinning separately from the rest of the drone suite, because the
defect was not a missing feature. It was one branch of a three-branch function
skipping a guard the other two used, in a module whose entire argument is that
an unverifiable assumption must not be reported as a passing one.

Nothing here dials a model or touches the network; every case runs against a
temporary directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import drone as drone_lib  # noqa: E402


@pytest.fixture
def rig(tmp_path: Path) -> Path:
    """A repo root with one indexed file, and a secret sitting beside it.

    The secret is *outside* the root on purpose: every escape case below is
    trying to reach it, so a test that passes because the file is absent would
    be proving nothing.
    """
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("hello", encoding="utf-8")
    (tmp_path / "outside.pem").write_text("secret", encoding="utf-8")
    return root


def _glob(value: str, root: Path) -> drone_lib.SurfaceCheck:
    return drone_lib._check_entry({"kind": "glob", "value": value}, root)


class TestAnEscapingGlobIsNotRun:
    """The tri-state is the point: refused, not answered."""

    @pytest.mark.parametrize(
        "pattern",
        [
            "../*.pem",
            "../../**/*.pem",
            "../outside.pem",
            "docs/../../*.pem",
        ],
    )
    def test_a_pattern_reaching_outside_the_root_reports_unverified(
        self, pattern: str, rig: Path
    ) -> None:
        check = _glob(pattern, rig)
        assert check.held is None, f"{pattern!r} was answered rather than refused"
        assert "outside the root" in check.reason

    def test_an_absolute_pattern_reports_unverified(self, rig: Path) -> None:
        check = _glob("/etc/*", rig)
        assert check.held is None
        assert "outside the root" in check.reason

    def test_an_empty_pattern_reports_unverified_for_its_own_reason(self, rig: Path) -> None:
        """Unverified, but **not** as an escape — the reason has to be true.

        An empty pattern is not a traversal attempt, and folding it into the
        containment refusal would explain it wrongly. It gets the phrasing the
        text-less ``contains`` case uses, because it is the same situation: the
        entry declares nothing to check.
        """
        check = _glob("", rig)
        assert check.held is None
        assert "declares no glob pattern" in check.reason
        assert "outside the root" not in check.reason

    def test_the_refusal_reason_matches_the_other_kinds(self, rig: Path) -> None:
        """One vocabulary for one situation — a reader learns it once.

        ``path`` and ``contains`` already say this sentence when
        ``_resolve_under`` refuses. The glob branch says the same one rather
        than inventing a second phrasing for the same refusal.
        """
        escaping_glob = _glob("../*.pem", rig)
        escaping_path = drone_lib._check_entry({"kind": "path", "value": "../outside.pem"}, rig)
        assert escaping_glob.reason == escaping_path.reason

    def test_the_secret_really_is_reachable_without_the_guard(self, rig: Path) -> None:
        """The test-of-the-test: without containment, `../*.pem` finds the file.

        Without this, every case above would still pass if the fixture simply
        never created the outside file — proving the guard works against a
        threat that was not there.
        """
        assert next(iter(rig.glob("../*.pem")), None) is not None


class TestAnOrdinaryGlobStillWorks:
    """Containment must not cost the feature."""

    def test_a_matching_pattern_holds(self, rig: Path) -> None:
        check = _glob("docs/*.md", rig)
        assert check.held is True
        assert check.reason == ""

    def test_a_recursive_pattern_holds(self, rig: Path) -> None:
        assert _glob("**/*.md", rig).held is True

    def test_a_pattern_matching_nothing_is_refuted_not_unverified(self, rig: Path) -> None:
        """Absent is a different answer from uncheckable, and stays so."""
        check = _glob("docs/*.rst", rig)
        assert check.held is False
        assert "no file matches" in check.reason


class TestTheGuardIsDeliberatelyConservative:
    """A false negative here costs `unverified`; a false positive costs a read."""

    def test_a_dotdot_that_stays_inside_is_still_refused(self, rig: Path) -> None:
        check = _glob("docs/../docs/*.md", rig)
        assert check.held is None, (
            "the guard is structural on purpose — a glob pattern cannot be "
            "resolved before it is walked, so `..` is refused wherever it appears"
        )

    def test_the_helper_is_a_pure_predicate(self) -> None:
        """No filesystem access: the decision precedes the walk, by design."""
        assert drone_lib._glob_stays_under("docs/*.md") is True
        assert drone_lib._glob_stays_under("../x") is False


class TestASymlinkCannotLaunderAMatchBackIn:
    """Containment is applied to the pattern *and* to each match it returns."""

    def test_a_symlink_pointing_out_of_the_root_does_not_count_as_a_match(self, rig: Path) -> None:
        link = rig / "docs" / "leak.pem"
        try:
            link.symlink_to(rig.parent / "outside.pem")
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            pytest.skip("this filesystem does not support symlinks")
        check = _glob("docs/*.pem", rig)
        assert check.held is False, (
            "the pattern stays inside the root, so it is walked — but the only "
            "match resolves outside it and must not be accepted"
        )


class TestTheEscapeCannotBreakTheRecordContract:
    """`c45`: every path through `invoke` leaves exactly one ledger record.

    Before the containment guard, an absolute glob pattern reached
    ``Path.glob``, which raises **NotImplementedError** — not caught by the
    ``(OSError, ValueError)`` this branch used to narrow to. It escaped
    ``check_surface``'s never-raise promise, escaped ``invoke``, and the run
    left **no ledger line at all**. A manifest string could delete the audit
    trail for its own evocation.

    The rest of this module exercises ``_check_entry`` directly, which is one
    layer below where that damage happened. This class goes through the real
    ``invoke``.
    """

    @staticmethod
    def _drone_asserting(tmp_path: Path, pattern: str) -> tuple[drone_lib.Drone, Path]:
        home = tmp_path / "drones" / "probe"
        home.mkdir(parents=True)
        (home / drone_lib.SOURCE_FILENAME).write_text(
            "def run(request):\n    return {'answer': 'ok'}\n", encoding="utf-8"
        )
        return (
            drone_lib.Drone(
                name="probe",
                home=home,
                manifest={"assumed_surface": [{"kind": "glob", "value": pattern}]},
            ),
            tmp_path / "root",
        )

    @pytest.mark.parametrize("pattern", ["/etc/pass*", "../../*.pem", ""])
    def test_a_hostile_pattern_still_leaves_exactly_one_record(
        self, pattern: str, tmp_path: Path
    ) -> None:
        drone, root = self._drone_asserting(tmp_path, pattern)
        root.mkdir()
        ledger = tmp_path / "ledger.jsonl"

        record = drone_lib.invoke(
            drone,
            root=root,
            opt_in=drone_lib.DroneOptIn(enabled=True, source="test", detail="test"),
            ledger=ledger,
        )

        assert record is not None, "invoke must return a record, never raise"
        assert ledger.exists(), "c45: the evocation must be on disk"
        assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_an_unverifiable_surface_does_not_read_as_verified(self, tmp_path: Path) -> None:
        """The tri-state has to survive the whole way out to the record.

        An escaping pattern is `None`, not `True` — so the drone's one
        assumption is *unchecked*, and the record must not present it as a
        surface that held.
        """
        drone, root = self._drone_asserting(tmp_path, "../../*.pem")
        root.mkdir()
        report = drone_lib.check_surface(drone, root=root)
        assert [check.held for check in report.checks] == [None]
        assert report.status != drone_lib.STATUS_OK
