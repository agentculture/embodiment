"""Tests for the serial delegate tool (plan task ``t1``).

The three acceptance criteria this file exists to prove, and where:

1. **The child surface excludes parent-level ``finish``, and an un-enumerated
   tool raises ``UnknownToolError`` rather than degrading to a soft success** —
   :class:`TestEnumeratedSurfaces`, :class:`TestUnEnumeratedToolsAreRefused`,
   :class:`TestFinalAuthorityStaysWithTheCortex`.
2. **Zero diffs under ``embodiment/``: the tool rides the existing subagent
   seam from ``examples/``** — :class:`TestRidesTheExistingSeam`.
3. **Per-child ledger attribution (``SOURCE_SUBAGENT``, ``child_task_id``)** —
   :class:`TestLedgerAttribution`.

Everything here is hermetic: scripted minds, no network, no live model, no
endpoint or dial configuration (that is task ``t2``'s file, not this one's).
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - fixed argv, no shell; the zero-diff delivery check
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import ledger
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.framing import CORTEX_MARKER
from embodiment.loop import EXIT_FINISHED, ToolError, ToolOutcome, UnknownToolError, run
from embodiment.subagent import SPAWN_GRANTED, SPAWN_REFUSED_ALLOWANCE
from examples import orchestrator_tools as ot

MODULE = Path(ot.__file__)
REPO_ROOT = MODULE.parent.parent


# ── scripted minds (no network, no live model) ───────────────────────────────


class Scripted:
    """A ``complete`` seam replaying fixed turns; the last one repeats."""

    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}-{len(arguments)}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


def _prose(content: str = "I have nothing to call.") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=[])


def _delegating_cortex(*subtasks: str, finish: str = "orchestrated") -> Scripted:
    """Delegate each subtask in order, then finish. The cortex, and only it."""
    turns = [_turn(_call("delegate", subtask=text)) for text in subtasks]
    turns.append(_turn(_call("finish", summary=finish)))
    return Scripted(*turns)


def _reporting_worker(findings: Any = "the worker's findings") -> Scripted:
    return Scripted(_turn(_call("report", findings=findings)))


def _drive(cortex: Any, worker: Any, **kw: Any) -> ot.DelegationOutcome:
    """Run one orchestration with injected minds and this module's defaults."""
    kw.setdefault("task", ot.orchestrator_task("delegate the subtasks, then answer"))
    return ot.run_delegation(cortex, worker, **kw)


# ── criterion 1: the enumerated surfaces ─────────────────────────────────────


class TestEnumeratedSurfaces:
    """The worker's surface is the pre-registration, and it excludes ``finish``."""

    def test_the_worker_surface_excludes_parent_level_finish(self) -> None:
        assert "finish" in ot.ORCHESTRATOR_TOOLS
        assert "finish" not in ot.WORKER_TOOLS
        assert set(ot.WORKER_TOOLS).isdisjoint(ot.FINAL_AUTHORITY_TOOLS)

    def test_the_worker_surface_is_a_strict_subset(self) -> None:
        assert set(ot.WORKER_TOOLS) < set(ot.ORCHESTRATOR_TOOLS) | set(ot.WORKER_TOOLS)
        withheld = set(ot.ORCHESTRATOR_TOOLS) - set(ot.WORKER_TOOLS)
        assert withheld == {"delegate", "note", "finish"}

    def test_the_worker_cannot_delegate_onwards(self) -> None:
        assert "delegate" not in ot.WORKER_TOOLS

    def test_the_surfaces_are_tuples_a_pre_registration_can_quote(self) -> None:
        assert isinstance(ot.WORKER_TOOLS, tuple)
        assert isinstance(ot.ORCHESTRATOR_TOOLS, tuple)


