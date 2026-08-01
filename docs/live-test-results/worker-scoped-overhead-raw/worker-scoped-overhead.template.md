<!-- ASSEMBLED by worker-scoped-overhead-raw/render-doc.py from this template plus
     generated tables. Edit the TEMPLATE, never the assembled document. -->
# Worker tiny-call overhead — what a scoped B1 call actually costs

**Date:** 2026-08-01 · **Plan:**
[error-derived-timeouts-bee-hive-architecture](../plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md),
task **t8** · **Covers:** `c43`, `h29` · **Harness:**
`examples/worker_scoped_overhead.py` · **Guard:**
[`thor-idle-guard.py`](worker-scoped-overhead-raw/thor-idle-guard.py) · **Raw:**
[`worker-scoped-overhead.jsonl`](worker-scoped-overhead.jsonl) (141 rows: 1
warm-up + 140 measured calls),
[`worker-scoped-overhead-summary.json`](worker-scoped-overhead-summary.json)

This is a **pre-measurement**. It ran before the B0/B1/B2 sweep fixed its call
granularity, so that the sweep can cite a number instead of a hope.

## The claim under test

The Bee-Hive's B1 tier argues scoped worker calls are cheap:

> the cortex spends 5,000–14,000 tokens on a turn; a scoped worker call spends
> tens. At 76.4 tok/s and ~9-way concurrency, N scoped calls cost less wall
> clock than one cortex turn.

Both cited numbers come from
[`worker-throughput.md`](worker-throughput.md) — **and that series measured
1200-token completions.** A B1 call is *tens* of tokens. In that regime the
per-call costs that do not scale with completion length (queueing, prefill of
repeated context, request framing, network) stop being a rounding error, and
the concurrency figure has no reason to transfer unexamined. Nobody had
measured it.

## Headline: the concurrency figure does NOT transfer, and the metric that says it does is the trap

| reading | 1200-token reference | measured on tens-of-token calls | transfers? |
|---|---|---|---|
| **calls answered per second, width 8 vs width 1** — what a sweep actually buys | 6.14× (width 8), ~9× (width 14) | **3.44×** lean context, **1.83×** realistic context | **NO** — 43% / 23% of ideal |
| `effective_concurrency` (aggregate tok/s ÷ per-stream tok/s) — the metric the old series published | 6.14 | 7.48 / 7.56 | reads *higher*, and is **misleading here** |

*Where these come from:* the **rich** figures are Table 4's; the **lean**
figures are Table 5's retry-free recomputation, because that cell's
as-measured row is contaminated by a single 20 s transport backoff (see
*Corrections* below — the contaminated reading is published too, never
deleted).

Read the second row carefully, because it is the finding a careless sweep would
trip over. `effective_concurrency` is a ratio of **token** throughputs. When a
call is fifteen tokens long, its per-stream tok/s is dominated by overhead
rather than decode, which **collapses the denominator** and inflates the ratio.
Recomputed on this data the metric says the concurrency figure transferred
*better* than at 1200 tokens (transfer ratio 1.22–1.23×). It did not. Measured
in the currency B1 spends — **answers per second** — width 8 buys 3.44× at best
and 1.83× with realistic context.

**The B1 sweep must cite calls/second, not `effective_concurrency`.** The
latter is a valid metric for long completions and an actively deceptive one for
scoped calls.

## What the residual says: at width 8, 90.6% of a scoped call is not its tokens

The residual is the share of a call's wall clock that its completion tokens do
not explain — modelled decode at the committed reference rate subtracted from
measured latency. It climbs from **52.5%** (lean context, width 1) to **90.6%**
(realistic context, width 8).

