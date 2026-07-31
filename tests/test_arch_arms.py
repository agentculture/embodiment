"""Task t5 — the architecture arms harness, and the four criteria it answers for.

Every test here is hermetic. Nothing in this module opens a socket: the live
lane is gated on ``EMBODIMENT_LIVE_RIG=1`` inside the harness itself and is
never exercised here (the operator has frozen live runs), and the two seam
classes are driven either by a scripted mind or by a monkey-patched
``ArchSeam._transport``, which is the same technique
``tests/test_worker_seam.py`` and ``tests/test_league_h2h.py`` use on
``WorkerSeam._post`` — one level lower here, so the body-shaping step (the
thinking-mode wire keys) stays *inside* the code under test.

Four acceptance criteria, four test classes carrying the weight:

* :class:`TestPerCallRecords` — criterion 1. Every model call lands ONE record
  carrying ``finish_reason``, prompt / completion / **reasoning** tokens
  separately, role, model and arm. ``ModelResponse`` carries no
  ``finish_reason`` (issue #37), so the record is built from the raw response.
* :class:`TestAnalyseRefusesWithoutBothFlatArms` — criterion 2. The ``analyse``
  verb refuses a verdict when either flat arm's cell is missing, and reports
  missing cells ``ABSENT``.
* :class:`TestSensesIsIdenticalAcrossArms` — criterion 3. Senses configuration
  is byte-identical across arms, asserted by a config hash.
* :class:`TestSamplingTableComesFromConfig` — criterion 4. Temperature,
  thinking mode and ``max_tokens``, per role per arm, are read from committed
  config and from nowhere else — no code default exists to fall back to.

The remaining classes hold the harness itself honest: the four arms exist and
run end-to-end, the worker never holds final authority, a degenerate hybrid
cell is excluded by rule rather than by judgement, and no cortex model id is
hard-coded anywhere (the rig's cortex is mid-upgrade).
"""

from __future__ import annotations

import ast
import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment.contract import ModelResponse, ToolCall  # noqa: E402
from examples import arch_arms as aa  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "examples" / "arch_arms.py"


# ── shared fixtures: a tiny problem, a tiny ladder, scripted minds ────────────


def _tiny_problem(problem_id: str = "tiny", difficulty: str = aa.DIFFICULTY_SIMPLE) -> aa.Problem:
    """A one-verb problem whose grader is a string compare. Not a challenge.

    Used where the test is about the harness rather than about a committed
    challenge problem — driving the real subset problem through every branch
    would measure `examples/challenge_subset.py`, which task t5 does not own.
    """

    class _Bench:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def execute(self, name: str, arguments: dict[str, Any]) -> Any:
            from embodiment import ToolOutcome

            self.calls.append((name, dict(arguments)))
            if name == "finish":
                return ToolOutcome(
                    result="submitted",
                    finished=True,
                    finish_summary=str(arguments.get("answer", "")),
                )
            return ToolOutcome(result=f"looked at {arguments}")

        def state(self) -> str:
            return f"{len(self.calls)} call(s)"

    def _grade(raw: str) -> dict[str, Any]:
        return {
            "answer": raw,
            "expected": "42",
            "is_correct": raw.strip() == "42",
            "verdict": "CORRECT" if raw.strip() == "42" else "WRONG",
        }

    return aa.Problem(
        id=problem_id,
        statement="What is the answer? Call finish with it.",
        difficulty=difficulty,
        failure_class="test-fixture",
        source="tests/test_arch_arms.py",
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "consider",
                    "description": "Think about it.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "Submit the answer.",
                    "parameters": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                },
            },
        ),
        make_bench=_Bench,
        grade=_grade,
    )


def _tiny_registry() -> dict[str, aa.Problem]:
    return {
        "easy": _tiny_problem("easy", aa.DIFFICULTY_SIMPLE),
        "hard": _tiny_problem("hard", aa.DIFFICULTY_COMPLEX),
    }


def _flat_mind(answer: str = "42"):
    """A mind that answers immediately by calling ``finish``."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(
            content="Answering.",
            reasoning="thinking about it",
            tool_calls=[ToolCall(id="c1", name="finish", arguments={"answer": answer})],
        )

    return complete


def _delegating_cortex(answer: str = "42", *, reason: str = "bulk search"):
    """Delegate once, then write the final answer itself."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        delegated = any(
            message.get("role") == "tool"
            and aa.DELEGATION_MARKER in str(message.get("content") or "")
            for message in messages
        )
        if not delegated:
            return ModelResponse(
                content="Handing this to the worker.",
                tool_calls=[
                    ToolCall(
                        id="c-delegate",
                        name="delegate",
                        arguments={"subtask": "do the ground work", "reason": reason},
                    )
                ],
            )
        return ModelResponse(
            content="I have what I need.",
            tool_calls=[ToolCall(id="c-finish", name="finish", arguments={"answer": answer})],
        )

    return complete


