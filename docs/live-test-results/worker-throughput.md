# Worker throughput and effective concurrency — widths 1, 2, 8, 14

**Date:** 2026-07-31 · **Plan:**
[orchestrator-worker-architectures](../plans/2026-07-31-orchestrator-worker-architectures.md),
task t3 · **Depends on:** t2 (`examples/worker_seam.py`, merged) · **Harness:**
`examples/worker_throughput.py` · **Raw:**
[`worker-throughput.jsonl`](worker-throughput.jsonl) (52 rows: 1 warm-up + 51
measured calls), [`worker-throughput-summary.json`](worker-throughput-summary.json)

## Headline answer: the operator's "50 tok/s × 14 concurrency" did NOT reproduce

Neither reading of the figure holds on this rig, measured at width 14:

| reading | target | measured | reproduced? |
|---|---|---|---|
| per-stream (50 tok/s sustained *per stream* at width 14) | 50.0 tok/s | **29.82 tok/s** | **NO** — 60% of target |
| aggregate (14 streams totalling 700 tok/s) | 700.0 tok/s | **268.14 tok/s** | **NO** — 38% of target |

Read plainly: the worker does **not** sustain 50 tok/s per stream once 14
requests are in flight, and the whole rig does **not** deliver 700 tok/s in
aggregate at that width. The number that *does* land near 50 tok/s is the
**single-stream (width 1)** rate — measured at **76.4 tok/s**, comfortably
above 50 — so if "50 tok/s" was meant as a rough single-stream figure it
undersells this rig; it is the "× 14 concurrency" compounding that does not
hold. This is reported as measured, not rounded toward the operator's number
in either direction.

## The rig, stated once so it isn't left implied

The worker (`unsloth/Qwen3.6-35B-A3B-NVFP4`) is served on **Thor**
(`http://thor.tail0be7e0.ts.net:8000/v1`), a different physical box than the
local cortex (spark). **This series' load never contended with the local
cortex** — every call in this measurement dialled Thor exclusively, and
nothing on spark was touched. Whatever ceiling shows up below is Thor's own,
not a shared-box artefact.

## Results table — per-stream and aggregate, never conflated

| width | batches | calls | aggregate tok/s | per-stream tok/s (mean / median) | effective concurrency | latency median / p95 (s) | truncated | errors / timeouts |
|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 3 | **76.13** | 76.43 / 77.56 | 0.996 | 10.87 / 14.82 | 0 | 0 / 0 |
| 2 | 2 | 4 | **102.36** | 64.00 / 64.38 | 1.599 | 10.18 / 13.01 | 0 | 0 / 0 |
| 8 | 2 | 16 | **254.18** | 41.40 / 41.54 | 6.140 | 18.56 / 22.76 | 0 | 0 / 0 |
| 14 | 2 | 28 | **268.14** | 29.82 / 31.53 | 8.993 | 28.34 / 40.78 | 2 (7.1%) | 0 / 0 |

