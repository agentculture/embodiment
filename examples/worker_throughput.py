#!/usr/bin/env python3
"""worker_throughput — measured tok/s and effective concurrency, widths 1/2/8/14.

Plan task **t3** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`), depends on t2
(`examples/worker_seam.py`, merged). This is a **live measurement task**: its
whole job is to put real numbers under three claims before any arm design cites
concurrency —

1. **What tok/s does the worker actually sustain**, at concurrency widths 1, 2,
   8 and 14 — reported as **per-stream** (one call's own tokens ÷ its own wall
   clock) and **aggregate** (a whole batch's tokens ÷ the batch's own wall
   clock) *separately*, because conflating the two is the classic way a
   concurrency benchmark lies to itself.
2. **Does concurrency actually buy parallelism**, or does the server queue
   requests behind a single decode stream? :data:`WidthSummary.effective_concurrency`
   (``aggregate_tok_s / per_stream_tok_s_mean``) answers this directly: a
   server that truly serves ``W`` concurrent streams independently reports
   ``effective_concurrency ≈ W``; a server that queues reports ``≈ 1``
   regardless of the width dialled.
3. **Did the operator-supplied "50 tok/s × 14 concurrency" figure reproduce**
   on this rig — checked both as a per-stream reading (50 tok/s sustained
   *per stream* even at width 14) and an aggregate reading (700 tok/s total
   across 14 streams), since the figure is ambiguous on its face and this
   module refuses to pick one silently (:func:`check_operator_claim`).

Reuse, not reimplementation (per this task's brief)
-----------------------------------------------------
:class:`ThroughputSeam` is `examples/worker_seam.py`'s :class:`~worker_seam.WorkerSeam`
**subclassed**, not rewritten — the transport, the bounded retries, and the
`Meter` per-call record (`finish_reason`, prompt/completion tokens, wall clock)
all come from there unchanged. The one thing that class does not keep is the
*raw* ``usage`` payload, and this task additionally needs
``usage.completion_tokens_details.reasoning_tokens`` (or the flat
``usage.reasoning_tokens`` some OpenAI-compatible servers use) when the server
reports it — `embodiment.contract.ModelResponse` carries no `finish_reason`
(issue #37) and `WorkerSeam.parse_completion` does not carry a reasoning/content
token split either, so this subclass keeps one extra field (:attr:`last_payload`)
and nothing else. :func:`reasoning_tokens_from` mirrors `examples/arch_arms.py`'s
function of the same name; kept as a small self-contained copy for the same
reason `worker_seam.py`'s own ``Meter`` mirrors `league_h2h.py`'s — this task
owns exactly the files it ships, not a shared import across sibling tasks'
harnesses (see `worker_seam.py`'s module docstring for the same argument made
about *its* copy).

Design choices, stated rather than left implicit
----------------------------------------------------
* **Warm-up policy.** One discarded call (``width=0, batch=-1``, marked
  ``warmup: true`` in the raw record) runs before width 1 begins, to absorb any
  cold-start cost (connection setup, first-request JIT/cache effects on the
  server). It is excluded from every width's summary and from the operator-claim
  check. No other call anywhere in the sweep is excluded — the first call of
  every batch counts, on purpose, because "does the first call of a batch look
  different" is itself part of what sustained-load stability means.
* **Fixed, committed prompt set** (:data:`PROMPT_SET`): eight short, benign,
  bounded-answer prompts ("in two sentences, …"), cycled through across the
  whole sweep so no two concurrent calls in one batch are identical (avoiding
  any server-side prefix-cache skew) and so every width sees the same rotation,
  making widths comparable. Editing this tuple invalidates comparison against
  any run committed before the edit — treat it the way a preregistration treats
  its constants.
* **`max_tokens=1200`, not d16's 16000 floor.** `docs/live-test-results/arena-budget.md`
  established 16000 as the floor for the *cortex driving a bounded tool loop
  across multi-turn arena matches* — a different workload. These are single
  bare completions answering a two-sentence factual prompt; from the t2 smoke
  transcript (`docs/live-test-results/worker-seam-smoke.json`) a *much* smaller
  prompt ("reply with exactly: WORKER-OK") already cost 176 completion tokens
  of mostly reasoning, so 1200 gives real headroom while keeping both wall
  clock and live spend bounded across four widths. Truncation at this budget is
  measured and reported per width, not assumed away.
* **Batches per width** (:data:`DEFAULT_BATCHES_PER_WIDTH`): every width runs
  more than one sequential batch, so a width's own numbers already show
  batch-to-batch drift (a proxy for sustained-load stability) rather than a
  single sample. Width 1 gets more batches (cheap, sequential, and it is the
  single-stream baseline everything else is measured against); width 14 gets
  fewer (it is the most expensive cell in the sweep).
* **Non-streaming completions.** This harness does not parse SSE, so it cannot
  separate time-to-first-token (queueing/prefill) from decode time — a call's
  ``latency_seconds`` is the two combined. This is a stated limitation, not a
  hidden one: it is *why* :attr:`WidthSummary.effective_concurrency` is framed
  as a throughput ratio rather than a queueing-delay measurement.
* **One fresh seam per call, never shared across threads.** `WorkerSeam.meter`
  has no lock; sharing one instance across concurrent threads would race its
  counters. Every call in a batch gets its own :class:`ThroughputSeam`, so
  there is nothing to race — the same choice `examples/worker_seam.py`'s own
  ``run_bare_completion`` makes for a single call.
* **Termination discipline matches `examples/orchestrator_tools.py`'s fan-out**
  (task t4): no ``while`` anywhere in this module, one bounded
  :func:`concurrent.futures.wait` per batch, ``shutdown(wait=False,
  cancel_futures=True)`` in a ``finally``, and a future that misses the
  deadline is recorded as a timeout and never read again — never joined,
  never re-queried.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/worker_throughput.py \\
        --worker-url http://thor.tail0be7e0.ts.net:8000/v1 \\
        --worker-model unsloth/Qwen3.6-35B-A3B-NVFP4 \\
        --out docs/live-test-results/worker-throughput.jsonl \\
        --summary-out docs/live-test-results/worker-throughput-summary.json
"""