def _self_solving_cortex(answer: str = "42"):
    """Never delegates: uses the bench itself, then finishes. The hybrid's other leg."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        used = any(
            message.get("role") == "tool" and "looked at" in str(message.get("content") or "")
            for message in messages
        )
        if not used:
            return ModelResponse(
                content="I will work this one myself.",
                tool_calls=[ToolCall(id="c-consider", name="consider", arguments={})],
            )
        return ModelResponse(
            content="Done.",
            tool_calls=[ToolCall(id="c-finish", name="finish", arguments={"answer": answer})],
        )

    return complete


def _reporting_worker(finding: str = "42"):
    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(
            content="Reporting back.",
            tool_calls=[ToolCall(id="c-report", name="report", arguments={"findings": finding})],
        )

    return complete


def _scripted_minds(*, cortex=None, worker=None) -> dict[str, Any]:
    return {
        aa.ROLE_CORTEX: cortex or _flat_mind(),
        aa.ROLE_WORKER: worker or _flat_mind(),
    }


def _ctx(**kw: Any) -> aa.CallContext:
    fields: dict[str, Any] = {
        "arm": aa.ARM_EXISTING,
        "rung": "C1",
        "problem": "tiny",
        "route": aa.ROUTE_TEXT,
        "senses_hash": "deadbeef",
        "live": False,
    }
    fields.update(kw)
    return aa.CallContext(**fields)


def _payload(
    *,
    content: str = "hello",
    reasoning: str = "thought",
    finish_reason: str = "stop",
    prompt_tokens: int = 11,
    completion_tokens: int = 500,
    reasoning_tokens: Optional[int] = 420,
    flat: bool = False,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if reasoning_tokens is not None:
        if flat:
            usage["reasoning_tokens"] = reasoning_tokens
        else:
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content, "reasoning_content": reasoning},
            }
        ],
        "usage": usage,
    }


def _write_config(tmp_path: Path, mutate=None) -> Path:
    """A copy of the committed sampling table, optionally mutated."""
    raw = json.loads(aa.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(raw)
    path = tmp_path / "sampling.json"
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


# ── criterion 4: the sampling table is committed config, not a code default ───


class TestSamplingTableComesFromConfig:
    """Temperature, thinking mode and ``max_tokens`` per role per arm.

    ``docs/live-test-results/configurations.md`` records temperature as a hidden
    variable that confounded an entire prior series: four harnesses hard-coded
    four different values and one of them was chosen by vibe. This criterion is
    the fix, and it is stronger than "the value is also written down" — there
    must be **no value in the code at all** to fall back to.
    """

    def test_the_committed_table_loads_and_covers_every_arm_and_role(self) -> None:
        config = aa.load_config()
        assert config.path == aa.DEFAULT_CONFIG_PATH
        for arm in aa.ARM_ORDER:
            for role in aa.ARMS[arm].configured_roles:
                sampling = config.sampling_for(arm, role)
                assert isinstance(sampling.temperature, float)
                assert sampling.thinking in config.thinking_modes
                assert isinstance(sampling.max_tokens, int)

    def test_budgets_are_16000_everywhere_per_d16(self) -> None:
        # Criterion 1's other half: d16 raised the budget after t24 measured the
        # 2048 default truncating 6.0% of completions with ZERO degradations.
        config = aa.load_config()
        for arm in aa.ARM_ORDER:
            for role in aa.ARMS[arm].configured_roles:
                assert config.sampling_for(arm, role).max_tokens == 16000

    def test_a_missing_sampling_field_raises_rather_than_defaulting(self, tmp_path: Path) -> None:
        def drop_temperature(raw: dict[str, Any]) -> None:
            del raw["sampling"]["E"]["cortex"]["temperature"]

        path = _write_config(tmp_path, drop_temperature)
        with pytest.raises(aa.ConfigError) as caught:
            aa.load_config(path)
        assert "temperature" in str(caught.value)
        assert "E" in str(caught.value) and "cortex" in str(caught.value)

    def test_a_missing_role_cell_raises_naming_the_arm_and_role(self, tmp_path: Path) -> None:
        def drop_worker(raw: dict[str, Any]) -> None:
            del raw["sampling"]["M"]["worker"]

        path = _write_config(tmp_path, drop_worker)
        config_error = pytest.raises(aa.ConfigError)
        with config_error as caught:
            aa.load_config(path)
        assert "M" in str(caught.value) and "worker" in str(caught.value)

    def test_the_committed_values_are_what_reaches_the_wire(self, tmp_path: Path) -> None:
        """Change the file, and the body posted changes. Nothing else can set them."""

        def odd_values(raw: dict[str, Any]) -> None:
            raw["sampling"]["E"]["cortex"] = {
                "temperature": 0.91,
                "thinking": "off",
                "max_tokens": 123,
            }

        config = aa.load_config(_write_config(tmp_path, odd_values))
        log = aa.CallLog()
        seam = aa.ArchSeam(
            dial=aa.Dial(role=aa.ROLE_CORTEX, model="m", base_url="http://x/v1", api_key="k"),
            sampling=config.sampling_for(aa.ARM_EXISTING, aa.ROLE_CORTEX),
            wire_extra=config.wire_extra("off"),
            role=aa.ROLE_CORTEX,
            ctx=_ctx(),
            log=log,
        )
        sent: dict[str, Any] = {}
        seam._transport = lambda body: (sent.update(body), _payload())[1]
        seam([{"role": "user", "content": "hi"}])

        assert sent["temperature"] == 0.91
        assert sent["max_tokens"] == 123
        assert sent["chat_template_kwargs"] == {"enable_thinking": False}
        assert log.records[0].temperature == 0.91
        assert log.records[0].max_tokens == 123
        assert log.records[0].thinking == "off"

    def test_thinking_mode_wire_keys_come_from_the_file_too(self, tmp_path: Path) -> None:
        def rename_wire(raw: dict[str, Any]) -> None:
            raw["thinking_wire"]["modes"]["on"] = {"extra_body": {"reasoning": "high"}}

        config = aa.load_config(_write_config(tmp_path, rename_wire))
        assert config.wire_extra("on") == {"extra_body": {"reasoning": "high"}}

    def test_an_explicitly_empty_thinking_mode_sends_nothing(self, tmp_path: Path) -> None:
        def empty_wire(raw: dict[str, Any]) -> None:
            raw["thinking_wire"]["modes"]["on"] = {}

        config = aa.load_config(_write_config(tmp_path, empty_wire))
        assert config.wire_extra("on") == {}

    def test_an_unmapped_thinking_mode_is_refused_at_load(self, tmp_path: Path) -> None:
        def unmapped(raw: dict[str, Any]) -> None:
            raw["sampling"]["E"]["cortex"]["thinking"] = "medium"

        with pytest.raises(aa.ConfigError) as caught:
            aa.load_config(_write_config(tmp_path, unmapped))
        assert "medium" in str(caught.value)

    def test_an_unmapped_thinking_mode_is_refused_at_use_too(self) -> None:
        """The load-time check and the wire-time check are separate guards.

        Only the second one protects a caller that reaches for a mode directly,
        and a silent ``{}`` there would mean the recorded mode and the dialled
        mode disagree — the exact hidden variable this file exists to stop.
        """
        config = aa.load_config()
        with pytest.raises(aa.ConfigError) as caught:
            config.wire_extra("medium")
        assert "medium" in str(caught.value)
        assert sorted(config.thinking_modes) == ["off", "on"]

    def test_no_module_level_sampling_default_exists_in_the_source(self) -> None:
        """Structural, not behavioural: there is nothing to fall back TO.

        A behavioural test only proves the default is unused today. This one
        fails the moment someone reintroduces ``DEFAULT_TEMPERATURE = 0.3`` —
        which is exactly how the confound configurations.md records got in.
        """
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        banned = ("TEMPERATURE", "MAX_TOKENS", "THINKING")
        offenders = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and any(word in target.id for word in banned)
        ]
        assert offenders == [], f"sampling defaults must live in config, not code: {offenders}"

    def test_no_sampling_key_is_read_with_an_inline_default(self) -> None:
        """``raw.get("temperature", 0.3)`` is the same confound wearing a dict."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        keys = {"temperature", "thinking", "max_tokens"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "get":
                continue
            if len(node.args) != 2:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in keys:
                offenders.append(str(first.value))
        assert offenders == [], f"sampling keys must be required, not defaulted: {offenders}"


