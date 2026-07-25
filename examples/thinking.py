"""How does an embodiment *think*? — reasoning telemetry, not just outcomes.

Every experiment in `docs/live-test-results/` measured **outcomes**: correct or
not, finished or stopped. That is the cheap half. Two arms can post identical
success rates while reasoning completely differently, and an outcome-only
instrument cannot see the difference — which is exactly what happened when the
muse and solo arms both scored 1/4 on the designed problem.

This captures three layers of "how it thought", each strictly more informative
and strictly more expensive than the last:

**1. Volume — already in embodiment, never reported.** ``WorkStats`` accumulates
``reasoning_chars`` against ``answer_chars`` on every turn via
``add_generated``. The ratio is *thought per word written*: a thinking model
that emits 1479 reasoning characters for a two-sentence answer is doing
something different from one that emits 50.

**2. Shape — the trajectory.** Turn count, tool sequence, where boundaries
fired, what degraded. Two runs reaching the same answer by different routes are
not the same run.

**3. Content — the thinking vector.** Embed the reasoning text through the
rig's ``embedder`` role and compare runs by cosine similarity. This is the only
layer that can answer "did the muse change *how* it thinks, even when the answer
was identical?"

Layer 3 is why this exists. A negative result on outcomes is not a negative
result on process, and until now this repository had no way to tell them apart.

**What a thinking vector is not.** It is an embedding of the text a model
emitted as its reasoning field. It is not a window into computation, not
evidence about internal states, and not a measure of understanding. Two runs
with cosine 0.97 emitted similar *text*; that is all. Reported that way on
purpose — the alternative is the kind of overclaim this package refuses
everywhere else.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

__all__ = [
    "TurnTrace",
    "ThinkingTrace",
    "tracing_seam",
    "embed_texts",
    "cosine",
    "compare_traces",
]

DEFAULT_EMBEDDER = "Qwen/Qwen3-Embedding-0.6B"


@dataclass
class TurnTrace:
    """One model turn's generated text, kept rather than only counted."""

    index: int
    reasoning: str = ""
    content: str = ""
    tool_calls: tuple[str, ...] = ()

    @property
    def thought_per_written(self) -> Optional[float]:
        """Reasoning characters per answer character; ``None`` when nothing written.

        ``None`` rather than a large number or a zero: a turn that emitted only
        tool calls wrote no prose, and inventing a ratio for it would be a
        fabricated measurement.
        """
        if not self.content:
            return None
        return len(self.reasoning) / len(self.content)


@dataclass
class ThinkingTrace:
    """Every turn of one drive, plus the roll-ups worth reporting."""

    label: str = ""
    turns: list[TurnTrace] = field(default_factory=list)

    def record(self, reasoning: str, content: str, tool_calls: Sequence[str]) -> None:
        self.turns.append(
            TurnTrace(
                index=len(self.turns),
                reasoning=reasoning or "",
                content=content or "",
                tool_calls=tuple(tool_calls),
            )
        )

    @property
    def reasoning_text(self) -> str:
        return "\n\n".join(t.reasoning for t in self.turns if t.reasoning)

    @property
    def reasoning_chars(self) -> int:
        return sum(len(t.reasoning) for t in self.turns)

    @property
    def answer_chars(self) -> int:
        return sum(len(t.content) for t in self.turns)

    @property
    def tool_sequence(self) -> tuple[str, ...]:
        return tuple(name for t in self.turns for name in t.tool_calls)

    @property
    def thought_ratio(self) -> Optional[float]:
        """Whole-drive reasoning:answer character ratio, or ``None`` if silent."""
        return self.reasoning_chars / self.answer_chars if self.answer_chars else None

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "turns": len(self.turns),
            "reasoning_chars": self.reasoning_chars,
            "answer_chars": self.answer_chars,
            "thought_ratio": (
                round(self.thought_ratio, 2) if self.thought_ratio is not None else None
            ),
            "per_turn_reasoning": [len(t.reasoning) for t in self.turns],
            "tool_sequence": list(self.tool_sequence),
        }