class TestUnEnumeratedToolsAreRefused:
    """A tool outside the enumeration RAISES; it never returns a cheerful outcome."""

    @pytest.mark.parametrize(
        "name", ["finish", "delegate", "note", "read_file", "write_file", "bash", ""]
    )
    def test_the_worker_refuses_every_un_enumerated_tool(self, name: str) -> None:
        worker = ot.WorkerExecutor(subtask="a scoped job")
        with pytest.raises(UnknownToolError):
            worker.execute(name, {})

    def test_the_refusal_is_not_a_soft_success(self) -> None:
        """Returning ``ToolOutcome(result="unknown tool …")`` would be a SUCCESS
        carrying an error string — the loop would record the step as fine. The
        refusal must be an exception, and the returned object must never exist."""
        worker = ot.WorkerExecutor(subtask="a scoped job")
        try:
            returned = worker.execute("finish", {"summary": "I am the final authority"})
        except UnknownToolError:
            returned = None
        assert returned is None
        assert worker.refused == ["finish"]

    def test_the_workers_own_tool_still_works(self) -> None:
        """The refusal must not be "everything raises"."""
        worker = ot.WorkerExecutor(subtask="a scoped job")
        outcome = worker.execute("report", {"findings": "three of four gauges read low"})
        assert outcome.finished is True
        assert "three of four gauges" in outcome.finish_summary
        assert worker.report == "three of four gauges read low"

    def test_an_empty_report_is_a_self_correcting_step_not_a_finish(self) -> None:
        worker = ot.WorkerExecutor(subtask="a scoped job")
        with pytest.raises(ToolError):
            worker.execute("report", {"findings": "   "})
        assert worker.report == ""

    def test_the_orchestrator_refuses_un_enumerated_tools_too(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        for name in ("report", "bash", "read_file"):
            with pytest.raises(UnknownToolError):
                boss.execute(name, {})

    def test_a_refused_child_tool_costs_one_step_and_the_child_recovers(self) -> None:
        """In a real drive the refusal is one non-ok step, not a new way out."""
        worker = Scripted(
            _turn(_call("finish", summary="I decide")),
            _turn(_call("report", findings="recovered and reported")),
        )
        delivered = _drive(_delegating_cortex("one job"), worker)
        child = delivered.log.runs[0]
        assert child.outcome is not None
        steps = child.outcome.result.steps
        assert steps[0].tool == "finish"
        assert steps[0].ok is False
        assert child.exit_reason == EXIT_FINISHED
        assert child.report == "recovered and reported"
        assert child.refused_tools == ["finish"]


class TestFinalAuthorityStaysWithTheCortex:
    """The worker ends its OWN drive; only the cortex ends the task."""

    def test_a_finished_child_does_not_finish_the_parent(self) -> None:
        cortex = Scripted(_turn(_call("delegate", subtask="one job")), _prose())
        delivered = _drive(cortex, _reporting_worker())
        assert delivered.log.runs[0].exit_reason == EXIT_FINISHED
        assert delivered.executor.finished is False
        assert delivered.outcome.exit_reason != EXIT_FINISHED

    def test_the_final_summary_is_the_cortexs_own_words(self) -> None:
        delivered = _drive(
            _delegating_cortex("one job", finish="the orchestrator's answer"),
            _reporting_worker("the worker's raw notes"),
        )
        assert delivered.outcome.exit_reason == EXIT_FINISHED
        assert delivered.outcome.result.summary == "the orchestrator's answer"
        assert "the worker's raw notes" not in delivered.outcome.result.summary

    def test_the_workers_report_still_reaches_the_cortex(self) -> None:
        """Withholding ``finish`` must not withhold the worker's contribution."""
        delivered = _drive(_delegating_cortex("one job"), _reporting_worker("gauge four is low"))
        results = [step.result for step in delivered.outcome.result.steps]
        assert any("gauge four is low" in text for text in results)

    def test_the_worker_is_framed_as_a_subagent_never_as_the_teammate(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1", identity="Gwen")
        spawn = boss.execute("delegate", {"subtask": "a scoped job"}).spawn
        assert spawn is not None
        assert spawn.role == ot.ROLE_WORKER
        assert spawn.system_prompt is not None
        assert ot.WORKER_SYSTEM in spawn.system_prompt
        assert CORTEX_MARKER not in spawn.system_prompt

    def test_absent_identity_leaves_the_worker_prompt_byte_identical(self) -> None:
        """colleague#352's acceptance criterion, applied to the delegate tool."""
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute("delegate", {"subtask": "a scoped job"}).spawn
        assert spawn is not None
        assert spawn.system_prompt == ot.WORKER_SYSTEM

    def test_the_orchestrator_alone_is_framed_as_the_cortex(self) -> None:
        """Both roles get the same identity; only one gets cortex authority."""
        framed = ot.orchestrator_prompt(identity="Gwen")
        assert CORTEX_MARKER in framed
        assert ot.ORCHESTRATOR_SYSTEM in framed

    def test_absent_identity_leaves_the_orchestrator_prompt_byte_identical(self) -> None:
        assert ot.orchestrator_prompt() == ot.ORCHESTRATOR_SYSTEM
        assert ot.orchestrator_prompt(identity="") == ot.ORCHESTRATOR_SYSTEM

    def test_the_worker_is_a_leaf(self) -> None:
        """The delegate tool asks for zero onward spawns; the seam grants zero."""
        delivered = _drive(_delegating_cortex("one job"), _reporting_worker())
        granted = [record for record in delivered.outcome.spawns if record.granted]
        assert granted[0].allowance_requested == 0
        assert granted[0].allowance_granted == 0


class TestNoRepoAccess:
    """c36: containment is the arm design's, not the advert's."""

    def test_the_worker_task_carries_no_repo_path(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute("delegate", {"subtask": "a scoped job"}).spawn
        assert spawn is not None
        assert spawn.task.repo_path == ""

    def test_neither_surface_names_a_repo_or_shell_tool(self) -> None:
        forbidden = {"bash", "shell", "exec", "read_file", "write_file", "edit", "run_command"}
        assert forbidden.isdisjoint(ot.WORKER_TOOLS)
        assert forbidden.isdisjoint(ot.ORCHESTRATOR_TOOLS)

    def test_the_module_imports_no_process_or_filesystem_root(self) -> None:
        forbidden = {"subprocess", "os", "pathlib", "shutil", "socket", "glob", "tempfile"}
        roots = {module.split(".")[0] for module, _ in _imports(_tree())}
        assert forbidden.isdisjoint(roots), f"unexpected import roots: {roots & forbidden}"


# ── serial delegation and the bounds that were already there ─────────────────


class TestSerialDelegation:
    def test_children_run_one_at_a_time(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), _reporting_worker())
        assert delivered.log.max_in_flight == 1
        assert len(delivered.log.runs) == 2

    def test_each_child_gets_its_own_task_id(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), _reporting_worker())
        ids = [child.child_task_id for child in delivered.log.runs]
        assert ids == ["orchestrator-1-worker-1", "orchestrator-1-worker-2"]

    def test_each_child_carries_its_own_subtask(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), _reporting_worker())
        assert [child.subtask for child in delivered.log.runs] == ["first job", "second job"]

    def test_lineage_names_the_parent_and_the_depth_is_one(self) -> None:
        delivered = _drive(_delegating_cortex("one job"), _reporting_worker())
        child = delivered.log.runs[0]
        assert child.lineage == ("orchestrator-1",)
        assert child.depth == 1

    def test_the_allowance_bound_refuses_the_delegation_past_it(self) -> None:
        cortex = _delegating_cortex("one", "two", "three")
        delivered = _drive(cortex, _reporting_worker(), spawn_allowance=2)
        outcomes = [record.outcome for record in delivered.outcome.spawns]
        assert outcomes == [SPAWN_GRANTED, SPAWN_GRANTED, SPAWN_REFUSED_ALLOWANCE]
        assert len(delivered.log.runs) == 2

    def test_child_turns_are_charged_to_the_parent(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), _reporting_worker())
        charged = sum(child.model_turns for child in delivered.log.runs)
        assert charged > 0
        assert delivered.outcome.child_model_turns == charged

    def test_a_delegation_with_no_subtask_is_a_self_correcting_step(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        with pytest.raises(ToolError):
            boss.execute("delegate", {"subtask": "  "})
        assert boss.delegations == 0

    def test_the_orchestrators_own_note_tool_is_not_a_delegation(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        outcome = boss.execute("note", {"text": "worth writing down"})
        assert outcome.spawn is None
        assert boss.notes == ["worth writing down"]


# ── criterion 3: per-child ledger attribution ────────────────────────────────


def _records(delivered: ot.DelegationOutcome) -> list[Any]:
    return delivered.records()


class TestLedgerAttribution:
    """ "Who degraded?" is answerable without inference."""

    def test_a_child_degradation_names_the_child_and_the_subagent_lane(self) -> None:
        """A silent worker is a degradation this host mints — and it is attributed."""
        delivered = _drive(_delegating_cortex("one job"), Scripted(_prose()))
        hits = [
            record for record in _records(delivered) if record.code == ot.DEGRADED_WORKER_NO_REPORT
        ]
        assert len(hits) == 1
        assert hits[0].source == ledger.SOURCE_SUBAGENT
        assert hits[0].child_task_id == "orchestrator-1-worker-1"

    def test_two_children_are_told_apart(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), Scripted(_prose()))
        attributed = {
            record.child_task_id
            for record in _records(delivered)
            if record.code == ot.DEGRADED_WORKER_NO_REPORT
        }
        assert attributed == {"orchestrator-1-worker-1", "orchestrator-1-worker-2"}

    def test_a_loop_minted_child_degradation_keeps_its_lane_and_gains_the_child(self) -> None:
        """A loop code stays a loop code; the child attribution rides beside it."""
        worker = _reporting_worker(findings={"a set is not JSON"})
        delivered = _drive(_delegating_cortex("one job"), worker)
        hits = [
            record
            for record in _records(delivered)
            if record.code == "tool-arguments-unserializable"
        ]
        assert hits, "expected the child's own loop to mint a degradation"
        assert hits[0].source == ledger.SOURCE_LOOP
        assert hits[0].child_task_id == "orchestrator-1-worker-1"

    def test_the_parents_own_degradation_carries_no_child_id(self) -> None:
        """An unwired seam degrades the PARENT; nothing may pin it on a child."""
        outcome = run(
            _delegating_cortex("one job"),
            ot.orchestrator_task("delegate the subtask, then answer"),
            executor=ot.OrchestratorExecutor(task_id="orchestrator-1"),
            max_steps=6,
            spawn_allowance=2,
            subagent=None,
        )
        records = ledger.read(loop=outcome)
        hits = [record for record in records if record.code == "subagent-seam-absent"]
        assert len(hits) == 1
        assert hits[0].child_task_id is None

    def test_the_spawn_record_carries_the_role_and_the_child_task_id(self) -> None:
        delivered = _drive(_delegating_cortex("one job"), _reporting_worker())
        record = delivered.outcome.spawns[0]
        assert record.role == ot.ROLE_WORKER
        assert record.child_task_id == "orchestrator-1-worker-1"
        assert record.to_dict()["child_task_id"] == "orchestrator-1-worker-1"

    def test_attribution_is_reported_as_a_mapping_not_left_to_inference(self) -> None:
        delivered = _drive(_delegating_cortex("first job", "second job"), Scripted(_prose()))
        attribution = delivered.report()["attribution"]
        assert set(attribution) == {"orchestrator-1-worker-1", "orchestrator-1-worker-2"}
        for codes in attribution.values():
            assert ot.DEGRADED_WORKER_NO_REPORT in codes

    def test_a_clean_run_attributes_nothing(self) -> None:
        delivered = _drive(_delegating_cortex("one job"), _reporting_worker())
        assert delivered.report()["attribution"] == {}
        assert [record.code for record in _records(delivered)] == []

    def test_this_hosts_codes_are_declared_not_ad_hoc(self) -> None:
        assert ot.DEGRADED_WORKER_NO_REPORT in ot.WORKER_DEGRADATIONS
        assert ot.DEGRADED_WORKER_ABORTED in ot.WORKER_DEGRADATIONS
        embodiment_codes = {entry.code for entry in ledger.known_codes()}
        assert set(ot.WORKER_DEGRADATIONS).isdisjoint(embodiment_codes)

    def test_a_worker_whose_mind_raises_degrades_rather_than_aborting_the_parent(self) -> None:
        class Exploding:
            def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
                raise RuntimeError("the worker endpoint fell over")

        delivered = _drive(_delegating_cortex("one job"), Exploding())
        assert delivered.outcome.exit_reason == EXIT_FINISHED
        hits = [
            record for record in _records(delivered) if record.code == ot.DEGRADED_WORKER_ABORTED
        ]
        assert len(hits) == 1
        assert hits[0].child_task_id == "orchestrator-1-worker-1"


# ── criterion 2: it rides the existing seam, and changes nothing under it ────


def _tree() -> ast.AST:
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _imports(tree: ast.AST) -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", tuple(a.name for a in node.names)))
    return found


def _branch() -> Optional[str]:
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - git absent
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


class TestRidesTheExistingSeam:
    def test_the_delegate_tool_asks_through_a_spawn_request(self) -> None:
        boss = ot.OrchestratorExecutor(task_id="orchestrator-1")
        outcome = boss.execute("delegate", {"subtask": "a scoped job"})
        assert isinstance(outcome, ToolOutcome)
        assert outcome.spawn is not None
        assert outcome.finished is False

    def test_the_module_mutates_nothing_inside_embodiment(self) -> None:
        """Riding the seam means READING it: no monkeypatching, no setattr."""
        roots = {"embodiment", "ledger", "loop", "subagent", "framing", "contract"}
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", "")
                assert name != "setattr", "the example must not patch embodiment"
            if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Attribute):
                    continue
                owner = target.value
                assert not (
                    isinstance(owner, ast.Name) and owner.id in roots
                ), f"the example assigns to {owner.id}.{target.attr}"

    def test_no_private_embodiment_name_is_imported(self) -> None:
        for module, names in _imports(_tree()):
            if module.split(".")[0] != "embodiment":
                continue
            for name in names:
                assert not name.startswith("_"), f"private import {name!r}"

    def test_no_embodiment_source_changed_on_this_branch(self) -> None:
        """Criterion 2 as a delivery check.

        Scoped to ``owa/t1`` on purpose: the zero-diff rule is *this task's*
        contract, not a permanent repo rule, so the check must not fire on a
        later branch that legitimately changes ``embodiment/``. Off-branch, or
        with no git / no ``main`` to compare against, it skips rather than
        pretending to have verified something.
        """
        if _branch() != "owa/t1":
            pytest.skip("the zero-diff delivery check only runs on owa/t1")
        proc = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
            ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "main...HEAD", "--", "embodiment"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:  # pragma: no cover - shallow clone, no `main`
            pytest.skip("no `main` to diff against")
        changed = [line for line in proc.stdout.splitlines() if line.strip()]
        assert changed == [], f"task t1 must not change embodiment/: {changed}"


