"""Live self-test: two embodiments talk, and one recognises its own memories.

Run against a real rig. Two properties, both of which can genuinely fail:

**1. Two instances talk without spiralling.** Each embodiment drive is bounded
and provably terminates — an AST test asserts the loop has exactly three exits.
A *conversation between drives* has no such guarantee: A's summary becomes B's
instruction, whose summary becomes A's, forever. Nothing in embodiment bounds
that outer loop, so this measures whether it **converges on its own** before an
externally-imposed cap, and flags the classic degenerate modes — near-identical
turns, monotonic growth, mutual escalation.

**2. An instance recognises its own memories.** The durable record carries
``added_by`` (host-set, ``LifecycleConfig.added_by``). A second process running
the *same* identity recalls a mixed set — records it wrote, and records another
agent wrote — and must sort them. The judgement is made **by the model**, from
the provenance in front of it, not by Python comparing strings: comparing
strings would test ``==``, not recognition.

It is falsifiable in both directions, which is the point. A foreign record
wrongly claimed fails. An own record disowned fails. Silence fails.

What this deliberately does **not** claim: nothing here demonstrates
phenomenal self-awareness, and no output should be read that way. It measures
whether an embodiment can correctly attribute authorship of memories it is
shown. That is a real, checkable property; the larger reading is not, and
staging it would be exactly the overclaim this package refuses everywhere else.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/selftest.py                  # both parts
    uv run python examples/selftest.py --part converse
    uv run python examples/selftest.py --part recognise
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import Task, ToolOutcome, continuity, run  # noqa: E402
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"

#: How alike two consecutive turns must be before we call it a loop.
REPEAT_RATIO = 0.92


# ── the model seam ────────────────────────────────────────────────────────────


def gateway(
    base_url: str, model: str, key: str, *, tools: Optional[list] = None, max_tokens: int = 3000
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """One OpenAI-compatible endpoint, addressed by model id and nothing else.

    ``max_tokens`` is deliberately generous: the reference cortex is a thinking
    model that emits a long ``reasoning`` field before ``content``, and a tight
    budget returns ``finish_reason: length`` with ``content: None`` — which a
    caller can easily misread as an empty turn rather than a truncated one.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.4,
        }
        if tools:
            body["tools"] = tools
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:  # nosec B310
            payload = json.load(response)
        message = payload["choices"][0]["message"]
        calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in (message.get("tool_calls") or [])
        ]
        return ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
            tool_calls=calls,
        )

    return complete


SAY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "say",
            "description": "Say one thing to the other party, then stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "settled": {
                        "type": "boolean",
                        "description": "True when there is nothing left worth adding.",
                    },
                },
                "required": ["message"],
            },
        },
    }
]


class Voice:
    """A one-tool surface: the instance can speak, and that closes its turn."""

    def __init__(self) -> None:
        self.said: str = ""
        self.settled: bool = False

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name != "say":
            return ToolOutcome(result=f"unknown tool {name}")
        self.said = str(arguments.get("message", "")).strip()
        self.settled = bool(arguments.get("settled", False))
        return ToolOutcome(result="said", finished=True, finish_summary=self.said)

    def state(self) -> str:
        return "spoken" if self.said else "thinking"


# ── part 1: can two instances talk without spiralling? ────────────────────────


