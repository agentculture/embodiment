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
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 16000,
    trace: Optional[list[dict[str, Any]]] = None,
):
    """One completion against the gateway.

    ``temperature`` is a parameter rather than a literal because ``main`` used
    to accept ``--cortex-temperature``, record it in the config preamble, and
    then send a hardcoded 0.3 — a recorded value that was not the value on the
    wire, which is precisely the hidden variable the preamble exists to prevent.

    ``trace``, when given, collects one raw record per completion — including
    ``finish_reason``, which this seam otherwise discards. A turn truncated by
    the token cap (``finish_reason == "length"``) and a model that simply
    stopped both arrive as empty content, and without the finish reason a
    reader cannot tell them apart; ``designed-problem.md`` read one as the
    other. Records append in call order, so the list is the raw transcript.
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
        choice = payload["choices"][0]
        message = choice["message"]
        calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in (message.get("tool_calls") or [])
        ]
        answer = ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
            tool_calls=calls,
        )
        if trace is not None:
            usage = payload.get("usage") or {}
            trace.append(
                {
                    "finish_reason": choice.get("finish_reason"),
                    "content": answer.content,
                    "reasoning": answer.reasoning,
                    "tool_calls": [call.to_dict() for call in calls],
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "max_tokens": max_tokens,
                }
            )
        return answer

    return complete


def run_once(
    complete: Any,
    *,
    variant: str = "3",
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """Drive one attempt at the entropic register problem and grade it.

    Split out of ``main`` so a hermetic test can drive the whole harness with a
    scripted seam. It was not testable before, and it did not work: ``main``
    built ``Task(system=..., tools=...)`` and called ``run(task=…, bench=…)``,
    and the contract has neither — so every invocation raised ``TypeError``
    before its first model call. A fresh bench per attempt, too.
    """
    bench = EntropicBench()
    problem = PROBLEM_3B if variant == "3b" else PROBLEM_3
    task = Task(id=f"entropic-{variant}", repo_path="", instruction=problem)
    outcome = run(complete, task, executor=bench, max_steps=max_steps)

    raw = (outcome.result.summary or "").strip()
    graded = grade_constrained(raw) if variant == "3b" else grade(raw)
    if not raw:
        graded = {"answer": "", "is_correct": False, "verdict": "NO ANSWER"}
    graded["variant"] = variant
    graded["raw_summary"] = raw
    graded["exit_reason"] = outcome.exit_reason
    graded["model_turns"] = getattr(outcome.result.stats, "model_turns", None)
    graded["tool_calls"] = [name for name, _ in bench.calls]
    graded["degradations"] = [record.to_dict() for record in outcome.degradations]
    return graded


def main() -> int:
    parser = argparse.ArgumentParser(description="Entropic Register challenge (problems 3 and 3b).")
    parser.add_argument(
        "--variant",
        choices=["3", "3b"],
        default="3",
        help="Problem variant (default: 3).",
    )
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--muse-temperature", type=float, default=None)
    parser.add_argument("--n", type=int, default=1, help="number of runs")
    parser.add_argument("--results", default=None)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16000,
        help="cortex token budget per turn; 16000 is this rig's measured floor (d16)",
    )
    parser.add_argument(
        "--trace-out",
        default=None,
        help="write the raw per-completion transcript (with finish_reason) here as JSON",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        return 1

    results_path = Path(args.results or f"results/entropic_config_{args.variant}.json").expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)
    config = write_config_preamble(
        str(results_path),
        cortex_model=args.cortex_model,
        cortex_temperature=args.cortex_temperature,
        muse_model=args.muse_model if args.muse else None,
        muse_temperature=args.muse_temperature,
        max_turns=args.max_steps,
        n=args.n,
        extra={"variant": args.variant, "max_tokens": args.max_tokens},
    )

    results: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    complete = gateway(
        args.base_url,
        args.cortex_model,
        key,
        tools=TOOLS,
        temperature=args.cortex_temperature,
        max_tokens=args.max_tokens,
        trace=trace,
    )

    def flush_trace() -> None:
        """Write the transcript so far — after every run, not once at the end.

        A transient ``HTTP 503`` from the gateway 41 minutes into a live series
        destroyed every turn already recorded, because the transcript was
        written only after the last run completed. A live series must survive a
        transient gateway failure holding the evidence it has already paid for.
        """
        if not args.trace_out:
            return
        trace_path = Path(args.trace_out).expanduser()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump({"config": config, "turns": trace, "results": results}, f, indent=2)
            f.write("\n")

    for i in range(args.n):
        seen = len(trace)
        try:
            g = run_once(complete, variant=args.variant, max_steps=args.max_steps)
        except Exception as exc:
            # A dead or overloaded gateway is an infrastructure event, and it is
            # DATA: recorded as its own run rather than killing the series and
            # discarding the runs that already succeeded.
            g = {
                "verdict": "ABORTED",
                "error": f"{type(exc).__name__}: {exc}",
                "answer": None,
                "is_correct": False,
            }
        for record in trace[seen:]:
            record["run"] = i
        # The verdict alone cannot separate "the model stopped" from "the token
        # cap cut it off", so every run carries its turns' finish reasons.
        g["finish_reasons"] = [record["finish_reason"] for record in trace[seen:]]
        results.append(g)
        flush_trace()
        if not args.json:
            print(f"Run {i + 1}: {g['verdict']}")

    if args.json:
        output = {"config": config, "results": results}
        print(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