`effective_concurrency = aggregate_tok_s / per_stream_tok_s_mean`. A server
serving `W` truly-independent streams reads `≈ W`; a server that queues
requests behind one decode stream reads `≈ 1` regardless of the width dialled
(both extremes are proven against synthetic data in
`tests/test_worker_throughput.py::TestWidthRunEffectiveConcurrency`, so the
metric's own arithmetic is pinned before it is trusted on real numbers).

**This rig is neither extreme — it is real, partial, sub-linear parallelism,
and it saturates around width 8, not 14.** `effective_concurrency` climbs from
1.0 (width 1, by construction) to 1.6 (width 2, 80% of ideal) to 6.14 (width
8, 77% of ideal) to 8.99 (width 14, 64% of ideal) — a *falling* efficiency
curve. The clearest single number: **going from width 8 to width 14 (+75% more
concurrent requests) bought +5.5% more aggregate throughput** (254.18 →
268.14 tok/s), while per-stream throughput fell 28% (41.40 → 29.82 tok/s),
median latency rose 53% (18.56s → 28.34s), and the **only two truncated
completions in the entire 51-call measured series happened at width 14**. Width
14 is not unsafe on this rig (zero errors, zero timeouts, still finished 26 of
28 calls cleanly) — it is simply not buying what its width implies. A
consumer choosing a fan-out width on this data would get most of the
throughput at width 8 for meaningfully better per-call latency.

## Sustained-load stability

> **SUPERSEDED 2026-08-01 — this section is wrong, and the paragraph below is
> kept verbatim rather than rewritten so the error stays readable.** The
> committed records hold **nine calls with `retries: 1`**, all at width 14 —
> **32% of that width's calls**. The retry path was not unexercised; it fired
> nine times and rescued nine calls. `ok: true` is set *after* a retry
> succeeds, so the flag this paragraph read cannot tell a clean call from a
> rescued one. Consequences, including a **reversed** safety conclusion for
> width 14 and the retry-corrected rate table (per-stream mean 29.82 →
> **37.00**, effective concurrency 8.99 → **7.25**), are in
> [`corrections.md` §10](corrections.md). The document's *headline* —
> saturation near width 8 — is unchanged and strengthened by the correction.

**Zero errors, zero timeouts, zero transport retries, and zero refusals across
all 51 measured calls plus the warm-up** — every one of the 52 committed rows
carries `"ok": true`. `finish_reasons` across the whole series: 50 `stop`, 2
`length` (both at width 14, addressed above). No `WorkerTransportError` fired
at any width, meaning `WorkerSeam`'s bounded retry path (`MAX_TRANSPORT_RETRIES
= 3`, 20s backoff) was never exercised for real — this measurement did not
need it.

This directly addresses the parked risk in the plan
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`'s risk list:
*"the served worker build's stability under sustained x14 load is unknown
until t3 reports; a sibling variant crash-looped on GB10 hardware"*).
**That sibling was a different build (`mmangkad/…`) on a different box** — it
is documented in lobes' catalog as a reason to *watch*, not a prediction about
this deployment, and this task treats it exactly that way. On the evidence
here: `unsloth/Qwen3.6-35B-A3B-NVFP4` on Thor showed no crash-loop signature,
no degraded responses, and no elevated failure rate at width 14 relative to
width 1 across 28 concurrent calls in 2 sequential batches. This is **n=2
batches per high width on one rig on one night** — it rules out an immediate,
obvious crash-loop; it does not certify hours-long sustained load, which this
task was explicitly scoped not to run ("not a stress test that ties up the
box for hours").

## Reasoning tokens vs content tokens

**This endpoint does not report a token-level reasoning/content split.** Every
one of the 52 raw records shows `reasoning_token_source: "absent"` —
`reasoning_tokens_reported: 0` at every width in the summary — meaning neither
`usage.completion_tokens_details.reasoning_tokens` nor the flat
`usage.reasoning_tokens` field ever appeared in a raw response from Thor. This
is recorded as an honest absence (`None`), never folded into a `0`, per
`reasoning_tokens_from`'s own contract (mirrored from
`examples/arch_arms.py`'s function of the same name).

What *is* available every call is the text split (`content` vs `reasoning`
fields, both present since this is a thinking model), and the character-count
proxy confirms the task brief's premise that reasoning spend dominates:

| width | content chars | reasoning chars | reasoning share |
|---|---|---|---|
| 1 | 986 | 12,228 | 92.5% |
| 2 | 1,331 | 10,787 | 89.0% |
| 8 | 5,111 | 48,622 | 90.5% |
| 14 | 8,130 | 90,985 | 91.8% |

Reasoning text is consistently **~90% of every completion's output**,
regardless of width. This is a character proxy, not a token count — stated
explicitly rather than presented as more precise than it is.

## Measurement discipline

- **Warm-up:** one discarded call (`width: 0, batch: -1, "warmup": true` in the
  raw JSONL) ran before width 1 began — 970 completion tokens, 14.76s, 65.7
  tok/s, `finish_reason: stop`. It is excluded from every width's summary and
  from the operator-claim check. **No other call in the sweep was excluded** —
  the first call of every batch counts on purpose, since whether the first
  call of a batch looks different is itself part of what "sustained-load
  stability" means.
- **Fixed, committed prompt set:** eight short, benign, bounded-answer prompts
  (`examples/worker_throughput.py::PROMPT_SET`), cycled continuously across the
  whole sweep (never reset per width) so no two concurrent calls in a batch
  share a prompt and every width sees the same rotation.
- **`max_tokens=1200`**, deliberately *not* `d16`'s 16000 cortex-tool-loop
  floor — these are single bare completions on a two-sentence factual prompt,
  a different workload than the multi-turn arena matches `d16` was measured
  against. 1200 proved generous: 50 of 52 calls finished naturally
  (`finish_reason: stop`, mean completion length well under the cap); only 2
  of 28 width-14 calls hit the ceiling.
- **Batches per width:** 3 at width 1, 2 each at widths 2/8/14
  (`DEFAULT_BATCHES_PER_WIDTH`) — width 1 gets more because it is cheap and is
  the single-stream baseline every other width is measured against; every
  width still runs more than one batch so batch-to-batch drift is visible
  (`batch_elapsed_seconds` is committed per width in the summary JSON, not
  just its sum).
- **Non-streaming completions.** This harness does not parse SSE, so a call's
  `latency_seconds` is queueing/prefill and decode time combined — it cannot
  isolate time-to-first-token. This is why `effective_concurrency` is framed
  as a throughput ratio rather than a queueing-delay measurement, and it is a
  real limit on what this document can say about *why* width 14 plateaus, only
  *that* it does.
- **One fresh `ThroughputSeam` per call, never shared across threads** — no
  seam's `Meter` counters are touched by more than one thread, so there is
  nothing to race.
- **Termination discipline** matches `examples/orchestrator_tools.py`'s
  fan-out (task t4): no `while` anywhere in the harness, one bounded
  `concurrent.futures.wait` per batch (a 300s circuit breaker — never fired
  in this series), `shutdown(wait=False, cancel_futures=True)` in a `finally`.
- **Total live spend, this committed run:** 39,439 completion tokens + 1,117
  prompt tokens across 52 calls, ~202s of pure batch wall clock (summed
  `batch_elapsed_seconds` + warm-up) inside a script run that completed in
  well under 5 minutes end to end. A small set of pre-flight wiring checks
  (~8.5K completion tokens at reduced budgets, not committed) preceded this
  run to confirm the harness dispatched real concurrency before spending the
  full sweep's budget.

## What this means for arm design (t4, t5, t11)

- **Do not size a fan-out's expected throughput off the operator's 50×14
  figure** — it does not hold on this rig. If a fan-out design (`t4`) needs a
  throughput assumption, use the **measured** aggregate at the intended width
  (e.g. ~254 tok/s at width 8, ~268 tok/s at width 14), not a multiplied
  single-stream rate.
- **Width 8 is close to this rig's practical throughput ceiling.** Widths
  above 8 buy little aggregate throughput for a real latency cost. This is a
  reason to prefer width ≤ 8 for latency-sensitive fan-outs, not a hard limit
  — width 14 still completed cleanly with zero errors.
- **The stability risk is closed for a single-night, moderate-load check.**
  The parked crash-loop concern did not reproduce; nothing here should be read
  as certifying multi-hour sustained load, which was out of scope by design.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...
EMBODIMENT_LIVE_RIG=1 uv run python examples/worker_throughput.py \
    --worker-url http://thor.tail0be7e0.ts.net:8000/v1 \
    --worker-model unsloth/Qwen3.6-35B-A3B-NVFP4 \
    --out docs/live-test-results/worker-throughput.jsonl \
    --summary-out docs/live-test-results/worker-throughput-summary.json
```