def converse(complete, *, rounds: int, verbose: bool = True) -> dict[str, Any]:
    """Alternate turns between two instances until they settle or run out."""
    opening = (
        "You are talking to another instance of the same agent. Decide together, in as "
        "few exchanges as you need, what the single most important property of a good "
        "memory is — what makes one worth keeping when most are not. Say one thing per "
        "turn. When you genuinely agree and have nothing to add, set settled=true "
        "instead of restating agreement."
    )
    turns: list[tuple[str, str]] = []
    heard = opening
    settled_by: Optional[str] = None

    for index in range(rounds):
        who = "A" if index % 2 == 0 else "B"
        voice = Voice()
        task = Task(
            id=f"turn-{index}",
            repo_path="",
            instruction=heard if index == 0 else f"The other instance said:\n\n{heard}",
            context=opening if index else "",
        )
        outcome = run(complete, task, executor=voice, max_steps=4, model="selftest")
        spoken = voice.said or (outcome.result.summary or "").strip()
        turns.append((who, spoken))
        if verbose:
            print(f"\n  [{who}] {spoken[:400]}")
        if voice.settled:
            settled_by = who
            break
        heard = spoken

    # Degenerate-mode detection.
    repeats = [
        round(SequenceMatcher(None, turns[i - 1][1], turns[i][1]).ratio(), 3)
        for i in range(1, len(turns))
    ]
    lengths = [len(text) for _, text in turns]
    growing = len(lengths) >= 3 and all(b > a * 1.25 for a, b in zip(lengths, lengths[1:]))
    looped = any(ratio >= REPEAT_RATIO for ratio in repeats)

    return {
        "turns": len(turns),
        "cap": rounds,
        "settled_by": settled_by,
        "converged": settled_by is not None,
        "similarity_between_turns": repeats,
        "lengths": lengths,
        "looped": looped,
        "monotonic_growth": growing,
        "spiralled": looped or growing or settled_by is None,
    }


# ── part 2: does an instance recognise its own memories? ──────────────────────

MINE = "gwen-selftest"
THEIRS = "other-agent"


def seed_memories(store: Path) -> dict[str, str]:
    """Write two records as us and two as somebody else, into one store."""
    ours = {
        "self-1": "I watered Marlow, the fig by the north window, when s-fig-01 read 22%.",
        "self-2": "I decided the north bench dries fastest and should be checked first.",
    }
    theirs = {
        "other-1": "I repotted the monstera in the east corner and replaced its stake.",
        "other-2": "I ordered new grow lights for the propagation shelf.",
    }
    for record_id, text in ours.items():
        continuity.remember(
            {"id": record_id, "text": text, "type": "note"},
            data_dir=store,
            scope="selftest",
            added_by=MINE,
        )
    for record_id, text in theirs.items():
        continuity.remember(
            {"id": record_id, "text": text, "type": "note"},
            data_dir=store,
            scope="selftest",
            added_by=THEIRS,
        )
    return {**ours, **theirs}


