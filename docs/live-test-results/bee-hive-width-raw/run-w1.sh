#!/usr/bin/env bash
# Dial the whole W1 rung, committing after every block.
#
# Run detached. `drive.py` writes records but does not commit; this wraps it so
# a run that dies halfway still leaves everything measured in git — the rule the
# pre-registration sets and the reason cells are dialled one at a time.
#
#   nohup bash run-w1.sh > /path/to/w1.log 2>&1 &
#
# Idempotent: `drive.py` skips a cell already recorded unless --force, so a
# re-run after a crash resumes rather than re-dialling.
set -uo pipefail
cd "$(dirname "$0")"
ROOT=$(git rev-parse --show-toplevel)
export EMBODIMENT_LIVE_RIG=1

BLOCKS="${BLOCKS:-1 2 3}"

for block in $BLOCKS; do
  echo "=== block $block starting $(date -Is) ==="
  uv run python drive.py --block "$block"
  status=$?
  cd "$ROOT"
  git add docs/live-test-results/bee-hive-width-raw/*.jsonl 2>/dev/null
  if ! git diff --cached --quiet; then
    git commit -q -m "t10 W1: block $block records, as dialled

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017BawRbNAxZ6YVfCUvVB3Cx"
    echo "=== block $block committed ==="
  else
    echo "=== block $block produced no new records ==="
  fi
  cd "$ROOT/docs/live-test-results/bee-hive-width-raw"
  if [ "$status" -ne 0 ]; then
    echo "=== block $block exited $status — stopping, records are committed ==="
    exit "$status"
  fi
done

echo "=== all blocks done $(date -Is) ==="
uv run python decide.py 2>&1 | tail -40 || true
