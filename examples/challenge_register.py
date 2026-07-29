"""Corrupted Register challenge harness.

Problem: 4-bit register, 5 routines (A-E) execute once each in unknown order.
Recordings after the 1st, 3rd, and 5th routines each have Hamming distance
exactly 1 from the true state. Constraint: A executed before D.

Answer: initial 0101, order C -> B -> E -> A -> D.

Follows the pattern of examples/proof.py: truth(), grade(), Bench class,
TOOLS list, main() with --json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from itertools import permutations
from pathlib import Path
from typing import Any

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

ROUTINES = {
    "A": lambda s: (s + 3) % 16,
    "B": lambda s: s ^ 0b1011,
    "C": lambda s: ((s << 1) | (s >> 3)) & 0xF,
    "D": lambda s: (s * 5) % 16,
    "E": lambda s: int(bin(s)[2:].zfill(4)[::-1], 2),
}

RECORDINGS = [0b0010, 0b0000, 0b1111]

PROBLEM = (
    "A four-bit register begins in an unknown state. Five routines execute "
    "exactly once each, in an unknown order:\n\n"
    "- A: add 3, modulo 16\n"
    "- B: XOR with 1011\n"
    "- C: rotate left by one bit\n"
    "- D: multiply by 5, modulo 16\n"
    "- E: reverse the four bits\n\n"
    "The register was recorded after the first, third, and fifth routines:\n\n"
    "- After 1: 0010\n"
    "- After 3: 0000\n"
    "- After 5: 1111\n\n"
    "Exactly one bit is wrong in each recorded value (Hamming distance 1).\n\n"
    "One additional fact: A executed before D.\n\n"
    "Determine (1) the register's initial value and (2) the exact execution "
    "order.\n\n"
    "Format your answer as: initial=XXXX order=ABCDE\n\n"
    "Then call finish with your answer."
)


def _hamming(a: int, b: int) -> int:
    """Count differing bits between two 4-bit integers."""
    return bin(a ^ b).count("1")


def _format_bits(n: int) -> str:
    """Format a 4-bit integer as a binary string."""
    return format(n, "04b")


#: The verifiable truth. Never shown to the model.
def truth() -> tuple[str, list[str]]:
    """Exhaustive search over 16 initial states x 120 orderings."""
    routines = list(ROUTINES.keys())
    for initial in range(16):
        for order in permutations(routines):
            # Constraint: A before D
            if order.index("A") > order.index("D"):
                continue

            state = initial
            states_after: list[int] = []
            for routine in order:
                state = ROUTINES[routine](state)
                states_after.append(state)

            # Check Hamming distance exactly 1 for each recording
            ok = True
            for recorded, actual in zip(RECORDINGS, states_after[::2]):
                if _hamming(recorded, actual) != 1:
                    ok = False
                    break
            if ok:
                return (_format_bits(initial), list(order))

    raise RuntimeError("no solution found")


def grade(answer: str) -> dict[str, Any]:
    """Grade the answer string against the unique solution."""
    correct_bits, correct_order = truth()
    correct_order_str = "".join(correct_order)

    # Parse the answer: accept "initial=XXXX order=ABCDE" or similar
    parsed_bits = None
    parsed_order = None
    for token in answer.replace(",", " ").replace("=", " ").split():
        if token in ("initial", "order"):
            continue
        if len(token) == 4 and all(c in "01" for c in token):
            parsed_bits = token
        elif len(token) == 5 and all(c in "ABCDE" for c in token):
            parsed_order = token

    is_correct = parsed_bits == correct_bits and parsed_order == correct_order_str

    return {
        "answer": answer,
        "expected": f"initial={correct_bits} order={correct_order_str}",
        "is_correct": is_correct,
        "verdict": "CORRECT" if is_correct else "WRONG",
    }


class RegisterBench:
    """A closed surface for the corrupted register problem."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "apply_routine":
            routine = str(arguments.get("routine", "")).upper()
            try:
                state = int(str(arguments.get("state")).strip(), 2)
            except (TypeError, ValueError):
                return ToolOutcome(result="state must be a 4-bit binary string")
            if routine not in ROUTINES:
                return ToolOutcome(result=f"unknown routine {routine}")
            result = ROUTINES[routine](state)
            return ToolOutcome(result=f"{_format_bits(result)}")

        if name == "hamming_distance":
            try:
                a = int(str(arguments.get("a")).strip(), 2)
                b = int(str(arguments.get("b")).strip(), 2)
            except (TypeError, ValueError):
                return ToolOutcome(result="arguments must be 4-bit binary strings")
            return ToolOutcome(result=str(_hamming(a, b)))

        if name == "check_order":
            order = str(arguments.get("order", "")).upper()
            has_a_before_d = "A" in order and "D" in order and order.index("A") < order.index("D")
            return ToolOutcome(result=f"A before D: {'yes' if has_a_before_d else 'no'}")

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
            "name": "apply_routine",
            "description": ("Apply a routine (A-E) to a 4-bit state and return the result."),
            "parameters": {
                "type": "object",
                "properties": {
                    "routine": {
                        "type": "string",
                        "description": "Routine letter: A, B, C, D, or E.",
                    },
                    "state": {
                        "type": "string",
                        "description": "4-bit binary string, e.g. '0101'.",
                    },
                },
                "required": ["routine", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hamming_distance",
            "description": ("Compute the Hamming distance between two 4-bit states."),
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "string",
                        "description": "First 4-bit binary string.",
                    },
                    "b": {
                        "type": "string",
                        "description": "Second 4-bit binary string.",
                    },
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_order",
            "description": (
                "Check whether an execution order satisfies the A-before-D " "constraint."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order": {
                        "type": "string",
                        "description": "Execution order string, e.g. 'CBEAD'.",
                    },
                },
                "required": ["order"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": ("Submit the final answer in format 'initial=XXXX order=ABCDE'."),
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
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
    """Drive one attempt at the register problem and grade it.

    Split out of ``main`` so a hermetic test can drive the whole harness with a
    scripted seam. It was not testable before, and it did not work: ``main``
    built ``Task(system=..., tools=...)`` and called ``run(task=…, bench=…)``,
    and the contract has neither — so every invocation raised ``TypeError``
    before its first model call. A fresh bench per attempt, too.
    """
    bench = RegisterBench()
    task = Task(id="register", repo_path="", instruction=PROBLEM)
    outcome = run(complete, task, executor=bench, max_steps=max_steps)

    raw = (outcome.result.summary or "").strip()
    correct_bits, correct_order = truth()
    graded = (
        grade(raw)
        if raw
        else {
            "answer": "",
            "expected": f"initial={correct_bits} order={''.join(correct_order)}",
            "is_correct": False,
            "verdict": "NO ANSWER",
        }
    )
    graded["raw_summary"] = raw
    graded["exit_reason"] = outcome.exit_reason
    graded["model_turns"] = getattr(outcome.result.stats, "model_turns", None)
    graded["tool_calls"] = [name for name, _ in bench.calls]
    graded["degradations"] = [record.to_dict() for record in outcome.degradations]
    return graded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Corrupted Register challenge: find initial state and order."
    )
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--muse-temperature", type=float, default=None)
    parser.add_argument("--n", type=int, default=1, help="number of runs")
    parser.add_argument("--results", default="results/register_config.json")
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
