"""Tests for :mod:`embodiment.lifecycle` — the continuity checkpoints (task t14).

Organised around the acceptance criteria:

1. **Posture + the structural half of the separation criterion** — stdlib and
   ``embodiment`` imports only, no ``colleague``, no store/scoring/embedding
   helper of its own, and no reference anywhere in the module to the loop's
   tool-approval vocabulary.
2. **The three named checkpoints** — ``before-action`` / ``before-completion``
   / ``before-memory``, each covered by a test showing coherence consulted and
   (at ``before-memory`` only) the memory write gated there.
3. **Provenance** — ``added_by`` / ``links`` / ``supersedes`` populated on a
   durable write, threaded from perception (the verbatim request), through the
   earlier boundary's recall, into the record eidetic actually stores.
4. **The behavioural half of the separation criterion** — driven through the
   REAL :func:`embodiment.loop.run`: a coherence verdict cannot alter a
   tool-approval decision, and a tool-approval decision cannot alter a
   coherence verdict.
5. **Degradation is host-visible (C3)** — either subsystem absent, a raising
   sink, and an internal fault all leave a recorded transition without ever
   raising into the host.

Everything here is hermetic, following the shape ``tests/test_continuity.py``
established after deviation **d2**: there are no fake CLI executables on
``PATH`` any more because there is no ``PATH`` lookup to fake. Instead

* every eidetic call is anchored at a throwaway ``tmp_path`` store;
* every coherence call injects an ``embed_fn``, so no embedding endpoint is
  ever dialled, and recall runs in an offline lexical mode;
* nothing is written outside ``tmp_path``.

Verdict-specific gating tests monkeypatch ``continuity.assess`` rather than
trying to steer the real engine onto a chosen number: the policy under test is
"what does this module DO with a verdict", not "what verdict does coherence
produce" (that is coherence's own suite). The real engine is exercised
separately, so the field names this module reads are pinned against the real
report shape rather than against a fixture that could drift.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import continuity
from embodiment.contract import (
    ERROR,
    INCOMPLETE,
    NO_RESULT_PRODUCED,
    OK,
    ContextPacket,
    IncompletionRecord,
    ModelResponse,
    Task,
    TaskResult,
    ToolCall,
)
from embodiment.lifecycle import (
    CHECKPOINT_ASSESS_SKIPPED,
    CHECKPOINT_ASSESSED,
    CHECKPOINT_DEGRADED,
    CHECKPOINT_KINDS,
    CHECKPOINT_MODE,
    CHECKPOINT_RECALLED,
    CHECKPOINT_REMEMBER_SKIPPED,
    CHECKPOINT_REMEMBERED,
    DEFAULT_CONSEQUENCE_THRESHOLD,
    DEFAULT_MAX_LINKS,
    RECORD_ID_PREFIX,
    ContinuityLifecycle,
    LifecycleConfig,
    LifecycleEvent,
    build_continuity_fn,
    record_id_for,
    request_text,
    select_for_memory,
)
from embodiment.loop import (
    BOUNDARY_ACTION,
    BOUNDARY_COMPLETION,
    BOUNDARY_MEMORY,
    DECISION_ALLOW,
    DECISION_DENY,
    EVENT_PRE_TOOL,
    LOOP_BOUNDARIES,
    Boundary,
    HookDecision,
    ToolOutcome,
    UnknownToolError,
    run,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "embodiment" / "lifecycle.py"


# ---------------------------------------------------------------------------
# helpers / doubles
# ---------------------------------------------------------------------------


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """A deterministic, offline stand-in for coherence's embedding endpoint."""
    out: list[list[float]] = []
    for text in texts:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        out.append([byte / 255.0 for byte in digest[:16]])
    return out


def _task(**kw: Any) -> Task:
    fields: dict[str, Any] = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    fields.update(kw)
    return Task(**fields)


def _result(**kw: Any) -> TaskResult:
    fields: dict[str, Any] = {"task_id": "t1", "status": OK, "summary": "did the thing"}
    fields.update(kw)
    return TaskResult(**fields)


def _boundary(
    name: str,
    *,
    task: Optional[Task] = None,
    result: Optional[TaskResult] = None,
    **kw: Any,
) -> Boundary:
    return Boundary(
        name=name,
        task=task if task is not None else _task(),
        result=result if result is not None else _result(),
        **kw,
    )


def _config(tmp_path: Path, **kw: Any) -> LifecycleConfig:
    """A fully hermetic config: throwaway store, offline recall, offline embedder."""
    fields: dict[str, Any] = {
        "data_dir": tmp_path / "store",
        "workdir": tmp_path,
        "embed_fn": _fake_embed,
        "recall_mode": "keyword",
        "reference_date": date(2026, 7, 25),
    }
    fields.update(kw)
    return LifecycleConfig(**fields)


def _assess_outcome(
    *,
    consequence: Optional[float] = None,
    future_constraint: Optional[float] = None,
    ok: bool = True,
    unavailable: Optional[dict[str, Any]] = None,
    degradation: Optional[continuity.Degradation] = None,
) -> continuity.AssessOutcome:
    """Build the shape ``coherence.assess`` really returns, with chosen numbers."""
    domains: dict[str, Any] = {"quality": {"scores": {}}}
    if consequence is not None or future_constraint is not None:
        subdimensions: dict[str, Any] = {}
        if consequence is not None:
            subdimensions["consequence"] = consequence
        if future_constraint is not None:
            subdimensions["future_constraint"] = future_constraint
        domains["meaning"] = {"meaning_score": 0.5, "subdimensions": subdimensions}
    return continuity.AssessOutcome(
        ok=ok,
        domains=domains,
        unavailable=dict(unavailable or {}),
        degradation=degradation,
    )


class _AssessRecorder:
    """Stands in for ``continuity.assess``; records the artifact text it saw."""

    def __init__(self, outcome: Optional[continuity.AssessOutcome] = None) -> None:
        self.outcome = outcome if outcome is not None else _assess_outcome(consequence=0.9)
        self.texts: list[str] = []
        self.paths: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, path: Any, **kwargs: Any) -> continuity.AssessOutcome:
        self.paths.append(str(path))
        self.texts.append(Path(path).read_text(encoding="utf-8"))
        self.kwargs.append(dict(kwargs))
        return self.outcome


class _RememberRecorder:
    """Stands in for ``continuity.remember``; records the record and kwargs."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.records: list[dict[str, Any]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, record: Any, **kwargs: Any) -> continuity.RememberOutcome:
        self.records.append(dict(record))
        self.kwargs.append(dict(kwargs))
        if not self.ok:
            return continuity.RememberOutcome(
                ok=False,
                record_id=None,
                degradation=continuity.Degradation(
                    subsystem="eidetic",
                    stage="remember",
                    code=continuity.CODE_SUBSYSTEM_ERROR,
                    reason="store on fire",
                    exception="RuntimeError",
                ),
            )
        return continuity.RememberOutcome(
            ok=True, record_id=str(record.get("id")), degradation=None, raw=dict(record)
        )


@pytest.fixture()
def clean_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no ambient store pin leaks into (or out of) a test."""
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.delenv("DR_DATA_DIR", raising=False)


