"""Tests for the ``embodiment memory`` noun (plan task t6): ingest, forget, overview.

Synthetic data only. The verbs enqueue into the spool and never touch the store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.explain.catalog import ENTRIES
from embodiment.ingest.spool import Spool


def _pocket(n: int = 0, rec: str = "r1") -> dict:
    return {
        "id": f"pocket:{rec}:fact:{n}",
        "text": f"synthetic fact number {n}",
        "type": "fact",
        "metadata": {
            "source": "pocket",
            "recording_id": rec,
            "title": "Synthetic title",
            "date": "2026-01-02",
        },
    }


def _write(path: Path, records: list) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _run(argv: list, capsys) -> tuple[int, str, str]:
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _inbox(state: Path) -> list:
    return sorted(Spool.for_state_dir(state).inbox.glob("*.json")) if state.exists() else []


def test_ingest_is_dry_run_without_apply(tmp_path, capsys) -> None:
    f = _write(tmp_path / "b.ndjson", [_pocket(0), _pocket(1)])
    state = tmp_path / "state"
    rc, out, _ = _run(
        ["memory", "ingest", "--file", str(f), "--state-dir", str(state), "--json"], capsys
    )
    assert rc == 0
    data = json.loads(out)
    assert data["applied"] is False
    assert data["records"] == 2
    assert data["source"] == "pocket"
    assert data["enqueue"] is None
    assert _inbox(state) == []
    assert "state" in data["daemon"]


def test_ingest_apply_enqueues_and_stamps(tmp_path, capsys) -> None:
    f = _write(tmp_path / "b.ndjson", [_pocket(0), _pocket(1)])
    state = tmp_path / "state"
    rc, out, _ = _run(
        ["memory", "ingest", "--file", str(f), "--apply", "--state-dir", str(state), "--json"],
        capsys,
    )
    assert rc == 0
    data = json.loads(out)
    assert data["applied"] is True
    assert data["enqueue"]["ok"] is True
    assert data["enqueue"]["batch_id"]
    files = _inbox(state)
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["kind"] == "ingest"
    assert len(body["records"]) == 2
    assert body["records"][0]["added_by"] == "culture-rules"
    assert body["records"][0]["visibility"] == "private"


def test_ingest_refusal_exits_1_with_error_and_hint(tmp_path, capsys) -> None:
    bad = _pocket(0)
    bad["metadata"]["source"] = "nope"
    f = _write(tmp_path / "b.ndjson", [_pocket(1), bad])
    state = tmp_path / "state"
    rc, out, err = _run(
        ["memory", "ingest", "--file", str(f), "--apply", "--state-dir", str(state)], capsys
    )
    assert rc == 1
    assert out == ""
    assert err.startswith("error: ")
    assert "hint: " in err
    assert "line 2" in err
    assert _inbox(state) == []


def test_ingest_refusal_json_goes_to_stderr(tmp_path, capsys) -> None:
    f = tmp_path / "b.ndjson"
    f.write_text("not json\n", encoding="utf-8")
    rc, out, err = _run(["memory", "ingest", "--file", str(f), "--json"], capsys)
    assert rc == 1
    assert out == ""
    assert json.loads(err)["remediation"]


def test_ingest_missing_file_is_env_error(tmp_path, capsys) -> None:
    rc, out, err = _run(["memory", "ingest", "--file", str(tmp_path / "absent")], capsys)
    assert rc == 2
    assert "hint: " in err


def test_ingest_requires_file_or_status(capsys) -> None:
    rc, out, err = _run(["memory", "ingest"], capsys)
    assert rc == 1
    assert "hint: " in err


def test_ingest_status_reports_spool_and_daemon_separately(tmp_path, capsys) -> None:
    state = tmp_path / "state"
    f = _write(tmp_path / "b.ndjson", [_pocket(0)])
    main(["memory", "ingest", "--file", str(f), "--apply", "--state-dir", str(state)])
    capsys.readouterr()
    rc, out, _ = _run(["memory", "ingest", "--status", "--state-dir", str(state), "--json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["spool"]["pending"] == 1
    assert set(data["spool"]) >= {"pending", "done", "failed", "oldest_pending_age_s"}
    assert data["daemon"]["state"] == "stopped"


def test_ingest_status_text(tmp_path, capsys) -> None:
    rc, out, _ = _run(["memory", "ingest", "--status", "--state-dir", str(tmp_path / "s")], capsys)
    assert rc == 0
    assert "pending" in out
    assert "daemon" in out


def test_forget_by_source_and_source_id(tmp_path, capsys) -> None:
    state = tmp_path / "state"
    rc, out, _ = _run(
        [
            "memory",
            "forget",
            "--source",
            "pocket",
            "--source-id",
            "r1",
            "--apply",
            "--state-dir",
            str(state),
            "--json",
        ],
        capsys,
    )
    assert rc == 0
    assert json.loads(out)["enqueue"]["ok"] is True
    body = json.loads(_inbox(state)[0].read_text(encoding="utf-8"))
    assert body == {"kind": "forget", "records": [{"source": "pocket", "source_id": "r1"}]}


def test_forget_by_id_and_dry_run(tmp_path, capsys) -> None:
    state = tmp_path / "state"
    rc, out, _ = _run(
        ["memory", "forget", "--id", "pocket:r1:fact:3", "--state-dir", str(state), "--json"],
        capsys,
    )
    assert rc == 0
    assert json.loads(out)["applied"] is False
    assert _inbox(state) == []
    rc, _, _ = _run(
        ["memory", "forget", "--id", "pocket:r1:fact:3", "--apply", "--state-dir", str(state)],
        capsys,
    )
    body = json.loads(_inbox(state)[0].read_text(encoding="utf-8"))
    assert body["records"] == [{"id": "pocket:r1:fact:3"}]


@pytest.mark.parametrize(
    "argv",
    [
        ["memory", "forget"],
        ["memory", "forget", "--source", "pocket"],
        ["memory", "forget", "--source-id", "r1"],
        ["memory", "forget", "--id", "pocket:r1:fact:1", "--source", "pocket", "--source-id", "r1"],
        ["memory", "forget", "--source", "bogus", "--source-id", "r1"],
        ["memory", "forget", "--id", "not-a-wire-id"],
    ],
)
def test_forget_bad_arguments_exit_1_with_hint(argv, capsys) -> None:
    rc, out, err = _run(argv, capsys)
    assert rc == 1
    assert out == ""
    assert "hint: " in err


def test_enqueue_failure_is_reported_not_raised(tmp_path, capsys) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    f = _write(tmp_path / "b.ndjson", [_pocket(0)])
    rc, out, err = _run(
        ["memory", "ingest", "--file", str(f), "--apply", "--state-dir", str(blocker / "s")],
        capsys,
    )
    assert rc == 2
    assert "hint: " in err
    assert "Traceback" not in err


def test_memory_overview(capsys) -> None:
    rc, out, _ = _run(["memory", "overview", "--json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["subject"] == "embodiment memory"
    text = json.dumps(data)
    assert "ingest" in text
    assert "forget" in text
    rc, out, _ = _run(["memory"], capsys)
    assert rc == 0
    assert "memory" in out


def test_global_overview_lists_memory(capsys) -> None:
    rc, out, _ = _run(["overview"], capsys)
    assert "memory" in out


def test_catalog_entries() -> None:
    for path in [("memory",), ("memory", "ingest"), ("memory", "forget"), ("memory", "overview")]:
        assert path in ENTRIES


# --- criterion 3: culture-rules' runner environment -------------------------


def test_runs_under_minimal_env_stdin_closed_temp_cwd(tmp_path) -> None:
    f = _write(tmp_path / "b.ndjson", [_pocket(0)])
    state = tmp_path / "state"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    base = [sys.executable, "-m", "embodiment"]

    def run(args):
        return subprocess.run(
            base + args,
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    ing = run(
        ["memory", "ingest", "--file", str(f), "--apply", "--state-dir", str(state), "--json"]
    )
    assert ing.returncode == 0, ing.stderr
    forg = run(
        [
            "memory",
            "forget",
            "--source",
            "pocket",
            "--source-id",
            "r1",
            "--apply",
            "--state-dir",
            str(state),
            "--json",
        ]
    )
    assert forg.returncode == 0, forg.stderr
    st = run(["memory", "ingest", "--status", "--state-dir", str(state), "--json"])
    assert st.returncode == 0, st.stderr
    assert json.loads(st.stdout)["spool"]["pending"] == 2
    assert len(_inbox(state)) == 2
