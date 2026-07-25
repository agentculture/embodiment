"""Tests for :mod:`embodiment.continuity` — the eidetic/coherence subprocess adapter (t13).

Everything here is hermetic: fake ``eidetic``/``coherence`` executables are
written to a throwaway ``tmp_path`` bin directory and put on ``PATH`` via
``monkeypatch`` — the real CLIs are never invoked, and no real memory store is
ever touched. Three things are pinned:

* **Purity** — the module imports stdlib only (constraint C1) and no
  ``embodiment`` module imports ``colleague``.
* **Trap #1 (cwd-dependent eidetic store resolution)** — every
  ``remember``/``recall`` call sets ``EIDETIC_DATA_DIR`` and/or an explicit
  subprocess ``cwd``; neither ever falls back to the ambient process cwd, and
  omitting both is itself a recorded degradation rather than a silent write.
* **Trap #2 (``coherence assess`` exits 0 on an unreachable embedder)** — an
  ``unavailable`` entry in the JSON payload is recorded as a degradation even
  though the fake CLI exits ``0``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from embodiment import continuity

PACKAGE_ROOT = Path(continuity.__file__).resolve().parent

# ---------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------


def _module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])
    return names


def _all_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return names


def test_continuity_imports_stdlib_only() -> None:
    for name in _module_level_imports(Path(continuity.__file__)):
        assert name in sys.stdlib_module_names or name == "__future__", name


def test_continuity_module_has_no_lazy_third_party_import() -> None:
    for name in _all_imports(Path(continuity.__file__)):
        assert name in sys.stdlib_module_names or name == "__future__", name


def test_no_colleague_import_anywhere_in_continuity() -> None:
    assert "colleague" not in _all_imports(Path(continuity.__file__))


def test_continuity_never_uses_shell_true() -> None:
    """bandit-relevant: the adapter must never hand a shell string to subprocess.

    AST-based (not a raw substring scan): the module's own docstring legitimately
    talks *about* ``shell=True`` (explaining why it's never used), so a text
    search would false-positive on the prose. This checks only actual call-site
    keyword arguments.
    """
    tree = ast.parse(Path(continuity.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    is_true = isinstance(kw.value, ast.Constant) and kw.value.value is True
                    assert not is_true, "found shell=True at a subprocess call site"


def test_continuity_defines_no_similarity_or_embedding_helpers() -> None:
    """Guard the 'no store, no scoring, no embedding logic' acceptance criterion.

    Checks actual defined/referenced code identifiers (function names, local
    names, attribute accesses) via AST — not module docstring prose, which
    legitimately names these same words while explaining why eidetic/coherence
    (not this module) own them.
    """
    tree = ast.parse(Path(continuity.__file__).read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# fake CLI fixtures
# ---------------------------------------------------------------------------

_FAKE_EIDETIC = '''"""Hermetic stand-in for the eidetic CLI, used only by embodiment's tests."""
import json
import os
import sys
import time


def main() -> int:
    sleep_for = os.environ.get("EMBODIMENT_TEST_SLEEP")
    if sleep_for:
        time.sleep(float(sleep_for))
    argv = sys.argv[1:]
    capture = os.environ.get("EMBODIMENT_TEST_CAPTURE")
    if capture:
        with open(capture, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "argv": argv,
                    "cwd": os.getcwd(),
                    "EIDETIC_DATA_DIR": os.environ.get("EIDETIC_DATA_DIR"),
                },
                fh,
            )
    sub = argv[0] if argv else ""
    if sub == "remember":
        override = os.environ.get("EMBODIMENT_TEST_REMEMBER_JSON")
        if override is not None:
            sys.stdout.write(override)
        else:
            record_raw = argv[1] if len(argv) > 1 else "{}"
            try:
                record = json.loads(record_raw)
            except json.JSONDecodeError:
                record = {}
            sys.stdout.write(json.dumps({"upserted": 1, "ids": [record.get("id", "unknown")]}))
    elif sub == "recall":
        override = os.environ.get("EMBODIMENT_TEST_RECALL_JSON")
        sys.stdout.write(override if override is not None else "[]")
    else:
        sys.stdout.write("{}")
    return int(os.environ.get("EMBODIMENT_TEST_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
'''

_FAKE_COHERENCE = '''"""Hermetic stand-in for the coherence CLI, used only by embodiment's tests."""
import json
import os
import sys
import time


def main() -> int:
    sleep_for = os.environ.get("EMBODIMENT_TEST_SLEEP")
    if sleep_for:
        time.sleep(float(sleep_for))
    argv = sys.argv[1:]
    capture = os.environ.get("EMBODIMENT_TEST_CAPTURE")
    if capture:
        with open(capture, "w", encoding="utf-8") as fh:
            json.dump({"argv": argv, "cwd": os.getcwd()}, fh)
    override = os.environ.get("EMBODIMENT_TEST_ASSESS_JSON")
    if override is not None:
        sys.stdout.write(override)
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "domain": "assess",
                    "score_type": "multi_domain_report",
                    "scores": {},
                    "frame": None,
                    "diagnostics": [],
                    "artifact": argv[0] if argv else "",
                    "domains": {"quality": {}},
                    "unavailable": {},
                }
            )
        )
    return int(os.environ.get("EMBODIMENT_TEST_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _install_fake_cli(bin_dir: Path, name: str, script: str) -> Path:
    """Write *script* as an executable *name* under *bin_dir*.

    The shebang embeds ``sys.executable``'s ABSOLUTE path rather than
    ``#!/usr/bin/env python3`` — the tests below deliberately shrink ``PATH``
    down to just the fake-CLI directory (the "hermetic" guarantee: no ambient
    PATH entry can leak in), which would otherwise leave ``env`` unable to
    resolve ``python3`` at all.
    """
    target = bin_dir / name
    target.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return target


@pytest.fixture()
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put fake ``eidetic``/``coherence`` executables on PATH; nothing else."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_cli(bin_dir, "eidetic", _FAKE_EIDETIC)
    _install_fake_cli(bin_dir, "coherence", _FAKE_COHERENCE)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


@pytest.fixture()
def empty_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put PATH somewhere with neither CLI — the 'both missing' fixture."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _read_capture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# probe() / no-continuity mode
# ---------------------------------------------------------------------------


def test_probe_full_when_both_clis_present(fake_bin: Path) -> None:
    status = continuity.probe()
    assert status.eidetic_available is True
    assert status.coherence_available is True
    assert status.mode == continuity.FULL_CONTINUITY
    assert status.degradations == ()


def test_probe_records_no_continuity_when_both_absent(empty_bin: Path) -> None:
    status = continuity.probe()
    assert status.eidetic_available is False
    assert status.coherence_available is False
    assert status.mode == continuity.NO_CONTINUITY
    codes = {d.code for d in status.degradations}
    subsystems = {d.subsystem for d in status.degradations}
    assert codes == {continuity.CODE_CLI_NOT_FOUND}
    assert subsystems == {"eidetic", "coherence"}


def test_probe_partial_when_only_eidetic_present(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_cli(bin_dir, "eidetic", _FAKE_EIDETIC)
    monkeypatch.setenv("PATH", str(bin_dir))
    status = continuity.probe()
    assert status.mode == continuity.PARTIAL_CONTINUITY
    assert len(status.degradations) == 1
    assert status.degradations[0].subsystem == "coherence"


def test_continuity_status_to_dict_round_trips_mode(empty_bin: Path) -> None:
    status = continuity.probe()
    data = status.to_dict()
    assert data["mode"] == continuity.NO_CONTINUITY
    assert len(data["degradations"]) == 2


# ---------------------------------------------------------------------------
# remember() / recall() — trap #1: storage-anchor enforcement
# ---------------------------------------------------------------------------


def test_remember_without_data_dir_or_repo_path_degrades_without_shelling_out(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))

    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"})

    assert outcome.ok is False
    assert outcome.degradation is not None
    assert outcome.degradation.subsystem == "eidetic"
    assert outcome.degradation.stage == "remember"
    assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR
    # The fake CLI was never actually invoked — no capture file appeared.
    assert not capture.exists()


def test_recall_without_data_dir_or_repo_path_degrades_without_shelling_out(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))

    outcome = continuity.recall("hello")

    assert outcome.ok is False
    assert outcome.records == []
    assert outcome.degradation is not None
    assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR
    assert not capture.exists()


def test_remember_sets_eidetic_data_dir_when_data_dir_given(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))
    store = tmp_path / "store"

    outcome = continuity.remember({"id": "rec-1", "text": "hi", "type": "note"}, data_dir=store)

    assert outcome.ok is True
    assert outcome.record_id == "rec-1"
    captured = _read_capture(capture)
    assert captured["EIDETIC_DATA_DIR"] == str(store)


