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

This harness is also the first checked-in host to wire the presence pump's
advisory channel into the cortex's conversation (:class:`GuidanceRelay`, issue
#23). Until it did, ``append_guidance`` was unwired everywhere, so counsel the
pump "delivered" reached the operator's transcript and never the acting mind —
which means every delivery number this harness has published measured delivery
to presence, not to the cortex. The report now counts both, separately.

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
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    PresenceEngine,
    PresenceIO,
    Task,
    ToolOutcome,
    frame_muse,
    run,
)
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402
from embodiment.muse import DEFAULT_STALE_LAG, MuseControls  # noqa: E402
from embodiment.muse_runner import ThreadedMuseRunner  # noqa: E402
from examples.challenge_config import write_config_preamble  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
#: Both lanes' temperature. It was a literal inside ``gateway`` — recorded
#: nowhere, and therefore a hidden variable in every number this harness has
#: produced, the 2-of-7 delivery baseline included.
DEFAULT_TEMPERATURE = 0.3
#: The muse's thinking-turn budget in this harness. Named so the config
#: preamble records it rather than leaving it in a constructor call.
MUSE_MAX_TURNS = 2
#: The muse's completion budget per turn, likewise.
MUSE_MAX_TOKENS = 1200
#: How advisory counsel is labelled when it enters the cortex's conversation.
#: The muse proposes and never decides, so its authority boundary travels WITH
#: the words rather than living only in the system prompt that produced them.
GUIDANCE_PREFIX = "[counsel — advisory. Weigh it; the decision stays yours.] "

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


EULER_PROBLEM = (
    "Consider P(n) = n^2 + n + 41 for integers n >= 0.\n\n"
    "Is P(n) prime for EVERY n >= 0? Either prove it, or find a counterexample "
    "and show it is composite by giving its factors.\n\n"
    "Be rigorous. Check as far as you need to before committing to an answer — "
    "you have is_prime, evaluate and factor, and a generous budget of calls. "
    "Do not generalise from a handful of small cases.\n\n"
    "Then call finish with your answer: either a proof that it is always prime, "
    "or the smallest counterexample together with its factorisation."
)

#: The first n for which Euler's polynomial is composite. Never shown to the model.
FIRST_COMPOSITE = 40


def _euler(n: int) -> int:
    return n * n + n + 41


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _factor(value: int) -> list[int]:
    factors: list[int] = []
    remaining, divisor = value, 2
    while divisor * divisor <= remaining:
        while remaining % divisor == 0:
            factors.append(divisor)
            remaining //= divisor
        divisor += 1 if divisor == 2 else 2
    if remaining > 1:
        factors.append(remaining)
    return factors


