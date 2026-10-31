"""Open asks Gwen posted to the mesh, the answers that come back, and the bounds on both.

Pure state plus one 0600 JSON file (the store pattern of :mod:`embodiment.daemon.reading`):
no IRC client is imported, inputs are plain values (nick, channel, text, time).

Answer matching (the documented rule; it lives in :func:`match_answer` ALONE so it can
change). IRC has no threads and it is not yet known whether agentirc carries IRCv3 reply
tags, so:

0. agentirc has native threads: each ask is posted as its own thread, named from its ask id
   (:func:`thread_name`, valid for ``^[a-zA-Z0-9]([a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?$``), and
   inbound messages carry an ``agentirc.io/thread`` tag. Rules are tried in this order and
   the one that matched is recorded (``Match.rule``) so the page can show it:

1. ``thread``: the message's thread name equals an open ask's thread: exact match;
2. ``reply_to``: if the caller passes ``reply_to`` (a msgid) and an open ask recorded that
   msgid, that ask matches exactly;
3. ``fallback``: otherwise a message matches only if it *addresses* ``own_nick`` (starts with
   ``nick:`` / ``nick,`` / ``nick `` or mentions it as a word) in the ask's channel, or is a
   direct message (``channel`` equals the sender's nick, which is how a DM ask is stored);
   it attaches to that channel's most recent open ask; when more than one ask is open in
   that channel the match is marked ``ambiguous`` (the page shows it).

"Late" means the answer arrived after the turn that asked had ended; the conversation id
is kept so the daemon can route it. Nothing here raises: failures come back as values.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

ASKS_NAME = "mesh-asks.json"
#: Prefix of an ask's thread name; with the 16-hex ask id it is 25 characters, inside the
#: 32 agentirc allows.
THREAD_PREFIX = "gwen-ask-"
RULE_THREAD, RULE_REPLY_TO, RULE_FALLBACK = "thread", "reply_to", "fallback"

#: How long an ask stays open. Hours, not minutes: a peer agent answers when its own turn
#: comes round, and a two-hour-late answer must still land (marked late). 6 h covers a
#: working session; past it the ask is shown as expired, never dropped.
DEFAULT_EXPIRY_S = 6 * 3600.0
#: Cap on open asks at once: each is a promise to listen; unbounded asks would let a chatty
#: turn bury the operator's page and give a peer unbounded standing to start turns.
MAX_OPEN_ASKS = 8
#: Cap on expired/answered asks kept on disk (shown, never silently dropped before this).
MAX_KEPT_ASKS = 200
#: Rate cap on turns started by a mesh message: a looping peer must not drive Gwen's
#: model (and voice) at its own pace. 6 per 10 minutes is above any human-paced exchange.
MAX_MESH_TURNS = 6
MESH_TURN_WINDOW_S = 600.0
#: Asks allowed in one turn: one post per operator turn is the loop breaker.
MAX_ASKS_PER_TURN = 1
#: Caps on stored text, so the file stays bounded whatever a peer sends.
MAX_TEXT_CHARS = 2000
MAX_ANSWERS_PER_ASK = 20

OPEN, ANSWERED, EXPIRED = "open", "answered", "expired"

REFUSE_TURN_LIMIT = "ask-refused-one-per-turn"
REFUSE_MESH_TURN = "ask-refused-mesh-started-turn"
REFUSE_OPEN_CAP = "ask-refused-open-cap"
REFUSE_RATE = "mesh-turn-refused-rate"
REFUSE_SAVE = "ask-refused-save-failed"


@dataclass(frozen=True)
class Match:
    ask_id: str
    conversation_id: str
    ambiguous: bool
    late: bool
    rule: str = RULE_FALLBACK
    #: The ask had expired when this answer came; it still lands, marked (operator,
    #: 2026-10-31: "Yes. Via event"). Only exact matches (thread, reply-to) reach an
    #: expired ask; the nick-addressed fallback stays on open asks, so an ordinary mention
    #: long after an ask expired is a new conversation, not a stale answer.
    after_expiry: bool = False


def thread_name(ask_id: str) -> str:
    return f"{THREAD_PREFIX}{ask_id}"


def _addresses(text: str, own_nick: str) -> bool:
    nick = re.escape(own_nick)
    return re.search(rf"(?<![\w-]){nick}(?![\w-])", text, re.IGNORECASE) is not None


def match_answer(
    asks: list[dict[str, Any]],
    own_nick: str,
    sender: str,
    channel: str,
    text: str,
    now: float,
    reply_to: Optional[str] = None,
    thread: Optional[str] = None,
) -> Optional[Match]:
    """The one matching rule (see the module docstring). ``None`` when nothing matches."""
    open_asks = [a for a in asks if a.get("status") == OPEN and now < a["expires_at"]]
    # Exact matches also reach an ask that expired (or was already answered): a late
    # answer in the ask's own thread is still that ask's answer.
    exact_pool = [a for a in asks if a.get("status") in (OPEN, EXPIRED, ANSWERED)]
    chosen: Optional[dict[str, Any]] = None
    ambiguous = False
    rule = RULE_FALLBACK
    if thread:
        chosen = next((a for a in exact_pool if a.get("thread") == thread), None)
        rule = RULE_THREAD
    if chosen is None and reply_to:
        chosen = next((a for a in exact_pool if a.get("msgid") == reply_to), None)
        rule = RULE_REPLY_TO
    if chosen is None:
        rule = RULE_FALLBACK
        direct = channel.lower() == sender.lower()
        if not (direct or _addresses(text or "", own_nick)):
            return None
        same = [a for a in open_asks if a["channel"].lower() == channel.lower()]
        if not same:
            return None
        chosen = max(same, key=lambda a: a["posted_at"])
        ambiguous = len(same) > 1
    ended = chosen.get("turn_ended_at")
    after_expiry = chosen.get("status") == EXPIRED or now >= chosen["expires_at"]
    late = (ended is not None and now > ended) or after_expiry
    return Match(chosen["ask_id"], chosen["conversation_id"], ambiguous, late, rule, after_expiry)


@dataclass(frozen=True)
class Result:
    ok: bool
    ask: Optional[dict[str, Any]] = None
    refusal: Optional[str] = None


class AskBook:
    """Open asks, kept across restarts. Never raises."""

    def __init__(
        self,
        path: Any,
        clock: Callable[[], float] = time.time,
        expiry_s: float = DEFAULT_EXPIRY_S,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._expiry_s = expiry_s
        self._lock = threading.Lock()
        self._asks: Optional[dict[str, dict[str, Any]]] = None
        self._turns_asked: dict[str, int] = {}
        self._mesh_turns: set[str] = set()
        self._mesh_starts: list[float] = []
        self.refusals: list[dict[str, Any]] = []
        self.save_failures = 0
        self.unreadable = 0

    # -- persistence -------------------------------------------------------------------
    @staticmethod
    def _valid(ask_id: Any, ask: Any) -> bool:
        return (
            isinstance(ask_id, str)
            and isinstance(ask, dict)
            and all(isinstance(ask.get(k), str) for k in ("conversation_id", "channel"))
            and all(isinstance(ask.get(k), (int, float)) for k in ("posted_at", "expires_at"))
            and ask.get("status") in (OPEN, ANSWERED, EXPIRED)
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._asks is None:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (FileNotFoundError, NotADirectoryError):
                data = {}
            except (OSError, ValueError):
                self.unreadable += 1
                try:
                    os.replace(self._path, self._path.with_name(f"{self._path.name}.unreadable"))
                except OSError:
                    self.save_failures += 1
                data = {}
            self._asks = {
                k: {**v, "ask_id": k}
                for k, v in (data.items() if isinstance(data, dict) else ())
                if self._valid(k, v)
            }
        return self._asks

    def _save(self, asks: dict[str, dict[str, Any]]) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".mesh-asks-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(asks, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            self.save_failures += 1
            return False
        return True

    def _expire(self, asks: dict[str, dict[str, Any]], now: float) -> None:
        for ask in asks.values():
            if ask["status"] == OPEN and now >= ask["expires_at"]:
                ask["status"] = EXPIRED  # shown, never dropped

    def _refuse(self, code: str, **fields: Any) -> Result:
        self.refusals.append({"code": code, "at": self._clock(), **fields})
        del self.refusals[:-100]
        return Result(False, refusal=code)

    # -- turns and bounds --------------------------------------------------------------
    def begin_turn(self, turn_id: str, mesh_started: bool = False) -> Result:
        """Register a turn. A mesh-started turn is rate-capped and may reply, never ask."""
        with self._lock:
            now = self._clock()
            if mesh_started:
                self._mesh_starts = [t for t in self._mesh_starts if now - t < MESH_TURN_WINDOW_S]
                if len(self._mesh_starts) >= MAX_MESH_TURNS:
                    return self._refuse(REFUSE_RATE, turn_id=turn_id)
                self._mesh_starts.append(now)
                self._mesh_turns.add(turn_id)
            self._turns_asked.setdefault(turn_id, 0)
            return Result(True)

    def end_turn(self, turn_id: str) -> None:
        """Stamp the turn's asks as ended: an answer after this is late."""
        with self._lock:
            asks = self._load()
            now = self._clock()
            changed = False
            for ask in asks.values():
                if ask.get("turn_id") == turn_id and ask.get("turn_ended_at") is None:
                    ask["turn_ended_at"] = now
                    changed = True
            if changed:
                self._save(asks)
            self._turns_asked.pop(turn_id, None)
            self._mesh_turns.discard(turn_id)

    # -- asks --------------------------------------------------------------------------
    def open_ask(
        self,
        turn_id: str,
        conversation_id: str,
        channel: str,
        text: str = "",
        msgid: Optional[str] = None,
    ) -> Result:
        with self._lock:
            now = self._clock()
            if turn_id in self._mesh_turns:
                return self._refuse(REFUSE_MESH_TURN, turn_id=turn_id)
            if self._turns_asked.get(turn_id, 0) >= MAX_ASKS_PER_TURN:
                return self._refuse(REFUSE_TURN_LIMIT, turn_id=turn_id)
            asks = self._load()
            self._expire(asks, now)
            if sum(1 for a in asks.values() if a["status"] == OPEN) >= MAX_OPEN_ASKS:
                return self._refuse(REFUSE_OPEN_CAP, turn_id=turn_id)
            ask_id = uuid.uuid4().hex[:16]
            ask = {
                "ask_id": ask_id,
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "channel": channel,
                "text": (text or "")[:MAX_TEXT_CHARS],
                "thread": thread_name(ask_id),
                "msgid": msgid,
                "posted_at": now,
                "expires_at": now + self._expiry_s,
                "turn_ended_at": None,
                "status": OPEN,
                "answers": [],
            }
            asks[ask_id] = ask
            for old in sorted(asks.values(), key=lambda a: a["posted_at"])[:-MAX_KEPT_ASKS]:
                if old["status"] != OPEN:
                    asks.pop(old["ask_id"], None)
            if not self._save(asks):
                asks.pop(ask_id, None)
                return self._refuse(REFUSE_SAVE, turn_id=turn_id)
            self._turns_asked[turn_id] = self._turns_asked.get(turn_id, 0) + 1
            return Result(True, ask=dict(ask))

    def answer(
        self,
        own_nick: str,
        sender: str,
        channel: str,
        text: str,
        reply_to: Optional[str] = None,
        thread: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Attach a message to its ask. Returns the match with the ask's ids, or None."""
        with self._lock:
            now = self._clock()
            asks = self._load()
            self._expire(asks, now)
            m = match_answer(
                list(asks.values()), own_nick, sender, channel, text, now, reply_to, thread
            )
            if m is None:
                self._save(asks)
                return None
            ask = asks[m.ask_id]
            if len(ask["answers"]) < MAX_ANSWERS_PER_ASK:
                ask["answers"].append(
                    {
                        "sender": sender,
                        "text": (text or "")[:MAX_TEXT_CHARS],
                        "at": now,
                        "late": m.late,
                        "ambiguous": m.ambiguous,
                        "rule": m.rule,
                        "after_expiry": m.after_expiry,
                    }
                )
            ask["status"] = ANSWERED
            self._save(asks)
            return {
                "ask_id": m.ask_id,
                "conversation_id": m.conversation_id,
                "late": m.late,
                "ambiguous": m.ambiguous,
                "rule": m.rule,
                "after_expiry": m.after_expiry,
            }

    def all(self) -> list[dict[str, Any]]:
        """Every kept ask, oldest first; expired ones are shown, never dropped."""
        with self._lock:
            asks = self._load()
            self._expire(asks, self._clock())
            return [dict(a) for a in sorted(asks.values(), key=lambda a: a["posted_at"])]