def test_remember_pins_explicit_cwd_when_only_repo_path_given(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 'explicit cwd' alternative to EIDETIC_DATA_DIR (trap #1, second form).

    Proves the subprocess cwd is the caller's repo_path — NOT the ambient
    process cwd (pytest's own cwd, which is the repo root and therefore
    provably different from this freshly created tmp_path directory).
    """
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))
    repo = tmp_path / "host-repo"
    repo.mkdir()
    assert str(repo) != os.getcwd()

    outcome = continuity.remember({"id": "rec-2", "text": "hi", "type": "note"}, repo_path=repo)

    assert outcome.ok is True
    captured = _read_capture(capture)
    assert captured["cwd"] == str(repo)
    assert captured["EIDETIC_DATA_DIR"] is None


def test_recall_sets_eidetic_data_dir_when_data_dir_given(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))
    monkeypatch.setenv(
        "EMBODIMENT_TEST_RECALL_JSON", json.dumps([{"id": "a", "text": "hi", "score": 0.9}])
    )
    store = tmp_path / "store"

    outcome = continuity.recall("hi", data_dir=store)

    assert outcome.ok is True
    assert outcome.records == [{"id": "a", "text": "hi", "score": 0.9}]
    captured = _read_capture(capture)
    assert captured["EIDETIC_DATA_DIR"] == str(store)


def test_recall_pins_explicit_cwd_when_only_repo_path_given(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))
    repo = tmp_path / "host-repo"
    repo.mkdir()

    outcome = continuity.recall("hi", repo_path=repo)

    assert outcome.ok is True
    captured = _read_capture(capture)
    assert captured["cwd"] == str(repo)
    assert captured["EIDETIC_DATA_DIR"] is None


def test_remember_env_override_never_shadows_data_dir_pin(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-supplied env override must not be able to clobber the safety pin."""
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))
    store = tmp_path / "store"

    outcome = continuity.remember(
        {"id": "rec-3", "text": "hi", "type": "note"},
        data_dir=store,
        env={"EIDETIC_DATA_DIR": "/somewhere/else"},
    )

    assert outcome.ok is True
    captured = _read_capture(capture)
    assert captured["EIDETIC_DATA_DIR"] == str(store)


