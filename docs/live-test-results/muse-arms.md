# The three muse arms — results (plan task t18, issue #21)

Contract: [`muse-arms-preregistration.md`](muse-arms-preregistration.md),
committed with the harness and before the first measured dial. This document
reports what that pre-registered protocol produced. Nothing here — not an arm,
not a threshold, not the classification rule, not the decision rule — was chosen
after seeing a number.

**Run date:** 2026-07-30. **Harness:**
[`examples/muse_arms.py`](../../examples/muse_arms.py), unmodified by this task.
**Raw transcript:** [`muse-arms.jsonl`](muse-arms.jsonl) — every prompt, every
reply, every tool call and every tool result for all 24 runs, nothing summarised
away. **Run config:** [`muse-arms-config.json`](muse-arms-config.json)
(`measured: true`). **Every results table in §§1–6 is `--analyse` output pasted
verbatim**; the three summary tables in §§3a–3b and the two framing tables above
them are counted off the same committed transcript and are labelled where they
appear.

## What ran, and what did not

| | |
|---|---|
| runs attempted | 24 |
| runs completed | **24** |
| arms | A, B, C — all three |
| repeats per arm | **n = 8** |
| workspace provider | `docker` — **real containers**, `--smoke` never passed |
| transport failures | 0 |
| retries | 0 |
| state-mutating `devague` commands | 0 |

**n = 8 was fixed before the first dial**, above the pre-registration's floor of
6, and the whole series ran as **one invocation** that interleaves the arms
within each repeat (`A-0, B-0, C-0, A-1, …`). No arm could have been extended or
stopped after its own numbers were visible; the transcript's ordering is the
evidence.

Nothing in the pre-registered protocol was skipped. One thing was **added**: a
post-hoc diagnostic, described in §8, which touches no dependent variable.

## Read this first — three facts that govern every number below

1. **Arm A did not fail.** Eight of eight correct, **zero** confidently wrong.
   The baseline sits on a ceiling, so there is nothing for an arm to improve.
   The pre-registration's §10 names this exact case, and the series verdict is
   **`INCONCLUSIVE`** because of it. §7 applies the rule in full.
2. **The nine `NO_ANSWER` runs are not silence and not abstention.** Every one
   of them emitted a **tool call** on the tools-off closing turn, which the
   gateway's tool parser consumed and — there being no `tools` in the request —
   discarded, returning `content: null`. The muse spoke; nothing arrived. §8
   shows the decoded bytes.
3. **Seventeen of arm C's 23 workspace calls never executed anything.** The muse
   passed `command` as a string 15 times and as a JSON-encoded list inside a
   string twice; `MuseWorkspace` refused all 17. Only 6 calls reached a
   container. That is the pre-registered `INCONCLUSIVE` condition for DV3, and
   §3 reports the argv contract failure as the finding rather than computing a
   distribution over the survivors.

## Run configuration, as recorded

| field | value |
|---|---|
| muse | `nvidia/Gemma-4-31B-IT-NVFP4`, temperature 0.3, `max_tokens` 3000 |
| cortex | none — this experiment dials no cortex |
| gateway | `http://localhost:8001/v1`, bearer auth |
| `max_turns` / `max_quiet_turns` / `max_tool_rounds` | 8 / 2 / 4 |
| `max_context_chars` / `max_insight_chars` / `max_tool_result_chars` | 4000 / 4000 / 2000 |
| identity | unconfigured — no Gwen framing in any arm |
| oracle | `truth()` = 76, trap = 72, `matches_pin: true` |
| base commit | `0cc0b73` (`merge(t17): the three-arm validation harness on the verified oracle (#21)`) |

The added-system-text confound the pre-registration declared in advance
(§4) reproduced exactly at run time: arm B carries **+1 828 characters** over
arm A and arm C **+2 507**, every byte of it a constant imported from the module
that owns the tool. Every arm's exact system message is in the transcript. Any
positive reading of arms B or C is *"tools plus being told about them"*, and
this design cannot separate the two.

---

## The tables

```text
runs: 24 · muse: nvidia/Gemma-4-31B-IT-NVFP4 · provider: docker
oracle: truth=76 trap=72 rejected=True matches_pin=True
```

### 1. Final answers — DV1, the headline

