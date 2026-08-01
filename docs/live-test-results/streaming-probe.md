# Does streaming retire the timeout class? — measured

**Dialled 2026-08-01, cortex idle** (the orchestrator-worker ladder had exited;
`ps` showed no `drive.py` or `ladder.py`). Raw records:
[`stream-probe.json`](stream-probe.json). Harness:
[`examples/stream_probe.py`](../../examples/stream_probe.py).

## The question

Four clocks in this cycle were sized against the wrong quantity, and the worst
of them — `GATEWAY_READ_TIMEOUT`, 600 s, in the lobes gateway process — is one
**no client-side value can raise**
([lobes-cli#169](https://github.com/agentculture/lobes-cli/issues/169)). It cost
the live series two repetitions at 2460 s each.

Streaming is the proposed structural answer: with chunks flowing, a client
bounds *idle time between chunks* rather than total request time, so generation
length stops being the binding quantity. That only works if two things are true,
and both are facts about this deployment rather than about SSE:

1. the gateway's read timeout must be **per-read**, not a total deadline;
2. a thinking model must emit chunks **during the think** — otherwise the gap
   before the first content chunk *is* the whole reasoning phase, and a long
   think trips an idle bound exactly the way a long generation tripped a total
   bound.

## 1. The gateway's bound is per-read — read, not assumed

`lobes/gateway/server.py` sets `conn.sock.settimeout(read_timeout)` on the
upstream socket, then relays:

```python
while True:
    chunk = resp.upstream.read(_CHUNK)
    if not chunk:
        break
    self.wfile.write(frame_chunk(chunk))
    self.wfile.flush()  # SSE must flush per chunk or it buffers until EOF
```

A Python socket timeout applies **per operation**, so every chunk read starts a
fresh 600 s window. Under streaming that constant stops being a total-request
deadline and becomes an inter-chunk idle bound. This is also exactly why the
non-streaming path failed at 600 s: one `read` of a buffered body waits the
entire generation.

## 2. Measured: chunks flow throughout the think

| | thinking on | thinking off |
|---|---:|---:|
| total seconds | 43.45 | 18.16 |
| completion tokens | 1101 | 468 |
| chunks | 390 | 164 |
| time to first chunk (s) | **0.263** | 0.248 |
| **max inter-chunk gap (s)** | **0.124** | 0.116 |
| mean inter-chunk gap (s) | 0.111 | 0.110 |
| first content delta at (s) | 43.221 | 0.248 |
| usage in terminal chunk | **yes** | **yes** |

With thinking on, the **first content delta does not arrive until 43.2 s** — the
very end. Under a non-streaming client that is a 43 s wall of silence, and it is
the same shape that killed two series calls at 600 s. Streaming, the largest gap
between chunks is **0.124 s**: a **~4,800×** margin against even a 600 s idle
bound, and comfortable against one of 5 s.

## 3. The field name is `reasoning`, and this probe got it wrong first

The first run reported **0 reasoning deltas** while 390 chunks arrived 0.11 s
apart — an incoherent reading that was the tell. The stream carries
`delta.reasoning`, not vLLM's documented `delta.reasoning_content`:

```json
{"role": "assistant", "content": ""}
{"reasoning": "Here"}
{"reasoning": "'s a thinking"}
{"reasoning": " process:\n\n"}
```

Delta keys across a whole stream: `content`, `reasoning`, `role`.

**A consumer written against the documented name sees zero reasoning and
concludes the model does not stream its thinking** — the opposite of the truth,
with nothing in the record to flag it. This repo has recorded that lesson
before, in `video-perception-probe.md`: *a capability probe must vary the
delivery path before reporting a capability absent.* Here the delivery path was
right and the **field name** was wrong; the correction is the same.

**The committed record above still carries the defect, and is left as measured.**
Both runs in [`stream-probe.json`](stream-probe.json) report
`reasoning_deltas: 0`, because both were produced by the pre-correction harness
— the finding was made by dumping delta keys beside it, not by re-running. The
zero is an artifact of the field name, not a fact about the rig, and the table
above already reads the truth off the gap between it and the 390 chunks. The
harness was fixed in task `t5` (it now counts either name and reports every
delta key it saw, so a rig that renames the field shows up as a **new key**
rather than as a silent zero) and the record was **not** re-run: nothing here
turns on the count, and re-dialling to make an artifact look tidier is how a
record stops meaning what it says.

Task `t5` also checked the **non**-streamed shape on the same rig
(2026-08-01, one completion): message keys are
`['annotations', 'audio', 'content', 'function_call', 'reasoning', 'refusal', 'role']`.
So `reasoning` is this deployment's name on *both* transports, and
`reasoning_content` appears nowhere — which is why the SSE reader's reassembled
message emits that one key rather than both. It still *accepts* both on input.

## What this settles, and what it does not

**Settled — streaming retires the total-request clocks.** Both
`GATEWAY_READ_TIMEOUT` and a client `REQUEST_TIMEOUT` become idle bounds with
four orders of magnitude of margin. The reasoning is also *visible* as it
arrives, which was the operator's other ask. Metering survives: the terminal
usage chunk arrives when `stream_options.include_usage` is set, so
`finish_reason` and all token counts still reach a record (claim c37).

**Not settled — the queue phase.** Both dials ran against an **idle** cortex, so
time-to-first-chunk of ~0.25 s says nothing about a *queued* request. The server
admits 2 concurrent sequences; a request waiting behind another legitimately
receives nothing until scheduled. The two-phase bound claim c38 requires — a
queue-aware time-to-first-chunk bound, and an inter-chunk bound only *after* the
first chunk — stands unmodified, and is the part this probe cannot discharge.

**Not solved at all — two of the four clocks.** `DEFAULT_FANOUT_TIMEOUT` bounds
a whole child *drive*, not one completion, and streaming a completion gives the
fan-out no progress signal. `RETRY_SLEEP_SECONDS` is an instrumentation defect —
a backoff timed inside the stopwatch of the call it retries — which no transport
change repairs. Streaming makes retries rarer; it does not make that constant
right.

## What `t5` built on this, and what it derived instead

`examples/worker_seam.py` — the transport for every cortex and worker dial in
the harness family, since `arch_arms.ArchSeam`, `arch_hive`,
`worker_throughput.ThroughputSeam` and `worker_scoped_overhead.ScopedSeam` all
ride it — now streams **by default** (deviation `d3`). `--no-stream` restores
the previous blocking transport.

Two clocks replace the one total-request deadline, and only one of them comes
from this page:

| Clock | Value | Where it comes from |
|---|---:|---|
| `STREAM_FIRST_CHUNK_TIMEOUT` | 2958.6 s | **Derived, not measured.** `STREAM_QUEUE_MARGIN × ((SERVER_MAX_NUM_SEQS − 1) × REQUEST_TIMEOUT + queue allowance)` = `2 × (1 × 1300.0 + 179.3)`. Queue depth 1 because the server admits 2 sequences; the allowance is the 179.3 s in `timeout-rate-measurements.json` |
| `STREAM_IDLE_TIMEOUT` | 60.0 s | Sized from cadence, cross-checked two ways: **484×** the 0.124 s largest gap above, and **310×** the 0.193 s mean inter-token interval the slowest committed per-stream rate (5.17 tok/s) implies |
| `STREAM_TOTAL_TIMEOUT` | 4258.6 s | The two phases summed — the outer backstop for a transport that dribbles forever |

The idle bound is installed on the socket **only after the first chunk arrives**,
which is the part this page could not settle: both dials here ran against an
idle cortex, so the ~0.25 s time-to-first-chunk says nothing about a queued
request, and an idle clock started at `t = 0` would kill one. That is why phase
one is derived from the queue model rather than from anything measured here, and
why the derivation is written down beside the constant rather than left in a
commit message. `tests/test_timeout_bounds.py` recomputes all three from
committed inputs and goes red if any drifts below its floor.

Two figures on this page are now **pinned** by that test: the 0.124 s largest
gap is read out of `stream-probe.json` rather than retyped, and
`--max-num-seqs=2` is read out of `timeout-rate-measurements.json`. Neither can
go stale silently.

**Transport, named:** every figure on this page was produced by the SSE
transport. Every *other* live-test result in this directory was produced by the
blocking one, and none of them was re-run or re-graded for this change (the
`d16` line). Latency figures are not comparable across the two; token accounting
is unaffected, because the terminal usage chunk is passed through verbatim.

## Reproduce

```bash
COLLEAGUE_API_KEY=… STREAM_PROBE_OUT=stream-probe.json \
  python3 examples/stream_probe.py
```

Dial only when the cortex is idle.