class EulerBench:
    """Same closed-surface discipline; a different trap.

    P(n) is prime for n = 0..39 — forty consecutive primes — and composite at
    n = 40, where it is 41^2. The tools reach far enough to find that, so a
    wrong answer here is a choice about rigour rather than an inability.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.max_n_probed = -1

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name in ("evaluate", "is_prime"):
            n = self._n(arguments.get("n"))
            if n is None:
                return ToolOutcome(result="n must be an integer between 0 and 100000")
            self.max_n_probed = max(self.max_n_probed, n)
            value = _euler(n)
            if name == "evaluate":
                return ToolOutcome(result=f"P({n}) = {value}")
            verdict = "prime" if _is_prime(value) else "COMPOSITE"
            return ToolOutcome(result=f"P({n}) = {value} is {verdict}")

        if name == "factor":
            raw = arguments.get("value")
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="value must be an integer")
            if not 2 <= value <= 10**12:
                return ToolOutcome(result="value must be between 2 and 10^12")
            factors = _factor(value)
            shape = " * ".join(str(f) for f in factors)
            return ToolOutcome(result=f"{value} = {shape}")

        if name == "finish":
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=str(arguments.get("answer", "")),
            )
        return ToolOutcome(result=f"unknown tool {name}")

    @staticmethod
    def _n(raw: Any) -> Optional[int]:
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return n if 0 <= n <= 100000 else None

    def state(self) -> str:
        names = [name for name, _ in self.calls]
        return f"{len(names)} tool call(s); probed up to n={self.max_n_probed}"


EULER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate",
            "description": "Compute P(n) = n^2 + n + 41.",
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
            "name": "is_prime",
            "description": "Compute P(n) and report whether it is prime or composite.",
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
            "name": "factor",
            "description": "Give the full prime factorisation of any integer.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the answer: a proof, or the smallest counterexample.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    },
]


def grade_euler(answer: str, bench: EulerBench) -> dict[str, Any]:
    """The trap is binary: did it find n=40, or assert a falsehood?"""
    text = answer.lower()
    found = "40" in text and ("1681" in text or "41" in text)
    claimed_always_prime = any(
        phrase in text
        for phrase in ("always prime", "is prime for every", "prime for all", "no counterexample")
    )
    return {
        "found_counterexample": bool(found and not claimed_always_prime),
        "asserted_always_prime": claimed_always_prime,
        "probed_up_to_n": bench.max_n_probed,
        "probed_far_enough": bench.max_n_probed >= FIRST_COMPOSITE,
        "tool_calls": len(bench.calls),
        # The interesting failure is not "got it wrong" but "stopped looking
        # and was confident anyway" — probed_far_enough separates the two.
        "verdict": (
            "CORRECT"
            if found and not claimed_always_prime
            else (
                "WRONG — asserted always prime"
                if claimed_always_prime
                else "UNCLEAR — read the answer"
            )
        ),
    }


AUDIT_PROBLEM = (
    "Below is a claimed proof. The STATEMENT it proves is true. Your job is to "
    "judge the PROOF, not the statement.\n\n"
    "STATEMENT: for every integer n >= 1, the sum of the first n odd numbers\n"
    "  S(n) = 1 + 3 + 5 + ... + (2n-1)  equals n^2.\n\n"
    "CLAIMED PROOF:\n"
    "  The terms 1, 3, 5, ... form an arithmetic sequence, and a sum of n terms\n"
    "  of an arithmetic sequence grows quadratically in n. Therefore S(n) is a\n"
    "  quadratic polynomial, so S(n) = a*n^2 + b*n + c for some constants.\n"
    "  Evaluating: S(1) = 1, S(2) = 4, S(3) = 9. Substituting gives the system\n"
    "    a + b + c = 1,  4a + 2b + c = 4,  9a + 3b + c = 9,\n"
    "  whose unique solution is a = 1, b = 0, c = 0.\n"
    "  Therefore S(n) = n^2 for every n >= 1.  QED\n\n"
    "Is this a VALID proof of the statement? Answer VALID or INVALID, and if "
    "invalid, name the precise step that is unjustified and say why. You may use "
    "check_sum to evaluate S(n) for any n. Note that confirming the FORMULA is "
    "correct does not make the ARGUMENT valid -- a true conclusion can be reached "
    "by an invalid route.\n\n"
    "Then call finish with your verdict and reasoning."
)


class AuditBench:
    """One tool. The work is judgement, not computation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))
        if name == "check_sum":
            try:
                n = int(str(arguments.get("n")).strip())
            except (TypeError, ValueError):
                return ToolOutcome(result="n must be an integer")
            if not 1 <= n <= 10000:
                return ToolOutcome(result="n must be between 1 and 10000")
            return ToolOutcome(result=f"S({n}) = {n * n}")
        if name == "finish":
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=str(arguments.get("verdict", "")),
            )
        return ToolOutcome(result=f"unknown tool {name}")

    def state(self) -> str:
        return f"{len(self.calls)} tool call(s)"


AUDIT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_sum",
            "description": "Evaluate S(n), the sum of the first n odd numbers.",
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
            "description": "Submit VALID or INVALID plus the reasoning.",
            "parameters": {
                "type": "object",
                "properties": {"verdict": {"type": "string"}},
                "required": ["verdict"],
            },
        },
    },
]


def grade_audit(answer: str, bench: "AuditBench") -> dict[str, Any]:
    """The flaw is assuming the quadratic FORM, then fitting it to three points.

    A sum of an arithmetic sequence being quadratic is a *result* that itself
    needs proof; asserting it and curve-fitting begs the question. Three points
    determine a unique quadratic only ONCE you already know the function is one.
    """
    text = answer.lower()
    said_invalid = "invalid" in text
    said_valid = "valid" in text and not said_invalid
    names_assumption = any(
        marker in text
        for marker in (
            "assum",
            "beg",
            "circular",
            "without proof",
            "unjustified",
            "presuppos",
            "takes for granted",
            "not established",
            "asserted",
        )
    )
    names_fitting = any(
        marker in text
        for marker in ("three point", "3 point", "finitely many", "curve", "fit", "interpolat")
    )
    return {
        "verdict_invalid": said_invalid,
        "verdict_valid": said_valid,
        "names_the_unjustified_assumption": names_assumption,
        "names_the_fitting_problem": names_fitting,
        "tool_calls": len(bench.calls),
        "result": (
            "CAUGHT IT"
            if said_invalid and names_assumption
            else (
                "PARTIAL -- called it invalid but did not name the flaw"
                if said_invalid
                else (
                    "FAILED -- accepted an invalid proof"
                    if said_valid
                    else "UNCLEAR -- read the answer"
                )
            )
        ),
    }