from __future__ import annotations

import argparse
import functools
import itertools
import json
import math
import statistics
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.worker_seam import (  # noqa: E402
    THOR_WORKER_MODEL_DOCUMENTED,
    THOR_WORKER_URL_DOCUMENTED,
    WorkerConfig,
    WorkerSeam,
    resolve_worker_config,
)

__all__ = [
    "PROMPT_SET",
    "DEFAULT_WIDTHS",
    "DEFAULT_BATCHES_PER_WIDTH",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "BATCH_WAIT_TIMEOUT_SECONDS",
    "THREAD_NAME_PREFIX",
    "FINISH_TRUNCATED",
    "TOKEN_DETAIL_USAGE_DETAILS",
    "TOKEN_DETAIL_USAGE_FLAT",
    "TOKEN_DETAIL_ABSENT",
    "OPERATOR_CLAIM_PER_STREAM_TOK_S",
    "OPERATOR_CLAIM_WIDTH",
    "OPERATOR_CLAIM_AGGREGATE_TOK_S",
    "reasoning_tokens_from",
    "ThroughputSeam",
    "CallSpec",
    "CallRecord",
    "WidthSummary",
    "WidthRun",
    "SweepResult",
    "check_operator_claim",
    "run_sweep",
    "build_parser",
    "main",
]

# ── the committed prompt set ──────────────────────────────────────────────────

#: FIXED and COMMITTED. Every prompt asks for a short, bounded, benign answer
#: so no call is likely to run away in length; the variety (not eight copies of
#: one prompt) avoids any server-side prefix-cache skew across a concurrent
#: batch. Do not edit without noting it — a changed prompt set makes any new
#: run incomparable to one committed before the edit.
PROMPT_SET: tuple[str, ...] = (
    "In two sentences, explain why the sky appears blue.",
    "List three benefits of automated testing, one short sentence each.",
    "In two sentences, explain what a hash map is.",
    "In two sentences, explain the difference between TCP and UDP.",
    "In two sentences, explain how binary search works.",
    "In two sentences, describe what a load balancer does.",
    "In two sentences, explain why caching improves performance.",
    "In two sentences, describe what garbage collection does in a language runtime.",
)

# ── sweep defaults ─────────────────────────────────────────────────────────────

DEFAULT_WIDTHS: tuple[int, ...] = (1, 2, 8, 14)

