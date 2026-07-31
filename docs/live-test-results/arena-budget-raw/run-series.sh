#!/usr/bin/env bash
# Arena series C (task t24) — the exact runner that produced this directory.
#
# Committed as a raw artifact, not as an example: it drives
# examples/league_seat.py through its documented CLI and adds nothing.
#
# Serial on purpose — the cortex is local and single, and task t28 shares the
# rig. Resumable on purpose — every match writes its report, trace and stderr
# the moment it finishes, and a match whose report is already on disk is
# SKIPPED, never replayed (pre-registration rule 2).
#
#   OUT=<dir> ./run-series.sh
#
# Requires COLLEAGUE_API_KEY in the environment.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="${OUT:-/tmp/arena-budget}"
SCRATCH="${SCRATCH:-$OUT/scratch}"
SEEDS=(4242 4243 4244)
SERIES="$OUT/series.jsonl"

mkdir -p "$OUT" "$SCRATCH"

# cell:arm:max_tokens
CELLS=("C2:command:2048" "C16:command:16000" "R2:resident:2048" "R16:resident:16000")

for spec in "${CELLS[@]}"; do
  IFS=: read -r cell arm budget <<<"$spec"
  for rep in 0 1 2; do
    seed="${SEEDS[$rep]}"
    id="${cell}-r${rep}"
    report="$OUT/report-$id.json"
    if [ -s "$report" ]; then
      echo "skip $id (already recorded)" >&2
      continue
    fi
    echo "[$(date -Is)] $id arm=$arm max_tokens=$budget seed=$seed" >&2
    rm -rf "${SCRATCH:?}/$id"
    mkdir -p "$SCRATCH/$id"
    start=$(date +%s)
    EMBODIMENT_LIVE_ARENA=1 timeout 3600 \
      "$REPO/.venv/bin/python" "$REPO/examples/league_seat.py" play \
      --arm "$arm" \
      --live \
      --max-tokens "$budget" \
      --max-turns 3 \
      --seed "$seed" \
      --match-id "$id" \
      --store "$SCRATCH/$id/memory" \
      --workdir "$SCRATCH/$id/arena" \
      --trace-out "$OUT/trace-$id.jsonl" \
      --json \
      >"$OUT/report-$id.json.part" 2>"$OUT/stderr-$id.txt"
    rc=$?
    elapsed=$(( $(date +%s) - start ))
    if [ "$rc" -eq 0 ] && [ -s "$OUT/report-$id.json.part" ]; then
      mv "$OUT/report-$id.json.part" "$report"
      cp "$SCRATCH/$id/arena/match-log.jsonl" "$OUT/matchlog-$id.jsonl" 2>/dev/null
      cp "$SCRATCH/$id/arena/match-log.config.json" "$OUT/config-$id.json" 2>/dev/null
    else
      # A failed match is data. It keeps its .part, its stderr and its row.
      echo "FAILED $id rc=$rc" >&2
    fi
    printf '{"cell":"%s","rep":%d,"arm":"%s","max_tokens":%d,"seed":%d,"returncode":%d,"seconds":%d}\n' \
      "$cell" "$rep" "$arm" "$budget" "$seed" "$rc" "$elapsed" >>"$SERIES"
    echo "    rc=$rc ${elapsed}s" >&2
  done
done
