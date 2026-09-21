"""embodiment.session — one session's conversational state.

Task t11 of the ``realtime-embodiment-app`` plan. This module is the thing a
background voice daemon holds between spoken turns for ONE session with Gwen:
a rolling window of turns under a token budget, that session's private
transcript log, and the two ways this daemon remembers in v1 — an explicit
spoken ask, and one attributed summary written once at session end.

Built on two merged primitives, and this module owns neither of them
---------------------------------------------------------------------
* :meth:`embodiment.daemon.state.DaemonState.open_transcript` (task t4) gives
  a private, 0600, size-bounded, per-session JSONL log that rejects an unsafe
  session id rather than trusting it as a filename. This module calls it
  exactly once, at construction, and writes every turn through the returned
  :class:`~embodiment.daemon.state.TranscriptLog` — never anything else.
* :class:`embodiment.memory.RoomMemory` (task t10) is the deadline-bounded,
  private-by-default write/recall seam to eidetic. This module never
  constructs one and never closes one: :meth:`Session.close` explicitly does
  NOT call ``RoomMemory.close`` — the daemon owns that object's lifecycle
  across every session it hosts, not any one session. ``memory`` is
  duck-typed (a real ``RoomMemory`` or a test double) and is treated as
  UNTRUSTED: every call to ``memory.remember`` is guarded, because
  ``RoomMemory`` itself promises never to raise but this module's own
  contract — "never raises" — must hold even when handed something that
  breaks that promise (round 2 finding: an unguarded call let a raising
  ``remember`` propagate out of both ``add_user`` and ``close``).

Session ids are daemon-generated, never client-supplied
---------------------------------------------------------
:func:`generate_session_id` uses :func:`secrets.token_hex`. t4's module
docstring is explicit that a valid session id is used **verbatim** as a
filename, so this module never accepts an id from a network client directly —
the daemon generates one (or a test supplies a fixed one) and hands it here.
Even so, an id reaching this module is not assumed safe a second time:
:meth:`Session.__repr__` and every report field that carries the id restrict
it to the same conservative charset :mod:`embodiment.daemon.state` accepts,
falling back to a hash, because a session id handed to a *test* — or a future
caller that gets this wrong — is exactly the kind of input t4's own module
docstring calls adversarial. The filename-safety question itself is
:mod:`embodiment.daemon.state`'s job and is not repeated here.

The window: a rolling budget, never a truncation
--------------------------------------------------
:meth:`Session.add_user` / :meth:`Session.add_assistant` append a turn, write
it through the transcript log unconditionally, and then enforce the token
budget by dropping the OLDEST turns first — never by rewriting or truncating
a kept turn's text. That is the verbatim invariant applied to the window
(criterion 1): a turn that survives the drop is byte-identical to what the
caller passed in. A single turn whose own size already exceeds the whole
budget is a case this module decides rather than crashes on: it is kept
(there is nothing smaller to keep instead) and recorded as
:data:`CODE_TURN_EXCEEDS_BUDGET` — a degradation, not an error, per
constraint C3.

The default counter, and its honestly-stated error
------------------------------------------------------
:func:`embodiment.context.count_tokens_chars` is reused rather than
reinvented — it is the same chars/4 heuristic the rest of this package's loop
already budgets against, with the same known bias: a byte-level BPE tokenizer
typically spends MORE tokens per character on Hebrew (and other non-Latin
scripts) than on English, because Hebrew's UTF-8 encoding is 2 bytes/character
against Latin's 1, and most tokenizer vocabularies were built mostly from
Latin-script text. A chars/4 estimate therefore UNDER-counts a Hebrew turn's
true token cost relative to an English one of the same character length — the
window may hold more Hebrew text than its real token budget allows before an
engine's own hard limit is hit. This module does not correct for that; a host
that needs an exact bound passes its own *count_tokens* (the engine's
``/tokenize`` endpoint, for instance) through the injectable seam.

Remembering: two write paths, and only two
----------------------------------------------
1. **Explicit ask** — :func:`default_ask_detector` is a small, stated
   heuristic (NOT a model call) for Hebrew and English IMPERATIVES only. Round
   2 measured it firing on plain speech about remembering — "I don't remember
   that he ever called me back", "do you remember that film we saw" — because
   the earlier version matched the trigger phrase anywhere in the text. It now
   requires the trigger to open the utterance or a clause (after an optional
   address like "גוון," / "Gwen,"), refuses a clause opening with a negation
   or question word ("don't", "do you", "did you", "I", "לא", "האם", "את"),
   and refuses outright when the whole utterance ends in "?" — a question is
   never an ask, however it is phrased. This trades toward MORE false
   negatives on purpose: a missed ask costs the user a repeat; a false
   positive silently stores speech nobody asked to keep. Documented false
   negatives (see the detector's own docstring) grew accordingly. When it
   fires on :meth:`Session.add_user`, ``memory.remember`` is called exactly
   ONCE with the extracted text, and all three of its outcomes are handled
   and returned to the caller as an :class:`AskOutcome`: confirmed, deferred
   (NOT an error — the write is still running and will land), and refused (a
   degradation, since the text was definitively not stored — including when
   ``memory.remember`` itself raised).
2. **End-of-session summary** — :meth:`Session.close` takes an injected
   ``summarise(messages) -> str`` callable, runs it under a caller-set
   deadline, and writes exactly ONE attributed record through
   ``memory.remember`` when — and only when — it returns non-blank speakable
   text within that deadline. If ``summarise`` raises, returns blank,
   overruns the deadline, or ``memory.remember`` itself raises, this module
   writes NOTHING: a bad or absent summary is a worse record than no record.
   **The summary only ever covers the current WINDOW**, never the whole
   session — the window is whatever fits the token budget at close time, and
   old turns may already have been dropped. The written record's own
   ``metadata`` therefore carries both ``turns_seen`` (the session's whole
   life) and ``turns_summarised`` (what the window held when ``summarise``
   was actually called), and :class:`SessionCloseReport` carries both too, so
   neither a downstream reader nor the record itself can be misread as "a
   summary of the whole session" when it is not.

No third path exists. ``tests/test_session.py``'s
``TestRememberDiscipline`` proves the call count directly with a counting
fake ``RoomMemory``, across a session with many turns and no ask, with an
ask, with a summary, and with both.

Degradations are bounded, deduped, and counted
-----------------------------------------------
Round 2 measured 20,000 essentially-identical
:data:`CODE_TURN_EXCEEDS_BUDGET` records from 20,000 oversized turns — an
unbounded list in a daemon session that can run for days. Every degradation
now goes through :meth:`Session._record_degradation`, which keeps at most ONE
representative :class:`SessionDegradation` per distinct code (the first
occurrence — later ones are noise, not new information) while still counting
every occurrence on :attr:`Session.degradation_counts`. The representative
list itself is additionally capped at :data:`_MAX_DEGRADATIONS` entries as a
defensive bound against a runaway *new* code (this module's own vocabulary is
small and fixed, so that cap should never bind in practice); anything that
would exceed it is counted on :attr:`Session.degradations_dropped` rather than
silently discarded — constraint C3 applies to this module's own bookkeeping,
not only to the seams below it.

``close()`` never raises, and never gets stuck without a report
------------------------------------------------------------------
Round 2 found two related defects: a raising ``memory.remember`` propagated
out of :meth:`Session.close`, and — because ``_closed`` was set ``True``
before the work ran and ``_close_report`` was only assigned at the very end —
a SECOND call after that first failure hit an assertion instead of returning
gracefully. Both are fixed the same way: the body of ``close()`` that can
fail runs inside a ``try`` whose ``except`` always produces a minimal, valid
report, so ``_close_report`` is unconditionally assigned before ``close()``
returns on every path, and a stored report is what a repeat call returns —
never re-derived, never re-attempted, and never assumed present without being
checked first.

Privacy of records, reports and ``repr``
------------------------------------------
Every degradation this module records, and every field of
:class:`SessionCloseReport` and :class:`AskOutcome`, carries a CODE and a
COUNT — never a turn's text, never the extracted "thing to remember", never a
summariser's or a memory seam's exception message beyond its type name.
``repr(session)`` is likewise text-free. ``tests/test_session.py``'s
``TestAttack`` plants a marker string in a turn (and in a raising
summariser's and a raising memory's exception message) and scans every
surface this module exposes for it.

Threading
---------
A :class:`Session` is **not** thread-safe. It is intended to be driven from
exactly one thread — the daemon's own turn loop, per the brief — and adds no
locking, because locking a single-writer object nobody asked for would only
hide a real bug (a second caller) behind an illusion of safety.

The one exception, noted where it happens: :meth:`Session.close` runs the
injected ``summarise`` callable on a **daemon** ``threading.Thread``, not a
``ThreadPoolExecutor``. Round 2 measured why the distinction matters in a
child process (``tests/test_session.py``'s
``test_hung_summariser_does_not_block_process_exit``): a
``ThreadPoolExecutor``'s worker threads are plain, non-daemon threads, and
``concurrent.futures`` registers an ``atexit`` hook that joins EVERY worker
thread it has ever created — regardless of ``shutdown(wait=False)`` — so a
summariser abandoned past its deadline left the whole process unable to exit
on its own; the probe script hit a 6s external timeout waiting for a process
whose own ``main()`` had already returned and printed its last line. A daemon
thread carries no such promise: the interpreter does not wait for it. The
worker thread never touches ``self`` — only the calling thread reads the
shared result box, once, after the wait — so no lock is needed even though a
timed-out worker keeps running in the background.
"""