#: More batches at low width (cheap, and it is the single-stream baseline
#: everything else is measured against); fewer at high width (the most
#: expensive cell). See the module docstring for the full rationale.
DEFAULT_BATCHES_PER_WIDTH: dict[int, int] = {1: 3, 2: 2, 8: 2, 14: 2}
DEFAULT_BATCHES_FALLBACK = 2

#: See the module docstring: this is NOT d16's 16000 cortex-tool-loop floor.
#: These are single bare completions on short factual prompts.
DEFAULT_MAX_TOKENS = 1200
DEFAULT_TEMPERATURE = 0.3

#: A batch-level circuit breaker. If it fires, that is itself a stability
#: finding — something in the batch took far longer than any healthy call in
#: this series ever has — and is reported as a timeout, not silently absorbed
#: by waiting longer. See the module docstring for the arithmetic that ruled
#: out just inheriting `WorkerSeam`'s own 300s-per-call x 3-retries budget.
BATCH_WAIT_TIMEOUT_SECONDS = 300.0

THREAD_NAME_PREFIX = "worker-throughput"

#: league's own word (reused here, matching `worker_seam.py`'s own local copy)
#: for a completion that ran out of budget mid-thought.
FINISH_TRUNCATED = "length"

#: Where a reasoning-token count came from. (``nosec B105``: bandit reads any
#: constant whose name contains TOKEN as a credential; these are usage-payload
#: field paths, matching `arena_series.py`'s `VERDICT_PASS` and
#: `arch_arms.py`'s identically-named constants for the same false positive.)
TOKEN_DETAIL_USAGE_DETAILS = "usage.completion_tokens_details.reasoning_tokens"  # nosec B105
TOKEN_DETAIL_USAGE_FLAT = "usage.reasoning_tokens"  # nosec B105
TOKEN_DETAIL_ABSENT = "absent"  # nosec B105

#: The operator's figure, as given: "50 tok/s x 14 concurrency". Ambiguous on
#: its face -- see :func:`check_operator_claim`, which checks both readings.
OPERATOR_CLAIM_PER_STREAM_TOK_S = 50.0
OPERATOR_CLAIM_WIDTH = 14
OPERATOR_CLAIM_AGGREGATE_TOK_S = OPERATOR_CLAIM_PER_STREAM_TOK_S * OPERATOR_CLAIM_WIDTH


def reasoning_tokens_from(usage: Mapping[str, Any]) -> tuple[Optional[int], str]:
    """Read a reasoning-token count from a raw ``usage`` payload, or say there is none.

    ``None`` (never ``0``) when the server reports no breakdown: a thinking
    model whose server is silent about the split has not thought about
    nothing, and folding "unknown" into "zero" is how a cost table stops being
    evidence. Mirrors `examples/arch_arms.py`'s function of the same name.
    """
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("reasoning_tokens"), int):
        return int(details["reasoning_tokens"]), TOKEN_DETAIL_USAGE_DETAILS
    if isinstance(usage.get("reasoning_tokens"), int):
        return int(usage["reasoning_tokens"]), TOKEN_DETAIL_USAGE_FLAT
    return None, TOKEN_DETAIL_ABSENT


# ── the transport: WorkerSeam, plus the one field it doesn't keep ────────────


class ThroughputSeam(WorkerSeam):
    """`WorkerSeam`, subclassed to also keep the last raw ``usage`` payload.

    Everything else — the endpoint, the bounded retries, `self.meter` — is
    inherited unchanged; this overrides only :meth:`_post` to remember what it
    returned, because `WorkerSeam.parse_completion` reads only
    ``prompt_tokens``/``completion_tokens`` and this task additionally needs
    whatever reasoning-token breakdown the server chose to report (or the
    honest absence of one).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_payload: Optional[dict[str, Any]] = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = super()._post(body)
        self.last_payload = payload
        return payload


# ── one call, fully accounted, and it never raises ────────────────────────────


@dataclass(frozen=True)
class CallSpec:
    """What one call in the sweep is: which width/batch/slot, and which prompt."""

    width: int
    batch: int
    slot: int
    prompt_id: int
    prompt: str
    warmup: bool = False


@dataclass(frozen=True)
class CallRecord:
    """One call's whole outcome. This is the raw, per-call artifact this task commits."""

    width: int
    batch: int
    slot: int
    prompt_id: int
    warmup: bool
    ok: bool
    error: Optional[str]
    latency_seconds: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    reasoning_token_source: str
    content_chars: Optional[int]
    reasoning_chars: Optional[int]
    finish_reason: Optional[str]
    truncated: bool
    retries: int
    tokens_per_second: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "batch": self.batch,
            "slot": self.slot,
            "prompt_id": self.prompt_id,
            "warmup": self.warmup,
            "ok": self.ok,
            "error": self.error,
            "latency_seconds": _round(self.latency_seconds),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_token_source": self.reasoning_token_source,
            "content_chars": self.content_chars,
            "reasoning_chars": self.reasoning_chars,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "retries": self.retries,
            "tokens_per_second": _round(self.tokens_per_second),
        }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 3)


