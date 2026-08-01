"""Tests for ``examples/arch_policy.py`` — arm P, the compiled policy.

Plan task **t7** of `error-derived-timeouts-bee-hive-architecture`. The file is
organised around the task's three acceptance criteria, then the structural
claims that make them more than assertions:

1. :class:`TestNoHostExecutionPrimitiveExists` and
   :class:`TestTheOnlyExecutionPathIsTheJail` — model-written policy source
   executes only inside the network-less workspace jail, proven hermetically.
2. :class:`TestTheThreeControlsAreDialable` — random, hand-written baseline and
   no-op exist as dialable control arms.
3. :class:`TestOutcomeEscalationAndAcceptanceAreDisjoint` — escalation rate and
   call-acceptance are separate reported columns from outcome.

Plus :class:`TestPolicyTerminates` (claim ``c11``/``h15``: the same structural
termination assertion the fan-out has), :class:`TestArmsAreData`,
:class:`TestTheEscalateReturnIsFirstClass` and :class:`TestConfigOnlyNoCodeDefaults`.

**This file contains no execution primitive either**, and that is checked: a
suite that ran a policy "just to see what it produces" would be the exact hole
criterion 1 names, so the guard covers both files — ``tests/
test_challenge_coding.py``'s precedent, cited rather than re-argued.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from embodiment.contract import ModelResponse
from embodiment.workspace import PROVIDER_FAKE, MuseWorkspace
from examples import arch_arms as aa
from examples import arch_hive as ah
from examples import arch_policy as ap

HARNESS_PATH = Path(ap.__file__).resolve()
THIS_PATH = Path(__file__).resolve()


# ══════════════════════════════════════════════════════════════════════════════
# doubles
# ══════════════════════════════════════════════════════════════════════════════


class _Package:
    """The shape ``headspace.api`` hands back, reduced to what is read."""

    def __init__(self, *, status: str = "success", output: str = "", summary: str = "") -> None:
        self.status = status
        self.outcome_summary = summary
        self.evidence = [_Excerpt(output)] if output else []
        self.provenance = _Provenance()


class _Excerpt:
    def __init__(self, excerpt: str) -> None:
        self.label = "captured output"
        self.kind = "excerpt"
        self.excerpt = excerpt
        self.truncated = False


class _Provenance:
    workspace_id = "ws-1"
    job_id = "job-1"


class ScriptedApi:
    """A ``headspace.api`` double that answers a program the way a container would.

    It reads the run nonce **out of the program it was handed**, exactly as the
    real driver does, and substitutes it into a committed stdout template. A
    harness that stopped embedding the nonce would fail here rather than pass.
    """

    def __init__(
        self,
        stdout: str = "",
        *,
        status: str = "success",
        create_error: Optional[Exception] = None,
        run_error: Optional[Exception] = None,
    ) -> None:
        self.stdout = stdout
        self.status = status
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._create_error = create_error
        self._run_error = run_error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(("create", (), dict(kwargs)))
        if self._create_error:
            raise self._create_error
        return _Package(summary="workspace ws-1 is ready on fake")

    def run(self, workspace_id: str, command: Any, **kwargs: Any) -> Any:
        self.calls.append(("run", (workspace_id, list(command)), dict(kwargs)))
        if self._run_error:
            raise self._run_error
        program = list(command)[-1]
        nonce = ap.nonce_of(program) or "MISSING"
        return _Package(status=self.status, output=self.stdout.replace("{nonce}", nonce))

    def destroy(self, workspace_id: str, **kwargs: Any) -> Any:
        self.calls.append(("destroy", (workspace_id,), dict(kwargs)))
        return _Package()

    def kwargs_for(self, verb: str) -> list[dict[str, Any]]:
        return [kwargs for name, _, kwargs in self.calls if name == verb]

    def argv_for_runs(self) -> list[list[str]]:
        return [list(args[1]) for name, args, _ in self.calls if name == "run"]


def _workspace(api: Any, **kwargs: Any) -> MuseWorkspace:
    return MuseWorkspace(provider=PROVIDER_FAKE, api=api, max_result_chars=0, **kwargs)


def _config() -> ap.PolicyConfig:
    return ap.load_policy_config()


def _episode() -> tuple[ap.Situation, ...]:
    return ap.demo_episode(_config().episode)


def _stdout_for(
    situations: Any,
    values: list[Any],
    *,
    repeat: int = 1,
    noise: str = "",
    seconds: float = 0.0001,
) -> str:
    """The stdout a container prints when the policy returned *values* in order."""
    rows = [
        {
            "i": index,
            "id": situation.id,
            "seconds": seconds,
            "type": type(value).__name__,
            "value": value,
        }
        for index, (situation, value) in enumerate(zip(situations, values))
    ]
    payload = json.dumps({"entry": ap.ENTRY_POINT, "decisions": rows}, sort_keys=True)
    lines = ([noise] if noise else []) + ["{nonce} " + payload] * repeat
    return "\n".join(lines) + ("\n" if lines else "")


def _perfect(situations: Any) -> list[Any]:
    """What a policy playing the harness's own rule would return, per situation."""
    return [ap.ESCALATE if not situation.decidable else situation.truth for situation in situations]


