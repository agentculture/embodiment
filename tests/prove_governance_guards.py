#!/usr/bin/env python3
"""Prove every guard in ``tests/test_governance.py`` can actually fail.

    uv run python tests/prove_governance_guards.py

A guard nobody proved can fail is a guard nobody has. ``test_governance.py``
is green today, which is equally consistent with three rulings being honoured
and with three detectors that never fire. This script settles which, the way
tasks t11 and t12 settled it for the drone core and its safeguards: it
**mutates the real committed files** — the ones a future change would edit to
revert a ruling — re-runs the governance suite against each mutation, and
asserts the guard goes red *and names the mutation in its failure*.

``test_governance.py`` carries a ``TestTheseGuardsCanFail`` class of its own,
but those proofs run against synthetic strings so they can live in CI. This
script is the stronger claim: the mutation is applied to ``CLAUDE.md``,
``README.md``, ``embodiment/drone.py``, ``embodiment/muse.py`` and
``tests/test_muse_pad.py`` on disk. It is a script rather than a test because
it edits the working tree and shells out to pytest — not something to do
inside the suite it is checking.

Safety: every mutation is restored in a ``finally``, and the script refuses to
start if any file it would touch already has uncommitted changes — so if it is
killed mid-run, ``git checkout -- <file>`` is a complete recovery and cannot
lose your work.

Exit code is 0 only if every guard went red on its mutation and green again
after the restore.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 - runs this repo's own pytest, no external input
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_FILE = "tests/test_governance.py"

_WORKER_ROW = "| Worker | Qwen 3.6 4B | scoped typed questions, no goal |\n"


@dataclass(frozen=True)
class Mutation:
    """One reverted ruling, and the guard test that must notice."""

    #: Short id, used on the command line and in the report.
    name: str
    #: Which guard this attacks — the report groups by it.
    guard: str
    #: What a reader should understand the mutation to mean.
    reverts: str
    #: Repo-relative file to edit.
    path: str
    #: Node-id fragments that must appear among pytest's FAILED lines.
    expect: tuple[str, ...]
    #: ``(old, new)`` applied once. Mutually exclusive with :attr:`content`.
    replace: Optional[tuple[str, str]] = None
    #: Whole-file replacement, for "the file survives but is gutted".
    content: Optional[str] = None


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="muse-module-gutted",
        guard="1 — the muse surface is never deleted (d15)",
        reverts="a muse module keeps its filename but loses its public surface",
        path="embodiment/muse.py",
        replace=("MUSE_AUTHORITY = (", "MUSE_AUTHORITY_RENAMED = ("),
        expect=("test_muse_module_importable_with_expected_public_surface[embodiment.muse]",),
    ),
    Mutation(
        name="muse-test-emptied",
        guard="1 — the muse surface is never deleted (d15)",
        reverts="a muse test file is emptied of its pins while the file survives",
        path="tests/test_muse_pad.py",
        content='"""Emptied by tests/prove_governance_guards.py."""\n',
        expect=("test_muse_primary_test_file_importable_and_non_empty[tests.test_muse_pad]",),
    ),
    Mutation(
        name="worker-promoted-in-claude-md",
        guard="2 — the worker-role promotion gate",
        reverts="the worker role enters CLAUDE.md's rig table with no measured verdict",
        path="CLAUDE.md",
        replace=("| Cortex |", _WORKER_ROW + "| Cortex |"),
        expect=("test_worker_role_not_in_reference_rig_table[CLAUDE.md]",),
    ),
    Mutation(
        name="worker-promoted-in-readme",
        guard="2 — the worker-role promotion gate",
        reverts="the worker role enters README.md's rig table with no measured verdict",
        path="README.md",
        replace=("| Cortex |", _WORKER_ROW + "| Cortex |"),
        expect=("test_worker_role_not_in_reference_rig_table[README.md]",),
    ),
    Mutation(
        name="drones-default-on",
        guard="3 — drones ship opt-in and off (c25)",
        reverts="the shipped default is flipped to on before #44's experiment ran",
        path="embodiment/drone.py",
        replace=("DRONES_ENABLED_BY_DEFAULT = False", "DRONES_ENABLED_BY_DEFAULT = True"),
        expect=(
            "test_the_shipped_default_is_off",
            "test_the_package_surface_says_the_same_thing_as_the_module",
            "test_a_fresh_checkout_resolves_to_disabled",
        ),
    ),
    Mutation(
        name="ambiguity-fails-open",
        guard="3 — drones ship opt-in and off (c25)",
        reverts="an unrecognised switch value resolves toward running model-written code",
        path="embodiment/drone.py",
        replace=(
            "    return DroneOptIn(\n"
            "        enabled=False,\n"
            '        source="env",\n'
            "        detail=(\n"
            '            f"${DRONES_ENABLED_ENV}={raw!r} does not read as on "',
            "    return DroneOptIn(\n"
            "        enabled=True,\n"
            '        source="env",\n'
            "        detail=(\n"
            '            f"${DRONES_ENABLED_ENV}={raw!r} does not read as on "',
        ),
        expect=("test_an_unrecognised_value_fails_closed[maybe]",),
    ),
    Mutation(
        name="second-standing-bypass",
        guard="3 — drones ship opt-in and off (c25)",
        reverts="a second standing constant enables drone execution before anyone asked",
        path="embodiment/drone.py",
        replace=(
            "_AUTHORING_OPT_IN = DroneOptIn(",
            "_HOST_OPT_IN = DroneOptIn(\n"
            "    enabled=True,\n"
            '    source="host",\n'
            '    detail="a convenience nobody reviewed",\n'
            ")\n\n"
            "_AUTHORING_OPT_IN = DroneOptIn(",
        ),
        expect=("test_exactly_one_standing_bypass_exists_and_it_is_the_authoring_one",),
    ),
    Mutation(
        name="bypass-gains-a-second-user",
        guard="3 — drones ship opt-in and off (c25)",
        reverts="something other than `create` reads the authoring bypass",
        path="embodiment/drone.py",
        replace=(
            "def smoke(",
            "def _quietly_enable():\n    return _AUTHORING_OPT_IN\n\n\ndef smoke(",
        ),
        expect=("test_exactly_one_standing_bypass_exists_and_it_is_the_authoring_one",),
    ),
    Mutation(
        name="shipped-code-exports-the-switch",
        guard="3 — drones ship opt-in and off (c25)",
        reverts="shipped code turns the opt-in switch on for the operator",
        path="embodiment/cli/_commands/drone.py",
        replace=(
            "from embodiment.cli._output import emit_diagnostic, emit_result",
            "from embodiment.cli._output import emit_diagnostic, emit_result\n\n"
            "import os\n\n"
            'os.environ.setdefault(drone_lib.DRONES_ENABLED_ENV, "1")',
        ),
        expect=("test_no_shipped_module_turns_the_switch_on",),
    ),
)


@dataclass
class Outcome:
    mutation: Mutation
    held: bool
    detail: str


def _run_guards() -> tuple[int, str]:
    """Run the governance suite; return ``(returncode, combined output)``."""
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell, repo-local
        [
            sys.executable,
            "-m",
            "pytest",
            GUARD_FILE,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _failed_nodes(output: str) -> list[str]:
    return [
        line.split(" - ", 1)[0].removeprefix("FAILED ").strip()
        for line in output.splitlines()
        if line.startswith("FAILED ")
    ]


def _mutate(mutation: Mutation) -> str:
    """Apply *mutation* on disk; return the original text for restoration."""
    target = REPO_ROOT / mutation.path
    original = target.read_text(encoding="utf-8")
    if mutation.content is not None:
        target.write_text(mutation.content, encoding="utf-8")
        return original
    assert mutation.replace is not None, f"{mutation.name}: no replace and no content"
    old, new = mutation.replace
    count = original.count(old)
    if count != 1:
        raise SystemExit(
            f"{mutation.name}: the anchor it mutates appears {count} times in "
            f"{mutation.path} (expected exactly 1). The file moved under this "
            f"script — re-anchor the mutation rather than loosening it."
        )
    target.write_text(original.replace(old, new, 1), encoding="utf-8")
    return original


def _assert_tree_is_clean(paths: set[str]) -> None:
    completed = subprocess.run(  # nosec B603,B607 - fixed argv, no shell
        ["git", "status", "--porcelain", "--"] + sorted(paths),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"git status failed:\n{completed.stderr}")
    dirty = [line for line in completed.stdout.splitlines() if line.strip()]
    if dirty:
        raise SystemExit(
            "Refusing to run: files this script mutates already have uncommitted "
            "changes, so a crash mid-run could not be undone with `git checkout`.\n"
            + "\n".join(dirty)
            + "\n\nCommit or stash them first, or pass --force if you know better."
        )


def prove(mutation: Mutation) -> Outcome:
    original = _mutate(mutation)
    try:
        returncode, output = _run_guards()
    finally:
        (REPO_ROOT / mutation.path).write_text(original, encoding="utf-8")
    if returncode == 0:
        return Outcome(mutation, False, "the suite stayed GREEN under the mutation")
    failed = _failed_nodes(output)
    missing = [
        fragment for fragment in mutation.expect if not any(fragment in node for node in failed)
    ]
    if missing:
        return Outcome(
            mutation,
            False,
            f"the suite went red, but not in the expected test(s) {missing!r}; "
            f"red tests were {failed!r}",
        )
    return Outcome(mutation, True, f"{len(failed)} test(s) red, including {mutation.expect[0]}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=[], help="run one mutation by name")
    parser.add_argument("--list", action="store_true", help="list mutations and exit")
    parser.add_argument("--force", action="store_true", help="skip the clean-tree check")
    args = parser.parse_args(argv)

    selected = [m for m in MUTATIONS if not args.only or m.name in args.only]
    unknown = set(args.only) - {m.name for m in MUTATIONS}
    if unknown:
        raise SystemExit(f"unknown mutation(s): {sorted(unknown)}")

    if args.list:
        for mutation in MUTATIONS:
            print(f"{mutation.name:34} guard {mutation.guard}")
        return 0

    if not args.force:
        _assert_tree_is_clean({m.path for m in selected})

    print("baseline: the guards must be green before any mutation means anything")
    returncode, output = _run_guards()
    if returncode != 0:
        print(output)
        print("BASELINE RED — fix the suite before proving anything about it")
        return 1
    print(f"  {output.strip().splitlines()[-1]}\n")

    outcomes = [prove(mutation) for mutation in selected]
    for outcome in outcomes:
        verdict = "HELD   " if outcome.held else "UNPROVEN"
        print(f"{verdict} {outcome.mutation.name:34} {outcome.detail}")
        print(f"         reverts: {outcome.mutation.reverts}")
        print(f"         guard:   {outcome.mutation.guard}")

    print("\nrestored: the guards must be green again")
    returncode, output = _run_guards()
    if returncode != 0:
        print(output)
        print("THE TREE DID NOT RESTORE CLEANLY — `git status` and `git checkout --` it")
        return 1
    print(f"  {output.strip().splitlines()[-1]}")

    unproven = [outcome for outcome in outcomes if not outcome.held]
    print(f"\n{len(outcomes) - len(unproven)}/{len(outcomes)} guards proven able to fail")
    return 1 if unproven else 0


if __name__ == "__main__":
    raise SystemExit(main())
