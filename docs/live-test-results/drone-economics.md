# The drone economics, measured — and the success signal fails

**Dialled 2026-08-01**, cortex and worker both idle. Raw records:
[`drone-authoring-cost.json`](drone-authoring-cost.json),
[`drone-evocation-cost.json`](drone-evocation-cost.json). Harnesses:
[`examples/author_drone.py`](../../examples/author_drone.py),
[`examples/drone_host.py`](../../examples/drone_host.py).

## The claim under test

`c30`'s third success signal:

> a drone's second evocation makes **0 cortex calls** at **≤5%** of its
> authoring tokens

Until now this had **no denominator** — nobody had ever paid an authoring turn,
so the signal could only be reported `ABSENT` (plan risk `r6`). Both halves are
now measured.

## Result: half passes, half fails

| | measured | target | |
|---|---:|---:|---|
| cortex calls, second evocation | **0** | 0 | **PASS** |
| completion tokens, second evocation | **1,532** | ≤ 239 | **FAIL** |
| ratio of authoring cost | **32.0%** | ≤ 5% | **FAIL** |

Authoring: **4,785 completion tokens over 199.3 s**, `finish_reason: stop`,
one cortex turn, streamed. Second evocation: **3 scoped worker calls, all
accepted**, 277 prompt and 1,532 completion tokens, correct answer.

**Zero cortex calls is structural, not lucky.** A drone's only outward seam is
`DroneRequest.ask`, which reaches the worker the host wires and nothing else;
there is no escalation path in v1 (`c46`). That half of the signal cannot fail
without the design changing.

## Why the token half fails, precisely

**511 completion tokens per scoped call.** `t8` measured the same class of call
at **~15 tokens** with `enable_thinking: false`
([`worker-scoped-overhead.md`](worker-scoped-overhead.md)) — a **34×**
difference, and it is not the prompt. It is that **the drone tier has no way to
turn thinking off.**

`examples/worker_seam.py`'s `WorkerSeam.__init__` takes `base_url`, `model`,
`api_key`, `role`, `max_tokens`, `temperature`, `tools`, `sleep`, `stream` and
`stream_queue_width`. There is **no thinking parameter and no extra-body seam**,
so nothing downstream of it — a drone, a host, or anything else dialling through
it — can send `chat_template_kwargs`. The mode is whatever the server defaults
to, and for this prompt shape that default is *on*.

The first attempt made the failure mode visible in the cleanest possible way: at
`max_tokens=256` the call spent **all 256 tokens thinking and emitted no
content**, and the drone recorded `outcome: failed` with zero calls accepted.
That is `#32`'s exact shape — a model handed a budget it cannot answer within —
reproduced in a second lane.

With thinking off the *cost* target would clear comfortably: 3 calls × ~15
tokens ≈ 45 tokens, **0.94%** of the authoring turn against a 5% target.

### But "turn thinking off" is not a free fix, and this document should not imply it is

The arithmetic above is a **cost** projection and nothing more. Disabling
reasoning on a reasoning model is a **quality decision wearing a configuration
flag**, and the consequence is unmeasured here:

- `t8`'s **"106 of 106 answered"** measured *that an answer came back in the
  declared shape*, not that it was **right**. Acceptance is schema compliance.
  Correctness was not graded.
- The width rung inherits the same limit. It ran effectively thinking-off at
  14.2 tokens per call with **100% acceptance in all 48 cells** — and its
  outcome metric is `items_per_second`, with acceptance deliberately kept out
  of the numerator. **No cell in that series checked whether a scoped answer
  was correct.** That is by design for a throughput rung, and it means the
  series says nothing about what thinking-off costs in quality.
- So a "fix" that meets a token budget by removing the model's reasoning would
  be **optimising the metric rather than the outcome** — the failure this repo
  has recorded under other names, most recently a ranking that measured
  interface compliance and called it play.

The honest remedy is therefore **not** "ship it thinking-off". It is: make the
mode **controllable and pinned**, then measure **cost and quality at each
setting** on a task with graded answers. A scoped question with a small
enumerable answer space is exactly the shape where thinking-off *might* be
free — and exactly the shape where that is cheap to check rather than assume.