def _mind(text: str) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(content=text, prompt_tokens=100, completion_tokens=50)

    return complete


def _seams(
    minds: Optional[dict[str, Callable[[list[dict[str, Any]]], ModelResponse]]] = None,
    *,
    config: Optional[ap.PolicyConfig] = None,
    log: Optional[aa.CallLog] = None,
) -> aa.ScriptedSeams:
    resolved = config or _config()
    return aa.ScriptedSeams(minds or {}, config=resolved, log=log or aa.CallLog())


def _run(
    arm_id: str,
    *,
    values: Optional[list[Any]] = None,
    api: Optional[ScriptedApi] = None,
    minds: Optional[dict[str, Callable[[list[dict[str, Any]]], ModelResponse]]] = None,
    config: Optional[ap.PolicyConfig] = None,
    log: Optional[aa.CallLog] = None,
    situations: Any = None,
) -> ap.PolicyAttemptRecord:
    resolved = config or _config()
    episode = situations if situations is not None else ap.demo_episode(resolved.episode)
    chosen = values if values is not None else _perfect(episode)
    scripted = api if api is not None else ScriptedApi(_stdout_for(episode, chosen))
    return ap.run_attempt(
        arm=ap.POLICY_ARMS[arm_id],
        config=resolved,
        seams=_seams(minds, config=resolved, log=log),
        episode=episode,
        senses_hash="hash",
        workspace=_workspace(scripted),
    )


# ══════════════════════════════════════════════════════════════════════════════
# criterion 1 — the jail
# ══════════════════════════════════════════════════════════════════════════════

#: Builtins that turn a string into running code, checked as **bare names** so
#: that ``re.compile`` — which is not one of these — does not read as one.
EXECUTION_BUILTINS = {"exec", "eval", "compile", "__import__"}

#: Ways to start a process, checked as attributes *and* bare names, because
#: ``from subprocess import Popen`` would make one of these a bare name.
EXECUTION_STARTERS = {
    "system",
    "popen",
    "spawnl",
    "spawnv",
    "spawnlp",
    "spawnvp",
    "fork",
    "forkpty",
    "execv",
    "execve",
    "execl",
    "execlp",
    "check_output",
    "check_call",
    "Popen",
    "run_path",
    "load_module",
    "exec_module",
}

#: Modules whose whole purpose is running something on this machine.
EXECUTION_IMPORTS = {
    "subprocess",
    "runpy",
    "ctypes",
    "multiprocessing",
    "pty",
    "importlib",
    "importlib.util",
    "importlib.machinery",
}


def _called_bare(source: str) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_attrs(source: str) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _imported_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _execution_hits(source: str) -> set[str]:
    """Every way *source* could start code on this machine, by AST."""
    bare, attrs = _called_bare(source), _called_attrs(source)
    return (
        (bare & EXECUTION_BUILTINS)
        | ((bare | attrs) & EXECUTION_STARTERS)
        | (_imported_modules(source) & EXECUTION_IMPORTS)
    )


class TestNoHostExecutionPrimitiveExists:
    """Criterion 1, first mechanism: there is nothing here to run a policy with.

    The harness cannot execute a model's policy on this machine because it
    contains no primitive that could — and neither does this test file.
    """

    @pytest.mark.parametrize("path", [HARNESS_PATH, THIS_PATH], ids=["harness", "tests"])
    def test_nothing_here_can_start_code_on_this_machine(self, path: Path) -> None:
        found = _execution_hits(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} reaches {sorted(found)}"

    def test_a_benign_lookalike_is_not_flagged(self) -> None:
        assert not _execution_hits("import re\nre.compile('x')\nlist(map(str, [1]))")

    @pytest.mark.parametrize(
        "planted",
        [
            "import subprocess\nsubprocess.run(['python3', '-c', source])",
            "exec(source)",
            "import os\nos.system('python3 -c ' + source)",
            "eval(compile(source, '<policy>', 'exec'))",
            "import runpy\nrunpy.run_path(path)",
            "from subprocess import Popen\nPopen(['python3'])",
            "import importlib.util\nspec.loader.exec_module(module)",
            "__import__('os').system(source)",
        ],
    )
    def test_the_scan_would_catch_one(self, planted: str) -> None:
        """The guard is not vacuous: every planted execution path is flagged."""
        assert _execution_hits(planted), planted

    def test_the_committed_control_sources_are_never_run_here_either(self) -> None:
        """A control's source is data too. It rides the identical jail path."""
        for source in ap.COMMITTED_SOURCES.values():
            assert isinstance(source.text, str)
            assert source.text.strip()
        assert not _execution_hits(HARNESS_PATH.read_text(encoding="utf-8"))


FIXTURES = ap.load_fixtures()
FIXTURES_BY_NAME = {fixture.name: fixture for fixture in FIXTURES}


def _grade_fixture(fixture: ap.Fixture) -> ap.PolicyAttemptRecord:
    """Grade one committed fixture through the whole shipped pipeline.

    The arm is irrelevant to what comes back — the double answers with the
    fixture's committed stdout whatever program it is handed — so any arm will
    do, and the no-op is chosen to make it obvious the fixture's own source, not
    the arm's, is what produced those bytes.
    """
    episode = _episode()
    return _run(
        ap.ARM_NOOP,
        api=ScriptedApi(fixture.stdout),
        situations=episode,
        minds={aa.ROLE_WORKER: ah.scripted_worker()},
    )


