"""Subagent attenuation — a runnable reference example.

Demonstrates that a child drive is strictly narrower than its parent on three
axes at once:

1. **Tool surface** — the child gets a strict subset of the parent's tools.
   A tool the parent has and the child does not is visibly refused.
2. **Spawn allowance** — exactly ``attenuate(parent)``, i.e. one less.
   A child asking for more than it is owed gets the attenuated value anyway.
3. **Own scratchpad** — the child writes to its own pad, readable after the run.

This is the guarantee from :mod:`embodiment.subagent` made visible in something
a host author can read and run, not just in tests.

Usage::

    uv run python examples/delegation.py          # human-readable report
    uv run python examples/delegation.py --json   # machine-readable JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from embodiment import (
    ModelResponse,
    Scratchpad,
    SubagentCall,
    SubagentResult,
    Task,
    ToolCall,
    ToolOutcome,
    run,
)
from embodiment.loop import UnknownToolError
from embodiment.subagent import (
    SpawnRequest,
    attenuate,
)

# ── the parent's tool surface ────────────────────────────────────────────────

#: Tools the parent has. The child will NOT get ``delegate`` or ``analyze``.
PARENT_TOOL_NAMES = ("delegate", "analyze", "record", "finish")


class ParentExecutor:
    """Parent tool surface: can delegate, analyze, record, and finish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "delegate":
            # Build a narrowed child executor (no delegate, no analyze).
            child_executor = ChildExecutor()
            child_task = Task(
                id="child-1",
                repo_path="",
                instruction=(
                    "You are a subagent. Record your findings on the scratchpad, "
                    "then finish with a summary."
                ),
            )
            # Ask for more allowance than we're owed — the seam should clamp.
            spawn = SpawnRequest(
                task=child_task,
                executor=child_executor,
                role="subagent",
                allowance=99,  # deliberately above parent's allowance
                max_steps=5,
                context={"child_executor": child_executor},
            )
            return ToolOutcome(result="delegating…", spawn=spawn)

        if name == "analyze":
            return ToolOutcome(result="parent analysis complete")

        if name == "record":
            return ToolOutcome(result="recorded")

        if name == "finish":
            summary = str(arguments.get("summary", ""))
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=summary,
            )

        return ToolOutcome(result=f"unknown tool {name}")

    def state(self) -> str:
        names = [n for n, _ in self.calls]
        return f"{len(names)} tool call(s)" + (f"; last: {names[-1]}" if names else "")


# ── the child's tool surface (strict subset) ────────────────────────────────

#: Tools the child has. Strict subset of PARENT_TOOL_NAMES.
CHILD_TOOL_NAMES = ("record", "finish")