For contrast, take this probe's own measured per-call overhead and put it under
a 1200-token completion, the length the old series measured: 1200 ÷ 76.43 =
15.70 s of decode, so the same **0.35 s** residual measured at `rich-off-w1`
would be 0.35 ÷ 16.05 = **2.2%** of that call's wall clock — against the
**66.1%** it is here. The overhead did not change between the two series; the
denominator did, by 30× (16.05 s against 0.53 s). That is `c43`, measured. (The arithmetic is pinned against synthetic data in
`tests/test_worker_scoped_overhead.py::TestResidualSeconds` so the metric was
trusted only after its own maths was proved.)

## Prefill is real, and it does not parallelise

Adding ~768 prompt tokens of realistic repeated context (match rules, roster,
map notes, recent history — the preamble a B1 sweep re-sends on every scoped
call) costs:

- **+0.12 s at width 1** — an implied prefill rate of ~6,509 tok/s;
- **+1.29 s at width 8** — an implied rate of ~596 tok/s, **eleven times worse**.

The consequence is the number a granularity choice needs: realistic context
takes answer throughput at width 8 from 8.383 calls/s down to 3.459 calls/s,
a **0.41× throughput ratio**. Concurrency multiplies decode; it does not
multiply prefill, and for a scoped call prefill is most of the bill. At width 8
with realistic context the worker is absorbing roughly 3,487 prompt tokens per
second (3.459 calls/s × 1,008 prompt tokens) to emit about 54 completion
tokens per second.

*Caveat, stated rather than buried:* the shared preamble is byte-identical
across every call by design, so a server-side prefix cache could be absorbing
part of this cost. That makes the measured figure a **lower bound** on what a
sweep whose context varies per call would pay — never an upper one.

## "Tens of tokens" is reachable only with the thinking toggle off

The worker is a thinking model. Whether a scoped answer fits in tens of tokens
turns out to be a property of the **wire keys**, not of the question:

- **`enable_thinking: false`** — 13.7–15.7 completion tokens, **0 truncations**,
  **106 of 106 calls returned a parseable `menu_index=`** (10/10, 10/10, 40/40,
  40/40, 6/6 across every thinking-off cell). Given a 2000-token budget it
  still answers in 15.3 tokens: the cap is not what makes it short.
- **`enable_thinking: true`** — at the same 48-token scoped cap, **0 of 6
  calls produced an answer** and 6 of 6 truncated. Given room to finish it
  spends **1,574.5 completion tokens (102.7×) and 21.6 s (39.1×)** on one
  scoped question, and *still* truncated 2 of 4 at a 2000-token budget.