class TestTheCommittedFixturesGradeAsDeclared:
    """The M2 kit: every ``stdout`` below is what a real container printed.

    ``expects`` is hand-declared in the committed table and is never computed
    from the code under test, so this is a check rather than the grader
    agreeing with itself.
    """

    def test_the_table_is_not_empty_and_every_fixture_says_what_it_proves(self) -> None:
        assert len(FIXTURES) >= 6
        for fixture in FIXTURES:
            assert fixture.proves.strip()
            assert fixture.stdout.strip()

    @pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
    def test_the_classifier_agrees_with_the_declared_counts(self, fixture: ap.Fixture) -> None:
        record = _grade_fixture(fixture)
        assert record.policy_acceptance["counts"] == fixture.expects["counts"]

    @pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
    def test_the_verdict_and_the_vacuity_gate_agree(self, fixture: ap.Fixture) -> None:
        record = _grade_fixture(fixture)
        assert record.verdict == fixture.expects["verdict"]
        assert record.vacuity["fired"] is fixture.expects["vacuity_fired"]
        assert record.vacuity["nonce_matches"] == fixture.expects["nonce_matches"]

    @pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
    def test_the_graded_correctness_agrees(self, fixture: ap.Fixture) -> None:
        record = _grade_fixture(fixture)
        assert record.graded["correct"] == fixture.expects["correct"]

    def test_a_policy_writing_to_stdout_cannot_hide_the_result(self) -> None:
        """The chatty fixture really did print around the driver's line."""
        fixture = FIXTURES_BY_NAME["chatty"]
        assert fixture.stdout.count("thinking about") == len(_episode())
        record = _grade_fixture(fixture)
        assert record.vacuity["nonce_matches"] == 1

    def test_the_committed_stdout_and_the_synthetic_template_agree(self) -> None:
        """The template the rest of this file uses is faithful to a real capture.

        Without this, every synthetic test would be grading a shape I invented.
        """
        episode = _episode()
        real = _grade_fixture(FIXTURES_BY_NAME["baseline"])
        synthetic = _run(
            ap.ARM_NOOP,
            api=ScriptedApi(_stdout_for(episode, _perfect(episode))),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
        )
        assert real.policy_acceptance["counts"] == synthetic.policy_acceptance["counts"]
        assert real.decisions == synthetic.decisions
        assert real.graded == synthetic.graded

    def test_the_sentinel_never_appears_on_this_machine(self) -> None:
        """Criterion 1's empirical half — a fact about the pipeline, not a reading.

        The fixture's source writes this path on whatever machine runs it. It
        has now been through extraction, program construction, the double, the
        classifier, the escalation lane and the grader. If any of those had run
        it here, the file would exist.
        """
        fixture = FIXTURES_BY_NAME["sentinel"]
        target = Path(ap.sentinel_path())
        assert "write_text" in fixture.source
        assert str(target) in fixture.source
        record = _grade_fixture(fixture)
        assert record.policy_acceptance["counts"]["decided"] == len(_episode())
        assert not target.exists(), f"{target} exists: something ran the policy on this host"