def tracing_seam(complete: Callable[[list], Any], trace: ThinkingTrace) -> Callable[[list], Any]:
    """Wrap a model seam so every turn's reasoning is kept as it passes.

    Deliberately a wrapper rather than a change to the loop: what a host records
    about its own model is host policy, and ``embodiment.loop`` has no business
    retaining prompt-shaped text it was handed. The loop already *counts*
    reasoning (``WorkStats.add_generated``); keeping the text is a decision only
    the host can make, because the text may be sensitive.
    """

    def wrapped(messages: list) -> Any:
        response = complete(messages)
        trace.record(
            getattr(response, "reasoning", "") or "",
            getattr(response, "content", "") or "",
            [call.name for call in getattr(response, "tool_calls", ()) or ()],
        )
        return response

    return wrapped


def embed_texts(
    texts: Sequence[str],
    *,
    base_url: str = "http://localhost:8001/v1",
    model: str = DEFAULT_EMBEDDER,
    api_key: str = "",
    timeout: float = 300.0,
    max_chars: int = 20000,
) -> Optional[list[list[float]]]:
    """Embed via the rig's ``embedder`` role. ``None`` on any failure.

    Never raises: a thinking vector is a diagnostic, and a diagnostic that can
    break the thing it observes is worse than no diagnostic.

    ``max_chars`` exists because reasoning traces are *long*. A measured drive
    produced 29,511 characters of reasoning across two turns — roughly 7–8k
    tokens against an embedder advertising an 8192 context. The first version of
    this function passed the whole trace, the request failed, and the swallow
    below turned that into a silent ``None``: the diagnostic degraded exactly as
    designed and told me nothing. Truncation is a real loss of signal and is
    reported rather than hidden — see ``truncated`` in :func:`compare_traces`.
    """
    usable = [t[:max_chars] for t in texts if t and t.strip()]
    if not usable:
        return None
    key = api_key or os.environ.get("COLLEAGUE_API_KEY", "")
    endpoint = f"{base_url.rstrip('/')}/embeddings"
    if not endpoint.startswith(("http://", "https://")):
        return None
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"model": model, "input": list(usable)}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            payload = json.load(response)
        return [row["embedding"] for row in payload["data"]]
    except Exception:  # noqa: BLE001 - a diagnostic must not break its subject
        return None


def cosine(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Cosine similarity, or ``None`` when either vector is degenerate."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


def compare_traces(traces: Sequence[ThinkingTrace], **embed_kwargs: Any) -> dict[str, Any]:
    """Roll up several drives: volume, shape, and pairwise thinking similarity."""
    report: dict[str, Any] = {"runs": [t.summary() for t in traces]}

    sequences = [t.tool_sequence for t in traces]
    report["identical_tool_sequences"] = len(set(sequences)) == 1

    texts = [t.reasoning_text for t in traces]
    limit = int(embed_kwargs.get("max_chars", 20000))
    report["truncated_for_embedding"] = {
        t.label or i: len(text)
        for i, (t, text) in enumerate(zip(traces, texts))
        if len(text) > limit
    }
    vectors = embed_texts(texts, **embed_kwargs)
    if vectors is None or len(vectors) != len(traces):
        report["thinking_similarity"] = None
        report["note"] = "embedder unavailable — volume and shape only"
        return report

    pairs = {}
    for i in range(len(traces)):
        for j in range(i + 1, len(traces)):
            score = cosine(vectors[i], vectors[j])
            label = f"{traces[i].label or i} vs {traces[j].label or j}"
            pairs[label] = round(score, 4) if score is not None else None
    report["thinking_similarity"] = pairs
    report["reading"] = (
        "Cosine over embeddings of the reasoning TEXT. High means the two drives "
        "produced similar prose while thinking, not that they computed alike."
    )
    return report
