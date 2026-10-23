"""embodiment.daemon.recall_events — recall off the voice turn's critical path.

Plan task ``t13`` of ``gwen-remembers-recordings`` (operator decision ``q8``):
*the turn starts generating without waiting for memory, and recall results
arrive as an event — used by the reply's next sentence, or starting Gwen
speaking, or stopping and restarting her.* This module is that orchestration
and nothing else. It owns no socket, no audio, no model and no prompt; every
one of those arrives injected, so the whole of it is testable without a
daemon. Wiring it into :mod:`embodiment.daemon.app` is task ``t16``.

What the generation seam can and cannot do
-------------------------------------------
Generation is ONE non-streaming completion (``app.py:http_complete``) and
:mod:`embodiment.voice` splits the finished reply into sentences. A completion
already in flight therefore cannot absorb a memory that lands while it runs,
and neither can the sentences it already produced. So "the next sentence
uses it" is built the only way the seam allows: at each sentence boundary the
reply loop looks for an arrival, and on one it asks for a **second**
completion for the not-yet-spoken remainder, given the memory and the
sentences already spoken as context. The first completion's unspoken
sentences are **dropped, never spliced** (``c59``) — and counted
(``sentences_dropped``), because the cost of a second completion is real and
unmeasured (plan risk on ``t13``).

Where an arrival lands, and the four counters
---------------------------------------------
Every recall that comes back (or fails) increments **exactly one** of
(``c62``/``h50``):

* :data:`LANDED_BEFORE_FIRST_SENTENCE` — taken up before anything was spoken;
  the whole reply comes from a completion that saw the memory.
* :data:`LANDED_DURING_REPLY` — taken up after sentence *k*; sentences
  *k+1..n* come from the remainder completion (or a restart, below).
* :data:`LANDED_FOLLOW_UP` — landed after the reply and was spoken as the one
  memory follow-up for that turn.
* :data:`LANDED_DROPPED` — everything else, with the reason counted separately
  (:meth:`BackgroundRecall.status` ``dropped_reasons``): an empty or failed
  recall, a late one below the threshold, outside the window, while somebody
  holds the floor, superseded by a newer turn, or after close.

A turn that answered without memory because recall was late is **not** a
degradation (the spec says so), so a drop is counted, never recorded to the
ledger. A recall that *fails*, a seam that raises, a completion that comes
back unusable and every restart are recorded through the injected *record*
sink (C3), with reasons built from counts and class names only — never the
memory, the query or what was spoken (:mod:`embodiment.safe_reason`).

The status block also carries the arrival-time distribution (a bounded ring of
``latency_s`` from turn start and, for a late arrival, ``after_reply_end_s``),
because the follow-up window below is derived from it.

The follow-up, and why its window is a parameter
-------------------------------------------------
A recall that lands after the reply becomes **at most one** follow-up for that
turn (``c60``), spoken only if its relevance clears
:attr:`RecallEventsConfig.follow_up_threshold` (the calibrated ``c52``
threshold), only while nobody holds the floor, and only if it landed within
:attr:`RecallEventsConfig.follow_up_window_s` of the reply's end.

Both default to ``None``, which turns follow-ups **off** — every late arrival
is then dropped and counted as ``no-window`` / ``no-threshold``. That is
deliberate, and it is lesson 1 of ``CLAUDE.md``: *a clock sized against the
wrong quantity silently becomes the measurement.* The window bounds how late
after a reply a memory may still be spoken; the quantity it must be derived
from is the measured recall-arrival distribution this module publishes, and
that has not been measured yet (plan task ``t18``). A number written here
today would be a guess wearing a constant's name. The window gates the
**arrival** — measured from reply end to arrival — not the moment speech
starts, so the follow-up completion's own latency comes on top of it; whoever
sizes the window from ``t18``'s numbers must size it with that in mind.

A recall with no relevance score (keyword mode reports none) never clears a
threshold, so in keyword mode a late memory is dropped rather than spoken on
an unscored guess.

Stop-and-restart
----------------
Reserved for a memory the already-spoken text is inconsistent with (``c61``).
Deciding *inconsistent* is a judgement this module does not make: it asks the
injected ``contradicts(memory, spoken)`` predicate, and with none injected
there is never a restart. When it fires — never before anything was spoken,
and never while the floor is held (re-checked after the judgement) — it calls
the same ``stop_playback`` path barge-in uses, records
:data:`RECALL_EVENTS_RESTARTED` with a reason that carries only counts, and
speaks a ``restart`` completion. A failed restart completion is recorded and
says nothing more: the reply was already voiced in part, and the remainder it
would fall back to is the text the memory contradicted.

The unprompted-speech seam
--------------------------
The follow-up is not a recall special case. :class:`Unprompted` is the one
entry point for *Gwen speaks without being spoken to* (``c63``), which #89
(job results) and #90 (chosen silence) also need: an :class:`UnpromptedEvent`
names its source, a dedup key (at most one utterance per key), an optional
window, an optional staleness check, and either ready text or a ``compose``
callable. It applies the floor, busy and window gates, composes, re-checks the
floor, and speaks sentence by sentence, stopping if the floor is taken.

**The never-silent rule (``turn.py``, b4) does not apply here.** A reply to a
person must never be silence, so :mod:`embodiment.turn` substitutes a fallback
for an unspeakable completion. A follow-up nobody asked for is the opposite
case: an unusable one is *dropped* (:data:`DROP_UNSPEAKABLE`) and nothing is
spoken — no fallback text, no audio. The reply this module drives still keeps
the rule: an unusable *first* completion speaks
:data:`~embodiment.turn.FALLBACK_TEXT`.

Degrade, never raise
--------------------
No public method raises. A raising ``floor_held`` counts as *held* (never talk
over someone because a seam broke); a raising clock makes a late arrival fall
outside every window; a raising ``spawn`` drops that turn's recall and the
turn still replies; a raising ``speak`` ends that utterance. Each is recorded.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.turn import FALLBACK_TEXT, is_speakable
from embodiment.voice import split_sentences

__all__ = [
    "LANDED_BEFORE_FIRST_SENTENCE",
    "LANDED_DURING_REPLY",
    "LANDED_FOLLOW_UP",
    "LANDED_DROPPED",
    "PURPOSE_REPLY",
    "PURPOSE_REMAINDER",
    "PURPOSE_RESTART",
    "PURPOSE_FOLLOW_UP",
    "DROP_ALREADY_SPOKEN",
    "DROP_BELOW_THRESHOLD",
    "DROP_BUSY",
    "DROP_CLOSED",
    "DROP_COMPOSE_FAILED",
    "DROP_EMPTY",
    "DROP_FLOOR_HELD",
    "DROP_MALFORMED",
    "DROP_NO_THRESHOLD",
    "DROP_NO_WINDOW",
    "DROP_OUTSIDE_WINDOW",
    "DROP_RECALL_FAILED",
    "DROP_SPEAK_FAILED",
    "DROP_SUPERSEDED",
    "DROP_UNSPEAKABLE",
    "RECALL_EVENTS_RECALL_FAILED",
    "RECALL_EVENTS_COMPLETE_FAILED",
    "RECALL_EVENTS_SPEAK_FAILED",
    "RECALL_EVENTS_SEAM_FAILED",
    "RECALL_EVENTS_RESTARTED",
    "RECALL_EVENTS_CLOSED",
    "DEFAULT_MAX_SAMPLES",
    "Recalled",
    "CompletionRequest",
    "RecallEventsConfig",
    "ReplyOutcome",
    "UnpromptedEvent",
    "UnpromptedOutcome",
    "Unprompted",
    "BackgroundRecall",
]

# ── where an arrival landed: exactly one per recall ──────────────────────

LANDED_BEFORE_FIRST_SENTENCE = "before_first_sentence"
LANDED_DURING_REPLY = "during_reply"
LANDED_FOLLOW_UP = "follow_up"
LANDED_DROPPED = "dropped"
_LANDINGS = (
    LANDED_BEFORE_FIRST_SENTENCE,
    LANDED_DURING_REPLY,
    LANDED_FOLLOW_UP,
    LANDED_DROPPED,
)

# ── what a completion request is for ─────────────────────────────────────

#: The reply. ``memory`` is empty on the first completion and set when the
#: memory landed before the first sentence was spoken.
PURPOSE_REPLY = "reply"
#: The not-yet-spoken remainder, given the memory and what was already said.
PURPOSE_REMAINDER = "remainder"
#: The memory contradicts what was said; speak a correction from here.
PURPOSE_RESTART = "restart"
#: The one memory follow-up for a reply that has already finished.
PURPOSE_FOLLOW_UP = "follow-up"

# ── why an arrival or an unprompted utterance was dropped (counted) ──────

DROP_ALREADY_SPOKEN = "already-spoken"
DROP_BELOW_THRESHOLD = "below-threshold"
DROP_BUSY = "busy"
DROP_CLOSED = "closed"
DROP_COMPOSE_FAILED = "compose-failed"
DROP_EMPTY = "empty"
DROP_FLOOR_HELD = "floor-held"
DROP_MALFORMED = "malformed"
DROP_NO_THRESHOLD = "no-threshold"
DROP_NO_WINDOW = "no-window"
DROP_OUTSIDE_WINDOW = "outside-window"
DROP_RECALL_FAILED = "recall-failed"
DROP_SPEAK_FAILED = "speak-failed"
DROP_SUPERSEDED = "superseded"
DROP_UNSPEAKABLE = "unspeakable"

# ── recorded through the record sink (C3) ────────────────────────────────

RECALL_EVENTS_RECALL_FAILED = "recall-events-recall-failed"
RECALL_EVENTS_COMPLETE_FAILED = "recall-events-complete-failed"
RECALL_EVENTS_SPEAK_FAILED = "recall-events-speak-failed"
RECALL_EVENTS_SEAM_FAILED = "recall-events-seam-failed"
#: Not a fault: the spec asks that every restart record why it restarted.
RECALL_EVENTS_RESTARTED = "recall-events-restarted"
RECALL_EVENTS_CLOSED = "recall-events-closed"

#: Size of the arrival-sample ring in :meth:`BackgroundRecall.status`. A
#: memory bound for a long-running daemon, not a measurement window.
DEFAULT_MAX_SAMPLES = 256

#: Bound on the dedup keys :class:`Unprompted` remembers.
_MAX_KEYS = 1024


def _no_record(code: str, reason: str) -> None:
    """The record sink when none is injected: counting still happens in status()."""


# ── data ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Recalled:
    """What the recall seam returns.

    *rendered* is prompt text from :func:`embodiment.memory.render_recalled`
    (already quoted and attributed — the ONE place recall enters a prompt);
    empty means nothing to use. *relevance* is the best record's similarity in
    the units of :attr:`RecallEventsConfig.follow_up_threshold`, or ``None``
    when the recall mode scores nothing. *count* is how many records rendered.
    """

    rendered: str = ""
    relevance: Optional[float] = None
    count: int = 0


@dataclass(frozen=True)
class CompletionRequest:
    """One call to the injected ``complete`` seam. It returns the text to speak.

    *query* is the user's words verbatim; *memory* the rendered recall (empty
    on the first completion); *spoken* the sentences already handed to the
    voice, in order — the context a remainder, restart or follow-up builds on.
    """

    purpose: str
    query: str
    memory: str = ""
    spoken: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecallEventsConfig:
    """Knobs. Both follow-up gates default to OFF; see the module docstring.

    *follow_up_window_s* is seconds from the reply's end to the arrival, to be
    derived from the measured arrival distribution (plan ``t18``), never
    guessed. *follow_up_threshold* is the calibrated ``c52`` threshold; an
    arrival clears it at ``relevance >= threshold``.
    """

    follow_up_window_s: Optional[float] = None
    follow_up_threshold: Optional[float] = None
    max_samples: int = DEFAULT_MAX_SAMPLES


@dataclass(frozen=True)
class ReplyOutcome:
    """What :meth:`BackgroundRecall.run_turn` did with the reply."""

    turn_id: int
    spoken: tuple[str, ...] = ()
    regenerated: bool = False
    restarted: bool = False
    interrupted: bool = False
    sentences_dropped: int = 0


@dataclass(frozen=True)
class UnpromptedEvent:
    """A request for Gwen to speak with nobody having spoken to her.

    *source* is a short label (``recall``, ``job``…) restricted before it is
    counted. *key* deduplicates: at most one utterance per key. *not_after* is
    an absolute time on the seam's clock past which the event is stale.
    *still_relevant*, when given, is asked before composing. Exactly one of
    *text* (ready words) or *compose* (a callable producing them, e.g. a
    completion) supplies what to say; *compose* wins when both are given.
    """

    source: str
    key: str
    text: str = ""
    compose: Optional[Callable[[], str]] = None
    not_after: Optional[float] = None
    still_relevant: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class UnpromptedOutcome:
    """What :meth:`Unprompted.offer` did. *reason* is empty when it spoke whole."""

    spoken: bool
    reason: str = ""
    sentences_spoken: int = 0


@dataclass
class _Arrival:
    recalled: Recalled
    failed: bool
    at: Optional[float]


@dataclass
class _Turn:
    turn_id: int
    query: str
    started_at: Optional[float]
    replying: bool = True
    reply_done_at: Optional[float] = None
    pending: Optional[_Arrival] = None
    classified: bool = False
    spoken: list[str] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────


def _coerce_recalled(value: object) -> Recalled:
    """Accept a :class:`Recalled`, a bare rendered string, or anything (= empty)."""
    if isinstance(value, Recalled):
        rendered = value.rendered if isinstance(value.rendered, str) else ""
        relevance = value.relevance
        if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
            relevance = None
        elif not math.isfinite(float(relevance)):
            relevance = None
        count = value.count if isinstance(value.count, int) else 0
        return Recalled(
            rendered=rendered,
            relevance=None if relevance is None else float(relevance),
            count=count,
        )
    if isinstance(value, str):
        return Recalled(rendered=value)
    return Recalled()


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _start_thread(job: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=job, name="embodiment-recall-event", daemon=True)
    thread.start()
    return thread


# ── the unprompted-speech seam ───────────────────────────────────────────


class Unprompted:
    """The ONE entry point for speech nobody asked for. Never raises.

    *speak* takes one sentence. *floor_held* says whether someone holds the
    floor (a ``speech_started`` with no matching stop); *busy* whether Gwen is
    already speaking a reply. Utterances through one instance never overlap:
    a second offer while one is speaking is dropped as :data:`DROP_BUSY`.
    """

    def __init__(
        self,
        *,
        speak: Callable[[str], object],
        floor_held: Callable[[], bool],
        clock: Callable[[], float],
        busy: Optional[Callable[[], bool]] = None,
        record: Optional[Callable[[str, str], None]] = None,
        split: Callable[[str], list[str]] = split_sentences,
    ) -> None:
        self._speak = speak
        self._floor_held = floor_held
        self._clock = clock
        self._busy = busy
        self._record_sink = record or _no_record
        self._split = split
        self._lock = threading.Lock()
        self._speaking = threading.Lock()
        self._keys: deque[str] = deque(maxlen=_MAX_KEYS)
        self._offered = 0
        self._spoken = 0
        self._dropped: dict[str, int] = {}
        self._by_source: dict[str, int] = {}
        self._degradations: dict[str, int] = {}
        self._sink_failures = 0

    # status

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "offered": self._offered,
                "spoken": self._spoken,
                "dropped": dict(self._dropped),
                "by_source": dict(self._by_source),
                "degradations": dict(self._degradations),
                "record_sink_failures": self._sink_failures,
            }

    # the entry point

    def offer(self, event: object) -> UnpromptedOutcome:
        """Speak *event* if every gate allows it. Never raises."""
        try:
            return self._offer(event)
        except Exception as exc:  # noqa: BLE001  # last resort: the seam never raises
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return self._drop(DROP_MALFORMED)

    def _offer(self, event: object) -> UnpromptedOutcome:
        with self._lock:
            self._offered += 1
        if not isinstance(event, UnpromptedEvent) or not isinstance(event.key, str):
            return self._drop(DROP_MALFORMED)
        source = safe_reason.safe_label(event.source)
        with self._lock:
            if len(self._by_source) < 64 or source in self._by_source:
                self._by_source[source] = self._by_source.get(source, 0) + 1
            if event.key in self._keys:
                return self._drop_locked(DROP_ALREADY_SPOKEN)

        gate = self._gate(event)
        if gate:
            return self._drop(gate)
        text = self._compose(event)
        if text is None:
            return self._drop(DROP_COMPOSE_FAILED)
        if not is_speakable(text):
            # The never-silent rule does NOT apply: no fallback, no audio.
            return self._drop(DROP_UNSPEAKABLE)
        if self.floor_held():
            return self._drop(DROP_FLOOR_HELD)

        if not self._speaking.acquire(blocking=False):
            return self._drop(DROP_BUSY)
        try:
            with self._lock:
                if event.key in self._keys:
                    return self._drop_locked(DROP_ALREADY_SPOKEN)
                self._keys.append(event.key)
            return self._say(text)
        finally:
            self._speaking.release()

    def _gate(self, event: UnpromptedEvent) -> str:
        if event.not_after is not None:
            now = self._now()
            limit = _finite(event.not_after)
            if now is None or limit is None or now > limit:
                return DROP_OUTSIDE_WINDOW
        if event.still_relevant is not None:
            try:
                relevant = bool(event.still_relevant())
            except Exception as exc:  # noqa: BLE001  # a broken check means stale
                self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
                relevant = False
            if not relevant:
                return DROP_SUPERSEDED
        if self.floor_held():
            return DROP_FLOOR_HELD
        if self._is_busy():
            return DROP_BUSY
        return ""

    def _compose(self, event: UnpromptedEvent) -> Optional[str]:
        if event.compose is None:
            return event.text if isinstance(event.text, str) else ""
        try:
            text = event.compose()
        except Exception as exc:  # noqa: BLE001  # a composer fault is a dropped utterance
            self._record(RECALL_EVENTS_COMPLETE_FAILED, safe_reason.describe_exception(exc))
            return None
        return text if isinstance(text, str) else ""

    def _say(self, text: str) -> UnpromptedOutcome:
        sentences = self._split(text)
        spoken = 0
        for sentence in sentences:
            if self.floor_held():
                break
            try:
                self._speak(sentence)
            except Exception as exc:  # noqa: BLE001  # a broken voice ends the utterance
                self._record(RECALL_EVENTS_SPEAK_FAILED, safe_reason.describe_exception(exc))
                break
            spoken += 1
        if spoken == 0:
            return self._drop(DROP_SPEAK_FAILED if sentences else DROP_UNSPEAKABLE)
        with self._lock:
            self._spoken += 1
        reason = "" if spoken == len(sentences) else DROP_FLOOR_HELD
        return UnpromptedOutcome(spoken=True, reason=reason, sentences_spoken=spoken)

    # gates shared with the reply loop

    def floor_held(self) -> bool:
        """Whether someone holds the floor. A broken seam counts as HELD."""
        try:
            return bool(self._floor_held())
        except Exception as exc:  # noqa: BLE001  # never talk over someone on a broken seam
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return True

    def _is_busy(self) -> bool:
        if self._busy is None:
            return False
        try:
            return bool(self._busy())
        except Exception as exc:  # noqa: BLE001  # unknown means busy
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return True

    def _now(self) -> Optional[float]:
        try:
            return _finite(self._clock())
        except Exception as exc:  # noqa: BLE001  # no clock, no window
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return None

    # counting

    def _drop(self, reason: str) -> UnpromptedOutcome:
        with self._lock:
            return self._drop_locked(reason)

    def _drop_locked(self, reason: str) -> UnpromptedOutcome:
        self._dropped[reason] = self._dropped.get(reason, 0) + 1
        return UnpromptedOutcome(spoken=False, reason=reason)

    def _record(self, code: str, reason: str) -> None:
        with self._lock:
            self._degradations[code] = self._degradations.get(code, 0) + 1
        try:
            self._record_sink(code, reason)
        except Exception:  # noqa: BLE001  # the sink must not break a turn; it is counted
            with self._lock:
                self._sink_failures += 1


# ── the orchestrator ─────────────────────────────────────────────────────


class BackgroundRecall:
    """One spoken turn with recall in the background. Never raises.

    Seams, all injected (``t16`` binds them to the daemon):

    * ``recall(query) -> Recalled`` — runs on the spawned job, off the turn.
    * ``complete(CompletionRequest) -> str`` — one non-streaming completion.
    * ``speak(sentence)`` — hands ONE sentence to the voice; returns when it is
      queued.
    * ``stop_playback()`` — the barge-in path; a restart reuses it.
    * ``floor_held() -> bool`` — someone is speaking (``speech_started``).
    * ``clock() -> float`` — monotonic seconds.
    * ``contradicts(memory, spoken) -> bool`` — optional; absent, no restart.
    * ``record(code, reason)`` — the degradation sink (C3).
    * ``spawn(job)`` — starts the recall job; default a daemon thread. Tests
      inject a holder so they choose the exact moment a recall lands.
    * ``unprompted`` — the shared :class:`Unprompted` seam; built over the same
      speak/floor/clock when absent.
    """

    def __init__(
        self,
        *,
        recall: Callable[[str], object],
        complete: Callable[[CompletionRequest], str],
        speak: Callable[[str], object],
        stop_playback: Callable[[], object],
        floor_held: Callable[[], bool],
        clock: Callable[[], float],
        config: Optional[RecallEventsConfig] = None,
        contradicts: Optional[Callable[[str, tuple[str, ...]], bool]] = None,
        record: Optional[Callable[[str, str], None]] = None,
        spawn: Optional[Callable[[Callable[[], None]], object]] = None,
        split: Callable[[str], list[str]] = split_sentences,
        unprompted: Optional[Unprompted] = None,
    ) -> None:
        self._recall = recall
        self._complete = complete
        self._speak = speak
        self._stop_playback = stop_playback
        self._floor_held = floor_held
        self._clock = clock
        self._config = config if isinstance(config, RecallEventsConfig) else RecallEventsConfig()
        self._contradicts = contradicts
        self._record_sink = record or _no_record
        self._spawn = spawn
        self._split = split
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._closed = False
        self._turn_serial = 0
        self._latest_turn = 0
        self._replying = 0
        self._pending = 0
        self._landed = {name: 0 for name in _LANDINGS}
        self._dropped_reasons: dict[str, int] = {}
        self._regenerations = 0
        self._restarts = 0
        self._sentences_dropped = 0
        self._arrival_n = 0
        self._samples: deque[dict[str, Any]] = deque(maxlen=max(1, int(self._config.max_samples)))
        self._degradations: dict[str, int] = {}
        self._sink_failures = 0
        self._unprompted = unprompted or Unprompted(
            speak=lambda sentence: self._speak(sentence),
            floor_held=lambda: self._floor_held(),
            clock=lambda: self._clock(),
            busy=self._reply_in_flight,
            record=self._record,
            split=split,
        )

    # ── public surface ───────────────────────────────────────────────────

    @property
    def unprompted(self) -> Unprompted:
        """The shared unprompted-speech seam, for non-recall callers (#89, #90)."""
        return self._unprompted

    def run_turn(self, query: object) -> ReplyOutcome:
        """Recall in the background, reply now, use the memory when it lands."""
        try:
            return self._run_turn(query)
        except Exception as exc:  # noqa: BLE001  # last resort: the daemon survives a turn
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return ReplyOutcome(turn_id=0)

    def status(self) -> dict[str, Any]:
        """Counters, the arrival distribution and the config in effect. JSON-shaped."""
        with self._lock:
            block: dict[str, Any] = {
                "arrivals": dict(self._landed),
                "dropped_reasons": dict(self._dropped_reasons),
                "pending": self._pending,
                "regenerations": self._regenerations,
                "restarts": self._restarts,
                "sentences_dropped": self._sentences_dropped,
                "arrival_n": self._arrival_n,
                "arrival_samples": [dict(sample) for sample in self._samples],
                "follow_up_window_s": self._config.follow_up_window_s,
                "follow_up_threshold": self._config.follow_up_threshold,
                "degradations": dict(self._degradations),
                "record_sink_failures": self._sink_failures,
            }
        block["unprompted"] = self._unprompted.status()
        return block

    def close(self) -> None:
        """Stop taking turns; any later arrival is dropped as :data:`DROP_CLOSED`."""
        with self._lock:
            self._closed = True

    def wait(self, timeout: float) -> bool:
        """Join outstanding recall jobs within *timeout* seconds. True when none remain.

        Only jobs started by the default spawner can be joined; the bound is the
        caller's (``t16`` derives it from its close deadline).
        """
        budget = _finite(timeout)
        if budget is None or budget < 0:
            budget = 0.0
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=budget / max(1, len(threads)))
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            return not self._threads and self._pending == 0

    # ── the reply ────────────────────────────────────────────────────────

    def _run_turn(self, query: object) -> ReplyOutcome:
        with self._lock:
            closed = self._closed
            if not closed:
                self._turn_serial += 1
                self._latest_turn = self._turn_serial
                self._replying += 1
            turn_id = self._turn_serial
        if closed:
            self._record(RECALL_EVENTS_CLOSED, "run_turn after close")
            return ReplyOutcome(turn_id=0)

        text = query if isinstance(query, str) else ""
        turn = _Turn(turn_id=turn_id, query=text, started_at=self._now())
        try:
            if text.strip():
                self._start_recall(turn)
            return self._reply(turn)
        finally:
            late = self._finish(turn)
            if late is not None:
                self._late(turn, late)

    def _reply(self, turn: _Turn) -> ReplyOutcome:
        first = self._complete_text(CompletionRequest(PURPOSE_REPLY, turn.query))
        if first is None:
            first = FALLBACK_TEXT  # the reply keeps the never-silent rule
        remaining = self._split(first) or [FALLBACK_TEXT]
        regenerated = restarted = interrupted = False
        dropped = 0
        while remaining:
            if self._unprompted.floor_held():
                interrupted = True
                break
            arrival, landed = self._take(turn)
            if arrival is not None:
                remaining, used, restart, cut = self._use(turn, arrival, landed, remaining)
                regenerated, restarted = regenerated or used, restarted or restart
                dropped += cut
                continue
            sentence = remaining.pop(0)
            if not self._say(sentence):
                break
            turn.spoken.append(sentence)
        return ReplyOutcome(
            turn_id=turn.turn_id,
            spoken=tuple(turn.spoken),
            regenerated=regenerated,
            restarted=restarted,
            interrupted=interrupted,
            sentences_dropped=dropped,
        )

    def _use(
        self, turn: _Turn, arrival: _Arrival, landed: str, remaining: list[str]
    ) -> tuple[list[str], bool, bool, int]:
        """Apply an arrival at a sentence boundary: (remaining, regenerated, restarted, cut)."""
        memory = arrival.recalled.rendered
        spoken = tuple(turn.spoken)
        if landed == LANDED_DURING_REPLY and self._judges_inconsistent(memory, spoken):
            if not self._unprompted.floor_held():
                return self._restart(turn, memory, spoken, remaining)
            return [], False, False, 0  # the floor is held: barge-in, not restart

        purpose = PURPOSE_REPLY if not spoken else PURPOSE_REMAINDER
        text = self._complete_text(CompletionRequest(purpose, turn.query, memory, spoken))
        if text is None:
            return remaining, False, False, 0  # degrade to the first completion
        cut = len(remaining)
        with self._lock:
            self._regenerations += 1
            self._sentences_dropped += cut
        return self._split(text), True, False, cut

    def _restart(
        self, turn: _Turn, memory: str, spoken: tuple[str, ...], remaining: list[str]
    ) -> tuple[list[str], bool, bool, int]:
        try:
            self._stop_playback()
        except Exception as exc:  # noqa: BLE001  # a broken stop still restarts the words
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
        cut = len(remaining)
        with self._lock:
            self._restarts += 1
            self._sentences_dropped += cut
        self._record(
            RECALL_EVENTS_RESTARTED,
            f"memory inconsistent with {len(spoken)} spoken sentence(s); playback stopped, "
            f"{cut} unspoken sentence(s) dropped, reply regenerated",
        )
        text = self._complete_text(CompletionRequest(PURPOSE_RESTART, turn.query, memory, spoken))
        if text is None:
            return [], False, True, cut
        return self._split(text), False, True, cut

    def _judges_inconsistent(self, memory: str, spoken: tuple[str, ...]) -> bool:
        if self._contradicts is None or not spoken:
            return False
        try:
            return bool(self._contradicts(memory, spoken))
        except Exception as exc:  # noqa: BLE001  # an unsure judge never stops Gwen
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return False

    def _complete_text(self, request: CompletionRequest) -> Optional[str]:
        """One completion, or ``None`` (recorded) when it raised or is unspeakable."""
        try:
            text = self._complete(request)
        except Exception as exc:  # noqa: BLE001  # the model is a seam
            self._record(RECALL_EVENTS_COMPLETE_FAILED, safe_reason.describe_exception(exc))
            return None
        if not isinstance(text, str) or not is_speakable(text):
            self._record(
                RECALL_EVENTS_COMPLETE_FAILED,
                f"{request.purpose} completion carried no letter or number",
            )
            return None
        return text

    def _say(self, sentence: str) -> bool:
        try:
            self._speak(sentence)
        except Exception as exc:  # noqa: BLE001  # a broken voice ends this reply
            self._record(RECALL_EVENTS_SPEAK_FAILED, safe_reason.describe_exception(exc))
            return False
        return True

    # ── arrivals ─────────────────────────────────────────────────────────

    def _start_recall(self, turn: _Turn) -> None:
        with self._lock:
            self._pending += 1

        def job() -> None:
            self._recall_job(turn)

        try:
            if self._spawn is None:
                thread = _start_thread(job)
                with self._lock:
                    self._threads = [t for t in self._threads if t.is_alive()] + [thread]
            else:
                self._spawn(job)
        except Exception as exc:  # noqa: BLE001  # no thread, no recall: the turn replies
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            arrival = _Arrival(recalled=Recalled(), failed=True, at=None)
            with self._lock:
                turn.classified = True
                self._count_locked(turn, arrival, LANDED_DROPPED, DROP_RECALL_FAILED)

    def _recall_job(self, turn: _Turn) -> None:
        """The spawned job. Never raises: the thread has nobody to raise to."""
        try:
            try:
                recalled, failed = _coerce_recalled(self._recall(turn.query)), False
            except Exception as exc:  # noqa: BLE001  # memory is a seam
                self._record(RECALL_EVENTS_RECALL_FAILED, safe_reason.describe_exception(exc))
                recalled, failed = Recalled(), True
            arrival = _Arrival(recalled=recalled, failed=failed, at=self._now())
            with self._lock:
                if turn.replying and not self._closed:
                    turn.pending = arrival
                    return
            self._late(turn, arrival)
        except Exception as exc:  # noqa: BLE001  # last resort for the thread
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))

    def _take(self, turn: _Turn) -> tuple[Optional[_Arrival], str]:
        """At a sentence boundary: claim a pending arrival and classify it, once."""
        with self._lock:
            arrival = turn.pending
            if arrival is None or turn.classified:
                return None, ""
            turn.pending = None
            turn.classified = True
            reason = self._unusable(arrival)
            if reason:
                self._count_locked(turn, arrival, LANDED_DROPPED, reason)
                return None, ""
            landed = LANDED_DURING_REPLY if turn.spoken else LANDED_BEFORE_FIRST_SENTENCE
            self._count_locked(turn, arrival, landed, "")
            return arrival, landed

    def _finish(self, turn: _Turn) -> Optional[_Arrival]:
        """The reply is over. Hand back an arrival that landed but was never taken."""
        done_at = self._now()
        with self._lock:
            turn.replying = False
            turn.reply_done_at = done_at
            self._replying = max(0, self._replying - 1)
            arrival, turn.pending = turn.pending, None
            return arrival if not turn.classified else None

    def _late(self, turn: _Turn, arrival: _Arrival) -> None:
        """An arrival after the reply: at most one follow-up, otherwise dropped."""
        with self._lock:
            if turn.classified:
                return
            turn.classified = True
            reason = DROP_CLOSED if self._closed else self._unusable(arrival)
        reason = reason or self._late_gate(turn, arrival)
        if reason:
            with self._lock:
                self._count_locked(turn, arrival, LANDED_DROPPED, reason)
            return

        window = float(self._config.follow_up_window_s or 0.0)
        memory = arrival.recalled.rendered
        spoken = tuple(turn.spoken)
        event = UnpromptedEvent(
            source="recall",
            key=f"recall-turn-{turn.turn_id}",
            compose=lambda: self._compose_follow_up(turn.query, memory, spoken),
            not_after=(turn.reply_done_at or 0.0) + window,
            still_relevant=lambda: self._is_latest(turn.turn_id),
        )
        outcome = self._unprompted.offer(event)
        with self._lock:
            if outcome.spoken:
                self._count_locked(turn, arrival, LANDED_FOLLOW_UP, "")
            else:
                self._count_locked(turn, arrival, LANDED_DROPPED, outcome.reason or "unknown")

    def _late_gate(self, turn: _Turn, arrival: _Arrival) -> str:
        threshold = _finite(self._config.follow_up_threshold)
        if threshold is None:
            return DROP_NO_THRESHOLD
        relevance = arrival.recalled.relevance
        if relevance is None or relevance < threshold:
            return DROP_BELOW_THRESHOLD
        window = _finite(self._config.follow_up_window_s)
        if window is None:
            return DROP_NO_WINDOW
        if arrival.at is None or turn.reply_done_at is None:
            return DROP_OUTSIDE_WINDOW
        if arrival.at - turn.reply_done_at > window:
            return DROP_OUTSIDE_WINDOW
        return ""

    def _compose_follow_up(self, query: str, memory: str, spoken: tuple[str, ...]) -> str:
        """The follow-up's words. A raise or unusable text is dropped by :class:`Unprompted`
        (recorded there), never replaced by a fallback."""
        return self._complete(CompletionRequest(PURPOSE_FOLLOW_UP, query, memory, spoken))

    @staticmethod
    def _unusable(arrival: _Arrival) -> str:
        if arrival.failed:
            return DROP_RECALL_FAILED
        if not arrival.recalled.rendered.strip():
            return DROP_EMPTY
        return ""

    def _is_latest(self, turn_id: int) -> bool:
        with self._lock:
            return self._latest_turn == turn_id

    def _reply_in_flight(self) -> bool:
        with self._lock:
            return self._replying > 0

    # ── counting and recording ───────────────────────────────────────────

    def _count_locked(self, turn: _Turn, arrival: _Arrival, landed: str, reason: str) -> None:
        """Exactly one landing per arrival. Caller holds the lock."""
        self._landed[landed] += 1
        self._pending = max(0, self._pending - 1)
        self._arrival_n += 1
        if landed == LANDED_DROPPED:
            self._dropped_reasons[reason] = self._dropped_reasons.get(reason, 0) + 1
        latency = after_end = None
        if arrival.at is not None and turn.started_at is not None:
            latency = round(arrival.at - turn.started_at, 6)
        if arrival.at is not None and turn.reply_done_at is not None:
            after_end = round(arrival.at - turn.reply_done_at, 6)
        self._samples.append(
            {"landed": landed, "latency_s": latency, "after_reply_end_s": after_end}
        )

    def _now(self) -> Optional[float]:
        try:
            return _finite(self._clock())
        except Exception as exc:  # noqa: BLE001  # no clock: no latency, no window
            self._record(RECALL_EVENTS_SEAM_FAILED, safe_reason.describe_exception(exc))
            return None

    def _record(self, code: str, reason: str) -> None:
        with self._lock:
            self._degradations[code] = self._degradations.get(code, 0) + 1
        try:
            self._record_sink(code, safe_reason.scrub(reason)[: safe_reason.MAX_DESCRIPTION_CHARS])
        except Exception:  # noqa: BLE001  # the sink must not break a turn; it is counted
            with self._lock:
                self._sink_failures += 1
