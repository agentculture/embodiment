# Generation-rate measurements — what the timeout bounds derive from, and how to re-derive them

**Config:** [`timeout-rate-measurements.json`](timeout-rate-measurements.json) ·
**Loader:** `tests/rate_config.py` ·
**Frame:** [error-derived-timeouts-bee-hive-architecture](../specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md),
task `t1`, claims `c39` / `h27` / `c40`

Every model-call timeout and fan-out deadline in `examples/` is derived, not
chosen:

```text
REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate
```

The rule is issue [#42](https://github.com/agentculture/embodiment/issues/42)'s.
This page is about its right-hand side — the one input that can rot without
anything failing.

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

| role | slowest | mean | fastest | n | condition | measured |
|---|---|---|---|---|---|---|
| cortex | **21.452** tok/s | 23.249 | 25.448 | 10 calls, 8 rate-bearing | single-stream, `--max-num-seqs=2` server | 2026-08-01 |
| worker | **12.921** tok/s | 38.873 | 77.590 | 51 calls | floor across widths 1/2/8/14; occurs at width 14 | 2026-07-31 |

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
