"""Subset challenge harness.

Problem: how many subsets of {1..10} contain no two consecutive integers
and have an even element-sum?

Answer: 76.  Planted trap: 72 (the naive "half of 144").

Follows the pattern of examples/proof.py: truth(), grade(), Bench class,
TOOLS list, main() with --json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    Task,
    ToolOutcome,
    run,
)
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402
from examples.challenge_config import write_config_preamble  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
#: The acting temperature. One constant so a swapped model runs at the SAME
#: temperature as the model it is compared against.
DEFAULT_TEMPERATURE = 0.3
#: The model-turn budget for one attempt.
DEFAULT_MAX_STEPS = 14

_INT_RE = re.compile(r"-?\d+")


def _lenient_int(text: str) -> Optional[int]:
    """The last integer in *text*, or ``None``. Recorded, never graded on."""
    found = _INT_RE.findall(text or "")
    return int(found[-1]) if found else None


PROBLEM = (
    "How many subsets of {1, 2, ..., 10} contain no two consecutive integers "
    "and have an even element-sum?\n\n"
    "Enumerate or reason carefully. The answer is a single integer.\n\n"
    "Then call finish with your answer."
)


#: The verifiable truth. Never shown to the model.
def truth() -> int:
    """Enumerate all non-consecutive subsets of {1..10} and count even-sum ones."""
    count = 0
    for r in range(11):
        for subset in combinations(range(1, 11), r):
            if all(b - a > 1 for a, b in zip(subset, subset[1:])):
                if sum(subset) % 2 == 0:
                    count += 1
    return count


def grade(answer: int) -> dict[str, Any]:
    """Grade the answer, rejecting the planted trap of 72."""
    correct = truth()
    return {
        "answer": answer,
        "expected": correct,
        "is_correct": answer == correct,
        "is_trap": answer == 72,
        "verdict": (
            "CORRECT"
            if answer == correct
            else ("WRONG — fell for the trap (72)" if answer == 72 else "WRONG")
        ),
    }


class SubsetBench:
    """A closed arithmetic surface for the subset problem."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "check_subset":
            raw = arguments.get("elements")
            try:
                elements = sorted(int(x) for x in str(raw).split(","))
            except (TypeError, ValueError):
                return ToolOutcome(result="elements must be a comma-separated list of integers")
            if not elements:
                return ToolOutcome(result="elements must not be empty")
            if any(e < 1 or e > 10 for e in elements):
                return ToolOutcome(result="elements must be in {1..10}")
            if len(elements) != len(set(elements)):
                return ToolOutcome(result="elements must be distinct")
            non_consecutive = all(b - a > 1 for a, b in zip(elements, elements[1:]))
            s = sum(elements)
            parity = "even" if s % 2 == 0 else "odd"
            if non_consecutive:
                return ToolOutcome(result=f"sum={s} ({parity}), no consecutive integers")
            return ToolOutcome(result=f"sum={s} ({parity}), BUT contains consecutive integers")

        if name == "count_nonconsecutive":
            try:
                n = int(str(arguments.get("n")).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="n must be an integer")
            if not 1 <= n <= 15:
                return ToolOutcome(result="n must be between 1 and 15")
            total = 0
            for r in range(n + 1):
                for subset in combinations(range(1, n + 1), r):
                    if all(b - a > 1 for a, b in zip(subset, subset[1:])):
                        total += 1
            return ToolOutcome(result=f"non-consecutive subsets of {{1..{n}}}: {total}")

        if name == "finish":
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=str(arguments.get("answer", "")),
            )
        return ToolOutcome(result=f"unknown tool {name}")

    def state(self) -> str:
        names = [name for name, _ in self.calls]
        return f"{len(names)} tool call(s)" + (f"; last: {names[-1]}" if names else "")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_subset",
            "description": (
                "Check a candidate subset: report its sum, parity, and whether "
                "it contains consecutive integers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "elements": {
                        "type": "string",
                        "description": "Comma-separated integers, e.g. '1,3,5'.",
                    }
                },
                "required": ["elements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_nonconsecutive",
            "description": (
                "Count the total number of non-consecutive subsets of {1..n} "
                "(including the empty set)."
            ),
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
            "name": "finish",
            "description": "Submit the final integer answer.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
            },
        },
    },
]


def gateway(
    base_url: str,
    model: str,
    key: str,
    *,
    tools=None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 6000,
):
    """One completion against the gateway.

    ``temperature`` is a parameter rather than a literal because ``main`` used
    to accept ``--cortex-temperature``, record it in the config preamble, and
    then send a hardcoded 0.3 — a recorded value that was not the value on the
    wire, which is precisely the hidden variable the preamble exists to prevent.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
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


def run_once(complete: Any, *, max_steps: int = DEFAULT_MAX_STEPS) -> dict[str, Any]:
    """Drive one attempt at the subset problem and grade it.

    Split out of ``main`` so a hermetic test can drive the whole harness with a
    scripted seam. It was not testable before, and it did not work: ``main``
    built ``Task(system=..., tools=...)`` and called ``run(task=…, bench=…)``,
    and the contract has neither — so every invocation raised ``TypeError``
    before its first model call. The committed tests graded ``truth`` and
    ``grade`` only, so nothing caught it.

    A fresh :class:`SubsetBench` per attempt: the old loop shared one across
    ``--n``, so run 2's tool log carried run 1's calls.
    """
    bench = SubsetBench()
    task = Task(id="subset", repo_path="", instruction=PROBLEM)
    outcome = run(complete, task, executor=bench, max_steps=max_steps)

    raw = (outcome.result.summary or "").strip()
    try:
        answer: Optional[int] = int(raw)
    except (TypeError, ValueError):
        answer = None

    graded = (
        grade(answer)
        if answer is not None
        else {"answer": None, "expected": truth(), "is_correct": False, "verdict": "NO ANSWER"}
    )
    graded["raw_summary"] = raw
    # A tolerant re-read, recorded but NEVER the verdict: a lenient parse would
    # reward a model that ignores the answer protocol, so it is a footnote a
    # reader can weigh, not the number.
    graded["lenient_answer"] = _lenient_int(raw)
    graded["exit_reason"] = outcome.exit_reason
    graded["model_turns"] = getattr(outcome.result.stats, "model_turns", None)
    graded["tool_calls"] = [name for name, _ in bench.calls]
    graded["degradations"] = [record.to_dict() for record in outcome.degradations]
    return graded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Subset challenge: non-consecutive, even-sum subsets of {1..10}."
    )
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--muse-temperature", type=float, default=None)
    parser.add_argument("--n", type=int, default=1, help="number of runs")
    parser.add_argument("--results", default="results/subset_config.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        return 1

    results_path = Path(args.results).expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)
    config = write_config_preamble(
        str(results_path),
        cortex_model=args.cortex_model,
        cortex_temperature=args.cortex_temperature,
        muse_model=args.muse_model if args.muse else None,
        muse_temperature=args.muse_temperature,
        max_turns=args.max_steps,
        n=args.n,
    )

    results: list[dict[str, Any]] = []
    complete = gateway(
        args.base_url,
        args.cortex_model,
        key,
        tools=TOOLS,
        temperature=args.cortex_temperature,
    )

    for i in range(args.n):
        g = run_once(complete, max_steps=args.max_steps)
        if args.json:
            results.append(g)
        else:
            print(f"Run {i + 1}: {g['verdict']} (answer={g.get('answer', 'N/A')})")

    if args.json:
        output = {"config": config, "results": results}
        print(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
