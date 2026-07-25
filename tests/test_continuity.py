"""Tests for :mod:`embodiment.continuity` — the in-process eidetic/coherence seam.

Deviation **d2** replaced t13's subprocess adapter with direct, module-scope
imports of ``eidetic`` and ``coherence``. These tests changed shape with it:
there are no fake CLI executables on ``PATH`` any more, because there is no
``PATH`` lookup to fake. The happy paths run against the **real** libraries
(that is the point of importing them), and only the failure paths use fakes.

Everything here is still hermetic:

* every eidetic call is anchored at a throwaway ``tmp_path`` store — no real
  memory store is ever read or written;
* every coherence call injects an ``embed_fn``, so no embedding endpoint is
  ever dialled;
* nothing is written outside ``tmp_path``.

Four things are pinned:

* **Posture** — the module imports eidetic/coherence at *module scope* (d2's
  actual content), imports no ``colleague``, spawns no subprocess, and still
  defines no store/scoring/embedding logic of its own.
* **Trap #1 (eidetic's store resolution is cwd-dependent)** — in-process there
  is no subprocess ``cwd`` to pin, so ``data_dir`` is the sole anchor and it is
  mandatory. The live test proves a record lands in the anchor and **not** in
  the ambient git repo the test process is running inside.
* **Trap #2 (partial availability is reported in the payload, not the return)**
  — a *successful return* carrying a non-empty ``unavailable`` map is recorded
  as a degradation. Proven against the real coherence engine.
* **Never raise into the host** — every entry point degrades to a value plus a
  recorded transition.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from embodiment import continuity

MODULE_PATH = Path(continuity.__file__).resolve()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _imports_of(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


def _module_level_imports(path: Path) -> list[str]:
    """Imports that run at *import time*: module body, plus one level into a
    module-scope ``try``/``except`` (which is where the optional-import guard
    lives)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        names.extend(_imports_of(node))
        if isinstance(node, ast.Try):
            for inner in node.body + node.orelse + node.finalbody:
                names.extend(_imports_of(inner))
            for handler in node.handlers:
                for inner in handler.body:
                    names.extend(_imports_of(inner))
    return names


def _all_imports(path: Path) -> list[str]:
    """Every import anywhere in the file, at any nesting depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        names.extend(_imports_of(node))
    return names


def _non_docstring_str_constants(path: Path) -> list[str]:
    """String literals that are not docstrings.

    A docstring is a bare string *expression statement*; excluding those lets
    the module document a boundary it must not encode in code.
    """
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


def _imports_inside_functions(path: Path) -> list[str]:
    """Import statements nested inside a function body (i.e. lazy imports)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                names.extend(_imports_of(inner))
    return names


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """A deterministic, offline stand-in for coherence's embedding endpoint."""
    out: list[list[float]] = []
    for text in texts:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        out.append([byte / 255.0 for byte in digest[:16]])
    return out


def _dead_embed(texts: list[str]) -> list[list[float]]:
    """An embedder that is down — drives coherence's real ``unavailable`` path."""
    from coherence.meaning import EmbedUnavailable

    raise EmbedUnavailable("embedding endpoint unreachable at http://127.0.0.1:1/v1")


def _artifact(tmp_path: Path, text: str = "The team migrated the store after data loss.\n") -> Path:
    path = tmp_path / "artifact.md"
    path.write_text(text, encoding="utf-8")
    return path


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "rec-1", "text": "hello world", "type": "note"}
    base.update(overrides)
    return base


