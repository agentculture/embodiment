"""Gwen's own past conversations, indexed and searched by her (deviation d8).

Operator, 2026-10-24: conversations are internal to Gwen -- "culture rules [are]
intended for between processes, and this [is] internal to embodiment/gwen" -- and
"I may want to ask questions about it". So a settled conversation (quiet for
:data:`~embodiment.daemon.history.SETTLE_AFTER_S`) is indexed here, in a private
index of its own, and ``search_conversations`` finds it on a typed turn beside the
library. The library is not written (c26 stands).

What a hit is worth travels with it (d7): every turn's text is labelled with who
said it and how it arrived, so an earlier reply of Gwen's reaches her prompt as
"Gwen's own earlier reply, not a source of fact", and the operator's speech as an
AI transcription. The labels are fixed host text, put in by this module.

The index is a :class:`~embodiment.library.index.DocumentIndex` in its own file
(the library's sync drops any item its catalog does not list, so the two never
share one). Each exchange -- an operator turn and the replies after it -- is one
chunk, because one short question alone is under the index's
:data:`~embodiment.library.index.MIN_CHUNK_CHARS` and would never be found.

Results are fenced like library text (one formatter, :mod:`embodiment.library.tools`),
and they arm the write guard: a quoted "remember that ..." from last week is a
quote, not an ask. The conversations the turn is IN (the live one, or the one being
continued) are never searched, so Gwen does not quote herself back.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from embodiment.daemon import history
from embodiment.library import tools as library_tools
from embodiment.tools import ToolSpec

CONVERSATION_KIND = "conversation"
INDEX_NAME = "conversations-index.json"
SEARCH_TOOL = "search_conversations"
#: Fixed host text: nothing above the threshold.
NO_MATCHES = "No earlier conversation matched."
#: Conversations indexed per settle tick: each exchange is two embeddings, and the
#: watch must not hold the embedder from a live turn for long.
MAX_PER_TICK = 3

#: The label each turn's text carries into the index, by ``via`` (d7).
LABELS = {
    history.VIA_TYPED: "the operator, typed (verbatim)",
    history.VIA_SPOKEN: "the operator, spoken (an AI transcription)",
    history.VIA_RECORDED: "the operator, recorded (an AI transcription; the audio is kept)",
    history.VIA_UPLOADED: "an uploaded audio file (an AI transcription; speaker unknown)",
    history.VIA_CLIP: "an audio clip (an AI transcription)",
    history.VIA_GENERATED: "Gwen, her own earlier reply (not a source of fact)",
    history.VIA_UNKNOWN: "the operator (how it arrived was not recorded)",
}

_DESCRIPTION = (
    "Search your own earlier conversations with the user by meaning, for questions "
    "about what was said before. Each passage says who said it and how; your own "
    "earlier replies are not facts. Cite each passage used as [n]."
)


def _day(ts: Any) -> str:
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()


def conversation_item(conversation: dict[str, Any], turns: list[dict[str, Any]]) -> tuple:
    """``(meta, passages)`` for one conversation: labelled turns, one heading per exchange."""
    digest = hashlib.sha256(
        json.dumps(
            [[t.get("role"), t.get("text"), t.get("via")] for t in turns], ensure_ascii=False
        ).encode()
    ).hexdigest()
    day = _day(conversation.get("started"))
    title = conversation.get("title") or ""
    meta = {
        "id": conversation["id"],
        "sha256": digest,
        "title": f"Conversation {day}: {title}" if day else f"Conversation: {title}",
        "kind": CONVERSATION_KIND,
        "created_at": day,
    }
    passages = []
    exchange = 0
    for i, turn in enumerate(turns):
        if turn.get("role") == "user":
            exchange += 1
        label = LABELS.get(turn.get("via"), LABELS[history.VIA_UNKNOWN])
        passages.append(
            {
                "anchor": f"turn-{i}",
                "heading": f"exchange {max(exchange, 1)}",
                "text": f"[{label}] {turn.get('text', '')}",
                "start_s": None,
                "end_s": None,
                "speaker": turn.get("role"),
            }
        )
    return meta, passages


def index_settled(
    index: Any,
    store: Any,
    settled_ids: Iterable[str],
    *,
    limit: int = MAX_PER_TICK,
) -> dict[str, Any]:
    """Bring *index* in step with the settled conversations; embed at most *limit*.

    *store* is a :class:`~embodiment.daemon.history.ConversationHistory`. The catalog
    is every settled conversation, so one that was continued leaves the index until
    it settles again (it is the live one meanwhile, never searched). Raises what
    :meth:`DocumentIndex.upsert_item` raises (``IndexRefused``) for the caller to
    count; what was indexed before the refusal stays.
    """
    items = {}
    for conversation_id in settled_ids:
        found = store.conversation(conversation_id)
        if found and found["turns"]:
            items[conversation_id] = conversation_item(found["conversation"], found["turns"])
    plan = index.reconcile([meta for meta, _ in items.values()])
    todo = [i for i in (*plan["added"], *plan["changed"]) if i in items][: max(0, limit)]
    embedded = 0
    for conversation_id in todo:
        meta, passages = items[conversation_id]
        embedded += index.upsert_item(meta, passages)
    return {
        "indexed": len(todo),
        "chunks": embedded,
        "removed": len(plan["removed"]),
        "waiting": len(plan["added"]) + len(plan["changed"]) - len(todo),
    }


class _Search:
    def __init__(
        self,
        index: Any,
        embed_query: Callable[[str], Any],
        collector: Any,
        exclude: Callable[[], Iterable[str]],
        threshold: float,
    ) -> None:
        self._index = index
        self._embed_query = embed_query
        self._collector = collector
        self._exclude = exclude
        self._threshold = threshold

    def __call__(self, query: Any = None, limit: Any = library_tools.DEFAULT_LIMIT) -> str:
        text = library_tools._text_arg(query, library_tools.MAX_QUERY_CHARS)
        bounded = library_tools._limit_arg(limit)
        try:
            vector = self._embed_query(text)
        except Exception as exc:  # noqa: BLE001  # re-raised with a declared code
            raise library_tools.LibraryToolError(library_tools.CODE_EMBED_FAILED) from exc
        if vector is None:
            raise library_tools.LibraryToolError(library_tools.CODE_EMBED_FAILED)
        skip = set(self._exclude() or ())
        hits = [
            hit
            for hit in self._index.search(
                vector,
                kinds=(CONVERSATION_KIND,),
                limit=bounded + len(skip),
                threshold=self._threshold,
            )
            if hit.item_id not in skip
        ][:bounded]
        if not hits:
            return NO_MATCHES
        citations = [
            library_tools.replace_source(
                library_tools._hit_citation(hit), library_tools.CONVERSATION_SOURCE
            )
            for hit in hits
        ]
        return library_tools._render(self._collector, citations)


def build_conversation_tool(
    index: Any,
    embed_query: Callable[[str], Any],
    collector: Any,
    *,
    exclude: Callable[[], Iterable[str]] = tuple,
    threshold: float = 0.0,
) -> ToolSpec:
    """``search_conversations`` over *index*, citing on the turn's *collector*.

    *exclude* names the conversations this turn is in; they are never returned.
    """
    return ToolSpec(
        name=SEARCH_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": library_tools.MAX_LIMIT,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        fn=_Search(index, embed_query, collector, exclude, threshold),
        description=_DESCRIPTION,
        codes=library_tools.CODES,
    )


def citation_is_conversation(source: Optional[dict]) -> bool:
    """True for a source dict a ``search_conversations`` hit produced."""
    return bool(source) and source.get("source") == library_tools.CONVERSATION_SOURCE
