# SonarCloud dispositions

Issues this repo has deliberately **declined to fix**, and the exact commands that record
that decision in SonarCloud rather than in a PR comment nobody will find again.

A refusal argued in a review thread leaves the issue `OPEN` forever: the next person sees a
count and no reasoning. The disposition belongs on the issue.

Project key `agentculture_embodiment`, organization `agentculture`. Every command below needs
`SONAR_TOKEN` exported with a token that has *Administer Issues* on the project.

## Why this file outlives the PR

**A transition applied to a pull-request issue does not carry over to `main`.** SonarCloud
tracks PR issues separately from branch issues, so once the PR merges, the next `main`
analysis raises the same issue fresh, unresolved. The commands below are therefore written
to be re-runnable against `main` — drop the `pullRequest=` filter when looking up the new
issue key, because the key changes.

## Current dispositions

| Rule | Location | Disposition | Recorded |
|------|----------|-------------|----------|
| `python:S107` | `embodiment/muse_runner.py` — `ThreadedMuseRunner.__init__` | Accepted (`WONTFIX`) | PR #38 |
| `python:S107` | `embodiment/loop.py` — `run` | Accepted | 2026-08-04 sweep |
| `python:S107` | `embodiment/continuity.py` — `recall` | Accepted | 2026-08-04 sweep |
| `python:S1172` | `embodiment/presence_engine.py` — `_PullSeam.drain` | Accepted | 2026-08-04 sweep |
| `python:S8495` | `embodiment/scope.py` — `_as_tuple` | Accepted | PR #76 |
| `python:S3776` | `embodiment/scope.py` — `_first_object` | Accepted | PR #76 |