from __future__ import annotations

import re
import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment.context import count_tokens_chars
from embodiment.daemon.state import DaemonState, TranscriptLog
from embodiment.memory import DEFAULT_WRITE_DEADLINE, PRIVATE
from embodiment.turn import is_speakable

__all__ = [
    "ROLE_USER",
    "ROLE_ASSISTANT",
    "DEFAULT_WINDOW_BUDGET_TOKENS",
    "DEFAULT_SUMMARY_DEADLINE",
    "DEFAULT_REMEMBER_DEADLINE",
    "ASK_RECORD_TYPE",
    "SUMMARY_RECORD_TYPE",
    "DEFAULT_ADDED_BY",
    "CODE_REMEMBER_DEFERRED",
    "CODE_TURN_EXCEEDS_BUDGET",
    "CODE_ASK_DETECTOR_FAILED",
    "CODE_ASK_REFUSED",
    "CODE_ASK_MEMORY_ERROR",
    "CODE_SUMMARY_ERROR",
    "CODE_SUMMARY_BLANK",
    "CODE_SUMMARY_TIMEOUT",
    "CODE_SUMMARY_NOT_PROVIDED",
    "CODE_SUMMARY_WRITE_REFUSED",
    "CODE_SUMMARY_MEMORY_ERROR",
    "CODE_SUMMARY_INVALID_DEADLINE",
    "CODE_CLOSE_ERROR",
    "Turn",
    "SessionDegradation",
    "AskOutcome",
    "SessionCloseReport",
    "Session",
    "generate_session_id",
    "default_ask_detector",
]