| arm | n | CORRECT | WRONG_TRAP | WRONG | ABSTAINED | MALFORMED | NO_ANSWER | confidently wrong |
|---|---|---|---|---|---|---|---|---|
| A | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0/8 (0%) |
| B | 8 | 2 | 0 | 1 | 0 | 0 | 5 | 1/8 (12%) |
| C | 8 | 4 | 0 | 0 | 0 | 0 | 4 | 0/8 (0%) |

**Against the 5-of-6 baseline.** The motivating number is that **5 of the muse's
6 measured failures stated a definite wrong final answer**
([`muse-cycle-baseline.md`](muse-cycle-baseline.md) §2, itself a prose
correction of a classifier that had reported zero). This series measured
**1 confidently-wrong answer in 24 runs** — `B-7`, which stated `ANSWER: 89`,
with no hedging vocabulary in its final turn.

That comparison must be read the way the pre-registration insisted: **the 5-of-6
is not this series' control and is not evidence about these arms.** It was
measured on the muse *model* driven through the **acting** loop on the `register`
and `entropic3b` problems with a tool bench under a 14-turn budget; this series
drives the **thinking** loop on the subset problem. Arm A is the control here,
and arm A's rate is **0 of 8**. The honest statement is: *on this problem, in
this loop, this muse does not state confidently wrong answers often enough to
measure a reduction in them.* The headroom the pre-registration expected was not
present.

`B-7` is worth reading in the transcript. It is not a guess: it builds a correct
two-parity DP, states the recurrence correctly, then seeds `dp[1]` with only two
of the four base cases, and iterates cleanly to 89 — a Fibonacci number. The one
confidently-wrong answer in the series is a careful derivation off a wrong base
case, not a shortcut.

**The lenient parse is recorded and is never the verdict.** `B-7`'s lenient
answer is also 89, so it changes nothing here; the column exists because
`association-work.md` learned that an exhaustive enumeration contains the truth
by construction.

### 2. Pad protocol adherence — DV2

| arm | runs | entries | intend | observe | conclude | revise | open intents | rejected | off-protocol |
|---|---|---|---|---|---|---|---|---|---|
| B | 8 | 44 | 22 | 22 | 0 | 0 | 0 | 0 | 0 |
| C | 8 | 21 | 19 | 2 | 0 | 0 | 17 | 0 | 0 |

**The predicted failure did not happen in arm B, and did happen in arm C.** The
pre-registration's prediction, from issue #21's n=1 probe, was *five intents,
zero observations, zero conclusions, five open intents at exit.*

- **Arm B refutes it.** All 8 runs alternate `intend` → `observe` perfectly, and
  **every run exits with 0 open intents** (per-run: 3/3, 2/2, 3/3, 3/3, 3/3,
  2/2, 3/3, 3/3). Handed a pad and nothing else, this muse follows the pad
  protocol.
- **Arm C reproduces it almost exactly.** Six of 8 runs wrote **intents only**
  and left every one open (`intend, intend, intend`); only `C-4` and `C-5`
  alternated. 17 of 19 intents are still open at exit.

The difference between the two arms is the workspace. The reading the data
supports: an intent whose observation would have come from a tool call that was
refused never gets its observation. The pad protocol did not fail on its own
terms — it failed downstream of the argv contract in §3.

**Zero conclusions in either arm, 0 of 65 entries.** Both arms use the pad as a
running work log and neither ever `conclude`s. `rejected_calls` and
`off_protocol_calls` are 0 across all 16 pad runs: no hallucinated `finish`, no
malformed pad call.

### 3. Arm C executions — DV3 and rule R-C1

| arm | executions | arithmetic-offloaded | reasoning-displaced | degraded | refused argv |
|---|---|---|---|---|---|
| C | 23 | 3 | 3 | 17 | 17 |

