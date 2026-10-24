"""Measure library retrieval through Gwen's own search route (plan t15, claim c29).

Two phases, so the question set is fixed BEFORE any measurement:

  write  STATE_DIR OUT_QUESTIONS [--per-item N] [--seed S]
         For each indexed library item, sample N passages and ask the gateway's
         ``worker`` role for one reworded question each (no copied phrases).
         Writes {question, item_id} lines plus a fixed set of off-topic
         negatives. The output carries real-derived text: keep it out of git.

  run    STATE_DIR QUESTIONS OUT_JSON
         Sends every question to the daemon's GET /api/library/search and
         writes AGGREGATES ONLY: hit@1/@5 for positives, false-positive rate
         for negatives (any hit at the daemon's threshold), and the per-query
         latency p50/p90/max at the indexed size.

The daemon must be running with the library configured. The install secret is
read from STATE_DIR and only ever sent as a header; the gateway key comes from
the environment (``grant run --inject EMBODIMENT_GATEWAY_KEY=...``).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8823"
GATEWAY = os.environ.get("EMBODIMENT_GATEWAY_URL", "http://localhost:8001")

NEGATIVES = [
    "What is the boiling point of water at the top of Everest?",
    "Who won the 1994 football World Cup?",
    "How do I bake sourdough bread at home?",
    "What is the capital of Peru?",
    "How many moons does Neptune have?",
    "What is a good stretch for lower back pain?",
    "Which year did the Berlin Wall fall?",
    "How do I change a bicycle tyre?",
    "What does a sommelier do?",
    "How long should a soft-boiled egg cook?",
]

PROMPT = (
    "Write ONE question a reader might ask whose answer is in the passage below. "
    "Reword it: do not copy any three consecutive words from the passage. "
    "Reply with the question only.\n\nPASSAGE:\n{passage}"
)


def _secret(state_dir: str) -> str:
    return (Path(state_dir) / "install-secret").read_text().strip()


def _api(state_dir: str, path: str, timeout: float = 30.0):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": "Bearer " + _secret(state_dir), "Origin": BASE}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - loopback
        return json.loads(resp.read())


def _worker(prompt: str) -> str:
    body = json.dumps(
        {
            "model": "worker",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.7,
            # worker is a reasoning model: without this the budget is spent thinking
            # and the reply content comes back empty (finish_reason "length").
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode()
    req = urllib.request.Request(
        GATEWAY + "/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + os.environ.get("EMBODIMENT_GATEWAY_KEY", ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:  # nosec B310 - loopback gateway
        text = json.loads(resp.read())["choices"][0]["message"]["content"] or ""
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def write(state_dir: str, out: str, per_item: int = 2, seed: int = 7) -> None:
    rng = random.Random(seed)
    items = [i for i in _api(state_dir, "/api/library")["items"] if i.get("indexed")]
    lines = []
    for item in items:
        passages = [
            p
            for p in _api(state_dir, f"/api/library/{item['id']}")["passages"]
            if len(p.get("text", "")) >= 120
        ]
        for passage in rng.sample(passages, min(per_item, len(passages))):
            question = _worker(PROMPT.format(passage=passage["text"][:1500]))
            if question:
                lines.append({"question": question, "item_id": str(item["id"])})
    lines += [{"question": q, "item_id": None} for q in NEGATIVES]
    Path(out).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    os.chmod(out, 0o600)
    print(f"wrote {len(lines)} questions ({len(lines) - len(NEGATIVES)} positive)", file=sys.stderr)


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def run(state_dir: str, questions: str, out: str) -> None:
    rows = [json.loads(x) for x in Path(questions).read_text().splitlines() if x.strip()]
    pos, neg, latency = [], [], []
    for row in rows:
        start = time.monotonic()
        hits = _api(state_dir, "/api/library/search?q=" + urllib.parse.quote(row["question"]))
        latency.append(time.monotonic() - start)
        ids = [str(h.get("item_id")) for h in hits.get("hits", [])]
        if row["item_id"] is None:
            neg.append(bool(ids))
        else:
            pos.append((ids[:1] == [row["item_id"]], row["item_id"] in ids[:5]))
    status = _api(state_dir, "/api/status")
    library = status.get("daemon", {}).get("library", {})
    result = {
        "positives": len(pos),
        "hit_at_1": sum(a for a, _ in pos),
        "hit_at_5": sum(b for _, b in pos),
        "negatives": len(neg),
        "false_positives": sum(neg),
        "latency_s": {
            "p50": round(statistics.median(latency), 3),
            "p90": round(_pct(latency, 0.9), 3),
            "max": round(max(latency), 3),
        },
        "indexed_items": library.get("items_indexed"),
        "indexed_chunks": library.get("chunks"),
        "threshold": library.get("similarity_threshold"),
        "threshold_calibrated": library.get("threshold_calibrated"),
        "library_gets": library.get("gets"),
    }
    Path(out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main(argv: list[str]) -> None:
    if argv[:1] == ["write"]:
        per = int(argv[argv.index("--per-item") + 1]) if "--per-item" in argv else 2
        seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 7
        write(argv[1], argv[2], per, seed)
    elif argv[:1] == ["run"]:
        run(argv[1], argv[2], argv[3])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
