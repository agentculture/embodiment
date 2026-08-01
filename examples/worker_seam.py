#!/usr/bin/env python3
"""worker_seam — worker dial config, per-call metering, and a wiring-smoke lane.

Plan task **t2** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`). Scope is
**dialling, config and metering only** — a sibling task (t1) owns the delegate
tool that hands work to this seam (`examples/orchestrator_tools.py`); nothing
here spawns, delegates, or drives a subagent. This module answers three
questions and nothing more:

1. Where does the worker's endpoint and model come from, and what happens when
   they are not configured?
2. How is one worker call recorded, given `embodiment.contract.ModelResponse`
   deliberately carries no `finish_reason` (issue #37)?
3. Does the wiring actually work — a bare completion, and a bounded tool loop
   with a schema on the wire, a tool call returned, its result fed back, and a
   clean finish — through the exact code path future measured arms will reuse?

The rig, recorded here for documentation ONLY (never as a fallback — see
:func:`resolve_worker_config`): the worker role is served on Thor at
``http://thor.tail0be7e0.ts.net:8000``, OpenAI-compatible, model id
``unsloth/Qwen3.6-35B-A3B-NVFP4``, a thinking model with verified tool support.
Auth is a bearer token from ``COLLEAGUE_API_KEY`` — the same environment
variable every harness in this repo already uses (`examples/league_seat.py`).

No silent fallback (criterion 1)
---------------------------------
The spark gateway at ``http://localhost:8001/v1`` is what every OTHER harness
in this repo dials by default (`examples/proof.py`'s ``DEFAULT_BASE_URL``,
`examples/league_seat.py`'s ``DEFAULT_BASE_URL``) — it has no worker role
advertised at all (spec s2: "no worker advert locally"). A worker seam that
inherited that default-on-absence convention would silently dial the WRONG
gateway and either 404 or, worse, reach some other role entirely. So this
module defines no default for ``--worker-url`` / ``--worker-model`` at any
layer: :func:`resolve_worker_config` reads ONLY explicit flags and the two
``EMBODIMENT_WORKER_*`` environment variables, and absence is a returned,
structured :class:`WorkerDegradation` — never a substituted string.

The MeteredSeam pattern (criterion 3)
---------------------------------------
:class:`WorkerSeam` mirrors `examples/league_h2h.py`'s ``MeteredSeam``
byte-for-byte in what it records — ``finish_reason``, prompt/completion
tokens, wall clock, transport retries, truncation — for the same reason that
module states: ``ModelResponse`` cannot carry ``finish_reason``, so a host
reading only the loop's own record cannot tell a truncated turn from a
deliberate one. This is a parallel copy rather than an import so t2 stays
self-contained inside the two files it owns; a future consolidation (t5) may
choose to share one implementation, but that is not this task's call to make.

The smoke lane (criterion 2)
-------------------------------
:func:`run_smoke` drives exactly two calls through :class:`WorkerSeam`:

* :func:`run_bare_completion` — one call, no tool schema, nothing but a
  trivial prompt. This is the reachability/parseability check spec probe
  ``s19`` already ran by hand (a tool call round-tripped clean); this module
  makes it a committed, repeatable artifact instead of an anecdote.
* :func:`run_tool_loop` — the SAME seam, now with :data:`SMOKE_TOOL_SCHEMA` on
  the wire, driven through :func:`embodiment.run` (the real bounded tool loop,
  not a hand-rolled stand-in) against :class:`SmokeBench`, a closed two-tool
  surface (``add``, ``finish``). This is the code path every future measured
  arm reuses: a real ``Task``, a real executor, a real loop exit.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/worker_seam.py \\
        --worker-url http://thor.tail0be7e0.ts.net:8000/v1 \\
        --worker-model unsloth/Qwen3.6-35B-A3B-NVFP4 \\
        --out docs/live-test-results/worker-seam-smoke.json
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
from typing import Any, Callable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import LoopAborted, LoopControls, Task, ToolOutcome, run  # noqa: E402
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402

__all__ = [
    "API_KEY_ENV",
    "WORKER_URL_ENV",
    "WORKER_MODEL_ENV",
    "THOR_WORKER_URL_DOCUMENTED",
    "THOR_WORKER_MODEL_DOCUMENTED",
    "SPARK_GATEWAY_URL",
    "DEGRADED_WORKER_URL_ABSENT",
    "DEGRADED_WORKER_MODEL_ABSENT",
    "DEGRADED_WORKER_API_KEY_ABSENT",
    "DEGRADED_WORKER_URL_INVALID",
    "WorkerDegradation",
    "WorkerConfig",
    "WorkerConfigResolution",
    "resolve_worker_config",
    "WorkerTransportError",
    "Meter",
    "WorkerSeam",
    "parse_completion",
    "SMOKE_TOOL_SCHEMA",
    "SMOKE_SYSTEM_PROMPT",
    "SMOKE_PROMPT",
    "BARE_PROMPT",
    "SmokeBench",
    "run_bare_completion",
    "run_tool_loop",
    "run_smoke",
    "build_parser",
    "main",
]

# ── config resolution — criterion 1 ───────────────────────────────────────────

#: The shared bearer-token variable every harness in this repo already reads
#: (see `examples/league_seat.py:API_KEY_ENV`). Reused verbatim, not renamed,
#: so one exported key drives every harness on this rig.
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: Explicit, worker-specific env vars. Distinct from `EMBODIMENT_BASE_URL`
#: (which every OTHER harness in this repo reads for the spark gateway) on
#: purpose: a host that exports one must not accidentally satisfy the other.
WORKER_URL_ENV = "EMBODIMENT_WORKER_URL"
WORKER_MODEL_ENV = "EMBODIMENT_WORKER_MODEL"

#: The rig's worker role, as documentation ONLY. Never read as a default by any
#: function in this module — see `resolve_worker_config`'s docstring. Named
#: "_DOCUMENTED" rather than "_DEFAULT" so a future edit cannot mistake it for
#: one.
THOR_WORKER_URL_DOCUMENTED = "http://thor.tail0be7e0.ts.net:8000/v1"
THOR_WORKER_MODEL_DOCUMENTED = "unsloth/Qwen3.6-35B-A3B-NVFP4"

#: The endpoint this module must NEVER silently fall back to. Named so a
#: reader can grep for it; it appears nowhere else in this file except this
#: comment and the module docstring above.
SPARK_GATEWAY_URL = "http://localhost:8001/v1"

DEGRADED_WORKER_URL_ABSENT = "worker-url-absent"
DEGRADED_WORKER_MODEL_ABSENT = "worker-model-absent"
DEGRADED_WORKER_API_KEY_ABSENT = "worker-api-key-absent"
DEGRADED_WORKER_URL_INVALID = "worker-url-invalid"


@dataclass(frozen=True)
class WorkerDegradation:
    """One structured, recorded reason the worker could not be dialled (C3).

    Never a raised exception on its own — the caller decides what to do with
    it (the CLI prints it and exits non-zero; a test asserts on it directly).
    """

    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class WorkerConfig:
    """A fully-resolved worker dial. Constructible only by `resolve_worker_config`."""

    base_url: str
    model: str
    api_key: str

    def to_dict(self) -> dict[str, Any]:
        # api_key is NEVER serialized or echoed anywhere (matches MeteredSeam's
        # own convention in league_h2h.py).
        return {"base_url": self.base_url, "model": self.model}


@dataclass(frozen=True)
class WorkerConfigResolution:
    """The result of resolving config: either a usable dial, or recorded ABSENT."""

    config: Optional[WorkerConfig]
    degradations: tuple[WorkerDegradation, ...] = ()

    @property
    def ok(self) -> bool:
        return self.config is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "config": self.config.to_dict() if self.config else None,
            "degradations": [d.to_dict() for d in self.degradations],
        }


def resolve_worker_config(
    *,
    cli_url: Optional[str] = None,
    cli_model: Optional[str] = None,
    cli_api_key: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> WorkerConfigResolution:
    """Resolve the worker endpoint/model/key from explicit flags/env ONLY.

    Precedence is flag over env, per argument, independently — a caller may
    pass ``--worker-url`` and still rely on ``EMBODIMENT_WORKER_MODEL`` for the
    model. There is NO third source: no constant in this module, no other
    environment variable (``EMBODIMENT_BASE_URL`` is never consulted here), and
    no inference from what other harnesses on this rig happen to be using.

    Absence of any one of the three inputs is a recorded
    :class:`WorkerDegradation` and ``config=None`` — never a partially-filled
    :class:`WorkerConfig` and never a substituted value. All three are checked
    (rather than short-circuiting on the first miss) so a caller sees the FULL
    picture of what is missing in one pass, matching the ``events.py``
    degradation-vocabulary convention of one legible record per fault.
    """
    live_env: Mapping[str, str] = os.environ if env is None else env
    url = (cli_url or live_env.get(WORKER_URL_ENV) or "").strip()
    model = (cli_model or live_env.get(WORKER_MODEL_ENV) or "").strip()
    api_key = (cli_api_key or live_env.get(API_KEY_ENV) or "").strip()

    degradations: list[WorkerDegradation] = []
    if not url:
        degradations.append(
            WorkerDegradation(
                DEGRADED_WORKER_URL_ABSENT,
                f"no --worker-url and no {WORKER_URL_ENV} in the environment -- "
                "refusing to fall back to the spark gateway",
            )
        )
    if not model:
        degradations.append(
            WorkerDegradation(
                DEGRADED_WORKER_MODEL_ABSENT,
                f"no --worker-model and no {WORKER_MODEL_ENV} in the environment",
            )
        )
    if not api_key:
        degradations.append(
            WorkerDegradation(
                DEGRADED_WORKER_API_KEY_ABSENT, f"no {API_KEY_ENV} in the environment"
            )
        )
    if degradations:
        return WorkerConfigResolution(config=None, degradations=tuple(degradations))

    if not url.startswith(("http://", "https://")):
        return WorkerConfigResolution(
            config=None,
            degradations=(
                WorkerDegradation(
                    DEGRADED_WORKER_URL_INVALID, f"--worker-url must be http(s), got {url!r}"
                ),
            ),
        )

    return WorkerConfigResolution(config=WorkerConfig(base_url=url, model=model, api_key=api_key))


# ── the metered transport — criterion 3 ───────────────────────────────────────

#: A timeout or connection error is CONTENTION on a shared rig, not a result —
#: same discipline as `examples/league_h2h.py`'s ``MeteredSeam``.
MAX_TRANSPORT_RETRIES = 3
RETRY_SLEEP_SECONDS = 20.0
REQUEST_TIMEOUT = 300.0

#: league's own word (reused here) for a completion that ran out of budget
#: mid-thought — an INSTRUMENT event, never a reasoning failure.
FINISH_TRUNCATED = "length"

#: How much of one turn's own words is kept verbatim in a committed transcript.
TRANSCRIPT_CONTENT_CHARS = 4000
TRANSCRIPT_REASONING_CHARS = 2000


class WorkerTransportError(RuntimeError):
    """The worker transport failed after exhausting its retries."""


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def parse_completion(payload: dict[str, Any]) -> ModelResponse:
    """Shape one OpenAI-compatible completion into a :class:`ModelResponse`.

    ``reasoning`` is carried separately from ``content`` because the worker is
    a thinking model (`unsloth/Qwen3.6-35B-A3B-NVFP4`); folding the two
    together would report a thought as if it were a reply. Mirrors
    `examples/league_seat.py`'s function of the same name.
    """
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    raw_calls = message.get("tool_calls") or []

    calls: list[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        function = (raw or {}).get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = {}
        calls.append(
            ToolCall(
                id=str((raw or {}).get("id") or f"call-{index}"),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    usage = payload.get("usage") or {}
    return ModelResponse(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or message.get("reasoning") or ""),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


@dataclass
class Meter:
    """What one role's calls cost. Cost is a result, so it is first-class.

    Field-for-field identical to `examples/league_h2h.py`'s ``Meter`` — see
    that class for the rationale of each field.
    """

    role: str
    model: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    retries: int = 0
    failures: int = 0
    empty_content: int = 0
    #: Turns that ran out of token budget mid-thought. An INSTRUMENT event.
    truncated: int = 0
    finish_reasons: dict[str, int] = field(default_factory=dict)
    #: One entry per completed model turn — the raw transcript.
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def record_turn(
        self,
        reply: ModelResponse,
        *,
        finish_reason: str,
        seconds: float,
        messages: int,
    ) -> None:
        content, content_clipped = _clip(reply.content or "", TRANSCRIPT_CONTENT_CHARS)
        reasoning, reasoning_clipped = _clip(reply.reasoning or "", TRANSCRIPT_REASONING_CHARS)
        self.transcript.append(
            {
                "role": self.role,
                "model": self.model,
                "messages_in": messages,
                "finish_reason": finish_reason,
                "seconds": round(seconds, 3),
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "content": content,
                "content_clipped": content_clipped,
                "reasoning": reasoning,
                "reasoning_clipped": reasoning_clipped,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments} for call in reply.tool_calls
                ],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 3),
            "retries": self.retries,
            "failures": self.failures,
            "empty_content": self.empty_content,
            "truncated": self.truncated,
            "finish_reasons": dict(self.finish_reasons),
        }


class WorkerSeam:
    """One OpenAI-compatible round trip per worker turn, fully accounted.

    A parallel copy of `examples/league_h2h.py`'s ``MeteredSeam`` — same
    endpoint construction, same four wire keys, ``tools`` present only when the
    caller passes a schema — kept self-contained here because t2 owns exactly
    two files. It records what :class:`ModelResponse` cannot:

    * ``finish_reason`` — the Qwen truncation trap: a budget-exhausted turn
      returns empty content and reads exactly like a model with nothing to say.
    * wall clock and token spend, per call.
    * transport retries. A timeout on a shared rig is contention, not a
      result; it is retried, bounded, and **counted**, never silently.

    Tests drive every branch by monkey-patching :meth:`_post` directly (the
    same technique `tests/test_league_h2h.py` uses on ``MeteredSeam``), so no
    real socket is ever touched by the hermetic suite.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        role: str = "worker",
        max_tokens: int,
        temperature: float = 0.3,
        tools: Optional[list[dict[str, Any]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be http(s), got {base_url!r}")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tools = tools
        self.meter = Meter(role=role, model=model)
        self._sleep = sleep

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed anywhere.
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        # The scheme is pinned to http(s) in __init__ and the endpoint is the
        # operator's own --worker-url; audited once, here.
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # nosec B310
            raw = response.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as bad_body:
            # A body we cannot parse is a TRANSPORT event, not an answer.
            #
            # Raised as OSError so ``__call__``'s existing retry path catches
            # it: without this it escaped the loop entirely, so a gateway
            # returning an HTML error page, or a truncated body, would abort
            # the drive with no retry AND no meter entry — the failure would
            # not appear in the per-call record at all. That is a C3 violation
            # (every degradation records a transition) in the one module whose
            # job is per-call accounting.
            #
            # Realistic on this rig rather than theoretical: the Spark gateway
            # proxies `worker` but reports `feasible: false`, and a misdialled
            # proxy is exactly what returns a non-JSON error body.
            preview = raw[:200].decode("utf-8", errors="replace")
            raise OSError(
                f"{self.meter.role} returned a body that is not JSON "
                f"({type(bad_body).__name__}): {preview!r}"
            ) from bad_body

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.tools:
            body["tools"] = self.tools

        started = time.monotonic()
        last: Exception = RuntimeError("unreachable")
        for attempt in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                payload = self._post(body)
            except (urllib.error.URLError, TimeoutError, OSError) as failure:
                last = failure
                self.meter.retries += 1
                if attempt >= MAX_TRANSPORT_RETRIES:
                    break
                self._sleep(RETRY_SLEEP_SECONDS)
                continue
            elapsed = time.monotonic() - started
            self.meter.calls += 1
            self.meter.seconds += elapsed
            reply = parse_completion(payload)
            self.meter.prompt_tokens += reply.prompt_tokens
            self.meter.completion_tokens += reply.completion_tokens
            reason = str(((payload.get("choices") or [{}])[0] or {}).get("finish_reason") or "")
            self.meter.finish_reasons[reason] = self.meter.finish_reasons.get(reason, 0) + 1
            if reason == FINISH_TRUNCATED:
                # An instrument event, recorded the moment it happens. It is
                # NEVER read as the model having nothing to say.
                self.meter.truncated += 1
                print(
                    f"notice: {self.meter.role} turn truncated at "
                    f"max_tokens={self.max_tokens} ({self.model})",
                    file=sys.stderr,
                )
            if not reply.content and not reply.tool_calls:
                self.meter.empty_content += 1
            self.meter.record_turn(
                reply, finish_reason=reason, seconds=elapsed, messages=len(messages)
            )
            return reply

        self.meter.seconds += time.monotonic() - started
        self.meter.failures += 1
        # A failed call is DATA. It degrades to an empty turn, which the loop
        # reads as a model with nothing more to say, and the failure count
        # rides into the artifact beside the result.
        raise WorkerTransportError(f"{self.meter.role} transport failed after retries: {last}")


# ── the smoke lane's closed tool surface ──────────────────────────────────────

SMOKE_TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two integers and return their exact sum.",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
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

SMOKE_SYSTEM_PROMPT = (
    "You are being wiring-tested against a freshly-dialled model seam. You have "
    "exactly two tools: add and finish. Use add to compute the requested sum, "
    "then call finish with the integer result. Do not answer without calling "
    "add first."
)

SMOKE_PROMPT = "What is 17 + 25? Call add to compute it, then call finish with the integer answer."

BARE_PROMPT = "Reply with exactly: WORKER-OK"

#: The verifiable truth for the smoke problem. Never shown to the model.
SMOKE_TRUTH = 42


class SmokeBench:
    """A minimal, closed tool surface: ``add`` then ``finish``. Nothing else.

    Deliberately as small as `examples/proof.py`'s ``ProofBench`` but smaller
    still — this is a WIRING check, not a reasoning test. No evaluator is
    exposed (c35: not every host has a shell), matching this repo's standing
    tool-surface discipline.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))

        if name == "add":
            a, b = self._int(arguments.get("a")), self._int(arguments.get("b"))
            if a is None or b is None:
                return ToolOutcome(result="a and b must both be integers")
            return ToolOutcome(result=str(a + b))

        if name == "finish":
            return ToolOutcome(
                result="submitted",
                finished=True,
                finish_summary=str(arguments.get("answer", "")),
            )

        return ToolOutcome(result=f"unknown tool {name}")

    @staticmethod
    def _int(raw: Any) -> Optional[int]:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    def state(self) -> str:
        names = [name for name, _ in self.calls]
        return f"{len(names)} tool call(s)" + (f"; last: {names[-1]}" if names else "")


# ── the smoke lane itself — criterion 2 ───────────────────────────────────────


def run_bare_completion(
    config: WorkerConfig, *, max_tokens: int, temperature: float
) -> dict[str, Any]:
    """One completion, no tool schema on the wire. The reachability check."""
    seam = WorkerSeam(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        role="worker-bare",
        max_tokens=max_tokens,
        temperature=temperature,
    )
    messages = [{"role": "user", "content": BARE_PROMPT}]
    reply = seam(messages)
    return {
        "kind": "bare-completion",
        "prompt": BARE_PROMPT,
        "content": reply.content,
        "cost": seam.meter.to_dict(),
        "transcript": seam.meter.transcript,
    }


def run_tool_loop(
    config: WorkerConfig, *, max_tokens: int, temperature: float, max_steps: int
) -> dict[str, Any]:
    """One bounded tool loop: schema on the wire, a call fed back, a clean finish.

    Drives the SAME ``WorkerSeam`` through :func:`embodiment.run` — the real
    loop every future measured arm reuses, not a hand-rolled stand-in.
    """
    seam = WorkerSeam(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        role="worker-tool-loop",
        max_tokens=max_tokens,
        temperature=temperature,
        tools=SMOKE_TOOL_SCHEMA,
    )
    bench = SmokeBench()
    task = Task.new(".", SMOKE_PROMPT, engine="worker-seam-smoke")

    aborted: Optional[str] = None
    try:
        outcome = run(
            seam,
            task,
            executor=bench,
            max_steps=max_steps,
            system_prompt=SMOKE_SYSTEM_PROMPT,
            controls=LoopControls(write_intent=False),
            model=config.model,
        )
    except LoopAborted as failure:
        outcome = failure.outcome
        aborted = str(failure.__cause__ or failure)

    return {
        "kind": "tool-loop",
        "prompt": SMOKE_PROMPT,
        "exit_reason": outcome.exit_reason,
        "status": outcome.result.status,
        "summary": outcome.result.summary,
        "model_turns": outcome.result.stats.model_turns,
        "tools_called": [step.tool for step in outcome.result.steps],
        "aborted": aborted,
        "cost": seam.meter.to_dict(),
        "transcript": seam.meter.transcript,
    }


def run_smoke(
    config: WorkerConfig,
    *,
    max_tokens: int = 16000,
    temperature: float = 0.3,
    max_steps: int = 6,
) -> dict[str, Any]:
    """Both smoke calls, bundled with the config they ran under.

    ``max_tokens=16000`` is d16's measured floor for this rig's thinking
    models (`docs/live-test-results/arena-budget.md`): a lower cap risks
    silently truncating the worker's own reasoning before it ever reaches the
    ``add``/``finish`` calls, which would make a wiring smoke fail for a budget
    reason and be misread as a wiring reason.
    """
    return {
        "kind": "worker-smoke",
        "note": "instrument check, not data",
        "worker": config.to_dict(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "max_steps": max_steps,
        "bare_completion": run_bare_completion(
            config, max_tokens=max_tokens, temperature=temperature
        ),
        "tool_loop": run_tool_loop(
            config, max_tokens=max_tokens, temperature=temperature, max_steps=max_steps
        ),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument(
        "--worker-url",
        default=None,
        help=(
            f"OpenAI-compatible base URL, e.g. {THOR_WORKER_URL_DOCUMENTED} "
            f"({WORKER_URL_ENV} env-overridable; no default, no fallback)"
        ),
    )
    parser.add_argument(
        "--worker-model",
        default=None,
        help=(
            f"e.g. {THOR_WORKER_MODEL_DOCUMENTED} "
            f"({WORKER_MODEL_ENV} env-overridable; no default)"
        ),
    )
    parser.add_argument("--max-tokens", type=int, default=16000, help="d16's measured floor")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--out", default=None, help="also write the smoke report JSON here")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    resolution = resolve_worker_config(cli_url=args.worker_url, cli_model=args.worker_model)
    if not resolution.ok:
        for degradation in resolution.degradations:
            print(f"notice: {degradation.code}: {degradation.detail}", file=sys.stderr)
        print(json.dumps(resolution.to_dict(), indent=2))
        return 2

    assert resolution.config is not None  # narrows for mypy/readers; ok implies this
    report = run_smoke(
        resolution.config,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        max_steps=args.max_steps,
    )
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