| run | # | class | signals | audit | truth in output | command |
|---|---|---|---|---|---|---|
| `C-0` | 0 | degraded | — | — | False | `` |
| `C-0` | 1 | degraded | — | — | False | `` |
| `C-0` | 2 | reasoning-displaced | full-domain/enumerates-subsets/parity-constraint/adjacency-constraint | — | False | `python3 -c from itertools import combinations; s = range(1, 11); count = 0; [ (c…` |
| `C-1` | 0 | degraded | — | — | False | `` |
| `C-1` | 1 | degraded | — | — | False | `` |
| `C-1` | 2 | degraded | — | — | False | `` |
| `C-2` | 0 | degraded | — | — | False | `` |
| `C-2` | 1 | degraded | — | — | False | `` |
| `C-2` | 2 | arithmetic-offloaded | enumerates-subsets | — | False | `"python3" "-c" " def is_valid(subset): sorted_s = sorted(subset) for i in range(…` |
| `C-2` | 3 | reasoning-displaced | full-domain/enumerates-subsets/parity-constraint/adjacency-constraint | — | False | `python3 -c " def is_valid(subset): sorted_s = sorted(subset) for i in range(len(…` |
| `C-3` | 0 | degraded | — | — | False | `` |
| `C-3` | 1 | degraded | — | — | False | `` |
| `C-3` | 2 | reasoning-displaced | full-domain/enumerates-subsets/parity-constraint/adjacency-constraint | — | True | `python3 -c from itertools import combinations def has_consecutive(subset): sorte…` |
| `C-4` | 0 | degraded | — | — | False | `` |
| `C-4` | 1 | arithmetic-offloaded | full-domain/enumerates-subsets/parity-constraint | AUDIT | True | `python3 -c from itertools import chain, combinations def is_valid(subset): sorte…` |
| `C-5` | 0 | degraded | — | — | False | `` |
| `C-5` | 1 | arithmetic-offloaded | full-domain/enumerates-subsets/parity-constraint | AUDIT | True | `python3 -c from itertools import chain, combinations def is_valid(subset): sorte…` |
| `C-6` | 0 | degraded | — | — | False | `` |
| `C-6` | 1 | degraded | — | — | False | `` |
| `C-6` | 2 | degraded | — | — | False | `` |
| `C-7` | 0 | degraded | — | — | False | `` |
| `C-7` | 1 | degraded | — | — | False | `` |
| `C-7` | 2 | degraded | — | — | False | `` |

#### 3a. The argv contract failure — the finding DV3 actually produced

**17 of 23 workspace calls never ran anything**, and every one of the 17 failed
the same way: the muse handed `command` as a string rather than as an
already-split argv list.

| what the muse passed | calls | outcome |
|---|---|---|
| a list of strings | 6 | accepted, reached a container |
| a plain string | 15 | refused |
| a string containing a JSON-encoded list | 2 | refused |

The pre-registration predicted this shape from the n=1 wiring run and predicted
the muse would correct itself on a later round. At n=8 it corrects **sometimes**:
3 of 8 runs (`C-1`, `C-6`, `C-7`) never landed a single accepted command, and 2
runs (`C-4`, `C-5`) got it right on the second attempt. No run landed more than
two. `MuseWorkspace` is behaving as designed — it
refuses a string rather than inventing a quoting rule no shell would honour, and
feeds the refusal back as readable text — and the muse mostly does not act on
the correction.

**This is DV3's pre-registered `INCONCLUSIVE` condition, and it is reported as
such.** §10 of the contract: *"Arm C's execution classification reports
`INCONCLUSIVE` when fewer than 6 executions across the arm reached the
`arithmetic-offloaded` / `reasoning-displaced` split … In that case the finding
to report is the argv contract failure, named as such."* By the rule's own count
**exactly 6** executions reached the split, so the gate misses by one; once the
mandatory audit below is applied, **5** did, and the gate fires. Either way the
distribution over 5–6 surviving calls is not a result, and it is not presented
as one.

#### 3b. The mandatory hand-audit — `C-4#1` and `C-5#1`

R-C1 reads the command, never the output, and the pre-registration named the
hole this leaves and bound this task to audit it: any execution classified
`arithmetic-offloaded` whose captured output contains a standalone `76` is
flagged `audit_required`, and **t18 must hand-audit each and report, agreeing
with the rule or not.**

```text
AUDIT REQUIRED (rule R-C1's named hole — hand-audit each and report): C-4#1, C-5#1
```

**Audit verdict: I disagree with the rule on both. Both are whole-problem
solvers and are in substance `reasoning-displaced`.**

Both commands are the same program. `C-4#1`, in full:

```python
from itertools import chain, combinations

def is_valid(subset):
    sorted_s = sorted(list(subset))
    for i in range(len(sorted_s) - 1):
        if sorted_s[i+1] == sorted_s[i] + 1:
            return False
    return True

def solve():
    s = set(range(1, 11))
    count = 0
    for r in range(11):
        for subset in combinations(s, r):
            if is_valid(subset):
                if sum(subset) % 2 == 0:
                    count += 1
    return count

print(solve())
```