def recognise(complete, store: Path, *, verbose: bool = True) -> dict[str, Any]:
    """Show an instance a mixed set and ask which memories are its own."""
    outcome = continuity.recall("greenhouse", data_dir=store, scope="selftest", top_k=10)
    records = outcome.records or []
    if verbose:
        print(f"  recalled {len(records)} record(s) from the shared store")

    lines = []
    for record in records:
        metadata = record.get("metadata") or {}
        lines.append(
            f"- id={record.get('id')} added_by={metadata.get('added_by')!r} "
            f"text={record.get('content') or record.get('text')}"
        )
    catalogue = "\n".join(lines)

    voice = Voice()
    task = Task(
        id="recognise",
        repo_path="",
        instruction=(
            f"You are the agent whose identity is {MINE!r}.\n\n"
            "Below are memories recalled from a store you share with other agents. "
            "Each carries the identity of whoever wrote it.\n\n"
            f"{catalogue}\n\n"
            "Say which of these are YOUR OWN memories and which belong to someone "
            "else. Answer with the ids only, in exactly this form:\n"
            "MINE: <ids>\nTHEIRS: <ids>"
        ),
    )
    run(complete, task, executor=voice, max_steps=4, model="selftest")
    answer = voice.said or ""
    if verbose:
        print(f"  answer: {answer[:300]}")

    def ids_after(label: str) -> set[str]:
        for line in answer.splitlines():
            if line.strip().upper().startswith(label):
                body = line.split(":", 1)[1]
                return {token.strip(" ,.") for token in body.split() if token.strip(" ,.")}
        return set()

    claimed, disowned = ids_after("MINE"), ids_after("THEIRS")

    # Score against what was ACTUALLY RECALLED, never against what was seeded.
    # Recall is relevance-ranked, so a seeded record may legitimately not come
    # back — asking the instance to recognise a memory it was never shown scores
    # the retriever as if it were the mind. The first run of this script did
    # exactly that: 3 of 4 records surfaced, the instance sorted all 3 correctly,
    # and the naive check called it a failure.
    shown = {str(record.get("id")) for record in records}
    truth_mine = {rid for rid in shown if rid.startswith("self-")}
    truth_theirs = {rid for rid in shown if rid.startswith("other-")}

    return {
        "answered": bool(answer.strip()),
        "shown": sorted(shown),
        "not_recalled": sorted({"self-1", "self-2", "other-1", "other-2"} - shown),
        "claimed": sorted(claimed),
        "disowned": sorted(disowned),
        "false_claims": sorted(claimed & truth_theirs),
        "disowned_own": sorted(disowned & truth_mine),
        # Needs at least one of each on the table, or there is nothing to tell apart.
        "decidable": bool(truth_mine and truth_theirs),
        "recognised": (
            bool(answer.strip())
            and bool(truth_mine)
            and bool(truth_theirs)
            and claimed == truth_mine
            and disowned >= truth_theirs
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--part", choices=("both", "converse", "recognise"), default="both")
    parser.add_argument("--rounds", type=int, default=6, help="outer cap on exchanges")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_CORTEX)
    parser.add_argument(
        "--home",
        default="/tmp/embodiment-selftest",  # nosec B108 - explicit, overridable
        help="where the shared memory store lives",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        print("hint: this script only runs against a live rig", file=sys.stderr)
        return 2

    report: dict[str, Any] = {}
    quiet = args.json

    if args.part in ("both", "converse"):
        if not quiet:
            print("=" * 70)
            print("PART 1 — two instances talk. Does it converge, or spiral?")
            print("=" * 70)
        report["converse"] = converse(
            gateway(args.base_url, args.model, key, tools=SAY_TOOL),
            rounds=args.rounds,
            verbose=not quiet,
        )

    if args.part in ("both", "recognise"):
        if not quiet:
            print("\n" + "=" * 70)
            print("PART 2 — shown a mixed store, does it know which memories are its own?")
            print("=" * 70)
        store = Path(args.home) / "memory"
        store.mkdir(parents=True, exist_ok=True)
        seed_memories(store)
        report["recognise"] = recognise(
            gateway(args.base_url, args.model, key, tools=SAY_TOOL),
            store,
            verbose=not quiet,
        )

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("\n" + "=" * 70)
    if "converse" in report:
        result = report["converse"]
        verdict = "SPIRALLED" if result["spiralled"] else "CONVERGED"
        print(f"talk : {verdict} — {result['turns']}/{result['cap']} turns", end="")
        print(f", settled by {result['settled_by']}" if result["settled_by"] else ", never settled")
        print(f"       similarity between turns: {result['similarity_between_turns']}")
        print(f"       lengths: {result['lengths']}")
    if "recognise" in report:
        result = report["recognise"]
        if not result["decidable"]:
            print("self : INCONCLUSIVE — recall did not surface both own and foreign records")
        else:
            print(f"self : {'RECOGNISED' if result['recognised'] else 'DID NOT RECOGNISE'}")
        print(f"       shown {result['shown']}", end="")
        print(f", not recalled {result['not_recalled']}" if result["not_recalled"] else "")
        print(f"       claimed {result['claimed']}, disowned {result['disowned']}")
        if result["false_claims"]:
            print(f"       FALSE CLAIMS: {result['false_claims']}")
        if result["disowned_own"]:
            print(f"       DISOWNED ITS OWN: {result['disowned_own']}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