def _kinds(lifecycle: ContinuityLifecycle) -> list[str]:
    return [event.kind for event in lifecycle.events]


def _of_kind(lifecycle: ContinuityLifecycle, kind: str) -> list[LifecycleEvent]:
    return [event for event in lifecycle.events if event.kind == kind]


# ---------------------------------------------------------------------------
# 1. posture + the structural half of the separation criterion
# ---------------------------------------------------------------------------


def _imports_of(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


def _all_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        names.extend(_imports_of(node))
    return names


def _identifiers(path: Path) -> set[str]:
    """Every name/attribute/import identifier used as *code* (never prose)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(alias.name for alias in node.names)
            found.update(alias.asname for alias in node.names if alias.asname)
    return found


def _non_docstring_str_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class TestPosture:
    def test_imports_are_stdlib_or_embodiment_only(self) -> None:
        """This module is policy, not plumbing: it reaches eidetic/coherence only
        through :mod:`embodiment.continuity`, never directly."""
        for name in _all_imports(MODULE_PATH):
            assert name in sys.stdlib_module_names or name in {
                "__future__",
                "embodiment",
            }, f"lifecycle.py imports {name}"

    def test_neither_subsystem_is_imported_directly(self) -> None:
        imported = _all_imports(MODULE_PATH)
        assert "eidetic" not in imported
        assert "coherence" not in imported

    def test_no_colleague_import_anywhere(self) -> None:
        assert "colleague" not in _all_imports(MODULE_PATH)

    def test_no_subprocess_family_import(self) -> None:
        imported = _all_imports(MODULE_PATH)
        for banned in ("subprocess", "shlex"):
            assert banned not in imported

    def test_defines_no_similarity_or_embedding_helpers(self) -> None:
        """The 'no store, no scoring, no embedding logic' constraint, by AST.

        Mirrors ``tests/test_continuity.py``'s identical guard on its sibling:
        checks code identifiers, not docstring prose (which legitimately names
        these words while explaining that coherence, not this module, owns
        them). ``embed_fn`` is threaded through untouched and is not a scorer.
        """
        lowered = {name.lower() for name in _identifiers(MODULE_PATH) if name}
        for token in ("cosine", "embed_texts", "bm25"):
            assert not any(token in name for name in lowered), token

    def test_module_defines_no_store_path_layout(self) -> None:
        """Store layout belongs to eidetic; this module must not encode it."""
        for value in _non_docstring_str_constants(MODULE_PATH):
            assert ".eidetic" not in value, f"store layout hardcoded: {value!r}"

    def test_never_references_the_tool_approval_vocabulary(self) -> None:
        """The structural half of "a coherence verdict cannot alter a
        tool-approval decision".

        Permission is the hook seam's exclusive authority. This module never
        imports, constructs, returns or even NAMES any part of that vocabulary
        in code, so there is no path by which a coherence verdict could reach
        it — the loop's control-bearing return channel is simply not a name
        this source mentions.
        """
        banned = {
            "HookDecision",
            "HookEvent",
            "HookFiring",
            "HookFn",
            "DECISION_ALLOW",
            "DECISION_DENY",
            "DECISION_REWRITE",
            "DECISION_OBSERVE",
            "CONTROL_BEARING_EVENTS",
            "EVENT_PRE_TOOL",
            "pre_tool",
            "hooks",
        }
        leaked = banned & _identifiers(MODULE_PATH)
        assert not leaked, f"lifecycle.py names the approval vocabulary: {sorted(leaked)}"

    def test_the_boundary_the_loop_hands_over_carries_no_approval_state(self) -> None:
        """The other side of the same wall, read off the loop's own dataclass.

        Even a *malicious* checkpoint policy could not read a tool-approval
        decision: :class:`~embodiment.loop.Boundary` carries no hook field at
        all. If a future change adds one, this fails and the separation is
        re-litigated deliberately rather than eroded quietly.
        """
        assert set(Boundary.__dataclass_fields__) == {
            "name",
            "task",
            "result",
            "step_index",
            "model_turns",
            "tool",
            "arguments",
        }


class TestConfigDefaults:
    """eidetic's ``docs/contract.md`` §4 tells every consumer to pin the
    defaults it mirrors with its own drift test rather than trust they agree.
    This config mirrors four of ``continuity``'s, so it pins all four."""

    def test_the_store_conventions_are_mirrored_not_re_chosen(self) -> None:
        config = LifecycleConfig()
        assert config.scope == continuity.DEFAULT_SCOPE
        assert config.visibility == continuity.DEFAULT_VISIBILITY
        assert config.recall_top_k == continuity.DEFAULT_TOP_K

    def test_the_recall_mode_is_eidetics_own_default(self) -> None:
        """Picking a different one here would be embodiment making a ranking
        decision it does not own."""
        assert LifecycleConfig().recall_mode == continuity.DEFAULT_MODE

    def test_no_store_anchor_is_assumed(self) -> None:
        """A default ``data_dir`` would be a guess about where a host's
        memories belong — ``continuity``'s trap #1, one layer up."""
        assert LifecycleConfig().data_dir is None

    def test_every_checkpoint_is_armed_by_default(self) -> None:
        config = LifecycleConfig()
        assert (config.assess_action, config.assess_completion, config.assess_memory) == (
            True,
            True,
            True,
        )
        assert config.consequential is None
        assert config.consider_every_action is False


class TestCallableContract:
    def test_call_always_returns_none(self, clean_store_env: None, tmp_path: Path) -> None:
        """Nothing this callable returns could be mistaken for a control
        decision — on any boundary, including an unrecognised future one."""
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        for name in (*LOOP_BOUNDARIES, "some-future-boundary"):
            assert lifecycle(_boundary(name)) is None

    def test_an_unknown_boundary_is_observed_not_dispatched(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        lifecycle(_boundary("some-future-boundary"))
        assert CHECKPOINT_ASSESSED not in _kinds(lifecycle)
        assert CHECKPOINT_REMEMBERED not in _kinds(lifecycle)

    def test_build_continuity_fn_returns_a_lifecycle(self) -> None:
        assert isinstance(build_continuity_fn(), ContinuityLifecycle)

    def test_build_continuity_fn_threads_config_and_sink(self, tmp_path: Path) -> None:
        seen: list[LifecycleEvent] = []
        config = _config(tmp_path)
        fn = build_continuity_fn(config, on_event=seen.append)
        assert fn.config is config
        fn(_boundary(BOUNDARY_COMPLETION))
        assert seen

    def test_record_id_uses_the_documented_prefix(self) -> None:
        assert record_id_for("abc") == f"{RECORD_ID_PREFIX}abc" == "embodiment-task-abc"

    def test_event_kinds_are_a_closed_documented_vocabulary(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task()
        lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="write_file", arguments={}))
        lifecycle(_boundary(BOUNDARY_COMPLETION, task=task))
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task))
        assert lifecycle.events
        for event in lifecycle.events:
            assert event.kind in CHECKPOINT_KINDS
            assert event.boundary in (*LOOP_BOUNDARIES, "")
            json.dumps(event.to_dict())


