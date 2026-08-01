#!/usr/bin/env python3
"""Have the cortex author a drone, and measure what that turn costs.

`c30`'s third success signal is *"a drone's second evocation makes 0 cortex
calls at <=5% of its authoring tokens"*. Nothing in the repo had ever paid an
authoring turn, so the denominator did not exist and the signal could only be
reported ABSENT (plan risk `r6`).

This pays it. The cortex writes the drone source; the tokens and wall clock it
spends are the authoring cost, recorded here rather than estimated from the
5,000-14,265 range quoted in #45.

The task is chosen to be a real one this repo actually needed twice today:
finding results documents missing from `docs/live-test-results/README.md`.
It recurs, its surface is stable, and it splits the way a drone should — file
walking and index parsing compile into code, while *"is this document
substantive enough to index?"* is a scoped question with a small answer space.

Streams, so a long think cannot hit a clock (`d3`), and so the reasoning is
visible while it works.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
MODEL = os.environ.get("EMBODIMENT_CORTEX_MODEL", "unsloth/Qwen3.6-27B-NVFP4")
API_KEY = os.environ.get("COLLEAGUE_API_KEY", "")

PROMPT = """\
Write a Python module for an `embodiment` drone named `index-gaps`.

A drone is deterministic code plus a few scoped calls to a smaller worker model
where judgement is genuinely needed. It must expose exactly this entry point:

    def run(request):
        # request.args   -> dict of string arguments
        # request.ask(question_id, payload) -> str, a scoped worker answer
        # return a dict:  {"summary": str, "detail": <json-serialisable>}
        # or, when the drone cannot decide:  {"cannot": "<why>"}

Task: given `args["repo"]` (a repository root), find every `*.md` file in
`docs/live-test-results/` that is NOT linked from that directory's `README.md`,
and report them.

Compile into code: walking the directory, parsing which filenames README.md
already links, and the set difference.

Ask the worker only this one scoped question, with id `substantive`:
given a document's filename and its first heading, answer exactly one of
`index` / `skip` / `unclear` — whether it is a substantive results document
worth indexing, or a generated sub-artifact that is not.

Rules:
- stdlib only, no third-party imports
- no network access, no subprocess
- deterministic apart from the scoped calls
- if `docs/live-test-results/README.md` does not exist, return the `cannot` form
- keep it under 120 lines

Output ONLY the Python module. No markdown fences, no commentary.
"""


def main() -> int:
    if not API_KEY:
        print("COLLEAGUE_API_KEY unset", file=sys.stderr)
        return 2

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 16000,
        "temperature": 0.3,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST",
    )

    started = time.monotonic()
    content: list[str] = []
    reasoning_chars = 0
    usage: dict | None = None
    finish_reason: str | None = None

    with urllib.request.urlopen(request, timeout=1300) as response:  # nosec B310
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                frame = json.loads(payload)
            except ValueError:
                continue
            if frame.get("usage"):
                usage = frame["usage"]
            for choice in frame.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                # `reasoning`, not `reasoning_content` — this deployment's name.
                if delta.get("reasoning"):
                    reasoning_chars += len(delta["reasoning"])
                if delta.get("content"):
                    content.append(delta["content"])

    elapsed = time.monotonic() - started
    source = "".join(content).strip()
    if source.startswith("```"):
        source = source.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    out_dir = os.environ["OUT_DIR"]
    with open(os.path.join(out_dir, "index_gaps_source.py"), "w", encoding="utf-8") as handle:
        handle.write(source + "\n")

    record = {
        "kind": "drone-authoring-cost",
        "model": MODEL,
        "elapsed_seconds": round(elapsed, 2),
        "finish_reason": finish_reason,
        "reasoning_chars": reasoning_chars,
        "usage": usage,
        "source_lines": len(source.splitlines()),
    }
    with open(os.path.join(out_dir, "authoring-cost.json"), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1)
    print(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