# ── configuration defaults ────────────────────────────────────────────────────

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

#: Chosen, not measured — a generous conversational window under the chars/4
#: heuristic (see module docstring). A host tunes this to its own engine.
DEFAULT_WINDOW_BUDGET_TOKENS = 4000

#: Seconds :meth:`Session.close` waits for an injected ``summarise`` to
#: return. Chosen, not measured: roughly the same order as eidetic's own
#: embedder timeout (:mod:`embodiment.memory`'s module docstring), on the
#: reasoning that a model-backed summariser is the same class of call. Also
#: the value substituted when a caller passes an unusable *deadline* (see
#: :func:`_sanitize_deadline`).
DEFAULT_SUMMARY_DEADLINE = 10.0

#: Reused from :mod:`embodiment.memory` unless a host overrides it per call.
DEFAULT_REMEMBER_DEADLINE = DEFAULT_WRITE_DEADLINE

#: eidetic record ``type`` values this module writes.
ASK_RECORD_TYPE = "explicit-ask"
SUMMARY_RECORD_TYPE = "session-summary"

#: ``added_by`` when no resolved identity and no explicit override is given.
DEFAULT_ADDED_BY = "gwen"

#: How many DISTINCT degradation codes keep a representative record. This
#: module's own vocabulary is small and fixed (roughly a dozen codes), so
#: this cap is a defensive bound against a future runaway code, not a limit
#: expected to bind in normal operation. See "Degradations are bounded,
#: deduped, and counted" in the module docstring.
_MAX_DEGRADATIONS = 64

# ── the degradation vocabulary (C3) ───────────────────────────────────────────

#: Re-exported for convenience so a caller branching on it need not import
#: :mod:`embodiment.memory` separately.
CODE_REMEMBER_DEFERRED = "remember-deferred"

#: A single turn's own token estimate already exceeds the configured budget.
#: It is kept anyway — nothing smaller exists to keep instead — and this
#: names the fact rather than crashing or silently truncating it.
CODE_TURN_EXCEEDS_BUDGET = "session-turn-exceeds-budget"
#: The injected (or default) ask detector raised. Treated as "no ask
#: detected", not as a fatal error — a hostile or buggy detector must not
#: take a spoken turn down.
CODE_ASK_DETECTOR_FAILED = "session-ask-detector-failed"
#: An explicit ask's write was definitively refused (not deferred).
CODE_ASK_REFUSED = "session-ask-refused"
#: ``memory.remember`` itself raised while writing an explicit ask.
#: ``RoomMemory`` promises never to raise, but ``memory`` is duck-typed and
#: this module's own "never raises" contract must hold regardless.
CODE_ASK_MEMORY_ERROR = "session-ask-memory-error"
#: The injected ``summarise`` raised.
CODE_SUMMARY_ERROR = "session-summary-error"
#: ``summarise`` returned nothing speakable (blank, whitespace-only, or only
#: punctuation/format characters — see :func:`embodiment.turn.is_speakable`).
CODE_SUMMARY_BLANK = "session-summary-blank"
#: ``summarise`` did not return within the caller's deadline.
CODE_SUMMARY_TIMEOUT = "session-summary-timeout"
#: :meth:`Session.close` was called with no ``summarise`` at all.
CODE_SUMMARY_NOT_PROVIDED = "session-summary-not-provided"
#: The summary's write was definitively refused (not deferred).
CODE_SUMMARY_WRITE_REFUSED = "session-summary-write-refused"
#: ``memory.remember`` itself raised while writing the end-of-session summary.
CODE_SUMMARY_MEMORY_ERROR = "session-summary-memory-error"
#: ``close(deadline=...)`` was not a usable non-negative finite number; a
#: safe default was substituted rather than raising or waiting forever.
CODE_SUMMARY_INVALID_DEADLINE = "session-summary-invalid-deadline"
#: Something inside ``close()`` raised that none of the guards above already
#: caught. Should be unreachable; recorded rather than trusted to be.
CODE_CLOSE_ERROR = "session-close-error"