# ---------------------------------------------------------------------------
# 2a. before-action — the "considered" beat
# ---------------------------------------------------------------------------


class TestBeforeAction:
    def test_consults_memory_and_coherence(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={"path": "a.py"}))

        assert CHECKPOINT_RECALLED in _kinds(lifecycle)
        assert CHECKPOINT_ASSESSED in _kinds(lifecycle)
        # the assessed snapshot names the PENDING action, not merely the task
        assert len(assess.texts) == 1
        assert "write_file" in assess.texts[0]
        assert "do the thing" in assess.texts[0]

    def test_never_writes_memory(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """before-memory is the ONLY checkpoint that may write."""
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        assert remember.records == []

    def test_only_consults_once_per_work_item_by_default(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="read_file", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        assert len(assess.texts) == 1
        skipped = _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)
        assert [e.detail for e in skipped] == ["already-considered"]

    def test_every_action_can_be_consulted_when_configured(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path, consider_every_action=True))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="read_file", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        assert len(assess.texts) == 2

    def test_a_second_work_item_gets_its_own_consideration(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_ACTION, task=_task(id="t1"), tool="read_file", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, task=_task(id="t2"), tool="read_file", arguments={}))

        assert len(assess.texts) == 2

    def test_the_host_decides_which_actions_are_consequential(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embodiment owns *when in the sequence*; which TOOLS matter is the
        host's knowledge — a kiosk's ``send_message`` is consequential, its
        ``get_weather`` is not, and this module cannot know that."""
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        config = _config(tmp_path, consequential=lambda b: b.tool == "send_message")
        lifecycle = ContinuityLifecycle(config)

        lifecycle(_boundary(BOUNDARY_ACTION, tool="get_weather", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, tool="send_message", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, tool="get_weather", arguments={}))

        assert len(assess.texts) == 1
        assert "send_message" in assess.texts[0]
        assert [e.detail for e in _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)] == [
            "not-consequential",
            "not-consequential",
        ]

    def test_the_host_predicate_may_fire_more_than_once_per_work_item(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        config = _config(tmp_path, consequential=lambda b: True)
        lifecycle = ContinuityLifecycle(config)

        lifecycle(_boundary(BOUNDARY_ACTION, tool="a", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, tool="b", arguments={}))

        assert len(assess.texts) == 2

    def test_a_raising_host_predicate_degrades_and_falls_back(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(boundary: Boundary) -> bool:
            raise RuntimeError("host predicate exploded")

        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path, consequential=boom))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert any(e.detail == "consequential-fn-failed" for e in degraded)
        # ...and the checkpoint still ran on the built-in cadence
        assert len(assess.texts) == 1

    def test_assessment_can_be_disabled_without_disabling_recall(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_action=False))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        assert assess.texts == []
        assert CHECKPOINT_RECALLED in _kinds(lifecycle)
        assert [e.detail for e in _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)] == ["disabled"]

    def test_recall_reads_the_anchored_store_and_reports_what_it_found(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        store = tmp_path / "store"
        continuity.remember(
            {"id": "mem-1", "text": "the thing was migrated last week", "type": "lesson"},
            data_dir=store,
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_action=False))

        lifecycle(
            _boundary(
                BOUNDARY_ACTION,
                task=_task(instruction="migrated"),
                tool="write_file",
                arguments={},
            )
        )

        recalled = _of_kind(lifecycle, CHECKPOINT_RECALLED)
        assert recalled and recalled[0].data["ids"] == ["mem-1"]
        assert recalled[0].data["count"] == 1


# ---------------------------------------------------------------------------
# 2b. before-completion — the "reviewed" beat
# ---------------------------------------------------------------------------


class TestBeforeCompletion:
    def test_consults_coherence_on_the_work_as_it_stands(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(
            _boundary(
                BOUNDARY_COMPLETION,
                result=_result(summary="the finished work", changed_files=["a.py"]),
            )
        )

        assert CHECKPOINT_ASSESSED in _kinds(lifecycle)
        assert "the finished work" in assess.texts[0]
        assert "a.py" in assess.texts[0]

    def test_never_touches_memory(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        recalls: list[Any] = []
        monkeypatch.setattr(continuity, "remember", remember)
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: recalls.append((a, k))
            or continuity.RecallOutcome(ok=True, records=[], degradation=None),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_COMPLETION))

        assert remember.records == []
        assert recalls == []

    def test_assessment_can_be_disabled(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_completion=False))

        lifecycle(_boundary(BOUNDARY_COMPLETION))

        assert assess.texts == []
        assert [e.detail for e in _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)] == ["disabled"]

    def test_an_unchanged_snapshot_is_never_assessed_twice_in_one_sequence(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Check coherence at meaningful boundaries, not continuously."

        before-completion and before-memory see the same work item moments
        apart. When the snapshot text is byte-identical the verdict is reused
        rather than re-derived — one embedding round-trip, not two — and the
        reuse is recorded rather than silent.
        """
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        monkeypatch.setattr(continuity, "remember", _RememberRecorder())
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task()
        result = _result(summary="a stable result")

        lifecycle(_boundary(BOUNDARY_COMPLETION, task=task, result=result))
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task, result=result))

        assert len(assess.texts) == 1
        assert "unchanged" in [e.detail for e in _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)]

    def test_a_changed_snapshot_is_reassessed(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        monkeypatch.setattr(continuity, "remember", _RememberRecorder())
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task()

        lifecycle(_boundary(BOUNDARY_COMPLETION, task=task, result=_result(summary="partial")))
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task, result=_result(summary="the real answer")))

        assert len(assess.texts) == 2


# ---------------------------------------------------------------------------
# 2c. before-memory — the "remembered" beat, and the gate
# ---------------------------------------------------------------------------


