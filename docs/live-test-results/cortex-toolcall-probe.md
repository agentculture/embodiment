# Does the new cortex hold the tool protocol? — and what it costs in wall clock

**Date:** 2026-07-31 · **Gateway:** spark, `localhost:8001` · **Role:** `cortex`
· **Model:** `unsloth/Qwen3.6-27B-NVFP4` · **n = 10**

The operator's stated expectation of the upgrade was *"probably more stable tool
calling — real win here"* (frame claim `c44`). This repo has a documented
baseline to beat, so the expectation was measured rather than inherited
(`c45`/`h33`).

## The baseline it is being measured against

Protocol failure is a first-class, previously-measured failure mode here:

- the `exit=stopped` collapse, where a mind reasons *past* its tool call and
  ends on prose — repaired from 25% to 67% by forcing a tool call per step
  ([scratchpad.md](scratchpad.md));
- [#32](https://github.com/agentculture/embodiment/issues/32) — handed a pad, a
  mind wrote tool calls and **no prose**, 8 turns, zero content;
- [#33](https://github.com/agentculture/embodiment/issues/33) — `command` sent
  as a string rather than an array, refusing 17 of 23 calls.

So "well-formed tool call" is not a given on this rig's history.

## The probe

A task needing a couple of reasoning steps, with a `finish(answer: integer)`
tool it **must** call to close — deliberately giving the mind something to
reason past. Ten independent runs, `max_tokens=16000` per `d16`.

> A train leaves at 09:14 and arrives at 13:02 the same day. It stops for 7
> minutes at each of 4 intermediate stations. How many minutes is it actually
> moving?

Truth: `(13:02 − 09:14) − 4×7 = 228 − 28 = **200**`.

## Result — clean sweep, and that is the problem with it

| measure | result |
|---|---|
| closed through the `finish` tool | **10 / 10** |
| well-formed arguments | **10 / 10** |
| correct answer (200) | **10 / 10** |
| ended on prose, never calling the tool | **0 / 10** |
| transport errors | 0 |

Every run returned `finish_reason: tool_calls` with parseable JSON arguments.
On this probe, the protocol held perfectly.

**Read this as a floor, not as a margin.** 10/10 is a ceiling with no headroom,
which is precisely the condition `M4` says measures nothing — four prior
experiments here died exactly this way. It establishes that the new cortex does
not fail the protocol on an easy, single-call task. It cannot distinguish
"much better than the old cortex" from "adequate", and it says nothing about
multi-turn drives, tool-heavy surfaces, or the hard rungs where the old
collapse actually appeared. The honest claim is *no protocol failure observed
at n=10 on an easy task*, and nothing wider.

## The unplanned finding: it is slow

| | seconds |
|---|---|
| median latency | **57.2** |
| fastest run | 25.3 |
| slowest run | **204.3** (4,042 completion tokens) |

Completion tokens ranged 567–4,042 for a task whose answer is one integer —
this is a thinking model and reasoning dominates, as expected. But the spread
matters more than the median: **an 8× range between the fastest and slowest run
of an identical prompt.**

Comparison to the old cortex is available but *not* like-for-like: the
[9.3 s / 209 token](README.md) figure was measured on a trivial prompt ("reply
with exactly: X"), not on a reasoning task, so it does not establish that the
new model is slower. What this probe does establish on its own terms is a cost
the series must plan around — roughly a minute per cortex reasoning turn, with a
tail several times that.

## Consequences for the series

1. **Wall-clock budgeting is real.** A multi-turn drive at ~57 s median per
   cortex turn, with a 200 s tail, sets the achievable `n` per cell. The
   pre-registration (`t11`) should size cells against measured latency rather
   than optimism.
2. **The delegation hypothesis gets *more* interesting, not less.** A slow,
   serial cortex beside a worker measured at 76 tok/s single-stream
   ([worker-throughput.md](worker-throughput.md)) is exactly the asymmetry an
   orchestrator arm is supposed to exploit. This does not predict the result —
   it says the effect, if any, has room to appear.
3. **Protocol stability cannot be an outcome metric.** With the control at
   10/10 there is nothing to improve, so no arm can separate on it. If the
   series wants to measure protocol robustness it needs a harder surface.

Raw per-run records are in this document's table above; the probe script is
reproducible from the parameters given (fixed prompt, single tool, `n=10`).