_MAX_REASON_LEN = 200

#: Session ids are shown in a report/``repr`` only when they already match
#: this conservative charset — the same one :mod:`embodiment.daemon.state`
#: accepts as a filename stem. Anything else is reported as a hash, never
#: verbatim: an id reaching this module is not assumed to already be safe.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


def generate_session_id() -> str:
    """A fresh, daemon-generated session id. Never derived from client input."""
    return secrets.token_hex(16)


def _safe_id_for_report(session_id: str) -> str:
    """*session_id*, verbatim if it is already filename-safe, else a hash."""
    if _SAFE_ID_RE.match(session_id):
        return session_id
    import hashlib

    return (
        "unsafe-"
        + hashlib.sha256(session_id.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]
    )


def _sanitize_deadline(deadline: Any) -> tuple[float, bool]:
    """``(usable_deadline, was_invalid)``. Never raises, never waits forever.

    A caller-supplied deadline is untrusted input: it may be the wrong type,
    ``NaN``, negative, or infinite. Any of those is replaced with
    :data:`DEFAULT_SUMMARY_DEADLINE` (a finite, sane wait) rather than
    propagating a ``ValueError``/``TypeError`` or handing a background wait a
    value that never returns.
    """
    try:
        value = float(deadline)
    except (TypeError, ValueError):
        return DEFAULT_SUMMARY_DEADLINE, True
    if value != value or value < 0 or value == float("inf"):  # value != value is the NaN check
        return DEFAULT_SUMMARY_DEADLINE, True
    return value, False


# ── the default ask detector ──────────────────────────────────────────────────

#: A small, stated heuristic — NOT a model call. Documented false negatives:
#: "please remember", "can you remember", "don't forget", any verb
#: conjugation beyond the four covered below (e.g. Hebrew plural imperatives),
#: an ask embedded mid-clause with no recognizable clause boundary before it
#: ("well anyway remember that milk" — one clause, trigger not at its start),
#: and — by design, per round 2 — ANY utterance that ends in "?", even one
#: that also contains a genuine imperative earlier ("Remember that? Are you
#: sure?" reads as a question and is never treated as an ask). The daemon may
#: swap in a model-backed detector of the same signature later.
_ASK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^תזכר[יו]\s*ש(.+)", re.DOTALL),
    re.compile(r"^זכר[יו]\s*ש(.+)", re.DOTALL),
    re.compile(r"(?i)^remember that\b(.*)", re.DOTALL),
)

#: A clause opening with any of these is a negation or a question frame, never
#: an imperative — checked in addition to (not instead of) requiring the
#: trigger itself to open the clause, which already rules out most of these
#: mechanically. Belt-and-suspenders, per this package's own "checked rather
#: than trusted" discipline (see e.g. ``daemon/state.py``'s session-id path
#: containment check).
_NEGATION_OR_QUESTION_PREFIXES: tuple[str, ...] = (
    "don't",
    "do not",
    "doesn't",
    "does not",
    "do you",
    "did you",
    "can you",
    "could you",
    "would you",
    "will you",
    "i ",
    "i'm",
    "i've",
    "i'd",
    "לא",
    "האם",
    "את ",
    "אתה ",
)

#: An optional address token before the imperative — "גוון," / "Gwen," — is
#: stripped before the clause is checked, so "Gwen, remember that milk" and
#: "Gwen remember that milk" are both recognized.
_ADDRESS_PREFIX_RE = re.compile(r"^\s*(?:גוון|gwen)[\s,:]*", re.IGNORECASE)

#: Clause boundaries: a comma, sentence-ending punctuation, or a newline. The
#: trigger must open one of the resulting clauses — never merely appear
#: somewhere inside one — which is what keeps "I don't remember that he ever
#: called me back" and "do you remember that film we saw" from matching: the
#: clause each sits in starts with "I don't"/"do you", not with the trigger.
_CLAUSE_SPLIT_RE = re.compile(r"[,.!?;\n]+")


def default_ask_detector(text: str) -> Optional[str]:
    """The thing to remember, or ``None``. A heuristic, not a model call.

    An utterance ending in "?" is never an ask (a question, however phrased,
    is not a command). Otherwise, EVERY clause (split on the punctuation in
    :data:`_CLAUSE_SPLIT_RE`, with a leading "Gwen,"/"גוון," address stripped)
    is checked in turn, and a clause counts only when — after that stripping —
    it OPENS with one of :data:`_ASK_PATTERNS` and does not open with a
    negation or question prefix. This is deliberately biased toward false
    negatives over false positives: round 2 measured the earlier
    contains-anywhere version firing on plain speech ABOUT remembering. See
    the pattern tables above for exactly what is (and is not) covered, and
    ``tests/test_session.py``'s ``TestDefaultAskDetector`` for the full
    positive/negative table, including the three round-2 regressions.

    Never raises on ordinary text; :meth:`Session.add_user` additionally
    guards against a detector — including this one — raising on adversarial
    input.
    """
    if not text:
        return None
    if text.rstrip().endswith("?"):
        return None
    for raw_clause in _CLAUSE_SPLIT_RE.split(text):
        clause = _ADDRESS_PREFIX_RE.sub("", raw_clause).lstrip()
        if not clause:
            continue
        lowered = clause.lower()
        if any(lowered.startswith(prefix) for prefix in _NEGATION_OR_QUESTION_PREFIXES):
            continue
        for pattern in _ASK_PATTERNS:
            match = pattern.match(clause)
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    return extracted
    return None


