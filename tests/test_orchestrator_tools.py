"""Tests for the delegate tools — serial (task ``t1``) and fan-out (task ``t4``).

Task ``t1``'s three acceptance criteria, and where they are proved:

1. **The child surface excludes parent-level ``finish``, and an un-enumerated
   tool raises ``UnknownToolError`` rather than degrading to a soft success** —
   :class:`TestEnumeratedSurfaces`, :class:`TestUnEnumeratedToolsAreRefused`,
   :class:`TestFinalAuthorityStaysWithTheCortex`.
2. **Zero diffs under ``embodiment/``: the tool rides the existing subagent
   seam from ``examples/``** — :class:`TestRidesTheExistingSeam`.
3. **Per-child ledger attribution (``SOURCE_SUBAGENT``, ``child_task_id``)** —
   :class:`TestLedgerAttribution`.

Task ``t4``'s three, which are about the **parallel fan-out** and are harder,
because the subagent seam was designed for serial spawns and the accounting,
partial-failure and termination semantics of a wide concurrent fan-out were
simply undefined before this task defined them:

4. **Bounded total charge**, stated as :data:`ot.FANOUT_ACCOUNTING_RULE` and
   proved clause by clause — :class:`TestTheAccountingRuleIsStated`,
   :class:`TestThePartitionIsExact`, :class:`TestThePlanCommitsNoMoreThanTheGrant`,
   :class:`TestTheBoundedTotalCharge`, :class:`TestTheChargeCannotBeRaced`.
5. **A failed or absent unit lands a recorded degradation plus partial
   results**, never an abort of the parent (C3) —
   :class:`TestPartialFailureIsRecordedNotFatal`,
   :class:`TestFanoutLedgerAttribution`.
6. **Termination preserved, proved STRUCTURALLY** — :class:`TestFanoutTerminates`
   reads the source the way ``tests/test_muse_tool_loop_ast.py`` does. A passing
   run does not prove a bound; these pins prove no other path exists.

Everything here is hermetic: scripted minds, no network, no live model, no
endpoint or dial configuration (that is task ``t2``'s file, not this one's).
The concurrency tests use real threads and real deadlines — a fan-out proved
only against a fake scheduler proves nothing about the one it will run on —
but every blocking mind is released from a ``finally`` and carries its own
outer timeout, so a failing assertion can never leave a thread parked.
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - fixed argv, no shell; the zero-diff delivery check
import threading
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
        assert cortex.calls > 0
        assert worker.calls > 0
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


# ═════════════════════════════════════════════════════════════════════════════
# task t4 — the parallel fan-out
#
# Everything below proves one of three things, and the file's docstring says
# which is which. Read `ot.FANOUT_ACCOUNTING_RULE` before this section: these
# tests are that rule, clause by clause, and the rule is what a pre-registration
# quotes.
# ═════════════════════════════════════════════════════════════════════════════


FANOUT_INSTRUCTION = "delegate the subtasks in parallel, then answer"

#: The domain the partition is proved over. Exhaustive rather than sampled: a
#: property checked on three inputs is an anecdote.
GRANTS = tuple(range(0, 41))
WIDTHS = tuple(range(1, 21))


def _jobs(count: int) -> tuple[str, ...]:
    return tuple(f"job {index}" for index in range(count))


def _fanning_cortex(*subtasks: str, finish: str = "orchestrated") -> Scripted:
    """Fan the whole batch out in ONE tool call, then finish. The cortex, and only it."""
    return Scripted(
        _turn(_call(ot.FANOUT_TOOL, subtasks=list(subtasks))),
        _turn(_call("finish", summary=finish)),
    )


def _fan(cortex: Any, worker: Any, **kw: Any) -> ot.FanoutOutcome:
    """Run one fan-out orchestration with injected minds and a bounded deadline."""
    kw.setdefault("task", ot.orchestrator_task(FANOUT_INSTRUCTION))
    kw.setdefault("timeout", 10.0)
    return ot.run_fanout(cortex, worker, **kw)


def _await(predicate: Any, seconds: float = 5.0) -> bool:
    """Poll *predicate* under a hard ceiling. Never an unbounded wait, even here."""
    import time

    for _ in range(int(seconds / 0.005)):
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ── worker minds, thread-safe by construction ────────────────────────────────


class Reporting:
    """Reports on its first turn, from any thread. The happy unit."""

    def __init__(self, findings: str = "the unit's findings") -> None:
        self.findings = findings
        self.lock = threading.Lock()
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        with self.lock:
            self.calls += 1
        return _turn(_call("report", findings=self.findings))


class Restless:
    """Calls ``report`` with nothing in it, forever.

    Each call is a :class:`ToolError` — a self-correcting step, not an exit — so
    a unit driven by this mind spends **exactly** its slice and never finishes.
    That is what makes the charge tests tight rather than vacuous: the bound is
    saturated on every run, so an off-by-one in the accounting shows up as an
    overspend rather than hiding under an under-spend.

    With a *barrier*, every thread parks at its first turn until all of them
    have arrived — which turns "these units ran concurrently" from a hope into
    a fact the scheduler cannot take away.
    """

    def __init__(self, barrier: Any = None) -> None:
        self.barrier = barrier
        self.local = threading.local()
        self.lock = threading.Lock()
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        with self.lock:
            self.calls += 1
        if self.barrier is not None and not getattr(self.local, "arrived", False):
            self.local.arrived = True
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return _turn(_call("report", findings="   "))


class Blocking:
    """Reports — unless the subtask names the marker, then it parks on *gate*.

    The inner ``timeout`` is a second belt: a test that fails before its
    ``finally`` still cannot leave a thread parked at interpreter exit.
    """

    def __init__(self, gate: Any, marker: str) -> None:
        self.gate = gate
        self.marker = marker

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        text = " ".join(str(message.get("content") or "") for message in messages)
        if self.marker in text:
            self.gate.wait(timeout=30)
        return _turn(_call("report", findings="reported"))


class Failing:
    """Falls over on the marked subtask; reports on the others."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        text = " ".join(str(message.get("content") or "") for message in messages)
        if self.marker in text:
            raise RuntimeError("the worker endpoint fell over")
        return _turn(_call("report", findings="reported"))