It binds the domain to `range(1, 11)`, enumerates every subset, rejects any pair
differing by one, keeps the even sums, and prints the count. Run, it emits `76`
— the entire answer. `C-5#1` differs only in its outer loop bound. Neither is a
partial sum, a parity check, one candidate subset, a small-`n` instance or a
recurrence value; each is the conclusion, computed.

**Why the rule missed them, precisely.** Both carry `full-domain`,
`enumerates-subsets` and `parity-constraint` but not `adjacency-constraint`. The
adjacency token set requires `"+ 1 "` or `"- 1 "` **with a trailing space**;
these programs write `sorted_s[i] + 1:` and `len(sorted_s) - 1)`, where the next
character is a colon and a parenthesis. The command that *did* match — `C-0#2`'s
`sub[j]+1 for j in ...` — matched only because a space happened to follow. The
signal is a whitespace coincidence.

**This is the error the rule declared in advance, in the direction it declared.**
The pre-registration states R-C1 is *"conservative toward `arithmetic-offloaded`
… so the `reasoning-displaced` count is a floor, and the rule errs in the
direction that flatters arm C."* It does, and by 2 of 3.

**A second finding the audit turned up, outside the flagged set.** `C-2#2` — the
third and last `arithmetic-offloaded` execution — **did not execute at all**. Its
argv carried literal double quotes (`"python3"`), the container reported
`exit status 127 … no executable named '"python3"' exists in this environment`,
and its payload was truncated mid-source. It is classified as an execution
because `ArmTools._ran` reads only `MuseWorkspace`'s four refusal strings and
never looks at the job's exit status, so a headspace `status: failure` package
counts as "ran". It should be `degraded`. That is a defect in the harness's
`_ran` predicate, not in R-C1, and it is recorded here rather than fixed — the
harness is not this task's to change.

**The audited split, stated beside the rule's and never in place of it:**

| | rule R-C1, as recorded | after the mandated hand-audit |
|---|---|---|
| `arithmetic-offloaded` | 3 | **0** |
| `reasoning-displaced` | 3 | **5** |
| `degraded` | 17 | 18 |
| reached the split | 6 | **5** |

**Every command this muse actually got to run was a whole-problem solver. Not
one execution in the series offloaded arithmetic while keeping the reasoning.**
That is the shape the counter-hypothesis predicts — but on 5 executions, with
arm A at a ceiling and 74% of calls refused, it is an observation about what the
muse reached for, not a verdict about what execution does to reasoning.

### 4. Counsel quality — scored with the answer stripped out first

| arm | n | COUNSEL | RESTATED | SILENT | mean moves | mean words | guidance lines |
|---|---|---|---|---|---|---|---|
| A | 8 | 0 | 8 | 0 | 0.0 | 110 | 8 |
| B | 8 | 1 | 2 | 5 | 0.1 | 33 | 3 |
| C | 8 | 1 | 3 | 4 | 0.1 | 32 | 2 |

**Independence from correctness is mechanical here, not promised.**
`strip_answer_lines` runs before the scorer, so the scorer cannot see the
answer. The data shows the decoupling working in both directions: arm A is
**8/8 correct and 0/8 `COUNSEL`**, and `B-7` — the one confidently-wrong run in
the series — scored **`COUNSEL`**. Counsel and correctness moved independently
because the scorer cannot see one from the other.

**But counsel did not meaningfully move between arms, and the machine's
`COUNSEL` counts should not be read as saying it did.** All 14 guidance lines
the series produced are in the transcript, and read as one kind of move: *verify
this specific step or edge case.* The two that scored `COUNSEL` (`C-3`, `B-7`)
did so on the word **"considered"** — an `alternative-framing` marker — in
"verify if it has **considered** the empty set". That is not an alternative
framing. Conversely arm A's "as this is the most likely point of **failure**"
reads to a human as a failure-condition move and scores nothing, because the
marker list holds `fails`, not `failure`.

So the honest hand-reading, which the pre-registration reserves for a reader
(`counsel_soundness: "not machine-graded — read it"`): **counsel quality is
uniform across the three arms and shallow in all of them.** What differs between
arms is not the quality of counsel but its **volume** — mean content words fall
110 → 33 → 32 and guidance lines 8 → 3 → 2 — and that fall is caused by the
dropped closing-turn emissions in §8, not by tools crowding out reflection.