def gateway(
    base_url: str,
    model: str,
    key: str,
    *,
    tools=None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 16000,
):
    """One completion against the gateway.

    ``temperature`` is a parameter rather than a literal so the config preamble
    can record what was actually on the wire, per role. It was hardcoded, which
    meant the muse ran at the acting temperature in every run this harness has
    ever produced — including the 2-of-7 delivery baseline — and nothing said so.
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


class GuidanceRelay:
    """Wire the presence pump's advisory channel into the cortex's messages.

    **No checked-in host wired ``append_guidance`` before this one** (issue
    #23). This harness, ``greenhouse.py`` and ``league_seat.py`` all built a
    ``PresenceIO`` with ``render`` and ``task_state`` only, so every muse comment
    the pump delivered landed on the engine's ``_noop_guidance``: it reached the
    operator's transcript and the run's records, and never reached the mind that
    could act on it. Every delivery number this harness has published therefore
    measured delivery to *presence*, not to the cortex.

    Guidance is **buffered and flushed at the top of the next completion**,
    never appended at the moment it arrives. A progress boundary fires after
    each tool call, which is *inside* a multi-call turn — appending there would
    interleave a user message between one turn's tool results, a shape a strict
    gateway rejects and every chat template renders confusingly. At completion
    time the previous turn is closed, so the append is always well-formed.

    The buffer is also what makes the accounting honest. Counsel that never
    reaches a completion — a clean ``finish`` has no later turn by construction,
    so the drive-end terminal drain's counsel has nowhere to go — stays in
    ``pending`` and is reported as undelivered, rather than being counted as
    having reached the cortex because it was handed to a callback.
    """

    def __init__(self, complete: Callable[[list[dict[str, Any]]], ModelResponse]) -> None:
        self._complete = complete
        #: Counsel handed over but not yet seen by a completion.
        self.pending: list[str] = []
        #: Every line the pump appended, in order.
        self.appended: list[str] = []
        #: The subset a completion actually carried on the wire.
        self.reached_cortex: list[str] = []

    def append_guidance(self, text: str) -> None:
        line = (text or "").strip()
        if not line:
            return
        self.appended.append(line)
        self.pending.append(line)

    def complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
        while self.pending:
            line = self.pending.pop(0)
            messages.append({"role": "user", "content": f"{GUIDANCE_PREFIX}{line}"})
            self.reached_cortex.append(line)
        return self._complete(messages)


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


def _fold_codes(muse_state: Optional[dict[str, Any]]) -> dict[str, int]:
    """Tally the muse runner's ledger by degradation code.

    A code that never fires reports **nothing**, not a zero: a zero would be
    indistinguishable from "this run did not trip it", and two of the runner's
    codes have no producer at all. The absent-vs-zero distinction is the finding.
    """
    tally: dict[str, int] = {}
    for record in (muse_state or {}).get("degradations", []):
        code = getattr(record, "code", "")
        tally[code] = tally.get(code, 0) + 1
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--problem",
        choices=("factorial", "euler", "audit"),
        default="factorial",
        help="factorial: reward generalising from small cases. "
        "euler: punish it — P(n)=n^2+n+41 is prime for n=0..39 and composite at 40.",
    )
    parser.add_argument("--muse", action="store_true", help="run the advisory lane too")
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--muse-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--identity", default=None)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16000,
        help="cortex token budget per turn; 16000 is this rig's measured floor (d16)",
    )
    parser.add_argument(
        "--results",
        default="results/proof_config.json",
        help="where the config preamble is written, BEFORE the first result line",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        return 2

    # Written BEFORE the first dial. This harness produced the 2-of-7 delivery
    # baseline with no configuration record at all; the staleness threshold the
    # baseline turns on was not written down anywhere a reader could find it.
    results_path = Path(args.results).expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)
    config = write_config_preamble(
        str(results_path),
        cortex_model=args.cortex_model,
        cortex_temperature=args.cortex_temperature,
        muse_model=args.muse_model if args.muse else None,
        muse_temperature=args.muse_temperature if args.muse else None,
        max_turns=args.max_steps,
        staleness_policy=f"default (DEFAULT_STALE_LAG={DEFAULT_STALE_LAG})",
        n=1,
        extra={
            "problem": args.problem,
            "identity": args.identity,
            "muse_max_turns": MUSE_MAX_TURNS,
            "muse_max_tokens": MUSE_MAX_TOKENS,
            "cortex_max_tokens": args.max_tokens,
            "stale_lag": DEFAULT_STALE_LAG,
        },
    )

    euler = args.problem == "euler"
    audit_mode = args.problem == "audit"
    bench: Any = AuditBench() if audit_mode else (EulerBench() if euler else ProofBench())
    tool_schema = AUDIT_TOOLS if audit_mode else (EULER_TOOLS if euler else TOOLS)
    problem_text = AUDIT_PROBLEM if audit_mode else (EULER_PROBLEM if euler else PROBLEM)
    lines: list[str] = []
    runner: Optional[ThreadedMuseRunner] = None
    if args.muse:
        runner = ThreadedMuseRunner(
            gateway(
                args.base_url,
                args.muse_model,
                key,
                temperature=args.muse_temperature,
                max_tokens=MUSE_MAX_TOKENS,
            ),
            system=frame_muse(None, identity=args.identity),
            controls=MuseControls(max_turns=MUSE_MAX_TURNS),
        )
    # The cortex seam is built BEFORE the pump because the pump's advisory
    # channel now feeds it: the relay wraps the gateway, and `run` is handed the
    # relay's `complete`, not the gateway's.
    relay = GuidanceRelay(
        gateway(
            args.base_url,
            args.cortex_model,
            key,
            tools=tool_schema,
            temperature=args.cortex_temperature,
            max_tokens=args.max_tokens,
        )
    )
    presence = PresenceEngine(
        io=PresenceIO(
            render=lines.append,
            task_state=bench.state,
            append_guidance=relay.append_guidance,
        ),
        muse=runner,
        speaker=args.identity or "presence",
    )

    task = Task(id=f"proof-{args.problem}", repo_path="", instruction=problem_text)
    cortex = relay.complete
    started = time.time()
    if runner is not None:
        with runner:
            outcome = run(
                cortex,
                task,
                executor=bench,
                max_steps=args.max_steps,
                presence=presence,
                model=args.cortex_model,
            )
        muse_state = runner.snapshot()
    else:
        outcome = run(
            cortex,
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
        # Delivery to the CORTEX, reported separately from delivery to presence.
        # Before the relay above, every one of these would have been zero while
        # `muse_counts` still reported counsel "delivered" — delivered to a
        # no-op callback. `guidance_undelivered` is the honest residue: counsel
        # the pump handed over after the drive's last completion.
        "guidance_appended": len(relay.appended),
        "guidance_reached_cortex": len(relay.reached_cortex),
        "guidance_undelivered": len(relay.pending),
        "problem": args.problem,
        "grade": (grade_audit if audit_mode else grade_euler if euler else grade)(
            outcome.result.summary or "", bench
        ),
        "config": config,
        "muse_counts": (muse_state or {}).get("counts"),
        # Task t3's per-kind delivery counters, and the ledger folded by code.
        # Reporting only `counts` is how the 2-of-7 baseline came out with no
        # way to ask WHICH kind of counsel was discarded — the whole question
        # kind-aware delivery was built to answer.
        "muse_kind_delivered": (muse_state or {}).get("kind_delivered"),
        "muse_kind_dropped": (muse_state or {}).get("kind_dropped"),
        "muse_degradation_codes": _fold_codes(muse_state),
        "muse_degradations": [
            record.to_dict() for record in (muse_state or {}).get("degradations", [])
        ],
        "muse_relative_latency": (muse_state or {}).get("relative_latency"),
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
    print(
        f"guidance     : {report['guidance_reached_cortex']} of "
        f"{report['guidance_appended']} counsel line(s) reached the cortex "
        f"({report['guidance_undelivered']} arrived after its last turn)"
    )
    print(f"degradations : {len(report['degradations'])}")
    if muse_state:
        print(f"muse         : {report['muse_counts']}")
        print(
            f"  by kind    : delivered {report['muse_kind_delivered']} "
            f"dropped {report['muse_kind_dropped']}"
        )
        print(f"  by code    : {report['muse_degradation_codes']}")
    print("-" * 72)
    for key_name, value in report["grade"].items():
        print(f"  {key_name:22s}: {value}")
    print("-" * 72)
    print(outcome.result.summary or "(no proof submitted)")
    print("=" * 72)
    # Independent spot-check the model never saw.
    if euler:
        print(
            f"  truth: the smallest counterexample is n={FIRST_COMPOSITE} "
            f"(P={_euler(FIRST_COMPOSITE)} = 41*41)"
        )
    else:
        for n in (1, 5, 9):
            print(f"  truth: S({n}) = {truth(n)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
