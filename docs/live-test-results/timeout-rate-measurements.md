# Generation-rate measurements — what the timeout bounds derive from, and how to re-derive them

**Config:** [`timeout-rate-measurements.json`](timeout-rate-measurements.json) ·
**Loader:** `tests/rate_config.py` ·
**Frame:** [error-derived-timeouts-bee-hive-architecture](../specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md),
task `t1` (extended by `t2`), claims `c39` / `h27` / `c40` / `c13` / `h5`

Every model-call timeout and fan-out deadline in `examples/` is derived, not
chosen:

```text
REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate
```

The rule is issue [#42](https://github.com/agentculture/embodiment/issues/42)'s.
This page is about its right-hand side — the one input that can rot without
anything failing.

Task `t2` added two things to that right-hand side, both because the rule as
written was one term short of the clock it guards:

- **a rate per *model*, not per convenient role.** A constant fronts every
  model dialled through it. `league_commander` puts Gemma 4 31B and the Qwen
  cortex on one `REQUEST_TIMEOUT`; #42 derived it at the cortex and called
  900 s passing, when at Gemma's rate it was 0.68× and below bound
  ([`corrections.md`](corrections.md) §9). Four of the seven audited constants
  front that model, so it has an entry here.
- **a non-generation allowance.** `max_tokens / tok_s` bounds *generation*. The
  clock in front of the request also covers queue wait and prompt processing,
  and one committed call spent **179.3 s** there — more than the entire slack a
  passing constant had left.

## Why the rate lives in a file

Two findings from the challenge pass, both about the input rather than the rule:

- **`c39` — a bare rate literal in test code goes stale silently.** The rig
  changes, the literal does not, and no test fails. The bound just quietly
  stops protecting, or starts over-protecting, and nobody is told. This is not
  hypothetical: the cortex rate below was measured on
  `unsloth/Qwen3.6-27B-NVFP4`, while `culture.yaml` and every `examples/*.py`
  default name `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` — a different build.
- **`c40` — the rate is condition-dependent.** 21.5 tok/s was measured
  single-stream against a server that admits two concurrent sequences. The
  worker's per-call rate falls from 76.4 tok/s at width 1 to 29.8 tok/s at
  width 14. A rate with no stated concurrency is not a measurement.

So the numbers live in a committed JSON file carrying rate, date, `n`, model,
endpoint and concurrency condition; `tests/rate_config.py` is the only way to
read them; and **there is no default**. Delete the config and
`load_rate_config()` raises, naming the missing measurement and pointing at
this page. That refusal is asserted by
`tests/test_rate_config.py::TestAbsentConfigRefuses`, which points the loader
at an empty `tmp_path` rather than touching the real file.

## What is recorded today

| role | divisor | slowest | mean | fastest | n | condition | measured |
|---|---|---|---|---|---|---|---|
| cortex | **21.452** tok/s | 21.452 | 23.249 | 25.448 | 10 calls, 8 rate-bearing | single-stream, `--max-num-seqs=2` server | 2026-08-01 |
| worker | **12.921** tok/s | 12.921 | 38.873 | 77.590 | 51 calls | floor across widths 1/2/8/14; occurs at width 14 | 2026-07-31 |
| muse | **12.103** tok/s | 5.175 | 9.574 | 12.103 | 224 calls | one call in flight, shared gateway | 2026-07-31 |

**Divisor** is the figure a bound actually divides into — `bound_input` in the
config, and `slowest_tok_s` wherever the config does not say otherwise. The
muse is the one role where it is not the slowest reading, and that is a
judgement stated and cited rather than left implicit in a number; see below.

The worker's headline is an envelope, not a reading. Its per-width
measurements are the measurement:

| width | slowest | mean | fastest | n | retry-bearing | retry-clean floor |
|---|---|---|---|---|---|---|
| 1 | 74.129 | 76.426 | 77.590 | 3 | 0 | 74.129 |
| 2 | 60.405 | 64.001 | 66.846 | 4 | 0 | 60.405 |
| 8 | 38.678 | 41.400 | 44.778 | 16 | 0 | 38.678 |
| 14 | **12.921** | 29.816 | 42.622 | 28 | **9** | **30.558** |

`tests/rate_config.py` refuses to interpolate: `at_width(4)` raises and names
the widths that were measured. Dial a measured width, or measure the one you
dial.

### Two caveats that must travel with these numbers

1. **The width-14 floor is retry-contaminated.** Nine of the 28 width-14 calls
   carry `retries: 1`, and `examples/worker_throughput.py` measures
   `latency_seconds` around the whole call — including
   `examples/worker_seam.py`'s `RETRY_SLEEP_SECONDS = 20.0` backoff.
   Subtracting exactly 20.0 s from those nine rows lifts them to
   35.8–44.5 tok/s, back into the width-8/14 band, and all nine are strictly
   slower than every retry-clean width-14 call. That is the same
   wall-clock-including-retry-overhead artifact
   [`corrections.md`](corrections.md) §5 corrected for the cortex, one rig
   over. Both readings are recorded; `slowest_retry_clean_tok_s` is the
   retry-free floor.
2. **Even retry-clean, high-width per-call rates include fixed per-call
   overhead** — queueing and prefill — amortised over a short completion,
   because that harness does not parse SSE and so cannot separate
   time-to-first-token from decode time. They are a **conservative floor for
   long generations, not a marginal generation rate**. The two full-budget
   calls at width 14 (`finish_reason == "length"`, 1200 tokens) are the least
   contaminated reading available there: 33.861 and 33.483 tok/s.

Deriving a bound from 12.921 over-protects by roughly 2.4× against the
retry-clean width-14 floor. That is the safe direction and it is deliberate: an
over-long timeout costs only when something is genuinely hung, while an
under-long one censors the evidence — the defect this whole lane exists to
close.

### Why the muse's divisor is its *fastest* implied rate

It is the only reading of that instrument that is a rate at all, and reaching
for the slowest out of habit would be a different way of being wrong rather
than a safe one.

Every muse call in the cited records is 31–236 completion tokens, median 45. At
that length implied rate (`completion_tokens / seconds`) is dominated by fixed
cost: the slowest call, **5.17 tok/s**, is a 31-token completion whose ~6 s of
wall clock is mostly the 1.61 s fixed cost plus queue. Extrapolating 5.17 tok/s
to a 16000-token budget claims **3092 s** for a generation the regression puts
near **1187 s** — a 2.6× inflation produced by treating a one-off cost as a
per-token cost.

The fastest implied rate is the honest floor. Queue wait and prompt processing
can only push implied rate *down*, so the fastest call is the least
contaminated observation and still a lower bound on true generation rate. Two
independent checks agree with it:

- the regression over all 224 points — 0.07407 s/token, fixed 1.610 s,
  r² 0.8142 — gives **13.5 tok/s** marginal, *faster* than the divisor;
- `league-h2h.jsonl`'s 36 Gemma seat-turns, a different harness on a different
  week, put the fastest implied rate at **12.238 tok/s**.

The standing gap, recorded in the config's `remeasure_when`: this is a ~68×
extrapolation from 236-token completions. The first committed record of this
model generating at length should **replace** this entry, not confirm it.

### Which width a calling pattern divides by — `calling_patterns`

```json
"roles": { "worker": { "calling_patterns": { "scoped_run": {…}, "strategist_cadence": {…} } } }
```

Added by plan task `t10` (`strategic-scope-governor`) for its risk `r1`.
`bound_input` answers *what is this role's divisor*; a role measured at four
widths forces the further question of **whose**. The worker is the only role
that has one, and the trap is specific: reuse the width-1 reading for a calling
pattern that is not width 1 and the clock is nearly six times too generous,
silently.

So each pattern records the reading it **rejected** beside the one it uses, and
`tests/rate_config.py` refuses any pattern whose chosen divisor is the *faster*
of the two. A record that could pick the flattering width would be the failure
`CLAUDE.md` names four times over.

| pattern | divides by | rejected | why, in one line |
|---|---|---|---|
| `scoped_run` | `slowest_tok_s` = **12.921** | `by_width.1.mean_tok_s` = 76.426 | a scoped run looks single-threaded from inside `scoped_run.py`, but nothing reserves the worker and the four ScopeBench arms share one deployment |
| `strategist_cadence` | `mean_tok_s` = **38.873** | `by_width.1.mean_tok_s` = 76.426 | a cadence ratio asks how fast the actor moves *typically*; the floor's job is to stop a clock cutting a turn, and there is no turn to cut |

Two things travel with these and are written out in the config's own `why`:

- **`scoped_run` has no constant to bound yet.** The ScopeBench pre-registration
  commits `examples/scope/` to introducing no timeout constant and
  `tests/test_scopebench.py` asserts it by AST, so this entry is the divisor
  waiting for the Stage-2 seam — there so whoever builds it derives rather than
  chooses. It is also *not itself a clock*: this role carries
  `rate_includes_non_generation: true`, so whether the queue allowance is added
  or already absorbed is decided per (budget, role) by
  `tests/test_timeout_bounds.py`, never by hand.
- **`strategist_cadence` carries an assumption about step length.** Its
  numerator is 1200 tokens — this measurement's own `max_tokens` — standing in
  for a typical acting step, and nothing here measures the completion-length
  distribution of a scoped actor. A host dialling the actor at the 16000 raised
  by deviation `d16` makes `T_actor_step` 411 s and collapses both
  `DEFAULT_MAX_LAG` and `DEFAULT_REVIEW_GAP` to 1. That is a re-derivation, not
  a tuning pass.

### The rounded figures a *derivation* quotes — `cited_mean_as`

`cited_as` and `cited_fastest_as` exist so a prose doc's rounded number can be
checked against the precise one. `t10` added `cited_mean_as` for the same reason
one layer out: `embodiment/strategist_runner.py` derives `DEFAULT_MAX_LAG` and
`DEFAULT_REVIEW_GAP` from the worker's mean at **38.9 tok/s**, and no committed
file published that figure. Nothing divides by it at runtime — but two shipped
constants rest on it, and a rate that justifies a constant goes stale exactly as
quietly as one that computes it. `tests/test_timeout_bounds.py` now walks the
runner's prose and fails any `N tok/s` this config does not publish.

## The non-generation allowance

```json
"non_generation_allowance": { "seconds": 179.2564, ... }
```

The rule bounds generation; the clock does not. `corrections.md` §9 measured
one call (`A-qwen-2`) at 193.6 s of wall clock for 365 completion tokens —
**179.3 s** of it not generating. `league_commander` at 900 s over a 744 s
generation bound had 155.8 s of slack, so a full-budget completion behind that
same queue would have totalled ~923 s and been cut, by a constant the audit had
recorded as passing.

It is computed as wall clock minus `completion_tokens / fastest implied rate in
the same series` — a residual over the model's own best observed speed, so it
is a lower bound on the gap rather than an estimate of it.

**One figure, applied repo-wide**, because it is the only such measurement that
exists. That over-protects wherever the true gap is smaller, which is the safe
direction, and it is said here rather than left implicit.

**It is not added where the rate already contains it.** A role whose rate is
wall-clock-over-tokens on short completions — the worker, per caveat 2 above —
has queue and prefill amortised into it already, and adding the allowance would
double-count. That is the `rate_includes_non_generation` flag, and
`tests/test_timeout_bounds.py` does **not** take the flag on trust: absorption
is allowed only where the cited rate is slower than the role's own retry-clean
floor by at least the whole allowance, *at that budget*. The check is per
(budget, role) pair because a rate's conservatism scales with the tokens
divided by it while queue wait does not — so the worker absorbs at 16000 tokens
and does not at 1200.

## Roles a constant fronts that nobody has timed

```json
"unmeasured_roles": { "senses": { ... } }
```

`examples/arch_vision.py` dials the **senses** role
(`coolthor/gemma-4-12B-it-NVFP4A16`) at a 16000-token budget through
`examples/arch_arms.py`'s `ArchSeam`, which subclasses `WorkerSeam` — so
`worker_seam.py`'s `REQUEST_TIMEOUT` fronts it. No committed record times that
model.

No proxy is substituted. "A 12B on the same rig cannot be slower than the 31B
whose rate we do have" is a plausible argument and still an argument; a rate
that appears where none was taken is exactly `c39`. The consequence is stated
instead: **that constant is proven against the cortex and the worker and is not
proven against a full-budget senses turn.**

The gap is self-closing. `tests/rate_config.py` refuses a config that lists a
role as both measured and unmeasured, so the first committed senses rate forces
the pair into the bound test's walk rather than leaving a stale hole recorded
beside a filled one.

## When to re-measure

The config's `remeasure_when` list is the authority; it is written per role so
it can be read by whoever is standing in front of the rig. The triggers that
apply to both:

- **the model id changes.** A rate belongs to a build, not to a role name.
  Pre-registration §1 already states the rule for capability facts — *"a run
  whose recorded cortex id is not the probed id does not inherit this section's
  capability facts"* — and the rate inherits the same discipline.
- **the concurrency condition changes.** For the cortex this means any harness
  that can put two requests in flight at once: `--max-num-seqs=2` admits a
  second stream rather than queueing it, so per-stream rate under contention
  can fall below the recorded floor. No harness does this today (the `B` and
  `P` arms deliberately avoid it), which is why `concurrency: 1` is honest now
  and not forever. For the worker it means dialling a width that has no entry
  in `by_width`.
- **the server's launch flags change** — `--max-num-seqs`, quantisation, MTP,
  KV cache size.
- **a second real tenant appears** on the endpoint.

Re-measuring is cheap relative to being wrong: the worker sweep is a few
minutes of Thor's time, and a censored distribution is unrecoverable after the
fact.

## How to re-derive

### Worker (`unsloth/Qwen3.6-35B-A3B-NVFP4` on Thor)

The harness exists and is committed. Run it against the live rig:

```bash
export COLLEAGUE_API_KEY=...            # same-origin key rules apply
export EMBODIMENT_WORKER_URL=http://thor.tail0be7e0.ts.net:8000/v1
export EMBODIMENT_WORKER_MODEL=unsloth/Qwen3.6-35B-A3B-NVFP4
uv run python -m examples.worker_throughput --help   # widths, batches, budget
```

It writes `worker-throughput.jsonl` (one row per call) and
`worker-throughput-summary.json`. Commit both, then recompute the config's
figures **from the jsonl** — never by hand-copying the summary:

- per width, over rows with `ok == true` and `warmup == false`:
  `slowest_tok_s` = `min(tokens_per_second)`, `fastest_tok_s` = `max(...)`,
  `mean_tok_s` = arithmetic mean, `n_calls` = row count;
- `retry_bearing_calls` = rows with `retries > 0`;
- `slowest_retry_clean_tok_s` = `min(tokens_per_second)` over rows with
  `retries == 0`;
- the role headline = the same three statistics over all measured rows, and
  `slowest_tok_s` must equal the minimum across `by_width`.

`tests/test_rate_config.py::TestWorkerRatesMatchRawRecords` recomputes all of
that from the committed jsonl and fails if the config disagrees, so a
transcription slip is caught at the next test run rather than at the next
incident.

### Cortex (`unsloth/Qwen3.6-27B-NVFP4`, local)

There is no standalone rate harness for the cortex. The figures come from a
real series cell — `C1-E` of the orchestrator-worker series — with retry
overhead removed:

```text
generation_seconds = recorded_seconds - retries * (REQUEST_TIMEOUT + RETRY_SLEEP_SECONDS)
tok_s              = completion_tokens / generation_seconds
```

The correction is not optional. Uncorrected wall-clock-over-tokens spans
5.0–25.4 tok/s and is an artifact of the 300 s timeout that was cutting turns,
not a measurement of throughput —
[`corrections.md`](corrections.md) §5 records that misdiagnosis by name. Calls
that returned zero tokens (`finish_reason: transport_failure`) contribute no
rate; record them in `n_calls` and exclude them from `n_rate_bearing`.

To re-measure deliberately rather than harvesting a cell, the cheapest honest
probe is `examples/cortex_toolcall_probe`-shaped: n≥10 single-stream calls at
the real `max_tokens`, recording `seconds`, `completion_tokens`, `retries` and
`finish_reason` per call, with `EMBODIMENT_LIVE_RIG=1` set and every other
consumer of port 8001 quiet. Commit the raw rows beside this file and cite them
in `sources`.

### Muse (`nvidia/Gemma-4-31B-IT-NVFP4`, proxied through the spark gateway)

Also harvested from a real series rather than a dedicated probe — the
`league_commander` runs, whose 224 Gemma calls are committed in
`league-commander-transcripts.jsonl` and
`league-commander-frontier-transcripts.jsonl` with `seconds`,
`completion_tokens` and `retries` per call. Zero retries and zero transport
errors across all of them, so unlike the worker nothing here needs a retry
correction:

```text
implied_tok_s = completion_tokens / seconds        # per call
divisor       = max(implied_tok_s)                 # the fastest, see above
```

`tests/test_rate_config.py::TestMuseRatesMatchRawRecords` recomputes the
headline figures, the call count and the divisor from those two files, and pins
the published `12.1` against the text of [`corrections.md`](corrections.md) — so
a re-measurement that drifts from its own citation fails.

To re-measure deliberately, the same probe shape the cortex needs applies, at
this model's id and **at completion lengths near the budget the bound is
derived for**. The 236-token ceiling in these records is the one weakness of
the current entry and a long-completion probe is what retires it.

**Citation status, stated plainly:** the full ten-row per-call table lives in
`orchestrator-worker-preregistration.md` §18 (*Amendment 1 — the request
timeout, raised on measured evidence*) and its raw records in
`orchestrator-worker-series-raw/superseded/C1-E-at-300s.jsonl`. Both are on
branch `owa/t12` (commit `25a65b7`) and reach `main` only when it merges. The
config records this in `pending_sources` rather than citing a path a reader on
`main` cannot open; the citations it lists under `sources` —
[`corrections.md`](corrections.md) and `CHANGELOG.md` 0.10.0 — are readable
here today and carry the 21.5–25.4 tok/s band.

## Updating the config

1. Re-measure and **commit the raw records first**. A config whose numbers
   cannot be recomputed from committed data is the c39 defect with extra steps.
2. Edit `timeout-rate-measurements.json`: the figures, `measured_on`, `model`,
   `n_calls`, `n_rate_bearing`, and the `condition` block. Update `caveats` if
   the reason a number is conservative has changed.
3. Update `sources` / `pending_sources` to the records you just committed.
   If the role carries `calling_patterns`, re-read each one: `tok_s` and
   `rejected_tok_s` must still **be** the figures they name, and the loader
   refuses the file outright if a re-measurement has made a chosen divisor the
   faster of the pair. A pattern surviving a re-measurement unchanged is a
   claim, not a default.
4. Run `uv run pytest tests/test_rate_config.py tests/test_timeout_bounds.py`.
   The first proves the config is complete, internally consistent and matches
   the raw records; the second (task `t2`) re-derives every harness bound from
   the new rate and fails any constant that has fallen below it.
5. If a bound moved, raise the constant **in the same commit**. A config that
   says a timeout is too short while the timeout stays short has recorded the
   defect rather than fixed it.

Bump `version` in the config and `SUPPORTED_VERSION` in `tests/rate_config.py`
together if the shape changes — the loader refuses an unknown schema rather
than reading it as if it were the known one.