**The decision rule's counsel clause, read as written.** It asks whether an
arm's counsel distribution is *"not worse than arm A's (no drop in `COUNSEL`
count)"*. `COUNSEL` went **0 → 1 → 1**, so by that clause counsel did not drop
in either arm. Reporting only that would be true and misleading, so both the
clause's answer and the reading above are recorded. In any case the clause is
never reached: §7's gate fires first.

### 5. Did an execution move a stated belief?

| run | executions | belief changed | answers stated in order |
|---|---|---|---|
| `C-0` | 3 | unaskable | — |
| `C-1` | 3 | unaskable | — |
| `C-2` | 4 | unaskable | — |
| `C-3` | 3 | unaskable | 76 |
| `C-4` | 2 | unaskable | 76, 76 |
| `C-5` | 2 | unaskable | 76, 76 |
| `C-6` | 3 | unaskable | 76 |
| `C-7` | 3 | unaskable | — |

**Eight of eight `unaskable`, and that is the correct value, not a missing one.**
The pre-registration requires `null` rather than `false` when the question was
never posed. It never was: this muse states no definite answer *before* its
first execution in any run, so there is no before-and-after pair to compare. Four
runs state no definite answer at all; the four that do state one state it only
after every execution has happened.

A fabricated `false` here would have read as *"execution changed nothing"*, which
the data cannot support in either direction.

### 6. Health — degradation is data

| arm | n | exits | degradations | lane degradations | transport errors | truncated turns | closing turns | mean elapsed s |
|---|---|---|---|---|---|---|---|---|
| A | 8 | concluded×8 | 0 | 0 | 0 | 0 | 8/8 | 83.8 |
| B | 8 | budget×1, concluded×2, quiet×5 | 11 | 0 | 0 | 0 | 8/8 | 132.9 |
| C | 8 | concluded×2, quiet×6 | 13 | 0 | 0 | 0 | 8/8 | 75.0 |

- **Zero transport errors, zero truncated turns, zero workspace-lane
  degradations** in 160 model turns. Nothing was retried, and there was nothing
  to retry.
- **Every degradation recorded is `muse-tool-rounds-exhausted`** — 11 in arm B,
  13 in arm C, none in arm A. The pre-registration declared this in advance as
  real behaviour rather than a harness fault: *"the muse does not stop calling
  tools voluntarily."* At n=8 it does not: both tool arms spend their whole
  round allowance.
- **The closing turn was put to 24 of 24 runs**, including every arm A run.
  `closing_error` is `null` everywhere; no run hit the "not re-dialling after a
  transport failure" path.
- Arm A's exits are `concluded` 8/8 — it writes `[done]` and stops. Arm B and C
  exit `quiet` in 11 of 16 runs, which §8 explains.

---

## 7. The decision rule, applied

The rule was fixed in §10 of the pre-registration. Applied per arm, against arm
A, on DV1:

**Verdict: `INCONCLUSIVE`.**

The gate that fires is the first one listed: *"arm A's confidently-wrong rate is
**0** or **n** (a floor or a ceiling: nothing to move)."* **Arm A's
confidently-wrong rate is 0 of 8.** Every arm A run reached 76 by an explicit
two-parity recurrence, and all 8 cross-check the total against the 144
non-consecutive subsets before committing. There is no failure rate for a tool
lane to reduce, so no
comparison on DV1 can distinguish the arms, whatever the arms did.

This was named as a live risk before the run. The pre-registration's §2 argued
this series could resolve where `association-work.md` and the arena's muse
factor could not, *because* there was headroom on the record — and then said, in
the same paragraph, *"Arm A is this series' own baseline and its floor is
measured, not assumed. If arm A turns out to sit at a ceiling too, that is a
reportable `INCONCLUSIVE` and it gets reported."* It sat at a ceiling. It is
reported.

**The other gates, for completeness:**

- *"any arm ran n < 6"* — does not fire. All three ran n = 8.
- *"more than a third of an arm's runs degraded"* — **ambiguous, and stated
  rather than resolved by choosing the convenient reading.** Under a literal
  reading (a run that carries any degradation record), arms B and C degraded on
  8 of 8 and this gate fires too. Under §9's sense of the word — *"a degraded
  call is data … a transport failure"* — no run in the series degraded and it
  does not fire. The only degradation code present is
  `muse-tool-rounds-exhausted`, which the same pre-registration explicitly
  classifies as real behaviour and not a fault. The verdict is `INCONCLUSIVE`
  under both readings, so nothing turns on it here; the ambiguity is recorded so
  a later series fixes the wording before it does turn on it.

