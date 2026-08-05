# The worker seat drives embodiment's own tool loop — three rungs, all clean, all at ceiling

Plan task `t1` of `config-not-minds-strategist`. The pre-registration is
[`worker-toolloop-preregistration.md`](worker-toolloop-preregistration.md),
written and committed **before** the first dial. This is the record.

- **Date** — 2026-08-04
- **Rig** — `lobes` at `http://localhost:8001`; role `worker`,
  `unsloth/Qwen3.6-35B-A3B-NVFP4` (proxied, thor). `cortex` ready and untouched.
  `senses` `ready=false` at dial time and not used.
- **Instrument** — `worker-toolloop-probe.py`, driving **`embodiment.loop.run`
  itself**. Scored by `worker-toolloop-analyse.py`, which is a separate file on
  purpose: the probe records facts, the analyser applies the rule that was fixed
  before the dial.
- **Budget / clock** — `max_tokens=16000` (`d16`); request timeout **derived**,
  `16000 / 12.921 tok/s = 1238.3 s`, the divisor the `scoped_run` calling
  pattern names, which explicitly rejects the width-1 reading.
- **n** — 12 per rung, 36 runs total, **0 transport failures**.

## 0. The verdict

**The worker drives the loop.** Every pre-registered bar was met on all three
rungs, including the two that were designed to be harder than the first. Under
the committed rule this **supports the acting-seat promotion** that decision
`c26` and honesty condition `h12` require — and the support is **bounded by what
the rungs actually varied**, which is protocol mechanics on a hermetic tool
surface, not acting quality.

Read the ceiling honestly. All three rungs returned 12/12 on every measure with
a single tool sequence each. That is the condition `M4` says measures nothing
about *margin*: it cannot distinguish "comfortably capable" from "just capable",
and it says nothing about a slow, failing, approval-gated or adversarial tool
surface. The claim this record supports is *no loop-protocol failure was
observed in 36 runs across three rungs* — and nothing wider.

## 1. Why this was measured at all

Assumption `c33`, from the challenge pass: **nothing committed measures this
model driving a tool loop.** Arm B is explicitly "no loop, no turn, no goal";
`worker-throughput`, `worker-seam` and `worker-scoped-overhead` all measure
single calls. The three-tier design promotes this seat *to the acting loop*, so
the capability at the centre of the design had never been dialled.

The probe drives `embodiment.loop.run` rather than a hand-rolled turn driver,
because the question is whether the worker can drive **our** loop. A stand-in
would have measured something we do not ship.

## 2. The ladder, and why it was declared in advance

This repo has four experiments that tied at a ceiling, and the lesson recorded
from them was to **pre-register the ladder, not only the thresholds**. So the
three rungs and the escalation rule were committed before the first run — which
turned out to matter, because R1 hit the ceiling immediately and a rule written
afterwards would have been a rule written to fit the data.

| rung | what it varies | result |
|---|---|---|
| **R1** | four calls, two data-dependent; sequence named in the instruction | 12/12 |
| **R2** | R1 + the sensor bus **refuses the first read** and must be retried | 12/12 |
| **R3** | sequence **not** named + a plausible-wrong `estimate_moisture` tool on the surface | 12/12 |

## 3. Results

| measure | R1 | R2 | R3 | bar |
|---|---|---|---|---|
| runs completing ≥2 tool calls | 12/12 | 12/12 | 12/12 | 12/12 |
| runs reaching `finish` | 12/12 | 12/12 | 12/12 | ≥11/12 |
| correct answer (51.5) | 12/12 | 12/12 | 12/12 | ≥11/12 |
| runs **without** a malformed-argument event | 12/12 | 12/12 | 12/12 | 12/12 |
| runs **without** a truncated turn | 12/12 | 12/12 | 12/12 | 12/12 |
| transport errors | 0 | 0 | 0 | 0 |

**The two discriminating measures, which are the reason R2 and R3 exist:**

