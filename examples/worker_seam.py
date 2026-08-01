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

Streaming is the transport (plan task ``t5``, deviation ``d3``)
--------------------------------------------------------------
Every dial through this seam is an SSE stream by default, because four clocks
in this repo were sized against total request time and the worst of them --
``GATEWAY_READ_TIMEOUT``, 600 s, *inside the lobes gateway process* where no
client value can reach it ([lobes-cli#169](https://github.com/agentculture/lobes-cli/issues/169))
-- killed two live calls at 2460 s each for zero tokens. Streaming does not
resize that class of clock, it dissolves it: the gateway sets
``conn.sock.settimeout(read_timeout)`` and then relays chunk by chunk, and a
Python socket timeout applies per operation, so under a stream that constant
stops being a total deadline and becomes an inter-chunk bound. Measured on this
rig (`docs/live-test-results/streaming-probe.md`): chunks flow throughout a
43.45 s think, largest gap **0.124 s**, and the terminal usage chunk still
carries ``finish_reason`` and every token count.

Three properties this module holds, each with a test that would notice its loss:

* **Metering parity.** :meth:`WorkerSeam._post` returns the *same shape* either
  way -- an OpenAI non-streaming completion dict -- so ``parse_completion``,
  :meth:`Meter.record_turn` and every subclass that keeps the raw payload
  (``arch_arms.ArchSeam``, ``worker_throughput.ThroughputSeam``,
  ``worker_scoped_overhead.ScopedSeam``) run byte-identical code on both paths.
  The terminal chunk's ``usage`` object is passed through **verbatim**, so
  whatever counts the server reports survive unaltered.
* **Two-phase bounds, both derived** (claim ``c38``). A queued request
  legitimately receives *nothing* until it is scheduled, so an idle clock
  started at ``t=0`` would kill a healthy request -- the same censoring shape on
  a new clock. Time-to-first-chunk gets a queue-derived bound;
  :data:`STREAM_IDLE_TIMEOUT` is installed on the socket **only after the first
  chunk arrives**. See :func:`derive_first_chunk_timeout`.
* **A died stream is never blindly retried** and never looks like a short turn
  (claim ``c41``). lobes' documented contract is no-retry-once-streaming, and it
  is the right one. A break *before* the first byte of body is a connection
  failure and rides the existing retry ladder; a break *after* it commits a
  record carrying ``stream_died: true`` plus whatever content and reasoning did
  arrive. This is issue #37's lesson applied forward: two different events must
  never reach the reader as the same object.

``stream=False`` restores the previous blocking transport byte for byte -- no
``stream`` key on the wire, no ``stream_options``, the same single
``urlopen``/``read`` -- and is reachable from the CLI as ``--no-stream``.

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
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional

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
    "DEGRADED_STREAM_DIED",
    "DEGRADED_STREAM_USAGE_ABSENT",
    "DEGRADED_STREAM_IDLE_UNENFORCEABLE",
    "WorkerDegradation",
    "WorkerConfig",
    "WorkerConfigResolution",
    "resolve_worker_config",
    "WorkerTransportError",
    "Meter",
    "WorkerSeam",
    "parse_completion",
    "DEFAULT_STREAM",
    "SERVER_MAX_NUM_SEQS",
    "STREAM_PREFILL_ALLOWANCE_SECONDS",
    "STREAM_QUEUE_MARGIN",
    "MEASURED_MAX_INTER_CHUNK_GAP_SECONDS",
    "STREAM_FIRST_CHUNK_TIMEOUT",
    "STREAM_IDLE_TIMEOUT",
    "STREAM_TOTAL_TIMEOUT",
    "TRANSPORT_STREAM",
    "TRANSPORT_BLOCKING",
    "FINISH_STREAM_DIED",
    "derive_first_chunk_timeout",
    "StreamBounds",
    "StreamResult",
    "read_sse",
    "read_timeout_setter",
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

#: The retry backoff — **exempt from the budget bound, with a reason** (plan
#: task t2, finding 3). A backoff bounds no token budget: ``max_tokens / tok/s``
#: answers "how long may generating take", while this answers "how long should
#: we wait for a transient condition to clear", and nothing committed in this
#: repo measures that. A derived-looking number here would be an invented one.
#:
#: What is NOT exempt is the accounting, and this is the constant that taught
#: the lesson. ``started`` below is set before the attempt loop and never reset,
#: so every second of this sleep is timed into the call's own latency. It has
#: corrupted three measurements: nine calls in `worker-throughput.jsonl`
#: (corrections.md §10), the cortex rate the pre-registration first published
#: (§5), and t8's scoped-overhead probe, where it is 20x the median healthy
#: scoped call and would have published concurrency making calls *slower*. The
#: contamination is recoverable only because ``Meter.retries`` is recorded
#: beside the clock: overhead is exactly ``retries x (REQUEST_TIMEOUT +
#: RETRY_SLEEP_SECONDS)``. `tests/test_timeout_bounds.py` pins that record
#: field, so removing it fails rather than silently ruining every rate.
RETRY_SLEEP_SECONDS = 20.0

#: **Derived, never chosen** — issue #42's rule, at the slowest model this
#: constant fronts.
#:
#: ``bound = max over every (model, max_tokens) pair on this wire of
#: max_tokens / rate + queue allowance``, with every rate read from
#: ``docs/live-test-results/timeout-rate-measurements.json`` and recomputed by
#: `tests/test_timeout_bounds.py`. This seam is not the worker's alone:
#: `examples/arch_arms.py`'s ``ArchSeam`` subclasses it, so the **cortex**
#: rides the same clock in arms E/M/H, the **worker** in W/M/H, and
#: `examples/arch_hive.py` and `examples/worker_throughput.py` add two more
#: budgets. The binding pair is the worker at the 16000-token ``d16`` budget:
#: 16000 / 12.921 tok/s = 1238.3 s. The cortex at the same budget bounds lower
#: (745.9 s of generation plus the 179.3 s measured queue allowance).
#:
#: **Queue time is inside this bound.** The rule's right-hand side covers
#: generation only, and one committed call spent 179.3 s queued and prefilling
#: (corrections.md §9). For the cortex that allowance is added; for the worker
#: it is *absorbed*, because the worker's cited rate is a wall-clock floor that
#: already contains queue and prefill — and the test checks that absorption
#: numerically rather than taking the claim on trust.
#:
#: **What this bound does not cover:** the **senses** role. `arch_vision.py`
#: dials it through this same seam at a 16000-token budget and no committed
#: record times that model, so the gap is declared in the rate config rather
#: than papered over with a plausible stand-in.
#:
#: Raised 300.0 -> 1300.0. Pre-registration amendment 1 raised it to 1200.0 on
#: branch ``owa/t12``, deriving at the *cortex* rate alone; that value is below
#: the worker's own bound on the same wire. See corrections.md §11.
REQUEST_TIMEOUT = 1300.0

#: league's own word (reused here) for a completion that ran out of budget
#: mid-thought — an INSTRUMENT event, never a reasoning failure.
FINISH_TRUNCATED = "length"

#: How much of one turn's own words is kept verbatim in a committed transcript.
TRANSCRIPT_CONTENT_CHARS = 4000
TRANSCRIPT_REASONING_CHARS = 2000


# ── streaming: the transport, and the two clocks it replaces one with ─────────

#: Which transport produced a record. Written into every transcript entry so a
#: committed artifact says what instrument made it — the ``d16`` line, applied
#: forward instead of retrofitted onto results that cannot be re-run.
TRANSPORT_STREAM = "sse"
TRANSPORT_BLOCKING = "blocking"

#: **Deviation ``d3``: streaming is the default for every cortex/worker dial.**
#: The plan had it flag-gated and off; that shape was written before the rig
#: could be probed. Landing it after the remaining measured cells would mean
#: those cells run on the transport that has censored this repo four times.
DEFAULT_STREAM = True

#: A completion that stopped because its transport broke mid-body, never
#: because the model had finished. Distinct from every server-sent
#: ``finish_reason`` on purpose (claim ``c41``): the boolean ``stream_died``
#: carries the fact, and this sentinel makes it visible in the one field a
#: reader already counts.
FINISH_STREAM_DIED = "stream-died"

DEGRADED_STREAM_DIED = "stream-died"
DEGRADED_STREAM_USAGE_ABSENT = "stream-usage-absent"
DEGRADED_STREAM_IDLE_UNENFORCEABLE = "stream-idle-bound-unenforceable"

#: ``--max-num-seqs`` on this rig's vLLM: how many sequences the server admits
#: concurrently. **A deployment fact, not a choice** — it is recorded in
#: ``docs/live-test-results/timeout-rate-measurements.json`` under the cortex
#: role's ``condition`` (visible in the server's own ``ps`` line), and
#: `tests/test_timeout_bounds.py` asserts this constant equals it, so the two
#: cannot drift apart.
SERVER_MAX_NUM_SEQS = 2

#: The measured seconds a request spends **not generating** — queue wait plus
#: prompt processing — cited from
#: ``docs/live-test-results/timeout-rate-measurements.json``'s
#: ``non_generation_allowance`` (179.3 s, `corrections.md` §9). Under a stream
#: this is the part of the wait that happens *after* the request is scheduled
#: and *before* the first chunk can exist, so it is added to the queue model
#: rather than folded into it.
STREAM_PREFILL_ALLOWANCE_SECONDS = 179.3

#: **The stated margin on the queue model.** The model below counts only the
#: sequences the *server* admits; it does not model a harness that dials several
#: of its own requests at once. ×2 buys one extra full neighbouring generation,
#: which covers a second tenant on the gateway or a harness dialling at width
#: ≤ 3. Wider dials must not rely on it: they pass ``stream_queue_width=`` and
#: :func:`derive_first_chunk_timeout` recomputes. Chosen, not derived — and
#: therefore named, like the retry backoff's exemption, rather than buried in an
#: expression.
STREAM_QUEUE_MARGIN = 2.0

#: The largest gap between chunks anyone has measured on this rig: 0.124 s,
#: across a 43.45 s think of 390 chunks (`docs/live-test-results/stream-probe.json`,
#: dialled 2026-08-01 against an idle cortex). Retyped here so the derivation
#: below reads in one place; `tests/test_timeout_bounds.py` asserts it equals
#: the committed probe record, so it cannot drift from the measurement it cites.
MEASURED_MAX_INTER_CHUNK_GAP_SECONDS = 0.124


def derive_first_chunk_timeout(
    *,
    dialled_width: int = 1,
    per_request_bound_s: Optional[float] = None,
    admitted: int = SERVER_MAX_NUM_SEQS,
    non_generation_s: float = STREAM_PREFILL_ALLOWANCE_SECONDS,
    margin: float = STREAM_QUEUE_MARGIN,
) -> float:
    """Seconds a request may legitimately receive **nothing at all**.

    Phase one of the two-phase bound claim ``c38`` requires, and the phase the
    streaming probe explicitly **could not** discharge: both of its dials ran
    against an idle cortex, so its ~0.25 s time-to-first-chunk says nothing
    about a queued request. This bound is therefore **derived from the queue
    model**, not measured, and says so.

    The model. The server admits ``admitted`` concurrent sequences, so a request
    arriving into a full server waits for one of them to finish: ``admitted - 1``
    generations ahead of it. A harness dialling ``dialled_width`` requests at
    once queues the surplus behind its own siblings, and the drain is counted
    serially rather than ``admitted`` at a time — that over-counts, which is the
    safe direction for a clock whose failure mode is cutting a healthy request.
    Each of those waits is bounded by ``per_request_bound_s``, the derived
    per-request bound already enforced on this wire (:data:`REQUEST_TIMEOUT`,
    itself ``max_tokens / slowest measured tok/s`` per issue #42). Once
    scheduled, the request still spends ``non_generation_s`` in prompt
    processing before a first token can exist. The whole thing carries
    :data:`STREAM_QUEUE_MARGIN`.

    :param dialled_width: how many requests the caller has in flight at once.
    :param per_request_bound_s: the per-request bound; :data:`REQUEST_TIMEOUT`
        read at call time (not bound at definition time, so a test that
        monkeypatches the constant moves this with it).
    """
    if dialled_width < 1:
        raise ValueError(f"dialled_width must be at least 1, got {dialled_width!r}")
    if admitted < 1:
        raise ValueError(f"admitted must be at least 1, got {admitted!r}")
    bound = REQUEST_TIMEOUT if per_request_bound_s is None else per_request_bound_s
    waiters = (admitted - 1) + max(dialled_width - admitted, 0)
    return margin * (waiters * bound + non_generation_s)


#: **Derived, never chosen** — phase one, at the serial cortex/worker lane's
#: width of 1. ``STREAM_QUEUE_MARGIN × ((SERVER_MAX_NUM_SEQS - 1) ×
#: REQUEST_TIMEOUT + STREAM_PREFILL_ALLOWANCE_SECONDS)``: one neighbouring
#: generation at the derived per-request bound, plus the measured queue/prefill
#: allowance, all from
#: ``docs/live-test-results/timeout-rate-measurements.json``. This clock fronts
#: the same roles ``REQUEST_TIMEOUT`` does — **worker** and **cortex** through
#: `arch_arms.py`'s ``ArchSeam`` and `arch_hive.py`, and the unmeasured
#: **senses** role through `arch_vision.py` — because it is the same seam.
#: Queue time is what this bound is *for*: it is the phase in which queueing
#: happens, and it is measured from the start of the call rather than per
#: attempt, so a retry ladder cannot multiply it.
#:
#: The accepted cost, stated rather than discovered later: this is also the
#: connect+headers bound, so a black-holed host now takes this long to fail
#: instead of ``REQUEST_TIMEOUT``. A refused connection and a DNS failure still
#: return immediately; `tests/test_timeout_bounds.py` checks the exhausted
#: ladder still fits inside the fan-out deadline, so a dead transport still
#: reports as a transport failure rather than as ``fanout-unit-absent``.
STREAM_FIRST_CHUNK_TIMEOUT = derive_first_chunk_timeout()

#: **Derived, never chosen** — phase two, and the clock that replaces the
#: total-request deadline. Sized from the measured chunk cadence with a stated
#: margin, and cross-checked against the committed rates rather than resting on
#: one probe:
#:
#: * directly, ``MEASURED_MAX_INTER_CHUNK_GAP_SECONDS`` is 0.124 s -> **484×**;
#: * from ``docs/live-test-results/timeout-rate-measurements.json``, the slowest
#:   per-stream rate any role was measured at (the muse, 5.17 tok/s) implies a
#:   mean inter-token interval of 0.193 s -> **310×**. That reading matters
#:   because the probe ran on an idle cortex while the committed rates were
#:   measured under contention, where a stream is interleaved with its
#:   neighbours and its own cadence halves.
#:
#: This clock is installed on the socket **only after the first chunk arrives**,
#: so queue wait is never charged as idle. It fronts the same worker / cortex /
#: senses roles as the constants above; the bound does not depend on the budget
#: (an idle gap is not a generation) so ``max_tokens`` enters only through the
#: outer :data:`STREAM_TOTAL_TIMEOUT` backstop.
#:
#: **What nothing measures, recorded rather than assumed:** vLLM may *preempt* a
#: running sequence when the KV pool is exhausted — the ceiling
#: `worker-throughput.md` hit at width 14 — and no committed record times how
#: long a preempted sequence stalls. A preemption longer than this bound would
#: be recorded as a died stream: visible, never retried, never silently folded
#: into a result. Re-derive if a wide dial starts reporting deaths.
STREAM_IDLE_TIMEOUT = 60.0

#: **Derived, never chosen** — the outer backstop the deviation instruction
#: keeps: the two phases summed. A stream that dribbles one chunk just inside
#: the idle bound forever trips neither phase clock, so the total is checked in
#: software after every chunk. ``STREAM_FIRST_CHUNK_TIMEOUT + REQUEST_TIMEOUT``:
#: the queue phase's bound plus the generation phase's, each already derived
#: from ``docs/live-test-results/timeout-rate-measurements.json`` at the worker
#: and cortex budgets. It carries no margin of its own — it inherits the queue
#: phase's ``STREAM_QUEUE_MARGIN`` and nothing else, deliberately, because a
#: margin on a sum of two already-margined terms is a number with no argument
#: behind it. The queue allowance appears in both terms; that double-count is in
#: the safe direction and is left rather than tuned away.
STREAM_TOTAL_TIMEOUT = STREAM_FIRST_CHUNK_TIMEOUT + REQUEST_TIMEOUT


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


# ── the SSE reader ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamBounds:
    """The three derived clocks one streamed call runs under.

    Held as a value rather than read from module globals inside the reader so
    that a test can drive every boundary condition without monkeypatching the
    constants the CI bound walk reads.
    """

    first_chunk_s: float
    idle_s: float
    total_s: float

    @classmethod
    def derived(cls, *, dialled_width: int = 1) -> "StreamBounds":
        """The shipped bounds, re-derived for a caller's actual dial width."""
        first = derive_first_chunk_timeout(dialled_width=dialled_width)
        return cls(first_chunk_s=first, idle_s=STREAM_IDLE_TIMEOUT, total_s=first + REQUEST_TIMEOUT)


@dataclass(frozen=True)
class StreamResult:
    """One consumed stream: the payload it reassembles to, and what went wrong.

    ``payload`` is an OpenAI **non-streaming** completion dict — the same shape
    :meth:`WorkerSeam._post` returns when streaming is off — so nothing
    downstream of the transport has to know which transport ran.
    """

    payload: dict[str, Any]
    degradations: tuple[WorkerDegradation, ...]
    chunks: int
    first_chunk_seconds: Optional[float]
    max_gap_seconds: float
    died: bool
    death_reason: str


#: SSE frames are ``data: {...}`` lines; the terminal marker is a literal.
_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"


def _iter_sse_lines(response: Any) -> Iterator[bytes]:
    """Line-at-a-time over a streaming response. Isolated so tests can hand in a list."""
    return iter(response)


def read_timeout_setter(response: Any) -> Optional[Callable[[float], None]]:
    """A way to change the read timeout of an already-open response, or ``None``.

    Phase two of the bound is a *socket* timeout, because a bound that only
    fires when the next chunk happens to arrive cannot detect a stall — the one
    thing it exists to detect. ``urlopen`` takes a single timeout for the whole
    connection, so the switch has to happen on the live socket.

    Three sources, in order: an explicit ``set_read_timeout`` hook (what a test
    double provides, and a documented seam rather than a monkeypatch); the
    socket underneath an ``http.client`` response; otherwise ``None``, which the
    caller records as a degradation rather than pretending the bound is armed.
    """
    explicit = getattr(response, "set_read_timeout", None)
    if callable(explicit):
        return explicit
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        return settimeout
    return None


def read_sse(
    lines: Iterable[bytes],
    *,
    bounds: StreamBounds,
    now: Callable[[], float] = time.monotonic,
    started: Optional[float] = None,
    set_read_timeout: Optional[Callable[[float], None]] = None,
) -> StreamResult:
    """Consume an SSE completion stream into a non-streaming completion payload.

    The two-phase bound, as code:

    * **Before the first chunk** nothing has arrived, so nothing is idle. The
      caller has already opened the socket with ``bounds.first_chunk_s``; a stall
      here raises, which lets the seam's existing retry ladder handle it — the
      body never started, so lobes' no-retry-once-streaming contract does not
      apply yet.
    * **After the first chunk** ``bounds.idle_s`` goes on the socket. A stall now
      is a **died stream**: recorded, kept with whatever partial content and
      reasoning arrived, and *never* retried.
    * ``bounds.total_s`` is checked in software after every chunk, because a
      stream that dribbles one chunk just inside the idle bound forever trips
      neither socket clock.

    Two field-name facts this function exists to get right, both measured:

    * the reasoning delta on this rig is ``delta.reasoning``, **not** vLLM's
      documented ``delta.reasoning_content``. A client written against the
      documented name reports zero reasoning and nothing flags it
      (`docs/live-test-results/streaming-probe.md` §3). Both are accepted.
    * tool-call arguments arrive as **fragments** keyed by ``index`` and must be
      concatenated; a reader that takes the last fragment silently produces
      unparseable JSON, which ``parse_completion`` then degrades to ``{}`` — a
      tool call that quietly loses its arguments.
    """
    started = now() if started is None else started
    last_at = started
    first_chunk_at: Optional[float] = None
    max_gap = 0.0
    chunks = 0

    content: list[str] = []
    reasoning: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: Optional[str] = None
    usage: Optional[dict[str, Any]] = None
    header: dict[str, Any] = {}
    unparsed = 0
    saw_done = False
    death_reason = ""

    iterator = iter(lines)
    while True:
        try:
            raw = next(iterator)
        except StopIteration:
            break
        except (TimeoutError, OSError) as stall:
            if first_chunk_at is None:
                # The body never started. This is a connection failure, not a
                # died stream, and it is the caller's retry ladder's business.
                raise
            death_reason = f"{type(stall).__name__}: {stall}"
            break

        at = now()
        if first_chunk_at is None:
            first_chunk_at = at - started
            if set_read_timeout is not None:
                # The idle bound is armed HERE and nowhere earlier. Queue wait
                # is never charged as idle because the clock that would charge
                # it does not exist until this line runs.
                set_read_timeout(bounds.idle_s)
        else:
            max_gap = max(max_gap, at - last_at)
        last_at = at
        chunks += 1

        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = text.strip()
        if line and line.startswith(_SSE_DATA_PREFIX):
            frame_text = line[len(_SSE_DATA_PREFIX) :].strip()
            if frame_text == _SSE_DONE:
                saw_done = True
                break
            try:
                frame = json.loads(frame_text)
            except ValueError:
                unparsed += 1
                frame = None
            if isinstance(frame, dict):
                for key in ("id", "created", "model", "system_fingerprint"):
                    if key in frame and key not in header:
                        header[key] = frame[key]
                if isinstance(frame.get("usage"), dict):
                    usage = frame["usage"]
                for choice in frame.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content.append(str(delta["content"]))
                    # `reasoning` is what this rig sends; `reasoning_content` is
                    # what vLLM documents. Take either.
                    for name in ("reasoning", "reasoning_content"):
                        if delta.get(name):
                            reasoning.append(str(delta[name]))
                            break
                    for fragment in delta.get("tool_calls") or []:
                        if not isinstance(fragment, dict):
                            continue
                        blank = {"name": "", "arguments": ""}
                        slot = tool_calls.setdefault(
                            int(fragment.get("index") or 0),
                            {"id": "", "type": "function", "function": blank},
                        )
                        if fragment.get("id"):
                            slot["id"] = str(fragment["id"])
                        function = fragment.get("function") or {}
                        if function.get("name"):
                            slot["function"]["name"] += str(function["name"])
                        if function.get("arguments"):
                            slot["function"]["arguments"] += str(function["arguments"])

        if at - started > bounds.total_s:
            death_reason = (
                f"the derived total bound of {bounds.total_s:.0f} s elapsed with the "
                "stream still delivering"
            )
            break

    clean = saw_done or finish_reason is not None
    died = not clean
    if died and not death_reason:
        death_reason = "the response body ended without a terminal frame"

    degradations: list[WorkerDegradation] = []
    if died:
        degradations.append(
            WorkerDegradation(
                DEGRADED_STREAM_DIED,
                f"{death_reason}; keeping {len(''.join(content))} content and "
                f"{len(''.join(reasoning))} reasoning characters. NOT retried: a stream "
                "whose body has started is never re-run (lobes' no-retry-once-streaming "
                "contract), so a partial turn is surfaced rather than silently repeated.",
            )
        )
    if usage is None:
        degradations.append(
            WorkerDegradation(
                DEGRADED_STREAM_USAGE_ABSENT,
                "no terminal usage chunk arrived, so this call contributes no token "
                "counts. stream_options.include_usage was requested; a server that "
                "ignores it un-measures the rig (claim c37) and streaming must go off "
                "for measured lanes until it does not.",
            )
        )

    message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    joined_reasoning = "".join(reasoning)
    if joined_reasoning:
        # `reasoning`, and only `reasoning`. Checked on this rig 2026-08-01
        # rather than assumed: a non-streamed completion here returns message
        # keys `['annotations', 'audio', 'content', 'function_call', 'reasoning',
        # 'refusal', 'role']` — the same name the deltas use, and NOT vLLM's
        # documented `reasoning_content`. Emitting both would make the
        # reassembled payload a superset of the shape it is supposed to be
        # indistinguishable from. Every reader in this repo accepts either name,
        # so a rig that flips to the documented one still parses.
        message["reasoning"] = joined_reasoning
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]

    payload: dict[str, Any] = dict(header)
    payload["object"] = "chat.completion"
    payload["choices"] = [
        {
            "index": 0,
            "message": message,
            "finish_reason": finish_reason or (FINISH_STREAM_DIED if died else ""),
        }
    ]
    payload["usage"] = usage or {}
    # Client-minted, never server-sent, and always present under streaming so a
    # reader never has to infer a death from an absence (claim c41).
    payload["stream_died"] = died
    payload["stream_unparsed_frames"] = unparsed

    return StreamResult(
        payload=payload,
        degradations=tuple(degradations),
        chunks=chunks,
        first_chunk_seconds=first_chunk_at,
        max_gap_seconds=max_gap,
        died=died,
        death_reason=death_reason,
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
    #: Turns whose transport broke mid-body. A TRANSPORT event, and never the
    #: same object as a short turn — issue #37's lesson, one layer up.
    stream_deaths: int = 0
    #: Streamed turns that arrived with no terminal usage chunk, so contributed
    #: no token counts. Counted rather than inferred from a zero.
    stream_usage_absent: int = 0
    finish_reasons: dict[str, int] = field(default_factory=dict)
    #: One entry per completed model turn — the raw transcript.
    transcript: list[dict[str, Any]] = field(default_factory=list)
    #: Every structured degradation this seam recorded (C3: nothing degrades
    #: silently, and the host reads it from the artifact rather than stderr).
    degradations: list[dict[str, Any]] = field(default_factory=list)

    def record_turn(
        self,
        reply: ModelResponse,
        *,
        finish_reason: str,
        seconds: float,
        messages: int,
        transport: str = TRANSPORT_BLOCKING,
        stream_died: bool = False,
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
                # Both fields are present on EVERY record, streamed or not.
                # `transport` names the instrument that produced the numbers
                # (the d16 line); `stream_died` is always a boolean so a reader
                # never distinguishes a died stream from a short turn by an
                # absence (claim c41).
                "transport": transport,
                "stream_died": stream_died,
            }
        )

    def record_degradation(self, degradation: WorkerDegradation) -> None:
        self.degradations.append(degradation.to_dict())

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
            "stream_deaths": self.stream_deaths,
            "stream_usage_absent": self.stream_usage_absent,
            "finish_reasons": dict(self.finish_reasons),
            "degradations": list(self.degradations),
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
        stream: bool = DEFAULT_STREAM,
        stream_queue_width: int = 1,
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
        self.stream = stream
        #: How many requests the caller has in flight at once. Only the queue
        #: phase depends on it, and the default is this repo's serial lane.
        self.stream_queue_width = stream_queue_width
        self.stream_bounds = StreamBounds.derived(dialled_width=stream_queue_width)
        #: Set by :meth:`__call__` before the attempt loop. The time-to-first-
        #: chunk bound is measured from the START OF THE CALL rather than per
        #: attempt: a connection that failed and reconnected is still waiting in
        #: the same queue, so a retry ladder must not multiply the queue bound.
        self._call_started: Optional[float] = None

    @property
    def transport(self) -> str:
        return TRANSPORT_STREAM if self.stream else TRANSPORT_BLOCKING

    def _degrade(self, degradation: WorkerDegradation) -> None:
        """Record a degradation on the meter and say so once, on stderr (C3)."""
        self.meter.record_degradation(degradation)
        print(
            f"notice: {self.meter.role}: {degradation.code}: {degradation.detail}",
            file=sys.stderr,
        )

    def _request(self, body: dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed anywhere.
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

    def _open(self, request: urllib.request.Request, *, timeout: float) -> Any:
        """The one audited dial. Both transports go through it."""
        # The scheme is pinned to http(s) in __init__ and the endpoint is the
        # operator's own --worker-url; audited once, here.
        return urllib.request.urlopen(request, timeout=timeout)  # nosec B310

    def _post_stream(self, body: dict[str, Any]) -> dict[str, Any]:
        """One SSE round trip, reassembled into a non-streaming completion payload."""
        bounds = self.stream_bounds
        started = time.monotonic() if self._call_started is None else self._call_started
        remaining = bounds.first_chunk_s - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError(
                f"{self.meter.role}: the derived time-to-first-chunk bound of "
                f"{bounds.first_chunk_s:.0f} s elapsed across this call's attempts before "
                "any chunk arrived"
            )
        with self._open(self._request(body), timeout=remaining) as response:
            setter = read_timeout_setter(response)
            if setter is None:
                self._degrade(
                    WorkerDegradation(
                        DEGRADED_STREAM_IDLE_UNENFORCEABLE,
                        "this response exposes no socket, so the inter-chunk idle bound "
                        f"of {bounds.idle_s:.0f} s cannot be armed; the call stays bounded "
                        f"by the {bounds.first_chunk_s:.0f} s read timeout and the "
                        f"{bounds.total_s:.0f} s total, which are looser",
                    )
                )
            result = read_sse(
                _iter_sse_lines(response),
                bounds=bounds,
                started=started,
                set_read_timeout=setter,
            )
        for degradation in result.degradations:
            self._degrade(degradation)
        return result.payload

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("stream"):
            return self._post_stream(body)
        request = self._request(body)
        with self._open(request, timeout=REQUEST_TIMEOUT) as response:
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
        if self.stream:
            # `include_usage` is what keeps metering alive across the transport
            # change (claim c37): without it a stream carries no token counts at
            # all and the rig is un-measured. Verified in lobes source that the
            # gateway rewrites only `model` and relays the body verbatim, so the
            # option survives the proxy — and measured on this rig, where the
            # terminal chunk arrived with finish_reason and every count.
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}

        started = time.monotonic()
        self._call_started = started
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
            # A died stream returns here rather than raising, and that is the
            # no-retry contract expressed structurally: control never re-enters
            # the attempt loop, so nothing can re-run a body that has already
            # started. `_degrade` has already announced it and put it on the
            # meter; this counts it and stamps the record.
            stream_died = bool(payload.get("stream_died"))
            if stream_died:
                self.meter.stream_deaths += 1
            if self.stream and not payload.get("usage"):
                self.meter.stream_usage_absent += 1
            if not reply.content and not reply.tool_calls:
                self.meter.empty_content += 1
            self.meter.record_turn(
                reply,
                finish_reason=reason,
                seconds=elapsed,
                messages=len(messages),
                transport=self.transport,
                stream_died=stream_died,
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
    config: WorkerConfig, *, max_tokens: int, temperature: float, stream: bool = DEFAULT_STREAM
) -> dict[str, Any]:
    """One completion, no tool schema on the wire. The reachability check."""
    seam = WorkerSeam(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        role="worker-bare",
        max_tokens=max_tokens,
        temperature=temperature,
        stream=stream,
    )
    messages = [{"role": "user", "content": BARE_PROMPT}]
    reply = seam(messages)
    return {
        "kind": "bare-completion",
        "prompt": BARE_PROMPT,
        "transport": seam.transport,
        "content": reply.content,
        "cost": seam.meter.to_dict(),
        "transcript": seam.meter.transcript,
    }


def run_tool_loop(
    config: WorkerConfig,
    *,
    max_tokens: int,
    temperature: float,
    max_steps: int,
    stream: bool = DEFAULT_STREAM,
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
        stream=stream,
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
        "transport": seam.transport,
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
    stream: bool = DEFAULT_STREAM,
) -> dict[str, Any]:
    """Both smoke calls, bundled with the config they ran under.

    ``max_tokens=16000`` is d16's measured floor for this rig's thinking
    models (`docs/live-test-results/arena-budget.md`): a lower cap risks
    silently truncating the worker's own reasoning before it ever reaches the
    ``add``/``finish`` calls, which would make a wiring smoke fail for a budget
    reason and be misread as a wiring reason.

    ``transport`` and ``stream_bounds`` ride in the report because a latency
    figure is not comparable across the transport change and the artifact has to
    say which one produced it.
    """
    bounds = StreamBounds.derived()
    return {
        "kind": "worker-smoke",
        "note": "instrument check, not data",
        "worker": config.to_dict(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "max_steps": max_steps,
        "transport": TRANSPORT_STREAM if stream else TRANSPORT_BLOCKING,
        "stream_bounds": {
            "first_chunk_seconds": round(bounds.first_chunk_s, 1),
            "idle_seconds": bounds.idle_s,
            "total_seconds": round(bounds.total_s, 1),
        },
        "bare_completion": run_bare_completion(
            config, max_tokens=max_tokens, temperature=temperature, stream=stream
        ),
        "tool_loop": run_tool_loop(
            config,
            max_tokens=max_tokens,
            temperature=temperature,
            max_steps=max_steps,
            stream=stream,
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
    parser.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        default=DEFAULT_STREAM,
        help=(
            "dial with the previous blocking transport instead of SSE. Streaming is the "
            "default (deviation d3); this restores the pre-t5 behaviour byte for byte — "
            "no stream key on the wire, one request-scoped clock — and is the escape "
            "hatch if a backend drops the terminal usage chunk (claim c37)"
        ),
    )
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
        stream=args.stream,
    )
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