**What is *not* claimed.** Arm C's confidently-wrong rate (0/8) is not lower
than arm A's (0/8). Arm B's is *higher* (1/8). Neither `SUPPORTED` nor
`SUPPORTED-AT-A-COST` nor `NOT-SUPPORTED` is reachable from a control with no
failures to reduce, and none of them is asserted.

**DV3's own verdict is `INCONCLUSIVE` as well**, on its own pre-registered gate
(§3a): 5 executions reached the classification split after the mandated audit,
against a floor of 6. The reportable finding is the **argv contract failure**,
not a distribution.

---

## 8. The closing turn, the asymmetry, and what the silence actually was

**The asymmetry, stated plainly because every counsel claim above depends on
it: arm A's answer is volunteered; arms B and C's is elicited.** Arm A states
`ANSWER: 76` in its own first thinking turn, unprompted, in 8 of 8 runs. Arms B
and C fill their turn budget with tool calls and are then asked, by one constant
tools-off closing prompt appended to the muse's own message history, to commit to
a final integer. That closing turn is put identically to all three arms — arm A
included, where it merely repeats an answer already given — but its *role* is not
symmetric, and it exists only because deviation **`d7`** (issue #32) measured
that without it arms B and C would report `NO_ANSWER` on every run and DV1 would
have measured the turn budget rather than the arms.

**Any cross-arm comparison of counsel in this document is a comparison between a
volunteered response and an elicited one.** That is not a caveat that can be
removed by analysis; it is a property of the design.

### The closing turn did not fix `d7` — it moved it

Nine runs — 5 in arm B, 4 in arm C — came back `NO_ANSWER`. All nine share one
signature and no other run has it:

| | |
|---|---|
| `finish_reason` | `stop` (not `tool_calls`) |
| `completion_tokens` | 27 – 118, always non-zero |
| `content` | **empty** |
| `reasoning` | **empty** |
| parsed `tool_calls` | **none** |

The transcript stores parsed fields, so it cannot by itself say whether the muse
said nothing or whether something was dropped. **A post-hoc diagnostic settled
it** — [`muse-arms-closing-turn-diagnostic.json`](muse-arms-closing-turn-diagnostic.json),
which replays each of the nine recorded closing prompts against the same gateway
with `return_token_ids`, then decodes the reply through the gateway's own
`/detokenize`. It is **not part of the series, touches no dependent variable, and
is not a retry of a measured call** — the series was closed and committed before
it ran, and a replay is a fresh sample rather than the original bytes.

`B-0`'s closing reply, decoded — 28 tokens on replay against the 27 the measured
run recorded, `content: null`, `stop_reason: 50`:

```text
<|tool_call>call:intend{text:<|"|>I will manually compute the DP transitions for n=1 to 10.<|"|>}<tool_call|><|tool_response>
```

**Eight of the nine replays reproduced a tool-call emission of exactly this
shape**, each with `content: null` and `stop_reason: 50` — the tool-call
terminator — where an ordinary reply on this rig stops at `106`
(`<end_of_turn>`). Replayed token counts land on the recorded ones (118 vs 118,
42 vs 42, 27 vs 27, 41 vs 41, 28 vs 28, 42 vs 42, 27 vs 28, 37 vs 28), which is
what makes the mechanism legible rather than merely plausible. `C-0`'s was a
`workspace_run` call carrying a full Python program. The ninth, `C-2`, came back
as prose at `stop_reason: 106` — a reminder that a replay at temperature 0.3 is
a new sample and not a recovery of what was originally sent.

**So the mechanism is:** told to stop and commit to an integer, on a request
carrying **no** `tools`, this muse emits a **pad tool call in Gemma's native
tool-call token syntax**. The gateway's tool parser consumes those tokens; with
no tools in the request there is nothing to attach them to; the parsed call is
discarded and `content` returns `null`. The harness records an empty turn and
`read_answer` correctly reports `NO_ANSWER`.

Three consequences, all load-bearing for how §1 is read:

1. **`NO_ANSWER` here is not abstention.** The framing offered `ANSWER: none`
   explicitly and **zero** runs took it. Nine runs tried to keep working with a
   tool and lost the reply. Arm B's and arm C's `NO_ANSWER` cells must not be
   read as refusals, as caution, or as the muse declining to be confidently
   wrong.
2. **Turning tools off does not turn tool-calling off** for a model whose recent
   history is tool-shaped. `d7`'s mitigation assumed it would. On a rig whose
   tool parser runs regardless of whether the request carried tools, the failure
   mode is *quieter* than the one it replaced: `finish_reason=tool_calls` with an
   empty body is visibly odd, while `finish_reason=stop` with an empty body looks
   like a model that had nothing to say.
3. **The observability gap is real and worth closing.** `RecordingSeam` stores
   parsed fields and not the raw HTTP body, so a turn where the server generated
   118 tokens and returned none of them is indistinguishable in the transcript
   from a turn where the model generated nothing. It took an out-of-band replay
   to tell them apart. Recommended follow-up, not made here: record the raw
   response body per turn.

---

## 9. What this series cannot answer

Carried forward from the pre-registration's §11, plus what the run added:

- **One problem, one rig, one model, one prompt, one temperature.** Nothing here
  generalises to another problem family, another muse, or another temperature.
- **The added system text is confounded with the tools** (+1 828 chars for B,
  +2 507 for C) and this design cannot separate them. A tools-off arm carrying
  B's protocol text with no bench wired would, and is not in this series.
- **DV1 measured almost nothing, because arm A did not fail.** The single most
  useful thing a follow-up could change is the problem: this one is inside this
  muse's competence in the thinking loop.
- **Counsel soundness is not machine-graded.** §4's hand-reading is one reader's,
  and the scorer it corrects flatters in one direction (agreement in exempt
  vocabulary scores `COUNSEL`) and misses in the other (`failure` ≠ `fails`).
- **Arm C's boundary is asserted elsewhere.** That the workspace reaches no
  repository, store or network is `tests/test_workspace.py`'s claim, including a
  live no-reach probe; this series inherits it and re-proves none of it.
- **The pad-recall boundary stays shut.** Claim `c10` holds until the echo probe
  re-runs clean with a workspace-result arm (task `t19`). Nothing here makes a
  pad entry recallable and no result above may be read as clearing it.
- **The closing turn is part of the protocol, not of production.** In a real
  drive nothing asks the muse for a final integer — the acting loop holds final
  authority. DV1 measures what the muse *would commit to when asked*.
- **The wiring smoke run's numbers are still not results.** n=1, `measured:
  false`. Its three arms all answered `76`; the measured series' arms did not.
  That divergence is itself a reason not to read an n=1 wiring run as evidence.

## 10. Follow-ups this run earned

Recorded here rather than acted on — the harness, the oracle and the rules were
all frozen for this task.

1. **`ArmTools._ran` counts a job that never started as an execution.** It reads
   `MuseWorkspace`'s four refusal strings and ignores the job's exit status, so
   `C-2#2` (exit 127, `no executable named '"python3"'`) was classified
   `arithmetic-offloaded`. One of 23 calls, and it inflated the wrong bucket.
2. **The closing-turn mitigation needs re-validating against the real failure.**
   `d7`'s design was validated on an n=1 wiring run in which all three arms
   answered. At n=8 it fails in 9 of 16 tool-arm runs, for a reason the wiring
   run could not have shown. A closing turn that survives a tool-shaped history
   — or a transcript that records the raw body so the failure is visible without
   a replay — is the actual fix.
3. **`R-C1`'s `adjacency-constraint` signal turns on a trailing space.** The rule
   is frozen for this series by design and must not be retuned after seeing data;
   a *successor* rule should match `+ 1` / `- 1` without requiring the following
   character to be whitespace. Recorded with the audit that found it, so the next
   series starts from a known defect rather than rediscovering it.
4. **The decision rule's degradation gate is ambiguous** (§7). "More than a third
   of an arm's runs degraded" needs to name which degradation codes count,
   before a series turns on it.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

# dial nothing: the oracle pin and each arm's exact wire image
uv run python examples/muse_arms.py --dry-run

# the measured series exactly as run (one invocation, arms interleaved per repeat)
uv run python examples/muse_arms.py --n 8 --provider docker \
    --out docs/live-test-results/muse-arms.jsonl

# re-render every table above from the committed transcript
uv run python examples/muse_arms.py --analyse \
    --out docs/live-test-results/muse-arms.jsonl
```

- embodiment (Claude)
