#!/usr/bin/env python3
"""Extract pi's answer from its `--mode json` event stream (stdin -> stdout).

Why this exists: `pi -p` prints only the final assistant message's TEXT part. The
associate model is a reasoning model, and on some runs it puts its whole answer in
the `thinking` part and leaves `text` empty - `pi -p` then prints nothing and exits
0, which looked like a reviewer that had nothing to say (seen on three reviews).

Prints the last non-empty assistant text. If no assistant message carried any text,
prints the last non-empty thinking part instead, under a line saying so, because a
review recovered from a reasoning trace is weaker evidence than a stated answer.
Exits 0 with empty output only when the stream truly held neither.
"""

import json
import sys


def main() -> int:
    last_text = last_thinking = ""
    for line in sys.stdin:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        parts = [p for p in message.get("content") or [] if isinstance(p, dict)]
        text = "\n".join(p.get("text") or "" for p in parts if p.get("type") == "text").strip()
        thinking = "\n".join(p.get("thinking") or "" for p in parts if p.get("type") == "thinking")
        if text:
            last_text = text
        if thinking.strip():
            last_thinking = thinking.strip()
    if last_text:
        print(last_text)
    elif last_thinking:
        print("> NOTE: pi's final message had no text part; this is its last reasoning trace.\n")
        print(last_thinking)
    return 0


if __name__ == "__main__":
    sys.exit(main())