class Mixed:
    """One mind, three behaviours: report, stay silent, or park until released."""

    def __init__(self, gate: Any) -> None:
        self.gate = gate

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        text = " ".join(str(message.get("content") or "") for message in messages)
        if "stuck" in text:
            self.gate.wait(timeout=30)
        if "silent" in text:
            return _prose("I have nothing to call.")
        return _turn(_call("report", findings="reported"))


class Silent:
    """Answers in prose and calls nothing, so every unit ends with no report."""

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        return _prose("There is nothing here to work with.")


class SilentAtBarrier(Silent):
    """:class:`Silent`, but every unit starts together — so all four finish in
    whatever order the scheduler picks, which is the point of the test that
    uses it."""

    def __init__(self, barrier: Any) -> None:
        self.barrier = barrier
        self.local = threading.local()

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        if not getattr(self.local, "arrived", False):
            self.local.arrived = True
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return super().__call__(messages)


# ── criterion 4a: the rule is STATED, not merely obeyed ──────────────────────


class TestTheAccountingRuleIsStated:
    """The rule is a module constant a pre-registration can quote verbatim.

    Behaviour that is correct but undocumented is not a contract; the point of
    this task was to *define* semantics that did not exist, so the definition
    has to be a thing you can point at.
    """

    def test_the_rule_is_a_quotable_module_constant(self) -> None:
        assert isinstance(ot.FANOUT_ACCOUNTING_RULE, str)
        assert len(ot.FANOUT_ACCOUNTING_RULE.strip().splitlines()) >= 20

    @pytest.mark.parametrize(
        "clause",
        [
            "partition",
            "before any of them starts",
            "NOT DISPATCHED",
            "WHOLE slice",
            "borrow",
            "call.max_steps",
            "MAX_FANOUT_WIDTH",
        ],
    )
    def test_the_rule_states_every_clause_these_tests_prove(self, clause: str) -> None:
        assert clause in ot.FANOUT_ACCOUNTING_RULE

    def test_a_run_carries_its_own_contract(self) -> None:
        """The artifact ships the rule, so a recorded run is self-describing."""
        report = ot.run_fanout_demo()
        assert report["fanout"]["rule"] == ot.FANOUT_ACCOUNTING_RULE


# ── criterion 4b: the partition, proved exhaustively ─────────────────────────


class TestThePartitionIsExact:
    """Clause 1. The only producer of a unit's budget, and it is exact.

    Proved over the whole practical domain rather than sampled — for these input
    sizes this is a proof, not evidence.
    """

    def test_every_partition_sums_to_exactly_the_grant(self) -> None:
        for grant in GRANTS:
            for width in WIDTHS:
                slices = ot.partition(grant, width)
                assert len(slices) == width, (grant, width)
                assert sum(slices) == grant, (grant, width, slices)

    def test_no_slice_is_ever_negative(self) -> None:
        for grant in GRANTS:
            for width in WIDTHS:
                assert min(ot.partition(grant, width)) >= 0, (grant, width)

    def test_slices_differ_by_at_most_one(self) -> None:
        """Fair by construction: nobody is starved so a sibling can be fat."""
        for grant in GRANTS:
            for width in WIDTHS:
                slices = ot.partition(grant, width)
                assert max(slices) - min(slices) <= 1, (grant, width, slices)

    def test_a_width_beyond_the_grant_leaves_the_tail_at_zero(self) -> None:
        assert ot.partition(3, 5) == (1, 1, 1, 0, 0)
        assert ot.partition(7, 3) == (3, 2, 2)

    def test_a_zero_or_negative_width_is_an_empty_partition(self) -> None:
        assert ot.partition(9, 0) == ()
        assert ot.partition(9, -3) == ()

    def test_a_malformed_grant_funds_nothing_rather_than_everything(self) -> None:
        """``as_count``'s safe direction, inherited: unreadable means none."""
        assert ot.partition(-4, 3) == (0, 0, 0)
        assert ot.partition(None, 3) == (0, 0, 0)  # type: ignore[arg-type]
        assert ot.partition("nonsense", 3) == (0, 0, 0)  # type: ignore[arg-type]


class TestThePlanCommitsNoMoreThanTheGrant:
    """Clause 2. The plan is settled before any thread exists, and it is honest."""

    def test_the_plan_never_commits_more_than_the_grant(self) -> None:
        for grant in range(0, 25):
            for width in range(0, 20):
                plan = ot.plan_fanout(_jobs(width), grant=grant, stem="b")
                assert plan.committed <= grant, (grant, width)

    def test_no_dispatched_unit_is_unfunded(self) -> None:
        for grant in range(0, 25):
            for width in range(0, 20):
                plan = ot.plan_fanout(_jobs(width), grant=grant, stem="b")
                for unit in plan.dispatched:
                    assert unit.grant >= ot.MIN_UNIT_GRANT, (grant, width, unit)

    def test_a_zero_slice_is_a_refusal_and_never_a_dispatch(self) -> None:
        """The trap :data:`ot.MIN_UNIT_GRANT` exists for.

        ``embodiment.loop.run`` sets ``turn_budget = max(1, max_steps)``, so a
        unit dispatched on a zero-turn slice still spends one model turn. A wide
        fan-out of zero-slice units would therefore outspend its grant by the
        width — the exact failure this bound is here to stop.
        """
        plan = ot.plan_fanout(("a", "b", "c"), grant=2, stem="b")
        assert [unit.grant for unit in plan.units] == [1, 1, 0]
        assert [unit.refusal for unit in plan.units] == ["", "", ot.REFUSED_UNFUNDED]
        assert len(plan.dispatched) == 2

    def test_units_beyond_the_width_limit_are_refused_not_dropped(self) -> None:
        subtasks = _jobs(ot.MAX_FANOUT_WIDTH + 3)
        plan = ot.plan_fanout(subtasks, grant=500, stem="b")
        assert plan.width == len(subtasks)
        assert len(plan.units) == len(subtasks), "a truncated unit would vanish silently"
        assert len(plan.dispatched) == ot.MAX_FANOUT_WIDTH
        assert {unit.refusal for unit in plan.refused} == {ot.REFUSED_OVER_WIDTH}

    def test_a_refusal_is_the_bound_working_and_is_not_a_degradation_code(self) -> None:
        """``SPAWN_REFUSED_BUDGET``'s reading, applied here: a ledger that
        reports the design working claims breakage that did not happen."""
        assert set(ot.FANOUT_REFUSALS).isdisjoint(ot.HOST_DEGRADATIONS)

    def test_the_unit_ids_are_unique_and_stem_from_the_batch(self) -> None:
        plan = ot.plan_fanout(_jobs(6), grant=6, stem="orchestrator-1-fanout-1")
        ids = [unit.unit_task_id for unit in plan.units]
        assert len(set(ids)) == len(ids)
        assert all(unit_id.startswith("orchestrator-1-fanout-1-") for unit_id in ids)


