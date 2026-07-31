# Muse arms — pre-registration (tools-off / +pad / +pad+workspace)

**Date:** 2026-07-30 · **Issue:**
[#21](https://github.com/agentculture/embodiment/issues/21) · **Plan:**
[the muse-cycle plan](../plans/2026-07-29-muse-cycle-pad-headspace-devague-legs.md),
tasks `t17` (this harness) and `t18` (the measured series) · **Harness:**
[`examples/muse_arms.py`](../../examples/muse_arms.py) · **Suite:**
`tests/test_muse_arms.py`

**This document is committed with the harness and before the first measured
dial.** The ordering is the point: everything below — the arms, the controls,
the metric, the classification rule, the decision rule and the reportable
negative — is fixed here so task `t18` executes a protocol rather than writing
one after seeing data.

Nothing in this document is a result. When the series runs, its numbers land in
`muse-arms.md` and its raw transcript in `muse-arms.jsonl`.

---

## 1. The question

Does giving the muse **working memory**, and then **somewhere to execute**,
reduce the rate at which it states a definite, confidently wrong final answer —
and does it cost counsel to buy that?

Three arms:

| arm | wired lanes | what the muse gets |
|---|---|---|
| **A** | *(none)* | today's tools-off seam — the baseline |
| **B** | `pad` | `embodiment.muse_pad.MusePad` — write-only working memory, no repo, no execution |
| **C** | `pad`, `workspace` | the pad **and** `embodiment.workspace.MuseWorkspace`, a bounded, network-less, disposable container |

## 2. Why this one can resolve where two predecessors returned `INCONCLUSIVE`

`association-work.md` and the arena's muse factor both returned `INCONCLUSIVE`
because every cell tied at a ceiling. Here there is headroom on the record:
**5 of the muse's 6 failures state a definite, wrong final answer**
(`association-work.md` §"Correction: the classifier said 0 reasoning failures",
restated in `muse-cycle-baseline.md`), and the dependent variable is a truth
function this repo owns and brute-force verified.

**That 5-of-6 is a motivating number, not this series' control, and must never
be reported as one.** It was measured on a *different configuration*: the muse
**model** driven through the **acting** loop (`examples/association_work.py`'s
executive axis) on the `register` and `entropic3b` problems, with a tool bench,
under a 14-turn budget. This series drives the **thinking** loop
(`MuseLoop.think`) on the subset problem. Arm A is this series' own baseline and
its floor is **measured, not assumed**. If arm A turns out to sit at a ceiling
too, that is a reportable `INCONCLUSIVE` and it gets reported.

## 3. The oracle — imported, never reimplemented

`truth()` and `grade()` are imported from `examples/challenge_subset.py`
**unchanged**, and `tests/test_muse_arms.py` asserts they are the *same function
objects* (identity, not equality), that the harness defines no shadowing
`truth`/`grade` of its own, that `truth() == 76`, and that `grade(72)` is
rejected as the planted trap.

- **Truth:** 76 of the 144 non-consecutive subsets of `{1..10}` have an even sum.
- **Planted trap:** 72 — the naive "half of 144".

Byte-stability is a **series-level** requirement: any change to either function
invalidates every arm already run. `oracle_pin()` writes the computed values
into the transcript preamble and `main()` **refuses to dial** when they no
longer match the pinned literals.

The problem text is *derived* from the oracle's own `PROBLEM` rather than
retyped: its final "Then call `finish` with your answer" sentence is dropped,
because **no arm has a `finish` tool** (`muse_pad.OMITTED_TOOLS`) and telling
the muse to call one would provoke off-protocol calls in arms B and C that arm A
could not make. A test pins that the derivation is a prefix of the original and
carries the whole question.

## 4. One runner, and the one confound that is real

`run_once()` is the whole runner. The arm reaches it only as a key into
`ARM_LANES`; a test asserts by AST that no function on the run path compares an
arm name or holds an arm literal.

Identical across all three arms, and asserted:

- **The boundary** — one `boundary()` taking no arm argument. The user message
  captured off all three live loops is byte-identical.
- **The controls** — one `controls()` taking no arm argument.
- **The transport** — one `RecordingSeam`: same endpoint, model, temperature and
  token budget. The tools-on body differs from the tools-off body by the
  presence of `tools` and by nothing else (asserted by dict equality).

**The confound, stated rather than hidden.** An arm cannot be handed a tool it
is not told about, so arms B and C carry more system text than arm A: the seam
appends `MUSE_TOOL_AUTHORITY` whenever a bench reaches the wire, and the harness
appends each wired lane's own protocol. Three things bound it:

1. **No prose in the harness is arm-conditional.** `TASK_FRAMING` is one
   constant, first in every arm, byte for byte. Every added block is a constant
   imported verbatim from the module that owns the tool
   (`MUSE_PAD_PROTOCOL`, `WORKSPACE_PROTOCOL`); a test asserts the composed
   framing equals exactly those constants concatenated.
2. **It is monotone and nested**: A's framing is a strict prefix-by-block of B's,
   and B's of C's. Measured: **+1 828 chars** for B over A, **+2 507** for C.
3. **It is recorded.** Every arm's exact system message is written into the
   transcript per run.

A reader who believes the added text alone explains a difference has the text in
front of them. The honest reading of any positive result is *"tools plus being
told about them"*, and t18 must phrase it that way.

## 5. The configuration, fixed here

| setting | value | note |
|---|---|---|
| muse model | `nvidia/Gemma-4-31B-IT-NVFP4` | recorded per run; roles resolve by name, never by parsing a model name |
| cortex | **none** | this experiment dials no cortex |
| temperature | `0.3` | one value for every arm — an A/B whose arms differ in temperature measures the temperature |
| `max_tokens` | `3000` | identical per arm |
| `max_turns` | `8` | |
| `max_quiet_turns` | `2` | **not** the default `1`, and for **every** arm alike — see below |
| `max_tool_rounds` | `4` | read only when a bench is wired; identical-but-inert on arm A |
| `max_context_chars` | `4000` | the problem must not be clipped |
| `max_insight_chars` | `4000` | |
| `max_tool_result_chars` | `2000` | |
| workspace provider | `docker` | `fake` is refused without `--smoke`; see §9 |
| closing turn | **1, tools-off, every arm** | see §5b |
| repeats | `n` per arm, decided in t18 | an arm under **n=6** reports `INCONCLUSIVE` |
| retries | **0** | a degraded call is data |

**Why `max_quiet_turns = 2`.** A tools-on turn can legitimately spend its whole
round allowance without emitting prose, and a quiet budget of 1 would end arms
B and C on their first pure-tool-call turn — an artefact of the budget rather
than a finding about the arm. Raising it applies to all three arms and costs
arm A at most one extra empty turn. Declared here, before the run, not adjusted
after one.

## 5b. The closing turn, and the wiring run that forced it

After each arm's thinking session ends, the harness appends one constant —
`CLOSING_PROMPT` — to **the muse's own message history** (the exact list
`MuseLoop` had assembled; nothing is reconstructed) and makes **one tools-off
call**. Identical constant, identical mechanism, every arm.