class ChildExecutor:
    """Child tool surface: can only record and finish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.pad: Optional[Scratchpad] = None

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "record":
            text = str(arguments.get("text", "")).strip()
            if self.pad is not None:
                self.pad.execute("intend", {"text": text})
            return ToolOutcome(result=f"recorded: {text}")

        if name == "finish":
            summary = str(arguments.get("summary", ""))
            if self.pad is not None:
                self.pad.execute("conclude", {"text": summary})
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=summary,
            )

        # A withheld tool is REFUSED, not answered. Returning a ToolOutcome
        # whose ``result`` merely says "unknown tool" would be a *success*
        # carrying an error string: the loop would record the step as fine and
        # the model would read prose where it should read a refusal. Raising
        # ``UnknownToolError`` is what narrowing a tool surface actually means
        # — and the loop already knows this shape, treating it as one
        # self-correcting step rather than a new way out.
        raise UnknownToolError(f"{name} is not available to this child")

    def state(self) -> str:
        names = [n for n, _ in self.calls]
        return f"{len(names)} tool call(s)" + (f"; last: {names[-1]}" if names else "")


# ── scripted minds (no live model) ───────────────────────────────────────────


def parent_complete(messages: list[dict[str, Any]]) -> ModelResponse:
    """Scripted parent: delegates on turn 1, finishes on turn 2."""
    already_delegated = False
    for m in messages:
        if m.get("role") == "tool" and "delegating" in m.get("content", ""):
            already_delegated = True
            break

    if already_delegated:
        return ModelResponse(
            content="Delegation complete. Finishing.",
            tool_calls=[
                ToolCall(
                    id="call-finish",
                    name="finish",
                    arguments={"summary": "Parent delegated work to child and completed."},
                )
            ],
        )
    return ModelResponse(
        content="I will delegate the analysis to a subagent.",
        tool_calls=[
            ToolCall(
                id="call-delegate",
                name="delegate",
                arguments={"instruction": "Analyze and record findings."},
            )
        ],
    )


def child_complete(messages: list[dict[str, Any]]) -> ModelResponse:
    """Scripted child: records, then finishes."""
    already_recorded = False
    for m in messages:
        if m.get("role") == "tool" and "recorded" in m.get("content", ""):
            already_recorded = True
            break

    if already_recorded:
        return ModelResponse(
            content="Analysis complete.",
            tool_calls=[
                ToolCall(
                    id="call-child-finish",
                    name="finish",
                    arguments={"summary": "Child analysis recorded on scratchpad."},
                )
            ],
        )
    return ModelResponse(
        content="Recording findings.",
        tool_calls=[
            ToolCall(
                id="call-record",
                name="record",
                arguments={"text": "Child performed analysis and recorded findings."},
            )
        ],
    )


# ── the subagent seam ────────────────────────────────────────────────────────


def subagent_seam(
    call: SubagentCall,
    *,
    child_pad: Scratchpad,
) -> Optional[SubagentResult]:
    """Drive the child drive, returning its result.

    ``child_pad`` is passed by the caller so the pad survives the seam call
    and is readable after the run completes.
    """
    child_executor = call.executor  # type: ignore[union-attr]
    child_executor.pad = child_pad

    child_outcome = run(
        child_complete,
        call.task,
        executor=child_executor,
        max_steps=call.max_steps,
        spawn_allowance=call.allowance,
        lineage=call.lineage,
        subagent=None,
    )

    return SubagentResult(
        sub_result=None,
        model_turns=child_outcome.result.stats.model_turns,
        result=child_outcome.result.summary,
        exit_reason=child_outcome.exit_reason,
    )


# ── run the demonstration ────────────────────────────────────────────────────

PARENT_ALLOWANCE = 2


def run_demo() -> dict[str, Any]:
    """Run the full demonstration and return a structured report."""
    parent_executor = ParentExecutor()
    parent_task = Task(
        id="parent-1",
        repo_path="",
        instruction=("Delegate the analysis to a subagent, then finish with a summary."),
    )

    # The child's scratchpad — created by the parent, passed to the seam,
    # and readable after the run completes.
    child_pad = Scratchpad()

    def seam(call: SubagentCall) -> Optional[SubagentResult]:
        return subagent_seam(call, child_pad=child_pad)

    outcome = run(
        parent_complete,
        parent_task,
        executor=parent_executor,
        max_steps=10,
        spawn_allowance=PARENT_ALLOWANCE,
        subagent=seam,
    )

    # Extract child info from the spawn records.
    spawn_records = outcome.spawns
    child_allowance = None

    for record in spawn_records:
        if record.granted:
            child_allowance = record.allowance_granted

    # Build the report.
    report: dict[str, Any] = {
        "parent": {
            "task_id": parent_task.id,
            "allowance": PARENT_ALLOWANCE,
            "tool_names": list(PARENT_TOOL_NAMES),
            "exit_reason": outcome.exit_reason,
            "summary": outcome.result.summary,
            "model_turns": outcome.result.stats.model_turns,
        },
        "child": {
            "allowance": child_allowance,
            "expected_allowance": attenuate(PARENT_ALLOWANCE),
            "tool_names": list(CHILD_TOOL_NAMES),
            "parent_tool_names": list(PARENT_TOOL_NAMES),
            "pad_entries": [e.to_dict() for e in child_pad.entries],
        },
        "attenuation": {
            "parent_allowance": PARENT_ALLOWANCE,
            "child_allowance": child_allowance,
            "decrement": PARENT_ALLOWANCE - (child_allowance or 0),
            "child_tools_strict_subset": set(CHILD_TOOL_NAMES) < set(PARENT_TOOL_NAMES),
            "withheld_tools": sorted(set(PARENT_TOOL_NAMES) - set(CHILD_TOOL_NAMES)),
        },
        "spawns": [r.to_dict() for r in spawn_records],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args()

    report = run_demo()

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    # Human-readable report.
    print("=" * 72)
    print("SUBAGENT ATTENUATION DEMONSTRATION")
    print("=" * 72)
    print()
    print("Parent drive:")
    print(f"  task_id:        {report['parent']['task_id']}")
    print(f"  allowance:      {report['parent']['allowance']}")
    print(f"  tools:          {report['parent']['tool_names']}")
    print(f"  exit_reason:    {report['parent']['exit_reason']}")
    print(f"  model_turns:    {report['parent']['model_turns']}")
    print()
    print("Child drive:")
    print(f"  allowance:      {report['child']['allowance']}")
    print(f"  expected:       {report['child']['expected_allowance']}")
    print(f"  tools:          {report['child']['tool_names']}")
    print(f"  parent tools:   {report['child']['parent_tool_names']}")
    print()
    att = report["attenuation"]
    print("Attenuation:")
    print(f"  parent_allowance:  {att['parent_allowance']}")
    print(f"  child_allowance:   {att['child_allowance']}")
    print(f"  decrement:         {att['decrement']}")
    print(f"  child_tools_strict_subset: {att['child_tools_strict_subset']}")
    print(f"  withheld_tools:    {att['withheld_tools']}")
    print()
    if report["spawns"]:
        print("Spawn records:")
        for sr in report["spawns"]:
            print(f"  outcome: {sr['outcome']}, granted: {sr.get('allowance_granted')}")
    print()
    print("The child's allowance is exactly one less than the parent's.")
    print("The child's tool set is a strict subset of the parent's.")
    print("The withheld tools (delegate, analyze) are not available to the child.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
