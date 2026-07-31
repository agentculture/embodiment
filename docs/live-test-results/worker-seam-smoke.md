# Worker wiring smoke — Thor reachable, tools round-trip, clean finish

**Date:** 2026-07-31 · **Plan:**
[orchestrator-worker-architectures](../plans/2026-07-31-orchestrator-worker-architectures.md),
task t2 · **Harness:** `examples/worker_seam.py` · **Raw:**
[`worker-seam-smoke.json`](worker-seam-smoke.json)

## What this is (and is not)

An **instrument check, not data** — the same status
`league-h2h-smoke.json` carries. Before any future task (t5) dials the worker
inside a measured arm, this confirms the dial itself works: a real HTTP round
trip to Thor's OpenAI-compatible endpoint, a real tool schema on the wire, a
real tool call fed back, a clean finish — through `WorkerSeam` and
`embodiment.run`, the exact code future arms will reuse (`run_bare_completion`
and `run_tool_loop` in `examples/worker_seam.py`).

This document makes committed, repeatable artifacts out of what the spec's
probes `s18`/`s19` ran by hand once (a 138-completion-token reasoning call; one
tool call round-tripping clean).

## Config

| | |
|---|---|
| worker URL | `http://thor.tail0be7e0.ts.net:8000/v1` |
| worker model | `unsloth/Qwen3.6-35B-A3B-NVFP4` |
| max_tokens | 16000 (d16's measured floor for this rig's thinking models) |
| temperature | 0.3 |
| max_steps (tool loop) | 6 |
| auth | bearer token from `COLLEAGUE_API_KEY` |

Resolved from explicit flags only (`--worker-url`, `--worker-model`) — no
default exists anywhere in `examples/worker_seam.py` for either value, and the
config resolver never reads `EMBODIMENT_BASE_URL` (the spark-gateway variable
every other harness in this repo defaults to). See
`resolve_worker_config`'s docstring and `tests/test_worker_seam.py::TestNoSilentFallback`.

## Results

**(a) Bare completion — no tool schema on the wire.**

| | |
|---|---|
| prompt | `Reply with exactly: WORKER-OK` |
| finish_reason | `stop` |
| prompt / completion tokens | 18 / 176 |
| wall clock | 4.50 s |
| content | `WORKER-OK` (preceded by two newlines; matched despite them) |

176 completion tokens for a three-word instruction is the reasoning-model
signature this rig's cortex also shows (`docs/live-test-results/README.md`'s
9.3 s / 209-token baseline) — the worker emits a full `reasoning` field before
`content`, confirmed in the raw transcript.

**(b) Bounded tool loop — schema on the wire, a call fed back, a clean finish.**

| | |
|---|---|
| prompt | "What is 17 + 25? Call add to compute it, then call finish with the integer answer." |
| exit_reason | `finished` |
| status | `ok` |
| summary | `42` (correct) |
| model turns | 2 |
| tools called, in order | `add`, `finish` |
| aborted | none |
| finish_reason (both turns) | `tool_calls` |
| prompt / completion tokens (summed) | 874 / 235 |
| wall clock | 3.32 s |

Turn 1: the worker reasoned briefly, then called `add(a=17, b=25)` with no
prose content. Turn 2: after the tool result (`42`) was fed back, it called
`finish(answer=42)`, again with no prose content. `embodiment.run` exited
`finished` on the first attempt — no retries, no truncation, no abort, no
budget pressure (2 of 6 steps used).

## Degradation record (C3)

Zero transport retries, zero failures, zero truncated turns across both calls
(3 model calls total). `empty_content` is 0 throughout — the tool-call turns
have empty `content` by design (the model's answer IS the call), which
`WorkerSeam` records but does not misclassify: `empty_content` only flags a
turn with **neither** content **nor** tool calls, and both tool turns here
carry a call.

## What this confirms, and what it does not

**Confirms:** the worker endpoint is reachable from this box over the
tailnet with the documented model id and bearer auth; the OpenAI-compatible
`tools` field round-trips through this gateway exactly as the cortex's does
(same `parse_completion` shape as `examples/league_seat.py`); `finish_reason`
and token counts are captured on every call, closing the gap
`embodiment.contract.ModelResponse` leaves open (issue #37); the bounded loop
terminates cleanly with the worker as the acting model.

**Does not confirm:** throughput or stability under concurrent load (t3's
job — the operator-supplied 50 tok/s × 14 figure is unmeasured on this rig
and unrelated to this single-call check); reasoning quality on anything
harder than 17 + 25; whether a future delegate tool (t1) can drive this same
seam through a subagent boundary. This document's scope is wiring, not
capability.
