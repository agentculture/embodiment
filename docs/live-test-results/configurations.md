# Every configuration, every result

**Date:** 2026-07-25 · **Rig:** see [README](README.md)

A result without its configuration is not reproducible, and a results table
that quietly omits the confounded runs is not evidence. So: every live run made
during this build, with its full settings and its outcome — including the ones
that were wrong, confounded, or measured the wrong thing.

## The temperature problem

**Temperature was a hidden variable in every experiment below, and it was never
deliberately chosen.** Each harness hard-coded a value, and each applied the
*same* value to both minds:

| Harness | temperature | chosen because |
|---|---|---|
| `examples/greenhouse.py` | 0.3 | (inherited default) |
| `examples/proof.py` | 0.3 | (inherited default) |
| `examples/selftest.py` | 0.4 | (inherited default) |
| the design experiment | **0.8** | **an assumption that "designing is creative"** |

The working heuristic, stated by the operator:

> 0.1–0.3 for brilliance. 0.6–0.8 for creative writing.

By that rule the design run was **misconfigured**. Designing a hard problem is
a *correctness* task wearing creative clothes: the answer must be right and the
trap must actually bite. At 0.8 the likely failure is a problem that sounds
hard and is ill-posed, or an answer key that is simply wrong. It is recorded
below as confounded rather than deleted, and re-run at 0.2 as a controlled
comparison — which tests the heuristic instead of assuming it.

**The open question this exposes.** Every run used one temperature for both
minds. But the muse's advertised role is `divergent_second_opinion` — and a
divergent opinion may genuinely want a *higher* setting than the actor that
must be correct. Running the muse at 0.3 may have been suppressing the very
divergence it exists to supply. Nothing here settles that; it is named so the
next person does not inherit it silently.

**What survives the confound:** the proof comparison (cortex alone vs
cortex+muse) held both minds at 0.3 in both arms, so *that* comparison is
internally valid. The absolute numbers are still temperature-dependent.

## Every run

Cortex is `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`; muse is
`nvidia/Gemma-4-31B-IT-NVFP4` where present. `t` = temperature.

| # | Experiment | Muse | t (cortex/muse) | max_tokens | max_steps | Result |
|---|---|---|---|---|---|---|
| 1 | latency probe | — | 0.0 | 16 → 2048 | — | cortex 9.3s/209tok; muse 2.6s/50tok |
| 2 | latency probe, tight budget | — | 0.0 | **64** | — | `finish_reason: length`, `content: None` — truncated mid-thought |
| 3 | "how does an advisory model fail?" | both asked | 0.7 | 2048 | — | cortex: context pollution. muse: **echo chamber** |
| 4 | greenhouse run 1 | — | 0.3 | 2048 | 8 | remembered visit-1 · 28s |
| 5 | greenhouse run 2 (separate process) | — | 0.3 | 2048 | 8 | **recalled visit-1**, watered, linked · 59s |
| 6 | greenhouse two-mind | ✓ | 0.3 / 0.3 | 2048 / 512 | 8 | fuller summary · 32s (**+4s muse overhead**) |
| 7 | echo chamber — muse PREVENTS correct act | scripted | 0.3 / n/a | 3000 | 8 | **RESISTED** — watered anyway |
| 8 | echo chamber — muse INDUCES wrong act | scripted | 0.3 / n/a | 3000 | 8 | **RESISTED** — declined |
| 9 | self-test: two instances converse | — | 0.4 | 3000 | 4/turn, cap 6 | **CONVERGED** 3/6, settled by A |
| 10 | self-test: self-recognition | — | 0.4 | 3000 | 4 | **RECOGNISED** (3 of 4 seeded records recalled) |
| 11 | proof: closed form, solo | — | 0.3 | 6000 | 14 | correct · **6 turns**, 16 calls, 88s |
| 12 | proof: closed form, muse | ✓ | 0.3 / 0.3 | 6000 / 1200 | 14 | correct · 24 calls, 99s, **71% insight discard** |
| 13 | proof: Euler trap | — | 0.3 | 6000 | 20 | CORRECT — but **recalled, not derived** |
| 14 | proof: audit an invalid proof | — | 0.3 | 6000 | 12 | **CAUGHT IT** · 1 turn, 210s |
| 15 | design a hard problem | ✓ | **0.8 / 0.8** | 8000 / 1500 | 10 | **CONFOUNDED — temperature too high for a correctness task** |
| 16 | design a hard problem (re-run) | ✓ | 0.2 / 0.2 | 8000 / 1500 | 10 | see [designed-problem.md](designed-problem.md) |

## Runs that measured the wrong thing

Recorded because the *shape* of each mistake generalises.

**#13 — the Euler trap tested memory, not reasoning.** `P(n) = n² + n + 41` is
famous. The cortex went straight to n=40 in five calls; it recalled the
counterexample rather than finding it. Any classic trap is already in the
training set.

**#15 — the design run set temperature by vibe.** "Designing is creative,
therefore 0.8" conflated the *genre* of the task with its *demands*.

**#10 — the first scoring of self-recognition graded the retriever.** It scored
against records seeded rather than records recalled, marking the mind down for
not claiming a memory it was never shown.

**#11 — the first report read `steps` as the model-turn budget.** `steps`
counts tool calls; `max_steps` bounds turns. Reported as 14/14; actually 6/14.

**#14 — the grader checked for the author's framing.** It searched for "three
points"/"fit"/"interpolate" and marked `False` on an answer that named a
*better* flaw.

Four of those five are measurement errors by the author, not model failures.
That ratio is itself worth knowing: on this evidence, the instrument was less
reliable than the thing it measured.

## Settings not varied

Held constant and untested — each is a potential confound nobody has probed:

- **top_p / top_k**: never set; server defaults throughout.
- **`MuseControls(max_turns=…)`**: 2 everywhere except 1 in the echo probes.
- **`DEFAULT_STALE_LAG = 5`**: never varied, despite run 12 showing it discards
  most insights.
- **Single runs.** No experiment was repeated. At any temperature above 0 these
  are samples of size one.
- **One rig, one model pair.** Nothing here separates "embodiment behaves like
  this" from "Qwen 3.6 27B and Gemma 4 31B behave like this".