def _failed_record(
    spec: CallSpec, *, error: str, elapsed: Optional[float], retries: int = 0
) -> CallRecord:
    return CallRecord(
        width=spec.width,
        batch=spec.batch,
        slot=spec.slot,
        prompt_id=spec.prompt_id,
        warmup=spec.warmup,
        ok=False,
        error=error,
        latency_seconds=elapsed,
        prompt_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        reasoning_token_source=TOKEN_DETAIL_ABSENT,
        content_chars=None,
        reasoning_chars=None,
        finish_reason=None,
        truncated=False,
        retries=retries,
        tokens_per_second=None,
    )


def _one_call(
    config: WorkerConfig,
    spec: CallSpec,
    *,
    max_tokens: int,
    temperature: float,
    sleep: Callable[[float], None] = time.sleep,
) -> CallRecord:
    """One completion, fully accounted. **This never raises.**

    A thread that raises leaves its `Future` holding an exception the
    collecting side must re-raise or swallow — and swallowing is exactly what
    this repo's C3 forbids. So every fault becomes a `CallRecord` here, on the
    thread that saw it.

    ``sleep`` is threaded straight through to `ThroughputSeam` (which threads
    it straight through to `WorkerSeam`'s own retry backoff): real calls get
    real sleeps, and a test exercising the retry-then-fail path can inject a
    no-op instead of paying `WorkerSeam.RETRY_SLEEP_SECONDS` x 3 in real wall
    clock for a hermetic assertion.
    """
    seam = ThroughputSeam(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        role=f"throughput-w{spec.width}",
        max_tokens=max_tokens,
        temperature=temperature,
        sleep=sleep,
    )
    messages = [{"role": "user", "content": spec.prompt}]
    started = time.monotonic()
    try:
        reply = seam(messages)
    except Exception as exc:  # noqa: BLE001 -- a failed call is DATA, never a raise from a thread
        elapsed = time.monotonic() - started
        return _failed_record(
            spec, error=f"{type(exc).__name__}: {exc}", elapsed=elapsed, retries=seam.meter.retries
        )

    elapsed = time.monotonic() - started
    turn = seam.meter.transcript[0]
    usage = (seam.last_payload or {}).get("usage") or {}
    reasoning_tokens, source = reasoning_tokens_from(usage)
    tokens_per_second = (
        reply.completion_tokens / elapsed if elapsed > 0 and reply.completion_tokens else None
    )
    return CallRecord(
        width=spec.width,
        batch=spec.batch,
        slot=spec.slot,
        prompt_id=spec.prompt_id,
        warmup=spec.warmup,
        ok=True,
        error=None,
        latency_seconds=elapsed,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        reasoning_tokens=reasoning_tokens,
        reasoning_token_source=source,
        content_chars=len(reply.content or ""),
        reasoning_chars=len(reply.reasoning or ""),
        finish_reason=turn["finish_reason"],
        truncated=turn["finish_reason"] == FINISH_TRUNCATED,
        retries=seam.meter.retries,
        tokens_per_second=tokens_per_second,
    )


CallFn = Callable[[WorkerConfig, CallSpec], CallRecord]


# ── one batch: width-many calls dispatched concurrently ───────────────────────


