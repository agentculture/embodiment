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

> `muse_runner.py` was **archived** on 2026-08-03 (embodiment#53) but not
> deleted — it stays readable as the source `embodiment/strategist_runner.py`
> was cited from. The file is still analysed, so the disposition below still
> applies and is left standing. `StrategistRunner.__init__`, written afterwards,
> deliberately does **not** inherit the debt: it groups its knobs into
> `StrategistLimits` instead.

### `python:S107` — 15 parameters against a limit of 13

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

## Re-applying a disposition after merge

Look up the current issue key on the branch, then comment and transition. Comment first, so
the reasoning is attached even if the transition is rejected.

```bash
# 1. Find the issue key on main (omit --data-urlencode 'pullRequest=38' when on a branch).
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

## Choosing between `accept` and `falsepositive`

- **`falsepositive`** — the analyzer is wrong about the code. Reserve it for that.
- **`accept`** — the analyzer is right and we are choosing to live with it.

Recording a true positive as a false positive is how a project loses the ability to trust
its own suppression history.

## Not listed here

`python:S7632` (four occurrences in `embodiment/workspace.py`) was **fixed in code**, not
declined, so it has no entry above. The trigger was never the repo's
`# noqa: CODE - reason` idiom, which SonarSource's parser deliberately supports: it was a
**comma inside the reason text**, which the analyzer splits on before it strips the trailing
comment, turning the reason's fragments into bogus rule IDs. The repo now writes the reason
as its own comment — `# noqa: CODE  # reason` — which is immune to whatever the reason says.