**It is here because a wiring run measured that it has to be**, and the
transcript is committed: [`muse-arms-wiring-smoke.jsonl`](muse-arms-wiring-smoke.jsonl),
one run per arm, `measured: false`, docker provider. Read off it:

| run | thinking turns | prose chars across them | closing turn |
|---|---|---|---|
| `A-0` | 2 | 2 085 | `ANSWER: 76` |
| `B-0` | 8 | **0** | the whole answer, in prose |
| `C-0` | 6 | 593 | `ANSWER: 76` |

Arm B's eight thinking turns came back `finish_reason=tool_calls` with an
**empty `content`** every single time: the muse handed a pad writes pad entries
and no prose at all. The thinking session therefore ends on the quiet exit with
nothing to grade. An earlier wiring run, made before the closing turn existed
and consequently not committed (the harness that produced it no longer exists),
confirmed the consequence directly — arms B and C both reported `NO_ANSWER`,
eight turns, six tool calls, zero content words. Without a closing turn DV1
would have measured the turn budget rather than the arms.

The failure is also pinned as a hermetic test in
`tests/test_muse_arms.py::TestClosingTurn`, which scripts eight pure-tool-call
turns and asserts the arm still yields a verdict — so the reason this turn
exists survives independently of any transcript.

Three properties keep it from becoming an arm difference:

- **One constant, no arm in it** — asserted by test.
- **Tools-off for every arm**, so no arm can spend its closing turn on another
  tool call, which is exactly what the tool arms did with every turn they had.
- **Not asked after a transport failure.** Re-dialling straight after a dead
  call is a retry in all but name; the run keeps its degradation instead.

That committed wiring run produced a gradeable answer in all three arms and one
real docker execution. **None of its numbers are results** — n=1, no repeats,
`measured: false`, and `--analyse` prints a banner saying so above every table —
and t18 must not cite them as such. They are recorded as harness-validation
evidence for a design decision, which is the only thing they can honestly
support.

Two observations from it that t18 should expect rather than be surprised by,
both n=1:

- **The workspace's argv contract defeats the muse at first.** It passed
  `command` as a *string* (and once as a JSON-encoded list *inside* a string).
  `MuseWorkspace` refuses a string rather than inventing a quoting rule no shell
  would honour, feeds the refusal back as text, and the muse corrected itself on
  a later round. Refused calls are counted in their own column and are
  classified `degraded`, never `arithmetic-offloaded`.
- **The muse does not stop calling tools voluntarily.** Both tool arms spent
  their whole round allowance and recorded `muse-tool-rounds-exhausted`. That is
  real behaviour, it lands in the health table, and it is not treated as a
  harness fault.

## 6. The dependent variables

### DV1 — the rate of confidently-wrong final answers (the headline)

Every arm is asked for its answer the same way: a final line
`ANSWER: <integer>`, with `ANSWER: none` offered **explicitly** as a valid
refusal. Without an abstention path, "confidently wrong" would measure a
protocol with no alternative rather than a choice the mind made.

`read_answer()` is the only verdict path and reads the **last** `ANSWER:` line
in the concatenated raw turn contents:

| verdict | meaning |
|---|---|
| `CORRECT` | a definite integer equal to 76 |
| `WRONG_TRAP` | a definite integer equal to 72 (the planted trap) |
| `WRONG` | any other definite integer |
| `ABSTAINED` | an explicit refusal from the committed `ABSTENTIONS` list |
| `MALFORMED` | an `ANSWER:` line parsing to neither |
| `NO_ANSWER` | no `ANSWER:` line at all |

**DV1 = `verdict ∈ {WRONG_TRAP, WRONG}`.**

A **lenient** parse (the last integer anywhere in the text) is recorded and is
**never** the verdict — `association-work.md` learned that the hard way: the
muse's exhaustive enumerations contain the correct answer *and* its wrong
conclusion, so a substring check for the truth inside an enumeration is
worthless. Hedging vocabulary in the final turn is likewise recorded as a
footnote and never changes a verdict.

### DV2 — pad protocol adherence (arms B and C)

Straight off `MusePad.counts()`: one count per kind with **zeros included**,
`open_intents`, `rejected_calls`, `off_protocol_calls`, and the kind `sequence`.
The pad's own on-disk JSONL is committed into the transcript verbatim.

The predicted failure, from issue #21's n=1 probe: **five intents, zero
observations, zero conclusions, five open intents at exit.** Arm B is a real
prediction because of it.

### DV3 — arm C: what the workspace was used for

Every `workspace_run` call is classified by **rule R-C1** (§7) and scored for
counsel independently of correctness (§8). Also recorded per run:

- `answer_timeline` — the definite answer, if any, stated at each turn;
- `belief_changed_after_execution` — whether a definite answer differed either
  side of the first execution. **`null`, never `false`**, when the run made no
  execution or stated no definite answer on both sides: the question was never
  posed, and a fabricated `false` would read as evidence that execution changed
  nothing.

## 7. Rule R-C1 — the arm C classification, fixed in advance

Applied per execution, in order. **Task t18 applies this rule; it does not get
to invent one after seeing the data.**

1. **`degraded`** — the call never ran (a refused argv, no engine, an engine
   failure, an unreadable result). Counted, **never classified**: a call that did
   not happen is not evidence about what execution was used for.