The economics claim in [#45](https://github.com/agentculture/embodiment/issues/45)
is plausible and the tier as wired cannot reach it. Whether it can be reached
**without paying for it somewhere else** is an open question, not a
foregone one.

## The same gap, found twice in one hour

This is not a drone-only defect. `examples/arch_hive.py` — the Bee-Hive arm —
**defines** `wire_extra(thinking)`, which maps a sampling table's declared mode
onto the wire, and **never calls it**. Its `build_worker_factory` constructs a
plain `WorkerSeam`, which as established has nowhere to put the result. So the
`thinking` field in
[`arch-hive-sampling.json`](arch-hive-sampling.json) is **inert**: declared,
validated, never sent.

`examples/arch_arms.py` does wire it (its seam merges `wire_extra` into the
request body), which is why the gap is easy to miss — the vocabulary exists and
one consumer uses it.

**What this does and does not do to the width rung — corrected 2026-08-01.**
This section first said the width rung's thinking-off mode "came from the
server's default, not from the committed sampling table, so the reproduction
instruction is unenforced." **That was wrong, and the records say so.** The rung
does not dial through `examples/arch_hive.py`'s factory: its driver
[`bee-hive-width-raw/drive.py`](bee-hive-width-raw/drive.py) defines its own
`WireSeam`, merges `config.wire_extra(sampling.thinking)` inside `_post` so the
keys ride the existing retry path, and then **asserts what actually went on the
wire** from `last_body` rather than trusting the branch. All **48 of 48** cells
record `thinking_wire = {"chat_template_kwargs": {"enable_thinking": false}}`
and `thinking_wire_asserted: true`; not one cell recorded otherwise. The rung is
pinned, the reproduction instruction is enforced, and a server-side default
change would be caught rather than absorbed.

What is true is the arithmetic either way: the rung spent **102,336 completion
tokens over 7,200 calls — 14.21 tokens per call**, matching `t8`'s
thinking-*off* figure of ~15. The gap is therefore confined to
`examples/arch_hive.py`, which the series did not use as its transport — and
the reason it is easy to state too broadly is that the *drone* lane, which does
dial a plain `WorkerSeam`, genuinely has no way to pin the mode at all.

This is the `t18` defect class — *a configuration value with no consumer* —
found the same way `t18` was: by trying to use the seam rather than by reading
it. `t18`'s remedy was a provoked vacuity assertion, and the same is owed here.

## What is owed

1. **`WorkerSeam` gains a thinking/extra-body parameter**, so a caller can send
   `chat_template_kwargs` at all.
2. **`arch_hive` calls `wire_extra`** and passes the result, with a vacuity
   assertion that fails if the declared mode stops reaching the payload. The
   shape to copy already exists and is proven in a live series:
   `bee-hive-width-raw/drive.py`'s `WireSeam` plus the `thinking_wire_asserted`
   field it writes into every cell record.
3. **Re-measure this signal at *both* settings** once thinking is controllable,
   on a task whose answers can be **graded** — not just accepted. The cost
   prediction, stated before the re-run: ≈45 completion tokens, ≈0.94% of
   authoring. The **quality** prediction is deliberately not stated, because
   nothing here supports one: if thinking-off answers turn out worse, the
   honest outcome is that the ≤5% target is **unreachable at equal quality**
   and the signal itself needs revising — not that the signal passed.

Filed rather than fixed here, because fixing it changes the transport the width
rung was measured on and this cycle is closing.

## Reproduce

```bash
# authoring — one cortex turn, streamed
COLLEAGUE_API_KEY=… OUT_DIR=. uv run python examples/author_drone.py

# evocation — two runs against the live worker
EMBODIMENT_LIVE_RIG=1 EMBODIMENT_DRONES_ENABLED=1 \
EMBODIMENT_WORKER_URL=http://thor…:8000/v1 \
EMBODIMENT_WORKER_MODEL=unsloth/Qwen3.6-35B-A3B-NVFP4 \
  uv run python examples/drone_host.py index-gaps --repo .
```

The drone under test is `.drones/index-gaps`, authored by the cortex for a task
this repo genuinely needed twice: finding results documents missing from the
index. On the run above it found **3**, correctly.