- **R2 — recovery.** The bus refused a read on **12 of 12** runs. The worker
  retried and recovered on **12 of 12**, and the retry is visible in the trace:
  every R2 run ran `read_sensor > read_sensor > read_sensor > average > finish`,
  one call longer than R1, exactly where the induced failure sits.
- **R3 — tool selection.** With the sequence unnamed and a cheaper, plausible,
  explicitly-approximate `estimate_moisture` on the surface, the worker took the
  distractor **0 of 12** times. Every run selected `read_sensor` and returned
  the true mean rather than the estimate's.

### The `#33` shape did not appear

`average` takes `values` as an **array of numbers** specifically because that is
the failure that refused 74% of one arm's calls (embodiment#33). In 36 runs the
argument arrived as an array every time. This does not close #33 — that was a
different model on a different surface — but it establishes the shape is not a
property of this seat on this tool.

### Truncation, measured rather than assumed absent

`ModelResponse` carries no `finish_reason` (embodiment#37), so inside the loop a
truncated turn and a deliberate one are the same object — which is how `t24`
found 6.0% of completions silently cut. This probe owns its HTTP client and
records `finish_reason` per turn: **0 truncated turns in 36 runs** at
`max_tokens=16000`. Recorded as a measurement, not as an absence of evidence.

## 4. The unplanned finding: this seat is fast

| measure | R1 | R2 | R3 |
|---|---|---|---|
| wall clock per run | median **4.59 s** | 6.25 s | 4.72 s |
| per-turn latency | median **1.64 s** | 1.19 s | 1.39 s |
| completion tokens per run | median 314 | 342 | 313 |

For comparison, `cortex-toolcall-probe.md` measured the **cortex** at a median
**57.2 s** per single reasoning turn with a tail to 204 s. On this task the
worker completes an entire four-call drive in under five seconds.

**This bears directly on assumption `c28`,** which worried that the three-tier
inversion puts the network on every acting turn while the local model retreats
to background work. On this evidence the concern does not bite: the proxied
worker's whole drive costs less than one local cortex turn. That is one task
shape on an idle rig and does not generalise to contended widths — the rate
config's own `scoped_run` note is that nothing reserves this deployment — but
the direction is the opposite of the one `c28` feared.

## 5. Limits — what this cannot support

- **One rig, one model, one task shape, n=12 per rung.** Every rung is at
  ceiling, so nothing here estimates a rate between the extremes.
- **The tool surface is hermetic, instant and free.** Real tools are slow, fail
  in ways that are not helpfully worded, and may require approval. R2's induced
  failure *told the model exactly what to do*; that is a floor for recovery, not
  a test of it.
- **It measures the acting protocol, not acting quality.** Whether the worker
  acts *well* under configuration governance is `t14`'s and `t15`'s question and
  is untouched here.
- **R1's records predate the `rung` field** (it was added when the ladder was
  implemented), so R1 rows carry `rung: null` rather than `"R1"`. Noted rather
  than backfilled — rewriting a committed measurement's provenance to look
  tidier is exactly the habit this repo's record exists to avoid.
- **`senses` was `ready=false`,** so nothing here touches the interaction tier
  or the checkpoint change recorded as confound `c20`.

## 6. What follows

- `h12`'s requirement — a cited measured verdict supporting the worker **as
  acting loop** — is met at the bounded strength stated in §0. The
  `_WORKER_ROLE_HAS_SUPPORTING_VERDICT` flip in
  `tests/test_governance.py`'s promotion gate may cite this file, and must cite
  §0's boundary with it.
- Plan risk `r3` (worker acting feasibility → `/deviate`) **does not fire**.
- The next real test of this seat is a non-hermetic surface under governance,
  which is `t12`'s host and `t15`'s live session.

## Raw

- `worker-toolloop-probe.jsonl` (R1), `worker-toolloop-probe-r2.jsonl`,
  `worker-toolloop-probe-r3.jsonl` — one record per run, every field a fact
- `worker-toolloop-probe.py` — the instrument, `--rung R1|R2|R3`
- `worker-toolloop-analyse.py` — the scorer, applying the pre-registered bars