# ── criterion 1: one record per model call, reasoning tokens kept apart ───────


class TestPerCallRecords:
    """``ModelResponse`` carries no ``finish_reason`` (#37), so the record does.

    A truncated turn and a deliberate one arrive at the loop as the same object.
    t24 measured the shipped 2048 default truncating 6.0% of completions with
    zero degradations recorded; the parallel record is how a second mind's
    truncation stops being invisible.
    """

    def _seam(self, log: aa.CallLog, **kw: Any) -> aa.ArchSeam:
        config = aa.load_config()
        return aa.ArchSeam(
            dial=aa.Dial(
                role=aa.ROLE_WORKER, model="worker-x", base_url="http://x/v1", api_key="k"
            ),
            sampling=config.sampling_for(aa.ARM_WORKER_SOLO, aa.ROLE_WORKER),
            wire_extra=config.wire_extra("on"),
            role=aa.ROLE_WORKER,
            ctx=_ctx(arm=aa.ARM_WORKER_SOLO),
            log=log,
            sleep=lambda _seconds: None,
            **kw,
        )

    def test_one_call_lands_exactly_one_record(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload()
        seam([{"role": "user", "content": "hi"}])
        seam([{"role": "user", "content": "again"}])
        assert len(log.records) == 2
        assert [record.index for record in log.records] == [0, 1]

    def test_a_record_carries_finish_reason_role_model_and_arm(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload(finish_reason="tool_calls")
        seam([{"role": "user", "content": "hi"}])
        record = log.records[0]
        assert record.finish_reason == "tool_calls"
        assert record.role == aa.ROLE_WORKER
        assert record.model == "worker-x"
        assert record.arm == aa.ARM_WORKER_SOLO
        assert record.rung == "C1"
        assert record.route == aa.ROUTE_TEXT

    def test_reasoning_tokens_are_recorded_apart_from_content_tokens(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload(completion_tokens=500, reasoning_tokens=420)
        seam([{"role": "user", "content": "hi"}])
        record = log.records[0]
        assert record.completion_tokens == 500
        assert record.reasoning_tokens == 420
        assert record.content_tokens == 80
        assert record.token_detail == aa.TOKEN_DETAIL_USAGE_DETAILS

    def test_a_flat_usage_reasoning_field_is_also_read(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload(reasoning_tokens=99, flat=True)
        seam([{"role": "user", "content": "hi"}])
        assert log.records[0].reasoning_tokens == 99
        assert log.records[0].token_detail == aa.TOKEN_DETAIL_USAGE_FLAT

    def test_absent_reasoning_details_are_recorded_absent_never_zero(self) -> None:
        """``0`` would read as "this thinking model thought about nothing"."""
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload(reasoning_tokens=None)
        seam([{"role": "user", "content": "hi"}])
        record = log.records[0]
        assert record.reasoning_tokens is None
        assert record.content_tokens is None
        assert record.token_detail == aa.TOKEN_DETAIL_ABSENT
        # The honest fallback is a character count, and it is never called tokens.
        assert record.reasoning_chars == len("thought")
        assert record.content_chars == len("hello")

    def test_a_truncated_call_is_flagged_on_its_own_record(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload(finish_reason="length")
        seam([{"role": "user", "content": "hi"}])
        assert log.records[0].truncated is True
        assert log.records[0].finish_reason == ws.FINISH_TRUNCATED
        assert log.truncated == 1

    def test_a_transport_failure_still_lands_a_record_before_it_raises(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)

        def boom(body: dict[str, Any]) -> dict[str, Any]:
            raise urllib.error.URLError("nope")

        seam._transport = boom
        with pytest.raises(ws.WorkerTransportError):
            seam([{"role": "user", "content": "hi"}])
        assert len(log.records) == 1
        assert log.records[0].finish_reason == aa.FINISH_TRANSPORT_FAILURE
        assert log.records[0].retries == ws.MAX_TRANSPORT_RETRIES + 1

    def test_a_scripted_seam_records_the_same_shape(self) -> None:
        log = aa.CallLog()
        config = aa.load_config()
        seam = aa.ScriptedArchSeam(
            _flat_mind(),
            role=aa.ROLE_CORTEX,
            model="scripted:cortex",
            sampling=config.sampling_for(aa.ARM_EXISTING, aa.ROLE_CORTEX),
            ctx=_ctx(),
            log=log,
            finish_reasons=["length", "stop"],
        )
        seam([{"role": "user", "content": "hi"}])
        seam([{"role": "user", "content": "hi"}])
        assert [record.finish_reason for record in log.records] == ["length", "stop"]
        assert log.records[0].truncated is True
        assert log.records[0].live is False
        assert log.records[0].token_detail == aa.TOKEN_DETAIL_SCRIPTED

    def test_a_record_round_trips_through_json(self) -> None:
        log = aa.CallLog()
        seam = self._seam(log)
        seam._transport = lambda body: _payload()
        seam([{"role": "user", "content": "hi"}])
        restored = json.loads(json.dumps(log.records[0].to_dict()))
        assert restored["kind"] == aa.KIND_CALL
        for required in (
            "finish_reason",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "content_tokens",
            "role",
            "model",
            "arm",
        ):
            assert required in restored

    def test_running_an_arm_lands_a_record_for_every_turn(self) -> None:
        config = aa.load_config()
        log = aa.CallLog()
        problem = _tiny_problem()
        seams = aa.ScriptedSeams(_scripted_minds(), config=config, log=log)
        record = aa.run_attempt(
            arm=aa.ARMS[aa.ARM_EXISTING],
            rung="C1",
            problem=problem,
            seams=seams,
            config=config,
            senses_hash="h",
        )
        assert record.calls > 0
        assert len(log.records) == record.calls
        assert {r.arm for r in log.records} == {aa.ARM_EXISTING}
        assert {r.problem for r in log.records} == {problem.id}


# ── criterion 2: no verdict without both controls ────────────────────────────


def _cell(arm: str, rung: str = "C1", *, correct: int = 1, attempted: int = 1) -> dict[str, Any]:
    return {
        "kind": aa.KIND_CELL,
        "arm": arm,
        "rung": rung,
        "route": aa.ROUTE_TEXT,
        "attempted": attempted,
        "correct": correct,
        "truncated_calls": 0,
        "calls": 2,
        "senses_config_hash": "h",
    }


def _log_file(tmp_path: Path, cells: list[dict[str, Any]]) -> Path:
    path = tmp_path / "series.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for cell in cells:
            handle.write(json.dumps(cell) + "\n")
    return path


class TestAnalyseRefusesWithoutBothFlatArms:
    """A verdict that silently omits its control is this repo's most-recorded failure.

    Arm W (the worker alone) is not decoration: without it, a hybrid or manager
    win cannot be attributed to orchestration rather than to the worker simply
    being sufficient. So a rung that has data but is missing E or W produces no
    verdict at all — not a hedged one.
    """

    def _rows(self, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {row["rung"]: row for row in report["rungs"]}

    def test_a_rung_missing_the_existing_arm_is_refused(self, tmp_path: Path) -> None:
        path = _log_file(
            tmp_path,
            [
                _cell(aa.ARM_WORKER_SOLO, correct=1),
                _cell(aa.ARM_MANAGER, correct=1),
                _cell(aa.ARM_HYBRID, correct=0),
            ],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["verdict"] == aa.VERDICT_ABSENT
        assert row["state"] == aa.STATE_REFUSED
        assert aa.ARM_EXISTING in row["cells_absent"]
        assert aa.ARM_EXISTING in row["refusal"]

    def test_a_rung_missing_the_worker_solo_control_is_refused(self, tmp_path: Path) -> None:
        path = _log_file(
            tmp_path, [_cell(aa.ARM_EXISTING, correct=0), _cell(aa.ARM_MANAGER, correct=1)]
        )
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["verdict"] == aa.VERDICT_ABSENT
        assert row["state"] == aa.STATE_REFUSED
        assert aa.ARM_WORKER_SOLO in row["cells_absent"]

    def test_a_refused_rung_never_carries_a_separated_verdict(self, tmp_path: Path) -> None:
        """Even a landslide. The margin is not computed when a control is gone."""
        path = _log_file(
            tmp_path,
            [_cell(aa.ARM_EXISTING, correct=0), _cell(aa.ARM_MANAGER, correct=99, attempted=99)],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["verdict"] != aa.VERDICT_SEPARATED
        assert row.get("leader") is None
        assert report["verdict"] != aa.VERDICT_SEPARATED
        assert "C1" in report["rungs_refused"]

    def test_missing_cells_are_reported_absent_by_name(self, tmp_path: Path) -> None:
        path = _log_file(tmp_path, [_cell(aa.ARM_EXISTING), _cell(aa.ARM_WORKER_SOLO)])
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["cells"][aa.ARM_MANAGER]["verdict"] == aa.VERDICT_ABSENT
        assert row["cells"][aa.ARM_HYBRID]["verdict"] == aa.VERDICT_ABSENT
        assert sorted(row["cells_absent"]) == sorted([aa.ARM_MANAGER, aa.ARM_HYBRID])

    def test_an_unrun_rung_is_absent_but_not_refused(self, tmp_path: Path) -> None:
        """The stop rule leaves higher rungs unrun; that is not a missing control."""
        path = _log_file(tmp_path, [_cell(aa.ARM_EXISTING), _cell(aa.ARM_WORKER_SOLO)])
        report = aa.analyse(path, config=aa.load_config())
        rows = self._rows(report)
        unrun = [row for row in report["rungs"] if row["rung"] != "C1"]
        assert unrun, "the ladder has more than one rung"
        for row in unrun:
            assert row["state"] == aa.STATE_UNRUN
            assert row["verdict"] == aa.VERDICT_ABSENT
            assert row["rung"] not in report["rungs_refused"]
        assert rows["C1"]["state"] == aa.STATE_GRADED

    def test_both_flat_arms_present_permits_a_verdict(self, tmp_path: Path) -> None:
        path = _log_file(
            tmp_path,
            [
                _cell(aa.ARM_EXISTING, correct=0),
                _cell(aa.ARM_WORKER_SOLO, correct=0),
                _cell(aa.ARM_MANAGER, correct=1),
            ],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["state"] == aa.STATE_GRADED
        assert row["verdict"] == aa.VERDICT_SEPARATED
        assert row["leader"] == aa.ARM_MANAGER
        assert report["verdict"] == aa.VERDICT_SEPARATED
        assert report["separated_at"] == "C1"

    def test_a_tie_between_the_arms_is_inconclusive_not_a_win(self, tmp_path: Path) -> None:
        path = _log_file(
            tmp_path,
            [
                _cell(aa.ARM_EXISTING, correct=1),
                _cell(aa.ARM_WORKER_SOLO, correct=1),
                _cell(aa.ARM_MANAGER, correct=1),
            ],
        )
        report = aa.analyse(path, config=aa.load_config())
        assert self._rows(report)["C1"]["verdict"] == aa.VERDICT_INCONCLUSIVE
        assert report["verdict"] == aa.VERDICT_INCONCLUSIVE

    def test_an_empty_log_yields_absent_never_inconclusive(self, tmp_path: Path) -> None:
        path = _log_file(tmp_path, [])
        report = aa.analyse(path, config=aa.load_config())
        assert report["verdict"] == aa.VERDICT_ABSENT
        assert report["cells_total"] == 0

    def test_a_truncated_cell_is_excluded_as_an_instrument_event(self, tmp_path: Path) -> None:
        truncated = _cell(aa.ARM_MANAGER, correct=1)
        truncated["truncated_calls"] = 2
        path = _log_file(
            tmp_path,
            [_cell(aa.ARM_EXISTING, correct=0), _cell(aa.ARM_WORKER_SOLO, correct=0), truncated],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["cells"][aa.ARM_MANAGER]["excluded"] == aa.EXCLUSION_TRUNCATED
        assert aa.ARM_MANAGER not in row["graded_arms"]
        assert row["verdict"] == aa.VERDICT_INCONCLUSIVE

    def test_a_refused_flat_arm_outranks_every_other_exclusion(self, tmp_path: Path) -> None:
        """Truncation in the control is still a missing control."""
        truncated = _cell(aa.ARM_WORKER_SOLO, correct=0)
        truncated["truncated_calls"] = 1
        path = _log_file(tmp_path, [_cell(aa.ARM_EXISTING, correct=0), truncated])
        report = aa.analyse(path, config=aa.load_config())
        row = self._rows(report)["C1"]
        assert row["state"] == aa.STATE_REFUSED
        assert row["verdict"] == aa.VERDICT_ABSENT


# ── criterion 3: senses is byte-identical across arms ────────────────────────


class TestSensesIsIdenticalAcrossArms:
    """A senses difference would confound every arm, so it is hashed, not trusted.

    Senses is not dialled by the text-only challenge rungs — it is recorded, the
    way `league_h2h` records ``SENSES_MODEL`` for a seat with no senses lane. The
    hash starts biting on real traffic the moment the perception-routing rung
    dials it, and the guard has to exist before then, not after.
    """

    def test_the_committed_table_has_exactly_one_senses_hash(self) -> None:
        config = aa.load_config()
        hashes = config.senses_hashes()
        assert set(hashes) == set(aa.ARM_ORDER)
        assert len(set(hashes.values())) == 1
        assert aa.assert_senses_identical(config) == next(iter(hashes.values()))

    def test_a_drifted_senses_temperature_raises_naming_the_arms(self, tmp_path: Path) -> None:
        def drift(raw: dict[str, Any]) -> None:
            raw["sampling"]["H"]["senses"]["temperature"] = 0.9

        config = aa.load_config(_write_config(tmp_path, drift))
        with pytest.raises(aa.ConfigError) as caught:
            aa.assert_senses_identical(config)
        assert "H" in str(caught.value)
        assert "senses" in str(caught.value)

    def test_the_hash_covers_the_role_dial_not_only_the_sampling(self, tmp_path: Path) -> None:
        """A swapped senses MODEL with identical sampling must still change the hash."""
        before = aa.load_config().senses_hashes()[aa.ARM_EXISTING]

        def swap_model(raw: dict[str, Any]) -> None:
            raw["roles"]["senses"]["model"] = "some/other-senses-model"

        after = aa.load_config(_write_config(tmp_path, swap_model)).senses_hashes()[aa.ARM_EXISTING]
        assert before != after

    def test_every_call_record_carries_the_senses_hash(self) -> None:
        config = aa.load_config()
        log = aa.CallLog()
        seams = aa.ScriptedSeams(_scripted_minds(), config=config, log=log)
        senses_hash = aa.assert_senses_identical(config)
        aa.run_attempt(
            arm=aa.ARMS[aa.ARM_EXISTING],
            rung="C1",
            problem=_tiny_problem(),
            seams=seams,
            config=config,
            senses_hash=senses_hash,
        )
        assert log.records
        assert {record.senses_config_hash for record in log.records} == {senses_hash}

    def test_running_a_series_refuses_a_drifted_config_before_any_call(
        self, tmp_path: Path
    ) -> None:
        def drift(raw: dict[str, Any]) -> None:
            raw["sampling"]["M"]["senses"]["max_tokens"] = 512

        config = aa.load_config(_write_config(tmp_path, drift))
        log = aa.CallLog()
        seams = aa.ScriptedSeams(_scripted_minds(), config=config, log=log)
        with pytest.raises(aa.ConfigError):
            aa.run_series(
                config=config,
                seams=seams,
                log=log,
                ladder=(aa.Rung(id="X", problems=("easy",), why="fixture"),),
                registry=_tiny_registry(),
            )
        assert log.records == []


# ── the four arms themselves ─────────────────────────────────────────────────


class TestTheFourArms:
    def test_exactly_four_arms_exist_with_the_briefed_shapes(self) -> None:
        assert aa.ARM_ORDER == (
            aa.ARM_EXISTING,
            aa.ARM_WORKER_SOLO,
            aa.ARM_MANAGER,
            aa.ARM_HYBRID,
        )
        assert aa.ARMS[aa.ARM_EXISTING].shape == aa.SHAPE_FLAT
        assert aa.ARMS[aa.ARM_WORKER_SOLO].shape == aa.SHAPE_FLAT
        assert aa.ARMS[aa.ARM_MANAGER].shape == aa.SHAPE_ORCHESTRATED
        assert aa.ARMS[aa.ARM_HYBRID].shape == aa.SHAPE_ORCHESTRATED

    def test_both_flat_arms_are_named_as_the_controls(self) -> None:
        assert set(aa.FLAT_ARMS) == {aa.ARM_EXISTING, aa.ARM_WORKER_SOLO}
        assert all(aa.ARMS[arm].shape == aa.SHAPE_FLAT for arm in aa.FLAT_ARMS)

    def test_the_top_level_mind_differs_between_the_two_flat_arms(self) -> None:
        assert aa.ARMS[aa.ARM_EXISTING].top_level_role == aa.ROLE_CORTEX
        assert aa.ARMS[aa.ARM_WORKER_SOLO].top_level_role == aa.ROLE_WORKER

    def test_only_the_hybrid_orchestrator_keeps_ground_work_of_its_own(self) -> None:
        """M and H are otherwise identical; this flag is the whole difference."""
        assert aa.ARMS[aa.ARM_MANAGER].keeps_work is False
        assert aa.ARMS[aa.ARM_HYBRID].keeps_work is True
        assert aa.ARMS[aa.ARM_MANAGER].delegates is True
        assert aa.ARMS[aa.ARM_HYBRID].delegates is True

    def test_no_cortex_model_id_is_hard_coded_anywhere_in_the_harness(self) -> None:
        """The rig's cortex is mid-upgrade; a literal here would name a dead mind."""
        text = SOURCE.read_text(encoding="utf-8")
        assert "Qwen3.6-27B" not in text
        assert "sakamakismile" not in text
        assert aa.load_config().role(aa.ROLE_CORTEX).model is None


class TestArmsRunHermetically:
    """All four arms complete a rung end-to-end with scripted minds, no network."""

    def _run(self, arm_id: str, *, cortex=None, worker=None, problem=None):
        config = aa.load_config()
        log = aa.CallLog()
        seams = aa.ScriptedSeams(
            _scripted_minds(cortex=cortex, worker=worker), config=config, log=log
        )
        record = aa.run_attempt(
            arm=aa.ARMS[arm_id],
            rung="C1",
            problem=problem or _tiny_problem(),
            seams=seams,
            config=config,
            senses_hash="h",
        )
        return record, log

    def test_the_existing_arm_answers_alone(self) -> None:
        record, _log = self._run(aa.ARM_EXISTING)
        assert record.is_correct is True
        assert record.top_level_role == aa.ROLE_CORTEX
        assert record.delegations == 0
        assert record.routed_to == aa.ROLE_CORTEX

    def test_the_worker_solo_arm_answers_alone_as_the_top_level_mind(self) -> None:
        record, log = self._run(aa.ARM_WORKER_SOLO)
        assert record.is_correct is True
        assert record.top_level_role == aa.ROLE_WORKER
        assert record.delegations == 0
        assert {r.role for r in log.records} == {aa.ROLE_WORKER}

    def test_the_manager_arm_delegates_the_ground_work_and_finishes_itself(self) -> None:
        record, log = self._run(
            aa.ARM_MANAGER, cortex=_delegating_cortex(), worker=_reporting_worker()
        )
        assert record.delegations == 1
        assert record.routed_to == aa.ROLE_WORKER
        assert record.is_correct is True
        assert record.child_model_turns > 0
        assert {r.role for r in log.records} == {aa.ROLE_CORTEX, aa.ROLE_WORKER}

    def test_the_hybrid_arm_can_keep_the_work_itself(self) -> None:
        record, log = self._run(aa.ARM_HYBRID, cortex=_self_solving_cortex())
        assert record.delegations == 0
        assert record.routed_to == aa.ROLE_CORTEX
        assert record.is_correct is True
        assert {r.role for r in log.records} == {aa.ROLE_CORTEX}

    def test_the_hybrid_arm_can_route_the_work_out_with_a_stated_reason(self) -> None:
        record, _log = self._run(
            aa.ARM_HYBRID,
            cortex=_delegating_cortex(reason="exhaustive search, not my strength"),
            worker=_reporting_worker(),
        )
        assert record.delegations == 1
        assert record.routed_to == aa.ROLE_WORKER
        assert record.routing[0]["reason"] == "exhaustive search, not my strength"

    def test_the_manager_orchestrator_holds_no_ground_work_verb(self) -> None:
        """M's whole definition: the worker executes ALL ground work."""
        problem = _tiny_problem()
        schema = aa.orchestration_schema(problem, keeps_work=False)
        names = {entry["function"]["name"] for entry in schema}
        assert names == set(aa.ORCHESTRATION_TOOLS)
        assert "consider" not in names

    def test_the_hybrid_orchestrator_holds_the_bench_as_well(self) -> None:
        problem = _tiny_problem()
        names = {
            entry["function"]["name"] for entry in aa.orchestration_schema(problem, keeps_work=True)
        }
        assert "consider" in names
        assert set(aa.ORCHESTRATION_TOOLS) <= names

    def test_the_worker_surface_excludes_finish_in_every_orchestrated_arm(self) -> None:
        """No arm makes the worker a second final authority."""
        problem = _tiny_problem()
        names = {entry["function"]["name"] for entry in aa.worker_schema_for(problem)}
        assert "finish" not in names
        assert "report" in names
        assert "consider" in names
        assert not (set(names) & set(aa.FINAL_AUTHORITY_TOOLS))

    def test_a_worker_that_reaches_for_finish_is_refused_not_answered(self) -> None:
        from embodiment import UnknownToolError

        executor = aa.BenchWorkerExecutor(problem=_tiny_problem(), subtask="do it")
        with pytest.raises(UnknownToolError):
            executor.execute("finish", {"answer": "42"})
        assert executor.refused == ["finish"]

    def test_the_manager_refuses_ground_work_even_if_its_surface_drifts(self) -> None:
        """The guard behind the manager's empty bench, exercised deliberately.

        Today the surface check catches this first, so the branch is unreachable
        by construction — which is exactly why it is worth pinning. A future
        edit that widens the manager's surface must fail loudly rather than
        dereference a bench that arm never had.
        """
        from embodiment import UnknownToolError

        executor = aa.ArchOrchestrator(
            problem=_tiny_problem(),
            arm=aa.ARMS[aa.ARM_MANAGER],
            task_id="drifted",
            worker_max_steps=2,
        )
        assert executor.bench is None
        executor.surface = executor.surface + ("consider",)
        with pytest.raises(UnknownToolError):
            executor.execute("consider", {})
        assert executor.refused == ["consider"]

    def test_a_worker_that_never_reports_lands_a_named_degradation(self) -> None:
        def silent(messages: list[dict[str, Any]]) -> ModelResponse:
            return ModelResponse(content="I have nothing.", tool_calls=[])

        record, _log = self._run(aa.ARM_MANAGER, cortex=_delegating_cortex(), worker=silent)
        from examples import orchestrator_tools as ot

        assert ot.DEGRADED_WORKER_NO_REPORT in record.degradation_codes

    def test_absent_identity_leaves_the_top_level_prompt_byte_identical(self) -> None:
        """colleague#352's acceptance criterion, inherited unchanged."""
        assert aa.top_level_prompt(aa.FLAT_SYSTEM, identity=None) == aa.FLAT_SYSTEM
        assert aa.top_level_prompt(aa.ORCHESTRATOR_SYSTEM, identity=None) == aa.ORCHESTRATOR_SYSTEM


# ── the ladder, and the rule that excludes a degenerate hybrid cell ──────────


class TestLadderAndDegenerateCells:
    def test_every_rung_names_committed_problems(self) -> None:
        for rung in aa.LADDER:
            assert rung.problems
            for problem_id in rung.problems:
                assert problem_id in aa.PROBLEMS

    def test_every_problem_cites_where_its_statement_and_answer_live(self) -> None:
        for problem in aa.PROBLEMS.values():
            assert problem.source
            assert problem.statement.strip()
            assert problem.difficulty in aa.DIFFICULTIES

    def test_heterogeneity_is_computed_from_the_rung_not_declared(self) -> None:
        """A flag a human sets is a flag a human sets wrongly."""
        homogeneous = aa.Rung(id="X", problems=("easy",), why="one difficulty")
        mixed = aa.Rung(id="Y", problems=("easy", "hard"), why="two difficulties")
        assert homogeneous.heterogeneous(_tiny_registry()) is False
        assert mixed.heterogeneous(_tiny_registry()) is True

    def test_the_ladder_ends_on_a_heterogeneous_rung(self) -> None:
        """Somewhere on the ladder the hybrid's routing has to be able to vary."""
        assert aa.LADDER[-1].heterogeneous(aa.PROBLEMS) is True

    def test_a_degenerate_hybrid_cell_is_excluded_by_rule(self, tmp_path: Path) -> None:
        homogeneous = next(rung for rung in aa.LADDER if not rung.heterogeneous(aa.PROBLEMS))
        path = _log_file(
            tmp_path,
            [
                _cell(aa.ARM_EXISTING, homogeneous.id, correct=0),
                _cell(aa.ARM_WORKER_SOLO, homogeneous.id, correct=0),
                _cell(aa.ARM_HYBRID, homogeneous.id, correct=1),
            ],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = next(r for r in report["rungs"] if r["rung"] == homogeneous.id)
        cell = row["cells"][aa.ARM_HYBRID]
        assert cell["excluded"] == aa.EXCLUSION_DEGENERATE
        assert aa.ARM_HYBRID not in row["graded_arms"]
        # ...and the exclusion cannot hand it the win.
        assert row["verdict"] == aa.VERDICT_INCONCLUSIVE

    def test_the_hybrid_cell_is_graded_on_a_heterogeneous_rung(self, tmp_path: Path) -> None:
        mixed = next(rung for rung in aa.LADDER if rung.heterogeneous(aa.PROBLEMS))
        path = _log_file(
            tmp_path,
            [
                _cell(aa.ARM_EXISTING, mixed.id, correct=0),
                _cell(aa.ARM_WORKER_SOLO, mixed.id, correct=0),
                _cell(aa.ARM_HYBRID, mixed.id, correct=1),
            ],
        )
        report = aa.analyse(path, config=aa.load_config())
        row = next(r for r in report["rungs"] if r["rung"] == mixed.id)
        assert row["cells"][aa.ARM_HYBRID].get("excluded") is None
        assert aa.ARM_HYBRID in row["graded_arms"]

    def test_a_whole_rung_runs_hermetically_across_all_four_arms(self, tmp_path: Path) -> None:
        config = aa.load_config()
        log = aa.CallLog()
        seams = aa.ScriptedSeams(
            _scripted_minds(cortex=_delegating_cortex(), worker=_reporting_worker()),
            config=config,
            log=log,
        )
        out = tmp_path / "series.jsonl"
        report = aa.run_series(
            config=config,
            seams=seams,
            log=log,
            ladder=(aa.Rung(id="X", problems=("easy", "hard"), why="fixture"),),
            registry=_tiny_registry(),
            out=out,
        )
        assert sorted(cell["arm"] for cell in report["cells"]) == sorted(aa.ARM_ORDER)
        assert all(cell["attempted"] == 2 for cell in report["cells"])
        written = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        kinds = {record["kind"] for record in written}
        assert kinds == {aa.KIND_PREAMBLE, aa.KIND_CALL, aa.KIND_ATTEMPT, aa.KIND_CELL}
        # And the artifact analyses without a second process.
        analysed = aa.analyse(out, config=config)
        assert analysed["cells_total"] == len(aa.ARM_ORDER)


# ── perception routing: not built, not precluded ─────────────────────────────


class TestPerceptionRoutingIsNotPrecluded:
    """A later task adds an image-routing dimension. This one must not block it.

    Every cell is keyed by ``(rung, arm, route)`` with exactly one route today.
    A two-part key would force that task to rewrite this one's analyse verb.
    """

    def test_exactly_one_route_ships_today_and_it_is_named(self) -> None:
        assert aa.ROUTES == (aa.ROUTE_TEXT,)
        assert aa.ROUTE_TEXT in aa.ROUTE_WHY

    def test_cells_are_keyed_by_route_as_well_as_rung_and_arm(self, tmp_path: Path) -> None:
        other = _cell(aa.ARM_EXISTING)
        other["route"] = "native-image"
        path = _log_file(tmp_path, [_cell(aa.ARM_EXISTING), _cell(aa.ARM_WORKER_SOLO), other])
        report = aa.analyse(path, config=aa.load_config())
        routes = {(row["rung"], row["route"]) for row in report["rungs"]}
        assert ("C1", aa.ROUTE_TEXT) in routes
        assert ("C1", "native-image") in routes

    def test_a_second_route_is_graded_on_its_own_controls(self, tmp_path: Path) -> None:
        """An unfamiliar route inherits the refusal rule rather than bypassing it."""
        other = _cell(aa.ARM_MANAGER)
        other["route"] = "native-image"
        path = _log_file(tmp_path, [_cell(aa.ARM_EXISTING), _cell(aa.ARM_WORKER_SOLO), other])
        report = aa.analyse(path, config=aa.load_config())
        row = next(r for r in report["rungs"] if r["route"] == "native-image")
        assert row["state"] == aa.STATE_REFUSED


# ── the live lane stays shut ─────────────────────────────────────────────────


class TestNoLiveDial:
    """Live runs are frozen. The gate is the harness's, not the test's."""

    def test_live_requires_the_rig_gate(self, monkeypatch: Any) -> None:
        monkeypatch.delenv(aa.LIVE_GATE_ENV, raising=False)
        with pytest.raises(aa.LiveRigClosed):
            aa.require_live_rig(env={})

    def test_the_gate_env_var_is_the_repo_wide_one(self) -> None:
        assert aa.LIVE_GATE_ENV == "EMBODIMENT_LIVE_RIG"

    def test_the_cli_refuses_live_without_the_gate(self, capsys: Any) -> None:
        code = aa.main(["run", "--rung", aa.LADDER[0].id, "--live"])
        captured = capsys.readouterr()
        assert code == 2
        assert "error:" in captured.err
        assert aa.LIVE_GATE_ENV in captured.err
        assert "hint:" in captured.err

    def test_an_unconfigured_role_degrades_to_absent_never_a_substitute(self) -> None:
        """The cortex model is null in the committed table; absence is recorded."""
        resolution = aa.resolve_dial(aa.load_config(), aa.ROLE_CORTEX, env={})
        assert resolution.dial is None
        codes = [degradation.code for degradation in resolution.degradations]
        assert aa.DEGRADED_MODEL_ABSENT in codes
        assert ws.SPARK_GATEWAY_URL not in json.dumps(resolution.to_dict())

    def test_the_worker_dial_goes_through_the_t2_resolver(self, monkeypatch: Any) -> None:
        """t2 owns the no-silent-fallback guarantee; t5 uses it rather than a copy."""
        seen: dict[str, Any] = {}
        real = ws.resolve_worker_config

        def spy(**kwargs: Any) -> Any:
            seen.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(aa.ws, "resolve_worker_config", spy)
        aa.resolve_dial(aa.load_config(), aa.ROLE_WORKER, env={"COLLEAGUE_API_KEY": "k"})
        assert seen["cli_model"] == "unsloth/Qwen3.6-35B-A3B-NVFP4"
        assert seen["cli_url"].startswith("http://thor")


# ── the CLI ──────────────────────────────────────────────────────────────────


class TestCli:
    def test_plan_names_the_arms_the_ladder_and_the_controls(self, capsys: Any) -> None:
        assert aa.main(["plan"]) == 0
        out = capsys.readouterr().out
        for arm in aa.ARM_ORDER:
            assert arm in out
        assert "control" in out.lower()
        assert aa.LADDER[0].id in out

    def test_plan_json_is_machine_readable(self, capsys: Any) -> None:
        assert aa.main(["plan", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["arms"]) == set(aa.ARM_ORDER)
        assert payload["flat_arms"] == list(aa.FLAT_ARMS)

    def test_config_prints_the_sampling_table_it_actually_read(self, capsys: Any) -> None:
        assert aa.main(["config", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["path"] == str(aa.DEFAULT_CONFIG_PATH)
        assert payload["sampling"]["E"]["cortex"]["max_tokens"] == 16000
        assert payload["senses_config_hash"]

    def test_config_never_prints_an_api_key(self, capsys: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("COLLEAGUE_API_KEY", "super-secret-value")
        assert aa.main(["config", "--json"]) == 0
        assert "super-secret-value" not in capsys.readouterr().out

    def test_run_is_scripted_by_default_and_writes_an_artifact(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        out = tmp_path / "series.jsonl"
        code = aa.main(["run", "--rung", aa.LADDER[0].id, "--out", str(out)])
        capsys.readouterr()
        assert code == 0
        assert out.exists()
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert any(record["kind"] == aa.KIND_CALL for record in records)
        assert all(record.get("live", False) is False for record in records if "live" in record)

    def test_analyse_reads_an_artifact_back_and_reapplies_the_rule(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        out = tmp_path / "series.jsonl"
        aa.main(["run", "--rung", aa.LADDER[0].id, "--out", str(out)])
        capsys.readouterr()
        assert aa.main(["analyse", "--log", str(out)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["kind"] == aa.KIND_ANALYSIS
        assert payload["verdict"] in aa.VERDICTS

    def test_an_unreadable_config_is_a_clean_error_not_a_traceback(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        code = aa.main(["config", "--config", str(broken)])
        captured = capsys.readouterr()
        assert code == 2
        assert "error:" in captured.err
        assert "hint:" in captured.err
        assert "Traceback" not in captured.err


# ── the harness's own preamble ───────────────────────────────────────────────


class TestPreamble:
    def test_the_preamble_records_the_config_that_produced_the_run(self, tmp_path: Path) -> None:
        config = aa.load_config()
        log = aa.CallLog()
        seams = aa.ScriptedSeams(_scripted_minds(), config=config, log=log)
        out = tmp_path / "series.jsonl"
        aa.run_series(
            config=config,
            seams=seams,
            log=log,
            ladder=(aa.Rung(id="X", problems=("easy",), why="fixture"),),
            registry=_tiny_registry(),
            out=out,
        )
        first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert first["kind"] == aa.KIND_PREAMBLE
        assert first["sampling"]["E"]["cortex"]["temperature"] == 0.3
        assert first["senses_config_hash"]
        assert first["config_path"] == str(config.path)
        assert first["live"] is False

    def test_the_preamble_carries_no_secret(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("COLLEAGUE_API_KEY", "top-secret")
        config = aa.load_config()
        log = aa.CallLog()
        seams = aa.ScriptedSeams(_scripted_minds(), config=config, log=log)
        out = tmp_path / "series.jsonl"
        aa.run_series(
            config=config,
            seams=seams,
            log=log,
            ladder=(aa.Rung(id="X", problems=("easy",), why="fixture"),),
            registry=_tiny_registry(),
            out=out,
        )
        assert "top-secret" not in out.read_text(encoding="utf-8")


# ── the live rig, opt-in and never dialled here ──────────────────────────────


@pytest.mark.skipif(
    os.environ.get("EMBODIMENT_LIVE_RIG") != "1",
    reason="live runs are frozen; set EMBODIMENT_LIVE_RIG=1 to dial the real rig",
)
class TestLiveRig:
    """Opt-in only, and not dialled by this task. Kept so the gate is exercised."""

    def test_the_gate_opens_when_the_env_var_is_set(self) -> None:
        aa.require_live_rig(env={aa.LIVE_GATE_ENV: "1"})
