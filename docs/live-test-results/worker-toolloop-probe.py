#!/usr/bin/env python3
"""Does the worker seat drive embodiment's OWN bounded tool loop? — plan task ``t1``.

The three-tier design (``docs/specs/2026-08-04-config-not-minds-strategist.md``,
decision ``c26``) promotes the *worker* from a tool-shaped seat to the acting
loop itself. Assumption ``c33`` records what that rests on: **nothing committed
measures this model driving a tool loop.** Arm B is explicitly "no loop, no
turn, no goal"; ``worker-throughput``/``worker-seam``/``worker-scoped-overhead``
all measure single calls. So the seat about to be promoted has an unmeasured
capability at the centre of the design.

This probe measures it, and it does so through ``embodiment.loop.run`` rather
than a stand-in: the question is whether the worker can drive *our* loop, so the
real loop is the instrument. A hand-rolled turn driver would be measuring
something we do not ship.

Design notes, each answering a way this probe could have measured nothing:

* **It must not be one call.** ``cortex-toolcall-probe.md`` scored 10/10 on a
  single ``finish`` call and said so plainly: a ceiling with no headroom, which
  is the condition ``M4`` says measures nothing. The task here needs **four**
  tool calls in sequence, two of them data-dependent on earlier results.
* **It exercises the ``#33`` shape directly.** That issue is an array argument
  sent as a string, which refused 74% of one arm's calls. ``average`` here takes
  ``values`` as an **array of numbers**, so the failure mode has somewhere to
  appear instead of being hoped absent.
* **Truncation is captured at the transport, not inferred.** ``ModelResponse``
  carries no ``finish_reason`` (embodiment#37), so inside the loop a truncated
  turn and a deliberate one are the same object — which is exactly how ``t24``
  found 6.0% of completions silently cut. This probe owns its HTTP client, so it
  records ``finish_reason`` per turn and can report truncation as measured
  rather than as absent-because-invisible.
* **The clock is derived, never chosen.** ``REQUEST_TIMEOUT >= max_tokens /
  slowest_measured_generation_rate``, read from the committed rate config, using
  the divisor the ``scoped_run`` calling pattern names (the all-width floor, not
  the width-1 reading — nothing reserves this deployment). A clock sized against
  the wrong quantity silently becomes the measurement; that lesson cost this
  repo four separate results.

Run::

    uv run python docs/live-test-results/worker-toolloop-probe.py --runs 6

Writes JSONL to ``worker-toolloop-probe.jsonl`` — one record per run, every
field a fact rather than a verdict. The verdict is written by a human into the
report beside it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from embodiment.contract import ModelResponse, Task, ToolCall  # noqa: E402
from embodiment.loop import ToolOutcome, run  # noqa: E402
from rate_config import load_rate_config  # noqa: E402

DEFAULT_GATEWAY = "http://localhost:8001/v1"
DEFAULT_ROLE_MODEL = "unsloth/Qwen3.6-35B-A3B-NVFP4"

#: The same environment variable every other harness here reads
#: (``examples/worker_seam.py:API_KEY_ENV``). Reused verbatim, not renamed.
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: Per ``d16``: the shipped 2048 default truncated 6.0% of completions with zero
#: degradations recorded. 16000 measured 0 of 58. The budget is the numerator of
#: the timeout rule and is read from the host that dials — here, this file.
MAX_TOKENS = 16000

#: The loop's step budget. Four tool calls are needed; 10 leaves room for a
#: model that takes a wrong turn and recovers, without letting a runaway run
#: forever. Termination is a structural guarantee of ``run`` regardless.
MAX_STEPS = 10

#: The world the probe asks about. Two readable sensors; the answer needs both.
SENSORS = {"fern-bed": 42, "orchid-bed": 61}
TRUTH = (SENSORS["fern-bed"] + SENSORS["orchid-bed"]) / 2  # 51.5

#: R1/R2 name the tool sequence: they measure protocol mechanics, not tool
#: selection. R3 deliberately does not, which is the whole point of that rung.
INSTRUCTION_SEQUENCE_NAMED = (
    "Report the mean moisture of the fern-bed and orchid-bed sensors. "
    "Read each sensor with read_sensor, compute the mean by passing both "
    "readings to average, and close by calling finish with that mean."
)

INSTRUCTION_UNNAMED = (
    "Report the mean moisture of the fern-bed and orchid-bed sensors. "
    "Use the tools available to you and close by calling finish."
)

#: R3's distractor. Plausible, cheaper-looking, and wrong: an estimate is not a
#: reading, and a seat that selects tools on convenience rather than on the task
#: will take it. Its returned value is deliberately off-truth so the choice is
#: visible in the answer, not only in the trace.
ESTIMATE_TOOL = {
    "type": "function",
    "function": {
        "name": "estimate_moisture",
        "description": (
            "Return a fast approximate moisture value for a sensor without "
            "reading the bus. Cheaper than read_sensor. May be stale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sensor_id": {"type": "string", "description": "The sensor to estimate."}
            },
            "required": ["sensor_id"],
        },
    },
}

#: What the distractor returns — off by enough that using it changes the answer.
ESTIMATES = {"fern-bed": 50, "orchid-bed": 50}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "read_sensor",
            "description": "Read the current moisture percentage of one sensor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor_id": {
                        "type": "string",
                        "description": "The sensor to read, e.g. fern-bed.",
                    }
                },
                "required": ["sensor_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "average",
            "description": "Compute the arithmetic mean of a list of numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "The numbers to average. Must be an array.",
                    }
                },
                "required": ["values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Close the task with the final answer.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "number", "description": "The mean moisture."}},
                "required": ["answer"],
            },
        },
    },
]


def derive_request_timeout(max_tokens: int) -> tuple[float, str]:
    """The clock, derived from the committed rate config — never chosen.

    Returns the bound and the provenance string recorded beside every run, so a
    reader can check the arithmetic without re-deriving the policy.
    """
    config = load_rate_config()
    rate = config.rate("worker")
    pattern = rate.calling_patterns["scoped_run"]
    divisor = pattern.tok_s
    bound = max_tokens / divisor
    return bound, (
        f"{max_tokens} tokens / {divisor} tok/s = {bound:.1f}s "
        f"(worker.calling_patterns.scoped_run, measured {rate.measured_on}, "
        f"rejecting the width-1 reading {pattern.rejected_tok_s} tok/s)"
    )


@dataclass
class TurnRecord:
    """One model turn, as the transport saw it — before the loop flattens it."""

    finish_reason: Optional[str]
    completion_tokens: int
    prompt_tokens: int
    latency_s: float
    tool_names: list[str]
    malformed_args: list[dict[str, Any]]
    had_content: bool


@dataclass
class ProbeExecutor:
    """The tool surface. Records every call, and never raises into the loop."""

    rung: str = "R1"
    calls: list[dict[str, Any]] = field(default_factory=list)
    malformed: list[dict[str, Any]] = field(default_factory=list)
    induced_failures: int = 0
    recovered_after_failure: bool = False
    used_distractor: bool = False

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append({"name": name, "arguments": arguments})
        if name == "estimate_moisture":
            self.used_distractor = True
            sensor = arguments.get("sensor_id")
            if sensor in ESTIMATES:
                return ToolOutcome(result=f"{sensor} estimated moisture: {ESTIMATES[sensor]}")
            return ToolOutcome(result=f"unknown sensor {sensor!r}")
        if name == "read_sensor":
            sensor = arguments.get("sensor_id")
            # R2: the bus refuses the FIRST read and names the retry. Recovery
            # inside the loop is the measurement; a happy path proves nothing
            # about what a real tool surface does.
            if self.rung == "R2" and self.induced_failures == 0:
                self.induced_failures += 1
                return ToolOutcome(
                    result="error: sensor bus is warming up and returned no value. "
                    "This is transient — call read_sensor again for the same sensor."
                )
            if sensor in SENSORS:
                if self.rung == "R2" and self.induced_failures == 1:
                    self.recovered_after_failure = True
                return ToolOutcome(result=f"{sensor} moisture: {SENSORS[sensor]}")
            return ToolOutcome(result=f"unknown sensor {sensor!r}")
        if name == "average":
            values = arguments.get("values")
            # The #33 shape: an array argument arriving as a string.
            if not isinstance(values, list):
                self.malformed.append(
                    {
                        "tool": "average",
                        "field": "values",
                        "expected": "array",
                        "got_type": type(values).__name__,
                        "got": repr(values)[:200],
                    }
                )
                return ToolOutcome(
                    result="error: values must be an array of numbers, not "
                    f"{type(values).__name__}"
                )
            try:
                numbers = [float(v) for v in values]
            except (TypeError, ValueError):
                self.malformed.append(
                    {"tool": "average", "field": "values", "got": repr(values)[:200]}
                )
                return ToolOutcome(result="error: values must all be numbers")
            if not numbers:
                return ToolOutcome(result="error: values must not be empty")
            return ToolOutcome(result=f"mean: {sum(numbers) / len(numbers)}")
        if name == "finish":
            answer = arguments.get("answer")
            return ToolOutcome(
                result=f"recorded: {answer}",
                finished=True,
                finish_summary=str(answer),
            )
        return ToolOutcome(result=f"unknown tool {name!r}")


class Dialler:
    """One model turn over the OpenAI-compatible surface, recording what the
    loop cannot see: ``finish_reason`` per turn (embodiment#37)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float,
        api_key: str = "",
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.tools = tools if tools is not None else TOOLS_SCHEMA
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.api_key = api_key
        self.turns: list[TurnRecord] = []
        self.transport_errors: list[str] = []

    def complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self.tools,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.3,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed local gateway
                request, timeout=self.timeout_s
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.transport_errors.append(f"{type(exc).__name__}: {exc}")
            raise
        latency = time.monotonic() - started

        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        raw_calls = message.get("tool_calls") or []

        tool_calls: list[ToolCall] = []
        malformed: list[dict[str, Any]] = []
        for raw in raw_calls:
            function = raw.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments")
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if not isinstance(arguments, dict):
                    raise ValueError("arguments were not a JSON object")
            except (TypeError, ValueError) as exc:
                # Unparseable arguments are a protocol failure, recorded as one.
                malformed.append(
                    {
                        "tool": name,
                        "reason": f"unparseable arguments: {exc}",
                        "raw": repr(raw_args)[:300],
                    }
                )
                continue
            tool_calls.append(
                ToolCall(
                    id=raw.get("id") or f"call-{len(tool_calls)}", name=name, arguments=arguments
                )
            )

        content = message.get("content") or ""
        self.turns.append(
            TurnRecord(
                finish_reason=choice.get("finish_reason"),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                latency_s=round(latency, 3),
                tool_names=[c.name for c in tool_calls],
                malformed_args=malformed,
                had_content=bool(content.strip()),
            )
        )
        return ModelResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


def rung_config(rung: str) -> tuple[str, list[dict[str, Any]]]:
    """The instruction and tool surface for one rung of the declared ladder."""
    if rung == "R3":
        # Sequence NOT named, and a plausible wrong tool is on the surface.
        return INSTRUCTION_UNNAMED, [*TOOLS_SCHEMA, ESTIMATE_TOOL]
    return INSTRUCTION_SEQUENCE_NAMED, list(TOOLS_SCHEMA)


def one_run(
    index: int,
    base_url: str,
    model: str,
    timeout_s: float,
    provenance: str,
    api_key: str,
    rung: str = "R1",
) -> dict:
    """One independent drive. Never raises: a failed run is a recorded run."""
    instruction, tools = rung_config(rung)
    dialler = Dialler(base_url, model, timeout_s, api_key, tools)
    executor = ProbeExecutor(rung=rung)
    task = Task(id=f"toolloop-{index}", repo_path=str(REPO_ROOT), instruction=instruction)

    started = time.monotonic()
    outcome_error: Optional[str] = None
    result = None
    try:
        result = run(
            dialler.complete,
            task,
            executor=executor,
            max_steps=MAX_STEPS,
            model=model,
        )
    except Exception as exc:  # a probe records failures, it does not propagate them
        outcome_error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    tool_names = [c["name"] for c in executor.calls]
    distinct_steps = len(executor.calls)
    reached_finish = any(c["name"] == "finish" for c in executor.calls)
    answer: Optional[float] = None
    for call in executor.calls:
        if call["name"] == "finish":
            raw = call["arguments"].get("answer")
            try:
                answer = float(raw)
            except (TypeError, ValueError):
                answer = None

    truncated_turns = [t for t in dialler.turns if t.finish_reason == "length"]

    return {
        "run": index,
        "rung": rung,
        "model": model,
        "base_url": base_url,
        "max_tokens": MAX_TOKENS,
        "max_steps": MAX_STEPS,
        "timeout_s": round(timeout_s, 1),
        "timeout_provenance": provenance,
        "elapsed_s": round(elapsed, 2),
        "model_turns": len(dialler.turns),
        "tool_calls": distinct_steps,
        "tool_sequence": tool_names,
        "multi_step": distinct_steps >= 2,
        "reached_finish": reached_finish,
        "answer": answer,
        "correct": answer is not None and abs(answer - TRUTH) < 0.001,
        "malformed_arg_events": executor.malformed
        + [m for t in dialler.turns for m in t.malformed_args],
        "truncated_turns": len(truncated_turns),
        "finish_reasons": [t.finish_reason for t in dialler.turns],
        "completion_tokens": sum(t.completion_tokens for t in dialler.turns),
        "prompt_tokens": sum(t.prompt_tokens for t in dialler.turns),
        "turn_latencies_s": [t.latency_s for t in dialler.turns],
        "turns_with_prose": sum(1 for t in dialler.turns if t.had_content),
        "transport_errors": dialler.transport_errors,
        "loop_error": outcome_error,
        "loop_status": getattr(result, "status", None) if result is not None else None,
        "induced_tool_failures": executor.induced_failures,
        "recovered_after_failure": executor.recovered_after_failure,
        "used_distractor": executor.used_distractor,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--rung", default="R1", choices=["R1", "R2", "R3"])
    parser.add_argument("--base-url", default=DEFAULT_GATEWAY)
    parser.add_argument("--model", default=DEFAULT_ROLE_MODEL)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if args.out is None:
        suffix = "" if args.rung == "R1" else f"-{args.rung.lower()}"
        args.out = str(Path(__file__).with_name(f"worker-toolloop-probe{suffix}.jsonl"))

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(
            f"error: no {API_KEY_ENV} in the environment\n"
            f"hint: export {API_KEY_ENV}=<the gateway's configured key>",
            file=sys.stderr,
        )
        return 2

    timeout_s, provenance = derive_request_timeout(MAX_TOKENS)
    print(f"clock: {provenance}", file=sys.stderr)
    instruction, tools = rung_config(args.rung)
    print(
        f"rung {args.rung}: truth={TRUTH} "
        f"tools={[t['function']['name'] for t in tools]} "
        f"sequence_named={args.rung != 'R3'}",
        file=sys.stderr,
    )

    records = []
    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as handle:
        for index in range(1, args.runs + 1):
            record = one_run(
                index, args.base_url, args.model, timeout_s, provenance, api_key, args.rung
            )
            records.append(record)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            print(
                f"run {index}: steps={record['tool_calls']} "
                f"seq={'>'.join(record['tool_sequence']) or '-'} "
                f"finish={record['reached_finish']} correct={record['correct']} "
                f"malformed={len(record['malformed_arg_events'])} "
                f"truncated={record['truncated_turns']} "
                f"{record['elapsed_s']}s",
                file=sys.stderr,
            )

    total = len(records)
    print("\n--- summary (facts only; the verdict is written by a human) ---", file=sys.stderr)
    print(f"runs: {total}", file=sys.stderr)
    print(
        f"multi-step (>=2 tool calls): {sum(r['multi_step'] for r in records)}/{total}",
        file=sys.stderr,
    )
    print(f"reached finish: {sum(r['reached_finish'] for r in records)}/{total}", file=sys.stderr)
    print(f"correct ({TRUTH}): {sum(r['correct'] for r in records)}/{total}", file=sys.stderr)
    print(
        f"runs with malformed args: "
        f"{sum(1 for r in records if r['malformed_arg_events'])}/{total}",
        file=sys.stderr,
    )
    print(
        f"runs with a truncated turn: "
        f"{sum(1 for r in records if r['truncated_turns'])}/{total}",
        file=sys.stderr,
    )
    print(
        f"transport errors: {sum(len(r['transport_errors']) for r in records)}",
        file=sys.stderr,
    )
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