> `muse_runner.py` was **archived** on 2026-08-03 (embodiment#53) but not
> deleted — it stays readable as the source `embodiment/strategist_runner.py`
> was cited from. The file is still analysed, so the disposition below still
> applies and is left standing. `StrategistRunner.__init__`, written afterwards,
> deliberately does **not** inherit the debt: it groups its knobs into
> `StrategistLimits` instead.

### `python:S107` — `ThreadedMuseRunner.__init__`, 15 parameters against a limit of 13

Declined, not a false positive: the count is real. `ThreadedMuseRunner.__init__` is this
module's injection seam. Fourteen of its fifteen parameters are keyword-only with defaults,
every one is read in the constructor (AST-verified — none is dead), and the class is
published public API (`embodiment.__all__`, ~53 call sites including `examples/proof.py`),
so collapsing the list into a config object is a breaking change that belongs in its own PR
with a deprecation cycle.

The recorded future path, if it is ever worth doing: group the five tuning scalars
(`max_pending`, `max_lag`, `max_failed_sessions`, `join_timeout`, `poll_interval`) behind a
default-constructed `MuseRunnerLimits`, taking the signature from 15 parameters to 11. None
of those five has a single call site in this repo, so the change is source-compatible here
and breaking only for external consumers.

That future path was **taken on day one** by the module cited out of this one:
`strategist_runner.StrategistLimits` groups the equivalent six scalars, so
`StrategistRunner.__init__` lands at eleven parameters. A brand-new module has no external
consumers, which is exactly the cost that blocks the same move here.

### `python:S107` — `loop.run`, 17 parameters against a limit of 13

Declined. The count is real and the finding is fair; the fix is not available at the price of
a lint sweep.

`run` is the package's headline public entry point — `from embodiment import run`, named in
`embodiment/__init__.py`'s own module docstring and resolved through `_LAZY_NAMES`. Inside
this repo alone it is imported by nineteen modules, four of them package modules
(`scoped_run.py`, `lifecycle.py`, `events.py`, `scratchpad.py`) and three of them shipped
examples, with roughly ninety call sites. Every parameter past the first two is keyword-only
with a default, and every one is read: there is no dead argument to delete, only a grouping
to invent.

The recorded future path: five of the seventeen are optional *seams* rather than tuning
scalars — `observer`, `presence`, `operator_inbox`, `continuity`, `subagent`. Grouping them
behind a default-constructed `LoopSeams` alongside the `LoopControls` object that already
exists would take the signature from 17 to 13, landing exactly on the limit. That is a
breaking change to the most-cited seam this package publishes, and it belongs in its own PR
with a deprecation cycle — not in a test-quality sweep whose standing rule is that production
behaviour does not move.

### `python:S107` — `continuity.recall`, 14 parameters against a limit of 13

Declined, and this one is more than an API-churn argument: **the parameter list is the
contract.**

`continuity.recall` mirrors `eidetic recall`'s flags one-for-one, and the module's docstring
commits to that in writing — *"mode, alpha and case_sensitive are passed through untouched
and this module reads no score"*. The mirror is what makes the seam auditable against
eidetic's own CLI; folding four of the flags behind a `RankingOptions` object would take the
count to 11 and simultaneously destroy the property the module is documented to have.

The signature is also a published seam in its own right. `recall_bundle.RecallFn` is defined
as *"the store seam: `recall(query, *, data_dir, scope, visibility, top_k, mode, reinforce,
include_shadowed, include_archived)`"* — a literal transcription of this parameter list — and
every no-store test fake in the suite implements it. Changing the shape changes what a host's
own store adapter has to look like.

One over the limit, against a documented mirror of a sibling CLI's flags, is the case the
rule is not built for.

### `python:S1172` — `_PullSeam.drain`'s unused `step_count`

Declined. The analyzer is factually right — the body never reads `step_count` — and it is
still not removable, so this is `accept`, not `falsepositive`.

`_PullSeam` adapts a `MusePullSeam` (t7's synchronous one-comment-per-boundary shape) onto
the `MuseSeam` drain shape so the engine keeps exactly one code path. `MuseSeam.drain` is
declared `drain(self, *, step_count: int = 0)` and `presence_engine._collect` calls it as
`muse.drain(step_count=step_count)` — **by keyword, unconditionally**. Deleting the parameter
breaks that call; renaming it to `_step_count` breaks it identically, because the caller names
it. The parameter is structurally mandated by a protocol this class exists to satisfy.

It is also correct for the body to ignore it: a pull seam has no cadence of its own. The pull
already happened inside `consider`, at the same boundary, so the step count a drain-shaped
seam would use to decide *whether it is ready* tells `_PullSeam` nothing it does not already
know. A `del step_count` line added only to quiet the analyzer would be noise that reads as
significant.

The one real alternative, recorded rather than taken: make `_PullSeam` inherit `MuseSeam`
explicitly (it is a `Protocol`, so explicit subclassing is legal), which is the shape
SonarPython's inherited-method exemption recognises. That changes an MRO and the answer to
`isinstance`/`issubclass` on a `@runtime_checkable` protocol — a production change, for a
lint rule, in a sweep that is not allowed to make one. It wants its own change if it is ever
wanted at all.

### `python:S8495` — `_as_tuple` does not always return tuples of the same length

Declined. The count really does vary — `()`, `(value,)`, or `n` entries — and that is the
function's entire job, so there is nothing to fix.

`_as_tuple` is a **sequence coercion**, not a record constructor. Its return type is
annotated `tuple[str, ...]`: a homogeneous variable-length sequence, the shape the rule's
premise does not describe. S8495 exists because a caller that writes `a, b = f()` breaks when
`f` returns three things. No caller here unpacks — all nine call sites assign the result
straight into a `tuple[str, ...]` dataclass field (`ScopeSnapshot`, `ScopeDirective`,
`ScopeReport`), where an absent field *must* give `()` and a bare string *must* give one
element rather than one element per character.

The sharpest evidence that the rule is reading the container and not the intent: this
function is a byte-faithful port of `contract._coerce_omissions`, which is analysed on every
`main` run, has the identical branch structure, and **has never raised S8495** — because it
returns `list[str]`. Same semantics, same varying length, different container, and only one
of them is a finding. Changing `_as_tuple` to return a list to match would trade an immutable
snapshot field for a mutable one, which is the property `ScopeSnapshot` exists to have.

`accept`, not `falsepositive`: the analyzer's literal statement is true. We decline the
remedy, not the fact — the same boundary the `_PullSeam.drain` entry sits on.

### `python:S3776` — `_first_object`, Cognitive Complexity 16 against a limit of 15

Declined, one point over, and the sibling S3776 on the same file was **fixed** rather than
dispositioned — so this is a judgement about this function, not a blanket refusal of the rule.
`_forbidden_key` had a genuinely separable clause; this one does not.

`_first_object` is a lexer: it returns the first balanced `{...}` span in a model turn. Brace
depth cannot be counted correctly without simultaneously tracking string literals and
backslash escapes — a `}` inside `"…}…"` must not decrement — so the nesting the rule is
measuring is the nesting the problem has. The one extraction available is the three-line
in-string state machine, and it fails the test this repo applies to a split: **you cannot
trust `_scan_in_string(character, escaped) -> (bool, bool)` from its name.** A reader
verifying escape handling has to read both halves anyway, so the split relocates the
complexity without reducing what has to be held in the head. Compare `_forbidden_in_mapping`,
which was extracted precisely because its name *is* checkable without its body.

The other obvious simplification is worse, and was measured rather than assumed. Handing the
span to `json.JSONDecoder().raw_decode` collapses the function to a complexity of 2 — and
**merges two distinct degradations into one**. On `{scope_id: not-quoted}` the current scanner
returns the balanced span, `_payload_of` fails the parse, and the record reads *"the directive
payload did not parse: …"*; `raw_decode` returns nothing and the record instead reads *"a turn
announced a directive but carried no complete JSON object (a truncated completion looks
exactly like this)"* — the truncation diagnosis, applied to a turn that was not truncated.
Both are `DEGRADED_MALFORMED` and **no test asserts the reason text**, so the whole suite stays
green while the host loses the ability to tell a truncated strategist from a malformed one.
That is the distinction `d16` was raised to preserve and the observability C3 requires.

## PR #76 — the first analysis of the scope lane

The strategic-scope-governor cycle's ~15k lines had never been seen by SonarCloud (CI scans
only `main` and PRs, never a branch — issue #60), so the `t16` sweep above ran a local AST
auditor as a stand-in and **named its own blind spot**: *families that have never fired here —
cognitive complexity, duplicated blocks, dead stores — could still raise.* When the PR opened,
ten issues landed and two of them were exactly that family. The prediction was right; this
section is the follow-through.

| Rule | Count | Disposition |
|------|-------|-------------|
| `python:S5863` — assertion against itself | 1 (`BUG`) | fixed — it alone held `new_reliability_rating` at 3 |
| `python:S5778` — multiple throwing calls in a `raises` block | 4 | all 4 fixed |
| `python:S3776` — cognitive complexity | 2 | 1 fixed, 1 accepted (above) |
| `python:S5958` — assertion too broad | 1 | fixed |
| `python:S3415` — assertion arguments reversed | 1 | fixed |
| `python:S8495` — varying tuple length | 1 | accepted (above) |

**The `BUG` was a real defect in the test, not a lint nit**, and worth reading as the one
substantive finding of the batch. `test_solving_is_deterministic` asserted
`solve(e).to_dict() == solve(e).to_dict()`. `oracle._solver_for` caches `_Solver` instances in
a module-level dict keyed by the episode's content hash, so the second call reused a solver
whose memo tables were already full and re-derived nothing: the test measured a populated
cache read twice. A public `oracle.reset_solver_cache()` now makes the cache visible instead
of a private trap, and the test clears it between two genuinely independent solves.

That the rewrite *strengthens* rather than silences was checked, not assumed. Non-determinism
was injected into the memoised search (a per-`_Solver` offset) and both assertion shapes were
run against it: the old shape **passed** the mutant and the new shape **failed** it. The
`_Solver` docstring's claim *"Constructed per solve, never shared"* — false the moment the
cache existed — was corrected in the same change.

The `S3776` fix on `_forbidden_key` touched shipped package code that is under structural
test, so behaviour preservation was proved beyond the suite: the pre-refactor implementation
was transcribed and differentially fuzzed against the new one over 60,008 payloads (26,219 of
them reaching a forbidden key), including multi-forbidden-key payloads where a depth-first to
breadth-first slip would report a *different* key, and payloads past `_MAX_PAYLOAD_DEPTH`.
Zero mismatches. That case matters because `tests/test_scope.py` asserts the refusal reason
names the key found.

## The 2026-08-04 sweep — what was fixed rather than dispositioned

Plan task `t16` of the strategic-scope-governor cycle triaged every open issue. Fifty-one
were open; forty-eight were **fixed in code** and three are the dispositions above.

| Rule | Count | Disposition |
|------|-------|-------------|
| `python:S9073` — composite assertion | 44 open (+1 the server could not yet see) | all 45 split |
| `python:S9083` — empty decorator parentheses | 4 | all 4 fixed |
| `python:S107` — parameter count | 2 | both accepted (above) |
| `python:S1172` — unused parameter | 1 | accepted (above) |

**S9073 earned its keep in every single instance**, which is why none of the forty-five is
dispositioned. The rule exists so a failing assertion names which conjunct failed, and every
occurrence here was one of the two shapes where that matters most:

- a **guard plus a claim about what it guards** — `assert recalled and recalled[0].data["ids"]
  == ["mem-1"]`, `assert isinstance(returned, ast.Constant) and returned.value is None`. Split,
  an empty list and a wrong list stop producing the same failure line.
- **two independent facts about one object** — `assert report.ok and report.exit_code == 0`,
  `assert "--run" in out and "--online" in out`. Split, the failure says which fact is false.

None was the shape the rule does *not* describe — a single proposition that happens to be
written with `and`, such as `assert 0 <= x <= 1`. (Python parses that as one `Compare` node,
so SonarPython would not raise on it anyway.)

Every split preserved semantics exactly, checked one at a time rather than trusted from a
script: no conjunct in the set called anything with a side effect, so no short-circuit was
load-bearing. Four had to be done by hand because a mechanical splitter would have damaged
them — three carried an assertion message that belongs to only one conjunct
(`tests/test_arena_series.py`, `tests/test_muse_challenge.py`,
`tests/test_scopebench_oracle.py`) and one carried a trailing comment a splitter drops
(`tests/test_lifecycle.py`). The suite was 6469 passed / 28 skipped before and after.

Fixed issues need **no** SonarCloud transition: the next `main` analysis simply stops
raising them. Only a declined issue needs a recorded decision, which is what this file is.

## What SonarCloud could not see, and how that gap was covered

Three blind spots are worth writing down, because each one makes a raw issue count misleading.

**1. A branch push produces no analysis.** `.github/workflows/tests.yml` fires only on
`pull_request` → `main` and `push` → `main`, so code on a feature branch is invisible to the
server until the PR opens. The strategic-scope-governor cycle added roughly 15k lines that
SonarCloud had never analysed at the time of this sweep. The gap was covered by walking the
tree locally with the same rule premises — which found exactly the 44 S9073 and 4 S9083 the
server reported, plus **one S9073 the server could not see yet**
(`tests/test_scopebench_oracle.py`), now fixed. That agreement is the evidence the local
audit reproduces the server's rules; it is not proof the *new* code is clean, because a local
AST walk covers only the rule families this repo has actually seen fire.

**That caveat was load-bearing and it cashed out.** When PR #76 opened, the server raised ten
issues on the same code, and two of them — the `python:S3776` pair — were from precisely the
family the caveat named as unreachable. See *PR #76* above. The stand-in was worth running and
it was not a substitute; the standing fix is issue #60, so a branch gets analysed before a PR
has to discover this.

**2. `examples/` is not analysed at all.** `sonar-project.properties` sets
`sonar.sources=embodiment` and `sonar.tests=tests`. The local audit found three `python:S107`
functions under `examples/` — `arch_arms.mint` (16), `arch_league.run_round` (15),
`arch_league.play_match` (14) — that would become open issues the day `examples/` joins
`sonar.sources`. They are left alone deliberately: those harnesses produced published live
results, and re-shaping their signatures for a rule that does not currently apply to them
trades reproducibility for a clean count.

**3. `sonar.sh issues` does not filter out resolved issues.** The vendored script calls
`/api/issues/search` without `resolved=false`, so its `total` counts already-dispositioned
issues alongside open ones. The baseline for this sweep was recorded as "53 open"; 51 were
open and two were the existing dispositions (`ThreadedMuseRunner.__init__` `ACCEPTED`, and a
`python:S8786` on `muse.py` marked `FALSE_POSITIVE`). Read `issueStatus` per issue, not the
total. The script is vendored and cited verbatim, so the fix belongs upstream, not here.

## Re-applying a disposition after merge

Look up the current issue key on the branch, then comment and transition. Comment first, so
the reasoning is attached even if the transition is rejected.

```bash
# 1. Find the issue key on main (omit --data-urlencode 'pullRequest=38' when on a branch).
#    `resolved=false` matters: the search endpoint returns dispositioned issues too.
curl -s -u "$SONAR_TOKEN:" -G 'https://sonarcloud.io/api/issues/search' \
  --data-urlencode 'componentKeys=agentculture_embodiment' \
  --data-urlencode 'rules=python:S107' \
  --data-urlencode 'resolved=false' \
  | python3 -c 'import sys,json;[print(i["key"], i["component"], i.get("line")) for i in json.load(sys.stdin)["issues"]]'

