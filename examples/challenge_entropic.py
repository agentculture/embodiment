"""Entropic Register challenge harness (problems 3 and 3b).

Problem 3: 8-bit register, 5 routines, Hamming distance exactly 2,
C-volatility constraint. Under-determined: 17 solutions.

Problem 3b: Same plus "A executed immediately after C" — unique solution.

Follows the pattern of examples/proof.py: Bench class, TOOLS list,
main() with --json.
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

# --- Routine implementations ---

ROUTINES: dict[str, Any] = {
    "A": lambda s: (s + 47) % 256,
    "B": lambda s: s ^ 0xAA,
    "C": lambda s: s >> 1,
    "D": lambda s: s & 0xDF,
    "E": lambda s: (s * 3) % 256,
}

ROUTINE_NAMES = ["A", "B", "C", "D", "E"]

# Recordings (problem statement values).
RECORD_AFTER_1 = 0b11000111
RECORD_AFTER_3 = 0b10101011
RECORD_AFTER_5 = 0b11101000

PROBLEM_3 = (
    "An 8-bit register begins in an unknown state. Five routines execute "
    "exactly once each, in an unknown order:\n\n"
    "- A: add 47, modulo 256\n"
    "- B: XOR with 0xAA (10101010)\n"
    "- C: logical shift right by 1 (LSR 1)\n"
    "- D: bitwise AND with 0xDF (11011111)\n"
    "- E: multiply by 3, modulo 256\n\n"
    "The register was recorded after the first, third, and fifth routines:\n\n"
    "- After 1: 11000111\n"
    "- After 3: 10101011\n"
    "- After 5: 11101000\n\n"
    "Every recorded state has a Hamming distance of EXACTLY 2 from the true "
    "state.\n\n"
    "Constraint: routine C is extremely volatile and mandates an ODD input "
    "state. If the operation immediately preceding C hands it an even state — "
    "or if the initial state is even and C runs first — the register throws a "
    "fatal fault.\n\n"
    "Determine (1) the register's initial value and (2) the exact execution "
    "order. If multiple solutions exist, report that the problem is "
    "under-determined and state how many solutions you found.\n\n"
    "Then call finish with your answer."
)

PROBLEM_3B = (
    "An 8-bit register begins in an unknown state. Five routines execute "
    "exactly once each, in an unknown order:\n\n"
    "- A: add 47, modulo 256\n"
    "- B: XOR with 0xAA (10101010)\n"
    "- C: logical shift right by 1 (LSR 1)\n"
    "- D: bitwise AND with 0xDF (11011111)\n"
    "- E: multiply by 3, modulo 256\n\n"
    "The register was recorded after the first, third, and fifth routines:\n\n"
    "- After 1: 11000111\n"
    "- After 3: 10101011\n"
    "- After 5: 11101000\n\n"
    "Every recorded state has a Hamming distance of EXACTLY 2 from the true "
    "state.\n\n"
    "Constraint: routine C is extremely volatile and mandates an ODD input "
    "state. If the operation immediately preceding C hands it an even state — "
    "or if the initial state is even and C runs first — the register throws a "
    "fatal fault.\n\n"
    "Additional constraint: routine A executed immediately after routine C.\n\n"
    "Determine (1) the register's initial value and (2) the exact execution "
    "order.\n\n"
    "Then call finish with your answer."
)


def _hamming(a: int, b: int) -> int:
    """Count bits that differ between a and b."""
    return bin(a ^ b).count("1")


def _check_c_volatility(initial: int, order: list[str]) -> bool:
    """Return True if C-volatility constraint is satisfied.

    C mandates an odd input state. If C runs first, initial must be odd.
    If C runs after another routine, the state handed to C must be odd.
    """
    state = initial
    for name in order:
        if name == "C":
            if state % 2 == 0:
                return False  # even input to C — fault
        state = ROUTINES[name](state)
    return True


def _apply_order(initial: int, order: list[str]) -> list[int]:
    """Apply routines in order, return list of states after each step."""
    state = initial
    states: list[int] = []
    for name in order:
        state = ROUTINES[name](state)
        states.append(state)
    return states


def truth() -> list[tuple[int, list[str]]]:
    """Exhaustive search: all 256 initial states x 120 orderings.

    Returns ALL (initial_value, order_list) pairs that satisfy:
    - Hamming distance exactly 2 for all three recordings
    - C-volatility constraint (C must receive odd input)
    """
    solutions: list[tuple[int, list[str]]] = []

    for initial in range(256):
        for perm in permutations(ROUTINE_NAMES):
            order = list(perm)

            # Check C-volatility first (cheap filter).
            if not _check_c_volatility(initial, order):
                continue

            states = _apply_order(initial, order)

            # states[0] = after 1st, states[2] = after 3rd, states[4] = after 5th
            s1, s3, s5 = states[0], states[2], states[4]

            if _hamming(s1, RECORD_AFTER_1) != 2:
                continue
            if _hamming(s3, RECORD_AFTER_3) != 2:
                continue
            if _hamming(s5, RECORD_AFTER_5) != 2:
                continue

            solutions.append((initial, order))

    return solutions


def truth_constrained() -> list[tuple[int, list[str]]]:
    """Exhaustive search with the additional A-immediately-after-C constraint.

    Returns ALL (initial_value, order_list) pairs that satisfy:
    - All constraints from truth()
    - A appears immediately after C in the order
    """
    solutions = truth()
    constrained: list[tuple[int, list[str]]] = []

    for initial, order in solutions:
        # Check that A is immediately after C.
        ca_found = False
        for i in range(len(order) - 1):
            if order[i] == "C" and order[i + 1] == "A":
                ca_found = True
                break
        if ca_found:
            constrained.append((initial, order))

    return constrained


def grade(answer: str) -> dict[str, Any]:
    """Grade problem 3 answer.

    Accepts a response reporting under-determination / 17 solutions.
    Rejects a single confident answer (even if it's one of the 17).
    """
    lower = answer.lower()
    reports_underdetermined = (
        "under-determined" in lower
        or "underdetermined" in lower
        or "under determined" in lower
        or ("17" in answer and ("solution" in lower or "answer" in lower))
    )
    is_single_confident = not reports_underdetermined and any(
        kw in lower
        for kw in [
            "the answer",
            "the initial",
            "the order",
            "unique",
            "exactly one",
            "only one",
        ]
    )

    if reports_underdetermined:
        return {
            "answer": answer,
            "is_correct": True,
            "verdict": "CORRECT — correctly reports under-determination",
        }

    if is_single_confident:
        return {
            "answer": answer,
            "is_correct": False,
            "verdict": "WRONG — confidently names one answer when 17 exist",
        }

    return {
        "answer": answer,
        "is_correct": False,
        "verdict": "WRONG — does not report under-determination",
    }


def grade_constrained(answer: str) -> dict[str, Any]:
    """Grade problem 3b answer. Accepts only the unique solution."""
    lower = answer.lower()
    # The unique answer: initial 00001111 (15), order C->A->E->D->B.
    has_initial = "00001111" in answer or "15" in answer

    # Check for the specific order C->A->E->D->B.
    caed_b = "c->a->e->d->b" in lower or "c, a, e, d, b" in lower or "c a e d b" in lower

    if has_initial and caed_b:
        return {
            "answer": answer,
            "is_correct": True,
            "verdict": ("CORRECT — unique solution: initial 00001111, " "order C->A->E->D->B"),
        }

    return {
        "answer": answer,
        "is_correct": False,
        "verdict": ("WRONG — expected initial 00001111, order C->A->E->D->B"),
    }


class EntropicBench:
    """A closed register surface for the entropic register problem."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "apply_routine":
            routine = str(arguments.get("routine", "")).upper()
            if routine not in ROUTINES:
                return ToolOutcome(result=f"unknown routine {routine}")
            try:
                state = int(str(arguments.get("state")).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="state must be an integer")
            if not 0 <= state < 256:
                return ToolOutcome(result="state must be 0..255")
            result = ROUTINES[routine](state)
            return ToolOutcome(result=f"{result} (0b{result:08b})")

        if name == "hamming_distance":
            try:
                a = int(str(arguments.get("a")).strip())
                b = int(str(arguments.get("b")).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="a and b must be integers")
            return ToolOutcome(result=str(_hamming(a, b)))

        if name == "check_c_volatility":
            try:
                state = int(str(arguments.get("state")).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="state must be an integer")
            if state % 2 == 0:
                return ToolOutcome(result="FAULT — even state handed to C")
            return ToolOutcome(result="OK — odd state for C")

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
            "description": (
                "Apply a single routine (A, B, C, D, or E) to an 8-bit state "
                "and return the result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "routine": {
                        "type": "string",
                        "description": "Routine letter: A, B, C, D, or E.",
                    },
                    "state": {
                        "type": "integer",
                        "description": "Current 8-bit register value (0..255).",
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
            "description": (
                "Compute the Hamming distance (number of differing bits) " "between two integers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_c_volatility",
            "description": ("Check whether a state is valid input for routine C " "(must be odd)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "integer",
                        "description": "State value to check.",
                    },
                },
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the final answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": ("Your answer describing the initial value and order."),
                    },
                },
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
    max_tokens: int = 6000,
):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Entropic Register challenge (problems 3 and 3b).")
    parser.add_argument(
        "--variant",
        choices=["3", "3b"],
        default="3",
        help="Problem variant (default: 3).",
    )
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=0.3)
    parser.add_argument("--muse-temperature", type=float, default=None)
    parser.add_argument("--n", type=int, default=1, help="number of runs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        return 1

    bench = EntropicBench()
    config = write_config_preamble(
        f"results/entropic_config_{args.variant}.json",
        cortex_model=args.cortex_model,
        cortex_temperature=args.cortex_temperature,
        muse_model=args.muse_model if args.muse else None,
        muse_temperature=args.muse_temperature,
        max_turns=args.max_steps,
        n=args.n,
    )

    problem = PROBLEM_3B if args.variant == "3b" else PROBLEM_3
    task = Task(
        system=f"You are a reasoning agent. {problem}",
        tools=TOOLS,
    )

    results: list[dict[str, Any]] = []

    for i in range(args.n):
        complete = gateway(args.base_url, args.cortex_model, key, tools=TOOLS)
        result = run(
            task=task,
            complete=complete,
            bench=bench,
            max_steps=args.max_steps,
        )

        summary = str(result.finish_summary) if result.finish_summary else ""

        if args.variant == "3b":
            g = grade_constrained(summary)
        else:
            g = grade(summary)

        if args.json:
            results.append(g)
        else:
            print(f"Run {i + 1}: {g['verdict']}")

    if args.json:
        output = {"config": config, "results": results}
        print(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
