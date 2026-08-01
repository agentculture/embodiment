#!/usr/bin/env python3
"""Does the cortex stream, and does its REASONING stream?

The question that decides whether streaming retires the timeout class or only
moves it. With SSE the gateway's read timeout becomes a per-chunk socket bound
(verified in lobes/gateway/server.py: conn.sock.settimeout(read_timeout), then
a chunk-by-chunk relay loop). So the binding quantity stops being total
generation time and becomes **the largest gap between chunks**.

For a thinking model that gap has a specific shape: if the server buffers the
reasoning phase and only emits once content starts, then time-to-first-chunk IS
the whole think, and a long think trips an idle bound exactly the way a long
generation tripped a total bound. If reasoning streams as deltas, chunks flow
throughout and nothing can trip.

Measures, per request: time to first chunk, every inter-chunk gap, whether a
reasoning delta ever appears -- under EITHER `delta.reasoning` (what this rig
sends) or `delta.reasoning_content` (what vLLM documents) -- whether
`delta.content` appears, and whether a terminal usage chunk arrives when
`stream_options.include_usage` is asked for (the metering property the harness
records depend on). Every delta key seen is reported, so a rig that renames the
field shows up as a new key rather than as a silent zero.

Run only when the cortex is idle.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
MODEL = os.environ.get("EMBODIMENT_CORTEX_MODEL", "unsloth/Qwen3.6-27B-NVFP4")
API_KEY = os.environ.get("COLLEAGUE_API_KEY", "")

#: Small enough to be cheap, hard enough that a thinking model actually thinks.
PROMPT = (
    "A bag holds 3 red and 5 blue marbles. Two are drawn without replacement. "
    "What is the probability both are the same colour? Answer with the fraction."
)


def probe(*, thinking: bool, max_tokens: int = 2000, timeout: float = 900.0) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}

    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )

    started = time.monotonic()
    first_chunk_at: float | None = None
    last_at = started
    gaps: list[float] = []
    reasoning_deltas = 0
    content_deltas = 0
    other_frames = 0
    usage: dict | None = None
    finish_reason: str | None = None
    first_reasoning_at: float | None = None
    first_content_at: float | None = None
    delta_keys: set[str] = set()

    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            now = time.monotonic()
            if first_chunk_at is None:
                first_chunk_at = now - started
            else:
                gaps.append(now - last_at)
            last_at = now

            if payload == "[DONE]":
                break
            try:
                frame = json.loads(payload)
            except ValueError:
                other_frames += 1
                continue

            if frame.get("usage"):
                usage = frame["usage"]
            for choice in frame.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta_keys.update(delta)
                # `reasoning` is the field THIS RIG sends; `reasoning_content`
                # is the one vLLM documents. The first run of this probe read
                # only the documented name, reported 0 reasoning deltas across
                # 390 chunks arriving 0.11 s apart, and would have concluded the
                # cortex does not stream its thinking — the opposite of the
                # truth. See streaming-probe.md §3.
                if delta.get("reasoning") or delta.get("reasoning_content"):
                    reasoning_deltas += 1
                    if first_reasoning_at is None:
                        first_reasoning_at = now - started
                if delta.get("content"):
                    content_deltas += 1
                    if first_content_at is None:
                        first_content_at = now - started

    return {
        "thinking": thinking,
        "total_seconds": round(time.monotonic() - started, 2),
        "time_to_first_chunk": round(first_chunk_at, 3) if first_chunk_at else None,
        "max_inter_chunk_gap": round(max(gaps), 3) if gaps else None,
        "mean_inter_chunk_gap": round(sum(gaps) / len(gaps), 4) if gaps else None,
        "chunks": len(gaps) + (1 if first_chunk_at is not None else 0),
        "reasoning_deltas": reasoning_deltas,
        # Reported so a future rig change shows up as a NEW KEY rather than as
        # a silent zero. The absence of a count is not evidence of absence when
        # the count is keyed on a name.
        "delta_keys": sorted(delta_keys),
        "content_deltas": content_deltas,
        "first_reasoning_at": round(first_reasoning_at, 3) if first_reasoning_at else None,
        "first_content_at": round(first_content_at, 3) if first_content_at else None,
        "finish_reason": finish_reason,
        "usage_in_terminal_chunk": usage is not None,
        "usage": usage,
        "unparsed_frames": other_frames,
    }


def main() -> int:
    if not API_KEY:
        print("COLLEAGUE_API_KEY is unset — refusing to dial", file=sys.stderr)
        return 2
    results = []
    for thinking in (True, False):
        try:
            result = probe(thinking=thinking)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result = {"thinking": thinking, "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        print(json.dumps(result, indent=1))
        print("-" * 60)
    out = os.environ.get("STREAM_PROBE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