2. **`reasoning-displaced`** — the command is a **whole-problem solver**: its
   argv text carries **all four** surface signals —
   - `full-domain` — a domain bound reaching the problem's own `{1..10}`,
   - `enumerates-subsets` — an enumeration over subsets rather than a single
     evaluation,
   - `parity-constraint` — the even-sum constraint,
   - `adjacency-constraint` — the no-two-consecutive constraint.

   A program that, run, yields the final answer. The machine was asked for the
   conclusion rather than for a step toward it.
3. **`arithmetic-offloaded`** — anything else that ran: a partial sum, a parity
   check, one candidate subset, a small-`n` instance, a recurrence value. The
   muse kept the reasoning and handed over a fact it would otherwise have had to
   assert.

The rule reads the **command**, not the output, because what is being measured
is what the muse *reached for*. An output-based rule would grade the workspace
instead of the mind using it — a test pins that the same command classifies
identically whatever came back.

**Direction of this rule's error, stated before the data exists.** It is
conservative *toward* `arithmetic-offloaded`: a whole-problem solver written in
an idiom none of the four token sets catch is classified
`arithmetic-offloaded`. So the `reasoning-displaced` count is a **floor**, and
the rule errs in the direction that **flatters arm C** — it under-counts the
displacement the counter-hypothesis predicts.

**The hole is closed by procedure, not by fudging the rule.**
`result_contains_truth` — a standalone `76` in the captured output — is recorded
on every execution and is **never** part of the classification. Any execution
classified `arithmetic-offloaded` whose result contains the truth is flagged
`audit_required`, `analyse()` prints the flagged list, and **t18 must hand-audit
every flagged execution and report the audit, agreeing with the rule or not.**

## 8. Counsel quality — scored blind to the answer

The operator's framing is that headspace lets the muse stop pretending to be a
calculator. The counter-prediction, which must be falsifiable, is that it
substitutes execution for reasoning and returns results where it should be
reframing. **An arm that gets more sums right while offering less counsel has
not succeeded at the thing being measured.**

So counsel is scored **with the answer removed from the text first**
(`strip_answer_lines`), which makes independence a mechanism rather than a
promise: the scorer cannot see the answer, so it cannot be right or wrong about
it. A test asserts the same narration scores identically ending on 76 and on 72.

| counsel verdict | gate |
|---|---|
| `SILENT` | nothing but the answer |
| `RESTATED` | a near-copy of the problem (shared content bigrams > `0.35`), **or** no counsel move at all |
| `COUNSEL` | at least one un-negated challenge move: unstated-assumption, failure-condition or alternative-framing |

The move vocabulary, the tokenisers and the negation filter are
`examples/muse_challenge.py`'s, reused unchanged — a grader already
adversarially reviewed once, rather than a second untested copy. Only three
verdicts are kept: `muse_challenge`'s `UNTARGETED`/`UNARGUED` need a per-case
anchor key naming what the counsel should have landed on, and this experiment's
task is solving a problem rather than challenging a stated conclusion, so no such
key exists and inventing one would be inventing a grader.

**Adversarial fixtures and a vacuity assertion ship with it** (issue #21's own
requirement): a bare answer line must score `SILENT`, a paraphrase of the problem
must score `RESTATED`, a genuine reframing must score `COUNSEL`, and the four
committed fixtures must not all score the same.

**Its known limit, recorded now rather than rediscovered later.** The negation
filter covers only the phrases `muse_challenge` marks polarity-sensitive; its
exemptions are load-bearing there and are inherited unchanged. Agreement written
in exempt vocabulary — *"there is no unstated assumption; I would not change a
thing"* — still scores `COUNSEL`. The error **flatters** the response, in every
arm equally, and is pinned as a passing test (`FIXTURE_EXEMPT_NEGATION`).

`counsel_soundness` is reported as `"not machine-graded — read it"`. A regex
cannot judge whether counsel is any good, and saying so beats implying otherwise.

## 9. Degradation, and the two refusals

**A degraded call is data.** Nothing is retried. A transport failure is recorded
on the turn *and* re-raised, so `MuseLoop.think` records its own
`muse-thinking-failed` degradation and the session stops cleanly; a harness that
swallowed the error and returned empty content would be silently re-dialling for
a better number. A test asserts exactly one HTTP body leaves the process for a
failing call. Loop degradations, workspace degradations, transport errors and
`finish_reason=length` truncations all reach the transcript and get their own
results table.