# ── data shapes ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Turn:
    """One turn in the rolling window. Text is kept verbatim, never rewritten."""

    role: str
    text: str


@dataclass(frozen=True)
class SessionDegradation:
    """One host-visible, text-free degradation (constraint C3)."""

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class AskOutcome:
    """What happened when :meth:`Session.add_user` checked for an explicit ask.

    ``detected`` is ``False`` (and every other field ``False``/``None``) when
    the ask detector found nothing — the overwhelmingly common case. Never
    carries the extracted text or the record's own content; only its id.
    """

    detected: bool = False
    remembered: bool = False
    deferred: bool = False
    refused: bool = False
    record_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "remembered": self.remembered,
            "deferred": self.deferred,
            "refused": self.refused,
            "record_id": self.record_id,
        }


@dataclass(frozen=True)
class SessionCloseReport:
    """What :meth:`Session.close` left behind. Counts and codes, never text.

    ``turns_summarised`` is the size of the WINDOW ``summarise`` was actually
    shown (whenever it was invoked at all) — never the whole session's
    ``turns_seen``. The two can differ a great deal once the window has
    dropped old turns; see the module docstring's "End-of-session summary"
    section.
    """

    turns_seen: int = 0
    records_written: int = 0
    summary_landed: bool = False
    summary_deferred: bool = False
    summary_skipped: bool = False
    summary_skip_reason: Optional[str] = None
    turns_summarised: int = 0
    degradations: tuple[SessionDegradation, ...] = field(default_factory=tuple)
    degradation_counts: dict[str, int] = field(default_factory=dict)
    degradations_dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns_seen": self.turns_seen,
            "records_written": self.records_written,
            "summary_landed": self.summary_landed,
            "summary_deferred": self.summary_deferred,
            "summary_skipped": self.summary_skipped,
            "summary_skip_reason": self.summary_skip_reason,
            "turns_summarised": self.turns_summarised,
            "degradations": [d.to_dict() for d in self.degradations],
            "degradation_counts": dict(self.degradation_counts),
            "degradations_dropped": self.degradations_dropped,
        }


TokenCounter = Callable[[list[dict[str, Any]]], int]
AskDetector = Callable[[str], Optional[str]]
Summariser = Callable[[list[dict[str, Any]]], str]


class _SummaryResultBox:
    """Where a daemon summariser thread leaves its result.

    Written once by the worker thread, read once by the calling thread after
    :attr:`done` is set (or the deadline passes) — never touched by both at
    once, so no lock is needed.
    """

    __slots__ = ("value", "error", "done")

    def __init__(self) -> None:
        self.value: Optional[str] = None
        self.error: Optional[BaseException] = None
        self.done = threading.Event()


# ── the session itself ────────────────────────────────────────────────────────