# ---------------------------------------------------------------------------
# remember() / recall() — degradation ladder
# ---------------------------------------------------------------------------


def test_remember_degrades_when_cli_missing(empty_bin: Path, tmp_path: Path) -> None:
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_CLI_NOT_FOUND
    assert outcome.degradation.subsystem == "eidetic"
    assert outcome.degradation.stage == "remember"


def test_recall_degrades_when_cli_missing(empty_bin: Path, tmp_path: Path) -> None:
    outcome = continuity.recall("hi", data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_CLI_NOT_FOUND


def test_remember_degrades_on_nonzero_exit(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBODIMENT_TEST_EXIT", "2")
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_NONZERO_EXIT
    assert outcome.degradation.exit_code == 2


def test_recall_degrades_on_nonzero_exit(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBODIMENT_TEST_EXIT", "1")
    outcome = continuity.recall("hi", data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_NONZERO_EXIT
    assert outcome.degradation.exit_code == 1


def test_remember_degrades_on_malformed_json(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBODIMENT_TEST_REMEMBER_JSON", "not json")
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_MALFORMED_JSON


def test_recall_degrades_on_wrong_json_shape(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """eidetic recall --json emits a JSON *list*; an object is a malformed shape."""
    monkeypatch.setenv("EMBODIMENT_TEST_RECALL_JSON", json.dumps({"not": "a list"}))
    outcome = continuity.recall("hi", data_dir=tmp_path)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_MALFORMED_JSON


def test_remember_degrades_on_unserialisable_record(fake_bin: Path, tmp_path: Path) -> None:
    class NotJsonable:
        pass

    outcome = continuity.remember(
        {"id": "x", "text": "hi", "type": "note", "bad": NotJsonable()}, data_dir=tmp_path
    )
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_INVALID_RECORD


def test_remember_timeout_degrades(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBODIMENT_TEST_SLEEP", "2.0")
    outcome = continuity.remember(
        {"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path, timeout=0.2
    )
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_TIMEOUT


def test_recall_timeout_degrades(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMBODIMENT_TEST_SLEEP", "2.0")
    outcome = continuity.recall("hi", data_dir=tmp_path, timeout=0.2)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_TIMEOUT


def test_remember_launch_error_degrades_on_nonexistent_repo_path(
    fake_bin: Path, tmp_path: Path
) -> None:
    """A repo_path that does not exist makes subprocess.run raise OSError at
    launch time (not a timeout) — covers the non-timeout launch-error branch."""
    missing_repo = tmp_path / "does-not-exist"
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, repo_path=missing_repo)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_LAUNCH_ERROR
    assert outcome.degradation.subsystem == "eidetic"


# ---------------------------------------------------------------------------
# assess() — trap #2: unreachable embedder still exits 0
# ---------------------------------------------------------------------------


def test_assess_unreachable_embedder_records_degradation_despite_exit_zero(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE central acceptance criterion: exit 0 + an `unavailable` payload
    must still surface as a recorded degradation, never a silent pass."""
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    payload = {
        "domain": "assess",
        "score_type": "multi_domain_report",
        "scores": {},
        "frame": None,
        "diagnostics": [{"code": "domain_unavailable", "message": "meaning unavailable"}],
        "artifact": str(artifact),
        "domains": {"quality": {"score_type": "quality"}},
        "unavailable": {
            "meaning": {
                "code": "embed_endpoint_unreachable",
                "reason": "embedding endpoint unreachable",
            },
            "investiture": {"code": "embed_endpoint_unreachable", "reason": "derives from meaning"},
        },
    }
    monkeypatch.setenv("EMBODIMENT_TEST_ASSESS_JSON", json.dumps(payload))
    monkeypatch.setenv("EMBODIMENT_TEST_EXIT", "0")  # explicit: the process succeeds

    outcome = continuity.assess(artifact)

    # The CLI "succeeded" (exit 0) and produced a usable, partial report...
    assert outcome.ok is True
    assert outcome.unavailable  # non-empty
    assert "meaning" in outcome.unavailable
    # ...but reading ok/exit-code alone would hide the degradation. It must
    # be recorded regardless.
    assert outcome.degradation is not None
    assert outcome.degradation.subsystem == "coherence"
    assert outcome.degradation.stage == "assess"
    assert outcome.degradation.code == continuity.CODE_DOMAIN_UNAVAILABLE
    assert "meaning" in outcome.degradation.reason


def test_assess_fully_available_records_no_degradation(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    payload = {
        "domain": "assess",
        "score_type": "multi_domain_report",
        "scores": {},
        "frame": None,
        "diagnostics": [],
        "artifact": str(artifact),
        "domains": {"quality": {}, "meaning": {}, "investiture": {}},
        "unavailable": {},
    }
    monkeypatch.setenv("EMBODIMENT_TEST_ASSESS_JSON", json.dumps(payload))

    outcome = continuity.assess(artifact)

    assert outcome.ok is True
    assert outcome.unavailable == {}
    assert outcome.degradation is None
    assert set(outcome.domains) == {"quality", "meaning", "investiture"}


def test_assess_degrades_when_cli_missing(empty_bin: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    outcome = continuity.assess(artifact)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_CLI_NOT_FOUND
    assert outcome.degradation.subsystem == "coherence"


def test_assess_degrades_on_nonzero_exit(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """coherence's own contract reserves a non-zero exit for a genuine file I/O
    error — never for a domain-unavailable outcome (that stays exit 0)."""
    artifact = tmp_path / "missing.md"
    monkeypatch.setenv("EMBODIMENT_TEST_EXIT", "1")
    outcome = continuity.assess(artifact)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_NONZERO_EXIT
    assert outcome.degradation.exit_code == 1


def test_assess_timeout_degrades(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    monkeypatch.setenv("EMBODIMENT_TEST_SLEEP", "2.0")
    outcome = continuity.assess(artifact, timeout=0.2)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_TIMEOUT
    assert outcome.degradation.subsystem == "coherence"


def test_assess_degrades_on_malformed_json(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    monkeypatch.setenv("EMBODIMENT_TEST_ASSESS_JSON", "not json")
    outcome = continuity.assess(artifact)
    assert outcome.ok is False
    assert outcome.degradation.code == continuity.CODE_MALFORMED_JSON


# ---------------------------------------------------------------------------
# to_dict() shapes
# ---------------------------------------------------------------------------


def test_degradation_to_dict_omits_exit_code_when_none() -> None:
    d = continuity.Degradation(
        subsystem="eidetic", stage="remember", code=continuity.CODE_CLI_NOT_FOUND, reason="x"
    )
    assert "exit_code" not in d.to_dict()


def test_degradation_to_dict_includes_exit_code_when_set() -> None:
    d = continuity.Degradation(
        subsystem="eidetic",
        stage="remember",
        code=continuity.CODE_NONZERO_EXIT,
        reason="x",
        exit_code=1,
    )
    assert d.to_dict()["exit_code"] == 1


def test_remember_outcome_to_dict_json_roundtrips(fake_bin: Path, tmp_path: Path) -> None:
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)
    json.dumps(outcome.to_dict())  # must not raise


def test_remember_outcome_to_dict_includes_degradation_when_present(
    empty_bin: Path, tmp_path: Path
) -> None:
    outcome = continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)
    data = outcome.to_dict()
    assert data["degradation"]["code"] == continuity.CODE_CLI_NOT_FOUND
    json.dumps(data)  # must not raise


def test_recall_outcome_to_dict_json_roundtrips(fake_bin: Path, tmp_path: Path) -> None:
    outcome = continuity.recall("hi", data_dir=tmp_path)
    data = outcome.to_dict()
    assert "degradation" not in data
    json.dumps(data)  # must not raise


def test_recall_outcome_to_dict_includes_degradation_when_present(
    empty_bin: Path, tmp_path: Path
) -> None:
    outcome = continuity.recall("hi", data_dir=tmp_path)
    data = outcome.to_dict()
    assert data["degradation"]["code"] == continuity.CODE_CLI_NOT_FOUND
    json.dumps(data)  # must not raise


def test_assess_outcome_to_dict_json_roundtrips(fake_bin: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    outcome = continuity.assess(artifact)
    data = outcome.to_dict()
    assert "degradation" not in data
    json.dumps(data)  # must not raise


def test_assess_outcome_to_dict_includes_degradation_when_present(
    empty_bin: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "artifact.md"
    artifact.write_text("hello", encoding="utf-8")
    outcome = continuity.assess(artifact)
    data = outcome.to_dict()
    assert data["degradation"]["code"] == continuity.CODE_CLI_NOT_FOUND
    json.dumps(data)  # must not raise


# ---------------------------------------------------------------------------
# scope/visibility drift test — eidetic docs/contract.md §4
# ---------------------------------------------------------------------------

# Frozen at the time this module was written, copied from eidetic-cli's
# docs/contract.md machine-readable block (§ "Machine-readable summary").
# This is the literal this module's constants are pinned against even when no
# sibling eidetic-cli checkout is available to compare live (e.g. a bare CI
# checkout of embodiment alone) — see the module-scope test below for the
# opportunistic live comparison.
_FROZEN_CONTRACT_VERSION = "1"
_FROZEN_DEFAULT_VISIBILITY = "public"
_FROZEN_PRIVATE_REQUIRES_EXPLICIT_FLAG = "true"
_FROZEN_SCOPE_NAMING = "culture.yaml-suffix-per-repo"


def test_default_visibility_matches_frozen_contract_literal() -> None:
    assert continuity.DEFAULT_VISIBILITY == _FROZEN_DEFAULT_VISIBILITY


def test_private_requires_explicit_flag_matches_frozen_contract_literal() -> None:
    assert continuity.PRIVATE_REQUIRES_EXPLICIT_FLAG is True


def test_scope_naming_convention_matches_frozen_contract_literal() -> None:
    assert continuity.SCOPE_NAMING_CONVENTION == _FROZEN_SCOPE_NAMING


def test_default_scope_is_default_when_unspecified() -> None:
    """Mirrors eidetic's own --scope argparse default ('default')."""
    assert continuity.DEFAULT_SCOPE == "default"


def test_private_visibility_is_reachable_only_via_explicit_argument(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves PRIVATE_REQUIRES_EXPLICIT_FLAG is honoured by construction:
    a no-visibility-argument call ships '--visibility public' on argv."""
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("EMBODIMENT_TEST_CAPTURE", str(capture))

    continuity.remember({"id": "x", "text": "hi", "type": "note"}, data_dir=tmp_path)

    captured = _read_capture(capture)
    argv = captured["argv"]
    assert "--visibility" in argv
    assert argv[argv.index("--visibility") + 1] == "public"


def _find_sibling_contract_doc() -> Path | None:
    """Best-effort locate a sibling eidetic-cli checkout's docs/contract.md.

    Tries an explicit override env var first (CI or an unusual layout can set
    this), then a short list of relative candidates that cover both a normal
    sibling checkout (``../eidetic-cli``) and an ``assign-to-workforce``-style
    worktree one level deeper (``../../eidetic-cli`` — see this repo's own
    CLAUDE.md worktree convention, ``../.worktrees.embodiment/<name>/``).
    Returns ``None`` (never raises) when no candidate exists — the live-drift
    test below skips rather than fails in that case, exactly as eidetic's own
    docs/contract.md §4 anticipates for a cross-repo consumer.
    """
    override = os.environ.get("EMBODIMENT_TEST_EIDETIC_CONTRACT")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root.parent / "eidetic-cli" / "docs" / "contract.md",
        repo_root.parent.parent / "eidetic-cli" / "docs" / "contract.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_contract_block(text: str) -> dict[str, str]:
    match = re.search(r"```text\n(.*?)\n```", text, re.DOTALL)
    assert match is not None, "contract.md is missing its ```text machine-readable block"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_drift_against_live_eidetic_contract_doc_when_reachable() -> None:
    """Opportunistic live-drift check (eidetic docs/contract.md §4's suggested
    pattern): when a sibling eidetic-cli checkout is available (true in this
    multi-project workspace), parse ITS machine-readable block and assert this
    module's hardcoded constants still agree with it. Skips — never fails —
    when no sibling checkout is reachable, e.g. a standalone embodiment-only
    CI checkout; the frozen-literal tests above still guard that case."""
    contract_path = _find_sibling_contract_doc()
    if contract_path is None:
        pytest.skip(
            "no sibling eidetic-cli checkout found (set EMBODIMENT_TEST_EIDETIC_CONTRACT "
            "to point at its docs/contract.md to run this check)"
        )
    fields = _parse_contract_block(contract_path.read_text(encoding="utf-8"))
    assert fields.get("version") == _FROZEN_CONTRACT_VERSION
    assert fields.get("default_visibility") == continuity.DEFAULT_VISIBILITY
    assert (
        fields.get("private_requires_explicit_flag")
        == str(continuity.PRIVATE_REQUIRES_EXPLICIT_FLAG).lower()
    )
    assert fields.get("scope_naming") == continuity.SCOPE_NAMING_CONVENTION