# 2. Attach the reasoning.
KEY=<paste-the-key>
curl -s -u "$SONAR_TOKEN:" -X POST 'https://sonarcloud.io/api/issues/add_comment' \
  --data-urlencode "issue=$KEY" \
  --data-urlencode 'text=Accepted, not fixed: ThreadedMuseRunner.__init__ is this module'"'"'s injection seam - 14 of its 15 parameters are keyword-only with defaults, every one is read (AST-verified, none dead), and the class is published public API, so collapsing the list into a config object is a breaking change that belongs in its own PR with a deprecation cycle.'

# 3. Record the disposition. Use `accept` (SonarCloud's current name for Won't Fix);
#    use `falsepositive` only when the finding is factually wrong, which S107 is not.
curl -s -u "$SONAR_TOKEN:" -X POST 'https://sonarcloud.io/api/issues/do_transition' \
  --data-urlencode "issue=$KEY" \
  --data-urlencode 'transition=accept'
```

Available transitions are `accept`, `confirm`, `resolve`, `falsepositive`, `wontfix`. To undo
any of them, transition the issue to `reopen`.

Prefer the vendored skill's supported entry point when it will do the job — it applies the
transition and attaches the rationale in one call, which is the ordering this file exists to
enforce:

```bash
SONAR_PROJECT=agentculture_embodiment bash .claude/skills/sonarclaude/scripts/sonar.sh accept \
  --issue "$KEY" --comment 'Accepted, not fixed: <the rationale, in full>'
```

## Choosing between `accept` and `falsepositive`

- **`falsepositive`** — the analyzer is wrong about the code. Reserve it for that.
- **`accept`** — the analyzer is right and we are choosing to live with it.

Recording a true positive as a false positive is how a project loses the ability to trust
its own suppression history.

Note the boundary the `_PullSeam.drain` entry above sits on, because it is the one that gets
mislabelled: "the parameter cannot be removed" is not the same claim as "the parameter is
used". The analyzer said the second and was right. `accept` is the honest transition even
when the suggested remedy is impossible.

## Not listed here

`python:S7632` (four occurrences in `embodiment/workspace.py`) was **fixed in code**, not
declined, so it has no entry above. The trigger was never the repo's
`# noqa: CODE - reason` idiom, which SonarSource's parser deliberately supports: it was a
**comma inside the reason text**, which the analyzer splits on before it strips the trailing
comment, turning the reason's fragments into bogus rule IDs. The repo now writes the reason
as its own comment — `# noqa: CODE  # reason` — which is immune to whatever the reason says.

The forty-eight issues cleared by the 2026-08-04 sweep are likewise absent by design: a fixed
issue closes itself on the next analysis and leaves no decision to record.