@pytest.fixture()
def clean_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no ambient store pin leaks into (or out of) a test."""
    monkeypatch.delenv("EIDETIC_DATA_DIR", raising=False)
    monkeypatch.delenv("DR_DATA_DIR", raising=False)


# ---------------------------------------------------------------------------
# posture — what deviation d2 actually changed
# ---------------------------------------------------------------------------


class TestPosture:
    """d2: direct module-scope imports, no subprocess, no reimplementation."""

    def test_eidetic_and_coherence_are_imported_at_module_scope(self) -> None:
        """The deviation's whole content: not lazy, not a subprocess."""
        imported = _module_level_imports(MODULE_PATH)
        assert "eidetic" in imported
        assert "coherence" in imported

    def test_no_lazy_function_local_import_of_either_subsystem(self) -> None:
        """d2 chose base dependencies over the optional-extra/lazy alternative.

        A function-local import would quietly re-introduce the rejected design
        while the pyproject still paid for the dependency.
        """
        lazy = _imports_inside_functions(MODULE_PATH)
        assert "eidetic" not in lazy
        assert "coherence" not in lazy

    def test_the_subprocess_adapter_is_gone(self) -> None:
        """No subprocess family import survives the rewrite (bandit-relevant).

        AST-based, not a substring scan: the module docstring legitimately
        explains *why* the subprocess adapter was replaced.
        """
        imported = _all_imports(MODULE_PATH)
        for banned in ("subprocess", "shlex", "shutil"):
            assert banned not in imported, f"continuity.py still imports {banned}"

    def test_no_stale_nosec_suppressions_remain(self) -> None:
        """Bandit ``# nosec`` markers were for the subprocess calls that are gone."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "nosec" not in source

    def test_no_colleague_import_anywhere_in_continuity(self) -> None:
        assert "colleague" not in _all_imports(MODULE_PATH)

    def test_continuity_defines_no_similarity_or_embedding_helpers(self) -> None:
        """Guard the 'no store, no scoring, no embedding logic' criterion.

        Checks defined/referenced code identifiers via AST — not docstring
        prose, which legitimately names these words while explaining that
        eidetic/coherence (not this module) own them. Composing a sibling's
        own scorer is fine; *defining* one here is not.
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        banned = ("cosine", "embed_texts", "bm25")
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                identifiers.add(node.name.lower())
            elif isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())
        for token in banned:
            assert not any(token in ident for ident in identifiers), token

    def test_module_defines_no_store_path_layout(self) -> None:
        """Store layout belongs to eidetic; embodiment must not encode it.

        ``<repo>/.eidetic/memory`` is eidetic's private ``_STORE_SUBPATH``.
        Hardcoding it here would be exactly the "reimplement, don't compose"
        failure the acceptance criterion forbids — and would silently rot the
        day eidetic changes its layout.
        """
        for value in _non_docstring_str_constants(MODULE_PATH):
            assert ".eidetic" not in value, f"store layout hardcoded: {value!r}"

    def test_the_sibling_imports_are_guarded(self) -> None:
        """Each subsystem stays optional — now that includes *import* failure.

        A bare module-scope ``from eidetic… import`` would make a broken
        sibling install break ``import embodiment`` outright, for a host that
        never touches continuity at all.
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        guarded: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Try):
                for inner in node.body:
                    guarded.update(_imports_of(inner))
        assert {"eidetic", "coherence"} <= guarded

    def test_no_sibling_import_sits_outside_a_guard(self) -> None:
        unguarded: set[str] = set()
        for node in ast.parse(MODULE_PATH.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.Try):
                unguarded.update(_imports_of(node))
        assert "eidetic" not in unguarded
        assert "coherence" not in unguarded


# ---------------------------------------------------------------------------
# probe() / continuity modes
# ---------------------------------------------------------------------------


class TestProbe:
    def test_probe_is_full_when_both_subsystems_import(self) -> None:
        status = continuity.probe()
        assert status.eidetic_available is True
        assert status.coherence_available is True
        assert status.mode == continuity.FULL_CONTINUITY
        assert status.degradations == ()

    def test_probe_records_no_continuity_when_both_imports_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "No module named 'eidetic'")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "No module named 'coherence'")

        status = continuity.probe()

        assert status.eidetic_available is False
        assert status.coherence_available is False
        assert status.mode == continuity.NO_CONTINUITY
        assert {d.code for d in status.degradations} == {continuity.CODE_IMPORT_FAILED}
        assert {d.subsystem for d in status.degradations} == {"eidetic", "coherence"}

    def test_probe_is_partial_when_only_coherence_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "boom")

        status = continuity.probe()

        assert status.mode == continuity.PARTIAL_CONTINUITY
        assert len(status.degradations) == 1
        assert status.degradations[0].subsystem == "coherence"

    def test_probe_is_partial_when_only_eidetic_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "boom")

        status = continuity.probe()

        assert status.mode == continuity.PARTIAL_CONTINUITY
        assert status.degradations[0].subsystem == "eidetic"

    def test_import_error_reason_is_carried_through_to_the_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C3: the degradation must say *why*, not merely that something failed."""
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "No module named 'data_refinery'")

        status = continuity.probe()

        assert "data_refinery" in status.degradations[0].reason

    def test_continuity_status_to_dict_round_trips_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "boom")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "boom")

        data = continuity.probe().to_dict()

        assert data["mode"] == continuity.NO_CONTINUITY
        assert len(data["degradations"]) == 2
        json.dumps(data)  # must not raise

    def test_availability_helpers_agree_with_probe(self) -> None:
        status = continuity.probe()
        assert continuity.eidetic_available() is status.eidetic_available
        assert continuity.coherence_available() is status.coherence_available


# ---------------------------------------------------------------------------
# TRAP #1 — eidetic's store resolution is cwd-dependent
# ---------------------------------------------------------------------------


