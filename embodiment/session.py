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
  across every session it hosts, not any one session.

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
(there is nothing smaller to keep instead) and recorded once as
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
   heuristic (NOT a model call) for Hebrew and English imperatives. It has
   real false negatives (documented in its own docstring) and the daemon may
   later swap in a model-backed one through the injectable ``ask_detector``
   seam. When it fires on :meth:`Session.add_user`, :meth:`RoomMemory.remember`
   is called exactly ONCE with the extracted text, and all three of its
   outcomes are handled and returned to the caller as an
   :class:`AskOutcome`: confirmed, deferred (NOT an error — the write is
   still running and will land), and refused (a degradation, since the text
   was definitively not stored).
2. **End-of-session summary** — :meth:`Session.close` takes an injected
   ``summarise(messages) -> str`` callable, runs it under a caller-set
   deadline, and writes exactly ONE attributed record through
   ``RoomMemory.remember`` when — and only when — it returns non-blank
   speakable text within that deadline. If ``summarise`` raises, returns
   blank, or overruns the deadline, this module writes NOTHING: a bad or
   absent summary is a worse record than no record.

No third path exists. ``tests/test_session.py``'s
``TestRememberDiscipline`` proves the call count directly with a counting
fake ``RoomMemory``, across a session with many turns and no ask, with an
ask, with a summary, and with both.