class TestBeforeMemory:
    def test_a_high_consequence_verdict_opens_the_gate(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a consequential change")))

        assert len(remember.records) == 1
        remembered = _of_kind(lifecycle, CHECKPOINT_REMEMBERED)
        assert remembered and remembered[0].detail == "high-consequence"

    def test_a_low_verdict_on_both_subdimensions_closes_it(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(
            continuity,
            "assess",
            _AssessRecorder(_assess_outcome(consequence=0.1, future_constraint=0.2)),
        )
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a trivial tweak")))

        assert remember.records == []
        skipped = _of_kind(lifecycle, CHECKPOINT_REMEMBER_SKIPPED)
        assert skipped and skipped[0].detail == "low-consequence"

    def test_coherence_is_consulted_before_the_write(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordering IS the checkpoint: the verdict must exist before the
        record does, or the gate is decorative."""
        order: list[str] = []

        def assess(path: Any, **kwargs: Any) -> continuity.AssessOutcome:
            order.append("assess")
            return _assess_outcome(consequence=0.9)

        def remember(record: Any, **kwargs: Any) -> continuity.RememberOutcome:
            order.append("remember")
            return continuity.RememberOutcome(ok=True, record_id="x", degradation=None)

        monkeypatch.setattr(continuity, "assess", assess)
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY))

        assert order == ["assess", "remember"]

    def test_a_structurally_vetoed_result_costs_no_assessment(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run with no deliverable is not memory-worthy whatever coherence
        thinks, so the cheap veto runs first and the embedding call is never
        made."""
        assess = _AssessRecorder()
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(status=ERROR, summary="boom")))

        assert assess.texts == []
        assert remember.records == []
        assert [e.detail for e in _of_kind(lifecycle, CHECKPOINT_ASSESS_SKIPPED)] == [
            "not-memory-worthy"
        ]
        assert [e.detail for e in _of_kind(lifecycle, CHECKPOINT_REMEMBER_SKIPPED)] == [
            "error-status"
        ]

    def test_an_absent_meaning_domain_falls_back_to_the_structural_signal(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcome = _assess_outcome(
            unavailable={"meaning": {"code": "embed_endpoint_unreachable", "reason": "down"}},
            degradation=continuity.Degradation(
                subsystem="coherence",
                stage="assess",
                code=continuity.CODE_DOMAIN_UNAVAILABLE,
                reason="domain(s) unavailable: meaning",
            ),
        )
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(outcome))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a real deliverable")))

        assert len(remember.records) == 1
        assert _of_kind(lifecycle, CHECKPOINT_REMEMBERED)[0].detail == "no-coherence-signal"
        # trap #2 one layer up: the partial report is still a recorded degradation
        assert any(
            e.data.get("code") == continuity.CODE_DOMAIN_UNAVAILABLE
            for e in _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        )

    def test_disabling_the_memory_assessment_leaves_the_structural_signal(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_memory=False))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a real deliverable")))

        assert assess.texts == []
        assert len(remember.records) == 1
        assert _of_kind(lifecycle, CHECKPOINT_REMEMBERED)[0].detail == "no-coherence-signal"

    def test_a_failed_write_is_recorded_not_raised(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", _RememberRecorder(ok=False))
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        assert lifecycle(_boundary(BOUNDARY_MEMORY)) is None

        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert any(e.data.get("code") == continuity.CODE_SUBSYSTEM_ERROR for e in degraded)
        assert CHECKPOINT_REMEMBERED not in _kinds(lifecycle)

    def test_the_write_is_anchored_at_the_configured_store(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        config = _config(tmp_path, scope="gwen", visibility="private")
        lifecycle = ContinuityLifecycle(config)

        lifecycle(_boundary(BOUNDARY_MEMORY))

        kwargs = remember.kwargs[0]
        assert kwargs["data_dir"] == config.data_dir
        assert kwargs["scope"] == "gwen"
        assert kwargs["visibility"] == "private"


# ---------------------------------------------------------------------------
# 3. the selective-remembering policy — one pure function
# ---------------------------------------------------------------------------


class TestSelectForMemory:
    def test_is_pure_and_needs_no_subsystem(self) -> None:
        should, reason = select_for_memory(_result(summary="a real result"), None)
        assert (should, reason) == (True, "no-coherence-signal")

    def test_an_error_status_is_never_remembered(self) -> None:
        assert select_for_memory(_result(status=ERROR, summary="x"), None) == (
            False,
            "error-status",
        )

    def test_an_empty_summary_is_never_remembered(self) -> None:
        assert select_for_memory(_result(summary=""), None) == (False, "no-deliverable")

    def test_the_no_result_sentinel_is_never_remembered(self) -> None:
        assert select_for_memory(_result(summary=NO_RESULT_PRODUCED), None) == (
            False,
            "no-deliverable",
        )

    def test_the_loops_own_incompletion_verdict_is_honoured(self) -> None:
        """Compose, don't re-derive: the loop already classified this run as
        having produced no deliverable, and that verdict is authoritative."""
        result = _result(
            status=INCOMPLETE,
            summary="I looked at some files",
            incompletion=IncompletionRecord(
                reason="write-no-changes", evidence="0 changed files", recommendation="retry"
            ),
        )
        assert select_for_memory(result, None) == (False, "incomplete")

    def test_a_high_consequence_alone_is_sufficient(self) -> None:
        outcome = _assess_outcome(consequence=0.9, future_constraint=0.0)
        assert select_for_memory(_result(summary="x"), outcome) == (True, "high-consequence")

    def test_a_high_future_constraint_alone_is_sufficient(self) -> None:
        outcome = _assess_outcome(consequence=0.0, future_constraint=0.9)
        assert select_for_memory(_result(summary="x"), outcome) == (True, "high-consequence")

    def test_low_on_both_is_an_explicit_drop(self) -> None:
        outcome = _assess_outcome(consequence=0.1, future_constraint=0.2)
        assert select_for_memory(_result(summary="x"), outcome) == (False, "low-consequence")

    def test_the_threshold_is_configurable(self) -> None:
        outcome = _assess_outcome(consequence=0.6)
        assert select_for_memory(_result(summary="x"), outcome, threshold=0.5)[0] is True
        assert select_for_memory(_result(summary="x"), outcome, threshold=0.9) == (
            False,
            "low-consequence",
        )

    def test_the_default_threshold_is_the_midpoint_of_coherences_range(self) -> None:
        assert DEFAULT_CONSEQUENCE_THRESHOLD == 0.5

    def test_a_failed_assessment_falls_back(self) -> None:
        outcome = continuity.AssessOutcome(ok=False, domains={}, unavailable={}, degradation=None)
        assert select_for_memory(_result(summary="x"), outcome) == (True, "no-coherence-signal")

    def test_a_missing_meaning_domain_falls_back(self) -> None:
        assert select_for_memory(_result(summary="x"), _assess_outcome()) == (
            True,
            "no-coherence-signal",
        )

    def test_non_numeric_subdimensions_fall_back(self) -> None:
        outcome = continuity.AssessOutcome(
            ok=True,
            domains={"meaning": {"subdimensions": {"consequence": "high"}}},
            unavailable={},
            degradation=None,
        )
        assert select_for_memory(_result(summary="x"), outcome) == (True, "no-coherence-signal")

    def test_a_malformed_meaning_domain_falls_back(self) -> None:
        outcome = continuity.AssessOutcome(
            ok=True, domains={"meaning": ["not", "a", "mapping"]}, unavailable={}, degradation=None
        )
        assert select_for_memory(_result(summary="x"), outcome) == (True, "no-coherence-signal")

    def test_malformed_subdimensions_fall_back(self) -> None:
        outcome = continuity.AssessOutcome(
            ok=True,
            domains={"meaning": {"subdimensions": ["not", "a", "mapping"]}},
            unavailable={},
            degradation=None,
        )
        assert select_for_memory(_result(summary="x"), outcome) == (True, "no-coherence-signal")

    def test_a_boolean_is_not_a_reading(self) -> None:
        """``True`` is an ``int`` in Python. Reading it as ``1.0`` would turn a
        malformed report into a confident "store me"."""
        outcome = continuity.AssessOutcome(
            ok=True,
            domains={"meaning": {"subdimensions": {"consequence": True}}},
            unavailable={},
            degradation=None,
        )
        assert select_for_memory(_result(summary="x"), outcome) == (True, "no-coherence-signal")

    def test_the_structural_veto_outranks_a_high_verdict(self) -> None:
        """Coherence can only ever narrow what is stored, never widen it past
        "there was nothing to store"."""
        outcome = _assess_outcome(consequence=1.0, future_constraint=1.0)
        assert select_for_memory(_result(status=ERROR, summary="boom"), outcome) == (
            False,
            "error-status",
        )

    def test_the_field_names_match_the_real_coherence_report(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """Pin the two field names this policy reads against the REAL engine.

        The gating tests above use a crafted outcome; this one proves the
        crafted shape is not a fiction that could drift away from what
        coherence actually emits.
        """
        artifact = tmp_path / "artifact.md"
        artifact.write_text(
            "The migration removed the legacy store. Owner: @ori. Next: verify backups.\n",
            encoding="utf-8",
        )
        outcome = continuity.assess(artifact, embed_fn=_fake_embed, reference_date=date(2026, 1, 1))

        subdimensions = outcome.domains["meaning"]["subdimensions"]
        assert isinstance(subdimensions["consequence"], float)
        assert isinstance(subdimensions["future_constraint"], float)
        should, reason = select_for_memory(_result(summary="x"), outcome)
        assert reason in {"high-consequence", "low-consequence"}
        assert isinstance(should, bool)


# ---------------------------------------------------------------------------
# 4. provenance: perception -> action -> durable record
# ---------------------------------------------------------------------------


class TestRequestText:
    def test_falls_back_to_the_instruction_with_no_perception(self) -> None:
        assert request_text(_task(instruction="do the thing")) == "do the thing"

    def test_prefers_the_operators_verbatim_words(self) -> None:
        packet = ContextPacket(
            original="  fix the  THING\n", interpretation="fix the thing", task_type="bugfix"
        )
        assert request_text(_task(context_packet=packet)) == "  fix the  THING\n"

    def test_never_reads_the_model_derived_interpretation(self) -> None:
        """The verbatim invariant: the provenance root is what was ASKED, and
        it is never taken from model output."""
        packet = ContextPacket(original="", interpretation="a model's rewording")
        assert request_text(_task(context_packet=packet)) == "do the thing"


class TestProvenance:
    def _run_sequence(
        self,
        lifecycle: ContinuityLifecycle,
        *,
        task: Task,
        result: TaskResult,
    ) -> None:
        lifecycle(_boundary(BOUNDARY_ACTION, task=task, result=result, tool="w", arguments={}))
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task, result=result))

    def test_the_full_chain_round_trips_through_the_real_store(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE provenance acceptance criterion, end to end against real eidetic.

        A prior record is recalled at before-action (perception), the work item
        acts, and the durable record written at before-memory carries
        ``added_by`` (who), ``links`` (what informed it) and ``supersedes``
        (what it replaces) — read back out of the store, not merely off the
        call this module made.
        """
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        store = tmp_path / "store"
        repo = tmp_path / "rig"
        repo.mkdir()
        (repo / "culture.yaml").write_text("nick: gwen\n", encoding="utf-8")
        continuity.remember(
            {"id": "mem-1", "text": "iceland migration notes", "type": "lesson"}, data_dir=store
        )

        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task(id="t9", repo_path=str(repo), instruction="iceland")
        result = _result(task_id="t9", summary="migrated iceland", continued_from="t8")
        self._run_sequence(lifecycle, task=task, result=result)

        back = continuity.recall("migrated iceland", data_dir=store, mode="exact", reinforce=False)
        assert [r["id"] for r in back.records] == [record_id_for("t9")]
        stored = back.records[0]
        assert stored["added_by"] == "gwen"
        assert stored["links"] == ["mem-1"]
        assert stored["supersedes"] == record_id_for("t8")
        assert stored["text"] == "migrated iceland"
        assert stored["type"] == "task"

    def test_links_come_from_the_recall_at_before_action(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: continuity.RecallOutcome(
                ok=True, records=[{"id": "mem-1"}, {"id": "mem-2"}], degradation=None
            ),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task(id="t9")

        self._run_sequence(lifecycle, task=task, result=_result(task_id="t9"))

        assert remember.records[0]["links"] == ["mem-1", "mem-2"]

    def test_links_are_bounded(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: continuity.RecallOutcome(
                ok=True, records=[{"id": f"mem-{n}"} for n in range(50)], degradation=None
            ),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path, consider_every_action=True))
        task = _task(id="t9")

        self._run_sequence(lifecycle, task=task, result=_result(task_id="t9"))

        assert len(remember.records[0]["links"]) == DEFAULT_MAX_LINKS

    def test_no_links_key_when_nothing_was_recalled(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY))

        assert "links" not in remember.records[0]

    def test_no_supersedes_key_on_a_fresh_work_item(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a fresh task")))

        assert "supersedes" not in remember.records[0]

    def test_added_by_can_be_configured_explicitly(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path, added_by="gwen-override"))

        lifecycle(_boundary(BOUNDARY_MEMORY))

        assert remember.kwargs[0]["added_by"] == "gwen-override"

    def test_added_by_is_resolved_identity_never_an_inference(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reuses ``embodiment.identity``'s seam — no parallel persona source,
        and never derived from a model name."""
        repo = tmp_path / "rig"
        repo.mkdir()
        (repo / "culture.yaml").write_text(
            "agents:\n- suffix: gwen\n  model: some-model-name\n", encoding="utf-8"
        )
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, task=_task(repo_path=str(repo))))

        assert remember.kwargs[0]["added_by"] == "gwen"

    def test_added_by_stays_none_when_nothing_resolves(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, task=_task(repo_path=str(tmp_path / "nowhere"))))

        assert remember.kwargs[0]["added_by"] is None

    def test_a_blank_repo_path_never_resolves_against_the_ambient_directory(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same cwd-dependence trap ``continuity`` refuses for the store:
        an empty ``repo_path`` would make the resolver read whatever directory
        the host process happens to be in."""
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, task=_task(repo_path="")))

        assert remember.kwargs[0]["added_by"] is None

    def test_the_record_carries_the_verbatim_request_not_the_interpretation(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        packet = ContextPacket(
            original="make the WIDGET stop crashing",
            interpretation="fix the widget crash",
            task_type="bugfix",
            confidence=0.8,
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, task=_task(context_packet=packet)))

        metadata = remember.records[0]["metadata"]
        assert metadata["request"] == "make the WIDGET stop crashing"
        assert metadata["perception"] == {"task_type": "bugfix", "confidence": 0.8}
        assert "fix the widget crash" not in json.dumps(remember.records[0])

    def test_the_recall_query_is_the_verbatim_request_too(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queries: list[str] = []

        def recall(query: str, **kwargs: Any) -> continuity.RecallOutcome:
            queries.append(query)
            return continuity.RecallOutcome(ok=True, records=[], degradation=None)

        monkeypatch.setattr(continuity, "recall", recall)
        packet = ContextPacket(original="the WIDGET keeps crashing", interpretation="rewritten")
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_action=False))

        lifecycle(
            _boundary(BOUNDARY_ACTION, task=_task(context_packet=packet), tool="w", arguments={})
        )

        assert queries == ["the WIDGET keeps crashing"]

    def test_created_is_when_the_work_began_not_when_memory_was_written(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        result = _result()
        result.stats.started_at = "2026-07-25T00:00:00+00:00"
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=result))

        assert remember.records[0]["created"] == "2026-07-25T00:00:00+00:00"

    def test_created_is_left_to_eidetic_when_the_loop_never_stamped_one(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY))

        assert "created" not in remember.records[0]

    def test_the_record_id_is_derived_from_the_task_id(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, task=_task(id="abc123")))

        assert remember.records[0]["id"] == record_id_for("abc123")

    def test_metadata_carries_the_lived_sequence_facts(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", remember)
        result = _result(changed_files=["a.py", "b.py"])
        result.stats.step_count = 3
        result.stats.engine = "mock"
        result.stats.model = "some-model"
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=result))

        metadata = remember.records[0]["metadata"]
        assert metadata["task_id"] == "t1"
        assert metadata["status"] == OK
        assert metadata["engine"] == "mock"
        assert metadata["model"] == "some-model"
        assert metadata["step_count"] == 3
        assert metadata["changed_files"] == ["a.py", "b.py"]

    def test_the_remembered_event_shows_the_provenance_it_wrote(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C3 again: a host reading only the ledger can see what provenance
        landed, without going back to the store."""
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", _RememberRecorder())
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: continuity.RecallOutcome(
                ok=True, records=[{"id": "mem-1"}], degradation=None
            ),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task(id="t9")
        result = _result(task_id="t9", continued_from="t8")

        self._run_sequence(lifecycle, task=task, result=result)

        data = _of_kind(lifecycle, CHECKPOINT_REMEMBERED)[0].data
        assert data["record_id"] == record_id_for("t9")
        assert data["links"] == ["mem-1"]
        assert data["supersedes"] == record_id_for("t8")


# ---------------------------------------------------------------------------
# 5. degradation is host-visible (C3)
# ---------------------------------------------------------------------------


class TestContinuityMode:
    def test_the_mode_is_recorded_once_up_front(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        assert lifecycle.status is None

        lifecycle(_boundary(BOUNDARY_COMPLETION))
        lifecycle(_boundary(BOUNDARY_COMPLETION))

        mode_events = _of_kind(lifecycle, CHECKPOINT_MODE)
        assert len(mode_events) == 1
        assert mode_events[0].detail == continuity.FULL_CONTINUITY
        assert lifecycle.status is not None
        assert lifecycle.status.mode == continuity.FULL_CONTINUITY

    def test_both_subsystems_absent_is_a_recorded_no_continuity_mode(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "No module named 'eidetic'")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "No module named 'coherence'")
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task()

        assert lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="w", arguments={})) is None
        assert lifecycle(_boundary(BOUNDARY_COMPLETION, task=task)) is None
        assert lifecycle(_boundary(BOUNDARY_MEMORY, task=task)) is None

        mode_events = _of_kind(lifecycle, CHECKPOINT_MODE)
        assert mode_events[0].detail == continuity.NO_CONTINUITY
        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert {e.data.get("subsystem") for e in degraded} >= {"eidetic", "coherence"}
        assert continuity.CODE_IMPORT_FAILED in {e.data.get("code") for e in degraded}

    def test_a_missing_coherence_costs_no_snapshot_write(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A host that never installed coherence must not pay filesystem churn
        at every boundary for an assessment that cannot happen."""
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "No module named 'coherence'")
        workdir = tmp_path / "snapshots"
        workdir.mkdir()
        lifecycle = ContinuityLifecycle(_config(tmp_path, workdir=workdir))

        lifecycle(_boundary(BOUNDARY_COMPLETION))

        assert list(workdir.iterdir()) == []
        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert any(e.data.get("code") == continuity.CODE_IMPORT_FAILED for e in degraded)

    def test_memory_still_works_with_coherence_absent(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each subsystem is independently optional: an eidetic-only host must
        still get memory, gated by the structural signal alone."""
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "boom")
        remember = _RememberRecorder()
        monkeypatch.setattr(continuity, "remember", remember)
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        lifecycle(_boundary(BOUNDARY_MEMORY, result=_result(summary="a real deliverable")))

        assert len(remember.records) == 1
        assert _of_kind(lifecycle, CHECKPOINT_REMEMBERED)[0].detail == "no-coherence-signal"

    def test_no_storage_anchor_degrades_without_blocking_the_checkpoint(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The coherence-only configuration: no ``data_dir``, so eidetic is
        never touched (trap #1's refusal) and every attempt says so."""
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        lifecycle = ContinuityLifecycle(LifecycleConfig(workdir=tmp_path, embed_fn=_fake_embed))
        task = _task()

        lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="w", arguments={}))
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task))

        codes = {e.data.get("code") for e in _of_kind(lifecycle, CHECKPOINT_DEGRADED)}
        assert continuity.CODE_NO_STORAGE_ANCHOR in codes
        assert CHECKPOINT_ASSESSED in _kinds(lifecycle)
        assert "EIDETIC_DATA_DIR" not in os.environ

    def test_a_recall_degradation_is_forwarded(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: continuity.RecallOutcome(
                ok=False,
                records=[],
                degradation=continuity.Degradation(
                    subsystem="eidetic",
                    stage="recall",
                    code=continuity.CODE_SUBSYSTEM_ERROR,
                    reason="store on fire",
                    exception="RuntimeError",
                ),
            ),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_action=False))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="w", arguments={}))

        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert degraded[-1].detail == continuity.CODE_SUBSYSTEM_ERROR
        assert degraded[-1].data["exception"] == "RuntimeError"
        assert degraded[-1].data["reason"] == "store on fire"

    def test_a_partial_recall_still_yields_its_records(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``ok=True`` alongside a degradation — eidetic's reinforce-failed
        case. The records are real; the degradation is still recorded."""
        monkeypatch.setattr(
            continuity,
            "recall",
            lambda *a, **k: continuity.RecallOutcome(
                ok=True,
                records=[{"id": "mem-1"}],
                degradation=continuity.Degradation(
                    subsystem="eidetic",
                    stage="recall",
                    code=continuity.CODE_REINFORCE_FAILED,
                    reason="write-back failed",
                ),
            ),
        )
        lifecycle = ContinuityLifecycle(_config(tmp_path, assess_action=False))

        lifecycle(_boundary(BOUNDARY_ACTION, tool="w", arguments={}))

        assert _of_kind(lifecycle, CHECKPOINT_RECALLED)[0].data["ids"] == ["mem-1"]
        assert continuity.CODE_REINFORCE_FAILED in {
            e.data.get("code") for e in _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        }


class TestNeverRaises:
    def test_an_internal_fault_degrades_instead_of_raising(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """Fault injection: a malformed Boundary blows up deep inside the
        checkpoint's own attribute access, proving the outer guard catches ANY
        exception, not only the ones this module anticipated."""
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        broken = Boundary(name=BOUNDARY_MEMORY, task=_task(), result=None)  # type: ignore[arg-type]

        assert lifecycle(broken) is None

        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert any(e.detail == "internal-error" for e in degraded)

    def test_an_unwritable_snapshot_directory_degrades(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        lifecycle = ContinuityLifecycle(_config(tmp_path, workdir=tmp_path / "does-not-exist"))

        assert lifecycle(_boundary(BOUNDARY_COMPLETION)) is None

        degraded = _of_kind(lifecycle, CHECKPOINT_DEGRADED)
        assert any(e.detail == continuity.CODE_ARTIFACT_UNREADABLE for e in degraded)

    def test_the_snapshot_never_outlives_the_assessment(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assess = _AssessRecorder()
        monkeypatch.setattr(continuity, "assess", assess)
        workdir = tmp_path / "snapshots"
        workdir.mkdir()
        lifecycle = ContinuityLifecycle(_config(tmp_path, workdir=workdir))

        lifecycle(_boundary(BOUNDARY_COMPLETION))

        assert assess.paths and assess.texts
        assert list(workdir.iterdir()) == []

    def test_a_raising_sink_is_recorded_and_disables_itself(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        seen: list[LifecycleEvent] = []

        def boom(event: LifecycleEvent) -> None:
            seen.append(event)
            raise RuntimeError("sink exploded")

        lifecycle = ContinuityLifecycle(
            _config(tmp_path, consider_every_action=True), on_event=boom
        )
        task = _task()
        lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="a", arguments={}))
        lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="b", arguments={}))

        assert len(seen) == 1  # tried once, then disabled
        assert any(e.detail == "sink-failed" for e in _of_kind(lifecycle, CHECKPOINT_DEGRADED))
        assert len(lifecycle.events) > 1  # ...and the ledger kept filling regardless

    def test_the_ledger_is_populated_without_any_sink(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        lifecycle(_boundary(BOUNDARY_COMPLETION))
        assert lifecycle.events

    def test_a_working_sink_sees_every_event(self, clean_store_env: None, tmp_path: Path) -> None:
        seen: list[LifecycleEvent] = []
        lifecycle = ContinuityLifecycle(_config(tmp_path), on_event=seen.append)

        lifecycle(_boundary(BOUNDARY_COMPLETION))

        assert [e.to_dict() for e in seen] == [e.to_dict() for e in lifecycle.events]

    def test_an_interrupt_still_yields_control(self, clean_store_env: None, tmp_path: Path) -> None:
        """ "Never raise" means never raise *errors* — not never yield control.
        The same deliberate carve-out ``continuity`` documents."""

        def interrupted(boundary: Boundary) -> bool:
            raise KeyboardInterrupt

        lifecycle = ContinuityLifecycle(_config(tmp_path, consequential=interrupted))

        with pytest.raises(KeyboardInterrupt):
            lifecycle(_boundary(BOUNDARY_ACTION, tool="w", arguments={}))


class TestBoundedState:
    def test_the_ledger_is_bounded_and_says_what_it_dropped(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resident agent runs for days. An unbounded ledger is a leak, and a
        ledger that silently forgets is a C3 violation — so it is bounded AND
        the drop count is readable."""
        monkeypatch.setattr(continuity, "assess", _AssessRecorder())
        lifecycle = ContinuityLifecycle(_config(tmp_path, max_events=3))

        for index in range(10):
            lifecycle(_boundary(BOUNDARY_COMPLETION, task=_task(id=f"t{index}")))

        assert len(lifecycle.events) == 3
        assert lifecycle.dropped_events > 0

    def test_per_task_state_is_released_at_before_memory(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        monkeypatch.setattr(continuity, "remember", _RememberRecorder())
        lifecycle = ContinuityLifecycle(_config(tmp_path))
        task = _task(id="t9")

        lifecycle(_boundary(BOUNDARY_ACTION, task=task, tool="w", arguments={}))
        assert lifecycle.tracked_tasks == 1
        lifecycle(_boundary(BOUNDARY_MEMORY, task=task))
        assert lifecycle.tracked_tasks == 0

    def test_abandoned_work_items_are_evicted_rather_than_leaked(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A drive that raises never reaches before-memory (the loop's own
        design), so its trace would live forever. Oldest-first eviction bounds
        that, and the loss of those links is recorded rather than silent."""
        monkeypatch.setattr(continuity, "assess", _AssessRecorder())
        lifecycle = ContinuityLifecycle(_config(tmp_path, max_tracked_tasks=2))

        for index in range(5):
            lifecycle(
                _boundary(BOUNDARY_ACTION, task=_task(id=f"t{index}"), tool="w", arguments={})
            )

        assert lifecycle.tracked_tasks == 2
        evicted = [e for e in _of_kind(lifecycle, CHECKPOINT_DEGRADED) if e.detail == "trace-lost"]
        assert evicted and evicted[0].data["task_id"] == "t0"


# ---------------------------------------------------------------------------
# 6. full-drive integration + the behavioural separation proof
# ---------------------------------------------------------------------------


def _call(name: str = "write_file", **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


class _Scripted:
    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return item


class _RecordingExecutor:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.changed: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="the work is complete")
        if name != "write_file":
            raise UnknownToolError(f"unknown tool: {name}")
        self.changed.append("a.py")
        return ToolOutcome(result="wrote it", changed_file="a.py")


def _drive(
    lifecycle: ContinuityLifecycle,
    executor: _RecordingExecutor,
    *,
    hooks: Any = None,
) -> Any:
    return run(
        _Scripted(_turn(_call("write_file", path="a")), _turn(_call("finish"))),
        _task(),
        executor=executor,
        max_steps=10,
        hooks=hooks,
        continuity=lifecycle,
    )


class TestFullDrive:
    def test_all_three_checkpoints_fire_in_lived_order(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "assess", _AssessRecorder(_assess_outcome(consequence=0.9)))
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        outcome = _drive(lifecycle, _RecordingExecutor())

        boundaries = [e.boundary for e in lifecycle.events if e.boundary]
        assert boundaries[0] == BOUNDARY_ACTION
        assert boundaries[-1] == BOUNDARY_MEMORY
        assert BOUNDARY_COMPLETION in boundaries
        assert outcome.result.status == OK
        assert CHECKPOINT_REMEMBERED in _kinds(lifecycle)

    def test_a_real_drive_lands_a_real_record_in_the_real_store(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """No doubles at all except the offline embedder: the whole arc, from
        the loop's first tool call to a record readable out of eidetic.

        The threshold is pinned to ``0.0`` so the gate is open whatever the
        offline embedder's projection happens to be — this test is about the
        arc completing, not about a particular verdict.
        """
        lifecycle = ContinuityLifecycle(_config(tmp_path, consequence_threshold=0.0))

        outcome = _drive(lifecycle, _RecordingExecutor())

        assert outcome.result.summary == "the work is complete"
        back = continuity.recall(
            "the work is complete",
            data_dir=tmp_path / "store",
            mode="exact",
            reinforce=False,
        )
        assert [r["id"] for r in back.records] == [record_id_for("t1")]
        assert back.records[0]["metadata"]["changed_files"] == ["a.py"]
        assert back.records[0]["metadata"]["request"] == "do the thing"
        # the whole lived sequence, in order, against the real subsystems
        assert [(e.boundary, e.kind, e.detail) for e in lifecycle.events] == [
            ("", CHECKPOINT_MODE, continuity.FULL_CONTINUITY),
            (BOUNDARY_ACTION, CHECKPOINT_RECALLED, ""),
            (BOUNDARY_ACTION, CHECKPOINT_ASSESSED, ""),
            (BOUNDARY_ACTION, CHECKPOINT_ASSESS_SKIPPED, "already-considered"),
            (BOUNDARY_COMPLETION, CHECKPOINT_ASSESSED, ""),
            (BOUNDARY_MEMORY, CHECKPOINT_ASSESS_SKIPPED, "unchanged"),
            (BOUNDARY_MEMORY, CHECKPOINT_REMEMBERED, "high-consequence"),
        ]

    def test_the_loop_is_unaffected_by_a_broken_checkpoint(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both subsystems gone: the drive's own outcome is identical to a run
        with no continuity seam injected at all."""
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "boom")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "boom")
        with_seam = _drive(ContinuityLifecycle(_config(tmp_path)), _RecordingExecutor())
        executor = _RecordingExecutor()
        without_seam = run(
            _Scripted(_turn(_call("write_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=executor,
            max_steps=10,
        )

        assert with_seam.result.status == without_seam.result.status
        assert with_seam.result.summary == without_seam.result.summary
        assert with_seam.exit_reason == without_seam.exit_reason
        assert with_seam.degradations == without_seam.degradations == []


class TestPermissionAndCoherenceCannotInfluenceEachOther:
    """The third acceptance criterion, driven through the REAL loop.

    Two separate injection points: ``hooks=`` (permission — control-bearing at
    ``pre_tool``) and ``continuity=`` (coherence — return value discarded by
    the loop, by design). These tests prove the wall holds in both directions.
    """

    def _assess_fake(
        self, monkeypatch: pytest.MonkeyPatch, outcome: continuity.AssessOutcome
    ) -> _AssessRecorder:
        recorder = _AssessRecorder(outcome)
        monkeypatch.setattr(continuity, "assess", recorder)
        monkeypatch.setattr(continuity, "remember", _RememberRecorder())
        return recorder

    def test_the_worst_possible_verdict_denies_nothing(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direction 1: coherence cannot alter a tool-approval decision.

        The same drive is run twice with the two extreme verdicts. If a
        coherence verdict could reach the approval path at all, the executed
        tool sequence would differ. It is identical.
        """
        worst = self._assess_fake(
            monkeypatch, _assess_outcome(consequence=0.0, future_constraint=0.0)
        )
        denied_executor = _RecordingExecutor()
        _drive(ContinuityLifecycle(_config(tmp_path)), denied_executor)

        best = self._assess_fake(
            monkeypatch, _assess_outcome(consequence=1.0, future_constraint=1.0)
        )
        allowed_executor = _RecordingExecutor()
        _drive(ContinuityLifecycle(_config(tmp_path)), allowed_executor)

        assert denied_executor.seen == allowed_executor.seen
        assert [name for name, _ in denied_executor.seen] == ["write_file", "finish"]
        assert worst.texts and best.texts  # both verdicts really were consulted

    def test_an_explicit_allow_is_not_second_guessed_by_coherence(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._assess_fake(monkeypatch, _assess_outcome(consequence=0.0, future_constraint=0.0))
        executor = _RecordingExecutor()

        def always_allow(event: Any) -> Any:
            if event.event == EVENT_PRE_TOOL:
                return HookDecision(decision=DECISION_ALLOW, source="test")
            return None

        _drive(ContinuityLifecycle(_config(tmp_path)), executor, hooks=always_allow)

        assert [name for name, _ in executor.seen] == ["write_file", "finish"]

    def test_a_denial_cannot_suppress_or_alter_the_consultation(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direction 2: a tool-approval decision cannot alter a coherence verdict.

        The identical work item is driven with an always-allow and an
        always-deny hook. The loop offers ``before-action`` BEFORE it fires
        ``pre_tool``, and the checkpoint has no access to the decision — so the
        text assessed at that boundary is byte-identical under both policies.
        """

        def hook(decision: str) -> Any:
            def _hook(event: Any) -> Any:
                if event.event == EVENT_PRE_TOOL:
                    return HookDecision(decision=decision, reason="test", source="test")
                return None

            return _hook

        allowed = self._assess_fake(monkeypatch, _assess_outcome(consequence=0.9))
        allow_lifecycle = ContinuityLifecycle(_config(tmp_path))
        allow_executor = _RecordingExecutor()
        _drive(allow_lifecycle, allow_executor, hooks=hook(DECISION_ALLOW))

        denied = self._assess_fake(monkeypatch, _assess_outcome(consequence=0.9))
        deny_lifecycle = ContinuityLifecycle(_config(tmp_path))
        deny_executor = _RecordingExecutor()
        _drive(deny_lifecycle, deny_executor, hooks=hook(DECISION_DENY))

        # the denial really did block the tool...
        assert ("write_file", {"path": "a"}) in allow_executor.seen
        assert ("write_file", {"path": "a"}) not in deny_executor.seen
        # ...and the before-action consultation for it was identical anyway:
        # the loop offers the boundary before it fires the approval hook, and
        # the checkpoint has no access to the decision that follows.
        assert allowed.texts[0] == denied.texts[0]
        assert "write_file" in allowed.texts[0]
        allow_kinds = [e.kind for e in allow_lifecycle.events if e.boundary == BOUNDARY_ACTION]
        deny_kinds = [e.kind for e in deny_lifecycle.events if e.boundary == BOUNDARY_ACTION]
        assert allow_kinds[:2] == deny_kinds[:2] == [CHECKPOINT_RECALLED, CHECKPOINT_ASSESSED]

    def test_the_checkpoint_return_value_is_never_a_decision(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._assess_fake(monkeypatch, _assess_outcome(consequence=0.0))
        lifecycle = ContinuityLifecycle(_config(tmp_path))

        returned = lifecycle(_boundary(BOUNDARY_ACTION, tool="write_file", arguments={}))

        assert returned is None
        assert not isinstance(returned, HookDecision)
