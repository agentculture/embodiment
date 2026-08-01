#!/usr/bin/env bash
# run-probe.sh — the whole t8 live lane, guarded on both ends.
#
# Plan task t8 of `error-derived-timeouts-bee-hive-architecture`. Acceptance
# criterion 3 says the probe "runs only post-series or in a declared idle
# window, never contending with a live cell". That is not a formality, so the
# window is not a thing a human remembers to check — it is this script's first
# and last act, and both verdicts are appended to `window-log.jsonl`, which the
# harness embeds in its own summary.
#
#   1. guard (label `before`)  — refuses to dial unless Thor is idle
#   2. the probe               — writes jsonl + summary + generated tables
#   3. guard (label `after`)   — records whether the window held for the run
#
# Step 3 runs even if step 2 fails, because "did a Thor-dialling cell start
# mid-probe" is exactly the question a failed probe most needs answered.
#
# Usage:
#     export COLLEAGUE_API_KEY=...
#     docs/live-test-results/worker-scoped-overhead-raw/run-probe.sh
#
# Optional: pass extra flags straight through to the harness, e.g.
#     ... run-probe.sh --cells lean-off-w1,lean-off-w8

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$(cd "${HERE}/.." && pwd)"
REPO="$(cd "${RESULTS}/../.." && pwd)"

GUARD="${HERE}/thor-idle-guard.py"
WINDOW_LOG="${HERE}/window-log.jsonl"

WORKER_URL="${EMBODIMENT_WORKER_URL:-http://thor.tail0be7e0.ts.net:8000/v1}"
WORKER_MODEL="${EMBODIMENT_WORKER_MODEL:-unsloth/Qwen3.6-35B-A3B-NVFP4}"

echo "== guard: before ==" >&2
if ! python3 "${GUARD}" --label before --append "${WINDOW_LOG}" >/dev/null; then
    echo "REFUSING TO DIAL: Thor is not idle. See ${WINDOW_LOG}." >&2
    echo "Wait for a window with:  python3 ${GUARD} --wait" >&2
    exit 1
fi

echo "== probe ==" >&2
set +e
(
    cd "${REPO}" && uv run python examples/worker_scoped_overhead.py \
        --worker-url "${WORKER_URL}" \
        --worker-model "${WORKER_MODEL}" \
        --window-log "${WINDOW_LOG}" \
        --out "${RESULTS}/worker-scoped-overhead.jsonl" \
        --summary-out "${RESULTS}/worker-scoped-overhead-summary.json" \
        --markdown-out "${RESULTS}/worker-scoped-overhead-tables.md" \
        "$@" >"${HERE}/probe-stdout.json" 2>"${HERE}/probe-stderr.log"
)
PROBE_STATUS=$?
set -e

echo "== guard: after ==" >&2
python3 "${GUARD}" --label after --append "${WINDOW_LOG}" >/dev/null || true

if [ "${PROBE_STATUS}" -ne 0 ]; then
    echo "probe exited ${PROBE_STATUS}; see ${HERE}/probe-stderr.log" >&2
    exit "${PROBE_STATUS}"
fi

echo "probe complete. tables: ${RESULTS}/worker-scoped-overhead-tables.md" >&2
