# Pre-registration — does the worker seat drive embodiment's own tool loop?

Plan task `t1` of `config-not-minds-strategist`. Written **before** the series
was dialled, because this repo has four experiments that tied at a ceiling and
the lesson recorded from them was to declare the ladder in advance rather than
after seeing the tie.

- **Date** — 2026-08-04
- **Rig** — `lobes` at `http://localhost:8001`, role `worker`,
  `unsloth/Qwen3.6-35B-A3B-NVFP4` (proxied, thor). `cortex` ready and untouched
  by this probe; `senses` `ready=false` at dial time and not used here.
- **Instrument** — `worker-toolloop-probe.py`, driving `embodiment.loop.run`
  itself rather than a stand-in turn driver.
- **Budget** — `max_tokens=16000` per `d16`; `max_steps=10`.
- **Clock** — derived, not chosen: `16000 / 12.921 tok/s = 1238.3 s`, the
  divisor named by the `scoped_run` calling pattern in the committed rate
  config, which explicitly rejects the width-1 reading (76.426 tok/s) because
  nothing reserves this deployment.

## What is being decided

Assumption `c33`: **nothing committed measures this model driving a tool loop.**
Arm B is explicitly "no loop, no turn, no goal"; the worker throughput, seam and
scoped-overhead records all measure single calls. The three-tier design
(decision `c26`) promotes this seat to the acting loop, so the capability at the
centre of the design is unmeasured.

Honesty condition `h12` requires the promotion verdict to cite a measured result
supporting the worker **as acting loop**. This series is that measurement, or it
is the reason there is not one.

## The rungs, declared in advance

Each rung is dialled only if the one before it is at ceiling. A ceiling is
**not** a pass: it is the condition `M4` says measures nothing, and the response
is to escalate, not to report a clean sweep.

| rung | shape | what it can separate |
|---|---|---|
| **R1** | Two `read_sensor` calls, then `average` over an **array**, then `finish`. Four calls, two data-dependent. | whether the seat can hold a multi-step loop at all, and whether the `#33` array-argument failure appears |
| **R2** | R1 plus a tool that **fails once** and must be retried with corrected arguments | whether the seat recovers from a tool error inside the loop, rather than only executing a happy path |
| **R3** | R1 with a **distractor tool** whose description invites a plausible wrong call, and an instruction that does not name the tool sequence | whether the seat selects tools on the task rather than on being told the sequence |

R1's instruction deliberately names the sequence. That makes R1 an **easy**
rung by construction: it measures protocol mechanics, not tool selection. This
is stated here so that a clean R1 is not later read as evidence of more than it
is.

## Thresholds

Fixed before dialling, per rung, over `n = 12`:

| measure | R1 bar | why this bar |
|---|---|---|
| runs completing ≥ 2 tool calls | **12/12** | below this the seat cannot hold a loop and the three-tier design fails at its premise |
| runs reaching `finish` | **≥ 11/12** | one stall is tolerable; a pattern of stalls is the `exit=stopped` collapse this rig has measured before |
| correct answer (51.5) | **≥ 11/12** | the arithmetic is trivial; this measures whether the loop's results reach the answer, not reasoning |
| runs with a malformed-argument event | **0/12** | the `#33` shape refused 74% of one arm's calls. Any occurrence here is a finding, not noise |
| runs with a truncated turn | **0/12** | at 16000 tokens `t24` measured 0 of 58. A non-zero count here re-opens `#37` for the acting seat |
| transport errors | **0** | a transport failure voids the cell rather than counting as a model failure |

## Verdict rule

- **SUPPORTS PROMOTION** — every R1 bar met, *and* R1 is not at ceiling on the
  discriminating measures, *or* an escalated rung was dialled and met its bars.
- **AT CEILING — ESCALATE** — every bar met with no headroom on any measure. R1
  then supports only the narrow claim *no loop failure observed at n=12 on an
  easy, sequence-named task*, and R2 is dialled before any promotion claim.
- **DOES NOT SUPPORT** — any bar missed. The three-tier decision `c26` returns
  to the operator through `/deviate`; it is not repaired by swapping models or
  retrying quietly. Plan risk `r3` names this response in advance.

## What this cannot establish

- One rig, one model, one task shape, `n=12`. Nothing here estimates a rate
  between the extremes it might find.
- It measures the **acting protocol**, not acting **quality**. Whether the
  worker acts *well* under governance is `t14`'s and `t15`'s question.
- The tool surface is hermetic and trivially cheap. A real surface with slow,
  failing, or approval-gated tools is a different measurement.
- `senses` was `ready=false` at dial time, so nothing here touches the
  interaction tier or the checkpoint change recorded as confound `c20`.