A thinking model handed a tens-of-token budget reproduces
[#32](https://github.com/agentculture/embodiment/issues/32)'s shape exactly: a
worker that consumes its budget and returns nothing usable. **The B1 sweep must
put `{"chat_template_kwargs": {"enable_thinking": false}}` on every scoped
worker call and assert it on the wire**, not assume it.

## The comparison the sweep asked for: N scoped calls versus one cortex turn

The claim's *direction* survives, comfortably — but the binding budget is not
the one the claim names:

- **By wall clock:** 1,384–2,525 scoped calls fit inside one cortex turn
  (400–730 s). At 0.289 s per call at width 8, a scoped call is cheap in time.
- **By completion tokens:** 319–910 scoped calls fit inside one cortex turn's
  5,000–14,265 completion tokens.
- **By prompt tokens — the constraint nobody costed:** each scoped call also
  carries **1,008 prompt tokens**. Filling one cortex turn's wall clock with
  scoped calls means pushing roughly **1.4 million prompt tokens** through the
  worker. Completion tokens are not the budget that binds B1; prefill is.

## Tables

{{TABLES}}

## The declared idle window (acceptance criterion 3)

A measured series (`owa/t12`, the orchestrator-worker ladder) was climbing on
this rig throughout. Its arms `W`, `M` and `H` dial the same Thor worker this
probe dials; arm `E` is cortex-alone and runs on spark. Contention would have
corrupted both measurements.

**What was checked, by
[`thor-idle-guard.py`](worker-scoped-overhead-raw/thor-idle-guard.py), before
and after every dial**, three independent angles folded pessimistically (any
angle reporting busy makes the verdict busy; `unknown` never votes idle):

1. **Process table** — a running `run-cell.sh <rung> <arm>` / `drive.py --arm
   <arm>` names its arm. Throughout this probe the only cell in flight was
   `C2-E` (`drive.py cell --rung C2 --arm E --max-seconds 10800`, started
   08:05), which never touches Thor.
2. **Ladder decisions** — `ladder-decisions.jsonl` held exactly one rung
   decision: `C1` → `CEILING` via clause `arm-e-first-ceiling`, with `C1-W`,
   `C1-M` and `C1-H` recorded **absent (never dialled)**. No Thor-dialling cell
   had run at all.
3. **Endpoint** — Thor reachable and serving
   `unsloth/Qwen3.6-35B-A3B-NVFP4`.

{{WINDOW}}

**No Thor-dialling cell started mid-probe.** Both runs' `after` checks
(10:59:31 and 11:04:05) returned `idle` with the same cortex-alone cell still
in flight, so no batch needed re-running for contention. The whole live lane
occupied 10:57:12 → 11:04:05 — under seven minutes.

**What this check cannot see, stated rather than implied.** The Thor endpoint is
a **lobes gateway**, not a bare vLLM server: it serves no Prometheus `/metrics`
(verified — `404 not found: /metrics`), so there is **no server-side view of
concurrent request load**. The guard proves this rig's known series was not
dialling Thor and that the endpoint answered; it cannot prove no third party
was dialling it. Server-side load confirmation is **ABSENT**.

## Corrections and instrument events

**One transport retry in 141 calls (0.7%), and it cost 20 seconds.** A single
call (`lean-off-w8`, batch 1, slot 6) failed its first attempt and was retried
by `WorkerSeam`'s bounded retry path. The backoff is a fixed
`RETRY_SLEEP_SECONDS = 20.0` — **20× the median healthy scoped-call latency of
0.98 s**. On a 1200-token call that constant is a perturbation; on a
tens-of-token call it does not perturb a batch's wall clock, it *replaces* it.

This mattered, and it was caught rather than published:

- The **pilot run** ([`worker-scoped-overhead-pilot.jsonl`](worker-scoped-overhead-pilot.jsonl),
  68 calls) hit the same event. Its `lean-off-w8` cell read 23.49 s aggregate
  against a 0.87 s median latency, which would have published a lean
  call-throughput speedup of **0.25×** — a number that says concurrency makes
  scoped calls *slower*, and is entirely an artefact of one sleep constant.
- The **confirmation run** raised n (140 calls) and hit it again. Rather than
  quietly re-running until the rig cooperated, the harness now treats it as
  first-class data: `transport_summary` reports the rate and the backoff ratio,
  and `without_retry_batches` republishes affected cells with the contaminated
  batch dropped **whole** (a retried call's neighbours shared its batch clock,
  so dropping the call alone would compare clean tokens against a dirty clock).
  Both readings are published — Table 3 as measured, Table 5 with the batch
  excluded. The as-measured reading is never deleted.

**This is a finding for this plan's own timeout work (`t2`, `t5`), not just a
nuisance.** A retry backoff chosen for long completions silently governing
scoped calls is the same defect class as a client timeout chosen for one token
budget silently governing another: a constant sized against the wrong
denominator. The plan's derived-bound discipline should cover retry backoff,
not only request timeouts.

## What the B1 sweep should now cite

1. **Call granularity, width 8, realistic context:** `0.289 s` wall clock,
   `15.7` completion tokens, `1,008` prompt tokens per scoped call.
2. **Concurrency:** `3.46 calls/s` at width 8 with realistic context
   (`8.38 calls/s` with lean context). **Not** 6.14×, **not** ~9×, and **not**
   `effective_concurrency` under any spelling.
3. **Prefill:** repeated context costs a `0.41×` throughput ratio at width 8.
   If the sweep re-sends a shared preamble per scoped call, that is the
   dominant cost and it should be a declared, priced design choice — hoisting
   shared context or accepting a 2.4× penalty knowingly, not by default.
4. **Thinking:** `enable_thinking: false` on every scoped call, asserted on the
   wire. With it on, a tens-of-token budget yields a 0% answer rate.
5. **Budget:** completion tokens are not the binding constraint for B1;
   prompt tokens are.

## Limits of this measurement

- **n = 140 measured calls** (plus a 68-call pilot), on **one rig, one model,
  in one seven-minute window**. Batch-to-batch spread is committed per cell in
  `batch_elapsed_seconds`, not just its sum; the width-8 cells were tight
  (`rich-off-w8` medians 2.16–2.23 s across five batches) but this is not a
  sustained-load result and does not claim to be.
- **Non-streaming.** A call's `latency_seconds` is queueing, prefill and decode
  combined; there is no time-to-first-token, so the residual **cannot be
  decomposed** into its parts. The lean-versus-rich difference at width 1 is
  the closest this probe gets to isolating prefill, and it is reported as
  `implied_`, never as a measured prefill rate. (Task `t5`'s streaming pilot is
  what would decompose it.)
- **The reference decode rate (76.43 tok/s) was itself measured on 1200-token
  completions.** Using it to model the decode time of a 15-token completion is
  a stated modelling choice; the residual is a difference against that model,
  not an independent measurement of overhead. Negative residuals are reported
  rather than clamped precisely so a wrong reference rate stays visible.
- **Prefix caching is not controlled for** (see the prefill caveat above).
- The `rich-on-natural-w1` cell is **n=4** and truncated 2 of 4 even at 2,000
  tokens, so `1,574.5` completion tokens is a **floor** on what an
  unsuppressed scoped call costs, not a mean of completed answers.

## Reproduce

Every table in this document regenerates from the committed artifacts **with no
rig and no API key**:

```bash
# tables, from the committed raw records
uv run python examples/worker_scoped_overhead.py \
    --rebuild-from docs/live-test-results/worker-scoped-overhead.jsonl \
                   docs/live-test-results/worker-scoped-overhead-summary.json \
    --summary-out docs/live-test-results/worker-scoped-overhead-summary.json \
    --markdown-out docs/live-test-results/worker-scoped-overhead-tables.md

# this document, from its template plus those tables plus the window log
python3 docs/live-test-results/worker-scoped-overhead-raw/render-doc.py
```

The hermetic suite (no socket, no rig):

```bash
uv run pytest tests/test_worker_scoped_overhead.py -q
```

A fresh live run — **check the window first; the guard refuses to dial when a
Thor-dialling cell is in flight, and `run-probe.sh` guards both ends
automatically**:

```bash
export COLLEAGUE_API_KEY=...

# is Thor free right now? (exit 0 idle, 1 busy, 2 uncheckable)
python3 docs/live-test-results/worker-scoped-overhead-raw/thor-idle-guard.py

# ...or wait for a window, bounded
python3 docs/live-test-results/worker-scoped-overhead-raw/thor-idle-guard.py \
    --wait --poll-seconds 60 --max-wait-seconds 7200

# the guarded lane (guard -> probe -> guard, both verdicts logged)
docs/live-test-results/worker-scoped-overhead-raw/run-probe.sh \
    --batches 'lean-off-w1:10,rich-off-w1:10,lean-off-w8:5,rich-off-w8:5,rich-on-w1:6,rich-on-w8:3,rich-off-natural-w1:6,rich-on-natural-w1:4'
```

**Live spend, this committed run:** 140 measured calls, 9,314 completion tokens
and 102,613 prompt tokens across 151.9 s of batch wall clock (the pilot added
68 calls, 8,249 completion and 53,115 prompt tokens). Note the shape of that
ratio — **11× more prompt tokens than completion tokens** — which is the whole
finding in one line.