# ── the runnable demonstration ───────────────────────────────────────────────


class TestDemo:
    def test_the_demo_runs_hermetically(self) -> None:
        report = ot.run_demo()
        assert report["orchestrator"]["exit_reason"] == EXIT_FINISHED
        assert report["orchestrator"]["delegations"] == len(ot.DEMO_SUBTASKS)

    def test_the_demo_shows_a_grant_a_degradation_and_the_bound(self) -> None:
        report = ot.run_demo()
        outcomes = [spawn["outcome"] for spawn in report["spawns"]]
        assert SPAWN_GRANTED in outcomes
        assert SPAWN_REFUSED_ALLOWANCE in outcomes
        assert report["attribution"], "the demo should show attribution at work"

    def test_the_report_is_json_serializable(self) -> None:
        loaded = json.loads(json.dumps(ot.run_demo(), indent=2))
        assert loaded["worker"]["tools"] == list(ot.WORKER_TOOLS)
        assert "finish" in loaded["worker"]["withheld_tools"]

    def test_main_emits_json_on_request(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert ot.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["orchestrator"]["exit_reason"] == EXIT_FINISHED

    def test_main_emits_a_readable_report_by_default(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert ot.main([]) == 0
        text = capsys.readouterr().out
        assert "SERIAL DELEGATION" in text
        assert "finish" in text


# ── the injected seam: no dial configuration lives here (task t2 owns it) ────


class TestTheWorkerMindIsInjected:
    def test_run_delegation_takes_both_minds_as_arguments(self) -> None:
        cortex, worker = _delegating_cortex("one job"), _reporting_worker()
        delivered = _drive(cortex, worker)
        assert cortex.calls > 0 and worker.calls > 0
        assert delivered.log.runs[0].model == ""

    def test_the_module_reads_no_environment_and_names_no_endpoint(self) -> None:
        source = MODULE.read_text(encoding="utf-8")
        for token in ("environ", "getenv", "http://", "https://", "localhost", "base_url"):
            assert token not in source, f"dial configuration ({token!r}) belongs to task t2"

    def test_the_model_label_is_recorded_for_traces_never_dialled(self) -> None:
        delivered = _drive(
            _delegating_cortex("one job"),
            _reporting_worker(),
            cortex_model="the-cortex-label",
            worker_model="a-model-label",
        )
        assert delivered.log.runs[0].model == "a-model-label"
        assert delivered.outcome.result.sub_results[0].model == "a-model-label"
        assert delivered.outcome.result.sub_results[0].role == ot.ROLE_WORKER
        assert delivered.outcome.result.sub_results[0].parent == "orchestrator-1"

    def test_both_levels_name_their_own_model_in_the_trace(self) -> None:
        """h9: a trace exposes the actual role and model behind a contribution."""
        delivered = _drive(
            _delegating_cortex("one job"),
            _reporting_worker(),
            cortex_model="the-cortex-label",
            worker_model="the-worker-label",
        )
        report = delivered.report()
        assert report["orchestrator"]["model"] == "the-cortex-label"
        assert [record["model"] for record in report["worker"]["runs"]] == ["the-worker-label"]
        assert [record["role"] for record in report["worker"]["runs"]] == [ot.ROLE_WORKER]


def test_the_task_helper_builds_a_repo_less_task() -> None:
    task = ot.orchestrator_task("do the thing")
    assert isinstance(task, Task)
    assert task.repo_path == ""