class TestTrapOneStorageAnchor:
    """In-process there is no subprocess cwd, so ``data_dir`` is the sole anchor.

    Over the CLI, ``repo_path`` pinned the child's ``cwd`` so eidetic's
    ``git rev-parse --show-toplevel`` probe resolved against a directory this
    adapter chose. In-process that lever does not exist: the probe reads the
    *host process's* ``os.getcwd()``. The only remaining honest anchor is
    eidetic's own ``EIDETIC_DATA_DIR`` override, which short-circuits
    ``_resolve_write_dir`` before the git probe runs at all.
    """

    def test_remember_without_data_dir_degrades_before_doing_anything(
        self, clean_store_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("eidetic must not be reached without a storage anchor")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", _explode)

        outcome = continuity.remember(_record())

        assert outcome.ok is False
        assert outcome.record_id is None
        assert outcome.degradation is not None
        assert outcome.degradation.subsystem == "eidetic"
        assert outcome.degradation.stage == "remember"
        assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR

    def test_recall_without_data_dir_degrades_before_doing_anything(
        self, clean_store_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("eidetic must not be reached without a storage anchor")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", _explode)

        outcome = continuity.recall("hello")

        assert outcome.ok is False
        assert outcome.records == []
        assert outcome.degradation is not None
        assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR

    def test_missing_anchor_never_mutates_the_host_environment(self, clean_store_env: None) -> None:
        continuity.remember(_record())
        assert "EIDETIC_DATA_DIR" not in os.environ
        assert "DR_DATA_DIR" not in os.environ

    def test_the_anchor_check_runs_before_the_import_check(
        self, clean_store_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Anchor-first is deliberate: it is the safety-critical refusal, and it
        must fire identically whether or not eidetic happens to be installed."""
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "boom")

        outcome = continuity.remember(_record())

        assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR

    def test_remember_writes_to_the_anchor_and_not_the_ambient_repo(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """THE trap-#1 acceptance criterion, run against the real eidetic.

        This test process runs with its cwd inside a git repo, so an unanchored
        *public* record would land in ``<repo-root>/.eidetic/memory`` — committed,
        team-shared, and wrong. Assert it landed in the anchor instead, and that
        eidetic's own resolver agrees under the pin.
        """
        from eidetic.memory.backend import _resolve_write_dir

        store = tmp_path / "store"
        repo_root = Path(__file__).resolve().parents[1]

        # Precondition: unanchored, eidetic really would write into this repo.
        assert str(repo_root) in _resolve_write_dir("public")

        outcome = continuity.remember(_record(id="anchored-1"), data_dir=store)

        assert outcome.ok is True
        assert outcome.record_id == "anchored-1"
        written = sorted(p for p in store.rglob("*") if p.is_file())
        assert written, "record did not land in the anchor"
        assert "anchored-1" in written[0].read_text(encoding="utf-8")
        assert not (repo_root / ".eidetic").exists()

    def test_the_pin_is_active_for_the_whole_call(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the pin is not merely computed — it is in force when eidetic looks."""
        seen: dict[str, Any] = {}

        class _Recording:
            def upsert(self, record: Any) -> None:
                from eidetic.memory.backend import _resolve_write_dir

                seen["env"] = os.environ.get("EIDETIC_DATA_DIR")
                seen["resolved_public"] = _resolve_write_dir("public")
                seen["resolved_private"] = _resolve_write_dir("private")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Recording())

        continuity.remember(_record(), data_dir=tmp_path)

        assert seen["env"] == str(tmp_path)
        # Both visibilities collapse onto the anchor: the git probe never runs.
        assert seen["resolved_public"] == str(tmp_path)
        assert seen["resolved_private"] == str(tmp_path)

    def test_the_pin_is_restored_after_the_call(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """In-process the pin mutates ``os.environ`` — it must not leak.

        eidetic's own ``_bridge_env`` additionally writes ``DR_DATA_DIR`` and
        never restores it. Over a subprocess boundary that leak was invisible;
        in-process it would outlive the call, so it is restored here too.
        """
        continuity.remember(_record(), data_dir=tmp_path)

        assert "EIDETIC_DATA_DIR" not in os.environ
        assert "DR_DATA_DIR" not in os.environ

    def test_a_pre_existing_pin_is_restored_exactly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EIDETIC_DATA_DIR", "/host/choice")
        monkeypatch.setenv("DR_DATA_DIR", "/host/dr-choice")

        continuity.remember(_record(), data_dir=tmp_path / "store")

        assert os.environ["EIDETIC_DATA_DIR"] == "/host/choice"
        assert os.environ["DR_DATA_DIR"] == "/host/dr-choice"

    def test_the_pin_is_restored_even_when_eidetic_raises(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Exploding:
            def upsert(self, record: Any) -> None:
                raise RuntimeError("store on fire")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Exploding())

        outcome = continuity.remember(_record(), data_dir=tmp_path)

        assert outcome.ok is False
        assert "EIDETIC_DATA_DIR" not in os.environ

    def test_recall_reads_from_the_anchor_only(self, clean_store_env: None, tmp_path: Path) -> None:
        store = tmp_path / "store"
        continuity.remember(_record(id="r1", text="iceland is cold"), data_dir=store)

        outcome = continuity.recall("iceland", data_dir=store, mode="exact")

        assert outcome.ok is True
        assert [r["id"] for r in outcome.records] == ["r1"]

    def test_recall_against_an_empty_anchor_returns_no_records(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        outcome = continuity.recall("anything", data_dir=tmp_path / "empty", mode="exact")

        assert outcome.ok is True
        assert outcome.records == []
        assert outcome.degradation is None


# ---------------------------------------------------------------------------
# eidetic seam — remember / recall behaviour
# ---------------------------------------------------------------------------


class TestRemember:
    def test_round_trip_through_the_real_library(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(
            _record(id="r1", text="the migration caused data loss", type="lesson"),
            data_dir=tmp_path,
            scope="embodiment",
        )

        outcome = continuity.recall(
            "migration", data_dir=tmp_path, scope="embodiment", mode="exact"
        )

        assert [r["id"] for r in outcome.records] == ["r1"]
        assert outcome.records[0]["type"] == "lesson"
        assert outcome.records[0]["scope"] == {"name": "embodiment", "visibility": "public"}

    def test_missing_required_keys_degrade_as_an_invalid_record(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        outcome = continuity.remember({"id": "x"}, data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_INVALID_RECORD
        assert "text" in outcome.degradation.reason
        assert "type" in outcome.degradation.reason

    def test_a_non_mapping_record_degrades_rather_than_raising(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        outcome = continuity.remember("not a mapping", data_dir=tmp_path)  # type: ignore[arg-type]

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_INVALID_RECORD

    def test_a_caller_supplied_score_is_never_stored(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """Mirrors eidetic's own rule: score is a recall-time artefact."""
        continuity.remember(_record(id="r1", score=0.99), data_dir=tmp_path)

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["score"] != 0.99

    def test_created_is_stamped_when_absent(self, clean_store_env: None, tmp_path: Path) -> None:
        continuity.remember(_record(id="r1"), data_dir=tmp_path)

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["created"] != "date-unknown"

    def test_an_explicit_created_is_preserved_verbatim(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="r1", created="2020-01-01"), data_dir=tmp_path)

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["created"] == "2020-01-01"

    def test_added_by_is_passed_through_and_never_inferred(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """Identity resolution belongs to ``embodiment.identity``, one layer up.

        eidetic's CLI stamps ``added_by`` from *its own* culture.yaml; reaching
        that in-process would stamp eidetic's identity onto embodiment's records.
        """
        continuity.remember(_record(id="r1"), data_dir=tmp_path, added_by="embodiment")

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["added_by"] == "embodiment"

    def test_added_by_defaults_to_none_rather_than_a_guess(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="r1"), data_dir=tmp_path)

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["added_by"] is None

    def test_an_inline_scope_on_the_record_is_honoured(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(
            _record(id="r1", scope={"name": "inline", "visibility": "private"}),
            data_dir=tmp_path,
        )

        outcome = continuity.recall(
            "hello", data_dir=tmp_path, scope="inline", visibility="private", mode="exact"
        )

        assert [r["id"] for r in outcome.records] == ["r1"]

    def test_a_malformed_inline_scope_degrades(self, clean_store_env: None, tmp_path: Path) -> None:
        outcome = continuity.remember(_record(id="r1", scope={"name": "inline"}), data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_INVALID_RECORD

    def test_import_failure_degrades_rather_than_raising(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "No module named 'eidetic'")

        outcome = continuity.remember(_record(), data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_IMPORT_FAILED
        assert outcome.degradation.subsystem == "eidetic"
        assert outcome.degradation.stage == "remember"

    def test_a_raising_backend_degrades_and_names_the_exception(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Exploding:
            def upsert(self, record: Any) -> None:
                raise RuntimeError("mongo is down")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Exploding())

        outcome = continuity.remember(_record(), data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_SUBSYSTEM_ERROR
        assert outcome.degradation.exception == "RuntimeError"
        assert "mongo is down" in outcome.degradation.reason

    def test_an_unknown_backend_degrades(self, clean_store_env: None, tmp_path: Path) -> None:
        """eidetic raises its own ``CliError`` for this; it must not reach the host."""
        outcome = continuity.remember(_record(), data_dir=tmp_path, backend="not-a-backend")

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_SUBSYSTEM_ERROR

    def test_a_runaway_reason_is_capped(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Exploding:
            def upsert(self, record: Any) -> None:
                raise RuntimeError("x" * 5000)

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Exploding())

        outcome = continuity.remember(_record(), data_dir=tmp_path)

        assert len(outcome.degradation.reason) <= continuity._MAX_REASON_LEN


class TestRecall:
    def test_records_are_returned_in_eidetics_own_json_shape(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """The subprocess adapter returned ``eidetic recall --json`` verbatim;
        the in-process seam must hand back the same dicts, not a new shape."""
        continuity.remember(_record(id="r1", text="iceland is cold"), data_dir=tmp_path)

        outcome = continuity.recall("iceland", data_dir=tmp_path, mode="exact")

        record = outcome.records[0]
        assert set(record) >= {
            "id",
            "text",
            "type",
            "hash",
            "metadata",
            "scope",
            "score",
            "created",
            "signal",
        }
        json.dumps(outcome.records)  # must not raise

    def test_every_hit_carries_a_score_and_a_signal(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="r1", text="iceland is cold"), data_dir=tmp_path)

        outcome = continuity.recall("iceland", data_dir=tmp_path, mode="exact")

        assert isinstance(outcome.records[0]["score"], float)
        assert isinstance(outcome.records[0]["signal"], float)

    def test_signal_is_computed_with_the_injected_clock(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """``now`` is injectable so a host's recall stays deterministic."""
        continuity.remember(
            _record(id="r1", text="iceland is cold", created="2020-01-01T00:00:00+00:00"),
            data_dir=tmp_path,
        )
        early = continuity.recall(
            "iceland",
            data_dir=tmp_path,
            mode="exact",
            now=datetime(2020, 1, 2, tzinfo=timezone.utc),
        )
        late = continuity.recall(
            "iceland",
            data_dir=tmp_path,
            mode="exact",
            now=datetime(2030, 1, 2, tzinfo=timezone.utc),
        )

        assert early.records[0]["signal"] > late.records[0]["signal"]

    def test_top_k_bounds_the_result_set(self, clean_store_env: None, tmp_path: Path) -> None:
        for index in range(5):
            continuity.remember(
                _record(id=f"r{index}", text=f"iceland entry {index}"), data_dir=tmp_path
            )

        outcome = continuity.recall("iceland", data_dir=tmp_path, mode="exact", top_k=2)

        assert len(outcome.records) == 2

    def test_shadowed_records_are_excluded_by_default(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="live", text="iceland live"), data_dir=tmp_path)
        continuity.remember(
            _record(id="old", text="iceland old", lifecycle="shadowed"), data_dir=tmp_path
        )

        default = continuity.recall("iceland", data_dir=tmp_path, mode="exact")
        widened = continuity.recall(
            "iceland", data_dir=tmp_path, mode="exact", include_shadowed=True
        )

        assert [r["id"] for r in default.records] == ["live"]
        assert {r["id"] for r in widened.records} == {"live", "old"}

    def test_archived_records_are_excluded_by_default(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="live", text="iceland live"), data_dir=tmp_path)
        continuity.remember(
            _record(id="gone", text="iceland gone", lifecycle="archived"), data_dir=tmp_path
        )

        default = continuity.recall("iceland", data_dir=tmp_path, mode="exact")
        widened = continuity.recall(
            "iceland", data_dir=tmp_path, mode="exact", include_archived=True
        )

        assert [r["id"] for r in default.records] == ["live"]
        assert {r["id"] for r in widened.records} == {"live", "gone"}

    def test_metadata_filters_are_passed_through(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(
            _record(id="a", text="iceland a", metadata={"family": "lesson"}), data_dir=tmp_path
        )
        continuity.remember(
            _record(id="b", text="iceland b", metadata={"family": "task"}), data_dir=tmp_path
        )

        outcome = continuity.recall(
            "iceland", data_dir=tmp_path, mode="exact", filters={"family": "lesson"}
        )

        assert [r["id"] for r in outcome.records] == ["a"]

    def test_recall_reinforces_matched_records_by_default(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """eidetic's documented recall contract: a hit bumps recall_count."""
        continuity.remember(_record(id="r1", text="iceland is cold"), data_dir=tmp_path)

        first = continuity.recall("iceland", data_dir=tmp_path, mode="exact")
        second = continuity.recall("iceland", data_dir=tmp_path, mode="exact")

        assert first.records[0]["recall_count"] == 0
        assert second.records[0]["recall_count"] == 1

    def test_reinforcement_can_be_turned_off(self, clean_store_env: None, tmp_path: Path) -> None:
        continuity.remember(_record(id="r1", text="iceland is cold"), data_dir=tmp_path)

        continuity.recall("iceland", data_dir=tmp_path, mode="exact", reinforce=False)
        second = continuity.recall("iceland", data_dir=tmp_path, mode="exact", reinforce=False)

        assert second.records[0]["recall_count"] == 0

    def test_a_failed_reinforcement_still_returns_the_records(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read-only store must not lose the read. C3: it is still recorded."""

        class _ReadOnly:
            def search(self, *args: Any, **kwargs: Any) -> list[Any]:
                from eidetic.memory.record import Record
                from eidetic.memory.scope import Scope

                hit = Record(
                    id="r1",
                    text="iceland",
                    type="note",
                    hash="",
                    metadata={},
                    scope=Scope("default", "public"),
                )
                hit.score = 1.0
                return [hit]

            def upsert(self, record: Any) -> None:
                raise OSError("read-only file system")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _ReadOnly())

        outcome = continuity.recall("iceland", data_dir=tmp_path, mode="exact")

        assert outcome.ok is True
        assert [r["id"] for r in outcome.records] == ["r1"]
        assert outcome.degradation is not None
        assert outcome.degradation.code == continuity.CODE_REINFORCE_FAILED

    def test_import_failure_degrades_rather_than_raising(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "boom")

        outcome = continuity.recall("hi", data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_IMPORT_FAILED

    def test_a_raising_search_degrades(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Exploding:
            def search(self, *args: Any, **kwargs: Any) -> list[Any]:
                raise RuntimeError("index corrupt")

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Exploding())

        outcome = continuity.recall("hi", data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.records == []
        assert outcome.degradation.code == continuity.CODE_SUBSYSTEM_ERROR
        assert outcome.degradation.exception == "RuntimeError"

    def test_a_non_record_search_result_degrades_as_malformed(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defensive: a backend that answers with the wrong shape must not
        propagate an AttributeError into the host's main path."""

        class _Wrong:
            def search(self, *args: Any, **kwargs: Any) -> Any:
                return ["not a record"]

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Wrong())

        outcome = continuity.recall("hi", data_dir=tmp_path)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_MALFORMED_RESULT


# ---------------------------------------------------------------------------
# TRAP #2 — partial availability rides the payload, not the return
# ---------------------------------------------------------------------------


class TestTrapTwoPartialAvailability:
    """Over the CLI this was "exit 0 with an ``unavailable`` map". In-process it
    is "returned normally with an ``unavailable`` map" — the same trap wearing
    different clothes. Never infer success from "it didn't raise"."""

    def test_unreachable_embedder_records_degradation_despite_a_clean_return(
        self, tmp_path: Path
    ) -> None:
        """THE central acceptance criterion, ported from the subprocess suite
        and now run against the REAL coherence engine."""
        artifact = _artifact(tmp_path)

        outcome = continuity.assess(artifact, embed_fn=_dead_embed)

        # coherence returned normally and produced a usable, partial report...
        assert outcome.ok is True
        assert outcome.unavailable
        assert "meaning" in outcome.unavailable
        assert "investiture" in outcome.unavailable
        assert "quality" in outcome.domains
        # ...but reading ok alone would hide the gap. It must be recorded.
        assert outcome.degradation is not None
        assert outcome.degradation.subsystem == "coherence"
        assert outcome.degradation.stage == "assess"
        assert outcome.degradation.code == continuity.CODE_DOMAIN_UNAVAILABLE
        assert "meaning" in outcome.degradation.reason
        assert "investiture" in outcome.degradation.reason

    def test_a_clean_return_is_not_treated_as_a_clean_run(self, tmp_path: Path) -> None:
        """The regression this whole class exists to prevent, stated bluntly."""
        artifact = _artifact(tmp_path)

        outcome = continuity.assess(artifact, embed_fn=_dead_embed)

        assert outcome.ok is True and outcome.degradation is not None

    def test_full_availability_records_no_degradation(self, tmp_path: Path) -> None:
        artifact = _artifact(tmp_path)

        outcome = continuity.assess(artifact, embed_fn=_fake_embed)

        assert outcome.ok is True
        assert outcome.unavailable == {}
        assert outcome.degradation is None
        assert set(outcome.domains) == {"quality", "meaning", "investiture"}

    def test_the_unavailable_map_is_surfaced_verbatim(self, tmp_path: Path) -> None:
        """A host needs the machine-readable code, not just the human reason."""
        artifact = _artifact(tmp_path)

        outcome = continuity.assess(artifact, embed_fn=_dead_embed)

        assert outcome.unavailable["meaning"]["code"] == "embed_endpoint_unreachable"

    def test_a_synthetic_partial_payload_still_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinned independently of coherence's own internals, so the guard
        survives a change in *which* domains can go unavailable."""
        payload = {
            "domain": "assess",
            "score_type": "multi_domain_report",
            "scores": {},
            "frame": None,
            "diagnostics": [{"code": "domain_unavailable", "message": "signal unavailable"}],
            "artifact": "x",
            "domains": {"quality": {}},
            "unavailable": {"signal": {"code": "whatever", "reason": "nope"}},
        }
        monkeypatch.setattr(continuity, "_coherence_assess", lambda *a, **k: payload)

        outcome = continuity.assess(_artifact(tmp_path))

        assert outcome.ok is True
        assert outcome.degradation.code == continuity.CODE_DOMAIN_UNAVAILABLE
        assert "signal" in outcome.degradation.reason


class TestAssess:
    def test_a_missing_artifact_degrades_rather_than_raising(self, tmp_path: Path) -> None:
        """coherence's library layer lets file I/O errors propagate — the CLI
        boundary converted them to exit 1/2. In-process embodiment IS that
        boundary, so it must catch them."""
        outcome = continuity.assess(tmp_path / "nope.md", embed_fn=_fake_embed)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_ARTIFACT_UNREADABLE
        assert outcome.degradation.exception == "FileNotFoundError"

    def test_a_directory_artifact_degrades(self, tmp_path: Path) -> None:
        outcome = continuity.assess(tmp_path, embed_fn=_fake_embed)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_ARTIFACT_UNREADABLE

    def test_undecodable_bytes_degrade(self, tmp_path: Path) -> None:
        artifact = tmp_path / "artifact.md"
        artifact.write_bytes(b"\xff\xfe\x00binary")

        outcome = continuity.assess(artifact, embed_fn=_fake_embed)

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_ARTIFACT_UNREADABLE

    def test_import_failure_degrades_rather_than_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "No module named 'numpy'")

        outcome = continuity.assess(_artifact(tmp_path))

        assert outcome.ok is False
        assert outcome.domains == {}
        assert outcome.unavailable == {}
        assert outcome.degradation.code == continuity.CODE_IMPORT_FAILED
        assert outcome.degradation.subsystem == "coherence"
        assert "numpy" in outcome.degradation.reason

    def test_an_unexpected_engine_error_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("anchor set is corrupt")

        monkeypatch.setattr(continuity, "_coherence_assess", _boom)

        outcome = continuity.assess(_artifact(tmp_path))

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_SUBSYSTEM_ERROR
        assert outcome.degradation.exception == "ValueError"

    def test_a_non_dict_report_degrades_as_malformed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_coherence_assess", lambda *a, **k: ["not", "a", "dict"])

        outcome = continuity.assess(_artifact(tmp_path))

        assert outcome.ok is False
        assert outcome.degradation.code == continuity.CODE_MALFORMED_RESULT

    def test_reference_date_defaults_to_today_like_the_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """coherence's engine never reads the clock; its CLI supplies today.
        In-process embodiment is the boundary, so it supplies today too."""
        seen: dict[str, Any] = {}

        def _capture(path: Any, **kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {"domains": {}, "unavailable": {}}

        monkeypatch.setattr(continuity, "_coherence_assess", _capture)

        continuity.assess(_artifact(tmp_path))

        assert seen["reference_date"] == date.today()

    def test_an_explicit_reference_date_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _capture(path: Any, **kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {"domains": {}, "unavailable": {}}

        monkeypatch.setattr(continuity, "_coherence_assess", _capture)

        continuity.assess(_artifact(tmp_path), reference_date=date(2020, 1, 1))

        assert seen["reference_date"] == date(2020, 1, 1)

    def test_assess_needs_no_storage_anchor(self, clean_store_env: None, tmp_path: Path) -> None:
        """coherence reads a path and talks to an embedder; it has no
        cwd-dependent store to land in, so trap #1 does not apply."""
        outcome = continuity.assess(_artifact(tmp_path), embed_fn=_fake_embed)

        assert outcome.ok is True
        assert "EIDETIC_DATA_DIR" not in os.environ


# ---------------------------------------------------------------------------
# never raise into the host
# ---------------------------------------------------------------------------


class TestNeverRaises:
    """Every entry point returns a value plus a recorded transition — always."""

    def test_no_entry_point_raises_when_both_subsystems_are_gone(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "gone")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "gone")

        assert continuity.probe().mode == continuity.NO_CONTINUITY
        assert continuity.remember(_record(), data_dir=tmp_path).ok is False
        assert continuity.recall("hi", data_dir=tmp_path).ok is False
        assert continuity.assess(_artifact(tmp_path)).ok is False

    def test_every_failure_carries_a_recorded_degradation(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C3: nothing degrades silently — a False ``ok`` always has a reason."""
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "gone")
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "gone")

        for outcome in (
            continuity.remember(_record(), data_dir=tmp_path),
            continuity.recall("hi", data_dir=tmp_path),
            continuity.assess(_artifact(tmp_path)),
        ):
            assert outcome.ok is False
            assert outcome.degradation is not None
            assert outcome.degradation.reason

    def test_a_base_exception_from_a_subsystem_is_not_swallowed(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``KeyboardInterrupt``/``SystemExit`` must still reach the host —
        "never raise" means never raise *errors*, not never yield control."""

        class _Interrupting:
            def upsert(self, record: Any) -> None:
                raise KeyboardInterrupt

        monkeypatch.setattr(continuity, "_eidetic_get_backend", lambda *a, **k: _Interrupting())

        with pytest.raises(KeyboardInterrupt):
            continuity.remember(_record(), data_dir=tmp_path)


# ---------------------------------------------------------------------------
# to_dict() shapes
# ---------------------------------------------------------------------------


class TestOutcomeShapes:
    def test_degradation_to_dict_omits_exception_when_none(self) -> None:
        degradation = continuity.Degradation(
            subsystem="eidetic",
            stage="remember",
            code=continuity.CODE_IMPORT_FAILED,
            reason="x",
        )
        assert "exception" not in degradation.to_dict()

    def test_degradation_to_dict_includes_exception_when_set(self) -> None:
        degradation = continuity.Degradation(
            subsystem="eidetic",
            stage="remember",
            code=continuity.CODE_SUBSYSTEM_ERROR,
            reason="x",
            exception="RuntimeError",
        )
        assert degradation.to_dict()["exception"] == "RuntimeError"

    def test_degradation_no_longer_carries_an_exit_code(self) -> None:
        """The subprocess model leaked ``exit_code`` into the public shape.
        In-process there is no exit code, and inventing one would be a lie."""
        assert not hasattr(
            continuity.Degradation(subsystem="s", stage="t", code="c", reason="r"), "exit_code"
        )

    def test_remember_outcome_round_trips_as_json(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        outcome = continuity.remember(_record(), data_dir=tmp_path)
        data = outcome.to_dict()
        assert "degradation" not in data
        json.dumps(data)

    def test_remember_outcome_includes_degradation_when_present(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "gone")
        data = continuity.remember(_record(), data_dir=tmp_path).to_dict()
        assert data["degradation"]["code"] == continuity.CODE_IMPORT_FAILED
        json.dumps(data)

    def test_recall_outcome_round_trips_as_json(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        outcome = continuity.recall("hi", data_dir=tmp_path, mode="exact")
        data = outcome.to_dict()
        assert "degradation" not in data
        json.dumps(data)

    def test_recall_outcome_includes_degradation_when_present(
        self, clean_store_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_EIDETIC_IMPORT_ERROR", "gone")
        data = continuity.recall("hi", data_dir=tmp_path).to_dict()
        assert data["degradation"]["code"] == continuity.CODE_IMPORT_FAILED
        json.dumps(data)

    def test_assess_outcome_round_trips_as_json(self, tmp_path: Path) -> None:
        outcome = continuity.assess(_artifact(tmp_path), embed_fn=_fake_embed)
        data = outcome.to_dict()
        assert "degradation" not in data
        json.dumps(data)

    def test_assess_outcome_includes_degradation_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(continuity, "_COHERENCE_IMPORT_ERROR", "gone")
        data = continuity.assess(_artifact(tmp_path)).to_dict()
        assert data["degradation"]["code"] == continuity.CODE_IMPORT_FAILED
        json.dumps(data)

    def test_every_public_name_is_exported(self) -> None:
        for name in continuity.__all__:
            assert hasattr(continuity, name), name


# ---------------------------------------------------------------------------
# scope/visibility drift test — eidetic docs/contract.md §4
# ---------------------------------------------------------------------------

# Frozen from eidetic-cli's docs/contract.md machine-readable block. These are
# the literals this module's constants are pinned against even when no sibling
# eidetic-cli checkout is available to compare live (e.g. a bare CI checkout of
# embodiment alone) — see the live comparison below.
_FROZEN_CONTRACT_VERSION = "1"
_FROZEN_DEFAULT_VISIBILITY = "public"
_FROZEN_PRIVATE_REQUIRES_EXPLICIT_FLAG = "true"
_FROZEN_SCOPE_NAMING = "culture.yaml-suffix-per-repo"


class TestContractDrift:
    """§4 tells every consumer to pin its own scope/visibility flags with its
    OWN drift test rather than trusting they happen to agree. Importing eidetic
    instead of shelling out to it does not change that — if anything it raises
    the stakes, because the flags are now Python defaults rather than argv."""

    def test_default_visibility_matches_the_frozen_contract_literal(self) -> None:
        assert continuity.DEFAULT_VISIBILITY == _FROZEN_DEFAULT_VISIBILITY

    def test_private_requires_explicit_flag_matches_the_frozen_literal(self) -> None:
        assert continuity.PRIVATE_REQUIRES_EXPLICIT_FLAG is True

    def test_scope_naming_convention_matches_the_frozen_literal(self) -> None:
        assert continuity.SCOPE_NAMING_CONVENTION == _FROZEN_SCOPE_NAMING

    def test_default_scope_matches_eidetics_own_default(self) -> None:
        assert continuity.DEFAULT_SCOPE == "default"

    def test_a_no_visibility_call_writes_a_public_record(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        """Proves PRIVATE_REQUIRES_EXPLICIT_FLAG is honoured by construction —
        checked on the stored record, which is what actually matters, rather
        than on an argv the in-process seam no longer builds."""
        continuity.remember(_record(id="r1"), data_dir=tmp_path)

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact")

        assert outcome.records[0]["scope"]["visibility"] == "public"

    def test_private_is_reachable_only_via_an_explicit_argument(
        self, clean_store_env: None, tmp_path: Path
    ) -> None:
        continuity.remember(_record(id="r1"), data_dir=tmp_path, visibility="private")

        outcome = continuity.recall("hello", data_dir=tmp_path, mode="exact", visibility="private")

        assert outcome.records[0]["scope"]["visibility"] == "private"

    def test_defaults_match_eidetics_live_argparse_defaults(self) -> None:
        """Now that eidetic is imported, its real defaults are readable — pin
        against the code, not only against the doc."""
        import argparse

        from eidetic.cli._commands import recall as eidetic_recall
        from eidetic.cli._commands import remember as eidetic_remember

        for verb, module in (("remember", eidetic_remember), ("recall", eidetic_recall)):
            sub = argparse.ArgumentParser().add_subparsers()
            module.register(sub)
            defaults = {action.dest: action.default for action in sub.choices[verb]._actions}
            assert defaults["scope"] == continuity.DEFAULT_SCOPE, verb
            assert defaults["visibility"] == continuity.DEFAULT_VISIBILITY, verb

    def _find_sibling_contract_doc(self) -> Path | None:
        """Best-effort locate a sibling eidetic-cli checkout's docs/contract.md.

        Tries an explicit override env var first, then candidates covering a
        normal sibling checkout (``../eidetic-cli``) and a worktree one level
        deeper (``../../eidetic-cli`` — this repo's
        ``../.worktrees.embodiment/<name>/`` convention). Returns ``None``
        rather than raising when nothing is reachable.
        """
        override = os.environ.get("EMBODIMENT_TEST_EIDETIC_CONTRACT")
        if override:
            candidate = Path(override)
            return candidate if candidate.is_file() else None

        repo_root = Path(__file__).resolve().parents[1]
        for candidate in (
            repo_root.parent / "eidetic-cli" / "docs" / "contract.md",
            repo_root.parent.parent / "eidetic-cli" / "docs" / "contract.md",
        ):
            if candidate.is_file():
                return candidate
        return None

    def test_drift_against_the_live_contract_doc_when_reachable(self) -> None:
        """Opportunistic live-drift check (§4's suggested pattern). Skips —
        never fails — when no sibling checkout is reachable; the frozen-literal
        tests above still guard that case."""
        contract_path = self._find_sibling_contract_doc()
        if contract_path is None:
            pytest.skip(
                "no sibling eidetic-cli checkout found (set "
                "EMBODIMENT_TEST_EIDETIC_CONTRACT to its docs/contract.md)"
            )
        match = re.search(
            r"```text\n(.*?)\n```", contract_path.read_text(encoding="utf-8"), re.DOTALL
        )
        assert match is not None, "contract.md is missing its machine-readable block"
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, _, value = line.strip().partition(":")
            if value:
                fields[key.strip()] = value.strip()

        assert fields.get("version") == _FROZEN_CONTRACT_VERSION
        assert fields.get("default_visibility") == continuity.DEFAULT_VISIBILITY
        assert (
            fields.get("private_requires_explicit_flag") == _FROZEN_PRIVATE_REQUIRES_EXPLICIT_FLAG
        )
        assert fields.get("scope_naming") == continuity.SCOPE_NAMING_CONVENTION


def test_python_version_guard() -> None:
    """The seam relies on 3.12 generics/typing already required by pyproject."""
    assert sys.version_info >= (3, 12)