class TestTheOnlyExecutionPathIsTheJail:
    """Criterion 1, second mechanism: where the policy source actually goes.

    Read off the invocation the harness constructed rather than off a reading of
    its source — ``tests/test_workspace.py``'s argument about secrets, applied
    to a policy.
    """

    @staticmethod
    def _run_and_record(arm_id: str = ap.ARM_BASELINE) -> tuple[ScriptedApi, Any]:
        episode = _episode()
        api = ScriptedApi(_stdout_for(episode, _perfect(episode)))
        record = _run(arm_id, api=api, situations=episode)
        return api, record

    def test_the_policy_source_reaches_the_engine_only_as_an_argv_element(self) -> None:
        api, _ = self._run_and_record()
        argvs = api.argv_for_runs()
        assert len(argvs) == 1
        assert argvs[0][:2] == ["python3", "-c"]
        assert f"def {ap.ENTRY_POINT}" in argvs[0][2]

    def test_a_model_written_policy_takes_the_same_one_path(self) -> None:
        episode = _episode()
        api = ScriptedApi(_stdout_for(episode, _perfect(episode)))
        _run(
            ap.ARM_COMPILED,
            api=api,
            minds={
                aa.ROLE_CORTEX: _mind(f"```python\ndef {ap.ENTRY_POINT}(s):\n    return None\n```")
            },
            situations=episode,
        )
        argvs = api.argv_for_runs()
        assert len(argvs) == 1
        assert argvs[0][:2] == ["python3", "-c"]
        assert f"def {ap.ENTRY_POINT}" in argvs[0][2]

    def test_nothing_but_the_workspace_run_verb_is_reached(self) -> None:
        api, _ = self._run_and_record()
        assert {name for name, _, _ in api.calls} <= {"create", "run", "destroy"}

    def test_create_carries_no_policy_and_no_profile(self) -> None:
        api, _ = self._run_and_record()
        assert api.kwargs_for("create") == [{"provider": PROVIDER_FAKE}]

    def test_put_and_export_are_never_called(self) -> None:
        api, _ = self._run_and_record()
        assert not [name for name, _, _ in api.calls if name in {"put", "export"}]

    def test_the_run_carries_no_environment(self) -> None:
        api, _ = self._run_and_record()
        for kwargs in api.kwargs_for("run"):
            assert set(kwargs) == {"provider"}, kwargs

    def test_the_harness_names_the_workspace_tool_exactly_once(self) -> None:
        """One call site, so widening the execution seam is a reviewable diff."""
        sites = [
            node
            for node in ast.walk(ast.parse(HARNESS_PATH.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]
        assert len(sites) == 1

    def test_one_attempt_runs_the_jail_exactly_once(self) -> None:
        api, record = self._run_and_record()
        assert len([name for name, _, _ in api.calls if name == "run"]) == 1
        assert record.jail["runs"] == 1

    def test_no_container_means_no_run_and_never_a_fallback(self) -> None:
        api = ScriptedApi(create_error=RuntimeError("no engine"))
        record = _run(ap.ARM_BASELINE, api=api)
        assert record.verdict == ap.VERDICT_NO_WORKSPACE
        assert not [name for name, _, _ in api.calls if name == "run"]
        assert record.decisions == {}

    def test_the_container_never_receives_a_truth_label(self) -> None:
        for situation in _episode():
            payload = situation.to_payload(escalate=ap.ESCALATE, unobserved=ap.UNOBSERVED, seed=1)
            assert set(payload) == set(ap.PAYLOAD_KEYS)
            assert "truth" not in payload
            assert "decidable" not in payload

    def test_an_unobserved_feature_is_hidden_from_the_container(self) -> None:
        """The occluded situations are genuinely occluded, not merely labelled."""
        occluded = [s for s in _episode() if not s.decidable]
        assert occluded, "the demo episode must exercise the escalate return"
        for situation in occluded:
            payload = situation.to_payload(escalate=ap.ESCALATE, unobserved=ap.UNOBSERVED, seed=1)
            hidden = [
                name
                for name, value in situation.hidden.items()
                if payload["features"][name] == ap.UNOBSERVED and value != ap.UNOBSERVED
            ]
            assert hidden, situation.id
            for name in hidden:
                assert payload["features"][name] == ap.UNOBSERVED


# ══════════════════════════════════════════════════════════════════════════════
# criterion 2 — the three controls
# ══════════════════════════════════════════════════════════════════════════════


class TestTheThreeControlsAreDialable:
    """Criterion 2: random, hand-written baseline and no-op are dialable arms.

    "Dialable" is checked as three separate facts, because any one of them
    alone would let a control exist on paper only: the kind is declared, the
    arm carries a committed configuration cell, and the arm runs end to end.
    """

    def test_all_three_control_kinds_exist(self) -> None:
        kinds = {ap.POLICY_ARMS[arm].source.kind for arm in ap.POLICY_ARM_ORDER}
        assert {ap.KIND_RANDOM, ap.KIND_BASELINE, ap.KIND_NOOP} <= kinds

    def test_each_control_kind_is_carried_by_exactly_one_arm(self) -> None:
        for kind in ap.CONTROL_KINDS:
            arms = [a for a in ap.POLICY_ARM_ORDER if ap.POLICY_ARMS[a].source.kind == kind]
            assert len(arms) == 1, (kind, arms)

    @pytest.mark.parametrize("kind", list(ap.CONTROL_KINDS))
    def test_every_control_has_a_committed_configuration_cell(self, kind: str) -> None:
        config = _config()
        arm = next(a for a in ap.POLICY_ARM_ORDER if ap.POLICY_ARMS[a].source.kind == kind)
        assert config.budget_for(arm).authoring_calls == 0
        assert config.play_for(arm).grain in config.grains

    @pytest.mark.parametrize("kind", list(ap.CONTROL_KINDS))
    def test_every_control_runs_end_to_end(self, kind: str) -> None:
        arm = next(a for a in ap.POLICY_ARM_ORDER if ap.POLICY_ARMS[a].source.kind == kind)
        record = _run(arm)
        assert record.arm == arm
        assert record.graded["attempted"] == len(_episode())

    def test_no_control_makes_an_authoring_call(self) -> None:
        for arm_id in ap.POLICY_ARM_ORDER:
            arm = ap.POLICY_ARMS[arm_id]
            if arm.is_control:
                assert arm.authoring_calls == 0
                assert aa.ROLE_CORTEX not in arm.configured_roles

    def test_the_compiled_arm_is_not_a_control(self) -> None:
        assert not ap.POLICY_ARMS[ap.ARM_COMPILED].is_control
        assert ap.POLICY_ARMS[ap.ARM_COMPILED].authoring_calls == 1

    def test_the_controls_were_registered_before_any_measured_dial(self) -> None:
        """The whole module is hermetic: there is no live dial to register before."""
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "require_live_rig" not in source
        assert "LiveSeams" not in source


class TestTheControlsMeasureWhatTheyAreFor:
    """The controls' *purpose*, checked rather than asserted in a docstring."""

    def test_the_noop_control_tells_a_policy_from_the_harness(self) -> None:
        """A no-op scores nothing: the harness hands out no points on its own."""
        episode = _episode()
        record = _run(ap.ARM_NOOP, values=[None] * len(episode), situations=episode)
        assert record.graded["correct"] == 0
        assert record.graded["decided"] == 0

    def test_the_random_control_is_a_strategy_shaped_program(self) -> None:
        """Syntactically valid, legal moves, no strategy — the arm that tells
        'the model can compile a strategy' from 'any program beats per-turn'."""
        source = ap.COMMITTED_SOURCES[ap.KIND_RANDOM]
        assert f"def {ap.ENTRY_POINT}" in source.text
        assert ap.ESCALATE not in source.text

    def test_the_hand_written_baseline_is_the_ceiling(self) -> None:
        episode = _episode()
        record = _run(
            ap.ARM_BASELINE,
            values=_perfect(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
        )
        decidable = [s for s in episode if s.decidable]
        assert record.graded["correct"] >= len(decidable)

    def test_a_valid_but_inert_policy_still_runs_to_completion(self) -> None:
        """The failure mode the controls exist for: legal moves, no strategy."""
        episode = _episode()
        record = _run(ap.ARM_RANDOM, values=[ap.ACTIONS[0]] * len(episode), situations=episode)
        assert record.verdict in (ap.VERDICT_CORRECT, ap.VERDICT_WRONG)
        assert record.policy_acceptance["acceptance_rate"] == 1.0


# ══════════════════════════════════════════════════════════════════════════════
# criterion 3 — three disjoint reported columns
# ══════════════════════════════════════════════════════════════════════════════


class TestOutcomeEscalationAndAcceptanceAreDisjoint:
    """Criterion 3: escalation rate and call-acceptance are separate columns.

    Issue #33 measured 17 of 23 calls refused on a shape error. A harness that
    folded refusals into outcome would report a broken arm as a losing arm; one
    that folded escalation into outcome could not tell a policy that answered
    from one that asked. So there are three key sets and they are pairwise
    disjoint — asserted, not trusted.
    """

    def test_the_three_key_sets_are_pairwise_disjoint(self) -> None:
        outcome = set(ap.OUTCOME_KEYS)
        escalation = set(ap.ESCALATION_KEYS)
        acceptance = set(ap.ACCEPTANCE_KEYS)
        assert not outcome & escalation
        assert not outcome & acceptance
        assert not escalation & acceptance

    def test_every_declared_key_is_actually_reported(self) -> None:
        payload = _run(ap.ARM_BASELINE).to_dict()
        for key in ap.OUTCOME_KEYS + ap.ESCALATION_KEYS + ap.ACCEPTANCE_KEYS:
            assert key in payload, key

    def test_the_outcome_block_carries_no_escalation_or_acceptance_word(self) -> None:
        graded = _run(ap.ARM_BASELINE).graded
        rendered = json.dumps(graded).lower()
        for word in ("escalat", "accept", "refus"):
            assert word not in rendered, (word, graded)

    def test_the_escalation_block_carries_no_correctness_word(self) -> None:
        escalation = _run(ap.ARM_BASELINE).escalation
        rendered = json.dumps(escalation).lower()
        for word in ("correct", "wrong", "verdict"):
            assert word not in rendered, (word, escalation)

    def test_the_acceptance_blocks_carry_no_correctness_word(self) -> None:
        record = _run(ap.ARM_BASELINE)
        for block in (record.policy_acceptance, record.escalation_acceptance):
            rendered = json.dumps(block).lower()
            for word in ("correct", "wrong", "verdict"):
                assert word not in rendered, (word, block)

    def test_an_interface_failure_and_a_strategy_failure_never_share_a_number(self) -> None:
        """A policy that returns garbage and one that returns wrong-but-legal
        moves must be distinguishable from the record alone."""
        episode = _episode()
        garbage = _run(ap.ARM_RANDOM, values=["NOT_AN_ACTION"] * len(episode), situations=episode)
        legal = _run(ap.ARM_RANDOM, values=[ap.ACTIONS[0]] * len(episode), situations=episode)
        assert garbage.policy_acceptance["acceptance_rate"] == 0.0
        assert legal.policy_acceptance["acceptance_rate"] == 1.0
        assert garbage.graded["correct"] == 0

    def test_a_refused_escalation_is_not_a_wrong_answer(self) -> None:
        episode = _episode()
        record = _run(
            ap.ARM_BASELINE,
            values=[ap.ESCALATE] * len(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker(off_space=True)},
        )
        assert record.escalation_acceptance["refusal_rate"] == 1.0
        assert record.graded["decided"] == 0

    def test_escalation_rate_is_reported_at_both_extremes(self) -> None:
        episode = _episode()
        never = _run(ap.ARM_RANDOM, values=[ap.ACTIONS[0]] * len(episode), situations=episode)
        always = _run(
            ap.ARM_BASELINE,
            values=[ap.ESCALATE] * len(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
        )
        assert never.escalation["escalation_rate"] == 0.0
        assert always.escalation["escalation_rate"] == 1.0

    def test_a_fully_escalating_policy_is_named_as_collapsed(self) -> None:
        """Pre-declared in the committed table, so it cannot be argued about after."""
        episode = _episode()
        record = _run(
            ap.ARM_BASELINE,
            values=[ap.ESCALATE] * len(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
        )
        assert record.escalation["collapsed"] is True
        assert _config().decision["escalation_rate_collapses_at"] == 1.0

    def test_the_refuting_threshold_is_committed_config_not_a_literal(self) -> None:
        assert _config().decision["call_acceptance_refutes_at_rate"] == 0.74
        assert "0.74" not in HARNESS_PATH.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# the escalate return is first-class
# ══════════════════════════════════════════════════════════════════════════════


class TestTheEscalateReturnIsFirstClass:
    """ "I don't know" is part of the contract, not an exception path."""

    def test_the_sentinel_is_declared_once_and_named_in_the_contract(self) -> None:
        assert ap.ESCALATE
        assert ap.ESCALATE not in ap.ACTIONS
        assert ap.ESCALATE in ap.POLICY_CONTRACT

    def test_the_sentinel_reaches_the_policy_through_the_payload(self) -> None:
        payload = _episode()[0].to_payload(escalate=ap.ESCALATE, unobserved=ap.UNOBSERVED, seed=1)
        assert payload["escalate"] == ap.ESCALATE
        assert payload["unobserved"] == ap.UNOBSERVED

    def test_escalating_is_in_protocol_and_returning_junk_is_not(self) -> None:
        assert ap.ESCALATED in ap.IN_PROTOCOL
        assert ap.DECIDED in ap.IN_PROTOCOL
        assert ap.OFF_PROTOCOL not in ap.IN_PROTOCOL

    def test_the_router_grades_itself(self) -> None:
        """Escalation quality is measured against host-side decidability."""
        episode = _episode()
        record = _run(
            ap.ARM_BASELINE,
            values=_perfect(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
        )
        assert record.escalation["escalated_undecidable"] == sum(
            1 for s in episode if not s.decidable
        )
        assert record.escalation["escalated_decidable"] == 0
        assert record.escalation["unescalated_undecidable"] == 0
        assert record.escalation["escalation_precision"] == 1.0
        assert record.escalation["escalation_recall"] == 1.0

    def test_over_confidence_is_measured_not_only_over_escalation(self) -> None:
        episode = _episode()
        record = _run(ap.ARM_RANDOM, values=[ap.ACTIONS[0]] * len(episode), situations=episode)
        assert record.escalation["unescalated_undecidable"] == sum(
            1 for s in episode if not s.decidable
        )
        assert record.escalation["escalation_recall"] == 0.0

    def test_an_arm_that_escalated_nothing_has_no_precision(self) -> None:
        episode = _episode()
        record = _run(ap.ARM_RANDOM, values=[ap.ACTIONS[0]] * len(episode), situations=episode)
        assert record.escalation["escalation_precision"] is None


# ══════════════════════════════════════════════════════════════════════════════
# termination (c11/h15)
# ══════════════════════════════════════════════════════════════════════════════


def _tree() -> ast.Module:
    return ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))


def _functions() -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(_tree())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_settled(iterator: ast.AST) -> bool:
    """Whether a ``for``'s iterable is a settled sequence rather than a stream."""
    if isinstance(iterator, (ast.Name, ast.Attribute, ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return True
    if isinstance(iterator, ast.Subscript):
        return True
    if isinstance(iterator, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
        return all(_is_settled(gen.iter) for gen in iterator.generators)
    if isinstance(iterator, ast.Call):
        func = iterator.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name in {
            "range",
            "enumerate",
            "zip",
            "sorted",
            "items",
            "values",
            "keys",
            "splitlines",
            "split",
            "tuple",
            "list",
            "chunk",
            "walk",
        }
    return False


class TestPolicyTerminates:
    """Claim ``c11``/``h15``: structural, not only behavioural.

    The shape ``tests/test_orchestrator_tools.py``'s ``TestFanoutTerminates``
    and ``tests/test_arch_hive.py``'s ``TestHiveTerminates`` established.
    """

    def test_the_module_contains_no_while_loop_at_all(self) -> None:
        assert not [node for node in ast.walk(_tree()) if isinstance(node, ast.While)]

    def test_every_iteration_walks_a_settled_sequence(self) -> None:
        unsettled = [
            ast.unparse(node.iter)
            for node in ast.walk(_tree())
            if isinstance(node, (ast.For, ast.AsyncFor)) and not _is_settled(node.iter)
        ]
        assert not unsettled, unsettled

    def test_the_module_never_sleeps_and_never_polls(self) -> None:
        assert "sleep" not in HARNESS_PATH.read_text(encoding="utf-8")

    def test_the_module_starts_no_thread_of_its_own(self) -> None:
        """Concurrency is borrowed from ``arch_hive.dispatch``, whose bound is
        proved there — so this module carries no pool to reason about."""
        source = HARNESS_PATH.read_text(encoding="utf-8")
        for word in ("ThreadPoolExecutor", "threading", "Future"):
            assert word not in source, word

    def test_the_escalation_count_is_bounded_before_any_call_exists(self) -> None:
        config = _config()
        episode = ap.demo_episode(config.episode)
        calls, refused = ap.plan_escalations(
            episode,
            grain=config.grain("unit"),
            budget=3,
            stem="e",
        )
        assert len(calls) == 3
        assert len(refused) == len(episode) - 3

    def test_a_zero_budget_dispatches_nothing_and_names_everything(self) -> None:
        config = _config()
        episode = ap.demo_episode(config.episode)
        calls, refused = ap.plan_escalations(
            episode, grain=config.grain("unit"), budget=0, stem="e"
        )
        assert calls == ()
        assert len(refused) == len(episode)

    def test_an_episodes_escalation_calls_never_pass_its_budget(self) -> None:
        config = _config()
        episode = ap.demo_episode(config.episode)
        record = _run(
            ap.ARM_BASELINE,
            values=[ap.ESCALATE] * len(episode),
            situations=episode,
            minds={aa.ROLE_WORKER: ah.scripted_worker()},
            config=config,
        )
        assert record.escalation_calls <= config.budget_for(ap.ARM_BASELINE).max_escalation_calls

    def test_the_authoring_lane_makes_exactly_one_completion(self) -> None:
        node = _functions()["author_policy"]
        calls = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        ]
        assert [call.func.id for call in calls].count("mind") == 1
        assert not [child for child in ast.walk(node) if isinstance(child, (ast.For, ast.While))]

    def test_a_compiled_run_dials_the_cortex_exactly_once(self) -> None:
        log = aa.CallLog()
        _run(
            ap.ARM_COMPILED,
            minds={
                aa.ROLE_CORTEX: _mind(f"```python\ndef {ap.ENTRY_POINT}(s):\n    return None\n```")
            },
            log=log,
        )
        assert [r.role for r in log.records].count(aa.ROLE_CORTEX) == 1

    def test_no_call_site_can_smuggle_an_argument_through_a_passthrough(self) -> None:
        """t6's structural prerequisite, discharged by enumeration everywhere.

        ``arch_arms._drive`` takes ``**kwargs``, so "no subagent seam" cannot be
        read off its call site. This module takes the property further: **no
        function here accepts ``**kwargs`` and no call here unpacks one**, so
        there is no hole an edit could route an argument through at all.
        """
        tree = _tree()
        with_kwargs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.kwarg
        ]
        assert not with_kwargs, with_kwargs
        unpacked = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and any(kw.arg is None for kw in node.keywords)
        ]
        assert not unpacked, unpacked

    def test_no_drive_is_started_here_at_all(self) -> None:
        """Arm P holds no tool loop: one completion, then a program plays.

        A stronger form of "the worker never holds a turn" than arm B's — there
        is no :func:`embodiment.run` call site to inspect, because there is no
        drive.
        """
        source = HARNESS_PATH.read_text(encoding="utf-8")
        assert "embodiment import" not in source or "run," not in source
        called = _called_bare(source) | _called_attrs(source)
        assert "run" not in called

    @pytest.mark.parametrize(
        "name",
        [
            "test_the_module_contains_no_while_loop_at_all",
            "test_every_iteration_walks_a_settled_sequence",
        ],
    )
    def test_the_proof_is_not_vacuous(self, name: str) -> None:
        """The AST checks would fail on a module that broke them."""
        planted = ast.parse("while True:\n    pass\nfor x in stream():\n    pass\n")
        assert [n for n in ast.walk(planted) if isinstance(n, ast.While)]
        assert not _is_settled(next(n for n in ast.walk(planted) if isinstance(n, ast.For)).iter)


# ══════════════════════════════════════════════════════════════════════════════
# arms are data
# ══════════════════════════════════════════════════════════════════════════════


class TestArmsAreData:
    def test_the_four_arms_differ_in_exactly_one_field(self) -> None:
        fields = set()
        arms = [ap.POLICY_ARMS[name] for name in ap.POLICY_ARM_ORDER]
        for left in arms:
            for right in arms:
                if left.id == right.id:
                    continue
                if left.source != right.source:
                    fields.add("source")
        assert fields == {"source"}
        for left in arms:
            for right in arms:
                if left.id != right.id:
                    assert left.grader is right.grader

    def test_nothing_branches_on_an_arm_id(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        for arm_id in ap.POLICY_ARM_ORDER:
            assert f'== "{arm_id}"' not in source
            assert f'!= "{arm_id}"' not in source
            assert f"arm == {arm_id!r}" not in source

    def test_the_source_is_read_off_the_arm(self) -> None:
        for arm_id in ap.POLICY_ARM_ORDER:
            arm = ap.POLICY_ARMS[arm_id]
            if arm.is_control:
                assert arm.source.text == ap.COMMITTED_SOURCES[arm.source.kind].text
            else:
                assert arm.source.text == ""

    def test_every_arm_is_graded_the_same_way(self) -> None:
        records = {arm: _run(arm) for arm in (ap.ARM_RANDOM, ap.ARM_BASELINE, ap.ARM_NOOP)}
        for record in records.values():
            assert set(record.graded) == set(records[ap.ARM_RANDOM].graded)


# ══════════════════════════════════════════════════════════════════════════════
# config only, no code defaults
# ══════════════════════════════════════════════════════════════════════════════


def _write_config(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    raw = json.loads(ap.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "table.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class TestConfigOnlyNoCodeDefaults:
    def test_the_committed_table_loads_and_covers_every_arm_and_role(self) -> None:
        config = _config()
        for arm_id in ap.POLICY_ARM_ORDER:
            for role in ap.POLICY_ARMS[arm_id].configured_roles:
                assert config.sampling_for(arm_id, role)

    def test_a_missing_sampling_field_raises_naming_the_arm_and_role(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path, lambda raw: raw["sampling"]["PR"]["worker"].pop("temperature")
        )
        with pytest.raises(ap.ConfigError, match="sampling.PR.worker"):
            ap.load_policy_config(path)

    def test_a_missing_budget_cell_raises(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, lambda raw: raw["budgets"].pop("PH"))
        with pytest.raises(ap.ConfigError, match="PH"):
            ap.load_policy_config(path)

    def test_an_authoring_budget_that_disagrees_with_the_arm_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A control granted an authoring call, or P granted two, is a different arm."""
        path = _write_config(tmp_path, lambda raw: raw["budgets"]["PR"].update(authoring_calls=1))
        with pytest.raises(ap.ConfigError, match="authoring_calls"):
            ap.load_policy_config(path)

    def test_a_retry_budget_for_the_compiled_arm_is_refused(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, lambda raw: raw["budgets"]["P"].update(authoring_calls=2))
        with pytest.raises(ap.ConfigError, match="authoring_calls"):
            ap.load_policy_config(path)

    def test_an_undeclared_grain_is_refused_at_load(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, lambda raw: raw["policy"]["P"].update(grain="huge"))
        with pytest.raises(ap.ConfigError, match="huge"):
            ap.load_policy_config(path)

    def test_an_undeclared_escalation_role_is_refused_at_load(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path, lambda raw: raw["policy"]["P"].update(escalation_role="oracle")
        )
        with pytest.raises(ap.ConfigError, match="oracle"):
            ap.load_policy_config(path)

    def test_a_non_finite_deadline_is_refused_at_load(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, lambda raw: raw["dispatch"].update(batch_timeout_seconds=0))
        with pytest.raises(ap.ConfigError, match="batch_timeout_seconds"):
            ap.load_policy_config(path)

    def test_no_model_id_is_hard_coded_in_the_module(self) -> None:
        source = HARNESS_PATH.read_text(encoding="utf-8")
        for fragment in ("Qwen", "gemma", "Gemma", "NVFP4"):
            assert fragment not in source, fragment

    def test_a_missing_table_raises_naming_the_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.json"
        with pytest.raises(ap.ConfigError, match=str(missing)):
            ap.load_policy_config(missing)

    def test_senses_is_identical_across_arms_and_hashed(self) -> None:
        assert ap.assert_senses_identical(_config())

    def test_a_senses_drift_between_arms_is_refused(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path, lambda raw: raw["sampling"]["PN"]["senses"].update(temperature=0.9)
        )
        config = ap.load_policy_config(path)
        with pytest.raises(ap.ConfigError, match="senses"):
            ap.assert_senses_identical(config)


# ══════════════════════════════════════════════════════════════════════════════
# the harness runs
# ══════════════════════════════════════════════════════════════════════════════


class TestTheHarnessRuns:
    @pytest.mark.parametrize("arm_id", list(ap.POLICY_ARM_ORDER))
    def test_an_arm_drives_end_to_end_and_grades(self, arm_id: str) -> None:
        record = _run(
            arm_id,
            minds={
                aa.ROLE_CORTEX: _mind(
                    f"```python\ndef {ap.ENTRY_POINT}(s):\n    return s['actions'][0]\n```"
                ),
                aa.ROLE_WORKER: ah.scripted_worker(),
            },
        )
        assert record.arm == arm_id
        assert record.verdict in ap.VERDICTS
        assert record.graded["attempted"] == len(_episode())

    def test_the_cli_plan_and_contract_render(self, capsys: Any) -> None:
        assert ap.main(["plan"]) == 0
        assert "arm P" in capsys.readouterr().out
        assert ap.main(["contract"]) == 0
        assert ap.ENTRY_POINT in capsys.readouterr().out

    def test_the_cli_config_reads_the_committed_table(self, capsys: Any) -> None:
        assert ap.main(["config", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["senses_config_hash"]

    def test_a_model_reply_with_no_code_is_recorded_not_run(self) -> None:
        api = ScriptedApi("")
        record = _run(
            ap.ARM_COMPILED,
            api=api,
            minds={aa.ROLE_CORTEX: _mind("I would rather not write that.")},
        )
        assert record.verdict == ap.VERDICT_NO_SOURCE
        assert not [name for name, _, _ in api.calls if name == "run"]
        assert record.raw_response

    def test_a_missing_nonce_line_is_never_correct(self) -> None:
        record = _run(ap.ARM_BASELINE, api=ScriptedApi("no result here\n"))
        assert record.verdict == ap.VERDICT_NO_RESULT
        assert record.vacuity["fired"] is False

    def test_two_nonce_lines_are_refused_as_a_possible_forgery(self) -> None:
        episode = _episode()
        api = ScriptedApi(_stdout_for(episode, _perfect(episode), repeat=2))
        record = _run(ap.ARM_BASELINE, api=api, situations=episode)
        assert record.verdict == ap.VERDICT_NO_RESULT
        assert record.spoof_suspected is True

    def test_the_raw_container_output_is_committed_on_every_record(self) -> None:
        record = _run(ap.ARM_BASELINE)
        assert record.jail["output"]
