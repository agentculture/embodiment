"""A long-running task: conjecture and prove a closed form.

The greenhouse demo is three tool calls. This is the other end — a task that
cannot be answered in one turn, to see what the loop, the budget, the presence
cadence and the muse actually do under sustained load.

The problem::

    Find a closed form for  S(n) = 1·1! + 2·2! + ... + n·n!,  and prove it.

Chosen because it has three genuinely different phases: *compute* small cases
(tool use), *conjecture* the pattern from them (inductive leap), then *prove* it
(deductive work no tool can do). The answer is ``(n+1)! - 1``, so correctness is
checkable rather than a matter of taste — which is the point. A "hard task" whose
output only a human can grade tells you nothing you can put in a test.

Tools are deliberately a closed surface — ``sum_terms``, ``factorial``,
``compare`` — and never an evaluator. Handing a model ``eval`` would make this a
test of sandboxing, not of reasoning, and embodiment's whole tool posture is that
the host decides what may be done (c35: not every host even has a shell).

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/proof.py                # cortex alone
    uv run python examples/proof.py --muse         # with the advisory lane
    uv run python examples/proof.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    MuseControls,
    PresenceEngine,
    PresenceIO,
    Task,
    ThreadedMuseRunner,
    ToolOutcome,
    frame_muse,
    run,
)
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"

PROBLEM = (
    "Find a closed form for the sum S(n) = 1*1! + 2*2! + 3*3! + ... + n*n!, "
    "and prove your answer is correct for all n >= 1.\n\n"
    "Work in three phases and do not skip any:\n"
    "1. COMPUTE. Use sum_terms to get S(n) for several small n, and factorial "
    "to look at nearby factorials. Gather evidence before guessing.\n"
    "2. CONJECTURE. State a closed form, then use compare to test it against "
    "S(n) for at least three values of n you have not already checked.\n"
    "3. PROVE. Give a real proof for all n >= 1 — induction is fine. A proof is "
    "not a table of values; the tools cannot do this part for you.\n\n"
    "Then call finish with the closed form and the full proof."
)


#: The verifiable truth. Never shown to the model.
def truth(n: int) -> int:
    return math.factorial(n + 1) - 1


class ProofBench:
    """A closed arithmetic surface. No evaluator, by design."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.compared: list[tuple[int, bool]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "sum_terms":
            n = self._n(arguments.get("n"))
            if n is None:
                return ToolOutcome(result="n must be an integer between 1 and 200")
            total = sum(k * math.factorial(k) for k in range(1, n + 1))
            return ToolOutcome(result=f"S({n}) = {total}")

        if name == "factorial":
            n = self._n(arguments.get("n"))
            if n is None:
                return ToolOutcome(result="n must be an integer between 1 and 200")
            return ToolOutcome(result=f"{n}! = {math.factorial(n)}")

        if name == "compare":
            n = self._n(arguments.get("n"))
            claimed = arguments.get("value")
            if n is None:
                return ToolOutcome(result="n must be an integer between 1 and 200")
            try:
                claimed_int = int(str(claimed).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="value must be an integer")
            actual = sum(k * math.factorial(k) for k in range(1, n + 1))
            agrees = claimed_int == actual
            self.compared.append((n, agrees))
            verdict = "MATCHES" if agrees else f"DIFFERS (actual S({n}) = {actual})"
            return ToolOutcome(result=f"your value {claimed_int} for n={n}: {verdict}")

        if name == "finish":
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=str(arguments.get("proof", "")),
            )
        return ToolOutcome(result=f"unknown tool {name}")

    @staticmethod
    def _n(raw: Any) -> Optional[int]:
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return n if 1 <= n <= 200 else None

    def state(self) -> str:
        names = [name for name, _ in self.calls]
        return f"{len(names)} tool call(s)" + (f"; last: {names[-1]}" if names else "")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sum_terms",
            "description": "Compute S(n) = 1*1! + 2*2! + ... + n*n! exactly.",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factorial",
            "description": "Compute n! exactly.",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare",
            "description": (
                "Check a value you computed from your closed form against the true S(n)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "value": {"type": "integer"}},
                "required": ["n", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the closed form and the full proof.",
            "parameters": {
                "type": "object",
                "properties": {"proof": {"type": "string"}},
                "required": ["proof"],
            },
        },
    },
]