def _run_batch(
    config: WorkerConfig,
    specs: Sequence[CallSpec],
    *,
    call_fn: CallFn,
    timeout: float = BATCH_WAIT_TIMEOUT_SECONDS,
) -> tuple[list[CallRecord], float]:
    """Dispatch every spec concurrently; return ``(records, batch_wall_clock_seconds)``.

    Termination, by construction: no ``while`` anywhere in this module, one
    :func:`concurrent.futures.wait` with a finite *timeout*, and
    ``shutdown(wait=False, cancel_futures=True)`` in a ``finally`` so teardown
    never joins a call that is still running. A future that misses the
    deadline is recorded as a timeout and its result is never read — matching
    `examples/orchestrator_tools.py`'s fan-out discipline for the same reason.

    ``records`` comes back in *submission* order (spec order), not completion
    order: a report whose shape depends on the scheduler is not a report.
    """
    if not specs:
        return [], 0.0

    pool = ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix=THREAD_NAME_PREFIX)
    pending: dict[Future[CallRecord], CallSpec] = {}
    started = time.monotonic()
    try:
        for spec in specs:
            pending[pool.submit(call_fn, config, spec)] = spec
        _done, absent = wait(list(pending), timeout=timeout)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - started

    records: list[CallRecord] = []
    for future, spec in pending.items():
        if future in absent:
            records.append(_failed_record(spec, error="timeout", elapsed=timeout))
        else:
            records.append(future.result(timeout=0))
    return records, elapsed


def _build_specs(width: int, batch: int, prompt_cursor: "itertools.count[int]") -> list[CallSpec]:
    specs = []
    for slot in range(width):
        prompt_id = next(prompt_cursor) % len(PROMPT_SET)
        specs.append(
            CallSpec(
                width=width,
                batch=batch,
                slot=slot,
                prompt_id=prompt_id,
                prompt=PROMPT_SET[prompt_id],
            )
        )
    return specs


# ── per-width aggregation ──────────────────────────────────────────────────────


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy's default method). Stdlib-only."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    return ordered[lo] * (hi - rank) + ordered[hi] * (rank - lo)


@dataclass
class WidthSummary:
    """Aggregate + per-stream numbers for one concurrency width. See the module
    docstring for why both are reported and never folded into one figure."""

    width: int
    batches: int
    calls: int
    ok: int
    errors: int
    timeouts: int
    truncated: int
    finish_reasons: dict[str, int]
    aggregate_seconds: float
    aggregate_completion_tokens: int
    aggregate_tokens_per_second: Optional[float]
    per_stream_tokens_per_second_mean: Optional[float]
    per_stream_tokens_per_second_median: Optional[float]
    latency_seconds_mean: Optional[float]
    latency_seconds_median: Optional[float]
    latency_seconds_p95: Optional[float]
    effective_concurrency: Optional[float]
    reasoning_tokens_sum: int
    reasoning_tokens_reported: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "batches": self.batches,
            "calls": self.calls,
            "ok": self.ok,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "truncated": self.truncated,
            "finish_reasons": dict(self.finish_reasons),
            "aggregate_seconds": round(self.aggregate_seconds, 3),
            "aggregate_completion_tokens": self.aggregate_completion_tokens,
            "aggregate_tokens_per_second": _round(self.aggregate_tokens_per_second),
            "per_stream_tokens_per_second_mean": _round(self.per_stream_tokens_per_second_mean),
            "per_stream_tokens_per_second_median": _round(self.per_stream_tokens_per_second_median),
            "latency_seconds_mean": _round(self.latency_seconds_mean),
            "latency_seconds_median": _round(self.latency_seconds_median),
            "latency_seconds_p95": _round(self.latency_seconds_p95),
            "effective_concurrency": _round(self.effective_concurrency),
            "reasoning_tokens_sum": self.reasoning_tokens_sum,
            "reasoning_tokens_reported": self.reasoning_tokens_reported,
        }