Privacy of records, reports and ``repr``
------------------------------------------
Every degradation this module records, and every field of
:class:`SessionCloseReport` and :class:`AskOutcome`, carries a CODE and a
COUNT — never a turn's text, never the extracted "thing to remember", never a
summariser's exception message beyond its type name. ``repr(session)`` is
likewise text-free. ``tests/test_session.py``'s ``TestAttack`` plants a
marker string in a turn (and in a raising summariser's message) and scans
every surface this module exposes for it.

Threading
---------
A :class:`Session` is **not** thread-safe. It is intended to be driven from
exactly one thread — the daemon's own turn loop, per the brief — and adds no
locking, because locking a single-writer object nobody asked for would only
hide a real bug (a second caller) behind an illusion of safety. The one
exception, noted where it happens: :meth:`Session.close` spawns a single
bounded worker thread to run the injected ``summarise`` callable under a
deadline (mirroring :mod:`embodiment.memory`'s own reasoning for why a
blocking call must run off the calling thread to be boundable at all), and
that worker never touches ``self`` — only the calling thread reads its
result and mutates session state.
"""

from __future__ import annotations

import re
import secrets
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
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
    "CODE_SUMMARY_ERROR",
    "CODE_SUMMARY_BLANK",
    "CODE_SUMMARY_TIMEOUT",
    "CODE_SUMMARY_NOT_PROVIDED",
    "CODE_SUMMARY_WRITE_REFUSED",
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
#: reasoning that a model-backed summariser is the same class of call.
DEFAULT_SUMMARY_DEADLINE = 10.0

#: Reused from :mod:`embodiment.memory` unless a host overrides it per call.
DEFAULT_REMEMBER_DEADLINE = DEFAULT_WRITE_DEADLINE

#: eidetic record ``type`` values this module writes.
ASK_RECORD_TYPE = "explicit-ask"
SUMMARY_RECORD_TYPE = "session-summary"

#: ``added_by`` when no resolved identity and no explicit override is given.
DEFAULT_ADDED_BY = "gwen"

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


# ── the default ask detector ──────────────────────────────────────────────────

#: A small, stated heuristic — NOT a model call. Known false negatives:
#: "please remember", "can you remember", "don't forget", any verb
#: conjugation beyond the four covered below (e.g. Hebrew plural imperatives),
#: and any ask that never uses one of these literal trigger phrases. The
#: daemon may swap in a model-backed detector of the same signature later.
_ASK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[\s,.!?])תזכר[יו]\s*ש(.+)", re.DOTALL),
    re.compile(r"(?:^|[\s,.!?])זכר[יו]\s*ש(.+)", re.DOTALL),
    re.compile(r"(?i)\bremember that\b(.+)", re.DOTALL),
)


def default_ask_detector(text: str) -> Optional[str]:
    """The thing to remember, or ``None``. A heuristic, not a model call.

    Matches a small set of Hebrew and English imperative phrasings (see the
    module-level pattern list for exactly which ones, and its docstring for
    the documented false negatives) and returns the text that follows the
    trigger, stripped of surrounding whitespace. Never raises on ordinary
    text; :meth:`Session.add_user` guards against a detector — including this
    one — raising on adversarial input.
    """
    if not text:
        return None
    for pattern in _ASK_PATTERNS:
        match = pattern.search(text)
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
    """What :meth:`Session.close` left behind. Counts and codes, never text."""

    turns_seen: int = 0
    records_written: int = 0
    summary_landed: bool = False
    summary_deferred: bool = False
    summary_skipped: bool = False
    summary_skip_reason: Optional[str] = None
    degradations: tuple[SessionDegradation, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns_seen": self.turns_seen,
            "records_written": self.records_written,
            "summary_landed": self.summary_landed,
            "summary_deferred": self.summary_deferred,
            "summary_skipped": self.summary_skipped,
            "summary_skip_reason": self.summary_skip_reason,
            "degradations": [d.to_dict() for d in self.degradations],
        }


TokenCounter = Callable[[list[dict[str, Any]]], int]
AskDetector = Callable[[str], Optional[str]]
Summariser = Callable[[list[dict[str, Any]]], str]


# ── the session itself ────────────────────────────────────────────────────────


class Session:
    """Conversational state for ONE session with Gwen. Not thread-safe.

    Args:
        state: the daemon's :class:`~embodiment.daemon.state.DaemonState`.
            :meth:`~embodiment.daemon.state.DaemonState.open_transcript` is
            called exactly once, at construction, with *session_id*.
        memory: a :class:`~embodiment.memory.RoomMemory` (or, in tests,
            anything duck-typed to its ``remember`` signature). This object
            is NOT owned by the session: :meth:`close` never closes it.
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
        self._closed = False
        self._close_report: Optional[SessionCloseReport] = None

    # -- introspection ------------------------------------------------------

    @property
    def degradations(self) -> tuple[SessionDegradation, ...]:
        return tuple(self._degradations)

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

    # -- the window -----------------------------------------------------------

    def add_user(self, text: str) -> Optional[AskOutcome]:
        """Add a user turn. Returns an :class:`AskOutcome` iff the ask detector fired.

        Writes through the transcript log unconditionally, enforces the
        window budget (oldest turns dropped first), and — as the one and
        only side effect beyond the window and the transcript — checks
        *text* for an explicit spoken "remember" ask.

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
            self._degradations.append(
                SessionDegradation(
                    CODE_TURN_EXCEEDS_BUDGET,
                    "a single turn's estimated size already exceeds the configured "
                    f"budget of {self._budget_tokens}; kept verbatim rather than "
                    "truncated",
                )
            )

    # -- the explicit ask -----------------------------------------------------

    def _check_ask(self, text: str) -> Optional[AskOutcome]:
        try:
            extracted = self._ask_detector(text)
        except Exception as exc:  # noqa: BLE001 - a hostile/buggy detector must not crash a turn
            self._degradations.append(
                SessionDegradation(
                    CODE_ASK_DETECTOR_FAILED,
                    f"ask detector raised {type(exc).__name__}; treated as no ask " "detected",
                )
            )
            return None
        if not extracted:
            return None

        result = self._memory.remember(
            extracted,
            visibility=PRIVATE,
            record_type=ASK_RECORD_TYPE,
            added_by=self._added_by if self._added_by is not None else DEFAULT_ADDED_BY,
            deadline=self._remember_deadline,
        )
        ok = bool(getattr(result, "ok", False))
        record_id = getattr(result, "record_id", None)
        degradation = getattr(result, "degradation", None)
        code = getattr(degradation, "code", None)

        if ok:
            self._records_written += 1
            return AskOutcome(detected=True, remembered=True, record_id=record_id)
        if code == CODE_REMEMBER_DEFERRED:
            return AskOutcome(detected=True, deferred=True, record_id=record_id)

        self._degradations.append(
            SessionDegradation(
                CODE_ASK_REFUSED,
                f"an explicit ask's write was refused ({code or 'unknown'})",
            )
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
        was refused.

        A second call returns the SAME report object without doing any work
        again — a different *summarise* passed to a later call is ignored,
        by design: this method commits to its first answer.
        """
        if self._closed:
            assert self._close_report is not None  # nosec B101 - invariant, not a control
            return self._close_report

        self._closed = True
        landed = False
        deferred = False
        skip_reason: Optional[str] = None

        if summarise is None:
            skip_reason = CODE_SUMMARY_NOT_PROVIDED
        else:
            summary_text, skip_reason = self._run_summariser(summarise, deadline)
            if summary_text is not None:
                landed, deferred, skip_reason = self._write_summary(summary_text)

        report = SessionCloseReport(
            turns_seen=self._turns_seen,
            records_written=self._records_written,
            summary_landed=landed,
            summary_deferred=deferred,
            summary_skipped=skip_reason is not None,
            summary_skip_reason=skip_reason,
            degradations=tuple(self._degradations),
        )
        self._close_report = report
        return report

    def _run_summariser(
        self, summarise: Summariser, deadline: float
    ) -> tuple[Optional[str], Optional[str]]:
        """Run *summarise* under *deadline*. ``(text_or_None, skip_reason_or_None)``.

        The worker thread never touches ``self`` — only this method, on the
        calling thread, reads its result and (via the caller) mutates
        session state. A worker that outlives the deadline is abandoned: its
        eventual result, success or failure, is never acted on, matching the
        brief's "write NOTHING rather than a bad summary" rule exactly —
        including the case where it would have succeeded one second late.
        """
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embodiment-session-close")
        try:
            future = executor.submit(summarise, self.messages())
        except Exception as exc:  # noqa: BLE001 - a dead executor is a degradation, not a crash
            self._degradations.append(
                SessionDegradation(
                    CODE_SUMMARY_ERROR, f"could not submit summarise: {type(exc).__name__}"
                )
            )
            executor.shutdown(wait=False)
            return None, CODE_SUMMARY_ERROR

        try:
            result = future.result(timeout=max(0.0, float(deadline)))
        except FutureTimeoutError:
            self._degradations.append(
                SessionDegradation(
                    CODE_SUMMARY_TIMEOUT,
                    f"summarise did not return within {deadline}s; abandoned and " "not written",
                )
            )
            return None, CODE_SUMMARY_TIMEOUT
        except Exception as exc:  # noqa: BLE001 - the injected summariser is untrusted
            self._degradations.append(
                SessionDegradation(CODE_SUMMARY_ERROR, f"summarise raised {type(exc).__name__}")
            )
            return None, CODE_SUMMARY_ERROR
        finally:
            executor.shutdown(wait=False)

        if not isinstance(result, str) or not is_speakable(result):
            self._degradations.append(
                SessionDegradation(
                    CODE_SUMMARY_BLANK,
                    "summarise returned nothing with a letter or number; not written",
                )
            )
            return None, CODE_SUMMARY_BLANK
        return result, None

    def _write_summary(self, summary_text: str) -> tuple[bool, bool, Optional[str]]:
        """``(landed, deferred, skip_reason)`` — the ONE end-of-session write."""
        result = self._memory.remember(
            summary_text,
            visibility=PRIVATE,
            record_type=SUMMARY_RECORD_TYPE,
            added_by=self._added_by if self._added_by is not None else DEFAULT_ADDED_BY,
            deadline=self._remember_deadline,
            metadata={"turns_seen": self._turns_seen},
        )
        ok = bool(getattr(result, "ok", False))
        degradation = getattr(result, "degradation", None)
        code = getattr(degradation, "code", None)

        if ok:
            self._records_written += 1
            return True, False, None
        if code == CODE_REMEMBER_DEFERRED:
            return False, True, None

        self._degradations.append(
            SessionDegradation(
                CODE_SUMMARY_WRITE_REFUSED,
                f"the end-of-session summary was refused ({code or 'unknown'})",
            )
        )
        return False, False, CODE_SUMMARY_WRITE_REFUSED