# ── criterion 4c: the bounded total charge, on live drives ───────────────────


class TestTheBoundedTotalCharge:
    """N concurrent children cannot exceed the grant a single serial spawn got."""

    def test_the_loop_bounds_a_drive_at_its_own_max_steps(self) -> None:
        """The PREMISE clause 3 leans on, pinned rather than assumed.

        The fan-out's bound is only as good as ``run(max_steps=k)`` spending at
        most ``k``. That is embodiment's guarantee, not this host's, so it is
        checked here explicitly — if it ever stopped holding, the rule above
        would be quietly wrong and this is the test that would say so.
        """
        for budget in range(1, 7):
            outcome = run(
                Restless(),
                Task(id="probe", repo_path="", instruction="x"),
                executor=ot.WorkerExecutor(subtask="x"),
                max_steps=budget,
                spawn_allowance=0,
                subagent=None,
            )
            assert outcome.result.stats.model_turns <= budget, budget

    def test_the_charge_never_exceeds_the_batch_grant(self) -> None:
        delivered = _fan(_fanning_cortex(*_jobs(6)), Restless(), fanout_max_steps=12, max_steps=40)
        plan = delivered.plans[0]
        assert delivered.charged > 0, "a vacuous bound proves nothing"
        assert delivered.charged <= plan.committed <= plan.grant

    def test_the_charge_saturates_the_grant_without_passing_it(self) -> None:
        """``Restless`` spends every turn it is given, so this is the tight case."""
        delivered = _fan(_fanning_cortex(*_jobs(6)), Restless(), fanout_max_steps=12, max_steps=40)
        plan = delivered.plans[0]
        assert plan.committed == plan.grant == 12
        assert delivered.charged == 12

    def test_the_charge_is_the_sum_of_what_each_unit_was_billed(self) -> None:
        delivered = _fan(_fanning_cortex(*_jobs(5)), Restless(), fanout_max_steps=11, max_steps=40)
        assert delivered.charged == sum(record.charged for record in delivered.log.runs)
        assert delivered.outcome.child_model_turns == delivered.charged

    def test_no_unit_spends_past_its_own_slice(self) -> None:
        delivered = _fan(_fanning_cortex(*_jobs(7)), Restless(), fanout_max_steps=15, max_steps=40)
        plan = delivered.plans[0]
        for unit, record in zip(plan.units, delivered.log.runs):
            assert record.child_task_id == unit.unit_task_id
            assert record.model_turns <= unit.grant, (unit, record)

    def test_a_wide_fanout_charges_no_more_than_one_serial_spawn_could(self) -> None:
        """The comparison the criterion actually asks for.

        A serial spawn may borrow everything the parent has left; the loop
        records that number on the ``SpawnRecord``. A twelve-unit fan-out rides
        the SAME record and the same number — the width buys no extra budget.
        """
        delivered = _fan(
            _fanning_cortex(*_jobs(12)), Restless(), fanout_max_steps=500, max_steps=20
        )
        granted = [record for record in delivered.outcome.spawns if record.granted]
        assert len(granted) == 1, "N units, ONE spawn — that is why the rule is needed"
        assert delivered.plans[0].grant == granted[0].steps_granted
        assert delivered.charged <= granted[0].steps_granted

    def test_the_parent_budget_bounds_the_whole_tree_at_every_width(self) -> None:
        """Widening the fan-out cannot exhaust the parent in a way serial would not."""
        for width in range(1, 13):
            delivered = _fan(
                _fanning_cortex(*_jobs(width)), Restless(), fanout_max_steps=500, max_steps=20
            )
            spent = delivered.outcome.result.stats.model_turns + delivered.charged
            assert spent <= 20, (width, spent)
            assert delivered.outcome.exit_reason in {EXIT_FINISHED, "budget"}

    def test_the_serial_shape_obeys_the_same_ceiling(self) -> None:
        """The control: the fan-out is not held to a looser bound than t1's."""
        delivered = _drive(
            _delegating_cortex(*_jobs(2)), Restless(), max_steps=20, worker_max_steps=500
        )
        spent = delivered.outcome.result.stats.model_turns + delivered.outcome.child_model_turns
        assert spent <= 20

    def test_a_zero_grant_dispatches_nothing_and_charges_nothing(self) -> None:
        delivered = _fan(_fanning_cortex("a", "b"), Restless(), fanout_max_steps=0)
        plan = delivered.plans[0]
        assert plan.dispatched == ()
        assert delivered.charged == 0
        assert {unit.refusal for unit in plan.units} == {ot.REFUSED_UNFUNDED}
        assert delivered.outcome.exit_reason == EXIT_FINISHED

    def test_nothing_reclaims_another_units_unspent_remainder(self) -> None:
        """Clause 5. A frugal unit's leftover goes back to the PARENT.

        Reclaim would need a shared counter read under contention; a bound that
        depends on how threads interleave is not a bound. So the leftover is
        simply not spent, and the charge comes in *under* what was committed.
        """
        delivered = _fan(_fanning_cortex(*_jobs(3)), Reporting(), fanout_max_steps=9, max_steps=40)
        plan = delivered.plans[0]
        assert [unit.grant for unit in plan.units] == [3, 3, 3]
        for unit, record in zip(plan.units, delivered.log.runs):
            assert record.model_turns <= unit.grant
        assert delivered.charged < plan.committed, "the frugal units should leave turns over"

    def test_an_absent_unit_is_charged_its_whole_slice(self) -> None:
        """Clause 4. It may still be running; charging it zero is not a bound."""
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("good one", "stuck one", "good two"),
                Blocking(gate, "stuck"),
                fanout_max_steps=9,
                timeout=0.3,
            )
            plan = delivered.plans[0]
            stuck = delivered.log.runs[1]
            assert stuck.exit_reason == ot.FANOUT_EXIT_ABSENT
            assert stuck.charged == plan.units[1].grant == 3
            assert delivered.charged <= plan.committed
        finally:
            gate.set()

    def test_an_overspending_unit_is_charged_verbatim_and_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defence in depth, fired deliberately.

        ``run`` bounds a unit at its slice (pinned above), so this branch is
        unreachable today. It exists because *clamping* an overspend would make
        the grant quietly mean less than it says — ``loop._charge_child``'s own
        reasoning — and a guard nobody has ever fired is a guard nobody knows
        works. The seam is patched here, in the test, never in the module.
        """
        real = ot._drive_worker

        def inflating(complete: Any, call: Any, record: Any) -> Any:
            reply = real(complete, call, record)
            record.model_turns = record.model_turns + 5
            record.charged = record.model_turns
            return reply

        monkeypatch.setattr(ot, "_drive_worker", inflating)
        delivered = _fan(_fanning_cortex("one job"), Restless(), fanout_max_steps=3)
        codes = [record.code for record in delivered.records()]
        assert ot.DEGRADED_FANOUT_OVERSPEND in codes
        assert (
            delivered.charged > delivered.plans[0].committed
        ), "the overspend is charged, not hidden"


class TestTheChargeCannotBeRaced:
    """The bound holds under real contention, not just in a quiet single thread."""

    WIDTH = 8

    def _contended(self) -> ot.FanoutOutcome:
        """Every unit parks until all of them have started, then all race."""
        barrier = threading.Barrier(self.WIDTH, timeout=15)
        try:
            return _fan(
                _fanning_cortex(*_jobs(self.WIDTH)),
                Restless(barrier),
                fanout_max_steps=self.WIDTH * 2,
                max_steps=60,
            )
        finally:
            barrier.abort()

    def test_the_units_really_do_run_at_the_same_time(self) -> None:
        """Without this the race tests below would prove nothing."""
        delivered = self._contended()
        assert delivered.log.max_in_flight == self.WIDTH

    @pytest.mark.parametrize("attempt", list(range(10)))
    def test_the_bound_holds_on_every_contended_attempt(self, attempt: int) -> None:
        delivered = self._contended()
        plan = delivered.plans[0]
        assert delivered.charged == sum(record.charged for record in delivered.log.runs)
        assert 0 < delivered.charged <= plan.committed <= plan.grant
        assert delivered.outcome.child_model_turns == delivered.charged

    def test_every_unit_is_accounted_for_exactly_once(self) -> None:
        delivered = self._contended()
        ids = [record.child_task_id for record in delivered.log.runs]
        assert len(ids) == len(set(ids)) == self.WIDTH

    def test_the_runs_are_reported_in_plan_order_not_completion_order(self) -> None:
        """A report whose shape depends on the scheduler is not a report."""
        delivered = self._contended()
        plan = delivered.plans[0]
        assert [record.child_task_id for record in delivered.log.runs] == [
            unit.unit_task_id for unit in plan.units
        ]

    def test_the_degradations_come_back_in_plan_order_too(self) -> None:
        """The runs list is plan-ordered structurally; the DEGRADATION stream is
        ordered by how the collector walks its futures, which is the half a
        scheduler could actually disturb. Pinned separately for that reason.
        """
        barrier = threading.Barrier(4, timeout=15)
        try:
            delivered = _fan(
                _fanning_cortex(*_jobs(4)),
                SilentAtBarrier(barrier),
                fanout_max_steps=8,
                max_steps=40,
            )
        finally:
            barrier.abort()
        attributed = [
            record.original.unit_task_id
            for record in delivered.records()
            if record.code == ot.DEGRADED_WORKER_NO_REPORT
        ]
        assert attributed == [unit.unit_task_id for unit in delivered.plans[0].units]

    def test_the_high_water_mark_tells_the_two_shapes_apart(self) -> None:
        serial = _drive(_delegating_cortex("a", "b", "c"), _reporting_worker())
        assert serial.log.max_in_flight == 1
        assert self._contended().log.max_in_flight > 1


# ── criterion 5: partial failure is recorded, never fatal ────────────────────


class TestPartialFailureIsRecordedNotFatal:
    """C3 at the fan-out's shape: partial results ride, and nothing is silent."""

    def test_a_failed_unit_degrades_and_its_siblings_still_report(self) -> None:
        delivered = _fan(
            _fanning_cortex("good one", "explode here", "good two"),
            Failing("explode"),
            fanout_max_steps=9,
        )
        assert delivered.outcome.exit_reason == EXIT_FINISHED
        assert delivered.aborted is False
        assert len([record for record in delivered.log.runs if record.report]) == 2
        codes = [record.code for record in delivered.records()]
        assert ot.DEGRADED_WORKER_ABORTED in codes

    def test_an_absent_unit_degrades_and_its_siblings_still_report(self) -> None:
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("good one", "stuck one", "good two"),
                Blocking(gate, "stuck"),
                fanout_max_steps=9,
                timeout=0.3,
            )
            assert delivered.outcome.exit_reason == EXIT_FINISHED
            assert len([record for record in delivered.log.runs if record.report]) == 2
            codes = [record.code for record in delivered.records()]
            assert ot.DEGRADED_FANOUT_TIMEOUT in codes
        finally:
            gate.set()

    def test_a_unit_whose_drive_raises_outright_is_recorded_not_propagated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The seam itself falling over — the one fault a unit's own loop
        cannot turn into a ``LoopAborted``. It must still not reach the parent."""

        def exploding(complete: Any, call: Any, record: Any) -> Any:
            raise RuntimeError("the seam itself fell over")

        monkeypatch.setattr(ot, "_drive_worker", exploding)
        delivered = _fan(_fanning_cortex("a", "b"), Restless(), fanout_max_steps=4)
        assert delivered.outcome.exit_reason == EXIT_FINISHED
        codes = [record.code for record in delivered.records()]
        assert codes.count(ot.DEGRADED_WORKER_ABORTED) == 2
        assert all(record.exit_reason == ot.FANOUT_EXIT_FAILED for record in delivered.log.runs)
        assert delivered.charged == delivered.plans[0].committed

    def test_a_fanout_where_every_unit_hangs_still_terminates_the_parent(self) -> None:
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("stuck a", "stuck b", "stuck c"),
                Blocking(gate, "stuck"),
                fanout_max_steps=6,
                timeout=0.3,
            )
            assert delivered.outcome.exit_reason == EXIT_FINISHED
            assert all(record.exit_reason == ot.FANOUT_EXIT_ABSENT for record in delivered.log.runs)
            assert delivered.charged == delivered.plans[0].committed
        finally:
            gate.set()

    def test_a_unit_landing_after_the_deadline_cannot_rewrite_the_verdict(self) -> None:
        """The race this design closes: the thread behind an absent unit is
        still running, and it used to write straight into the record the
        collector had already charged and reported."""
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("stuck one"),
                Blocking(gate, "stuck"),
                fanout_max_steps=4,
                timeout=0.3,
            )
            record = delivered.log.runs[0]
            assert record.exit_reason == ot.FANOUT_EXIT_ABSENT
            assert record.sealed is True
        finally:
            gate.set()
        assert _await(lambda: record.late), "the late unit should have tried to publish"
        assert record.exit_reason == ot.FANOUT_EXIT_ABSENT
        assert record.charged == delivered.plans[0].units[0].grant
        assert record.report == ""

    def test_the_refusals_reach_the_acting_model_as_text(self) -> None:
        """A cortex that silently got two answers instead of three will
        synthesise as if three agreed."""
        delivered = _fan(_fanning_cortex("a", "b", "c"), Reporting(), fanout_max_steps=2)
        results = " ".join(step.result for step in delivered.outcome.result.steps)
        assert f"NOT RUN ({ot.REFUSED_UNFUNDED})" in results
        assert "orchestrator-1-fanout-1-3" in results

    def test_the_reports_that_did_come_back_reach_the_acting_model(self) -> None:
        delivered = _fan(
            _fanning_cortex("a", "b"), Reporting("gauge four is low"), fanout_max_steps=4
        )
        results = " ".join(step.result for step in delivered.outcome.result.steps)
        assert results.count("gauge four is low") >= 2

    def test_an_absence_reaches_the_acting_model_as_text_too(self) -> None:
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("good one", "stuck one"),
                Blocking(gate, "stuck"),
                fanout_max_steps=6,
                timeout=0.3,
            )
            results = " ".join(step.result for step in delivered.outcome.result.steps)
            assert f"NO REPORT ({ot.FANOUT_EXIT_ABSENT})" in results
        finally:
            gate.set()

    def test_the_batch_exit_reason_says_partial_when_something_is_missing(self) -> None:
        whole = _fan(_fanning_cortex("a", "b"), Reporting(), fanout_max_steps=6)
        assert whole.outcome.spawns[0].exit_reason == ot.FANOUT_EXIT_COMPLETE
        partial = _fan(_fanning_cortex("a", "b", "c"), Reporting(), fanout_max_steps=2)
        assert partial.outcome.spawns[0].exit_reason == ot.FANOUT_EXIT_PARTIAL

    def test_nothing_degrades_silently(self) -> None:
        """Every unit that failed to deliver carries a recorded code, and every
        recorded code is one this host declared."""
        gate = threading.Event()
        try:
            delivered = _fan(
                _fanning_cortex("good one", "silent one", "stuck one"),
                Mixed(gate),
                fanout_max_steps=9,
                timeout=0.3,
            )
            good, silent, stuck = delivered.log.runs
            assert good.report
            assert good.degradation_codes == []
            assert not silent.report
            assert ot.DEGRADED_WORKER_NO_REPORT in silent.degradation_codes
            assert stuck.exit_reason == ot.FANOUT_EXIT_ABSENT
            assert ot.DEGRADED_FANOUT_TIMEOUT in stuck.degradation_codes
            codes = {record.code for record in delivered.records()}
            assert ot.DEGRADED_WORKER_NO_REPORT in codes
            assert ot.DEGRADED_FANOUT_TIMEOUT in codes
        finally:
            gate.set()

    def test_a_clean_fanout_attributes_nothing(self) -> None:
        delivered = _fan(_fanning_cortex("a", "b", "c"), Reporting(), fanout_max_steps=9)
        assert [record.code for record in delivered.records()] == []
        assert delivered.report()["attribution"] == {}


class TestFanoutLedgerAttribution:
    """ "Which unit degraded?" is answerable without inference — and it has to be
    answered a level deeper than the loop can reach, because a fan-out is ONE
    spawn and ``SpawnRecord.child_task_id`` can only ever name the batch."""

    def test_every_unit_degradation_carries_its_unit_id_as_a_field(self) -> None:
        delivered = _fan(_fanning_cortex("one", "two", "three"), Silent(), fanout_max_steps=9)
        hits = [
            record for record in delivered.records() if record.code == ot.DEGRADED_WORKER_NO_REPORT
        ]
        assert len(hits) == 3
        assert {record.original.unit_task_id for record in hits} == {
            f"orchestrator-1-fanout-1-{index}" for index in (1, 2, 3)
        }

    def test_the_reason_names_the_unit_too_for_a_reader_who_only_has_text(self) -> None:
        delivered = _fan(_fanning_cortex("one", "two"), Silent(), fanout_max_steps=6)
        for record in delivered.records():
            assert record.original.unit_task_id in record.reason

    def test_the_loop_can_only_attribute_the_batch_which_is_why_the_field_exists(self) -> None:
        delivered = _fan(_fanning_cortex("one", "two"), Silent(), fanout_max_steps=6)
        for record in delivered.records():
            assert record.child_task_id == "orchestrator-1-fanout-1"
            assert record.source == ledger.SOURCE_SUBAGENT
            assert record.original.unit_task_id.startswith("orchestrator-1-fanout-1-")

    def test_a_loop_minted_unit_degradation_keeps_its_own_lane(self) -> None:
        """A loop code is a loop code; the unit attribution rides beside it."""
        worker = Scripted(_turn(_call("report", findings={"a set is not JSON"})))
        delivered = _fan(_fanning_cortex("one job"), worker, fanout_max_steps=4)
        hits = [
            record
            for record in delivered.records()
            if record.code == "tool-arguments-unserializable"
        ]
        assert hits, "expected the unit's own loop to mint a degradation"
        assert hits[0].source == ledger.SOURCE_LOOP
        assert hits[0].original.unit_task_id == "orchestrator-1-fanout-1-1"

    def test_this_hosts_fanout_codes_are_declared_and_disjoint_from_embodiments(self) -> None:
        assert set(ot.FANOUT_DEGRADATIONS) < set(ot.HOST_DEGRADATIONS)
        assert set(ot.WORKER_DEGRADATIONS) < set(ot.HOST_DEGRADATIONS)
        assert set(ot.HOST_DEGRADATIONS).isdisjoint({entry.code for entry in ledger.known_codes()})

    def test_the_degradation_shape_is_json_ready_with_its_unit(self) -> None:
        found = ot.FanoutDegradation(code="x", reason="y", unit_task_id="u-1")
        assert json.loads(json.dumps(found.to_dict()))["unit_task_id"] == "u-1"


# ── criterion 6: termination, proved STRUCTURALLY ────────────────────────────
#
# In the spirit of tests/test_muse_tool_loop_ast.py: a passing run proves one
# scenario; these read the source and prove there is no other.


#: Every function the structural pins below read. A rename has to come past here.
_FANOUT_LANE = (
    "partition",
    "plan_fanout",
    "_fan_out",
    "_drive_unit",
    "_absent",
    "_publish",
    "_unit_call",
    "_unit_record",
    "_attribute",
    "_fanout_note",
    "build_fanout_seam",
)


def _functions() -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.walk(_tree()) if isinstance(node, ast.FunctionDef)}


def _function(name: str) -> ast.FunctionDef:
    node = _functions().get(name)
    assert node is not None, f"{name} must exist by that name; the pins here read it"
    return node


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    """Every call to ``name(...)`` or ``<something>.name(...)`` inside *node*."""
    found: list[ast.Call] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id == name:
            found.append(child)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            found.append(child)
    return found


def _kwarg(call: ast.Call, name: str) -> Optional[ast.AST]:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


#: Iterator shapes that cannot run forever: a bound name, an attribute, or one
#: of the settled builders. A bare generator call would not be on this list.
_SETTLED_BUILDERS = {"enumerate", "range", "sorted", "tuple", "list", "reversed"}
_SETTLED_METHODS = {"items", "keys", "values"}


def _is_settled(iterator: ast.AST) -> bool:
    if isinstance(iterator, (ast.Name, ast.Attribute, ast.Tuple, ast.List)):
        return True
    if isinstance(iterator, ast.Subscript):
        return True
    if isinstance(iterator, ast.Call):
        func = iterator.func
        if isinstance(func, ast.Name) and func.id in _SETTLED_BUILDERS:
            return True
        if isinstance(func, ast.Attribute) and func.attr in _SETTLED_METHODS:
            return True
    return False


class TestFanoutTerminates:
    """The termination guarantee, preserved and proved by reading the source."""

    def test_the_module_contains_no_while_loop_at_all(self) -> None:
        """The strongest form of the bound: there is no unbounded iteration
        construct in this file for anyone to argue about."""
        assert [node for node in ast.walk(_tree()) if isinstance(node, ast.While)] == []

    def test_every_iteration_in_the_fanout_lane_walks_a_settled_sequence(self) -> None:
        """A ``for`` over a bound name or a settled builder cannot run forever;
        a ``for`` over a freshly-called generator could."""
        for name in _FANOUT_LANE:
            node = _function(name)
            for child in ast.walk(node):
                if isinstance(child, ast.For):
                    assert _is_settled(child.iter), f"{name}: {ast.dump(child.iter)}"
                elif isinstance(child, ast.comprehension):
                    assert _is_settled(child.iter), f"{name}: {ast.dump(child.iter)}"

    def test_wait_is_called_exactly_once_and_always_with_a_timeout(self) -> None:
        """A hung unit must not be able to park the parent's drive."""
        calls = _calls_named(_tree(), "wait")
        assert len(calls) == 1, [ast.dump(call) for call in calls]
        timeout = _kwarg(calls[0], "timeout")
        assert timeout is not None, "the one wait must carry a timeout"
        assert isinstance(timeout, ast.Name), ast.dump(timeout)
        assert timeout.id == "timeout"

    def test_the_timeout_default_is_a_finite_positive_number(self) -> None:
        assert isinstance(ot.DEFAULT_FANOUT_TIMEOUT, float)
        assert 0 < ot.DEFAULT_FANOUT_TIMEOUT < float("inf")

    def test_the_pool_is_shut_down_exactly_once_without_waiting(self) -> None:
        """``shutdown(wait=True)`` would join a thread that is still blocked —
        the deadline would bound the wait and then teardown would undo it."""
        calls = _calls_named(_tree(), "shutdown")
        assert len(calls) == 1
        waited = _kwarg(calls[0], "wait")
        assert isinstance(waited, ast.Constant), ast.dump(calls[0])
        assert waited.value is False

    def test_the_shutdown_runs_in_a_finally(self) -> None:
        node = _function("_fan_out")
        tries = [child for child in ast.walk(node) if isinstance(child, ast.Try)]
        assert len(tries) == 1
        finalbody = ast.Module(body=tries[0].finalbody, type_ignores=[])
        assert _calls_named(finalbody, "shutdown"), "teardown must not depend on the happy path"

    def test_every_future_result_is_read_with_a_zero_timeout(self) -> None:
        """A read that cannot block, even if ``wait`` were wrong about done-ness."""
        calls = _calls_named(_tree(), "result")
        assert calls
        for call in calls:
            timeout = _kwarg(call, "timeout")
            assert isinstance(timeout, ast.Constant), ast.dump(call)
            assert timeout.value == 0, ast.dump(call)

    def test_the_module_never_sleeps_and_never_polls(self) -> None:
        assert not _calls_named(_tree(), "sleep")

    def test_the_thread_pool_is_constructed_once_with_a_bounded_width(self) -> None:
        calls = _calls_named(_tree(), "ThreadPoolExecutor")
        assert len(calls) == 1
        workers = _kwarg(calls[0], "max_workers")
        assert isinstance(workers, ast.Call)
        assert getattr(workers.func, "id", "") == "len", ast.dump(workers)

    def test_the_partition_is_the_only_arithmetic_that_funds_a_unit(self) -> None:
        """``subagent.py`` proves its depth bound by counting its arithmetic.
        Same move: one ``divmod``, in one function, and no other producer."""
        # One tree, walked once: node identity is what says WHICH function owns
        # the operation, and two parses produce two disjoint sets of objects.
        tree = _tree()
        divmods = _calls_named(tree, "divmod")
        assert len(divmods) == 1
        owners = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            if any(child is divmods[0] for child in ast.walk(node))
        ]
        assert owners == ["partition"]

    def test_nothing_widens_a_slice(self) -> None:
        """``min`` narrows and ``max`` widens; ``partition`` calls neither."""
        assert not _calls_named(_function("partition"), "max")
        assert not _calls_named(_function("_fan_out"), "max")

    def test_the_width_bound_is_declared_once_and_never_rewritten(self) -> None:
        writes = [
            target.id
            for node in ast.walk(_tree())
            if isinstance(node, (ast.Assign, ast.AugAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name) and target.id == "MAX_FANOUT_WIDTH"
        ]
        assert len(writes) == 1

    def test_the_fanout_seam_raises_nothing_of_its_own(self) -> None:
        """A fan-out that raised would be a fourth way out of the parent's loop."""
        for name in ("_fan_out", "_drive_unit", "_absent", "_publish", "build_fanout_seam"):
            assert not [node for node in ast.walk(_function(name)) if isinstance(node, ast.Raise)]

    def test_the_dispatch_count_is_bounded_before_any_thread_exists(self) -> None:
        """The step-count bound. Both caps bite, and the smaller one wins."""
        for width in range(0, 40):
            for grant in range(0, 20):
                plan = ot.plan_fanout(_jobs(width), grant=grant, stem="b")
                assert len(plan.dispatched) <= min(ot.MAX_FANOUT_WIDTH, grant, width)

    def test_every_unit_is_a_leaf_by_construction(self) -> None:
        """Depth is bounded at the batch: ``_unit_call`` narrows the budget and
        never touches ``allowance``, so a unit inherits the batch's
        ``NO_SPAWNS`` and no unit can fan out again."""
        node = _function("_unit_call")
        calls = _calls_named(node, "replace")
        assert len(calls) == 1
        assert {keyword.arg for keyword in calls[0].keywords} == {
            "task",
            "executor",
            "max_steps",
            "lineage",
            "context",
        }

    def test_the_batch_asks_for_no_onward_spawns(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute(ot.FANOUT_TOOL, {"subtasks": ["a"]}).spawn
        assert spawn is not None
        assert spawn.allowance == 0

    def test_the_units_lineage_names_the_batch_and_the_depth_is_two(self) -> None:
        delivered = _fan(_fanning_cortex("a"), Reporting(), fanout_max_steps=4)
        record = delivered.log.runs[0]
        assert record.lineage == ("orchestrator-1", "orchestrator-1-fanout-1")
        assert record.depth == 2

    def test_concurrency_comes_from_the_standard_library_only(self) -> None:
        roots = {module.split(".")[0] for module, _ in _imports(_tree())}
        assert {"threading", "concurrent"} <= roots
        assert roots <= {
            "__future__",
            "argparse",
            "concurrent",
            "dataclasses",
            "embodiment",
            "json",
            "sys",
            "threading",
            "typing",
        }, f"an unexpected import root: {roots}"

    @pytest.mark.parametrize("name", _FANOUT_LANE)
    def test_the_proof_is_not_vacuous(self, name: str) -> None:
        assert _function(name).name == name


# ── the fan-out surface, and the serial one it must not disturb ──────────────


class TestTheFanoutSurface:
    def test_the_fanout_surface_adds_one_verb_and_keeps_finish_alone(self) -> None:
        assert set(ot.FANOUT_ORCHESTRATOR_TOOLS) == set(ot.ORCHESTRATOR_TOOLS) | {ot.FANOUT_TOOL}
        assert "finish" in ot.FANOUT_ORCHESTRATOR_TOOLS
        assert set(ot.WORKER_TOOLS).isdisjoint(ot.FANOUT_ORCHESTRATOR_TOOLS)
        assert set(ot.WORKER_TOOLS).isdisjoint(ot.FINAL_AUTHORITY_TOOLS)

    def test_the_serial_pre_registration_is_untouched(self) -> None:
        """Widening ``t1``'s tuple silently would be exactly the drift the
        pre-registration exists to prevent."""
        assert ot.ORCHESTRATOR_TOOLS == ("delegate", "note", "finish")
        assert ot.OrchestratorExecutor.tools == ot.ORCHESTRATOR_TOOLS
        assert ot.WORKER_TOOLS == ("report",)
        assert ot.WORKER_DEGRADATIONS == (
            ot.DEGRADED_WORKER_NO_REPORT,
            ot.DEGRADED_WORKER_ABORTED,
        )

    def test_a_unit_cannot_fan_out_again(self) -> None:
        assert ot.FANOUT_TOOL not in ot.WORKER_TOOLS
        worker = ot.WorkerExecutor(subtask="a scoped job")
        with pytest.raises(UnknownToolError):
            worker.execute(ot.FANOUT_TOOL, {"subtasks": ["a"]})

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"subtasks": []}, {"subtasks": ["ok", "   "]}, {"subtasks": 7}, {"subtasks": None}],
    )
    def test_an_unusable_fanout_request_is_a_self_correcting_step(
        self, arguments: dict[str, Any]
    ) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        with pytest.raises(ToolError):
            boss.execute(ot.FANOUT_TOOL, arguments)
        assert boss.fanouts == 0

    def test_a_bare_string_is_read_as_one_subtask(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute(ot.FANOUT_TOOL, {"subtasks": "just the one"}).spawn
        assert spawn is not None
        assert spawn.context == {"subtasks": ("just the one",)}

    def test_the_units_are_framed_as_subagents_never_as_the_teammate(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1", identity="Gwen")
        spawn = boss.execute(ot.FANOUT_TOOL, {"subtasks": ["a", "b"]}).spawn
        assert spawn is not None
        assert spawn.role == ot.ROLE_FANOUT
        assert spawn.system_prompt is not None
        assert ot.WORKER_SYSTEM in spawn.system_prompt
        assert CORTEX_MARKER not in spawn.system_prompt

    def test_absent_identity_leaves_the_unit_prompt_byte_identical(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute(ot.FANOUT_TOOL, {"subtasks": ["a"]}).spawn
        assert spawn is not None
        assert spawn.system_prompt == ot.WORKER_SYSTEM

    def test_the_batch_and_its_units_carry_no_repo_path(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        spawn = boss.execute(ot.FANOUT_TOOL, {"subtasks": ["a"]}).spawn
        assert spawn is not None
        assert spawn.task.repo_path == ""
        delivered = _fan(_fanning_cortex("a"), Reporting(), fanout_max_steps=4)
        assert delivered.log.runs[0].report

    def test_one_tool_call_makes_exactly_one_spawn_request(self) -> None:
        """N units, ONE spawn — which is precisely why the accounting rule has
        to exist: the loop's per-spawn budget re-check happens once."""
        delivered = _fan(_fanning_cortex(*_jobs(5)), Reporting(), fanout_max_steps=10)
        assert len([record for record in delivered.outcome.spawns if record.granted]) == 1
        assert len(delivered.log.runs) == 5

    def test_the_fanout_orchestrator_still_serves_the_serial_verb(self) -> None:
        """One seam, two shapes. The serial path must not have been broken by
        teaching the same executor to fan out."""
        cortex = Scripted(
            _turn(_call("delegate", subtask="one job")),
            _turn(_call("finish", summary="done")),
        )
        delivered = _fan(cortex, Reporting("serial findings"), fanout_max_steps=6)
        assert delivered.plans == []
        assert delivered.log.runs[0].report == "serial findings"
        assert delivered.log.max_in_flight == 1

    def test_the_fanout_orchestrator_refuses_un_enumerated_tools(self) -> None:
        boss = ot.FanoutOrchestratorExecutor(task_id="orchestrator-1")
        for name in ("report", "bash", "read_file"):
            with pytest.raises(UnknownToolError):
                boss.execute(name, {})


class TestFanoutRidesTheExistingSeam:
    def test_no_embodiment_source_changed_on_this_branch(self) -> None:
        """Task ``t4``'s zero-diff contract, scoped to its own branch exactly as
        ``t1``'s is scoped to its own."""
        if _branch() != "owa/t4":
            pytest.skip("the zero-diff delivery check only runs on owa/t4")
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
        assert changed == [], f"task t4 must not change embodiment/: {changed}"


# ── the runnable fan-out demonstration ───────────────────────────────────────


class TestFanoutDemo:
    def test_the_demo_runs_hermetically(self) -> None:
        report = ot.run_fanout_demo()
        assert report["orchestrator"]["exit_reason"] == EXIT_FINISHED

    def test_the_demo_shows_the_partition_biting(self) -> None:
        batch = ot.run_fanout_demo()["fanout"]["batches"][0]
        assert batch["width"] == len(ot.DEMO_FANOUT_SUBTASKS)
        assert batch["committed"] == ot.DEMO_FANOUT_GRANT
        assert batch["units"][-1]["refusal"] == ot.REFUSED_UNFUNDED
        assert batch["units"][-1]["dispatched"] is False

    def test_the_demo_charges_no_more_than_the_grant(self) -> None:
        fan = ot.run_fanout_demo()["fanout"]
        assert fan["charged_total"] <= fan["committed_total"] <= fan["grant_total"]

    def test_the_demo_shows_a_degradation_attributed_to_one_unit(self) -> None:
        report = ot.run_fanout_demo()
        assert report["attribution"], "the demo should show attribution at work"

    def test_the_report_is_json_serializable(self) -> None:
        loaded = json.loads(json.dumps(ot.run_fanout_demo(), indent=2))
        assert loaded["fanout"]["rule"] == ot.FANOUT_ACCOUNTING_RULE
        assert loaded["orchestrator"]["tools"] == list(ot.FANOUT_ORCHESTRATOR_TOOLS)

    def test_main_runs_the_fanout_on_request(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert ot.main(["--fanout"]) == 0
        text = capsys.readouterr().out
        assert "PARALLEL FAN-OUT" in text
        assert f"NOT RUN ({ot.REFUSED_UNFUNDED})" in text
        assert "A fan-out spends what ONE serial spawn may spend" in text

    def test_main_emits_the_fanout_json_on_request(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert ot.main(["--fanout", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["fanout"]["max_width"] == ot.MAX_FANOUT_WIDTH

    def test_the_serial_demo_is_still_the_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert ot.main([]) == 0
        assert "SERIAL DELEGATION" in capsys.readouterr().out