def _summarize_width(width: int, batches: int, records: Sequence[CallRecord]) -> WidthSummary:
    ok_records = [r for r in records if r.ok]
    errors = [r for r in records if not r.ok]
    timeouts = [r for r in errors if r.error == "timeout"]

    finish_reasons: dict[str, int] = {}
    for record in ok_records:
        key = record.finish_reason or "unknown"
        finish_reasons[key] = finish_reasons.get(key, 0) + 1

    aggregate_completion_tokens = sum(r.completion_tokens or 0 for r in ok_records)
    per_stream = [r.tokens_per_second for r in ok_records if r.tokens_per_second is not None]
    latencies = [r.latency_seconds for r in ok_records if r.latency_seconds is not None]
    per_stream_mean = statistics.fmean(per_stream) if per_stream else None
    reported = [r for r in ok_records if r.reasoning_tokens is not None]

    return WidthSummary(
        width=width,
        batches=batches,
        calls=len(records),
        ok=len(ok_records),
        errors=len(errors),
        timeouts=len(timeouts),
        truncated=sum(1 for r in ok_records if r.truncated),
        finish_reasons=finish_reasons,
        aggregate_seconds=0.0,  # filled in by _summarize_width_run, which has the batch clocks
        aggregate_completion_tokens=aggregate_completion_tokens,
        aggregate_tokens_per_second=None,
        per_stream_tokens_per_second_mean=per_stream_mean,
        per_stream_tokens_per_second_median=statistics.median(per_stream) if per_stream else None,
        latency_seconds_mean=statistics.fmean(latencies) if latencies else None,
        latency_seconds_median=statistics.median(latencies) if latencies else None,
        latency_seconds_p95=_percentile(latencies, 95) if latencies else None,
        effective_concurrency=None,
        reasoning_tokens_sum=sum(r.reasoning_tokens or 0 for r in reported),
        reasoning_tokens_reported=len(reported),
    )


@dataclass
class WidthRun:
    """Everything measured at one concurrency width: every batch's records and wall clock."""

    width: int
    batches: int
    records: list[CallRecord] = field(default_factory=list)
    batch_elapsed_seconds: list[float] = field(default_factory=list)

    def summary(self) -> WidthSummary:
        base = _summarize_width(self.width, self.batches, self.records)
        aggregate_seconds = sum(self.batch_elapsed_seconds)
        aggregate_tps = (
            base.aggregate_completion_tokens / aggregate_seconds if aggregate_seconds > 0 else None
        )
        effective_concurrency = (
            aggregate_tps / base.per_stream_tokens_per_second_mean
            if aggregate_tps is not None and base.per_stream_tokens_per_second_mean
            else None
        )
        base.aggregate_seconds = aggregate_seconds
        base.aggregate_tokens_per_second = aggregate_tps
        base.effective_concurrency = effective_concurrency
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "batches": self.batches,
            "batch_elapsed_seconds": [round(s, 3) for s in self.batch_elapsed_seconds],
            "summary": self.summary().to_dict(),
        }


# ── the operator's claim, checked both readings ────────────────────────────────


def check_operator_claim(width_summary: WidthSummary) -> dict[str, Any]:
    """Compare measured width-14 numbers to the operator's "50 tok/s x 14" figure.

    The figure is ambiguous on its face: 50 tok/s **per stream**, sustained
    across 14 concurrent streams (implying ~700 tok/s aggregate); or 50 tok/s
    as the **aggregate** figure achieved with 14 concurrent streams open. Both
    readings are checked, and both booleans are reported, rather than silently
    picking one interpretation.
    """
    per_stream = width_summary.per_stream_tokens_per_second_mean
    aggregate = width_summary.aggregate_tokens_per_second
    return {
        "claim": (
            f"{OPERATOR_CLAIM_PER_STREAM_TOK_S:.0f} tok/s x {OPERATOR_CLAIM_WIDTH} concurrency"
        ),
        "per_stream_reading_target_tok_s": OPERATOR_CLAIM_PER_STREAM_TOK_S,
        "aggregate_reading_target_tok_s": OPERATOR_CLAIM_AGGREGATE_TOK_S,
        "measured_width": width_summary.width,
        "measured_per_stream_mean_tok_s": _round(per_stream),
        "measured_aggregate_tok_s": _round(aggregate),
        "per_stream_reading_reproduced": (
            per_stream is not None and per_stream >= OPERATOR_CLAIM_PER_STREAM_TOK_S
        ),
        "aggregate_reading_reproduced": (
            aggregate is not None and aggregate >= OPERATOR_CLAIM_AGGREGATE_TOK_S
        ),
    }


# ── the sweep ──────────────────────────────────────────────────────────────────


@dataclass
class SweepResult:
    config: WorkerConfig
    max_tokens: int
    temperature: float
    warmup: Optional[CallRecord]
    widths: list[WidthRun]

    def all_records(self) -> list[CallRecord]:
        records: list[CallRecord] = [] if self.warmup is None else [self.warmup]
        for width_run in self.widths:
            records.extend(width_run.records)
        return records

    def width_summary(self, width: int) -> Optional[WidthSummary]:
        for width_run in self.widths:
            if width_run.width == width:
                return width_run.summary()
        return None

    def to_summary_dict(self) -> dict[str, Any]:
        claim_width = self.width_summary(OPERATOR_CLAIM_WIDTH)
        return {
            "worker": self.config.to_dict(),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "prompt_set_size": len(PROMPT_SET),
            "warmup": self.warmup.to_dict() if self.warmup else None,
            "widths": [width_run.to_dict() for width_run in self.widths],
            "operator_claim": (
                check_operator_claim(claim_width) if claim_width is not None else None
            ),
        }