class Session:
    """Conversational state for ONE session with Gwen. Not thread-safe.

    Args:
        state: the daemon's :class:`~embodiment.daemon.state.DaemonState`.
            :meth:`~embodiment.daemon.state.DaemonState.open_transcript` is
            called exactly once, at construction, with *session_id*.
        memory: a :class:`~embodiment.memory.RoomMemory` (or, in tests,
            anything duck-typed to its ``remember`` signature). This object
            is NOT owned by the session: :meth:`close` never closes it. Every
            call into it is guarded — see the module docstring.
        session_id: normally omitted — a fresh id is generated with
            :func:`generate_session_id`. Accepting one explicitly exists for
            tests and daemon-side resumption, never for a value taken
            directly from a network client (see the module docstring).
        budget_tokens: the window's token budget. See
            :data:`DEFAULT_WINDOW_BUDGET_TOKENS`.
        count_tokens: an injectable counter over the whole window (the same
            shape as :func:`embodiment.context.count_tokens_chars`, which is
            the default). See the module docstring for its stated bias.
        ask_detector: an injectable ``(text) -> Optional[str]``. Defaults to
            :func:`default_ask_detector`.
        added_by: attribution for every record this session writes. Defaults
            to :data:`DEFAULT_ADDED_BY` (``"gwen"``) when not given.
        transcript_max_bytes: forwarded to
            :meth:`~embodiment.daemon.state.DaemonState.open_transcript`.
        remember_deadline: forwarded to every ``memory.remember`` call this
            session makes (the explicit ask; the end-of-session write uses
            the same deadline for its own store confirmation).
    """

    def __init__(
        self,
        state: DaemonState,
        memory: Any,
        *,
        session_id: Optional[str] = None,
        budget_tokens: int = DEFAULT_WINDOW_BUDGET_TOKENS,
        count_tokens: Optional[TokenCounter] = None,
        ask_detector: Optional[AskDetector] = None,
        added_by: Optional[str] = None,
        transcript_max_bytes: Optional[int] = None,
        remember_deadline: float = DEFAULT_REMEMBER_DEADLINE,
    ) -> None:
        self.session_id = session_id if session_id is not None else generate_session_id()
        self._memory = memory
        self._budget_tokens = max(0, int(budget_tokens))
        self._count_tokens: TokenCounter = count_tokens or count_tokens_chars
        self._ask_detector: AskDetector = ask_detector or default_ask_detector
        self._added_by = added_by
        self._remember_deadline = remember_deadline

        self.transcript: TranscriptLog = state.open_transcript(
            self.session_id, max_bytes=transcript_max_bytes
        )

        self._turns: deque[Turn] = deque()
        self._turns_seen = 0
        self._records_written = 0
        self._degradations: list[SessionDegradation] = []
        self._degradation_seen_codes: set[str] = set()
        self._degradation_counts: dict[str, int] = {}
        self._degradations_dropped = 0
        self._closed = False
        self._close_report: Optional[SessionCloseReport] = None

    # -- introspection ------------------------------------------------------

    @property
    def degradations(self) -> tuple[SessionDegradation, ...]:
        """At most one representative entry per distinct code. See
        :attr:`degradation_counts` for how many times each actually fired.
        """
        return tuple(self._degradations)

    @property
    def degradation_counts(self) -> dict[str, int]:
        """How many times each degradation code has fired, ever."""
        return dict(self._degradation_counts)

    @property
    def degradations_dropped(self) -> int:
        """Distinct NEW codes beyond :data:`_MAX_DEGRADATIONS` that could not
        get a representative slot. Expected to stay ``0`` given this module's
        small, fixed vocabulary; see the module docstring.
        """
        return self._degradations_dropped

    @property
    def turns_seen(self) -> int:
        """Every turn ever added, including ones since dropped from the window."""
        return self._turns_seen

    @property
    def closed(self) -> bool:
        return self._closed

    def messages(self) -> list[dict[str, str]]:
        """What a turn should see: the current window, oldest first.

        Plain ``{"role": ..., "content": ...}`` dicts — the shape
        :func:`embodiment.context.count_tokens_chars` (and this module's own
        default counter) already reads. Text is exactly what was added;
        nothing here is ever rewritten.
        """
        return [{"role": t.role, "content": t.text} for t in self._turns]

    def window_tokens(self) -> int:
        """The current window's estimated token cost, via the injected counter."""
        return self._count_tokens(self.messages())

    def __repr__(self) -> str:
        return (
            f"Session(id={_safe_id_for_report(self.session_id)!r}, "
            f"turns_in_window={len(self._turns)}, turns_seen={self._turns_seen}, "
            f"closed={self._closed})"
        )

    # -- degradations: bounded, deduped, counted -----------------------------

    def _record_degradation(self, code: str, reason: str) -> None:
        """Record one occurrence of *code*. Keeps at most one entry per code.

        See "Degradations are bounded, deduped, and counted" in the module
        docstring. ``reason`` must never carry turn text or a seam's raw
        exception message — every call site in this module already honours
        that; this method does not itself sanitize *reason*.
        """
        self._degradation_counts[code] = self._degradation_counts.get(code, 0) + 1
        if code in self._degradation_seen_codes:
            return
        if len(self._degradations) >= _MAX_DEGRADATIONS:
            self._degradations_dropped += 1
            return
        self._degradation_seen_codes.add(code)
        self._degradations.append(SessionDegradation(code, reason[:_MAX_REASON_LEN]))

    # -- the window -----------------------------------------------------------

    def add_user(self, text: str) -> Optional[AskOutcome]:
        """Add a user turn. Returns an :class:`AskOutcome` iff the ask detector fired.

        Writes through the transcript log unconditionally, enforces the
        window budget (oldest turns dropped first), and — as the one and
        only side effect beyond the window and the transcript — checks
        *text* for an explicit spoken "remember" ask. Never raises for a
        failure in the ask detector or in ``memory.remember``; both are
        guarded (see the module docstring).

        Raises ``TypeError`` if *text* is not a ``str``. This is a caller
        contract violation, not an environment failure, and is exactly what
        keeps "no code path writes audio bytes to disk" a structural fact
        rather than a runtime check: there is no parameter here, or anywhere
        in this module's public surface, that accepts ``bytes``.
        """
        self._require_str(text)
        self._append(ROLE_USER, text)
        return self._check_ask(text)

    def add_assistant(self, text: str) -> None:
        """Add an assistant turn. Writes through the transcript; no ask check.

        Raises ``TypeError`` if *text* is not a ``str`` — see
        :meth:`add_user`.
        """
        self._require_str(text)
        self._append(ROLE_ASSISTANT, text)

    @staticmethod
    def _require_str(text: Any) -> None:
        if not isinstance(text, str):
            raise TypeError(f"turn text must be a str, got {type(text).__name__}")

    def _append(self, role: str, text: str) -> None:
        self.transcript.write(role, text)
        self._turns.append(Turn(role=role, text=text))
        self._turns_seen += 1
        self._enforce_budget()

    def _enforce_budget(self) -> None:
        """Drop oldest turns until the window fits, or exactly one remains."""
        while len(self._turns) > 1 and self.window_tokens() > self._budget_tokens:
            self._turns.popleft()
        if len(self._turns) == 1 and self.window_tokens() > self._budget_tokens:
            self._record_degradation(
                CODE_TURN_EXCEEDS_BUDGET,
                "a single turn's estimated size already exceeds the configured "
                f"budget of {self._budget_tokens}; kept verbatim rather than "
                "truncated",
            )

    # -- the explicit ask -----------------------------------------------------

    def _check_ask(self, text: str) -> Optional[AskOutcome]:
        try:
            extracted = self._ask_detector(text)
        except Exception as exc:  # noqa: BLE001 - a hostile/buggy detector must not crash a turn
            self._record_degradation(
                CODE_ASK_DETECTOR_FAILED,
                f"ask detector raised {type(exc).__name__}; treated as no ask detected",
            )
            return None
        if not extracted:
            return None

        try:
            result = self._memory.remember(
                extracted,
                visibility=PRIVATE,
                record_type=ASK_RECORD_TYPE,
                added_by=self._added_by if self._added_by is not None else DEFAULT_ADDED_BY,
                deadline=self._remember_deadline,
            )
        except Exception as exc:  # noqa: BLE001 - memory is duck-typed and untrusted; see docstring
            self._record_degradation(
                CODE_ASK_MEMORY_ERROR, f"memory.remember raised {type(exc).__name__}"
            )
            return AskOutcome(detected=True, refused=True, record_id=None)

        ok = bool(getattr(result, "ok", False))
        record_id = getattr(result, "record_id", None)
        degradation = getattr(result, "degradation", None)
        code = getattr(degradation, "code", None)

        if ok:
            self._records_written += 1
            return AskOutcome(detected=True, remembered=True, record_id=record_id)
        if code == CODE_REMEMBER_DEFERRED:
            return AskOutcome(detected=True, deferred=True, record_id=record_id)

        self._record_degradation(
            CODE_ASK_REFUSED,
            f"an explicit ask's write was refused ({code or 'unknown'})",
        )
        return AskOutcome(detected=True, refused=True, record_id=record_id)

    # -- end of session ---------------------------------------------------------

    def close(
        self,
        summarise: Optional[Summariser] = None,
        *,
        deadline: float = DEFAULT_SUMMARY_DEADLINE,
    ) -> SessionCloseReport:
        """End the session. Idempotent, never raises, does not close ``memory``.

        With no *summarise* (or one that raises, returns blank, or overruns
        *deadline*), writes NOTHING and reports why. Otherwise writes exactly
        ONE attributed :data:`SUMMARY_RECORD_TYPE` record through
        ``memory.remember`` and reports whether it landed, was deferred, or
        was refused — including when ``memory.remember`` itself raised.

        A second call returns the SAME report object without doing any work
        again — a different *summarise* passed to a later call is ignored,
        by design: this method commits to its first answer. This holds even
        if the FIRST call's own bookkeeping somehow failed: ``_closed`` and
        ``_close_report`` are set together, inside one ``try``/``except``, so
        there is no window where the session is marked closed with no report
        to show for it.
        """
        if self._closed:
            if self._close_report is not None:
                return self._close_report
            # Structurally unreachable given the assignment below always
            # running before `_closed` is read again — checked rather than
            # trusted, per this module's own discipline elsewhere.
            return self._build_report()

        self._closed = True
        try:
            self._run_close(summarise, deadline)
        except Exception as exc:  # noqa: BLE001 - close() must never raise, per its own contract
            self._record_degradation(
                CODE_CLOSE_ERROR,
                f"close() raised {type(exc).__name__}; a minimal report was recorded instead",
            )
        report = self._build_report()
        self._close_report = report
        return report

    def _run_close(self, summarise: Optional[Summariser], deadline: float) -> None:
        """The work :meth:`close` does. Sets ``self._close_*`` fields it owns.

        Split out from :meth:`close` so the try/except there covers
        everything below, including a defect in this method itself.
        """
        self._close_landed = False
        self._close_deferred = False
        self._close_skip_reason: Optional[str] = None
        self._close_turns_summarised = 0

        if summarise is None:
            self._close_skip_reason = CODE_SUMMARY_NOT_PROVIDED
            return

        window_snapshot = self.messages()
        self._close_turns_summarised = len(window_snapshot)
        summary_text, skip_reason = self._run_summariser(summarise, deadline, window_snapshot)
        self._close_skip_reason = skip_reason
        if summary_text is not None:
            landed, deferred, skip_reason = self._write_summary(
                summary_text, self._close_turns_summarised
            )
            self._close_landed = landed
            self._close_deferred = deferred
            self._close_skip_reason = skip_reason

    def _build_report(self) -> SessionCloseReport:
        """Assemble the report from whatever ``_run_close`` managed to set.

        Every field defaults safely (``getattr`` with a fallback) so a
        ``_run_close`` that failed before setting its own attributes still
        produces a valid, honest report rather than an ``AttributeError``.
        """
        return SessionCloseReport(
            turns_seen=self._turns_seen,
            records_written=self._records_written,
            summary_landed=getattr(self, "_close_landed", False),
            summary_deferred=getattr(self, "_close_deferred", False),
            summary_skipped=getattr(self, "_close_skip_reason", None) is not None,
            summary_skip_reason=getattr(self, "_close_skip_reason", None),
            turns_summarised=getattr(self, "_close_turns_summarised", 0),
            degradations=tuple(self._degradations),
            degradation_counts=dict(self._degradation_counts),
            degradations_dropped=self._degradations_dropped,
        )

    def _run_summariser(
        self, summarise: Summariser, deadline: float, messages: list[dict[str, Any]]
    ) -> tuple[Optional[str], Optional[str]]:
        """Run *summarise* on a daemon thread, bounded by *deadline*.

        ``(text_or_None, skip_reason_or_None)``. See the module docstring's
        "Threading" section for why this is a daemon ``threading.Thread``
        rather than a ``ThreadPoolExecutor``: the latter's non-daemon workers
        measurably block process exit even after ``shutdown(wait=False)``,
        which a background daemon session can never afford. A worker that
        outlives the deadline is abandoned: its eventual result, success or
        failure, is never acted on — matching the "write NOTHING rather than
        a bad summary" rule exactly, including the case where it would have
        succeeded moments late.
        """
        resolved_deadline, invalid = _sanitize_deadline(deadline)
        if invalid:
            self._record_degradation(
                CODE_SUMMARY_INVALID_DEADLINE,
                "close(deadline=...) was not a usable non-negative finite number; "
                f"substituted {resolved_deadline}s",
            )

        box = _SummaryResultBox()

        def worker() -> None:
            try:
                box.value = summarise(messages)
            except Exception as exc:  # noqa: BLE001 - the injected summariser is untrusted
                box.error = exc
            finally:
                box.done.set()

        try:
            thread = threading.Thread(target=worker, name="embodiment-session-close", daemon=True)
            thread.start()
        except Exception as exc:  # noqa: BLE001 - starting the worker must not crash close()
            self._record_degradation(
                CODE_SUMMARY_ERROR, f"could not start summariser thread: {type(exc).__name__}"
            )
            return None, CODE_SUMMARY_ERROR

        finished = box.done.wait(timeout=resolved_deadline)
        if not finished:
            self._record_degradation(
                CODE_SUMMARY_TIMEOUT,
                f"summarise did not return within {resolved_deadline}s; abandoned "
                "and not written",
            )
            return None, CODE_SUMMARY_TIMEOUT
        if box.error is not None:
            self._record_degradation(
                CODE_SUMMARY_ERROR, f"summarise raised {type(box.error).__name__}"
            )
            return None, CODE_SUMMARY_ERROR

        result = box.value
        if not isinstance(result, str) or not is_speakable(result):
            self._record_degradation(
                CODE_SUMMARY_BLANK,
                "summarise returned nothing with a letter or number; not written",
            )
            return None, CODE_SUMMARY_BLANK
        return result, None

    def _write_summary(
        self, summary_text: str, turns_summarised: int
    ) -> tuple[bool, bool, Optional[str]]:
        """``(landed, deferred, skip_reason)`` — the ONE end-of-session write."""
        try:
            result = self._memory.remember(
                summary_text,
                visibility=PRIVATE,
                record_type=SUMMARY_RECORD_TYPE,
                added_by=self._added_by if self._added_by is not None else DEFAULT_ADDED_BY,
                deadline=self._remember_deadline,
                metadata={"turns_seen": self._turns_seen, "turns_summarised": turns_summarised},
            )
        except Exception as exc:  # noqa: BLE001 - memory is duck-typed and untrusted; see docstring
            self._record_degradation(
                CODE_SUMMARY_MEMORY_ERROR, f"memory.remember raised {type(exc).__name__}"
            )
            return False, False, CODE_SUMMARY_MEMORY_ERROR

        ok = bool(getattr(result, "ok", False))
        degradation = getattr(result, "degradation", None)
        code = getattr(degradation, "code", None)

        if ok:
            self._records_written += 1
            return True, False, None
        if code == CODE_REMEMBER_DEFERRED:
            return False, True, None

        self._record_degradation(
            CODE_SUMMARY_WRITE_REFUSED,
            f"the end-of-session summary was refused ({code or 'unknown'})",
        )
        return False, False, CODE_SUMMARY_WRITE_REFUSED
