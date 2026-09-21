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

label=${1:?label}; base=${2:?base-ref}; head=${3:?head-ref}; brief_file=${4:-}
timeout_s=${DUAL_REVIEW_TIMEOUT:-1200}
max_patch_lines=${DUAL_REVIEW_MAX_PATCH_LINES:-4000}

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

run_qwen() { ( cd "$wt" && timeout "$timeout_s" qwen --approval-mode plan "$prompt" </dev/null ) >"$out_dir/qwen.md" 2>"$out_dir/qwen.err"; echo $? >"$out_dir/qwen.rc"; }
run_pi()   { ( cd "$wt" && timeout "$timeout_s" pi -p --no-session --tools read,grep,find,ls "$prompt" </dev/null ) >"$out_dir/pi.md" 2>"$out_dir/pi.err"; echo $? >"$out_dir/pi.rc"; }

start=$(date +%s)
run_qwen & run_pi & wait
elapsed=$(( $(date +%s) - start ))

status=0
for r in qwen pi; do
  rc=$(cat "$out_dir/$r.rc")
  verdict=$(grep -m1 -i '^VERDICT:' "$out_dir/$r.md" || true)
  counts=$(grep -o -E '^\- \[(BLOCKER|MAJOR|MINOR)\]' "$out_dir/$r.md" | sort | uniq -c | tr '\n' ' ' || true)
  if [[ "$rc" != 0 || -z "$verdict" ]]; then
    status=3
    echo "$r: NO USABLE REVIEW (rc=$rc) - see $out_dir/$r.err"
  else
    echo "$r: $verdict   ${counts:-no findings}"
  fi
done
echo "reviews: $out_dir  (${elapsed}s)"
exit "$status"