def run_sweep(
    config: WorkerConfig,
    *,
    widths: Sequence[int] = DEFAULT_WIDTHS,
    batches_per_width: Optional[Mapping[int, int]] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    warmup: bool = True,
    call_fn: Optional[CallFn] = None,
    batch_timeout: float = BATCH_WAIT_TIMEOUT_SECONDS,
) -> SweepResult:
    """Run the whole widths x batches sweep, sequentially across widths.

    ``call_fn`` defaults to :func:`_one_call` bound to ``max_tokens``/
    ``temperature`` (real network); tests inject a fake to exercise the
    scheduling and aggregation logic without a socket.
    """
    resolved_call_fn: CallFn = call_fn or functools.partial(
        _one_call, max_tokens=max_tokens, temperature=temperature
    )
    plan = dict(DEFAULT_BATCHES_PER_WIDTH)
    if batches_per_width:
        plan.update(batches_per_width)

    prompt_cursor = itertools.count()
    warmup_record: Optional[CallRecord] = None
    if warmup:
        warmup_spec = CallSpec(
            width=0, batch=-1, slot=0, prompt_id=0, prompt=PROMPT_SET[0], warmup=True
        )
        warmup_record = resolved_call_fn(config, warmup_spec)

    width_runs: list[WidthRun] = []
    for width in widths:
        batches = plan.get(width, DEFAULT_BATCHES_FALLBACK)
        run = WidthRun(width=width, batches=batches)
        for batch_index in range(batches):
            specs = _build_specs(width, batch_index, prompt_cursor)
            batch_records, elapsed = _run_batch(
                config, specs, call_fn=resolved_call_fn, timeout=batch_timeout
            )
            run.records.extend(batch_records)
            run.batch_elapsed_seconds.append(elapsed)
        width_runs.append(run)

    return SweepResult(
        config=config,
        max_tokens=max_tokens,
        temperature=temperature,
        warmup=warmup_record,
        widths=width_runs,
    )


# ── CLI ────────────────────────────────────────────────────────────────────────


def _parse_widths(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split(",") if part.strip())


def _parse_batches(raw: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        width_str, _, count_str = pair.partition(":")
        result[int(width_str)] = int(count_str)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument(
        "--worker-url",
        default=None,
        help=f"OpenAI-compatible base URL, e.g. {THOR_WORKER_URL_DOCUMENTED}",
    )
    parser.add_argument("--worker-model", default=None, help=f"e.g. {THOR_WORKER_MODEL_DOCUMENTED}")
    parser.add_argument(
        "--widths",
        default=",".join(str(w) for w in DEFAULT_WIDTHS),
        help="comma-separated concurrency widths to sweep",
    )
    parser.add_argument(
        "--batches",
        default=None,
        help="override batches-per-width, e.g. '1:3,2:2,8:2,14:2' (default: built-in)",
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--no-warmup", action="store_true", help="skip the single discarded warm-up call"
    )
    parser.add_argument("--out", default=None, help="write every per-call record as JSONL here")
    parser.add_argument("--summary-out", default=None, help="write the aggregated summary JSON")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    resolution = resolve_worker_config(cli_url=args.worker_url, cli_model=args.worker_model)
    if not resolution.ok:
        for degradation in resolution.degradations:
            print(f"notice: {degradation.code}: {degradation.detail}", file=sys.stderr)
        print(json.dumps(resolution.to_dict(), indent=2))
        return 2

    assert resolution.config is not None  # narrows for readers; ok implies this
    result = run_sweep(
        resolution.config,
        widths=_parse_widths(args.widths),
        batches_per_width=_parse_batches(args.batches) if args.batches else None,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        warmup=not args.no_warmup,
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            for record in result.all_records():
                handle.write(json.dumps(record.to_dict()) + "\n")

    summary = result.to_summary_dict()
    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
