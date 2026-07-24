"""Tests for :mod:`embodiment.contract` — the carved data contract (task t1).

Three things are pinned here:

* **Purity** — the module imports stdlib only and no ``embodiment`` module
  imports ``colleague`` (constraint C1; task t2 adds the runtime guard).
* **Field parity** — every carved shape keeps colleague's field names and
  order, declared explicitly below so parity is checkable without importing
  colleague.
* **Round-trip** — every dataclass survives ``to_dict`` → JSON → ``from_dict``,
  including the verbatim invariant on ``ContextPacket.original`` and the
  best-effort (never-raises) coercion of malformed payloads.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from embodiment import contract

PACKAGE_ROOT = Path(contract.__file__).resolve().parent

# --- purity ---------------------------------------------------------------


def _module_level_imports(path: Path) -> list[str]:
    """Top-level module names imported at module scope in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])
    return names


def _all_imports(path: Path) -> list[str]:
    """Every module name imported anywhere in ``path`` (module scope or inside a def)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return names


def test_contract_imports_stdlib_only() -> None:
    for name in _module_level_imports(Path(contract.__file__)):
        assert name in sys.stdlib_module_names or name == "__future__", name


def test_no_embodiment_module_imports_colleague() -> None:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        assert "colleague" not in _all_imports(path), path


def test_contract_module_has_no_lazy_third_party_import() -> None:
    for name in _all_imports(Path(contract.__file__)):
        assert name in sys.stdlib_module_names or name == "__future__", name


# --- constants ------------------------------------------------------------


def test_status_constants() -> None:
    assert contract.OK == "ok"
    assert contract.ERROR == "error"
    assert contract.INCOMPLETE == "incomplete"


def test_no_result_produced_is_a_machine_sentinel() -> None:
    # Frozen wire value: an artifact written by the first consumer and one
    # written by embodiment must compare equal on the empty-result case.
    assert contract.NO_RESULT_PRODUCED == "__COLLEAGUE_NO_RESULT_PRODUCED__"
    assert contract.NO_RESULT_PRODUCED.startswith("__")


def test_senses_chat_kinds_is_the_closed_vocabulary() -> None:
    assert contract.SENSES_CHAT_KINDS == ("talk", "ack", "update", "clarify")


# --- carve boundary -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "HookFiring",
        "CapacityDecision",
        "DECISION_ALLOW",
        "DECISION_DENY",
        "DECISION_REWRITE",
        "DECISION_OBSERVE",
        "LintReport",
        "CoherenceReport",
        "DeepthinkCall",
        "ChainView",
        "SensesDirectRecord",
    ],
)
def test_colleague_policy_shapes_stay_behind(name: str) -> None:
    # These become injection points in later tasks, not contract members.
    assert not hasattr(contract, name), name


@pytest.mark.parametrize(
    "field_name",
    [
        "hook_firings",
        "capacity_decision",
        "capacity_warning",
        "gates_deferred",
        "lint_report",
        "coherence_report",
        "test_integrity_report",
        "affected_tests_report",
        "deepthink",
        "chain",
    ],
)
def test_task_result_drops_policy_fields(field_name: str) -> None:
    assert field_name not in {f.name for f in dataclasses.fields(contract.TaskResult)}


# --- field parity ---------------------------------------------------------

EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "ToolCall": ("id", "name", "arguments"),
    "ModelResponse": (
        "content",
        "tool_calls",
        "prompt_tokens",
        "completion_tokens",
        "reasoning",
    ),
    "Usage": ("prompt_tokens", "completion_tokens", "total_tokens"),
    "WorkStats": (
        "request",
        "engine",
        "model",
        "started_at",
        "duration_seconds",
        "model_turns",
        "step_count",
        "tool_counts",
        "files_changed",
        "bytes_written",
        "reasoning_chars",
        "reasoning_bytes",
        "answer_chars",
        "answer_bytes",
    ),
    "SubResult": (
        "task_id",
        "engine",
        "model",
        "status",
        "summary",
        "changed_files",
        "usage",
        "role",
        "parent",
    ),
    "ContextPacket": (
        "original",
        "interpretation",
        "confidence",
        "task_type",
        "omissions",
        "ack",
    ),
    "SensesRecord": ("point", "latency", "tokens", "degraded"),
    "SensesBlock": ("mode", "packet", "records", "injections", "chat"),
    "IncompletionRecord": ("reason", "evidence", "recommendation"),
    "Step": ("index", "tool", "arguments", "result", "ok"),
    "Task": (
        "id",
        "repo_path",
        "instruction",
        "context",
        "constraints",
        "engine",
        "watch",
        "goal",
        "acceptance",
        "attachments",
        "context_packet",
        "flight_repo_path",
    ),
    "TaskResult": (
        "task_id",
        "status",
        "summary",
        "changed_files",
        "steps",
        "usage",
        "stats",
        "artifacts_path",
        "error",
        "branch",
        "pr_url",
        "sub_results",
        "command",
        "destination",
        "announcement",
        "not_finished",
        "stopped_without_finish",
        "role",
        "mode",
        "acceptance_outcomes",
        "finish_recovered",
        "memory",
        "media",
        "senses",
        "incompletion",
        "continued_from",
    ),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_FIELDS))
def test_field_parity(name: str) -> None:
    cls = getattr(contract, name)
    assert dataclasses.is_dataclass(cls)
    actual = tuple(f.name for f in dataclasses.fields(cls))
    assert actual == EXPECTED_FIELDS[name]


def test_public_api_is_exported() -> None:
    required = {
        "Task",
        "TaskResult",
        "Step",
        "ToolCall",
        "ModelResponse",
        "WorkAborted",
        "ContextPacket",
        "SensesRecord",
        "OK",
        "ERROR",
        "INCOMPLETE",
        "NO_RESULT_PRODUCED",
    }
    assert required <= set(contract.__all__)
    for name in contract.__all__:
        assert hasattr(contract, name), name


@pytest.mark.parametrize("name", sorted(EXPECTED_FIELDS))
def test_every_dataclass_serializes(name: str) -> None:
    cls = getattr(contract, name)
    assert callable(cls.to_dict)
    assert callable(cls.from_dict)


# --- round-trips ----------------------------------------------------------


def _roundtrip(obj: Any) -> Any:
    """to_dict → JSON text → from_dict, so a round-trip proves JSON-safety too."""
    return type(obj).from_dict(json.loads(json.dumps(obj.to_dict())))


def test_tool_call_roundtrip() -> None:
    call = contract.ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"})
    assert _roundtrip(call) == call


def test_model_response_roundtrip() -> None:
    response = contract.ModelResponse(
        content="done",
        tool_calls=[contract.ToolCall(id="c1", name="finish", arguments={"summary": "ok"})],
        prompt_tokens=11,
        completion_tokens=3,
        reasoning="thinking about it",
    )
    assert _roundtrip(response) == response


def test_usage_roundtrip_and_add() -> None:
    usage = contract.Usage()
    usage.add(10, 4)
    usage.add(1, 1)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 5, 16)
    assert _roundtrip(usage) == usage


def test_work_stats_roundtrip_and_add_generated() -> None:
    stats = contract.WorkStats(request="fix it", engine="mock", model="m", started_at="now")
    stats.tool_counts["read_file"] = 2
    stats.add_generated(reasoning="ab", answer="çd")
    assert stats.reasoning_chars == 2
    assert stats.answer_chars == 2
    assert stats.answer_bytes == 3  # ç is two UTF-8 bytes
    assert _roundtrip(stats) == stats


def test_sub_result_roundtrip() -> None:
    sub = contract.SubResult(
        task_id="child",
        engine="mock",
        model="m",
        status=contract.OK,
        summary="s",
        changed_files=["a.py"],
        usage=contract.Usage(1, 2, 3),
        role="reader",
        parent="parent-id",
    )
    assert _roundtrip(sub) == sub


def test_sub_result_omits_role_and_parent_when_none() -> None:
    sub = contract.SubResult(task_id="c", engine="mock", model="m", status=contract.OK)
    assert "role" not in sub.to_dict()
    assert "parent" not in sub.to_dict()


def test_context_packet_roundtrip() -> None:
    packet = contract.ContextPacket(
        original="fix the thing",
        interpretation="repair the thing",
        confidence=0.75,
        task_type="bugfix",
        omissions=["which file"],
        ack="On it.",
    )
    assert _roundtrip(packet) == packet


def test_senses_record_roundtrip() -> None:
    record = contract.SensesRecord(point="interpret", latency=0.25, tokens=42, degraded=False)
    assert _roundtrip(record) == record


def test_senses_block_roundtrip() -> None:
    block = contract.SensesBlock(
        mode="split",
        packet=contract.ContextPacket(original="hello"),
        records=[contract.SensesRecord(point="interpret")],
        injections=[{"text": "try the other file", "at": 1.0, "source": "operator"}],
        chat=[{"message": "hi", "answer": "hello", "kind": "talk"}],
    )
    assert _roundtrip(block) == block


def test_incompletion_record_roundtrip() -> None:
    record = contract.IncompletionRecord(
        reason="budget exhausted", evidence="step 12", recommendation="continue"
    )
    assert _roundtrip(record) == record


def test_step_roundtrip() -> None:
    step = contract.Step(index=1, tool="read_file", arguments={"path": "x"}, result="ok", ok=True)
    assert _roundtrip(step) == step


def test_task_roundtrip() -> None:
    task = contract.Task(
        id="abc123",
        repo_path="/repo",
        instruction="do the thing",
        context="ctx",
        constraints=["no network"],
        engine="mock",
        watch=True,
        goal="the thing is done",
        acceptance=["tests pass"],
        attachments=[{"path": "a.png", "media_type": "image/png"}],
        context_packet=contract.ContextPacket(original="do the thing"),
        flight_repo_path="/operator-repo",
    )
    assert _roundtrip(task) == task


def test_task_result_roundtrip_full() -> None:
    result = contract.TaskResult(
        task_id="t1",
        status=contract.OK,
        summary="done",
        changed_files=["a.py"],
        steps=[contract.Step(index=0, tool="read_file")],
        usage=contract.Usage(3, 4, 7),
        stats=contract.WorkStats(request="do it", engine="mock", model="m"),
        artifacts_path="/repo/.embodiment",
        error=None,
        branch="feature",
        pr_url="https://example.invalid/pr/1",
        sub_results=[contract.SubResult(task_id="c", engine="mock", model="m", status=contract.OK)],
        command="work",
        destination="slug",
        announcement="arrived",
        not_finished=False,
        stopped_without_finish=False,
        role="reader",
        mode="work",
        acceptance_outcomes=[{"criterion": "tests pass", "met": True, "evidence": "pytest"}],
        finish_recovered="literal-markup",
        memory={"query": "q", "recalled": 1, "injected_chars": 12, "lesson_recorded": True},
        media={"attachments": [{"path": "a.png", "status": "delivered"}]},
        senses=contract.SensesBlock(
            mode="split",
            packet=contract.ContextPacket(original="do it"),
            records=[contract.SensesRecord(point="interpret", degraded=True)],
        ),
        incompletion=contract.IncompletionRecord("r", "e", "rec"),
        continued_from="t0",
    )
    assert _roundtrip(result) == result


def test_task_result_roundtrip_minimal() -> None:
    result = contract.TaskResult(task_id="t1", status=contract.ERROR, error="boom")
    assert _roundtrip(result) == result


# --- omit-when-None / omit-when-empty -------------------------------------


def test_task_omits_optional_keys() -> None:
    data = contract.Task.new("/repo", "do it").to_dict()
    assert set(data) == {"id", "repo_path", "instruction", "context", "constraints", "engine"}


def test_task_result_omits_optional_keys() -> None:
    data = contract.TaskResult(task_id="t1", status=contract.OK).to_dict()
    for key in (
        "destination",
        "announcement",
        "role",
        "mode",
        "acceptance_outcomes",
        "finish_recovered",
        "memory",
        "media",
        "senses",
        "incompletion",
        "continued_from",
        "sub_results",
    ):
        assert key not in data, key
    # …while the always-present keys stay present even when null.
    for key in ("artifacts_path", "error", "branch", "pr_url", "command"):
        assert key in data, key


def test_context_packet_omits_ack_when_none() -> None:
    assert "ack" not in contract.ContextPacket(original="x").to_dict()


def test_senses_block_omits_empty_live_lane_keys() -> None:
    data = contract.SensesBlock(mode="cortex-only").to_dict()
    assert set(data) == {"mode", "packet", "records"}


# --- the verbatim invariant -----------------------------------------------


VERBATIM_SAMPLES = [
    "  leading and trailing whitespace  ",
    "line one\nline two\n",
    "unicode: naïve café — ünïcödé 🌍",
    "\ttabbed\r\n",
    '{"looks": "like json"}',
    "",
]


@pytest.mark.parametrize("text", VERBATIM_SAMPLES)
def test_context_packet_original_is_verbatim(text: str) -> None:
    packet = contract.ContextPacket(original=text)
    assert _roundtrip(packet).original == text


@pytest.mark.parametrize("text", VERBATIM_SAMPLES)
def test_task_context_packet_original_is_verbatim(text: str) -> None:
    task = contract.Task.new("/repo", "instruction", context_packet=contract.ContextPacket(text))
    assert _roundtrip(task).context_packet is not None
    assert _roundtrip(task).context_packet.original == text


# --- best-effort coercion: malformed payloads never raise -----------------


def test_context_packet_from_dict_tolerates_malformed_confidence() -> None:
    assert (
        contract.ContextPacket.from_dict({"original": "x", "confidence": "n/a"}).confidence == 0.0
    )
    assert contract.ContextPacket.from_dict({"original": "x", "confidence": None}).confidence == 0.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["a", "b"], ["a", "b"]),
        ("bare string", ["bare string"]),
        (None, []),
        (7, []),
        ({"a": 1}, []),
    ],
)
def test_context_packet_from_dict_coerces_omissions(raw: Any, expected: list[str]) -> None:
    assert (
        contract.ContextPacket.from_dict({"original": "x", "omissions": raw}).omissions == expected
    )


@pytest.mark.parametrize("raw", [None, 7, {"a": 1}, "", "   "])
def test_context_packet_from_dict_degrades_bad_ack_to_none(raw: Any) -> None:
    assert contract.ContextPacket.from_dict({"original": "x", "ack": raw}).ack is None


def test_context_packet_ack_is_stripped_and_capped() -> None:
    packet = contract.ContextPacket.from_dict({"original": "x", "ack": "  hi  "})
    assert packet.ack == "hi"
    long = contract.ContextPacket.from_dict({"original": "x", "ack": "a" * 900})
    assert long.ack is not None
    assert len(long.ack) == 500


def test_senses_record_from_dict_tolerates_malformed_numbers() -> None:
    record = contract.SensesRecord.from_dict({"point": "p", "latency": "slow", "tokens": "many"})
    assert record.point == "p"
    assert record.latency is None
    assert record.tokens is None


def test_senses_block_from_dict_drops_malformed_entries() -> None:
    block = contract.SensesBlock.from_dict(
        {
            "mode": "split",
            "packet": "not-a-dict",
            "records": [{"point": "p"}, "junk", 3],
            "injections": [{"text": "t"}, None],
            "chat": [{"message": "m"}, []],
        }
    )
    assert block.packet is None
    assert [r.point for r in block.records] == ["p"]
    assert block.injections == [{"text": "t"}]
    assert block.chat == [{"message": "m"}]


def test_incompletion_record_from_dict_tolerates_junk() -> None:
    assert contract.IncompletionRecord.from_dict("junk") == contract.IncompletionRecord("", "", "")
    assert contract.IncompletionRecord.from_dict({"reason": None}).reason == ""


def test_task_from_dict_degrades_malformed_optional_payloads() -> None:
    task = contract.Task.from_dict(
        {
            "id": "i",
            "repo_path": "/r",
            "instruction": "do it",
            "acceptance": "bare string",
            "attachments": "bare string",
            "context_packet": "bare string",
        }
    )
    assert task.acceptance is None
    assert task.attachments is None
    assert task.context_packet is None


def test_task_result_from_dict_degrades_malformed_optional_payloads() -> None:
    result = contract.TaskResult.from_dict(
        {
            "task_id": "t",
            "status": contract.OK,
            "acceptance_outcomes": 7,
            "memory": "bare string",
            "media": "bare string",
            "senses": "bare string",
            "incompletion": "bare string",
        }
    )
    assert result.acceptance_outcomes is None
    assert result.memory is None
    assert result.media is None
    assert result.senses is None
    assert result.incompletion is None


def test_task_result_from_dict_drops_malformed_acceptance_entries() -> None:
    result = contract.TaskResult.from_dict(
        {
            "task_id": "t",
            "status": contract.OK,
            "acceptance_outcomes": [{"criterion": "c", "met": True}, "junk"],
        }
    )
    assert result.acceptance_outcomes == [{"criterion": "c", "met": True, "evidence": ""}]


def test_model_response_from_dict_tolerates_malformed_payload() -> None:
    response = contract.ModelResponse.from_dict(
        {"content": "x", "tool_calls": [{"id": "a", "name": "n"}, "junk"], "prompt_tokens": "n/a"}
    )
    assert response.prompt_tokens == 0
    assert [c.id for c in response.tool_calls] == ["a"]


def test_tool_call_from_dict_tolerates_malformed_arguments() -> None:
    call = contract.ToolCall.from_dict({"id": "a", "name": "n", "arguments": "not-a-dict"})
    assert call.arguments == {}


# --- Task.new -------------------------------------------------------------


def test_task_new_mints_a_short_unique_id() -> None:
    first = contract.Task.new("/repo", "do it")
    second = contract.Task.new("/repo", "do it")
    assert len(first.id) == 12
    assert int(first.id, 16) >= 0  # hex
    assert first.id != second.id
    assert first.engine == "mock"


def test_task_new_copies_mutable_inputs() -> None:
    constraints = ["one"]
    attachments = [{"path": "a.png", "media_type": "image/png"}]
    acceptance = ["tests pass"]
    task = contract.Task.new(
        "/repo",
        "do it",
        constraints=constraints,
        acceptance=acceptance,
        attachments=attachments,
    )
    constraints.append("two")
    acceptance.append("more")
    attachments[0]["path"] = "mutated.png"
    assert task.constraints == ["one"]
    assert task.acceptance == ["tests pass"]
    assert task.attachments == [{"path": "a.png", "media_type": "image/png"}]


# --- WorkAborted ----------------------------------------------------------


def test_work_aborted_carries_the_partial_result() -> None:
    partial = contract.TaskResult(task_id="t", status=contract.ERROR, error="engine exploded")
    exc = contract.WorkAborted(partial)
    assert isinstance(exc, Exception)
    assert exc.result is partial
    assert str(exc) == "engine exploded"


def test_work_aborted_falls_back_to_a_default_message() -> None:
    partial = contract.TaskResult(task_id="t", status=contract.ERROR)
    assert str(contract.WorkAborted(partial)) == "drive aborted"


def test_work_aborted_is_raisable_and_catchable() -> None:
    partial = contract.TaskResult(task_id="t", status=contract.ERROR, error="boom")
    with pytest.raises(contract.WorkAborted) as caught:
        raise contract.WorkAborted(partial)
    assert caught.value.result.task_id == "t"
