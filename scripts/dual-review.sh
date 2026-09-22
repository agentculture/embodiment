#!/usr/bin/env bash
# dual-review.sh - two independent, read-only reviews of one diff, in parallel.
#
#   scripts/dual-review.sh <label> <base-ref> <head-ref> [brief-file]
#
#   label       names the review (t4, wave-2, final-pr); also names the output dir
#   base/head   the diff under review is base...head
#   brief-file  optional: what the change was supposed to do (for a task, its
#               summary / instruction / acceptance criteria VERBATIM from
#               `devague plan waves --json`)
#
# Reviewers, chosen by the operator because they are different model families:
#   qwen  Qwen Code   -> lobes `worker` role     (--approval-mode plan: analyse only)
#   pi    pi          -> lobes `associate` role  (read/grep/find/ls tools only)
#
# Both run in ONE throwaway detached worktree of <head-ref>, so they can open any
# file but cannot touch the branch. Neither can run commands or edit. Their output
# is a second opinion to verify and own, never authority: this script never fails
# a merge by itself. It exits 0 when both reviewers answered, 3 when one did not.
#
# Output: <parent-of-repo>/.worktrees.<repo>/reviews/<label>/{qwen,pi}.md + brief + patch
set -euo pipefail

# The whole body lives in main() and is invoked on the LAST line, so bash parses the
# entire file before running any of it. A review runs for many minutes; without this,
# editing the script while one is live makes the running copy read shifted text and
# die mid-run (it happened on t9).
main() {

  label=${1:?label}; base=${2:?base-ref}; head=${3:?head-ref}; brief_file=${4:-}
  timeout_s=${DUAL_REVIEW_TIMEOUT:-1200}
  max_patch_lines=${DUAL_REVIEW_MAX_PATCH_LINES:-4000}

  for bin in $(for r in ${DUAL_REVIEW_REVIEWERS:-qwen27}; do echo "${r%27}"; done | sort -u); do
    command -v "$bin" >/dev/null || { echo "error: reviewer '$bin' is not on PATH" >&2; echo "hint: install it, or fix PATH, before reviewing" >&2; exit 2; }
  done

  repo_root=$(git rev-parse --show-toplevel)
  wt_root="$(dirname "$repo_root")/.worktrees.$(basename "$repo_root")"
  out_dir="$wt_root/reviews/$label"
  wt="$wt_root/review-$label"
  mkdir -p "$out_dir"

  cleanup() { git -C "$repo_root" worktree remove --force "$wt" >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  cleanup
  git -C "$repo_root" worktree add --detach -q "$wt" "$head"

  # The material both reviewers read. It lives in the throwaway tree, never the repo.
  git -C "$repo_root" diff --stat "$base...$head" >"$wt/REVIEW_STAT.txt"
  git -C "$repo_root" diff "$base...$head" -- . ':(exclude)uv.lock' ':(exclude)web/package-lock.json' >"$wt/REVIEW_FULL.patch"
  patch_lines=$(wc -l <"$wt/REVIEW_FULL.patch")
  if (( patch_lines > max_patch_lines )); then
    head -n "$max_patch_lines" "$wt/REVIEW_FULL.patch" >"$wt/REVIEW_DIFF.patch"
    patch_note="The patch is $patch_lines lines; REVIEW_DIFF.patch holds the first $max_patch_lines. REVIEW_STAT.txt lists every changed file: open the changed files directly for the rest."
  else
    cp "$wt/REVIEW_FULL.patch" "$wt/REVIEW_DIFF.patch"
    patch_note="REVIEW_DIFF.patch is the complete patch ($patch_lines lines)."
  fi
  rm -f "$wt/REVIEW_FULL.patch"

  {
    echo "# Review brief: $label"
    echo
    echo "Diff under review: \`$base...$head\`. $patch_note"
    echo
    if [[ -n "$brief_file" && -f "$brief_file" ]]; then
      echo "## What this change was supposed to do (verbatim from the confirmed plan)"
      echo
      cat "$brief_file"
    else
      echo "No task brief: review the change on its own terms and against CLAUDE.md."
    fi
  } >"$wt/REVIEW_BRIEF.md"
  cp "$wt/REVIEW_BRIEF.md" "$wt/REVIEW_STAT.txt" "$wt/REVIEW_DIFF.patch" "$out_dir/"

  # The 27B's time goes into re-reading whole files at 80-120K tokens of context (3-5 min
  # per call there). Measured 2026-09-22: unaided it did not finish a 113 kB diff in 40
  # min; with one worker gathering evidence it took 49 min because it re-read everything
  # the worker cited. So the split below makes the WORKER write the whole draft and the
  # 27B verify only the cited lines. DUAL_REVIEW_DELEGATE=0 restores the older prompt.
  if [[ "${DUAL_REVIEW_DELEGATE:-1}" == "1" ]]; then
  read -r -d '' prompt <<'PROMPT' || true
You are the REVIEW LEAD for a code change in this repository. You cannot run commands or edit files; read only. You have a `worker` subagent (the `agent` tool, subagent_type "worker") that is faster than you. Your job is to judge, not to read: keep your own reading to the lines a finding cites.

Step 1 (delegate, in ONE agent call, run_in_background false): send the worker this exact task: "Read REVIEW_BRIEF.md, REVIEW_STAT.txt and REVIEW_DIFF.patch in this directory, then the changed files, their tests and CLAUDE.md. Answer every MANDATORY integrator question in the brief with file:line evidence. Judge each acceptance criterion MET / NOT MET / CANNOT TELL with the test that proves it. Then hunt real defects, in this order: a failure swallowed without a recorded degradation; anything that can raise into a caller promised never-raise; secrets or transcript text reaching a log, event or served file; an unbounded wait, a blocking call on a hot path, a thread that cannot be stopped; a test that cannot fail or asserts on a mock; behaviour the brief did not ask for. For EVERY finding give file:line, the defect in one sentence, and a concrete input or sequence that triggers it. Every line number you cite MUST come from read_file on the working tree, never from the patch: the patch's line numbers are offsets inside hunks, and a draft whose cited ranges do not hold what they claim is rejected whole and you are sent to redo it. Do not search for or call tools other than read_file, grep_search, glob and list_directory. No style, naming or formatting. Write the complete draft review in exactly this shape: ## Criteria / ## Findings ([BLOCKER|MAJOR|MINOR] file:line - defect - trigger) / ## Not examined / VERDICT: approve | changes-requested." If the worker fails or returns nothing usable, send it once more with the same task; if it fails again, do the work yourself.

Step 2 (verify, yourself): for each finding in the draft open ONLY the cited file at the cited lines (a bounded read_file range, not the whole file) and confirm the trigger is real; If the cited lines do not hold what the finding claims, locate the symbol it names (grep_search for the function, class or test name, then one bounded read_file at the hit) before judging - a wrong line number is not evidence against a finding. Drop a finding only when you have READ the code it is about and the trigger is not real; say so under "## Not examined" as "dropped: <finding> - <why>". A finding you could not read within budget is listed there as "unverified: <finding>", never as dropped. Do the same spot-check for each criterion's cited test (the test exists and asserts behaviour, not a mock). Do not re-read files the draft did not cite. Do not exceed 16 tool calls in this step.

Step 3: output the final review in exactly this shape and nothing else (severity is yours to adjust; add a finding only if you saw it yourself in step 2):

## Criteria
- <criterion, shortened>: MET | NOT MET | CANNOT TELL - <evidence: file:line or test name>

## Findings
- [BLOCKER|MAJOR|MINOR] <file>:<line> - <the defect> - <a concrete input or sequence that triggers it>
(write "none" if there are none; do not invent findings to fill the section)

## Not examined
- <what the worker and you did not or could not check; each dropped finding>

VERDICT: approve | changes-requested
PROMPT
  else
  read -r -d '' prompt <<'PROMPT' || true
You are reviewing a code change in this repository. You cannot run commands or edit files; read only.

1. Read REVIEW_BRIEF.md, then REVIEW_STAT.txt, then REVIEW_DIFF.patch. Open any changed file, its tests, and CLAUDE.md as needed.
2. Judge the change against the acceptance criteria in the brief, if there are any. For each criterion say MET, NOT MET or CANNOT TELL, and point at the test that proves it.
3. Then look for real defects. Prioritise, in this order:
   - a failure that is swallowed without a recorded degradation (this repo forbids silent degradation)
   - anything that can raise into a caller that was promised never-raise
   - secrets or transcript text reaching a log, an event, or a served file
   - an unbounded wait, a blocking call on a hot path, a thread that cannot be stopped
   - a test that cannot fail, or that asserts on a mock instead of behaviour
   - behaviour the brief did not ask for
4. Do not report style, naming, or formatting. Do not praise. Do not restate the diff.

Answer in exactly this shape and nothing else:

## Criteria
- <criterion, shortened>: MET | NOT MET | CANNOT TELL - <evidence: file:line or test name>

## Findings
- [BLOCKER|MAJOR|MINOR] <file>:<line> - <the defect> - <a concrete input or sequence that triggers it>
(write "none" if there are none; do not invent findings to fill the section)

## Not examined
- <what you did not or could not check>

VERDICT: approve | changes-requested
PROMPT
  fi

  # qwen27: the same Qwen Code harness against the dense Qwen 3.8 27B (the `cortex`
  # model). THE DEFAULT AND SOLE REVIEWER since 2026-09-22, by the operator's word: on
  # the first diff both read, the 27B found every defect the worker found plus three
  # more, all reproduced. The worker is kept as an opt-in (DUAL_REVIEW_REVIEWERS="qwen").
  # Override the model id with DUAL_REVIEW_QWEN27_MODEL. One review at a time: the
  # models are single instances on this rig and concurrent reviews starve each other.
  # `--allowed-tools=agent` lets plan mode delegate to the operator's `worker` subagent
  # (~/.qwen/agents/worker.md, the 35B on thor) for evidence gathering. Plan mode still
  # denies it a shell and edits (smoke-tested 2026-09-22: the worker read a file, could
  # not run wc); without the flag the 27B's first `agent` call is refused non-interactively
  # and it reviews unaided, which on a 116 kB diff did not finish in 40 min. The prompt
  # goes through -p: `--allowed-tools` is an array flag and swallows a positional prompt
  # in both its bare and `=` forms (four queued reviews died in 1 s with "No input
  # provided via stdin" before this was corrected).
  call_qwen27() { ( cd "$wt" && timeout "$timeout_s" qwen -m "${DUAL_REVIEW_QWEN27_MODEL:-unsloth/Qwen3.8-27B-NVFP4}" --approval-mode plan --allowed-tools=agent -p "$prompt" </dev/null ); }
  run_qwen27() { run_reviewer qwen27; }
  call_qwen() { ( cd "$wt" && timeout "$timeout_s" qwen --approval-mode plan "$prompt" </dev/null ); }
  # The associate model can spend its ENTIRE output budget reasoning about a large
  # diff and be cut off before it writes an answer (57 kB of trace, no text, on t8).
  # pi's --thinking levels do not bind for this model, so it is steered in words.
  local pi_brevity="Reason briefly: a few short notes, never a restatement of the diff or the brief. As soon as you have read REVIEW_BRIEF.md, REVIEW_STAT.txt and REVIEW_DIFF.patch, start WRITING the answer, beginning with the '## Criteria' heading. An unfinished answer is worth more than finished reasoning."
  # pi runs in --mode json and its answer is extracted by pi-final-text.py: plain
  # `pi -p` prints nothing (rc 0) when the reasoning model leaves its final text part
  # empty, which cost three reviews before it was diagnosed.
  call_pi()   { ( cd "$wt" && timeout "$timeout_s" pi -p --no-session --mode json --thinking low --append-system-prompt "$pi_brevity" --tools read,grep,find,ls "$prompt" </dev/null ) | python3 "$repo_root/scripts/pi-final-text.py"; }

  # One retry when a reviewer exits 0 with nothing to say (seen from pi on t4). The
  # attempt count is recorded, so a flaky reviewer shows up in the summary line.
  run_reviewer() {
    local name=$1 attempt rc=0
    for attempt in 1 2; do
      rc=0
      "call_$name" >"$out_dir/$name.md" 2>"$out_dir/$name.err" || rc=$?
      echo "$attempt" >"$out_dir/$name.attempts"
      [[ $rc -eq 0 && ! -s "$out_dir/$name.md" ]] || break
    done
    echo "$rc" >"$out_dir/$name.rc"
  }
  run_qwen() { run_reviewer qwen; }
  run_pi()   { run_reviewer pi; }

  # DUAL_REVIEW_REVIEWERS="pi" re-runs one reviewer alone (e.g. after it came back
  # empty) without repeating the other's half; the default is both, in parallel.
  # Default is Qwen Code alone: the operator paused pi reviews on 2026-09-22 after the
  # associate model repeatedly spent its whole output budget reasoning about large
  # diffs and never wrote an answer. Pass DUAL_REVIEW_REVIEWERS="qwen pi" to bring it
  # back; the JSON extraction, retry and brevity steer all still apply to it.
  local reviewers=${DUAL_REVIEW_REVIEWERS:-"qwen27"}
  start=$(date +%s)
  for r in $reviewers; do "run_$r" & done
  wait
  elapsed=$(( $(date +%s) - start ))

  status=0
  for r in $reviewers; do
    rc=$(cat "$out_dir/$r.rc")
    # Reviewers drift on the last line ("## Verdict: approve", "**VERDICT**: ..."), so
    # match loosely; a review with criteria but no verdict line is still a review.
    verdict=$(grep -m1 -i -E '^[#*[:space:]]*verdict[*[:space:]]*:' "$out_dir/$r.md" | sed -E 's/^[#*[:space:]]*//' || true)
    if [[ -z "$verdict" ]] && grep -q -i '^## Criteria' "$out_dir/$r.md"; then verdict="VERDICT: (none stated)"; fi
    counts=$(grep -o -E '^\- \[(BLOCKER|MAJOR|MINOR)\]' "$out_dir/$r.md" | sort | uniq -c | tr '\n' ' ' || true)
    if [[ "$rc" != 0 || -z "$verdict" ]]; then
      status=3
      echo "$r: NO USABLE REVIEW (rc=$rc) - see $out_dir/$r.err"
    else
      tries=$(cat "$out_dir/$r.attempts" 2>/dev/null || echo 1)
      echo "$r: $verdict   ${counts:-no findings}$([[ $tries -gt 1 ]] && echo "   (answered on attempt $tries)")"
    fi
  done
  echo "reviews: $out_dir  (${elapsed}s)"
  return "$status"
}

main "$@"; exit