Two things the harness refuses outright:

- **A moved oracle.** `main()` exits `2` when `truth()`/`grade()` no longer match
  the pin.
- **A fabricated execution.** headspace's `fake` provider reports success
  *without executing anything* — right for a wiring smoke run, poison in a
  measured one, since arm C's whole dependent variable is what a real execution
  returned. `--provider fake` therefore exits `1` unless `--smoke` is passed,
  and `--smoke` stamps `measured: false` into the config, which `analyse()`
  prints as a banner above every table.

## 10. The decision rule, fixed here

Read **per arm**, against arm A, on DV1:

- **`SUPPORTED`** — an arm's confidently-wrong rate is **lower** than arm A's by
  at least **2 runs** at n ≥ 6, **and** its counsel-verdict distribution is not
  worse than arm A's (no drop in `COUNSEL` count).
- **`SUPPORTED-AT-A-COST`** — the confidently-wrong rate falls by the same
  margin **but** the `COUNSEL` count drops. This is the counter-prediction
  landing, and it is a *reportable outcome*, not a failure to report.
- **`NOT-SUPPORTED`** — no such reduction.
- **`INCONCLUSIVE`** — arm A's confidently-wrong rate is **0** or **n** (a floor
  or a ceiling: nothing to move), **or** any arm ran **n < 6**, **or** more than
  a third of an arm's runs degraded.

**DV3 has its own vacuity gate.** Arm C's execution classification reports
`INCONCLUSIVE` when fewer than **6** executions across the arm reached the
`arithmetic-offloaded` / `reasoning-displaced` split — that is, when `degraded`
plus refused-argv calls swallowed the arm. In that case the finding to report is
the **argv contract failure**, named as such, not a classification distribution
computed from two or three surviving calls.

The series is published **either way**, `INCONCLUSIVE` included.

## 11. What this design cannot answer

Stated now so it is not quietly claimed later:

- **One problem.** Everything below is about the subset problem. Nothing here
  generalises to another problem family.
- **One rig, one model, one prompt.** No temperature sweep, no second muse
  model, no prompt variation. Every number is conditional on all of them.
- **The added system text is confounded with the tools** (§4) and cannot be
  separated by this design. A tools-off arm carrying B's protocol text with no
  bench wired would separate them and is **not** in this series.
- **Counsel soundness is not machine-graded.** The scorer counts moves; whether
  the counsel was *right* needs a reader.
- **Arm C's boundary is asserted elsewhere.** That the workspace reaches no
  repository, store or network is `tests/test_workspace.py`'s claim, including
  a live no-reach probe under `EMBODIMENT_LIVE_RIG`; this harness inherits it
  and re-proves none of it.
- **The pad-recall boundary stays shut.** Claim `c10` holds until the echo probe
  re-runs clean with a workspace-result arm (task `t19`). Nothing in this series
  makes a pad entry recallable, and no result here may be read as clearing that.
- **The closing turn is part of the protocol, not of production.** In a real
  drive nothing asks the muse for a final integer — the acting loop holds final
  authority and writes the one summary. DV1 therefore measures *what the muse
  would commit to when asked*, which is the comparable quantity, and not a thing
  the muse does unprompted in a live drive.

## 12. Reproducing

```bash
# dial nothing: print the oracle pin and each arm's exact wire image
uv run python examples/muse_arms.py --dry-run

# a wiring smoke run (fabricated executions — stamps measured:false)
COLLEAGUE_API_KEY=… uv run python examples/muse_arms.py \
    --n 1 --smoke --provider fake --out /tmp/muse-arms-smoke.jsonl

# the measured series (task t18's to run)
COLLEAGUE_API_KEY=… uv run python examples/muse_arms.py \
    --n 6 --out docs/live-test-results/muse-arms.jsonl

# re-render every table from the committed transcript
uv run python examples/muse_arms.py --analyse \
    --out docs/live-test-results/muse-arms.jsonl
```

Every table in the results document is produced by `--analyse` and pasted. A
number in the prose is a number the transcript contains — three of this repo's
own corrections were transcription errors made between a run and its write-up.

- embodiment (Claude)