def gateway(base_url: str, model: str, key: str, *, tools=None, max_tokens: int = 6000):
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:  # nosec B310
            payload = json.load(response)
        message = payload["choices"][0]["message"]
        calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in (message.get("tool_calls") or [])
        ]
        return ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
            tool_calls=calls,
        )

    return complete


def grade(proof: str, bench: ProofBench) -> dict[str, Any]:
    """Grade what can be graded mechanically, and say what cannot."""
    text = proof.lower().replace(" ", "")
    stated = any(
        marker in text for marker in ("(n+1)!-1", "(n+1)!−1", "(n+1)!+(-1)", "factorial(n+1)-1")
    )
    checks = [n for n, agreed in bench.compared if agreed]
    return {
        "closed_form_correct": stated,
        "compare_calls": len(bench.compared),
        "compare_agreed": len(checks),
        "compare_disagreed": len(bench.compared) - len(checks),
        "mentions_induction": "induct" in text,
        "phases_used": sorted({name for name, _ in bench.calls}),
        # Deliberately not graded here: whether the induction step is VALID.
        # A regex cannot judge a proof, and pretending otherwise would be worse
        # than saying so.
        "proof_validity": "not machine-graded — read it",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--identity", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        return 2

    bench = ProofBench()
    lines: list[str] = []
    runner: Optional[ThreadedMuseRunner] = None
    if args.muse:
        runner = ThreadedMuseRunner(
            gateway(args.base_url, args.muse_model, key, max_tokens=1200),
            system=frame_muse(None, identity=args.identity),
            controls=MuseControls(max_turns=2),
        )
    presence = PresenceEngine(
        io=PresenceIO(render=lines.append, task_state=bench.state),
        muse=runner,
        speaker=args.identity or "presence",
    )

    task = Task(id="proof-1", repo_path="", instruction=PROBLEM)
    started = time.time()
    if runner is not None:
        with runner:
            outcome = run(
                gateway(args.base_url, args.cortex_model, key, tools=TOOLS),
                task,
                executor=bench,
                max_steps=args.max_steps,
                presence=presence,
                model=args.cortex_model,
            )
        muse_state = runner.snapshot()
    else:
        outcome = run(
            gateway(args.base_url, args.cortex_model, key, tools=TOOLS),
            task,
            executor=bench,
            max_steps=args.max_steps,
            presence=presence,
            model=args.cortex_model,
        )
        muse_state = None
    elapsed = time.time() - started

    stats = outcome.result.stats
    report = {
        "muse": bool(args.muse),
        "elapsed_seconds": round(elapsed, 1),
        "exit_reason": outcome.exit_reason,
        "status": outcome.result.status,
        # `model_turns` is what `max_steps` actually bounds. `steps` counts TOOL
        # CALLS — the loop appends one Step per call, and a single turn may
        # carry several. Reporting only `steps` invites reading "steps: 14" of
        # "max_steps: 14" as a budget hit when the two count different things;
        # a muse run made 24 tool calls under the same 14-turn budget.
        "model_turns": getattr(stats, "model_turns", None),
        "max_steps": args.max_steps,
        "tool_call_count": len(outcome.result.steps),
        "tool_calls": [name for name, _ in bench.calls],
        "degradations": [record.to_dict() for record in outcome.degradations],
        "presence_lines": len(lines),
        "grade": grade(outcome.result.summary or "", bench),
        "muse_counts": (muse_state or {}).get("counts"),
    }

    if args.json:
        report["proof"] = outcome.result.summary
        print(json.dumps(report, indent=2, default=str))
        return 0

    print("=" * 72)
    print(f"PROOF TASK — {'cortex + muse' if args.muse else 'cortex alone'}")
    print("=" * 72)
    print(f"exit         : {report['exit_reason']} ({report['status']})")
    print(f"model turns  : {report['model_turns']}/{report['max_steps']}  (this is the budget)")
    print(f"tool calls   : {report['tool_call_count']}   in {elapsed:.0f}s")
    print(f"             : {report['tool_calls']}")
    print(f"presence     : {report['presence_lines']} line(s)")
    print(f"degradations : {len(report['degradations'])}")
    if muse_state:
        print(f"muse         : {report['muse_counts']}")
    print("-" * 72)
    for key_name, value in report["grade"].items():
        print(f"  {key_name:22s}: {value}")
    print("-" * 72)
    print(outcome.result.summary or "(no proof submitted)")
    print("=" * 72)
    # Independent spot-check the model never saw.
    for n in (1, 5, 9):
        print(f"  truth: S({n}) = {truth(n)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
