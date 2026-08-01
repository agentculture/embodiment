"""The drone core: the smoke-before-save rule, the artifact shape, the catalog.

The headline pin is :class:`TestCreateRefusesWithoutAProvenSmokeRun`. *Well-
shaped is not runnable* — a prior task in this cycle shipped problems with
``statement=""`` and ``grade=lambda _raw: {}`` that reviewed fine for an entire
task while the rung could not be dialled. A drone saved without having been run
is that failure with a friendlier name, so every refusal below also asserts
that **nothing was written**: an unsaved failure is cheap, a saved broken drone
is a trap.

No real drone is authored anywhere in this suite — every fixture writes its own
throwaway source into ``tmp_path``. That ordering is the point: the refusal is
proven before any drone exists to be trusted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from embodiment import drone as drone_lib
from embodiment.cli import main

# ── fixtures ────────────────────────────────────────────────────────────────

#: A drone that runs, asks its one declared question, and answers.
GOOD_SOURCE = """\
def run(request):
    verdict = request.ask("is_cycle_intentional", {"module": "a"})
    if verdict is None:
        return {"cannot": "the worker did not answer is_cycle_intentional"}
    return {"answer": f"cycles: {verdict}", "detail": {"asked": 1}}
"""

DRAFT: dict[str, Any] = {
    "purpose": "Maps the import graph for a package and flags cycles.",
    "assumed_surface": [
        {"kind": "path", "value": "embodiment/cli/_commands/", "note": "verb modules live here"}
    ],
    "capabilities": ["read_repo"],
    "questions": [
        {
            "id": "is_cycle_intentional",
            "prompt": "Is this import cycle intentional?",
            "schema": {"type": "string", "enum": ["yes", "no"]},
        }
    ],
    "smoke": {"args": {"package": "embodiment"}, "answers": {"is_cycle_intentional": "no"}},
}

DESCRIPTION = "maps imports for a package, flags cycles"

#: An explicit host opt-in (task t12's c25 guard). Drones are off by default —
#: including in this suite, where ``conftest.py`` strips the switch from the
#: environment — so every test that actually RUNS a drone says so out loud.
#: ``create`` needs none of this: it uses the authoring opt-in for the staged
#: copy, which is why the authoring tests above are untouched.
ENABLED = drone_lib.DroneOptIn(enabled=True, source="explicit", detail="test opt-in")


@pytest.fixture()
def drones_dir(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    # DRAFT's assumed surface, made true. A drone whose assumptions are already
    # false is refused by the staleness guard, so the fixture builds a root the
    # fixture drone was plausibly authored against; the staleness tests then
    # break exactly this path to prove the guard fires.
    (root / "embodiment" / "cli" / "_commands").mkdir(parents=True)
    return root / drone_lib.DRONES_DIRNAME


def evoke(created: drone_lib.Drone, drones_dir: Path, **kwargs: Any) -> drone_lib.Evocation:
    """Run a drone with the opt-in explicitly granted."""
    kwargs.setdefault("opt_in", ENABLED)
    return drone_lib.invoke(created, root=drones_dir.parent, **kwargs)


def write_source(tmp_path: Path, text: str, name: str = "drone.py") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def author(
    drones_dir: Path,
    source_text: str,
    *,
    name: str = "import-graph",
    draft: dict[str, Any] | None = None,
    description: str | None = DESCRIPTION,
    **kwargs: Any,
) -> drone_lib.Drone:
    source = write_source(drones_dir.parent, source_text, f"{name}-source.py")
    return drone_lib.create(
        name,
        source=source,
        drones_dir=drones_dir,
        draft=json.loads(json.dumps(draft if draft is not None else DRAFT)),
        description=description,
        author_model="test-model",
        commit="0123456789abcdef",
        **kwargs,
    )


# ── the headline: nothing is saved that has not been proven to run ──────────


class TestCreateRefusesWithoutAProvenSmokeRun:
    """``create`` refuses to save a drone that has not passed a smoke invocation."""

    def _refuse(self, drones_dir: Path, source_text: str, **kwargs: Any) -> drone_lib.DroneError:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, source_text, **kwargs)
        # THE assertion: the refusal left nothing behind to be trusted later.
        assert not (drones_dir / kwargs.get("name", "import-graph")).exists()
        return exc.value

    def test_source_that_raises_is_not_saved(self, drones_dir: Path) -> None:
        err = self._refuse(drones_dir, "def run(request):\n    raise ValueError('boom')\n")
        assert "smoke" in err.message
        assert "ValueError" in err.message

    def test_source_that_fails_to_import_is_not_saved(self, drones_dir: Path) -> None:
        err = self._refuse(drones_dir, "import a_module_that_does_not_exist_anywhere\n")
        assert "failed to import" in err.message

    def test_source_without_the_entrypoint_is_not_saved(self, drones_dir: Path) -> None:
        err = self._refuse(drones_dir, "def not_run(request):\n    return 'hi'\n")
        assert drone_lib.DRONE_ENTRYPOINT in err.message

    def test_a_drone_that_answers_nothing_is_not_saved(self, drones_dir: Path) -> None:
        """The K1 shape: well-formed, returns, reports nothing.

        ``grade=lambda _raw: {}`` passed review for an entire task. An empty
        answer with no refusal is the same defect wearing a drone's name.
        """
        err = self._refuse(drones_dir, "def run(request):\n    return {}\n")
        assert "neither an answer nor" in err.message

    def test_a_blank_answer_string_is_not_an_answer(self, drones_dir: Path) -> None:
        self._refuse(drones_dir, "def run(request):\n    return {'answer': '   '}\n")

    def test_a_drone_returning_the_wrong_type_is_not_saved(self, drones_dir: Path) -> None:
        err = self._refuse(drones_dir, "def run(request):\n    return 42\n")
        assert "not a DroneAnswer" in err.message

    def test_an_undeclared_question_is_not_saved(self, drones_dir: Path) -> None:
        """The scoped call that is not scoped (#45 failure mode 2)."""
        err = self._refuse(
            drones_dir,
            "def run(request):\n"
            "    request.ask('review_this_file', {})\n"
            "    return {'answer': 'done'}\n",
        )
        assert "undeclared question" in err.message

    def test_a_canned_answer_violating_its_own_schema_is_not_saved(self, drones_dir: Path) -> None:
        draft = json.loads(json.dumps(DRAFT))
        draft["smoke"]["answers"]["is_cycle_intentional"] = "maybe"
        err = self._refuse(drones_dir, GOOD_SOURCE, draft=draft)
        assert "violates its own declared schema" in err.message

    def test_a_question_with_no_canned_answer_is_not_saved(self, drones_dir: Path) -> None:
        draft = json.loads(json.dumps(DRAFT))
        draft["smoke"]["answers"] = {}
        err = self._refuse(drones_dir, GOOD_SOURCE, draft=draft)
        assert "no canned answer" in err.message

    def test_an_empty_source_is_not_saved(self, drones_dir: Path) -> None:
        err = self._refuse(drones_dir, "\n\n")
        assert "empty" in err.message

    def test_the_drones_dir_is_not_even_created_by_a_refusal(self, drones_dir: Path) -> None:
        """Staging happens outside ``.drones/`` — a refusal leaves no trace at all."""
        self._refuse(drones_dir, "def run(request):\n    raise ValueError('boom')\n")
        assert not drones_dir.exists()

    def test_smoke_reports_a_refused_call_as_a_failure(self, drones_dir: Path) -> None:
        """A drone asking with the wrong id gets ``None`` and must not be saved.

        The manifest's canned answers are validated up front, so a refused call
        during smoke means the drone and its own declaration disagree.
        """
        source = (
            "def run(request):\n"
            "    request.ask('is_cycle_intentional', {})\n"
            "    return {'answer': 'ok'}\n"
        )
        draft = json.loads(json.dumps(DRAFT))
        # Declared, canned — but the drone will be handed None because the
        # smoke map is emptied through a schema the validator accepts.
        draft["questions"][0]["schema"] = {"type": "null"}
        draft["smoke"]["answers"]["is_cycle_intentional"] = None
        err = self._refuse(drones_dir, source, draft=draft)
        assert "refused" in err.message


class TestDescriptionIsRequiredAtCreateTime:
    """A drone nobody can pick from ``list`` is dead weight that cost a cortex turn."""

    def test_absent_description_refuses(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE, description=None)
        assert "one-line description" in exc.value.message
        assert not (drones_dir / "import-graph").exists()

    def test_blank_description_refuses(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError):
            author(drones_dir, GOOD_SOURCE, description="   ")

    def test_multiline_description_refuses(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE, description="does a thing\nand another")
        assert "single line" in exc.value.message

    def test_overlong_description_refuses(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE, description="x" * 200)
        assert "limit" in exc.value.message

    def test_description_may_come_from_the_draft_manifest(self, drones_dir: Path) -> None:
        draft = json.loads(json.dumps(DRAFT))
        draft["description"] = DESCRIPTION
        created = author(drones_dir, GOOD_SOURCE, draft=draft, description=None)
        assert created.description == DESCRIPTION


# ── the committed, legible artifact ─────────────────────────────────────────


class TestTheSavedArtifact:
    def test_all_three_files_land(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        assert (created.home / "manifest.json").is_file()
        assert (created.home / "drone.py").is_file()
        assert (created.home / "README.md").is_file()

    def test_manifest_carries_the_required_fields(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        manifest = json.loads((created.home / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema"] == drone_lib.MANIFEST_SCHEMA_VERSION
        assert manifest["name"] == "import-graph"
        assert manifest["description"] == DESCRIPTION
        assert manifest["purpose"]
        assert manifest["author"]["model"] == "test-model"
        assert manifest["author"]["commit"] == "0123456789abcdef"
        assert manifest["author"]["date"]
        assert manifest["assumed_surface"][0]["kind"] == "path"
        assert manifest["capabilities"] == ["read_repo"]
        assert manifest["questions"][0]["id"] == "is_cycle_intentional"

    def test_manifest_records_that_smoke_passed(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        manifest = json.loads((created.home / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["smoke"]["passed"] is True
        assert manifest["smoke"]["at"]
        assert "accepted" in manifest["smoke"]["summary"]

    def test_manifest_is_legible_json_not_a_pickle(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        text = (created.home / "manifest.json").read_text(encoding="utf-8")
        assert text.startswith("{\n"), "the manifest must be diffable, so it is indented"
        assert text.endswith("\n")

    def test_saved_source_is_byte_identical_to_what_was_handed_in(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        assert (created.home / "drone.py").read_text(encoding="utf-8") == GOOD_SOURCE

    def test_readme_always_states_the_threat_model(self, drones_dir: Path) -> None:
        """C2: state the threat model rather than letting a name imply a sandbox.

        Asserted on the *collapsed* text so the checks are about the claims
        being present, not about where the generator happened to wrap lines —
        a reflow must not silently drop a threat-model sentence, and it must
        not fail this test either.
        """
        created = author(drones_dir, GOOD_SOURCE)
        readme = (created.home / "README.md").read_text(encoding="utf-8")
        flat = " ".join(readme.split()).lower()
        assert "## threat model" in readme.lower()
        assert "there is **no sandbox**" in flat
        assert "runs this model-written python in the calling process" in flat
        assert "is a *declaration for review*, not an enforcement boundary" in flat
        assert "read `drone.py` before evoking a drone you did not author" in flat

    def test_readme_covers_what_when_wrong_and_reauthor(self, drones_dir: Path) -> None:
        readme = (author(drones_dir, GOOD_SOURCE).home / "README.md").read_text(encoding="utf-8")
        assert "## What it does" in readme
        assert "## When it is wrong" in readme
        assert "## How to re-author it" in readme
        assert "## Provenance" in readme

    def test_readme_notes_are_appended(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE, notes="Only run me on Mondays.")
        assert "Only run me on Mondays." in (created.home / "README.md").read_text("utf-8")

    def test_an_existing_name_refuses_without_force(self, drones_dir: Path) -> None:
        author(drones_dir, GOOD_SOURCE)
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE)
        assert "already exists" in exc.value.message

    def test_force_replaces(self, drones_dir: Path) -> None:
        author(drones_dir, GOOD_SOURCE)
        replaced = author(
            drones_dir,
            GOOD_SOURCE.replace("cycles:", "CYCLES:"),
            description="a replacement",
            force=True,
        )
        assert replaced.description == "a replacement"
        assert "CYCLES:" in (replaced.home / "drone.py").read_text(encoding="utf-8")

    def test_a_failed_install_leaves_the_existing_drone_intact(
        self, drones_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--force must not destroy a working drone when the INSTALL fails.

        Smoke failure is pinned below; this is the other half — the staged copy
        passed and *putting it in place* is what broke. The staging dir lives in
        system temp, so the move is usually a cross-filesystem copytree that can
        fail partway; doing it before anything is destroyed is what keeps the
        old drone. Losing it costs a cortex turn to rebuild.

        Found by review after t11 merged, re-implemented against t12's rewrite.
        """
        author(drones_dir, GOOD_SOURCE)

        def dead_move(src: Any, dst: Any) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(drone_lib.shutil, "move", dead_move)
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE, description="a replacement", force=True)
        assert "left intact" in exc.value.remediation
        assert exc.value.env is True
        monkeypatch.undo()

        survivor = drone_lib.load("import-graph", drones_dir)
        assert survivor.description == DESCRIPTION
        assert (survivor.home / "drone.py").read_text(encoding="utf-8") == GOOD_SOURCE

    def test_a_failed_force_recreate_leaves_the_existing_drone_intact(
        self, drones_dir: Path
    ) -> None:
        """--force removes the old drone only AFTER the new one passes smoke.

        Otherwise a botched re-author destroys a working drone and leaves
        nothing — the worst possible outcome for a verb whose whole value is
        not paying the authoring turn again.
        """
        author(drones_dir, GOOD_SOURCE)
        with pytest.raises(drone_lib.DroneError):
            author(
                drones_dir,
                "def run(request):\n    raise ValueError('botched')\n",
                description="a broken replacement",
                force=True,
            )
        survivor = drone_lib.load("import-graph", drones_dir)
        assert survivor.description == DESCRIPTION
        assert (survivor.home / "drone.py").read_text(encoding="utf-8") == GOOD_SOURCE

    def test_an_invalid_name_refuses(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            author(drones_dir, GOOD_SOURCE, name="Not A Name")
        assert "valid drone name" in exc.value.message

    def test_a_pure_code_drone_needs_no_questions(self, drones_dir: Path) -> None:
        """The cheapest outcome: zero worker calls, zero tokens (#45 q3)."""
        draft = {
            "purpose": "counts python files",
            "assumed_surface": [],
            "capabilities": ["read_repo"],
            "questions": [],
            "smoke": {"args": {}, "answers": {}},
        }
        created = author(
            drones_dir,
            "def run(request):\n    return {'answer': 'counted'}\n",
            name="count-files",
            draft=draft,
            description="counts python files under a path",
        )
        assert created.questions == ()
        readme = (created.home / "README.md").read_text(encoding="utf-8")
        assert "pure code-drone" in readme


# ── evoke ───────────────────────────────────────────────────────────────────


class TestInvoke:
    def test_a_saved_drone_runs(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        record = evoke(
            created, drones_dir, ask=drone_lib.mapping_ask({"is_cycle_intentional": "yes"})
        )
        assert record.ok
        assert record.answer == "cycles: yes"
        assert record.detail == {"asked": 1}
        assert record.calls == (drone_lib.DroneCall("is_cycle_intentional", True, ""),)
        assert record.call_acceptance == 1.0

    def test_no_worker_seam_means_the_drone_says_i_cannot(self, drones_dir: Path) -> None:
        """v1 has no escalation path: an undecidable case refuses (c46)."""
        created = author(drones_dir, GOOD_SOURCE)
        record = evoke(created, drones_dir)
        assert record.ok
        assert record.answer is None
        assert record.cannot
        assert record.calls[0].accepted is False

    def test_an_answer_violating_the_declared_schema_is_a_refused_call(
        self, drones_dir: Path
    ) -> None:
        """Interface failure and task failure must never share a number (#33)."""
        created = author(drones_dir, GOOD_SOURCE)
        record = evoke(
            created, drones_dir, ask=drone_lib.mapping_ask({"is_cycle_intentional": "probably"})
        )
        assert record.calls[0].accepted is False
        assert "enum" in record.calls[0].reason
        assert record.cannot

    def test_a_failing_drone_returns_a_record_rather_than_raising(self, drones_dir: Path) -> None:
        """C3: every degradation is observable to the host, not an escaping exception."""
        created = author(drones_dir, GOOD_SOURCE)
        (created.home / "drone.py").write_text(
            "def run(request):\n    raise RuntimeError('later breakage')\n", encoding="utf-8"
        )
        record = evoke(created, drones_dir)
        assert record.ok is False
        assert "RuntimeError" in record.failure
        assert record.answer is None

    def test_the_record_carries_the_audit_fields_t12_needs(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        record = evoke(created, drones_dir)
        assert record.name == "import-graph"
        assert len(record.source_sha256) == 64
        assert record.capabilities == ("read_repo",)
        assert record.source_sha256 == drone_lib.source_hash(created.source)

    def test_call_acceptance_is_none_when_nothing_was_asked(self, drones_dir: Path) -> None:
        draft = {
            "purpose": "p",
            "assumed_surface": [],
            "capabilities": [],
            "questions": [],
            "smoke": {"args": {}, "answers": {}},
        }
        created = author(
            drones_dir,
            "def run(request):\n    return 'flat answer'\n",
            name="flat",
            draft=draft,
            description="a code-only drone",
        )
        record = evoke(created, drones_dir)
        assert record.call_acceptance is None

    def test_args_reach_the_drone(self, drones_dir: Path) -> None:
        draft = {
            "purpose": "p",
            "assumed_surface": [],
            "capabilities": [],
            "questions": [],
            "smoke": {"args": {"package": "x"}, "answers": {}},
        }
        created = author(
            drones_dir,
            "def run(request):\n    return {'answer': request.args['package']}\n",
            name="echo-arg",
            draft=draft,
            description="echoes its package argument",
        )
        record = evoke(created, drones_dir, args={"package": "embodiment"})
        assert record.answer == "embodiment"

    def test_repeated_evocation_does_not_grow_sys_modules(self, drones_dir: Path) -> None:
        """A cheap verb must not leak in proportion to how often it is called."""
        import sys

        created = author(drones_dir, GOOD_SOURCE)
        before = len(sys.modules)
        for _ in range(5):
            evoke(created, drones_dir)
        assert len(sys.modules) == before

    def test_re_authoring_is_picked_up_rather_than_cached(self, drones_dir: Path) -> None:
        """Each load compiles the file's current bytes, so a rewrite really runs."""
        created = author(drones_dir, GOOD_SOURCE)
        assert evoke(created, drones_dir).cannot
        (created.home / "drone.py").write_text(
            "def run(request):\n    return {'answer': 'rewritten'}\n", encoding="utf-8"
        )
        assert evoke(created, drones_dir).answer == "rewritten"

    def test_a_same_length_rewrite_in_the_same_second_is_not_served_from_bytecode(
        self, drones_dir: Path
    ) -> None:
        """The bug a handoff probe found, pinned so it cannot come back.

        ``importlib``'s ``SourceFileLoader`` validates a cached ``.pyc`` on
        *(mtime, source size)*. Re-author a drone within the same second to a
        body of the **same byte length** — entirely ordinary for a one-line fix
        — and it happily reuses the OLD bytecode. A drone that silently runs
        its previous version is precisely the confidently-out-of-date failure
        this feature exists to prevent, and it is near-undebuggable in the
        field: the file on disk is right and the behaviour is wrong.

        ``invoke`` therefore compiles the bytes it read rather than importing
        the path. Both bodies below are deliberately 46 bytes.
        """
        first = "def run(request):\n    return {'answer': 'ok'}\n"
        second = "def run(request):\n    return {'answer': 'NO'}\n"
        assert len(first) == len(second) == 46, "the test's premise: identical size"

        draft = {
            "purpose": "p",
            "assumed_surface": [],
            "capabilities": [],
            "questions": [],
            "smoke": {"args": {}, "answers": {}},
        }
        created = author(drones_dir, first, name="rewritten", draft=draft, description="d")
        assert evoke(created, drones_dir).answer == "ok"
        # No sleep: writing inside the same second IS the condition under test.
        (created.home / "drone.py").write_text(second, encoding="utf-8")
        assert evoke(created, drones_dir).answer == "NO"

    def test_the_recorded_hash_describes_the_code_that_ran(self, drones_dir: Path) -> None:
        """One read: the audit trail cannot describe bytes other than the executed ones."""
        created = author(drones_dir, GOOD_SOURCE)
        replacement = "def run(request):\n    return {'answer': 'v2'}\n"
        (created.home / "drone.py").write_text(replacement, encoding="utf-8")
        record = evoke(created, drones_dir)
        assert record.answer == "v2"
        expected = hashlib.sha256(replacement.encode("utf-8")).hexdigest()
        assert record.source_sha256 == expected

    def test_an_unreadable_source_degrades_rather_than_raising(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        (created.home / "drone.py").unlink()
        record = evoke(created, drones_dir)
        assert record.ok is False
        assert "cannot read" in record.failure

    def test_a_syntax_error_is_reported_not_raised(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        (created.home / "drone.py").write_text("def run(request:\n", encoding="utf-8")
        record = evoke(created, drones_dir)
        assert record.ok is False
        assert "does not compile" in record.failure

    def test_loading_an_unknown_drone_names_the_list_verb(self, drones_dir: Path) -> None:
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.load("nope", drones_dir)
        assert "drone list" in exc.value.remediation


# ── the catalog `list` renders ──────────────────────────────────────────────


class TestCatalog:
    def test_empty_dir_lists_nothing(self, drones_dir: Path) -> None:
        assert drone_lib.catalog(drones_dir) == []

    def test_a_record_carries_name_description_age_and_status(self, drones_dir: Path) -> None:
        author(drones_dir, GOOD_SOURCE)
        (record,) = drone_lib.catalog(drones_dir)
        assert record.name == "import-graph"
        assert record.description == DESCRIPTION
        assert record.age
        assert record.status == drone_lib.STATUS_UNCHECKED
        assert record.model == "test-model"
        assert record.commit == "0123456789abcdef"

    def test_default_status_is_unchecked_not_ok(self, drones_dir: Path) -> None:
        """No check ran is reported as no check ran.

        t12 wires the assumed-surface re-check. Until then, claiming ``ok``
        would be the confident false claim C3 exists to prevent.
        """
        author(drones_dir, GOOD_SOURCE)
        (record,) = drone_lib.catalog(drones_dir)
        assert record.status != "ok"
        assert record.status == drone_lib.STATUS_UNCHECKED

    def test_the_status_check_is_pluggable(self, drones_dir: Path) -> None:
        """The seam t12 fills."""
        author(drones_dir, GOOD_SOURCE)
        (record,) = drone_lib.catalog(drones_dir, status_fn=lambda d: "STALE")
        assert record.status == "STALE"

    def test_a_raising_status_check_does_not_break_the_listing(self, drones_dir: Path) -> None:
        author(drones_dir, GOOD_SOURCE)

        def boom(_: drone_lib.Drone) -> str:
            raise RuntimeError("check exploded")

        (record,) = drone_lib.catalog(drones_dir, status_fn=boom)
        assert record.status == drone_lib.STATUS_BROKEN
        assert "check exploded" in record.problem

    def test_a_broken_drone_still_renders_a_row(self, drones_dir: Path) -> None:
        created = author(drones_dir, GOOD_SOURCE)
        (created.home / "manifest.json").write_text("{ not json", encoding="utf-8")
        (record,) = drone_lib.catalog(drones_dir)
        assert record.status == drone_lib.STATUS_BROKEN
        assert record.problem
        assert record.description == "(manifest unreadable)"

    def test_records_are_sorted_by_name(self, drones_dir: Path) -> None:
        author(drones_dir, GOOD_SOURCE, name="zebra", description="z")
        author(drones_dir, GOOD_SOURCE, name="alpha", description="a")
        assert [r.name for r in drone_lib.catalog(drones_dir)] == ["alpha", "zebra"]

    def test_render_has_the_four_columns_aligned(self, drones_dir: Path) -> None:
        """Every column starts at its header's offset, on every row.

        Deliberately uses names and descriptions of DIFFERENT lengths — equal
        widths would make a renderer that ignores padding entirely look
        aligned, which is the way this kind of test passes while broken.
        """
        author(drones_dir, GOOD_SOURCE, name="import-graph", description=DESCRIPTION)
        author(drones_dir, GOOD_SOURCE, name="fc", description="finds call sites")
        text = drone_lib.render_catalog(drone_lib.catalog(drones_dir))
        header, *rows = text.splitlines()
        assert header.split() == ["name", "does", "authored", "status"]
        assert len(rows) == 2
        offsets = {column: header.index(column) for column in ("name", "does", "status")}
        for row in rows:
            for column, offset in offsets.items():
                assert row[offset] != " ", f"{column} column misaligned in {row!r}"
                if offset:
                    assert row[offset - 1] == " ", f"{column} column not separated in {row!r}"
        # And the columns really are in the declared order on each row.
        for row in rows:
            assert row.index(row.split()[0]) == offsets["name"]
        assert all(not row.endswith(" ") for row in rows)

    def test_render_of_an_empty_catalog_points_at_create(self) -> None:
        text = drone_lib.render_catalog([])
        assert "no drones" in text
        assert "drone create" in text


class TestHumanizeAge:
    @pytest.mark.parametrize(
        ("delta", "expected"),
        [
            (timedelta(seconds=5), "just now"),
            (timedelta(minutes=7), "7m ago"),
            (timedelta(hours=5), "5h ago"),
            (timedelta(days=12), "12d ago"),
            (timedelta(days=41), "41d ago"),
        ],
    )
    def test_rendering(self, delta: timedelta, expected: str) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        assert drone_lib.humanize_age((now - delta).isoformat(), now) == expected

    def test_unparseable_is_unknown_not_a_guess(self) -> None:
        assert drone_lib.humanize_age("last tuesday") == "unknown"
        assert drone_lib.humanize_age("") == "unknown"

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        assert drone_lib.humanize_age("2026-07-20T12:00:00", now) == "12d ago"


# ── manifest + schema validation ────────────────────────────────────────────


class TestValidateAnswer:
    @pytest.mark.parametrize(
        ("value", "schema"),
        [
            ("yes", {"type": "string", "enum": ["yes", "no"]}),
            (3, {"type": "integer"}),
            (3.5, {"type": "number"}),
            (True, {"type": "boolean"}),
            (["a"], {"type": "array", "items": {"type": "string"}}),
            (
                {"k": 1},
                {"type": "object", "required": ["k"], "properties": {"k": {"type": "integer"}}},
            ),
            (None, {"type": "null"}),
        ],
    )
    def test_conforming_values(self, value: Any, schema: dict[str, Any]) -> None:
        assert drone_lib.validate_answer(value, schema) == ""

    @pytest.mark.parametrize(
        ("value", "schema", "fragment"),
        [
            ("maybe", {"type": "string", "enum": ["yes", "no"]}, "enum"),
            (True, {"type": "integer"}, "boolean"),
            (True, {"type": "number"}, "boolean"),
            ("3", {"type": "integer"}, "expected integer"),
            ([1], {"type": "array", "items": {"type": "string"}}, "item 0"),
            ({}, {"type": "object", "required": ["k"]}, "missing required key"),
            ({"k": "x"}, {"type": "object", "properties": {"k": {"type": "integer"}}}, "key 'k'"),
            ("x", {"type": "nonsense"}, "not one of"),
        ],
    )
    def test_rejected_values(self, value: Any, schema: dict[str, Any], fragment: str) -> None:
        assert fragment in drone_lib.validate_answer(value, schema)

    def test_a_non_object_schema_is_rejected(self) -> None:
        assert drone_lib.validate_answer("x", "not-a-schema")  # type: ignore[arg-type]


class TestValidateManifest:
    def _valid(self) -> dict[str, Any]:
        manifest = json.loads(json.dumps(DRAFT))
        manifest.update(
            {
                "schema": drone_lib.MANIFEST_SCHEMA_VERSION,
                "name": "import-graph",
                "description": DESCRIPTION,
                "author": {"model": "m", "date": "2026-08-01T00:00:00+00:00", "commit": "abc"},
            }
        )
        return manifest

    def test_the_valid_manifest_validates(self) -> None:
        drone_lib.validate_manifest(self._valid())

    @pytest.mark.parametrize(
        "missing",
        [
            "schema",
            "name",
            "description",
            "purpose",
            "author",
            "assumed_surface",
            "capabilities",
            "questions",
            "smoke",
        ],
    )
    def test_every_required_field_is_required(self, missing: str) -> None:
        manifest = self._valid()
        del manifest[missing]
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert missing in exc.value.message

    def test_a_future_schema_version_is_refused(self) -> None:
        manifest = self._valid()
        manifest["schema"] = drone_lib.MANIFEST_SCHEMA_VERSION + 1
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert "schema version" in exc.value.message

    def test_blank_provenance_is_refused(self) -> None:
        manifest = self._valid()
        manifest["author"]["model"] = "  "
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert "author.model" in exc.value.message

    def test_duplicate_question_ids_are_refused(self) -> None:
        manifest = self._valid()
        manifest["questions"].append(json.loads(json.dumps(manifest["questions"][0])))
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert "twice" in exc.value.message

    def test_a_question_with_no_typed_answer_space_is_refused(self) -> None:
        """A free-text hole is a slow subagent with extra steps."""
        manifest = self._valid()
        manifest["questions"][0]["schema"] = {"description": "anything you like"}
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert "neither 'type' nor 'enum'" in exc.value.message

    def test_an_answer_for_an_undeclared_question_is_refused(self) -> None:
        manifest = self._valid()
        manifest["smoke"]["answers"]["typo_id"] = "x"
        with pytest.raises(drone_lib.DroneError) as exc:
            drone_lib.validate_manifest(manifest)
        assert "undeclared question" in exc.value.message

    def test_assumed_surface_entries_need_a_kind_and_value(self) -> None:
        manifest = self._valid()
        manifest["assumed_surface"] = [{"value": "some/path"}]
        with pytest.raises(drone_lib.DroneError):
            drone_lib.validate_manifest(manifest)


class TestFindDronesDir:
    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(drone_lib.DRONES_DIR_ENV, str(tmp_path / "elsewhere"))
        assert drone_lib.find_drones_dir() == tmp_path / "elsewhere"

    def test_walks_up_to_the_repo_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(drone_lib.DRONES_DIR_ENV, raising=False)
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert drone_lib.find_drones_dir(nested) == tmp_path / drone_lib.DRONES_DIRNAME

    def test_falls_back_to_the_start_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(drone_lib.DRONES_DIR_ENV, raising=False)
        # tmp_path has no .git anywhere above it inside the temp root, but the
        # real filesystem root might; assert only that a path is returned.
        assert drone_lib.find_drones_dir(tmp_path).name == drone_lib.DRONES_DIRNAME


class TestGitCommit:
    def test_a_non_repo_yields_empty_rather_than_raising(self, tmp_path: Path) -> None:
        assert drone_lib.git_commit(tmp_path / "nowhere") == ""


# ── the CLI surface ─────────────────────────────────────────────────────────


@pytest.fixture()
def cli_drones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(drone_lib.DRONES_DIR_ENV, raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "embodiment" / "cli" / "_commands").mkdir(parents=True)
    return root / drone_lib.DRONES_DIRNAME


@pytest.fixture()
def drones_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn drones on for one test, the way an operator would.

    Deliberately NOT autouse: the suite's default is a fresh checkout with the
    switch unset (``conftest.py`` strips it), so every CLI test that runs a
    drone has to ask for it here — and the ones that do not are proving the
    refusal.
    """
    monkeypatch.setenv(drone_lib.DRONES_ENABLED_ENV, "1")


def _create_argv(cli_drones: Path, source: Path, manifest: Path, name: str = "import-graph"):
    return [
        "drone",
        "create",
        name,
        "--source",
        str(source),
        "--description",
        DESCRIPTION,
        "--manifest",
        str(manifest),
        "--drones-dir",
        str(cli_drones),
        "--author-model",
        "test-model",
        "--commit",
        "abc1234",
    ]


@pytest.fixture()
def staged(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "drone.py"
    source.write_text(GOOD_SOURCE, encoding="utf-8")
    manifest = tmp_path / "draft.json"
    manifest.write_text(json.dumps(DRAFT), encoding="utf-8")
    return source, manifest


class TestDroneCli:
    def test_bare_noun_prints_the_overview(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["drone"]) == 0
        assert "# embodiment drone" in capsys.readouterr().out

    def test_overview_states_the_threat_model(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["drone", "overview"]) == 0
        out = capsys.readouterr().out
        assert "NO sandbox" in out
        assert "Threat model" in out

    def test_overview_json_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["drone", "overview", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["subject"] == "embodiment drone"
        assert payload["sections"]

    def test_create_then_list_then_evoke(
        self,
        cli_drones: Path,
        staged: tuple[Path, Path],
        drones_on: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source, manifest = staged
        assert main(_create_argv(cli_drones, source, manifest)) == 0
        assert "created drone: import-graph" in capsys.readouterr().out

        assert main(["drone", "list", "--drones-dir", str(cli_drones)]) == 0
        listing = capsys.readouterr().out
        assert "import-graph" in listing
        assert DESCRIPTION in listing
        # `list` re-checked the assumed surface; the fixture root satisfies it.
        assert drone_lib.STATUS_OK in listing

        answers = cli_drones.parent / "answers.json"
        answers.write_text(json.dumps({"is_cycle_intentional": "yes"}), encoding="utf-8")
        rc = main(
            [
                "drone",
                "evoke",
                "import-graph",
                "--drones-dir",
                str(cli_drones),
                "--answers",
                str(answers),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "cycles: yes" in out
        assert "1 asked, 1 accepted" in out

    def test_create_failure_writes_nothing_and_prints_a_hint(
        self, cli_drones: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def run(request):\n    return {}\n", encoding="utf-8")
        manifest = tmp_path / "draft.json"
        manifest.write_text(json.dumps(DRAFT), encoding="utf-8")
        rc = main(_create_argv(cli_drones, source, manifest))
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == "", "errors must never reach stdout"
        assert captured.err.startswith("error:")
        assert "hint:" in captured.err
        assert not (cli_drones / "import-graph").exists()

    def test_create_json_shape(
        self, cli_drones: Path, staged: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, manifest = staged
        assert main([*_create_argv(cli_drones, source, manifest), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["name"] == "import-graph"
        assert payload["description"] == DESCRIPTION
        assert payload["smoke"]["passed"] is True
        assert payload["capabilities"] == ["read_repo"]

    def test_create_json_error_is_json_on_stderr(
        self, cli_drones: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def run(request):\n    return {}\n", encoding="utf-8")
        manifest = tmp_path / "draft.json"
        manifest.write_text(json.dumps(DRAFT), encoding="utf-8")
        rc = main([*_create_argv(cli_drones, source, manifest), "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        error = json.loads(captured.err)
        assert error["code"] == 1
        assert error["remediation"]

    def test_list_empty_is_success_not_an_error(
        self, cli_drones: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["drone", "list", "--drones-dir", str(cli_drones)]) == 0
        captured = capsys.readouterr()
        assert "no drones" in captured.out
        assert captured.err == ""

    def test_list_json_shape(
        self, cli_drones: Path, staged: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        assert main(["drone", "list", "--drones-dir", str(cli_drones), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["drones_dir"] == str(cli_drones)
        (record,) = payload["drones"]
        assert set(record) == {
            "name",
            "description",
            "authored",
            "age",
            "status",
            "model",
            "commit",
            "problem",
        }

    def test_list_reports_a_broken_drone_on_stderr_and_still_lists_it(
        self, cli_drones: Path, staged: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        (cli_drones / "import-graph" / "manifest.json").write_text("nope", encoding="utf-8")
        assert main(["drone", "list", "--drones-dir", str(cli_drones)]) == 0
        captured = capsys.readouterr()
        assert "import-graph" in captured.out
        assert drone_lib.STATUS_BROKEN in captured.out
        assert captured.err.startswith("warning:")

    def test_evoke_with_no_worker_warns_on_stderr_and_reports_i_cannot(
        self,
        cli_drones: Path,
        staged: tuple[Path, Path],
        drones_on: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        assert main(["drone", "evoke", "import-graph", "--drones-dir", str(cli_drones)]) == 0
        captured = capsys.readouterr()
        assert "I cannot:" in captured.out
        assert "no worker seam is wired" in captured.err

    def test_evoke_json_reports_call_acceptance_as_its_own_axis(
        self,
        cli_drones: Path,
        staged: tuple[Path, Path],
        drones_on: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        answers = cli_drones.parent / "answers.json"
        answers.write_text(json.dumps({"is_cycle_intentional": "no"}), encoding="utf-8")
        rc = main(
            [
                "drone",
                "evoke",
                "import-graph",
                "--drones-dir",
                str(cli_drones),
                "--answers",
                str(answers),
                "--json",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["answer"] == "cycles: no"
        assert payload["calls_asked"] == 1
        assert payload["calls_accepted"] == 1
        assert payload["call_acceptance"] == 1.0
        assert len(payload["source_sha256"]) == 64
        assert payload["capabilities"] == ["read_repo"]

    def test_evoke_unknown_drone_errors_with_a_hint(
        self, cli_drones: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["drone", "evoke", "nope", "--drones-dir", str(cli_drones)])
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "hint:" in captured.err

    def test_evoke_of_a_later_broken_drone_never_leaks_a_traceback(
        self,
        cli_drones: Path,
        staged: tuple[Path, Path],
        drones_on: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        (cli_drones / "import-graph" / "drone.py").write_text(
            "def run(request):\n    raise RuntimeError('rot')\n", encoding="utf-8"
        )
        rc = main(["drone", "evoke", "import-graph", "--drones-dir", str(cli_drones)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "RuntimeError" in captured.err
        assert "hint:" in captured.err

    def test_bad_arg_pair_errors_cleanly(
        self, cli_drones: Path, staged: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, manifest = staged
        main(_create_argv(cli_drones, source, manifest))
        capsys.readouterr()
        rc = main(
            [
                "drone",
                "evoke",
                "import-graph",
                "--drones-dir",
                str(cli_drones),
                "--arg",
                "novalue",
            ]
        )
        assert rc == 1
        assert "KEY=VALUE" in capsys.readouterr().err

    def test_an_unreadable_manifest_is_an_environment_error(
        self, cli_drones: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "drone.py"
        source.write_text(GOOD_SOURCE, encoding="utf-8")
        rc = main(_create_argv(cli_drones, source, tmp_path / "missing.json"))
        assert rc == 2
        assert "hint:" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "argv",
        [
            ["drone", "overview"],
            ["drone", "list"],
            ["drone", "create", "x", "--source", "y"],
            ["drone", "evoke", "x"],
        ],
    )
    def test_every_verb_accepts_json(self, argv: list[str]) -> None:
        """The rubric contract: every command takes ``--json``."""
        from embodiment.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args([*argv, "--json"])
        assert args.json is True

    def test_an_unknown_drone_verb_routes_through_the_structured_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["drone", "list", "--bogus"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("error:")
        assert "hint:" in err


class TestExplainCoversTheDroneSurface:
    @pytest.mark.parametrize(
        "path",
        [
            ["drone"],
            ["drone", "overview"],
            ["drone", "create"],
            ["drone", "evoke"],
            ["drone", "list"],
        ],
    )
    def test_entry_exists(self, path: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["explain", *path]) == 0
        assert capsys.readouterr().out.strip().startswith("#")

    def test_explain_drone_states_the_threat_model(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["explain", "drone"]) == 0
        out = capsys.readouterr().out.lower()
        assert "no sandbox" in out
        assert "calling process" in out

    def test_explain_drone_carries_the_break_even_guidance(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure mode is quiet waste, so the economics ship with the verb."""
        assert main(["explain", "drone"]) == 0
        out = capsys.readouterr().out
        assert "break-even" in out
        assert "pure loss" in out
        assert "one cortex turn" in out

    def test_explain_drone_states_the_no_escalation_rule(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["explain", "drone"]) == 0
        assert "I cannot" in capsys.readouterr().out
