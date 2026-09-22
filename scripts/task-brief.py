#!/usr/bin/env python3
"""Render one plan task's brief, VERBATIM from `devague plan waves --json`.

    scripts/task-brief.py t4            # markdown on stdout

Used twice per task: as the task agent's contract, and as the brief handed to
scripts/dual-review.sh. Nothing here paraphrases: the plan text is what the
operator confirmed, and a reworded brief silently drifts from it.
"""

import json
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: task-brief.py <task-id>", file=sys.stderr)
        return 1
    tid = sys.argv[1]
    raw = subprocess.run(["devague", "plan", "waves", "--json"], text=True, capture_output=True)
    if raw.returncode != 0:
        print(raw.stderr, file=sys.stderr)
        return 2
    payload = json.loads(raw.stdout)
    task = payload["tasks"].get(tid)
    if task is None:
        print(f"error: no task {tid} in plan {payload['plan']}", file=sys.stderr)
        return 1
    wave = next(i for i, w in enumerate(payload["waves"]) if tid in w)
    print(f"**Task `{tid}`** (plan `{payload['plan']}`, wave {wave}): {task['summary']}\n")
    print("**Instruction (verbatim):**\n")
    print(task["instruction"] or "_none recorded - do not invent one_")
    print("\n**Acceptance criteria (verbatim; each must be proved by a test):**\n")
    for i, crit in enumerate(task["acceptance_criteria"], 1):
        print(f"{i}. {crit}")
    print(f"\n**Spec targets this task covers:** {', '.join(task['covers']) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
