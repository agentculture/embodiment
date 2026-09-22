"""embodiment.daemon.app — the daemon: ears → perception → memory → turn → voice.

Task ``t15`` of the ``realtime-embodiment-app`` plan. Everything the earlier
waves built exists in isolation; this module is the wiring, and nothing else.
It owns no transport, no audio device, no model client and no HTTP handler —
each of those is a *seam* it is handed, so every one of them can be faked
(which is how this module is tested; the live rig is ``t21``'s job).

What runs, and on which thread
------------------------------
* **The ears thread.** One daemon thread with its own asyncio loop, running
  :meth:`connect` and then the event stream of an ears-only realtime client
  (:mod:`embodiment.realtime.client`). Measured against the real array:
  ``session.created`` at 30 ms, end-of-speech to transcript median 161 ms and
  p90 209 ms, 0 dropped frames over 1971 frames. It never sends ``response.create`` —
  this daemon runs the turn itself, because memory (and later vision and
  tools) must reach the prompt. The thread does no work beyond classifying an
  event: a transcription is *queued*, never answered inline, so a slow turn
  cannot make the ear deaf.
* **The turn thread.** One daemon thread draining that bounded queue and
  running :meth:`DaemonApp.run_turn` — perception, recall, the model, the
  voice — one turn at a time.
* **The caller's thread.** :meth:`DaemonApp.run` blocks on the host's
  ``stop_event`` and, every ``poll_interval_s``, calls :meth:`DaemonApp.pump`:
  the bus's heartbeat/broker clock plus the drain of the played-audio feature
  frames the voice has traced since the last tick.
* **The endpoint's own capture thread** calls the frame callback this module
  installs. That callback is bound to the *generation* of the ear that was
  attached when capture started, so a displaced ear's late frame is dropped
  and counted rather than mixed into the live session (see "one ear").

One ear, and the handover is said out loud
------------------------------------------
Exactly one :class:`~embodiment.audio.endpoint.AudioEndpoint` is attached at
any moment — lobes has not validated concurrent realtime sessions, so two ears
would not merely be untidy, they would be two sessions. A second
:meth:`DaemonApp.attach_ear` either **pre-empts** (detach the first, attach the
second) or is **refused**, chosen by :attr:`AppConfig.preempt_ear`, and either
way it publishes exactly ONE ``state`` event naming both ears and appends one
ledger record (:data:`APP_EAR_PREEMPTED` / :data:`APP_EAR_REFUSED`). The whole
attach runs under one lock and the previous ear is fully torn down — capture
stopped, voice closed, endpoint detached and closed — before the new one is
installed, so there is never a moment with two live capture callbacks.

Hot mic on attach, mute in the capture path
-------------------------------------------
Attaching an ear starts capture immediately (``CLAUDE.md``: "Hot mic on
``start``, always visible, always mutable") and publishes a ``mic`` event
saying so. :meth:`DaemonApp.set_mute` calls the endpoint's own ``mute``, which
each endpoint enforces *before encode* in its capture path — never in a UI —
and publishes the new state.

Reaching the dashboard from another device
-------------------------------------------
:data:`ENV_HTTP_BIND`, :data:`ENV_BIND_PUBLIC` and :data:`ENV_ALLOWED_HOSTS`
(and the ``--http-bind`` / ``--bind-public`` / ``--allowed-host`` flags that
set them) move the dashboard off loopback. One thing to know before doing it,
measured on the tailnet: **plain http on a non-localhost address cannot vouch
for the dashboard's cookie.** The event stream authenticates by cookie; the
guard vouches for a cookie only with an allow-listed ``Origin`` or
``Sec-Fetch-Site: same-origin``; a same-origin ``EventSource`` GET sends no
``Origin`` at all, and browsers send ``Sec-Fetch-*`` only to *secure contexts*
(https, or localhost). So over plain http to a tailnet address every stream
request is refused ``http-refused-cookie-without-origin`` — ten of them, in
the run that found this — and the dashboard works only on localhost or behind
TLS. Put TLS in front (``tailscale serve`` pointing at ``127.0.0.1:8823`` is
the cheapest way, and makes the page a secure context);
:meth:`DaemonApp.status` reports ``http.secure_context_required`` so a host
can see when this applies rather than discovering it as ten refusals.

Clients are information, never input
------------------------------------
:meth:`DaemonApp.attach_client` / :meth:`DaemonApp.detach_client` maintain a
count that is published as a ``clients`` event and reported by
:meth:`DaemonApp.status`. Nothing on the turn or voice path reads it: the
daemon answers whether anyone is watching or not, and attaching a client
writes nothing to disk, touches no session, and leaves ``status()`` — minus
its own ``clients`` block — byte-identical.

Degrade, never raise; and never silently
----------------------------------------
No public method here raises for an environment problem. Every fault goes
through the ONE recording path, :meth:`DaemonApp._record`: it counts the code,
appends one ledger record (crash-durable, t4) and publishes one ``degradation``
bus event. A sub-module's own fault keeps that sub-module's own code
(``voice-tts-failed``, ``recall-deadline-exceeded``, ``turn-budget-exhausted``)
because that is the name a host would look for; codes this module invents for
its own faults are prefixed ``app-``. Exception text never becomes a reason:
:func:`embodiment.safe_reason.describe_exception` is the only renderer, so no
record, log line, event or status field can carry what was said, what the model
replied, a key, or an attacker's own string.

What is NOT here
----------------
No reconnect. A dropped realtime session is recorded and the daemon keeps
running deaf until it is restarted — reconnection is a policy with its own
clocks and is not in this task. No session rotation: one
:class:`~embodiment.session.Session` per daemon run, closed at shutdown. No
live dial: :func:`http_complete`'s wire shape is this module's best reading of
an OpenAI-compatible ``/v1/chat/completions`` on the lobes gateway and is
unverified against a running one (plan task ``t21``).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from embodiment import memory as memory_module
from embodiment import safe_reason
from embodiment.audio.endpoint import SAMPLE_RATE_HZ as PLAYBACK_RATE_HZ
from embodiment.audio.endpoint import NullEndpoint
from embodiment.audio.features import FeatureExtractor
from embodiment.bus import Bus, fold_degradation
from embodiment.contract import ModelResponse
from embodiment.daemon.state import DaemonState, resolve_state_dir
from embodiment.http import guard as guard_module
from embodiment.http import server as server_module
from embodiment.memory import PRIVATE, RoomMemory, render_recalled
from embodiment.perception import perceive
from embodiment.realtime import wire
from embodiment.realtime.client import RealtimeConfig, RealtimeEars
from embodiment.session import ASK_RECORD_TYPE, Session
from embodiment.tools import ToolRegistry, bind_tools
from embodiment.turn import SYSTEM_PROMPT, TurnConfig, TurnResult
from embodiment.turn import turn as run_one_turn
from embodiment.voice import BARGE_IN_BOUND_S, Voice, VoiceConfig, http_synthesize

__all__ = [
    "APP_SOURCE",
    "CHAT_ROUTE",
    "WINDOW_HEADER",
    "MAX_REASON_CHARS",
    "MAX_DEGRADATION_CODES",
    "MAX_FEATURE_DRAIN",
    "APP_BOOTSTRAP_DEGRADED",
    "APP_CAPTURE_FAILED",
    "APP_FRAMES_NO_SESSION",
    "APP_REPLY_SECRET_SCRUBBED",
    "APP_REMEMBER_REFUSED",
    "REMEMBER_TOOL_NAME",
    "REMEMBER_TOOL_PROMPT",
    "FORGET_TOOL_NAME",
    "FORGET_TOOL_DESCRIPTION",
    "FORGET_ID_DESCRIPTION",
    "CLOCK_ZONE_NAME",
    "CLOCK_ZONE",
    "CLOCK_LINE_PREFIX",
    "CLOCK_LINE_PATTERN",
    "HEBREW_DAYS",
    "clock_line",
    "REMEMBER_FACT_MAX_CHARS",
    "REPLY_REDACTED",
    "RECENT_TURNS",
    "TurnTiming",
    "APP_EAR_TEARDOWN_TIMEOUT",
    "APP_BARGE_IN_STOP_TIMEOUT",
    "TEARDOWN_DEADLINE_S",
    "BARGE_IN_STOP_BOUND_S",
    "APP_RECALL_FAILED",
    "APP_RECALL_LEXICAL_BLIND",
    "ENDPOINT_PLAYBACK_QUIET",
    "APP_CLOSED",
    "APP_EARS_STREAM_ENDED",
    "APP_EARS_CLOSE_INCOMPLETE",
    "APP_EARS_REDIALLED",
    "APP_EAR_RATE_UNKNOWN",
    "APP_EARS_THREAD_FAILED",
    "APP_EARS_UNAVAILABLE",
    "APP_EAR_ATTACH_FAILED",
    "APP_EAR_DETACH_FAILED",
    "APP_EAR_PREEMPTED",
    "APP_EAR_REFUSED",
    "APP_FEATURES_FAILED",
    "APP_FRAME_FROM_STALE_EAR",
    "APP_HTTP_UNAVAILABLE",
    "APP_NO_ENDPOINT",
    "APP_PUBLISH_FAILED",
    "APP_SHUTDOWN_INCOMPLETE",
    "APP_STT_ERROR",
    "APP_STT_FRAME_MALFORMED",
    "TRANSCRIPT_EMPTY",
    "TRANSCRIPT_NOT_TEXT",
    "TRANSCRIPT_SPEECH",
    "APP_TRANSCRIPT_NOT_TEXT",
    "APP_TURN_FAILED",
    "APP_TURN_QUEUE_FULL",
    "AppConfig",
    "AppCloseReport",
    "EarHandover",
    "DaemonApp",
    "http_complete",
    "ENV_HTTP_BIND",
    "ENV_BIND_PUBLIC",
    "ENV_ALLOWED_HOSTS",
    "guard_host_of",
    "allowed_origins_for",
    "SUMMARY_PROMPT",
    "SUMMARY_MAX_TOKENS",
    "main",
]

#: The ``source`` every degradation this module records for itself carries.
APP_SOURCE = "app"

#: The gateway route :func:`http_complete` posts to. Unverified against a live
#: lobes instance — see the module docstring's "What is NOT here".
CHAT_ROUTE = "/v1/chat/completions"

#: The header that introduces the conversation window inside the system prompt.
#: The window is the only way prior turns can reach the model:
#: :func:`embodiment.turn.turn` takes exactly one utterance, verbatim, and the
#: verbatim invariant forbids splicing anything into it.
WINDOW_HEADER = "השיחה עד כה:"

#: Cap on any reason string this module records. A **judgement call**, matched
#: to the cap the sibling modules already use for the same field.
MAX_REASON_CHARS = 300

#: How many DISTINCT degradation codes :meth:`DaemonApp.status` keeps a count
#: for. A **judgement call**: this module's own vocabulary is 20 codes and the
#: sibling vocabularies it folds are fixed too, so 128 is generous headroom
#: while still bounding memory against a sub-module that invents codes.
MAX_DEGRADATION_CODES = 128

#: The share of the ears' shutdown slice that may be spent waiting for the
#: ears thread to publish its event loop. A **judgement call**: the loop is
#: created on the thread's first statement, so this only ever covers thread
#: start-up, and a quarter of the slice leaves three quarters for the close
#: and the join themselves.
_EARS_LOOP_WAIT_SHARE = 0.25

#: The share of the ears' shutdown slice handed to the CLIENT's own ``close``
#: — and therefore the quantity this module's wait is derived from, never the
#: other way round. A **judgement call**: half, so the join afterwards still
#: has half a slice to see the thread finish.
_EARS_CLOSE_WAIT_SHARE = 0.5

#: Where the ears step sits in the shutdown budget. Named because two places
#: must agree on it: the step itself, and the bound the ear is handed.
_EARS_STEP_FRACTION = 0.35

#: How the ``start`` verb hands its HTTP flags to the daemon CHILD: ``start``
#: re-execs a fresh interpreter, so a flag parsed in the CLI reaches
#: :func:`main` only through the environment.
ENV_HTTP_BIND = "EMBODIMENT_HTTP_BIND"
ENV_BIND_PUBLIC = "EMBODIMENT_BIND_PUBLIC"
#: Comma-separated.
ENV_ALLOWED_HOSTS = "EMBODIMENT_ALLOWED_HOSTS"

#: The system prompt for the end-of-session summary. Hebrew, because the
#: window it summarises is Hebrew, and short because the record is a memory
#: entry rather than minutes.
SUMMARY_PROMPT = (
    "סכמי בקצרה, במשפט או שניים, על מה דיברתם בשיחה הזו. "
    "כתבי רק את הסיכום, בגוף שלישי, בלי פתיח ובלי סיום."
)

#: Token ceiling for that call. A **judgement call**: a summary that needs
#: more than this is not a summary.
SUMMARY_MAX_TOKENS = 300

#: Stems that make an utterance worth reporting when the ask detector did NOT
#: fire. A **heuristic, unmeasured**: there is no live series behind this
#: list, and it exists for VISIBILITY only — nothing here writes, refuses or
#: remembers anything, and the detector in :mod:`embodiment.session` remains
#: the only thing that decides what an ask is. Kept deliberately narrow on
#: the operator's instruction: wide enough not to miss a spoken "remember",
#: narrow enough not to fire on ordinary speech.
_ASK_STEMS: tuple[str, ...] = ("תזכר", "זכר", "remember", "don't forget", "dont forget")

#: The tool the model calls to remember something, and the one sentence in
#: the system prompt that makes it do so. **Measured on the rig before it was
#: written** (deviation ``d7``): with nothing in the prompt the model called
#: the tool on 3 of 5 spoken asks, with a bland one-sentence mention also 3 of
#: 5 — and the two it missed were the operator's own words, "remember all of
#: this" and "keep what I said", which is exactly the case the tool exists
#: for. With the obligation below, including naming those phrasings and
#: forbidding a bare confirmation, it called the tool on 5 of 5 across two
#: passes with 0 of 2 false positives on ordinary questions. The extra
#: sentences are not decoration: one bland sentence measured no better than
#: saying nothing at all.
REMEMBER_TOOL_NAME = "remember"
REMEMBER_TOOL_DESCRIPTION = (
    "Store one fact the user asked to be remembered, so it can be recalled in a "
    "later conversation. Call this whenever the user asks you to remember, keep, "
    "note or not forget something — however they phrase it."
)
REMEMBER_FACT_DESCRIPTION = (
    "The fact to store, in the user's own words. When the user refers to what was "
    "just said, write out what they meant."
)
REMEMBER_TOOL_PROMPT = (
    "כשהמשתמש מבקש ממך לזכור, לשמור, לרשום או לא לשכוח משהו — בכל ניסוח, "
    "כולל «תזכרי את כל זה» או «שמרי את מה שאמרתי» — עלייך לקרוא לכלי remember "
    "עם העובדה, לפני שאת עונה. אל תאשרי שזכרת בלי לקרוא לכלי. "
    "כשהמשתמש מבקש ממך לשכוח משהו, קראי לכלי forget עם מזהה הרשומה — הוא מופיע "
    "כ־id= ברשומה שנזכרה למעלה, או חוזר מהכלי remember. "
    "לעולם אל תגידי שזכרת או ששכחת משהו אלא אם הכלי החזיר ok. "
    "אם אין רשומה מתאימה לשכוח, אמרי שלא מצאת אותה."
)

#: The tool the model calls to forget something (decision 18, deviation
#: ``d8``). Live, Gwen was asked to forget where a key was and said she had;
#: the registry held one tool, the ledger showed no ``tool-unknown``, and the
#: record was still ``active``. It archives — eidetic's own lifecycle — and
#: never deletes a byte; only Gwen's own private store; the id is
#: model-supplied and therefore validated before it goes anywhere.
#:
#: **Measured on the rig before it was committed** (single-turn asks to the
#: live ``senses`` role, the real two schemas and the real prompt, a recalled
#: record present, two passes of five per cell): a forget-ask called
#: ``forget`` with the recalled id on 10 of 10; an ordinary question called
#: no tool on 10 of 10; a forget-ask with NOTHING recalled invented no id and
#: made no "I forgot" claim on 10 of 10. That last cell is what the no-claim
#: sentence in :data:`REMEMBER_TOOL_PROMPT` buys. The claim check is a stem
#: scan of the reply, so "neither" says the reply did not claim success; it
#: does not prove the reply said "not found".
FORGET_TOOL_NAME = "forget"
FORGET_TOOL_DESCRIPTION = (
    "Forget one stored fact the user asked you to forget, by its record id. Call "
    "this whenever the user asks you to forget, drop or erase something you "
    "remembered — however they phrase it. Use the id shown as id= on the recalled "
    "record, or the id remember returned."
)
FORGET_ID_DESCRIPTION = "The record id to forget, exactly as shown (id=...)."

#: Memory's own codes for a forget that was refused as a FACT about the
#: store rather than a fault in it, mapped to the fixed tokens the tool
#: reports. Anything else is a fault and is folded with its own code.
_FORGET_REFUSAL_BY_CODE = {
    memory_module.continuity.CODE_RECORD_NOT_FOUND: "unknown-id",
    memory_module.continuity.CODE_ALREADY_ARCHIVED: "already-archived",
    memory_module.continuity.CODE_INVALID_RECORD: "bad-id",
}

#: The clock the prompt carries (decision 18). Recalled records render
#: ``recorded=<UTC ISO>``; without a "now" the model cannot relate that to
#: anything. One fixed-shape line, local to the rig's room, injected through
#: :class:`DaemonApp`'s ``now`` so a test can freeze it. It says what time it
#: is, never who is speaking, so absent-identity byte-identity is untouched.
CLOCK_ZONE_NAME = "Asia/Jerusalem"
CLOCK_LINE_PREFIX = "השעה עכשיו:"
#: Monday-first, as :meth:`datetime.weekday` counts.
HEBREW_DAYS = ("יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון")
CLOCK_LINE_PATTERN = (
    r"השעה עכשיו: \d{4}-\d{2}-\d{2} \d{2}:\d{2} \((?:" + "|".join(HEBREW_DAYS) + r")\)"
)


def _resolve_zone() -> tuple[tzinfo, bool]:
    """``(zone, available)``: the rig's zone, or UTC and ``False`` without tzdata.

    Degrade, never raise — a daemon with no timezone database still runs, and
    :meth:`DaemonApp.status` reports which clock the prompt is carrying.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(CLOCK_ZONE_NAME), True
    except Exception:  # noqa: BLE001  # a missing tzdata is a degradation, not a crash
        return timezone.utc, False


CLOCK_ZONE, CLOCK_ZONE_AVAILABLE = _resolve_zone()


def clock_line(moment: datetime) -> str:
    """The one prompt line that says what time it is, in the documented shape.

    An aware *moment* is converted to :data:`CLOCK_ZONE`; a naive one is taken
    as already local. Minutes, not seconds — the model is relating a timestamp
    to "now", not measuring latency.
    """
    local = moment.astimezone(CLOCK_ZONE) if moment.tzinfo is not None else moment
    day = HEBREW_DAYS[local.weekday()]
    return f"{CLOCK_LINE_PREFIX} {local.strftime('%Y-%m-%d %H:%M')} ({day})"


def _now_local() -> datetime:
    return datetime.now(CLOCK_ZONE)


#: The longest fact the tool will store. A **judgement call**: a spoken fact
#: is a sentence or two, and a model handing over a kilobyte has misunderstood
#: what it was asked to keep rather than found something worth keeping.
REMEMBER_FACT_MAX_CHARS = 2000

#: How many turns' timings :meth:`DaemonApp.status` keeps, so a reader can
#: compute a median and a p90 from status alone without a log pipeline. A
#: **judgement call**: twenty is a few minutes of conversation, enough for a
#: p90 to mean something and small enough that the snapshot stays a snapshot.
RECENT_TURNS = 20

#: What replaces a secret found inside a model reply. The same spelling
#: :data:`embodiment.realtime.client.REDACTED` uses, so the two surfaces read
#: alike in a log — pinned by a test rather than trusted to stay in step. It
#: is a marker a person can act on, not an empty string that would leave a
#: reply reading as though nothing had happened.
REPLY_REDACTED = "[redacted]"

#: The whole ear teardown's bound, in seconds — stopping capture, re-pointing
#: the voice, detaching and closing one endpoint. **Derived from what it
#: bounds**: those are device calls measured in tens of milliseconds, and the
#: host endpoint's own close report on this rig runs 0.09–0.16 s, so 1.0 s is
#: roughly six times the measured worst case. It is not a performance target;
#: it is the point past which a handover stops waiting for a device that is
#: not answering, because the operator asked for a different ear and holding
#: the ear lock for a dead one serves nobody.
TEARDOWN_DEADLINE_S = 1.0

#: How long :meth:`DaemonApp._barge_in` waits for the speaker to actually
#: stop, in seconds. **Derived from the bound the voice itself states**
#: (:data:`embodiment.voice.BARGE_IN_BOUND_S`) plus a small margin for the
#: hand-off to the helper thread — a waiter must not expire before the thing
#: it waits on was allowed to finish, which is the mistake the ears-close
#: clock already made once. The measured reality is far under it: t7 round 5
#: puts the endpoint's own stop at 1 ms.
BARGE_IN_STOP_MARGIN_S = 0.05
BARGE_IN_STOP_BOUND_S = BARGE_IN_BOUND_S + BARGE_IN_STOP_MARGIN_S

#: How long a silence the warm-up plays at attach, in seconds. A **judgement
#: call**: long enough that the endpoint really starts its player (and so
#: really runs its link check), short enough to be inaudible and to cost
#: nothing on the attach path. Played at the protocol's playback rate, which
#: is fixed at :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` — unlike the
#: CAPTURE rate, which is the ear's to declare and is never assumed here.
WARMUP_SILENCE_S = 0.1

#: How many late frames from a displaced ear are expected rather than wrong.
#: The handover guarantees at least one — the endpoint's capture thread can
#: already be inside a callback when its ear is retired — and a real capture
#: buffer is tens of milliseconds, so 16 frames (~0.3 s at 20 ms) is a
#: **judgement call** sized to "the thread noticed", not to any measurement.
#: Below it: counted and published. Above it: an ear that will not stop, which
#: is a fault and goes in the ledger.
_STALE_FRAME_TOLERANCE = 16

#: How much longer than the client's own bound this module waits for it. A
#: **judgement call**: enough that a client answering inside its bound always
#: wins, small enough that a client ignoring it is not waited out.
_EARS_CLOSE_GRACE_S = 0.25

#: How many traced played-audio feature frames one :meth:`DaemonApp.pump` may
#: publish. A **judgement call**: the voice's own buffer holds at most 2048
#: frames, and 256 per tick (≈10 ticks to drain a full buffer at the default
#: 0.5 s poll) bounds how long one pump can spend publishing.
MAX_FEATURE_DRAIN = 256

# ── the degradation vocabulary this module owns (C3) ─────────────────────────

#: ``main()`` could not build one of its own parts; the daemon runs degraded.
APP_BOOTSTRAP_DEGRADED = "app-bootstrap-degraded"
#: The ears client answered ``connect() is False`` — a dead or refusing gateway.
APP_EARS_UNAVAILABLE = "app-ears-unavailable"
#: The event stream ended while the daemon was still meant to be listening.
APP_EARS_STREAM_ENDED = "app-ears-stream-ended"
#: The ears thread itself failed; the daemon keeps running, deaf.
APP_EARS_THREAD_FAILED = "app-ears-thread-failed"
#: A handover changed the ear's rate, so the session was re-dialled to declare it.
APP_EARS_REDIALLED = "app-ears-redialled"
#: An endpoint could not say what rate it delivers; the wire default was declared.
APP_EAR_RATE_UNKNOWN = "app-ear-rate-unknown"
#: The CLIENT reported its own close as not graceful — its answer, not our clock.
APP_EARS_CLOSE_INCOMPLETE = "app-ears-close-incomplete"
#: The gateway sent an ``error`` event — an STT fault, said out loud by lobes.
APP_STT_ERROR = "app-stt-error"
#: A frame arrived that did not decode to a known event.
APP_STT_FRAME_MALFORMED = "app-stt-frame-malformed"
#: No audio endpoint could be built; a :class:`NullEndpoint` stands in.
APP_NO_ENDPOINT = "app-no-audio-endpoint"
#: A second ear displaced the first (``preempt_ear`` policy).
APP_EAR_PREEMPTED = "app-ear-preempted"
#: A second ear was turned away (``preempt_ear`` off).
APP_EAR_REFUSED = "app-ear-refused"
#: Attaching an ear failed; nothing is attached.
APP_EAR_ATTACH_FAILED = "app-ear-attach-failed"
#: Detaching an ear raised; it is dropped anyway.
APP_EAR_DETACH_FAILED = "app-ear-detach-failed"
#: A captured frame could not be forwarded to the ears.
APP_CAPTURE_FAILED = "app-capture-failed"
#: A captured frame arrived with no realtime session to send it to.
APP_FRAMES_NO_SESSION = "app-frames-no-session"
#: The model called ``remember`` with something that could not be stored.
APP_REMEMBER_REFUSED = "app-remember-refused"
#: The model called ``forget`` with an id that could not be archived: not an
#: id, unknown, already archived, or the store failed. Counted with a fixed
#: reason token; the id the model supplied is never in the record.
APP_FORGET_REFUSED = "app-forget-refused"
#: The wall clock the prompt line reads from raised; the turn ran without
#: the line. A prompt with no time in it is a recorded absence, not a crash.
APP_CLOCK_FAILED = "app-clock-failed"
#: A model reply or summary came back with a secret inside it and was
#: scrubbed before it reached anything that keeps or speaks text.
APP_REPLY_SECRET_SCRUBBED = "app-reply-secret-scrubbed"  # nosec B105 - a code, not a secret
#: An ear's teardown did not finish inside :data:`TEARDOWN_DEADLINE_S`. The
#: handover went ahead anyway; the endpoint is kept for a later close to retry.
APP_EAR_TEARDOWN_TIMEOUT = "app-ear-teardown-timeout"
#: The speaker did not stop inside :data:`BARGE_IN_STOP_BOUND_S`. The turn is
#: already marked superseded, so the reply will not be spoken either way.
APP_BARGE_IN_STOP_TIMEOUT = "app-barge-in-stop-timeout"

#: A recall, or the render of one, failed outright.
APP_RECALL_FAILED = "app-recall-failed"
#: The lexical index cannot tokenise the utterance's script, so a keyword
#: search over it can only ever return nothing. See
#: :meth:`DaemonApp._recall_blind_fallback`.
APP_RECALL_LEXICAL_BLIND = "app-recall-lexical-blind"

#: :mod:`embodiment.memory`'s own code for a recall that ran out of time, and
#: its exact-match mode. Imported rather than written out — unlike the host
#: endpoint's codes, memory is importable from here — and named as constants
#: only to keep the call sites readable.
MEMORY_DEADLINE_EXCEEDED = memory_module.CODE_DEADLINE_EXCEEDED
MEMORY_MODE_EXACT = "exact"

#: How many words of an utterance the blind fallback tries, longest first. A
#: **judgement call**: the longest word carries the most meaning in Hebrew
#: (prefixes attach, so «המפתח» is a whole noun phrase), and two calls fit
#: inside a 250 ms recall deadline where five would not.
_FALLBACK_TERMS = 2

#: The host endpoint's own code for a sink that is turned down or system-muted
#: (t7 round 8). Written out rather than imported: the import-graph rule
#: (t7 criterion 3) allows this module to import ``embodiment.audio.host``
#: inside :func:`main` only, so a module-level import of the constant is not
#: available here. ``tests/test_daemon_app.py`` pins the two against each
#: other, which is where the drift would otherwise hide.
ENDPOINT_PLAYBACK_QUIET = "audio-host-playback-quiet"
#: A displaced ear is STILL delivering frames well after the handover — an ear
#: that will not stop. The first few late frames are guaranteed by the handover
#: design and are only counted; this code is for the count that keeps growing.
APP_FRAME_FROM_STALE_EAR = "app-frame-from-stale-ear"
#: The feature extractor failed on captured or played audio.
APP_FEATURES_FAILED = "app-features-failed"
#: The transcript the ear delivered was not text. Still a degradation: the
#: wire says a transcript is a string, so anything else is a fault, not a
#: quiet room.
APP_TRANSCRIPT_NOT_TEXT = "app-transcript-not-text"
#: The turn queue was full; this utterance was dropped rather than queued.
APP_TURN_QUEUE_FULL = "app-turn-queue-full"
#: The turn path itself failed. The daemon keeps running.
APP_TURN_FAILED = "app-turn-failed"
#: A bus publish raised. The event is lost; the turn is not.
APP_PUBLISH_FAILED = "app-publish-failed"
#: The dashboard server could not start; the daemon runs without it.
APP_HTTP_UNAVAILABLE = "app-http-unavailable"
#: Something was still unfinished when the shutdown deadline expired.
APP_SHUTDOWN_INCOMPLETE = "app-shutdown-incomplete"
#: Something was asked of the app after it closed.
APP_CLOSED = "app-closed"

#: What one delivered transcript turned out to be. :func:`classify_transcript`
#: is the ONE place the test is written; both entry points read its answer.
TRANSCRIPT_SPEECH = "speech"
TRANSCRIPT_EMPTY = "empty"
TRANSCRIPT_NOT_TEXT = "not-text"

_SAFE_NAME_FALLBACK = "ear"


@dataclass(frozen=True)
class AppConfig:
    """Everything the daemon runs under. No secret is ever logged from here."""

    gateway_url: str = "http://localhost:8001"
    api_key: str = field(default="", repr=False)
    language: str = wire.LANGUAGE
    #: ``True``: a second ear displaces the first. ``False``: it is refused.
    preempt_ear: bool = True
    #: The recall clock, derived from the quantity it bounds: how long a spoken
    #: turn may wait for memory before answering without it.
    recall_deadline: float = 0.25
    recall_top_k: int = 5
    recall_mode: str = "keyword"
    #: How often :meth:`DaemonApp.run` pumps the bus clock and the out-features.
    poll_interval_s: float = 0.5
    #: The whole-app shutdown bound; lifecycle's watchdog is the backstop.
    shutdown_deadline: float = 5.0
    #: Utterances that may wait for the turn thread before one is dropped.
    turn_queue_size: int = 8
    #: The model seam's own bound, in seconds.
    completion_deadline: float = 60.0
    #: The role the speaker resolves by NAME on the gateway (never by model).
    role: str = "senses"
    system_prompt: str = SYSTEM_PROMPT
    memory_scope: str = "gwen"
    added_by: str = "gwen"
    session_summary_deadline: float = 5.0
    #: Whether a browser/robot ear (:class:`~embodiment.audio.remote.RemoteEndpoint`)
    #: is started by the daemon. **Always False in v1**, by the operator's
    #: decision — the field exists so the dashboard has a stable contract to
    #: read rather than a key that appears later (t18).
    realtime_ear_enabled: bool = False
    #: The URL such an ear would be reached on. ``None`` in v1, for the same
    #: reason. Never carries a secret: the install secret goes in a header,
    #: never in a URL (``embodiment.audio.remote``'s own rule).
    realtime_ws_url: Optional[str] = None
    #: Bind the memory tools — ``remember`` (``d7``) and ``forget`` (``d8``) —
    #: into the turn. On by default: the operator's decision is that
    #: remembering and forgetting are the MODEL's to call, not phrases the
    #: daemon pattern-matches. One flag for both: a Gwen who can remember but
    #: not forget is the live failure this replaced.
    remember_tool: bool = True
    http_enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = server_module.DEFAULT_PORT
    #: Required for ANY routable bind — t16's rule, enforced at the CLI's
    #: parse time and again here. Binding off loopback puts the dashboard,
    #: the event stream (which carries the transcript) and the control API on
    #: the network, with the install secret and the Host/Origin allow-list as
    #: the only things in front of them.
    bind_public: bool = False
    #: Extra hosts the guard accepts, beyond loopback — a tailnet address or
    #: name, for instance. Each is also accepted as an ``http://<host>``
    #: Origin, so the dashboard's own fetches pass the Origin check.
    allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnTiming:
    """When each stage of one turn happened. Numbers and ids; never text.

    t21 measures "end-of-speech to first reply audio", so the stages are
    recorded where they actually occur rather than reconstructed afterwards:
    ``eos_at`` on the ears' ``speech_stopped``, ``transcript_at`` on the
    transcription, ``reply_text_at`` when senses returned, ``first_audio_at``
    at the voice's first successful ``endpoint.play()``, ``spoken_done_at``
    when the reply was fully queued.

    **``spoken_done_at`` is when the audio was QUEUED, not heard.** Nothing in
    this process can observe a listener; naming it anything else would be the
    kind of claim this package exists to refuse.

    The ``*_at`` values are ``time.monotonic()`` and are therefore comparable
    only within one process run — which is why the derived milliseconds are
    computed here, beside the values they come from, and why ``eos_wall``
    carries a wall clock for anyone correlating with another machine's log.
    ``eos_at_ms`` is lobes' OWN offset from the ``speech_stopped`` event, kept
    unaltered so a disagreement between its clock and ours stays visible
    instead of being averaged away.

    ``None`` anywhere means that stage did not happen — a superseded turn
    never reaches audio, a failed one never reaches a reply — and is a
    different fact from zero.
    """

    serial: int
    eos_at: Optional[float] = None
    eos_wall: Optional[float] = None
    eos_at_ms: Optional[int] = None
    transcript_at: Optional[float] = None
    reply_text_at: Optional[float] = None
    first_audio_at: Optional[float] = None
    spoken_done_at: Optional[float] = None
    eos_to_first_audio_ms: Optional[float] = None
    transcript_to_first_audio_ms: Optional[float] = None
    superseded: bool = False
    failed: bool = False
    rendered_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "eos_at": self.eos_at,
            "eos_wall": self.eos_wall,
            "eos_at_ms": self.eos_at_ms,
            "transcript_at": self.transcript_at,
            "reply_text_at": self.reply_text_at,
            "first_audio_at": self.first_audio_at,
            "spoken_done_at": self.spoken_done_at,
            "eos_to_first_audio_ms": self.eos_to_first_audio_ms,
            "transcript_to_first_audio_ms": self.transcript_to_first_audio_ms,
            "superseded": self.superseded,
            "failed": self.failed,
            "rendered_ids": list(self.rendered_ids),
        }

    def derived(self) -> "TurnTiming":
        """The same record with the millisecond figures filled in."""
        return replace(
            self,
            eos_to_first_audio_ms=_elapsed_ms(self.eos_at, self.first_audio_at),
            transcript_to_first_audio_ms=_elapsed_ms(self.transcript_at, self.first_audio_at),
        )


@dataclass(frozen=True)
class _Heard:
    """One utterance on its way to a turn, with when its stages happened."""

    text: str
    eos_at: Optional[float] = None
    eos_wall: Optional[float] = None
    eos_at_ms: Optional[int] = None
    transcript_at: Optional[float] = None


@dataclass(frozen=True)
class EarHandover:
    """What one :meth:`DaemonApp.attach_ear` / :meth:`detach_ear` did."""

    ear: str
    attached: bool
    refused: bool = False
    preempted: bool = False
    previous: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ear": self.ear,
            "attached": self.attached,
            "refused": self.refused,
            "preempted": self.preempted,
            "previous": self.previous,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AppCloseReport:
    """What :meth:`DaemonApp.close` finished, and what it did not."""

    closed: bool
    already_closed: bool = False
    ears_stopped: bool = True
    turn_thread_stopped: bool = True
    turns_abandoned: int = 0
    voice_closed: bool = True
    endpoint_closed: bool = True
    server_stopped: bool = True
    session_closed: bool = True
    memory_closed: bool = True
    bus_closed: bool = True
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "closed": self.closed,
            "already_closed": self.already_closed,
            "ears_stopped": self.ears_stopped,
            "turn_thread_stopped": self.turn_thread_stopped,
            "turns_abandoned": self.turns_abandoned,
            "voice_closed": self.voice_closed,
            "endpoint_closed": self.endpoint_closed,
            "server_stopped": self.server_stopped,
            "session_closed": self.session_closed,
            "memory_closed": self.memory_closed,
            "bus_closed": self.bus_closed,
            "elapsed_s": round(self.elapsed_s, 3),
        }

    @property
    def unfinished(self) -> tuple[str, ...]:
        """Every part that did not finish inside the deadline."""
        pairs = (
            ("ears", self.ears_stopped),
            ("turn_thread", self.turn_thread_stopped),
            ("voice", self.voice_closed),
            ("endpoint", self.endpoint_closed),
            ("server", self.server_stopped),
            ("session", self.session_closed),
            ("memory", self.memory_closed),
            ("bus", self.bus_closed),
        )
        return tuple(name for name, done in pairs if not done)


def classify_transcript(text: object) -> str:
    """What the ear delivered: :data:`TRANSCRIPT_SPEECH`, ``EMPTY`` or ``NOT_TEXT``.

    The ONE place the test lives, read by both
    :meth:`DaemonApp.submit_transcript` and :meth:`DaemonApp.run_turn`, so the
    queued path and a direct call can never disagree about what counts as
    something to answer. Pure: it counts nothing, publishes nothing and
    records nothing.

    "Empty" is whitespace-only by ``str.strip``, which also covers the
    separators ``str.splitlines`` honours (U+0085, U+2028, U+2029) — a commit
    made of nothing but those is a quiet room, not an utterance.
    """
    if not isinstance(text, str):
        return TRANSCRIPT_NOT_TEXT
    return TRANSCRIPT_SPEECH if text.strip() else TRANSCRIPT_EMPTY


def _safe_name(value: object) -> str:
    """One sanitiser for every ear name: the ``[A-Za-z0-9._-]`` charset, capped."""
    return safe_reason.safe_label(value, fallback=_SAFE_NAME_FALLBACK)


def _as_text(value: object) -> str:
    """*value* as text: a string as it is, ``None`` as empty, anything else via ``str``."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _safe_reason_text(value: object) -> str:
    """One sanitiser for every reason this module records."""
    return safe_reason.scrub(_as_text(value))[:MAX_REASON_CHARS]


def http_complete(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict[str, Any]]] = None,
    gateway_url: str = "http://localhost:8001",
    api_key: str = "",
    role: str = "senses",
    max_tokens: int = 16000,
    deadline: float = 60.0,
) -> ModelResponse:
    """``POST /v1/chat/completions`` on the lobes gateway, resolving by ROLE name.

    The role goes in ``model`` because lobes resolves a role **by name** and
    never by parsing a model name (``CLAUDE.md``, "the realtime interface").
    The key goes only into the ``Authorization`` header, never the URL.

    Raises on any failure — network, non-2xx, malformed body — so
    :func:`embodiment.turn.turn` can fold it through its own single
    exception-to-reason site. **Unverified against a live gateway**: every
    test injects a fake seam, and the live dial is plan task ``t21``.
    """
    origin = gateway_url.rstrip("/")
    url = f"{origin}{CHAT_ROUTE}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    payload: dict[str, Any] = {
        "model": role,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(  # nosec B310 - scheme checked above
        request, timeout=deadline
    ) as response:
        raw = json.loads(response.read().decode("utf-8"))
    choices = raw.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    usage = raw.get("usage") or {}
    return ModelResponse.from_dict(
        {
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning") or message.get("reasoning_content") or "",
            "tool_calls": _wire_tool_calls(message.get("tool_calls")),
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
        }
    )


def _wire_tool_calls(raw: Any) -> list[dict[str, Any]]:
    """OpenAI-shaped ``message.tool_calls`` -> the contract's flat shape.

    The gateway sends ``{"id", "type", "function": {"name", "arguments"}}``
    with ``arguments`` as a JSON *string*; :class:`~embodiment.contract.ToolCall`
    reads ``{"id", "name", "arguments": {...}}``. Handed over unconverted, every
    call arrived with an EMPTY name and no arguments — which the registry
    records as ``tool-unknown`` — and nothing noticed while the registry was
    empty. Found by the first clip probe after ``d7`` bound a tool.

    Arguments that are not valid JSON become ``{}``: the tool then refuses on
    its own vocabulary (``remember`` says ``empty``; any other tool raises a
    ``TypeError`` for a missing argument, recorded as a failed step). Nothing
    silent, and the turn survives. A call that is not a mapping is dropped,
    as :meth:`ModelResponse.from_dict` already does.
    """
    calls: list[dict[str, Any]] = []
    for call in raw if isinstance(raw, (list, tuple)) else []:
        converted = _wire_tool_call(call)
        if converted is not None:
            calls.append(converted)
    return calls


def _wire_tool_call(call: Any) -> Optional[dict[str, Any]]:
    """One call of :func:`_wire_tool_calls`; ``None`` for a call that is not a mapping."""
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    source = function if isinstance(function, dict) else call
    return {
        "id": str(call.get("id") or ""),
        "name": str(source.get("name") or ""),
        "arguments": _wire_tool_arguments(source.get("arguments")),
    }


def _wire_tool_arguments(arguments: Any) -> dict[str, Any]:
    """The call's ``arguments`` as a mapping: a JSON string is parsed, anything else is ``{}``."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return arguments


class DaemonApp:
    """The wiring: one ear, one turn at a time, everything published."""

    def __init__(
        self,
        *,
        state: DaemonState,
        bus: Any,
        memory: Any,
        complete: Callable[..., Any],
        ears_factory: Callable[[int], Any],
        config: Optional[AppConfig] = None,
        endpoint_factory: Optional[Callable[[], Any]] = None,
        voice_factory: Optional[Callable[[Any], Any]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        summarise: Optional[Callable[..., Any]] = None,
        redact: Iterable[str] = (),
        server: Any = None,
        tools: Optional[ToolRegistry] = None,
        clock: Callable[[], float] = time.monotonic,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._config = config or AppConfig()
        self._state = state
        self._bus = bus
        self._memory = memory
        self._tools = tools if tools is not None else ToolRegistry()
        if self._config.remember_tool and REMEMBER_TOOL_NAME not in self._tools.specs:
            # Registered BEFORE the seam is bound below, so the schema reaches
            # the wire: bind_tools closes over the registry as it is, and
            # turn.turn records a registry whose tools the model was never
            # shown. The registry is otherwise empty by default, which is the
            # package's own rule — this daemon binds exactly two tools, both
            # over Gwen's own private memory.
            self._tools.register(
                REMEMBER_TOOL_NAME,
                {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": REMEMBER_FACT_DESCRIPTION}
                    },
                    "required": ["fact"],
                },
                self._remember_tool,
                description=REMEMBER_TOOL_DESCRIPTION,
            )
        if self._config.remember_tool and FORGET_TOOL_NAME not in self._tools.specs:
            self._tools.register(
                FORGET_TOOL_NAME,
                {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "string", "description": FORGET_ID_DESCRIPTION}
                    },
                    "required": ["record_id"],
                },
                self._forget_tool,
                description=FORGET_TOOL_DESCRIPTION,
            )
        self._complete = (
            complete
            if getattr(complete, "__embodiment_bound_registry__", None) is self._tools
            else bind_tools(_as_seam(complete), self._tools)
        )
        self._ears_factory = ears_factory
        self._ears: Any = None
        self._ears_rate: Optional[int] = None
        self._ears_sessions = 0
        self._ears_redials = 0
        self._endpoint_factory = endpoint_factory
        self._voice_factory = voice_factory or (lambda endpoint: None)
        self._session_factory = session_factory
        self._summarise = summarise
        #: Every literal spelling of every secret this daemon holds, in the
        #: forms they can arrive in. Built once: the scrub runs on every
        #: reply, and recomputing the escaped forms per turn would be work
        #: done on the hot path for a value that never changes.
        self._secret_forms = server_module.redaction_forms(
            tuple(s for s in (self._config.api_key, *redact) if s)
        )
        self._server = server
        self._clock = clock
        self._now = now or _now_local

        self._lock = threading.RLock()
        self._ear_lock = threading.RLock()
        self._ear_name: Optional[str] = None
        self._ear_endpoint: Any = None
        self._voice: Any = None
        self._generation = 0
        self._handovers = 0
        self._refusals = 0
        self._frames_dropped_no_session = 0
        self._pending_eos: Optional[tuple[float, float, Optional[int]]] = None
        self._recent_turns: deque[dict[str, Any]] = deque(maxlen=RECENT_TURNS)
        self._replies_scrubbed = 0
        self._teardown_timeouts = 0
        self._unreaped_endpoints: list[Any] = []
        self._barge_in_stop_timeouts = 0
        self._mute_intent = False
        self._warmups = 0
        self._warmup_failures = 0
        self._stale_frames = 0
        self._stale_generation: Optional[int] = None
        self._stale_generation_frames = 0
        self._stale_generation_recorded = False
        self._frames_captured = 0
        self._frames_forwarded = 0
        self._features_in = FeatureExtractor()

        self._clients = 0
        self._remote_clients = 0

        self._transcripts_received = 0
        self._empty_commits = 0
        self._transcripts_not_text = 0
        self._turns_completed = 0
        self._turns_in_flight = 0
        self._turn_serial = 0
        self._superseded_serial = -1
        self._turns_superseded = 0
        self._turns_dropped = 0
        self._turns_failed = 0
        self._remember_tool_calls = 0
        self._remember_tool_written = 0
        self._remember_tool_refused = 0
        self._forget_tool_calls = 0
        self._forget_tool_written = 0
        self._forget_tool_refused = 0
        self._asks_detected = 0
        self._asks_remembered = 0
        self._asks_failed = 0
        self._asks_deferred = 0
        self._asks_missed = 0
        self._summary_attempted = 0
        self._summary_written = 0
        self._summary_skip_reason: Optional[str] = None
        self._recall_mode: Optional[str] = None
        self._recall_last_hits = 0
        self._recall_hits_total = 0
        self._recall_rendered_total = 0
        self._recall_empty_total = 0
        self._recall_deadline_exceeded = 0
        self._recall_errors = 0
        self._recall_fallback_hits = 0
        self._recall_archived_hidden = 0
        self._recall_last_ids: tuple[str, ...] = ()
        self._recall_calls = 0
        self._degradation_counts: dict[str, int] = {}
        self._publish_errors = 0
        self._ledger_errors = 0

        self._session: Any = None
        self._voice_folded = 0
        self._session_folded = 0

        self._started = False
        self._closed = False
        self._stopping = False
        self._close_report: Optional[AppCloseReport] = None

        self._turn_queue: "queue.Queue[_Heard]" = queue.Queue(
            maxsize=max(1, int(self._config.turn_queue_size))
        )
        self._turn_thread: Optional[threading.Thread] = None
        self._ears_thread: Optional[threading.Thread] = None
        self._ears_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ears_connected = False
        self._ears_closed = threading.Event()
        self._ears_close_done = threading.Event()
        self._ears_close_bound: Optional[float] = None
        self._worker_stop = threading.Event()

    # ── the ONE recording path ───────────────────────────────────────────

    def _record(
        self, code: str, reason: object, *, source: str = APP_SOURCE, once: bool = False
    ) -> None:
        """Count it, append it to the crash ledger, publish it. Never raises.

        The ledger is appended BEFORE the event is published, and that order
        is deliberate: the ledger is the crash record (t4 ``fsync``-s every
        append), so a process killed between the two keeps the durable fact
        and loses only the notification. A reader that has seen the ledger
        record has therefore not necessarily seen the event yet — they are
        eventually both, never atomically.

        *once* is for the per-frame paths: a capture that fails once fails
        30 times a second, and a ledger that is deliberately unbounded (t4)
        must not be the thing that fills the disk. The FIRST occurrence of
        that code is recorded in full and every repeat only bumps the count
        in :meth:`status` — the same "degrade once, count after" discipline
        :mod:`embodiment.realtime.client` and :mod:`embodiment.events` use.
        """
        safe_code = _safe_name(code)
        safe_text = _safe_reason_text(reason)
        with self._lock:
            seen = self._degradation_counts.get(safe_code)
            if seen is None and len(self._degradation_counts) >= MAX_DEGRADATION_CODES:
                safe_code = "app-degradation-codes-capped"
                seen = self._degradation_counts.get(safe_code)
            self._degradation_counts[safe_code] = (seen or 0) + 1
            if once and seen:
                return
        try:
            self._state.ledger.append(safe_code, safe_text)
        except Exception:  # noqa: BLE001  # the ledger promises never to raise; count anyway
            with self._lock:
                self._ledger_errors += 1
        self._publish_degradation(_safe_name(source), safe_code, safe_text)

    def _publish_degradation(self, source: str, code: str, reason: str) -> None:
        """The one publish that must never re-enter :meth:`_record`."""
        try:
            self._bus.publish("degradation", {"source": source, "code": code, "reason": reason})
        except Exception:  # noqa: BLE001  # an injected bus is not trusted; count, never recurse
            with self._lock:
                self._publish_errors += 1

    def _scrub_reply(self, text: object) -> str:
        """Take the secrets out of a model reply before anything keeps it.

        The gateway is not trusted with its own key. A reply is *persisted*
        (the 0600 transcript, the session record in the private store),
        *published* (the ``reply`` event, which every dashboard viewer
        receives) and *spoken* — so a gateway that echoes the bearer token
        back inside ``message.content``, or an error page that quotes it,
        would put it in all three. The realtime client already guards this
        class on its own wire
        (:func:`embodiment.realtime.client._safe`); the HTTP seam did not,
        which is the wave review's one finding.

        Both spellings are checked, through the same helper the dashboard
        server uses (:func:`embodiment.http.server.redaction_forms`): the raw
        secret, and the secret as JSON would write it, because a key
        containing a quote or a backslash arrives escaped and a raw-substring
        filter would never see it.

        Scrubbing is counted and recorded once. A hit is not a formatting
        detail — it means the gateway said something it should never have
        said, and the operator should hear about it even though the daemon
        has already made it harmless.
        """
        value = _as_text(text)
        if not value or not self._secret_forms:
            return value
        scrubbed = value
        for form in self._secret_forms:
            if form in scrubbed:
                scrubbed = scrubbed.replace(form, REPLY_REDACTED)
        if scrubbed == value:
            return value
        with self._lock:
            self._replies_scrubbed += 1
        self._record(
            APP_REPLY_SECRET_SCRUBBED,
            "a secret came back inside a model reply and was removed",
            once=True,
        )
        return scrubbed

    def _scrubbing_summariser(self) -> Optional[Callable[[list[dict[str, Any]]], str]]:
        """The injected summariser, with its answer scrubbed on the way out.

        Wrapped here rather than at the call site because
        :meth:`embodiment.session.Session.close` takes the summariser and
        writes what it returns straight into the private store — there is no
        later point at which this module sees that text.
        """
        summarise = self._summarise
        if summarise is None:
            return None

        def scrubbed(messages: list[dict[str, Any]]) -> str:
            return self._scrub_reply(summarise(messages))

        return scrubbed

    def _fold(self, source: str, record: Any) -> None:
        """Record a sibling module's own degradation, keeping its own code."""
        folded = fold_degradation(record, source=source)
        self._record(folded["code"], folded["reason"], source=folded["source"])

    def _publish(self, kind: str, data: dict[str, Any]) -> None:
        """Publish one event. A publish failure is recorded, never raised."""
        try:
            self._bus.publish(kind, data)
        except Exception as exc:  # noqa: BLE001  # an injected bus is not trusted
            with self._lock:
                self._publish_errors += 1
            self._record(APP_PUBLISH_FAILED, f"{_safe_name(kind)}: {_describe(exc)}", once=True)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bring the daemon up: the ear, the dashboard, the ears loop. Idempotent."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self._log("started", target="embodiment.daemon.app")
        self._publish("state", {"component": "daemon", "status": "starting"})
        self._attach_default_ear()
        self._start_server()
        self._start_turn_thread()
        self._publish("state", {"component": "daemon", "status": "running"})

    def run(self, stop_event: threading.Event) -> int:
        """Block until *stop_event*, then shut down within a bound. Never raises."""
        self.start()
        try:
            while not stop_event.wait(max(0.01, float(self._config.poll_interval_s))):
                self.pump()
        except Exception as exc:  # noqa: BLE001  # a daemon loop must not die on a fault
            self._record(APP_TURN_FAILED, _describe(exc))
        finally:
            self.close(deadline=self._config.shutdown_deadline)
        return 0

    def pump(self) -> None:
        """One tick of the daemon's own clock: out-features, then the bus clock."""
        self._drain_out_features()
        try:
            self._bus.tick(self._clock())
        except Exception as exc:  # noqa: BLE001  # an injected bus is not trusted
            self._record(APP_PUBLISH_FAILED, f"tick: {_describe(exc)}")

    def shutdown(self, deadline: float = 5.0) -> AppCloseReport:
        """The name :class:`embodiment.daemon.lifecycle.DaemonRunner` calls."""
        return self.close(deadline=deadline)

    def close(self, deadline: float = 5.0) -> AppCloseReport:
        """Stop everything within *deadline*, reporting what is unfinished.

        Idempotent and never raises. The order is deliberate: the ear stops
        first (nothing new arrives), then the ears session, then the turn
        worker, then the parts a turn depends on, and the bus last — so every
        degradation recorded on the way out is still published.
        """
        with self._lock:
            if self._closed:
                report = self._close_report or AppCloseReport(closed=True, already_closed=True)
                return AppCloseReport(**{**report.to_dict(), "already_closed": True})
            self._closed = True
            # The bound the EAR will be given, derived here — before anything
            # can start stopping — because both paths that close it must use
            # the same number: the listening coroutine's own finally (which
            # usually gets there first, since setting ``_stopping`` ends its
            # stream) and :meth:`_stop_ears`. Handing the client the whole
            # shutdown deadline from one path and a slice of it from the other
            # is what made the waiter time out on a healthy close.
            self._ears_close_bound = max(
                0.05, max(0.1, float(deadline)) * _EARS_STEP_FRACTION * _EARS_CLOSE_WAIT_SHARE
            )
            self._stopping = True
        started = self._clock()
        budget = max(0.1, float(deadline))
        self._worker_stop.set()

        def step(fraction: float, call: Callable[[float], bool]) -> bool:
            """One shutdown step, under the slice of the budget it is allowed."""
            share = self._share(started, budget, fraction)
            return self._bounded(lambda: call(share), share)

        endpoint_closed = step(
            0.20,
            lambda d: self.detach_ear(publish=False, deadline=d).attached is False
            and self._reap_endpoints(d),
        )
        ears_stopped = step(_EARS_STEP_FRACTION, self._stop_ears)
        turn_stopped = step(0.50, self._stop_turn_thread)
        abandoned = self._turn_queue.qsize()
        voice_closed = step(0.60, self._close_voice)
        server_stopped = step(0.75, self._stop_server)
        session_closed = step(0.90, self._close_session)
        memory_closed = step(0.97, self._close_memory)

        self._publish("state", {"component": "daemon", "status": "stopped"})
        bus_closed = step(1.0, self._close_bus)

        report = AppCloseReport(
            closed=True,
            already_closed=False,
            ears_stopped=ears_stopped,
            turn_thread_stopped=turn_stopped,
            turns_abandoned=abandoned,
            voice_closed=voice_closed,
            endpoint_closed=endpoint_closed,
            server_stopped=server_stopped,
            session_closed=session_closed,
            memory_closed=memory_closed,
            bus_closed=bus_closed,
            elapsed_s=self._clock() - started,
        )
        with self._lock:
            self._close_report = report
        if report.unfinished:
            self._record(APP_SHUTDOWN_INCOMPLETE, "unfinished: " + ",".join(report.unfinished))
        self._log("stopped", unfinished=list(report.unfinished))
        return report

    def _bounded(self, call: Callable[[], bool], deadline: float) -> bool:
        """Run one shutdown step on a worker thread, bounded by *deadline*.

        Lesson 2, found by attacking this module: a seam that ignores the
        deadline it was given (a memory layer whose ``close`` hangs, a server
        that will not stop) made :meth:`close` itself unbounded — the one
        method the daemon's own watchdog is relying on to come back. Each step
        now gets a thread of its own and a join; a step that does not finish
        is reported as unfinished rather than waited for.
        """
        box: list[bool] = []

        def work() -> None:
            try:
                box.append(bool(call()))
            except Exception as exc:  # noqa: BLE001  # a step that raises is a step that failed
                self._record(APP_SHUTDOWN_INCOMPLETE, _describe(exc))
                box.append(False)

        worker = threading.Thread(target=work, name="embodiment-shutdown", daemon=True)
        worker.start()
        worker.join(timeout=max(0.05, deadline))
        return bool(box and box[0])

    def _share(self, started: float, budget: float, fraction: float) -> float:
        """The deadline for the step that must be done by *fraction* of the budget."""
        return max(0.05, (started + budget * fraction) - self._clock())

    # ── the ear registry: exactly one ────────────────────────────────────

    def attach_ear(self, name: object, endpoint: Any) -> EarHandover:
        """Make *endpoint* the one active ear. Never raises.

        A second attach pre-empts or is refused by policy; either way exactly
        one ``state`` event and one ledger record describe the handover.
        """
        ear = _safe_name(name)
        with self._ear_lock:
            if self._closed:
                self._record(APP_CLOSED, f"attach_ear after close: {ear}")
                return EarHandover(ear=ear, attached=False, refused=True, reason="closed")
            previous = self._ear_name
            if previous is not None and not self._config.preempt_ear:
                self._refusals += 1
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "refused",
                        "ear": ear,
                        "previous": previous,
                    },
                )
                self._record(APP_EAR_REFUSED, f"{ear} refused; {previous} keeps the session")
                return EarHandover(
                    ear=ear,
                    attached=False,
                    refused=True,
                    previous=previous,
                    reason="one ear at a time",
                )
            if previous is not None:
                self._teardown_ear()
            if not self._install_ear(ear, endpoint):
                return EarHandover(
                    ear=ear, attached=False, previous=previous, reason="attach failed"
                )
            if previous is not None:
                self._handovers += 1
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "handover",
                        "ear": ear,
                        "previous": previous,
                    },
                )
                self._record(APP_EAR_PREEMPTED, f"{previous} displaced by {ear}")
            else:
                self._publish(
                    "state",
                    {"component": "ear", "status": "attached", "ear": ear, "previous": None},
                )
            self._log("ear-attached", ear=ear, previous=previous)
            self._publish_mic()
            return EarHandover(
                ear=ear, attached=True, preempted=previous is not None, previous=previous
            )

    def detach_ear(
        self, name: object = None, *, publish: bool = True, deadline: Optional[float] = None
    ) -> EarHandover:
        """Drop the active ear. Idempotent; never raises."""
        with self._ear_lock:
            previous = self._ear_name
            if previous is None:
                return EarHandover(ear="", attached=False, previous=None, reason="no ear")
            self._teardown_ear(deadline)
            if publish:
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "detached",
                        "ear": previous,
                        "previous": previous,
                    },
                )
                self._publish_mic()
            self._log("ear-detached", ear=previous)
            return EarHandover(ear="", attached=False, previous=previous)

    def _install_ear(self, ear: str, endpoint: Any) -> bool:
        """Attach, dial, capture, speak. In that order. Returns whether it took.

        The order is the fix for a live defect, not a preference. Capture used
        to start before the realtime session was dialled, and an endpoint's
        capture thread is live the moment ``start_capture`` returns — so on
        the restart of 2026-09-22 the first three frames found
        ``self._ears is None`` and were recorded as ``app-capture-failed``
        carrying an ``AttributeError`` (``'NoneType' object has no attribute
        'send_audio'``: 47 characters, fingerprint ``636e0031``, exactly what
        the ledger showed). Dialling first costs nothing — the session's rate
        comes from a property the endpoint answers without capturing — and it
        means a frame always has somewhere to go.
        """
        generation = self._generation + 1
        if not self._safely(endpoint.attach, APP_EAR_ATTACH_FAILED, ear):
            self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
            return False
        self._generation = generation
        self._ear_name = ear
        self._ear_endpoint = endpoint
        self._features_in.reset()
        self._ensure_ears_session(ear, endpoint)
        try:
            endpoint.start_capture(self._frame_callback(generation))
        except Exception as exc:  # noqa: BLE001  # an endpoint is not trusted to keep its word
            self._record(APP_EAR_ATTACH_FAILED, f"{ear}: {_describe(exc)}")
            self._ear_name = None
            self._ear_endpoint = None
            self._generation += 1
            self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
            return False
        self._ensure_voice(ear, endpoint)
        self._carry_mute(ear, endpoint)
        self._warm_up_playback(endpoint)
        self._fold_endpoint(endpoint)
        return True

    def _carry_mute(self, ear: str, endpoint: Any) -> None:
        """Apply the operator's last mute decision to a newly attached ear.

        Found answering the review's Q6: without this, a handover silently
        un-mutes. The mic is muted by muting the ENDPOINT, and a new endpoint
        starts at its own default (hot) — so an operator who muted, then had
        a browser ear pre-empt the host one (or used stop/start, which builds
        a fresh endpoint), got a live microphone back without asking for it.
        The ``mic`` event did report the change, so it was visible rather than
        secret, but visible is not the same as intended.

        "Hot mic on start" (``CLAUDE.md``) is about the daemon's INITIAL
        state, which is what :attr:`_mute_intent` defaults to. After that the
        operator's last word travels with the ear.
        """
        if not self._mute_intent:
            return
        self._safely(lambda: endpoint.mute(True), APP_CAPTURE_FAILED, f"{ear} mute")

    def _ensure_voice(self, ear: str, endpoint: Any) -> None:
        """ONE :class:`~embodiment.voice.Voice` for the daemon's life, re-pointed.

        Built by the injected factory the first time an ear attaches, and
        afterwards handed the new endpoint with ``set_endpoint`` (t12 round 3)
        rather than rebuilt. Three reasons, all of them defects in the
        rebuild-per-ear shape this replaces:

        * a new :class:`Voice` starts its degradation list empty while this
          app's fold index still pointed past it, so the new voice's first
          degradations were silently skipped until its count caught up;
        * every rebuild started a new pacing thread and a new timeline, which
          is exactly what that module's pacing worker is written to avoid; and
        * ``queued_not_traced`` and the degradation counts — the numbers that
          say what a handover cost — were thrown away with the old object.
        """
        voice = self._voice
        if voice is None:
            try:
                self._voice = self._voice_factory(endpoint)
            except Exception as exc:  # noqa: BLE001  # a factory is a seam, not a promise
                self._voice = None
                self._record(APP_EAR_ATTACH_FAILED, f"{ear} voice: {_describe(exc)}")
            return
        self._safely(lambda: voice.set_endpoint(endpoint), APP_EAR_ATTACH_FAILED, f"{ear} voice")

    def _warm_up_playback(self, endpoint: Any) -> None:
        """Play a short silence so the playback link is checked before Gwen speaks.

        The endpoint only verifies that its player landed on the intended node
        once a player actually exists, so before this an ear that had never
        spoken reported ``playback_target_verified: None`` — indistinguishable,
        to anything asserting on it, from an endpoint that does not verify at
        all. A tenth of a second of zeros starts the player, the check runs,
        and the verdict is a real boolean before the first reply.

        NOT a fault path: a warm-up that fails is counted
        (``status()["ear"]["warmup_failures"]``) and nothing is written to the
        ledger — the ledger records what went wrong, and a warm-up is a
        convenience, not a promise. It is also not a turn: nothing is
        published, no reply exists, and the voice is not involved.

        Asynchronous by design: the verdict lands when the endpoint's own
        thread has started the child and inspected the link, typically inside
        a second. This does not wait for it — an attach on the hot path must
        not block on an audio server — so a caller asserting on the boolean
        should poll it with a bound rather than read it the instant attach
        returns.
        """
        silence = b"\x00\x00" * int(PLAYBACK_RATE_HZ * WARMUP_SILENCE_S)
        try:
            endpoint.play(silence)
        except Exception:  # noqa: BLE001  # a warm-up is a convenience, never a promise
            with self._lock:
                self._warmup_failures += 1
            return
        with self._lock:
            self._warmups += 1

    def _fold_endpoint(self, endpoint: Any) -> None:
        """Record what the endpoint says about ITSELF at the moment it attaches.

        An endpoint that cannot import its driver, cannot find a device, or
        cannot open a stream still *attaches* — it degrades rather than
        raising, exactly as this package requires. Without this, ``status()``
        would show an ear called ``host`` with nothing behind it and the
        ledger would say nothing (``CLAUDE.md``: a healthy-looking ``status``
        is not evidence that Gwen heard anything).
        """
        probed = _probe(endpoint) or {}
        for key in ("degradation", "degradation_in", "degradation_out"):
            record = probed.get(key)
            if isinstance(record, dict) and record.get("code"):
                self._record(record.get("code", ""), record.get("reason", ""), source="endpoint")
        quiet = probed.get("playback_quiet_count")
        if isinstance(quiet, int) and not isinstance(quiet, bool) and quiet > 0:
            # Round 8 reports a quiet sink as a COUNTER and an event of its
            # own, not on one of the three degradation slots above, so it
            # would otherwise never reach the ledger — and a reply nobody can
            # hear is exactly the failure a host needs named rather than
            # inferred from silence.
            self._record(
                ENDPOINT_PLAYBACK_QUIET,
                f"the sink is below the floor or system-muted ({quiet}x)",
                source="endpoint",
            )

    def _teardown_ear(self, deadline: Optional[float] = None) -> None:
        """Stop the current ear, within a bound. Every failure recorded, none raised.

        Bounded because it is not only reached from :meth:`close`: a handover
        runs it on the attaching thread, holding the ear lock, and an endpoint
        whose ``stop_capture`` blocks would hold that lock for as long as the
        device felt like it. The operator asked for a different ear; waiting
        for a dead one serves nobody.

        What the bookkeeping does FIRST is what makes giving up safe: the
        generation is bumped and ``_ear_name``/``_ear_endpoint`` cleared
        before any endpoint call, so a hung endpoint's late frames are already
        stale, ``status()`` never touches it, and nothing routes to it again.
        An endpoint that overruns is recorded
        (:data:`APP_EAR_TEARDOWN_TIMEOUT`) and kept on the unreaped list for a
        later :meth:`close` to retry — the same shape t7's endpoint uses for a
        child it could not reap.
        """
        endpoint, ear = self._ear_endpoint, self._ear_name or ""
        self._ear_name = None
        self._ear_endpoint = None
        self._generation += 1
        voice = self._voice
        if voice is not None:
            self._fold_voice(voice)

        def work() -> bool:
            # Always True on its own: every sub-call below is folded through
            # _safely, which records the failure and moves on, so this step
            # has no verdict of its own. The only way it reads as False is
            # _bounded's timeout, which the caller counts and records below.
            if voice is not None:
                # The voice outlives the ear: it is pointed at a NullEndpoint
                # rather than closed, so a reply with no ear attached is still
                # PUBLISHED through the one code path that publishes replies,
                # and the next attach costs a re-point instead of a rebuild.
                # Inside the bound because re-pointing stops the OLD endpoint,
                # which is exactly the call that can hang.
                self._safely(
                    lambda: voice.set_endpoint(NullEndpoint()),
                    APP_EAR_DETACH_FAILED,
                    f"{ear} voice",
                )
            if endpoint is None:
                return True
            self._safely(endpoint.stop_capture, APP_EAR_DETACH_FAILED, ear)
            self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
            self._safely(lambda: endpoint.close(TEARDOWN_DEADLINE_S), APP_EAR_DETACH_FAILED, ear)
            return True

        bound = TEARDOWN_DEADLINE_S if deadline is None else max(0.05, float(deadline))
        if self._bounded(work, bound):
            return
        with self._lock:
            self._teardown_timeouts += 1
            if endpoint is not None:
                self._unreaped_endpoints.append(endpoint)
        self._record(
            APP_EAR_TEARDOWN_TIMEOUT,
            f"{_safe_name(ear)} did not stop inside {bound}s; kept for a later close",
        )

    def _reap_endpoints(self, deadline: float) -> bool:
        """Retry the endpoints a handover had to give up on. Never raises."""
        with self._lock:
            pending, self._unreaped_endpoints = self._unreaped_endpoints, []
        if not pending:
            return True
        share = max(0.05, float(deadline) / len(pending))
        reaped = True
        for endpoint in pending:

            def close_one(ep: Any = endpoint) -> bool:
                # Always True: whether the close RAISED is recorded by
                # _safely, and is a different fact from whether it RETURNED.
                # Only the second one is what this bound is asking about.
                self._safely(lambda: ep.close(share), APP_EAR_DETACH_FAILED, "unreaped")
                return True

            if not self._bounded(close_one, share):
                reaped = False
                with self._lock:
                    self._unreaped_endpoints.append(endpoint)
        return reaped

    def _safely(self, call: Callable[[], Any], code: str, what: str) -> bool:
        """Run *call*, recording any failure under *code*. Never raises."""
        try:
            call()
        except Exception as exc:  # noqa: BLE001  # every teardown step is best-effort
            self._record(code, f"{_safe_name(what)}: {_describe(exc)}")
            return False
        return True

    def _attach_default_ear(self) -> None:
        """The host ear — degraded if there is no device — or a recorded stand-in.

        Which one a host should expect, stated because the two look similar in
        the ledger and are not the same fact:

        * **No driver, no device, or a stream that will not open** (no
          ``sounddevice`` on this box, no PortAudio, no microphone): the
          factory still returns a :class:`~embodiment.audio.host.HostEndpoint`
          and ``attach()`` degrades rather than raising. The ear is
          ``host``, ``status()["ear"]["degraded"]`` is ``True``, and the
          endpoint's own code (``audio-host-import-failed``,
          ``audio-host-portaudio-missing``, ``audio-host-no-devices``,
          ``audio-host-open-failed``) is in the ledger. This is deliberate: a
          host ear that is present-but-deaf can be *recovered* without
          restarting the daemon — install the driver or plug the device in,
          then ``POST /api/voice/stop`` and ``/api/voice/start``, which builds
          a fresh endpoint. A :class:`NullEndpoint` could not recover, because
          nothing would ever build a real one again.
        * **The factory itself failed or was never given** (it raised, or
          returned ``None``): there is nothing to recover, so a
          :class:`NullEndpoint` stands in under the ear name ``null`` with
          :data:`APP_NO_ENDPOINT` recorded. The daemon still hears nothing and
          still speaks into the void, which is the same behaviour by a
          different route — hence one code path, not two.

        Either way ``status()["ear"]`` names which (``kind``) and says it is
        degraded, because "a healthy-looking ``status`` is not evidence that
        Gwen heard anything" (``CLAUDE.md``).
        """
        endpoint: Any = None
        if self._endpoint_factory is not None:
            try:
                endpoint = self._endpoint_factory()
            except Exception as exc:  # noqa: BLE001  # no device is a degradation, not a crash
                self._record(APP_NO_ENDPOINT, _describe(exc))
                endpoint = None
        if endpoint is None:
            if self._endpoint_factory is not None:
                pass  # the failure above is already recorded
            self._record(APP_NO_ENDPOINT, "no audio endpoint; the daemon speaks into the void")
            endpoint = NullEndpoint()
            self.attach_ear("null", endpoint)
            return
        self.attach_ear("host", endpoint)

    def _frame_callback(self, generation: int) -> Callable[[bytes], None]:
        """A capture callback bound to ONE ear generation. Never raises at the device."""

        def on_frame(pcm: bytes) -> None:
            if generation != self._generation:
                self._note_stale_frame(generation)
                return
            with self._lock:
                self._frames_captured += 1
            ears = self._ears
            if ears is None:
                # No session to send to — a factory that failed, or an ear
                # attached before one could be dialled. Named and counted
                # rather than left to raise an AttributeError that reads like
                # a bug in the transport.
                with self._lock:
                    self._frames_dropped_no_session += 1
                self._record(APP_FRAMES_NO_SESSION, "no realtime session to send to", once=True)
                return
            try:
                ears.send_audio(pcm)
            except Exception as exc:  # noqa: BLE001  # the ears client is a seam
                self._record(APP_CAPTURE_FAILED, _describe(exc), once=True)
            else:
                with self._lock:
                    self._frames_forwarded += 1
            self._publish_features("in", self._features_in, pcm)

        return on_frame

    def _note_stale_frame(self, generation: int) -> None:
        """A frame from an ear that is no longer the ear. Count it; judge it. Never raises.

        One late frame is not a fault — it is what a handover looks like from
        inside the displaced endpoint's capture thread, and it showed up in
        the ledger on the first live stop for exactly that reason. So the
        first frame of a retired generation is counted and published as a
        ``state`` event, and only a count that keeps GROWING past
        :data:`_STALE_FRAME_TOLERANCE` is recorded: that is an ear that will
        not stop, which is the fault worth waking someone for.

        The per-generation count is kept for ONE generation at a time — the
        one currently delivering late frames — so a daemon that hands over all
        day accumulates nothing.
        """
        with self._lock:
            self._stale_frames += 1
            if generation != self._stale_generation:
                self._stale_generation = generation
                self._stale_generation_frames = 0
                self._stale_generation_recorded = False
            self._stale_generation_frames += 1
            count = self._stale_generation_frames
            total = self._stale_frames
            first = count == 1
            crossing = count > _STALE_FRAME_TOLERANCE and not self._stale_generation_recorded
            if crossing:
                self._stale_generation_recorded = True
        if first or crossing:
            self._publish(
                "state",
                {
                    "component": "ear",
                    "status": "stale-frame",
                    "stale_frames": total,
                    "generation": generation,
                },
            )
        if crossing:
            self._record(
                APP_FRAME_FROM_STALE_EAR,
                f"a retired ear has delivered {count} frames since the handover",
            )

    def _publish_features(self, direction: str, extractor: Any, pcm: bytes) -> None:
        try:
            frames = extractor.feed(pcm)
        except Exception as exc:  # noqa: BLE001  # a dashboard trace must not stop the ear
            self._record(APP_FEATURES_FAILED, f"{direction}: {_describe(exc)}", once=True)
            return
        for frame in frames:
            self._publish("features", {"direction": direction, **frame})

    def _drain_out_features(self) -> None:
        """Publish what the voice traced from PLAYED audio since the last pump.

        Through :meth:`~embodiment.voice.Voice.drain_features` only (t12 round
        3), never by slicing ``feature_frames`` from outside: that list is
        appended to under the voice's own lock by its pacing thread, and a
        caller reaching past that lock is a race waiting for a slow pump.
        """
        voice = self._voice
        if voice is None:
            return
        try:
            taken = voice.drain_features(MAX_FEATURE_DRAIN)
        except Exception as exc:  # noqa: BLE001  # the voice is a seam like any other
            self._record(APP_FEATURES_FAILED, f"out: {_describe(exc)}", once=True)
            return
        for frame in taken or ():
            if isinstance(frame, dict):
                self._publish("features", {"direction": "out", **frame})

    # ── clients: published, never read by the turn ───────────────────────

    def attach_client(self, *, remote: bool = False) -> int:
        """One more viewer or browser ear. Information only."""
        with self._lock:
            self._clients += 1
            if remote:
                self._remote_clients += 1
            counts = (self._clients, self._remote_clients)
        self._publish("clients", {"count": counts[0], "remote": counts[1]})
        return counts[0]

    def detach_client(self, *, remote: bool = False) -> int:
        """One fewer viewer. Never negative; information only."""
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if remote:
                self._remote_clients = max(0, self._remote_clients - 1)
            counts = (self._clients, self._remote_clients)
        self._publish("clients", {"count": counts[0], "remote": counts[1]})
        return counts[0]

    # ── the mic ──────────────────────────────────────────────────────────

    def set_mute(self, muted: bool) -> dict[str, Any]:
        """Mute or unmute the capture path (the endpoint enforces it before encode)."""
        endpoint = self._ear_endpoint
        wanted = bool(muted)
        if endpoint is None:
            # Remembered anyway: the next ear to attach honours it rather than
            # coming up hot because nothing was listening when it was asked.
            with self._lock:
                self._mute_intent = wanted
            self._record(APP_NO_ENDPOINT, "mute requested with no ear attached")
            return {"muted": wanted, "ear": self._ear_name, "applied": False}
        with self._lock:
            self._mute_intent = wanted
        applied = self._safely(lambda: endpoint.mute(wanted), APP_CAPTURE_FAILED, "mute")
        self._publish_mic()
        return {"muted": self._muted(), "ear": self._ear_name, "applied": applied}

    def _muted(self) -> bool:
        endpoint = self._ear_endpoint
        if endpoint is None:
            return True
        try:
            return bool(endpoint.muted)
        except Exception as exc:  # noqa: BLE001  # an endpoint probe is not trusted
            self._record(APP_CAPTURE_FAILED, f"muted: {_describe(exc)}")
            return True

    def _publish_mic(self) -> None:
        ear = self._ear_name
        self._publish("mic", {"hot": ear is not None and not self._muted(), "ear": ear})

    # ── the turn ─────────────────────────────────────────────────────────

    def submit_transcript(self, text: object) -> bool:
        """Queue one heard utterance for the turn thread. Never blocks, never raises.

        This is where what the EAR delivered is counted, and where an empty
        commit stops: it never takes a slot in the bounded turn queue, so a
        quiet room cannot displace a real utterance under back-pressure.
        """
        if self._closed:
            self._record(APP_CLOSED, "transcript after close")
            return False
        kind = classify_transcript(text)
        if kind == TRANSCRIPT_NOT_TEXT:
            self._note_transcript(kind)
            self._record(APP_TRANSCRIPT_NOT_TEXT, _safe_name(type(text).__name__))
            return False
        self._note_transcript(kind)
        if kind == TRANSCRIPT_EMPTY:
            return False
        with self._lock:
            eos, self._pending_eos = self._pending_eos, None
        heard = _Heard(
            text=text,
            eos_at=eos[0] if eos else None,
            eos_wall=eos[1] if eos else None,
            eos_at_ms=eos[2] if eos else None,
            transcript_at=self._clock(),
        )
        try:
            self._turn_queue.put_nowait(heard)
        except queue.Full:
            with self._lock:
                self._turns_dropped += 1
            self._record(
                APP_TURN_QUEUE_FULL, f"{self._turn_queue.maxsize} already waiting", once=True
            )
            return False
        return True

    def _note_transcript(self, kind: str) -> None:
        """Count one delivered transcript, and say so on the bus. Never raises.

        An empty commit is **counted and published, never recorded**. Measured
        on the rig: in 60 s of the operator speaking, the server VAD committed
        15 turns and 7 of them came back with an empty transcript (breath and
        room noise, whisper returning nothing). That is the segmenter doing
        its job at roughly seven a minute while the room is quiet — writing it
        to the crash ledger would bury the things that actually went wrong
        under the sound of nobody talking.
        """
        with self._lock:
            if kind == TRANSCRIPT_EMPTY:
                self._empty_commits += 1
            elif kind == TRANSCRIPT_NOT_TEXT:
                self._transcripts_not_text += 1
            else:
                self._transcripts_received += 1
            counts = (self._transcripts_received, self._empty_commits)
        if kind != TRANSCRIPT_EMPTY:
            return
        self._publish(
            "state",
            {
                "component": "ears",
                "status": "empty-commit",
                "empty_commits": counts[1],
                "transcripts": counts[0],
            },
        )

    def run_turn(self, text: object) -> TurnResult:
        """ONE spoken turn, start to finish. Never raises; the daemon survives it.

        The ONE code path: the turn thread calls exactly this. It classifies
        through :func:`classify_transcript` exactly as
        :meth:`submit_transcript` does, so a direct caller cannot start a turn
        on something the ear path would have refused. The COUNTERS live at the
        ear's entry point, not here, so a queued transcript is counted once.
        """
        if self._closed:
            self._record(APP_CLOSED, "run_turn after close")
            return TurnResult(spoken="")
        kind = classify_transcript(text)
        if kind == TRANSCRIPT_NOT_TEXT:
            self._record(APP_TRANSCRIPT_NOT_TEXT, _safe_name(type(text).__name__))
            return TurnResult(spoken="")
        if kind == TRANSCRIPT_EMPTY:
            # Counted where it arrived (``submit_transcript``), not here, and
            # never recorded: an empty commit is not a fault.
            return TurnResult(spoken="")
        return self._run_heard(_Heard(text=text, transcript_at=self._clock()))

    def _run_heard(self, heard: _Heard) -> TurnResult:
        """The ONE turn path. Both entry points arrive here with their timings."""
        text = heard.text
        with self._lock:
            self._turns_in_flight += 1
            self._turn_serial += 1
            serial = self._turn_serial
        timing = TurnTiming(
            serial=serial,
            eos_at=heard.eos_at,
            eos_wall=heard.eos_wall,
            eos_at_ms=heard.eos_at_ms,
            transcript_at=heard.transcript_at,
        )
        try:
            return self._run_turn(text, serial, timing)
        except Exception as exc:  # noqa: BLE001  # a turn fault must not kill the daemon
            with self._lock:
                self._turns_failed += 1
            self._record(APP_TURN_FAILED, _describe(exc))
            self._note_timing(replace(timing, failed=True).derived())
            return TurnResult(spoken="")
        finally:
            with self._lock:
                self._turns_in_flight -= 1

    def _run_turn(self, text: str, serial: int, timing: TurnTiming) -> TurnResult:
        self._publish("turn", {"phase": "heard", "step_count": 0})
        packet, record = perceive(text)
        spoken_in = packet.original if isinstance(packet.original, str) else text
        if getattr(record, "degraded", False):
            self._record("app-perception-degraded", "intake degraded")
        self._publish("transcript", {"role": "user", "text": spoken_in})

        session = self._ensure_session()
        if session is not None:
            self._note_ask(session, spoken_in)

        recalled, rendered_ids = self._recall(spoken_in)
        timing = replace(timing, rendered_ids=rendered_ids)
        window = self._window(session)
        config = TurnConfig(
            role=self._config.role,
            system_prompt=_compose_prompt(
                self._config.system_prompt,
                window,
                recalled,
                tool_prompt=REMEMBER_TOOL_PROMPT if self._config.remember_tool else "",
                clock=self._clock_line(),
            ),
        )
        self._publish("turn", {"phase": "thinking", "step_count": 0})
        result = run_one_turn(spoken_in, self._complete, tools=self._tools, config=config)
        # Before the session, the transcript, the bus or the voice see it.
        spoken_out = self._scrub_reply(result.spoken)
        if spoken_out != result.spoken:
            result = replace(result, spoken=spoken_out)
        timing = replace(timing, reply_text_at=self._clock())
        for degradation in result.degradations:
            self._fold("turn", degradation)
        with self._lock:
            self._turns_completed += 1

        spoke = self._speak(result.spoken, serial)
        timing = replace(
            timing,
            first_audio_at=getattr(spoke, "first_play_at", None) if spoke else None,
            spoken_done_at=self._clock() if spoke else None,
            superseded=self._is_superseded(serial),
        ).derived()

        # The transcript record carries the timings, so a reader has the turn
        # and its measurement in one place rather than two files to join.
        if session is not None:
            self._safely(
                lambda: session.add_assistant(result.spoken, metadata=timing.to_dict()),
                APP_TURN_FAILED,
                "add_assistant",
            )
            self._fold_session(session)
        self._publish(
            "turn",
            {"phase": "spoken", "step_count": result.steps, **timing.to_dict()},
        )
        self._note_timing(timing)
        return result

    def _note_timing(self, timing: TurnTiming) -> None:
        """Keep one turn's measurement where a reader can find it. Never raises."""
        with self._lock:
            self._recent_turns.append(timing.to_dict())

    def _speak(self, spoken: str, serial: int) -> Any:
        """Hand the reply to the voice — unless the room moved on while we thought.

        Two checks, because the race is real: a ``speech_started`` can land in
        the microseconds between deciding to speak and the voice actually
        starting. The FIRST check (before handing over) is what normally
        stops a superseded reply. The SECOND (after
        :meth:`~embodiment.voice.Voice.speak` returns, which is when the
        audio is queued rather than played) catches the hairline case and
        stops the speaker through the voice's own barge-in path, so nothing
        that started playing after a ``speech_started`` was seen keeps
        playing beyond the barge-in bound.
        """
        voice = self._voice
        if self._is_superseded(serial):
            self._publish_reply(spoken, superseded=True)
            return None
        if voice is None:
            self._record(APP_NO_ENDPOINT, "nothing to speak through; the reply was not voiced")
            self._publish_reply(spoken, superseded=False)
            return None
        spoke: list[Any] = []

        def say() -> None:
            spoke.append(voice.speak(spoken))

        self._safely(say, APP_TURN_FAILED, "speak")
        if self._is_superseded(serial):
            self._safely(voice.on_speech_started, APP_TURN_FAILED, "barge-in")
        self._fold_voice(voice)
        return spoke[0] if spoke else None

    def _is_superseded(self, serial: int) -> bool:
        with self._lock:
            return self._superseded_serial == serial

    def _publish_reply(self, text: str, *, superseded: bool) -> None:
        """The app's own reply publish, for replies the voice never sees.

        :class:`~embodiment.voice.Voice` publishes the reply it is about to
        speak; this is the other two cases — no voice at all, and a reply
        dropped because the room moved on — so a dashboard sees every reply
        either way, and can tell which kind it was.
        """
        self._publish("reply", {"text": text, "superseded": superseded})

    def _remember_tool(self, fact: object = "", **_ignored: Any) -> str:
        """The ``remember`` tool: the model's own way to keep a fact. Never raises.

        The operator's decision (``d7``): "we can't have the exact «תזכרי ש»
        as key — it needs to be the model calling that tool." The spoken
        detector stays, counted, as a FALLBACK for the phrasings it does
        match; this is the primary path, and it works on any phrasing the
        model recognises as a request to remember.

        Writes through the same :class:`~embodiment.memory.RoomMemory` the
        detector used — the private store, deadline-bounded — so there is one
        kind of "the user asked me to keep this" in the store rather than two
        that a later reader has to reconcile.

        Returns a short structured line the model can speak from. It carries
        the record id and never the fact: the model already has the fact, and
        a tool result is one more place text could leak from.
        """
        with self._lock:
            self._remember_tool_calls += 1
        text = fact if isinstance(fact, str) else ""
        stripped = text.strip()
        if not isinstance(fact, str):
            return self._refuse_remember("not-text")
        if not stripped:
            return self._refuse_remember("empty")
        if len(stripped) > REMEMBER_FACT_MAX_CHARS:
            return self._refuse_remember("too-long")
        try:
            result = self._memory.remember(
                stripped,
                visibility=PRIVATE,
                record_type=ASK_RECORD_TYPE,
                added_by=self._config.added_by,
            )
        except Exception as exc:  # noqa: BLE001  # memory is a seam; a turn never dies on it
            self._record(APP_REMEMBER_REFUSED, f"store: {_describe(exc)}")
            return self._refuse_remember("store-failed", recorded=True)
        if not getattr(result, "ok", False):
            degradation = getattr(result, "degradation", None)
            if degradation is not None:
                self._fold("memory", degradation)
            return self._refuse_remember("store-refused", recorded=True)
        record_id = _safe_record_id(getattr(result, "record_id", ""))
        with self._lock:
            self._remember_tool_written += 1
            written = self._remember_tool_written
        self._publish(
            "state",
            {
                "component": "memory",
                "status": "remembered",
                "record_id": record_id,
                "remembered": written,
                "source": "tool",
            },
        )
        return f"ok: stored as {record_id}" if record_id else "ok: stored"

    def _refuse_remember(self, reason: str, *, recorded: bool = False) -> str:
        """Count a refusal, say so on the bus, and tell the model why. Never text.

        *reason* is a fixed token from this method's own vocabulary, never
        anything the model supplied — the fact itself is exactly what must not
        reach a record, a log or an event.
        """
        with self._lock:
            self._remember_tool_refused += 1
            refused = self._remember_tool_refused
        if not recorded:
            self._record(APP_REMEMBER_REFUSED, reason)
        self._publish(
            "state",
            {
                "component": "memory",
                "status": "refused",
                "reason": _safe_name(reason),
                "refused": refused,
                "source": "tool",
            },
        )
        return f"refused: {reason}"

    def _forget_tool(self, record_id: object = "", **_ignored: Any) -> str:
        """The ``forget`` tool: the model's own way to drop a fact. Never raises.

        Decision 18 (``d8``). Archives through :meth:`RoomMemory.forget` —
        eidetic's own lifecycle, in Gwen's private store, deadline-bounded —
        and never deletes a byte. The id is what the model said, so it is
        untrusted: restricted to :func:`_safe_record_id`'s charset here and
        again inside memory, and refused with a fixed token otherwise.

        Returns a short structured line: ``ok: forgot <id>`` or ``refused:
        <reason>``. The prompt forbids the model from claiming it forgot
        anything unless it saw the ``ok``.
        """
        with self._lock:
            self._forget_tool_calls += 1
        if not isinstance(record_id, str):
            return self._refuse_forget("not-text")
        stripped = record_id.strip()
        if not stripped or _safe_record_id(stripped) != stripped:
            return self._refuse_forget("bad-id")
        try:
            result = self._memory.forget(stripped, visibility=PRIVATE)
        except Exception as exc:  # noqa: BLE001  # memory is a seam; a turn never dies on it
            self._record(APP_FORGET_REFUSED, f"store: {_describe(exc)}")
            return self._refuse_forget("store-failed", recorded=True)
        if not getattr(result, "ok", False):
            code = getattr(result, "code", None)
            reason = _FORGET_REFUSAL_BY_CODE.get(code)
            if reason is not None:
                # A fact about the store, not a fault in it: one ledger line,
                # the fixed token, and never the id the model supplied.
                return self._refuse_forget(reason)
            degradation = getattr(result, "degradation", None)
            if degradation is not None:
                self._fold("memory", degradation)
            return self._refuse_forget("store-refused", recorded=degradation is not None)
        with self._lock:
            self._forget_tool_written += 1
            written = self._forget_tool_written
        self._publish(
            "state",
            {
                "component": "memory",
                "status": "forgotten",
                "record_id": stripped,
                "forgotten": written,
                "source": "tool",
            },
        )
        return f"ok: forgot {stripped}"

    def _refuse_forget(self, reason: str, *, recorded: bool = False) -> str:
        """Count a forget refusal, say so on the bus, tell the model why. Never the id.

        *reason* is a fixed token from this method's vocabulary — ``not-text``,
        ``bad-id``, ``unknown-id``, ``already-archived``, ``store-failed``,
        ``store-refused`` — never anything the model supplied.
        """
        with self._lock:
            self._forget_tool_refused += 1
            refused = self._forget_tool_refused
        if not recorded:
            self._record(APP_FORGET_REFUSED, reason)
        self._publish(
            "state",
            {
                "component": "memory",
                "status": "refused",
                "reason": _safe_name(reason),
                "refused": refused,
                "source": "tool",
                "tool": FORGET_TOOL_NAME,
            },
        )
        return f"refused: {reason}"

    def _clock_line(self) -> str:
        """The prompt's clock line, or ``""`` — recorded — if the clock fails."""
        try:
            return clock_line(self._now())
        except Exception as exc:  # noqa: BLE001  # a turn never waits on a clock
            self._record(APP_CLOCK_FAILED, _describe(exc), once=True)
            return ""

    def _note_ask(self, session: Any, text: str) -> None:
        """Add the user turn and COUNT what the explicit-ask path did with it.

        ``Session.add_user`` already detects a spoken "remember that …" and
        writes it through :class:`~embodiment.memory.RoomMemory` (t11). What
        was missing was any way to see a ZERO: the operator asked Gwen to
        remember something, she said she would, and nothing downstream could
        show whether a record had been written, refused, deferred, or never
        detected at all. These counters are that view.
        """
        try:
            outcome = session.add_user(text)
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(APP_TURN_FAILED, f"add_user: {_describe(exc)}")
            return
        if outcome is None:
            self._note_missed_ask(text)
            return
        with self._lock:
            self._asks_detected += 1
            if getattr(outcome, "remembered", False):
                self._asks_remembered += 1
                status = "remembered"
            elif getattr(outcome, "deferred", False):
                self._asks_deferred += 1
                status = "deferred"
            else:
                self._asks_failed += 1
                status = "refused"
            counts = (self._asks_detected, self._asks_remembered, self._asks_failed)
        self._publish(
            "state",
            {
                "component": "memory",
                "status": f"ask-{status}",
                "asks_detected": counts[0],
                "remembered": counts[1],
                "remember_failed": counts[2],
            },
        )

    def _note_missed_ask(self, text: str) -> None:
        """An utterance that SOUNDS like an ask but matched no pattern.

        A heuristic, and only ever a heuristic: it decides nothing, writes
        nothing and refuses nothing — it exists so that a spoken ask the
        detector did not recognise is visible instead of silent, which is the
        exact failure the operator hit (he asked, she agreed, and nothing
        anywhere showed that no record had been written). Counting EVERY
        non-ask would be noise, so only an utterance carrying one of a few
        remember-stems is reported.
        """
        lowered = text.lower()
        if not any(stem in lowered for stem in _ASK_STEMS):
            return
        with self._lock:
            self._asks_missed += 1
            count = self._asks_missed
        self._publish(
            "state",
            {"component": "memory", "status": "ask-not-detected", "ask_not_detected": count},
        )

    def _recall(self, text: str) -> tuple[str, tuple[str, ...]]:
        """The ONE place recall reaches a prompt, bounded by its own deadline.

        Counted at every step, because the failure this replaces was a silent
        zero: live, Gwen was told where a key was, wrote the record, was asked
        about it after a restart and answered without it — and
        ``status()["recall"]`` said ``mode: lexical, calls: 1`` with no
        degradation, which is indistinguishable from a recall that found
        nothing because there was nothing to find. ``hits`` and ``rendered``
        are what tell those two apart.
        """
        started = self._clock()
        result = self._recall_once(text, self._config.recall_mode, self._config.recall_deadline)
        records = self._records_of(result)

        if not records and not _lexical_can_index(text):
            records = self._recall_blind_fallback(text, started)

        # Belt over braces (d8). ``continuity.recall`` applies eidetic's
        # lifecycle filter, verified by test — but a forgotten fact that comes
        # back in the prompt is the whole feature failing silently, so the
        # daemon drops an archived record itself and COUNTS it: a non-zero
        # count says the seam below stopped filtering.
        records, hidden = _drop_archived(records)

        rendered = self._render(records)
        rendered_ids = _rendered_ids(records) if rendered else ()
        with self._lock:
            self._recall_last_hits = len(records)
            self._recall_hits_total += len(records)
            self._recall_archived_hidden += hidden
            if not records:
                self._recall_empty_total += 1
            rendered_count = len(records) if rendered else 0
            self._recall_rendered_total += rendered_count
            counts = (len(records), rendered_count)
            self._recall_last_ids = rendered_ids
        self._publish(
            "state",
            {
                "component": "recall",
                "status": "searched" if counts[0] else "empty",
                "last_hits": counts[0],
                "rendered": counts[1],
                "archived_hidden": hidden,
            },
        )
        return rendered, rendered_ids

    def _recall_once(self, query: str, mode: str, deadline: float) -> Any:
        """One bounded call into memory. Counts and folds; never raises."""
        try:
            result = self._memory.recall(
                query,
                deadline=max(0.01, deadline),
                mode=mode,
                top_k=self._config.recall_top_k,
                visibility=PRIVATE,
            )
        except Exception as exc:  # noqa: BLE001  # memory is a seam; a turn never waits on it
            with self._lock:
                self._recall_errors += 1
            self._record(APP_RECALL_FAILED, _describe(exc))
            return None
        with self._lock:
            self._recall_calls += 1
            self._recall_mode = getattr(result, "mode", None)
        for degradation in getattr(result, "degradations", ()) or ():
            if getattr(degradation, "code", "") == MEMORY_DEADLINE_EXCEEDED:
                with self._lock:
                    self._recall_deadline_exceeded += 1
            self._fold("memory", degradation)
        return result

    def _recall_blind_fallback(self, text: str, started: float) -> list[Any]:
        """The lexical index cannot see this utterance. Say so, then try again.

        eidetic's keyword/BM25 tokeniser is ``[a-z0-9]+``
        (``eidetic/memory/scoring.py``), so a Hebrew utterance tokenises to
        the EMPTY list: no query terms, no document terms, no hits, ``ok``
        true and not one degradation anywhere. Gwen speaks Hebrew, so on this
        rig that is every recall she will ever make — the live miss was not a
        ranking problem, it was a search that could not see its own index.

        This is an upstream defect and not ours to patch, so the workaround is
        narrow and LOUD: recorded as :data:`APP_RECALL_LEXICAL_BLIND` the
        first time, then the longest words of the utterance are tried in
        ``exact`` mode, which matches by substring and therefore works in any
        script. Whatever is left of the recall deadline bounds it; nothing
        here is retried forever, and a turn still answers without memory
        rather than waiting for it.
        """
        self._record(
            APP_RECALL_LEXICAL_BLIND,
            "the lexical index cannot tokenise this script; falling back to exact",
            once=True,
        )
        for word in _fallback_terms(text):
            remaining = self._config.recall_deadline - (self._clock() - started)
            if remaining <= 0:
                break
            result = self._recall_once(word, MEMORY_MODE_EXACT, remaining)
            records = self._records_of(result)
            if records:
                with self._lock:
                    self._recall_fallback_hits += 1
                return records
        return []

    @staticmethod
    def _records_of(result: Any) -> list[Any]:
        return list(getattr(result, "records", []) or []) if result is not None else []

    def _render(self, records: list[Any]) -> str:
        """The ONE render. A render fault costs the memory, never the turn."""
        if not records:
            return ""
        try:
            return render_recalled(records)
        except Exception as exc:  # noqa: BLE001  # a render fault must not lose the turn
            with self._lock:
                self._recall_errors += 1
            self._record(APP_RECALL_FAILED, f"render: {_describe(exc)}")
            return ""

    def _window(self, session: Any) -> list[dict[str, str]]:
        if session is None:
            return []
        try:
            messages = session.messages()
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(APP_TURN_FAILED, f"window: {_describe(exc)}")
            return []
        return [m for m in messages if isinstance(m, dict)][:-1]

    def _ensure_session(self) -> Any:
        with self._lock:
            if self._session is not None:
                return self._session
        try:
            factory = self._session_factory or (
                lambda: Session(self._state, self._memory, added_by=self._config.added_by)
            )
            session = factory()
        except Exception as exc:  # noqa: BLE001  # a turn happens with or without a session
            self._record(APP_TURN_FAILED, f"session: {_describe(exc)}")
            return None
        with self._lock:
            self._session = session
        return session

    def _fold_voice(self, voice: Any) -> None:
        """Record every voice degradation this app has not already recorded."""
        records = list(getattr(voice, "degradations", ()) or ())
        with self._lock:
            start, self._voice_folded = self._voice_folded, len(records)
        for degradation in records[start:]:
            self._fold("voice", degradation)

    def _fold_session(self, session: Any) -> None:
        try:
            records = list(session.degradations)
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(APP_TURN_FAILED, f"session degradations: {_describe(exc)}")
            return
        with self._lock:
            start, self._session_folded = self._session_folded, len(records)
        for degradation in records[start:]:
            self._fold("session", degradation)

    # ── the threads ──────────────────────────────────────────────────────

    def _start_turn_thread(self) -> None:
        thread = threading.Thread(target=self._turn_worker, name="embodiment-turn", daemon=True)
        self._turn_thread = thread
        thread.start()

    def _turn_worker(self) -> None:
        while not self._worker_stop.is_set():
            try:
                heard = self._turn_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as exc:  # noqa: BLE001  # a queue fault must not kill the worker
                self._record(APP_TURN_FAILED, _describe(exc))
                continue
            self._run_heard(heard)

    def _stop_turn_thread(self, deadline: float) -> bool:
        thread = self._turn_thread
        if thread is None:
            return True
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _ensure_ears_session(self, ear: str, endpoint: Any) -> None:
        """Dial a session that declares THIS ear's rate. Never raises.

        Decision 15 (issue #85): the session's ``input_sample_rate`` is what
        the attached endpoint says it delivers — 16 kHz native from the host
        array, 24 kHz from a browser ear — and this module never assumes
        either. The rate is part of the session's own URL, so it cannot be
        changed under a live session: a handover to an ear with a different
        rate RE-DIALS, which is why the client arrives here as a factory
        rather than as an object built before any ear existed.
        """
        rate = self._endpoint_rate(ear, endpoint)
        if self._ears is not None and rate == self._ears_rate:
            return
        if self._ears is not None:
            self._stop_ears_session(self._config.shutdown_deadline * _EARS_STEP_FRACTION)
            self._ears_redials += 1
            self._record(
                APP_EARS_REDIALLED,
                f"{self._ears_rate} Hz -> {rate} Hz for ear {ear}",
            )
        self._start_ears_session(rate)

    def _endpoint_rate(self, ear: str, endpoint: Any) -> int:
        """What this endpoint says it delivers. Never assumed, never raised."""
        try:
            rate = int(endpoint.sample_rate)
        except Exception as exc:  # noqa: BLE001  # an endpoint without the property
            self._record(APP_EAR_RATE_UNKNOWN, f"{_safe_name(ear)}: {_describe(exc)}")
            return int(wire.INPUT_SAMPLE_RATE)
        if rate <= 0:
            self._record(APP_EAR_RATE_UNKNOWN, f"{_safe_name(ear)}: {rate}")
            return int(wire.INPUT_SAMPLE_RATE)
        return rate

    def _start_ears_session(self, rate: int) -> None:
        """Build a client for *rate* and put it on its own thread. Never raises."""
        try:
            self._ears = self._ears_factory(rate)
        except Exception as exc:  # noqa: BLE001  # a factory is a seam, not a promise
            self._ears = None
            self._record(APP_EARS_UNAVAILABLE, f"factory: {_describe(exc)}")
            return
        self._ears_rate = rate
        self._ears_sessions += 1
        self._ears_closed.clear()
        self._ears_close_done.clear()
        self._publish(
            "state",
            {"component": "ears", "status": "dialling", "input_sample_rate": rate},
        )
        thread = threading.Thread(target=self._ears_main, name="embodiment-ears", daemon=True)
        self._ears_thread = thread
        thread.start()

    def _stop_ears_session(self, deadline: float) -> bool:
        """Close the current session and reap its thread. Never raises."""
        stopped = self._stop_ears(max(0.05, deadline))
        self._ears_thread = None
        self._ears_loop = None
        return stopped

    def _ears_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._ears_loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._listen())
        except Exception as exc:  # noqa: BLE001  # the ear may die; the daemon may not
            self._record(APP_EARS_THREAD_FAILED, _describe(exc))
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001  # a loop that will not close is counted, not raised
                self._record(APP_EARS_THREAD_FAILED, "event loop would not close")
            self._ears_loop = None

    async def _listen(self) -> None:
        if self._ears is None:
            return
        try:
            connected = await self._ears.connect()
        except Exception as exc:  # noqa: BLE001  # the client promises False, not an exception
            self._record(APP_EARS_UNAVAILABLE, _describe(exc))
            return
        if self._stopping:
            # A stop that arrived while the handshake was in flight. Without
            # this the thread would go on to serve an event stream nobody is
            # going to close (found by a 2-in-25 flake on close(): the ears
            # step burned its whole slice joining a thread parked in
            # ``events()``), and the real client would leave its WebSocket
            # open behind it.
            await self._shut_ears_down()
            return
        if not connected:
            self._ears_connected = False
            self._record(APP_EARS_UNAVAILABLE, "the gateway did not give us a session")
            self._publish("state", {"component": "ears", "status": "unavailable"})
            return
        self._ears_connected = True
        self._publish("state", {"component": "ears", "status": "listening"})
        try:
            async for event in self._ears.events():
                if self._stopping:
                    break
                self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  # any transport fault ends the stream
            self._record(APP_EARS_STREAM_ENDED, _describe(exc))
        finally:
            self._ears_connected = False
            # Whatever ended the stream — a stop, a peer close, a transport
            # fault — the client is closed from inside its own loop, which is
            # the only thread that can close it gracefully.
            await self._shut_ears_down()
        if not self._stopping:
            self._record(APP_EARS_STREAM_ENDED, "the event stream ended; no reconnect in t15")
            self._publish("state", {"component": "ears", "status": "ended"})

    def _on_event(self, event: Any) -> None:
        """Classify ONE server event. Runs on the ears thread; never blocks on a turn."""
        try:
            if isinstance(event, wire.TranscriptionCompleted):
                self.submit_transcript(event.text)
            elif isinstance(event, wire.SpeechStarted):
                self._barge_in()
            elif isinstance(event, wire.SpeechStopped):
                # The start of t21's measurement, taken where it happens.
                at_ms = getattr(event, "at_ms", None)
                with self._lock:
                    self._pending_eos = (
                        self._clock(),
                        time.time(),
                        at_ms if isinstance(at_ms, int) else None,
                    )
            elif isinstance(event, wire.ServerError):
                self._record(APP_STT_ERROR, f"code={_safe_name(event.code)}")
            elif isinstance(event, wire.MalformedEvent):
                self._record(APP_STT_FRAME_MALFORMED, "a frame did not decode")
            elif isinstance(event, wire.SessionCreated):
                self._publish("state", {"component": "ears", "status": "session"})
            elif isinstance(event, wire.SessionClosed):
                self._record(APP_EARS_STREAM_ENDED, "the server closed the session")
        except Exception as exc:  # noqa: BLE001  # one bad event never ends the stream
            self._record(APP_EARS_THREAD_FAILED, _describe(exc))

    def _barge_in(self) -> None:
        """Speech onset while we are talking: stop our own speaker. We own barge-in.

        An ears-only session never receives ``response.interrupted`` and lobes
        accepts ``aec_mode`` without acting on it, so this is the whole of the
        daemon's barge-in: on ``speech_started`` during playback, stop our own
        output. Measured on the rig after t7 round 5: the endpoint's
        ``stop_playback`` (close the player's stdin, ``SIGKILL`` it) takes
        **1 ms** and discarded 52320 queued samples — it was 1.3 s before that
        round, when the player was asked to terminate politely. The voice's
        own bookkeeping (dropping the pacing buffer, counting the discarded
        samples) is in-memory and runs after it, inside the 200 ms bound
        :data:`embodiment.voice.BARGE_IN_BOUND_S` states.

        Stopping the speaker is only half of it. Observed live: the operator
        barged in, Gwen started the turn for what he said, he began speaking
        AGAIN while senses was still generating — and that second
        ``speech_started`` had nothing to stop, because nothing was playing
        yet. The reply then arrived and played over him. So a
        ``speech_started`` that lands while a turn is IN FLIGHT supersedes
        that turn: see :meth:`_supersede_turn_in_flight`.
        """
        self._supersede_turn_in_flight()
        voice, endpoint = self._voice, self._ear_endpoint
        speaking = bool(getattr(voice, "speaking", False))
        playing = False
        if endpoint is not None:
            try:
                playing = bool(endpoint.playing)
            except Exception as exc:  # noqa: BLE001  # an endpoint probe is not trusted
                self._record(APP_CAPTURE_FAILED, f"playing: {_describe(exc)}")
        if voice is None or not (speaking or playing):
            return

        def stop_speaking() -> bool:
            # Always True for the same reason as _reap_endpoints: a stop that
            # RAISED is recorded by _safely; this bound asks only whether it
            # RETURNED, and conflating the two would report a timeout for a
            # speaker that answered immediately with an error.
            self._safely(voice.on_speech_started, APP_TURN_FAILED, "barge-in")
            return True

        if not self._bounded(stop_speaking, BARGE_IN_STOP_BOUND_S):
            # The speaker did not stop in the time the VOICE says it needs.
            # Recorded and counted, and then we carry on: this runs on the
            # ears thread, and a thread parked on a device that will not
            # answer is a daemon that has stopped listening — a far worse
            # outcome than a reply that keeps playing for a moment. The turn
            # is already marked superseded, so the reply will not be spoken.
            with self._lock:
                self._barge_in_stop_timeouts += 1
            self._record(
                APP_BARGE_IN_STOP_TIMEOUT,
                f"the speaker did not stop inside {BARGE_IN_STOP_BOUND_S}s",
                once=True,
            )
        self._fold_voice(voice)

    def _supersede_turn_in_flight(self) -> None:
        """A turn whose reply must not be spoken, because the room moved on.

        Marks the turn that is currently between "transcript accepted" and
        "reply handed to the voice". When that reply comes back it is dropped
        rather than spoken, counted on ``status()["turns"]["superseded"]``,
        and published as a ``reply`` event carrying ``superseded: true`` so a
        dashboard can still show what she would have said.

        **Not undone.** If the commit that follows turns out to be empty —
        noise, breath, decision 14's seven-a-minute — the dropped reply is not
        resurrected: the operator's word is that a lost reply beats a late
        one, and an answer arriving after the room has moved on is the defect
        this exists to prevent, not a prize to be salvaged.
        """
        with self._lock:
            if self._turn_serial == 0 or self._turns_in_flight == 0:
                return
            if self._superseded_serial == self._turn_serial:
                return
            self._superseded_serial = self._turn_serial
            self._turns_superseded += 1
            count = self._turns_superseded
        self._publish("state", {"component": "turn", "status": "superseded", "superseded": count})

    async def _shut_ears_down(self, deadline: Optional[float] = None) -> None:
        """Close the ears from INSIDE their own loop. Idempotent; never raises.

        The ONE place ``ears.close`` is awaited. *deadline* is the bound the
        CLIENT is given, and it is the caller's business because the caller is
        the one waiting: :meth:`_stop_ears` passes the slice it will wait for,
        while the listening coroutine — which nobody is waiting on — passes
        the whole shutdown deadline. Measured on the rig before this was
        derived: the client was handed the full 5 s shutdown deadline while
        the outer wait was half the ears step's slice, so the outer timed out
        first and recorded ``app-ears-thread-failed: close: TimeoutError``
        against an ear that was closing perfectly well (CLAUDE.md lesson 1 —
        a clock sized against the wrong quantity becomes the measurement).

        :attr:`_ears_close_done` is set on the way out whichever path ran, so
        a waiter outside the loop never needs a future linked across it.
        """
        if self._ears_closed.is_set() or self._ears is None:
            return
        self._ears_closed.set()
        bound = deadline
        if bound is None:
            bound = self._ears_close_bound or self._config.shutdown_deadline
        try:
            report = await self._ears.close(max(0.05, float(bound)))
        except Exception as exc:  # noqa: BLE001  # the client promises a report, not silence
            self._record(APP_EARS_THREAD_FAILED, f"close: {_describe(exc)}")
        else:
            if getattr(report, "graceful", True) is False:
                # The client's own report is what says whether the handshake
                # completed — not our clock running out on it.
                self._record(APP_EARS_CLOSE_INCOMPLETE, "the close handshake did not complete")
        finally:
            self._ears_close_done.set()

    def _stop_ears(self, deadline: float) -> bool:
        """Tell the ear to stop and wait, bounded, for its thread. Never raises.

        The wait is on a plain :class:`threading.Event`, not on a future
        returned by ``run_coroutine_threadsafe``. That linkage is what put a
        20-line traceback into ``daemon.err`` on a live stop: cancelling the
        wrapper after its loop had closed made ``concurrent.futures`` call
        ``call_soon_threadsafe`` on a closed loop, and the resulting
        ``RuntimeError: Event loop is closed`` is printed by the futures
        machinery from a callback no caller can catch. Nothing is linked
        across the loop boundary now — the coroutine is created ON the loop
        thread, and the only thing crossing back is an event being set.

        Three further guards, each for a way this step was measured burning
        its whole slice for nothing:

        * a thread that has already finished needs nothing scheduled;
        * a loop that is not running never runs what is scheduled on it, so
          it is checked first and the schedule is skipped; and
        * the wait for the close takes only a share of the slice, so the join
          afterwards still has time to observe the thread finishing.
        """
        thread = self._ears_thread
        if thread is None or not thread.is_alive():
            return True
        loop = self._await_ears_loop(deadline)
        close_bound = max(0.05, deadline * _EARS_CLOSE_WAIT_SHARE)
        if (
            loop is not None
            and loop.is_running()
            and not loop.is_closed()
            and not self._ears_closed.is_set()
        ):
            if self._schedule_ears_close(loop, close_bound):
                # Strictly longer than what the client itself was given, so a
                # client answering inside its own bound always wins the race
                # against this wait.
                self._ears_close_done.wait(timeout=close_bound + _EARS_CLOSE_GRACE_S)
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _schedule_ears_close(self, loop: asyncio.AbstractEventLoop, bound: float) -> bool:
        """Ask the ears loop to close its client, from its own thread. Never raises.

        The coroutine is built inside the callback, on the loop's thread, so a
        loop that closes before the callback runs leaves no un-awaited
        coroutine behind and nothing to cancel.
        """

        def spawn() -> None:
            loop.create_task(self._shut_ears_down(bound))

        try:
            loop.call_soon_threadsafe(spawn)
        except RuntimeError:
            # The loop closed between the check and here, which means the
            # listener's own finally has already closed the ear.
            return False
        return True

    def _await_ears_loop(self, deadline: float) -> Optional[asyncio.AbstractEventLoop]:
        """The ears loop, waiting a slice of *deadline* for the thread to make one.

        ``start()`` immediately followed by ``close()`` used to find the loop
        absent, skip the close entirely, and then join a thread that was only
        just getting going — so the ear was never told to stop. A bounded
        wait closes that window from this side; :meth:`_listen`'s own
        ``_stopping`` check closes it from the other.
        """
        thread = self._ears_thread
        if thread is None or not thread.is_alive():
            return self._ears_loop
        limit = self._clock() + max(0.05, deadline) * _EARS_LOOP_WAIT_SHARE
        while self._ears_loop is None and self._clock() < limit:
            time.sleep(0.005)
        return self._ears_loop

    # ── the dashboard ────────────────────────────────────────────────────

    def controls(self) -> server_module.Controls:
        """The four control callables the dashboard server binds to."""
        return server_module.Controls(
            start_voice=self._control_start_voice,
            stop_voice=self._control_stop_voice,
            set_mute=self._control_set_mute,
            status=self.status,
        )

    def _control_start_voice(self) -> dict[str, Any]:
        if self._ear_name is not None:
            return {"ear": self._ear_name, "handover": None}
        self._attach_default_ear()
        return {"ear": self._ear_name, "handover": None}

    def _control_stop_voice(self) -> dict[str, Any]:
        handover = self.detach_ear()
        return {"ear": self._ear_name, "handover": handover.to_dict()}

    def _control_set_mute(self, muted: bool) -> dict[str, Any]:
        return self.set_mute(muted)

    def _start_server(self) -> None:
        """Start the dashboard, if one was handed in.

        The server is constructed with ``controls=app.controls()`` (see
        :func:`main`) rather than bound here — :class:`DashboardServer` takes
        its controls at construction and has no rebinding seam.
        """
        server = self._server
        if server is None or not self._config.http_enabled:
            return
        try:
            server.start()
        except Exception as exc:  # noqa: BLE001  # no dashboard is a degradation, not a crash
            self._record(APP_HTTP_UNAVAILABLE, _describe(exc))

    def _stop_server(self, deadline: float) -> bool:
        server = self._server
        if server is None:
            return True
        try:
            server.shutdown(max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001  # a server that will not stop is recorded
            self._record(APP_HTTP_UNAVAILABLE, f"shutdown: {_describe(exc)}")
            return False
        return True

    # ── the rest of shutdown ─────────────────────────────────────────────

    def _close_voice(self, deadline: float) -> bool:
        voice = self._voice
        if voice is None:
            return True
        self._fold_voice(voice)
        return self._safely(lambda: voice.close(max(0.05, deadline)), APP_TURN_FAILED, "voice")

    def _close_session(self, deadline: float) -> bool:
        """Close the session, which is where the end-of-session summary is written.

        The summary is attempted inside this step's share of the shutdown
        budget — a summariser is a model call, and a stop that waits on one
        without a bound is a stop that does not come back. Its ABSENCE is
        named rather than left to be inferred from a missing record:
        ``status()["memory"]["summary_skip_reason"]`` carries t11's own reason
        (``session-summary-not-provided`` when no summariser was wired at all,
        which is what a daemon built without a senses seam will report).
        """
        session = self._session
        if session is None:
            return True
        with self._lock:
            self._summary_attempted += 1
        try:
            report = session.close(self._scrubbing_summariser(), deadline=max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(APP_TURN_FAILED, f"session close: {_describe(exc)}")
            with self._lock:
                self._summary_skip_reason = "close-raised"
            return False
        landed = bool(getattr(report, "summary_landed", False))
        with self._lock:
            if landed:
                self._summary_written += 1
                self._summary_skip_reason = None
            else:
                self._summary_skip_reason = _safe_name(
                    getattr(report, "summary_skip_reason", None) or "unknown"
                )
        for degradation in getattr(report, "degradations", ()) or ():
            self._fold("session", degradation)
        return True

    def _close_memory(self, deadline: float) -> bool:
        try:
            report = self._memory.close(deadline=max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001  # memory is a seam
            self._record("app-memory-close-failed", _describe(exc))
            return False
        for degradation in getattr(report, "degradations", ()) or ():
            self._fold("memory", degradation)
        return not getattr(report, "unconfirmed", ())

    def _close_bus(self, deadline: float) -> bool:
        try:
            self._bus.close(max(0.05, deadline))
        except Exception:  # noqa: BLE001  # the bus is closing; there is nowhere left to publish
            with self._lock:
                self._publish_errors += 1
            return False
        return True

    def _log(self, event: str, **fields: Any) -> None:
        """One operational-log line. Never carries transcript text."""
        try:
            self._state.operational_log.write(event, **fields)
        except Exception:  # noqa: BLE001  # the log promises never to raise; count anyway
            with self._lock:
                self._ledger_errors += 1

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """A JSON-safe snapshot of every part. Never raises; never carries speech."""
        try:
            return self._status()
        except Exception as exc:  # noqa: BLE001  # a status probe must never raise
            self._record("app-status-failed", _describe(exc))
            return {"running": False, "detail": "status probe failed"}

    def _status(self) -> dict[str, Any]:
        with self._lock:
            counts = dict(self._degradation_counts)
            clients = {"count": self._clients, "remote": self._remote_clients}
            transcripts = {
                "received": self._transcripts_received,
                "empty_commits": self._empty_commits,
                "not_text": self._transcripts_not_text,
            }
            turns = {
                "completed": self._turns_completed,
                "in_flight": self._turns_in_flight,
                "superseded": self._turns_superseded,
                "barge_in_stop_timeouts": self._barge_in_stop_timeouts,
                # The last RECENT_TURNS measurements, newest last, so a reader
                # can compute a median and a p90 from status alone.
                "recent": list(self._recent_turns),
                "dropped": self._turns_dropped,
                "failed": self._turns_failed,
                "queued": self._turn_queue.qsize(),
            }
            audio = {
                "frames_captured": self._frames_captured,
                "frames_forwarded": self._frames_forwarded,
                "frames_from_stale_ear": self._stale_frames,
                "frames_dropped_no_session": self._frames_dropped_no_session,
                "stale_frame_tolerance": _STALE_FRAME_TOLERANCE,
            }
            recall_mode = self._recall_mode
            recall_calls = self._recall_calls
            publish_errors = self._publish_errors
            ledger_errors = self._ledger_errors
            session = self._session
        return {
            "running": self._started and not self._closed,
            "closed": self._closed,
            # The browser-ear contract t18 reads. Fixed at False/None in v1 —
            # this daemon starts no RemoteEndpoint — but present, so the web
            # app branches on a value rather than on a missing key.
            "realtime_ear_enabled": bool(self._config.realtime_ear_enabled),
            "realtime_ws_url": self._config.realtime_ws_url or None,
            "ear": {
                # ``active`` is the ear's NAME ("host", "browser", "null") or
                # None when nothing is attached — a string, never a bool. A
                # predicate wanting "is an ear attached" asks
                # ``active is not None``; the name is what distinguishes the
                # host array from a browser ear in the same field.
                "active": self._ear_name,
                "kind": (
                    _safe_name(type(self._ear_endpoint).__name__)
                    if self._ear_endpoint is not None
                    else None
                ),
                "degraded": _endpoint_degraded(self._ear_endpoint),
                "generation": self._generation,
                "handovers": self._handovers,
                "refusals": self._refusals,
                "preempt_policy": self._config.preempt_ear,
                # Mine, not the client's: ``ears`` below is the realtime
                # client's own report and this module never writes into it.
                "sample_rate": _endpoint_rate_or_none(self._ear_endpoint),
                # t7 round 7: the endpoint verifies that its child is really
                # linked to the node it asked for, rather than to whatever
                # the server picked. Surfaced here so a dashboard — and t21's
                # acceptance run — can assert BOTH before the first turn:
                # a playback stream on the wrong sink is Gwen talking to the
                # monitor, and a capture stream on the wrong source is Gwen
                # listening to it.
                **_target_verification(self._ear_endpoint),
                **_playback_conditions(self._ear_endpoint),
                "teardown_timeouts": self._teardown_timeouts,
                "unreaped_endpoints": len(self._unreaped_endpoints),
                "warmups": self._warmups,
                "warmup_failures": self._warmup_failures,
                "declared_sample_rate": self._ears_rate,
                "sessions": self._ears_sessions,
                "redials": self._ears_redials,
                "muted": self._muted(),
                "mute_intent": self._mute_intent,
                "endpoint": _probe(self._ear_endpoint),
            },
            "clients": clients,
            # What the EAR delivered. Separate from ``turns`` because an empty
            # commit is a transcript that never became one, and separate from
            # ``ears`` because that key is the realtime client's own report,
            # which this module never writes into.
            "transcripts": transcripts,
            "turns": turns,
            "audio": audio,
            "recall": {
                "mode": recall_mode,
                "calls": recall_calls,
                "hits_total": self._recall_hits_total,
                "last_hits": self._recall_last_hits,
                "rendered_total": self._recall_rendered_total,
                "empty_total": self._recall_empty_total,
                "deadline_exceeded": self._recall_deadline_exceeded,
                "errors": self._recall_errors,
                "lexical_fallback_hits": self._recall_fallback_hits,
                "archived_hidden_total": self._recall_archived_hidden,
                "last_rendered_ids": list(self._recall_last_ids),
                "deadline_s": self._config.recall_deadline,
                "configured_mode": self._config.recall_mode,
                "semantic": _semantic_available(self._memory),
            },
            "memory": {
                **_memory_status(self._memory),
                "remember_tool_calls": self._remember_tool_calls,
                "remember_tool_written": self._remember_tool_written,
                "remember_tool_refused": self._remember_tool_refused,
                "forget_tool_calls": self._forget_tool_calls,
                "forget_tool_written": self._forget_tool_written,
                "forget_tool_refused": self._forget_tool_refused,
                "asks_detected": self._asks_detected,
                "remembered": self._asks_remembered,
                "remember_failed": self._asks_failed,
                "remember_deferred": self._asks_deferred,
                "ask_not_detected": self._asks_missed,
                "summary_attempted": self._summary_attempted,
                "summary_written": self._summary_written,
                "summary_skip_reason": self._summary_skip_reason,
            },
            "session": _session_status(session),
            "clock": {"zone": CLOCK_ZONE_NAME, "zone_available": CLOCK_ZONE_AVAILABLE},
            "ears": _probe(self._ears),
            "voice": _probe(self._voice),
            "http": _http_status(self._server, self._config),
            "bus": {
                "degradation_counts": dict(getattr(self._bus, "degradation_counts", {}) or {}),
                "hook_errors": int(getattr(self._bus, "hook_errors", 0) or 0),
            },
            "state": self._state.status(),
            "degradations": counts,
            "replies_scrubbed": self._replies_scrubbed,
            "publish_errors": publish_errors,
            "ledger_errors": ledger_errors,
        }


# ── module-level helpers ─────────────────────────────────────────────────────


def _describe(exc: BaseException) -> str:
    """The ONE exception renderer: a class name, never a message."""
    return safe_reason.describe_exception(exc)


def _as_seam(complete: Callable[..., Any]) -> Callable[..., Any]:
    """Accept both seam shapes: ``seam(messages, tools=...)`` and ``f(messages)``."""

    def seam(messages: list[dict[str, Any]], tools: Any = None) -> Any:
        try:
            return complete(messages, tools=tools)
        except TypeError:
            return complete(messages)

    return seam


def _compose_prompt(
    base: str,
    window: list[dict[str, str]],
    recalled: str,
    tool_prompt: str = "",
    clock: str = "",
) -> str:
    """The system prompt for ONE turn: base, clock, tools, the window, then recall.

    Recall enters here and nowhere else — :func:`embodiment.memory.render_recalled`
    has already attributed and fenced it as data. It never enters the user text,
    because that text is the caller's own words, verbatim (the verbatim invariant).

    *clock* is :func:`clock_line`'s one line, or empty when the clock failed
    (recorded by the caller). It says what time it is, never who is speaking.
    """
    parts = [base]
    if clock:
        parts.append(clock)
    if tool_prompt:
        # Fixed text, identical for every caller and every identity, so the
        # absent-identity byte-identity rule is untouched: this says what the
        # model may DO, never who it is.
        parts.append(tool_prompt)
    if window:
        lines = [WINDOW_HEADER]
        for message in window:
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            lines.append(f"{role}: {content}")
        parts.append("\n".join(lines))
    if recalled:
        parts.append(recalled)
    return "\n\n".join(parts)


def _probe(subject: Any) -> Optional[dict[str, Any]]:
    """``subject.status()`` if it has one, else ``None``. Never raises."""
    if subject is None:
        return None
    try:
        probe = getattr(subject, "status", None)
        if not callable(probe):
            return None
        result = probe()
    except Exception:  # noqa: BLE001  # a sub-status that fails is reported as unavailable
        return {"unavailable": True}
    return result if isinstance(result, dict) else {"unavailable": True}


def _memory_status(memory: Any) -> dict[str, Any]:
    """The store counters ``CLAUDE.md`` requires a host to be able to see.

    ``store_root_is_symlink`` is a BOOL and the rest are counts, so the two
    are coerced separately: reporting a planted symlink as the integer ``1``
    (or, worse, as ``None`` because it failed an ``isinstance(int)`` check)
    would be a tampered store reported as an ordinary one.
    """
    counters = (
        "store_permission_failures",
        "store_symlinks_skipped",
        "store_non_files_skipped",
        "pending",
        "abandoned_dropped",
    )
    out: dict[str, Any] = {}
    for name in counters:
        try:
            value = getattr(memory, name, None)
        except Exception:  # noqa: BLE001  # a counter probe is not trusted either
            value = None
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
    try:
        flag = getattr(memory, "store_root_is_symlink", None)
    except Exception:  # noqa: BLE001  # a probe that fails cannot clear the store
        flag = None
    out["store_root_is_symlink"] = flag if isinstance(flag, bool) else None
    try:
        out["scope"] = str(getattr(memory, "scope", ""))
        out["data_dir"] = str(getattr(memory, "data_dir", ""))
    except Exception:  # noqa: BLE001  # a path probe that fails is reported as unknown
        out["scope"] = out.get("scope", "")
        out["data_dir"] = ""
    return out


def _endpoint_rate_or_none(endpoint: Any) -> Optional[int]:
    """What the endpoint says it delivers, for ``status``. Never raises.

    The catch is narrow on purpose: an endpoint with no ``sample_rate``, or
    one whose value is not a number, is honestly *unknown* and reports as
    ``None``. Anything else a property does is a fault rather than an absence,
    and belongs to :meth:`DaemonApp.status`'s own recorded catch — reporting
    it as "unknown" here would be exactly the silent degradation this package
    forbids.
    """
    if endpoint is None:
        return None
    try:
        rate = int(endpoint.sample_rate)
    except (AttributeError, TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def _target_verification(endpoint: Any) -> dict[str, Any]:
    """The endpoint's verdict on whether its streams reached the right node.

    Four fields, two questions, and the difference between them matters:

    * ``capture_target`` / ``playback_target`` — **was this target checked at
      all?** Always a plain bool. This is what ``status()["ear"]["endpoint"]``'s
      ``device`` cannot tell a reader: that field is the ALSA card index, which
      after t7 round 7 is no longer what reaches ``pw-record --target`` on the
      pipewire path. A reader wanting "is this ear pointed at something that
      was verified" reads these, and never has to interpret a device string.
    * ``capture_target_verified`` / ``playback_target_verified`` — **and did
      it land there?** ``True``/``False`` once asked; ``None`` when it was not
      asked at all (a browser ear, a :class:`NullEndpoint`, or a player that
      has not started). ``None`` is "not applicable", which is a different
      claim from ``False`` — "asked, and it is linked somewhere else".
    * ``capture_target_ambiguous_count`` / ``playback_target_ambiguous_count``
      — **how often the question could not be answered** (t7 round 10): a
      name match refused because the pipewire dump had not settled, which is
      what the acoustic self-test produces when the monitor's own stream runs
      beside Gwen's player. ``None`` from an endpoint that does not count
      them. A non-zero count beside a ``True`` verdict is still worth seeing:
      it says the answer took more than one look.

    The node NAME is never copied into any of them: a target is reported as a
    boolean or a count, and the name stays inside the endpoint that resolved
    it. Never raises.
    """
    probed = _probe(endpoint) or {}
    out: dict[str, Any] = {}
    for key in ("playback_target_verified", "capture_target_verified"):
        value = probed.get(key)
        verdict = value if isinstance(value, bool) else None
        out[key] = verdict
        out[key.replace("_verified", "")] = verdict is not None
    for key in ("playback_target_ambiguous_count", "capture_target_ambiguous_count"):
        count = probed.get(key)
        out[key] = count if isinstance(count, int) and not isinstance(count, bool) else None
    return out


def _drop_archived(records: list[Any]) -> tuple[list[Any], int]:
    """``(kept, hidden)``: every record whose ``lifecycle`` is archived, removed.

    Reads eidetic's own field and value (``continuity.LIFECYCLE_ARCHIVED``);
    a record that is not a mapping is kept for :func:`render_recalled` to
    render as explicitly empty, as before.
    """
    kept: list[Any] = []
    hidden = 0
    for record in records:
        if (
            isinstance(record, Mapping)
            and record.get("lifecycle") == memory_module.continuity.LIFECYCLE_ARCHIVED
        ):
            hidden += 1
            continue
        kept.append(record)
    return kept, hidden


def _rendered_ids(records: list[Any]) -> tuple[str, ...]:
    """The ids of the records that went into a prompt. Ids only, never text."""
    out: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        identifier = _safe_record_id(record.get("id"))
        if identifier:
            out.append(identifier)
    return tuple(out)


def _elapsed_ms(start: Optional[float], end: Optional[float]) -> Optional[float]:
    """Milliseconds between two monotonic readings, or ``None`` if either is absent.

    Negative is impossible from a monotonic clock and is reported as ``None``
    rather than as a small number: it would mean the two readings did not come
    from the same clock, and publishing a plausible-looking figure from that
    is worse than publishing nothing.
    """
    if start is None or end is None:
        return None
    delta = (end - start) * 1000.0
    return round(delta, 3) if delta >= 0 else None


def _safe_record_id(value: object) -> str:
    """A recalled record's id, restricted to the charset ids are allowed.

    A record id comes out of a store every agent on this host can write to, so
    it is untrusted text until it is restricted — the same rule the rest of
    this module applies to any id it reports.
    """
    return _safe_name(value)


def _env_flag(value: Optional[str]) -> bool:
    """An env var read as a flag. Only an explicit yes is a yes."""
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def parse_allowed_hosts(value: Optional[str]) -> tuple[str, ...]:
    """Split :data:`ENV_ALLOWED_HOSTS`. Order preserved, blanks and dupes dropped.

    Each entry is written the way an operator reads it off a browser's address
    bar — a name or address, optionally with a port. Nothing is resolved,
    looked up or guessed at: naming a tailnet address accepts that address and
    nothing else.
    """
    out: list[str] = []
    for item in str(value or "").split(","):
        host = item.strip()
        if host and host not in out:
            out.append(host)
    return tuple(out)


def guard_host_of(value: str) -> str:
    """The ``allowed_hosts`` form of an operator's entry: the hostname, no port.

    The guard compares a request's ``Host`` header **with the port removed**
    (``guard._hostname_of``), so an allow-list entry that keeps its port can
    never match anything — ``100.64.0.7:8823`` in the list, ``100.64.0.7`` on
    the wire. The ORIGIN check is the other way round: a browser sends the
    port in ``Origin``, so that form keeps it. Getting these two backwards is
    a refusal the operator would only discover on their phone, so the split
    is made here and pinned by a test against the real guard.
    """
    host = value.strip().lower()
    if host.startswith("["):
        closing = host.find("]")
        return host[: closing + 1] if closing != -1 else host
    return host.split(":", 1)[0]


def allowed_origins_for(hosts: Iterable[str]) -> tuple[str, ...]:
    """Both schemes for every allowed host: ``http://`` and ``https://``.

    Both, because the Host and the Origin do not have to agree about the
    scheme. A TLS terminator in front — ``tailscale serve``, a tunnel, a
    reverse proxy — presents ``https://<host>`` as the Origin on the
    dashboard's control POSTs while the ``Host`` header it forwards stays the
    same. Listing only the scheme the daemon itself speaks would refuse
    exactly the deployment that makes the dashboard usable off this box.
    """
    out: list[str] = []
    for host in hosts:
        cleaned = host.strip().lower().rstrip("/")
        if not cleaned:
            continue
        for scheme in ("http", "https"):
            origin = f"{scheme}://{cleaned}"
            if origin not in out:
                out.append(origin)
    return tuple(out)


def _lexical_can_index(text: str) -> bool:
    """Whether a lexical (BM25) search has anything to work with here.

    eidetic's keyword tokeniser keeps ASCII alphanumeric runs and nothing
    else, so an utterance with none of them produces no terms at all — and a
    search with no terms is not a search that found nothing, it is a search
    that never happened. Asked behaviourally rather than by copying the
    upstream regex, and guarded by a test that stocks a Hebrew store and
    checks a record comes back through the daemon's own path.
    """
    return any(ch.isascii() and ch.isalnum() for ch in text)


def _fallback_terms(text: str) -> list[str]:
    """The longest words of *text*, longest first, for a substring search."""
    words = [word.strip("?!.,;:\"'()[]{}<>") for word in text.split()]
    ranked = sorted({word for word in words if len(word) > 1}, key=len, reverse=True)
    return ranked[:_FALLBACK_TERMS]


def _playback_conditions(endpoint: Any) -> dict[str, Any]:
    """What the sink's own volume lane says (t7 round 8). Status only.

    Reported, never acted on: this module changes nobody's volume, and a sink
    the operator has turned down is a fact about the room rather than a fault
    in the daemon. ``None`` throughout for an endpoint that does not report
    these — a browser ear, a :class:`NullEndpoint`, or a non-pipewire backend
    — which is "cannot say", not "fine". Never raises.
    """
    probed = _probe(endpoint) or {}
    volume = probed.get("playback_volume")
    muted = probed.get("playback_muted_by_system")
    latency = probed.get("playback_latency_ms")
    return {
        "playback_volume": float(volume) if isinstance(volume, (int, float)) else None,
        "playback_muted_by_system": muted if isinstance(muted, bool) else None,
        "playback_latency_ms": latency if isinstance(latency, (int, float)) else None,
    }


def _http_status(server: Any, config: AppConfig) -> Optional[dict[str, Any]]:
    """The server's own status, passed through whole, plus the bind it was asked for.

    t16's status already carries ``bind``/``public_bind`` and round 4's
    control counters (``controls_inflight``, ``controls_timed_out``,
    ``controls_refused_busy``, ``control_timeout_s``), so nothing is copied
    field by field — a server that gains a counter gains it here for free.
    What only this module knows is how many extra hosts the guard was
    configured with. The host NAMES are not reported and the install secret
    never appears anywhere: a count answers "is the allow-list what I set?"
    without publishing the operator's tailnet address to every dashboard
    viewer.
    """
    probed = _probe(server)
    configured = {
        "configured_bind": config.bind,
        "bind_public": bool(config.bind_public),
        "allowed_hosts": len(config.allowed_hosts),
        # True when the browser will withhold what the cookie rule needs —
        # see the module docstring's "Reaching the dashboard from another
        # device". Not a fault, and not something this daemon can fix from
        # its side: it says "put TLS in front of me".
        "secure_context_required": not server_module.is_loopback_address(config.bind),
    }
    if probed is None:
        return {**configured, "running": False}
    return {**probed, **configured}


def _endpoint_degraded(endpoint: Any) -> Optional[bool]:
    """Whether the attached endpoint reports ANY degradation about itself."""
    if endpoint is None:
        return None
    probed = _probe(endpoint) or {}
    return any(
        isinstance(probed.get(key), dict) and probed[key].get("code")
        for key in ("degradation", "degradation_in", "degradation_out")
    )


def _semantic_available(memory: Any) -> bool:
    """Whether the LAST recall actually ran semantic.

    Not a claim about what the rig could do: the embedder is down on this rig,
    eidetic falls back to lexical SILENTLY (``CLAUDE.md``, C3), and this field
    exists so a host reads the mode that was actually in effect rather than
    the one that was asked for.
    """
    try:
        return bool(getattr(memory, "last_recall_mode", None) == "semantic")
    except Exception:  # noqa: BLE001  # a probe that fails means "we cannot claim it"
        return False


def _session_status(session: Any) -> Optional[dict[str, Any]]:
    """The session's own counters, INCLUDING its transcript log's.

    The transcript log is the wave-1 obligation carried into t15: a private,
    size-bounded, per-session file that the host must be able to see the
    state of — its path, whether it is persistent, what it has had to evict.
    Reporting the session without it made "no transcript was ever written" a
    fact nothing could show.
    """
    if session is None:
        return None
    try:
        log = getattr(session, "transcript", None)
        transcript = _probe(log)
        if transcript is not None:
            # t4's own counters say what the log has had to drop; the path
            # and persistence say whether there IS a log, which is the fact
            # the live runs needed and nothing reported.
            path = getattr(log, "path", None)
            transcript = {
                **transcript,
                "path": str(path) if path is not None else None,
                "persistent": bool(getattr(log, "persistent", False)),
                "max_bytes": int(getattr(log, "max_bytes", 0) or 0),
            }
        return {
            "turns_seen": int(session.turns_seen),
            "closed": bool(session.closed),
            "degradation_counts": dict(session.degradation_counts),
            "transcript": transcript,
        }
    except Exception:  # noqa: BLE001  # a session probe that fails is reported as unavailable
        return {"unavailable": True}


def main() -> DaemonApp:
    """Build the daemon from the environment. Zero arguments; never raises.

    This is :data:`embodiment.daemon.lifecycle.DEFAULT_TARGET`'s factory: the
    daemon process imports it, calls it, and hands the result to
    :class:`~embodiment.daemon.lifecycle.DaemonRunner`. Every part that cannot
    be built degrades to a working stand-in and is recorded, because a daemon
    that refuses to start is a daemon whose host learns nothing.
    """
    # The ONE place this package imports the host endpoint, and it is inside
    # ``main`` on purpose: ``embodiment.daemon.app`` is the daemon's
    # composition root, and the import-graph rule (t7 criterion 3) allows a
    # host import here and nowhere else — not at module scope, not in a helper
    # beside it, and not in a function nested inside this one. Every other
    # module in this package depends on the AudioEndpoint protocol only.
    from embodiment.audio.host import HostEndpoint

    state = DaemonState(resolve_state_dir())
    realtime = RealtimeConfig.from_env()
    secret = guard_module.load_or_create_install_secret(state.dir)
    if secret.code:
        state.ledger.append(secret.code, _safe_reason_text(secret.detail))
    bus = Bus(redact=tuple(s for s in (realtime.api_key, secret.secret) if s))
    config = AppConfig(
        gateway_url=realtime.gateway_url,
        api_key=realtime.api_key,
        bind=os.environ.get(ENV_HTTP_BIND) or AppConfig.bind,
        bind_public=_env_flag(os.environ.get(ENV_BIND_PUBLIC)),
        allowed_hosts=parse_allowed_hosts(os.environ.get(ENV_ALLOWED_HOSTS)),
    )

    data_dir = Path(state.dir) / "memory" if state.dir is not None else Path(".")
    memory = RoomMemory(data_dir, scope=config.memory_scope, added_by=config.added_by)
    tools = ToolRegistry()

    def seam(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
        return http_complete(
            messages,
            tools=tools,
            gateway_url=config.gateway_url,
            api_key=config.api_key,
            role=config.role,
            deadline=config.completion_deadline,
        )

    def summarise(messages: list[dict[str, Any]]) -> str:
        """The end-of-session summary seam: one senses call, its own prompt.

        Wired here because it was the missing half of the memory lane —
        ``Session.close`` writes a summary record only when it is GIVEN a
        summariser, and a daemon built without one reported
        ``session-summary-not-provided`` and wrote nothing, which is exactly
        what the live runs showed. Bounded by the close step that calls it.
        """
        reply = http_complete(
            [{"role": "system", "content": SUMMARY_PROMPT}, *messages],
            gateway_url=config.gateway_url,
            api_key=config.api_key,
            role=config.role,
            max_tokens=SUMMARY_MAX_TOKENS,
            deadline=config.session_summary_deadline,
        )
        return reply.content

    def voice_factory(endpoint: Any) -> Voice:
        return Voice(
            endpoint=endpoint,
            config=VoiceConfig(gateway_url=config.gateway_url, api_key=config.api_key),
            bus=bus,
            features=FeatureExtractor(),
            synthesize=http_synthesize,
        )

    app = DaemonApp(
        config=config,
        state=state,
        bus=bus,
        memory=memory,
        complete=bind_tools(seam, tools),
        tools=tools,
        ears_factory=lambda rate: RealtimeEars(replace(realtime, input_sample_rate=rate)),
        endpoint_factory=HostEndpoint,
        summarise=summarise,
        # The bus already redacts these on its way out; this is the other
        # direction — what the GATEWAY sends back.
        redact=tuple(s for s in (realtime.api_key, secret.secret) if s),
        voice_factory=voice_factory,
    )
    try:
        app._server = server_module.DashboardServer(
            config=server_module.ServerConfig(
                bind=server_module.resolve_bind(config.bind, bind_public=config.bind_public),
                port=config.port,
                bind_public=config.bind_public,
                redact=tuple(s for s in (config.api_key, secret.secret) if s),
            ),
            guard=guard_module.Guard(
                guard_module.GuardConfig(
                    install_secret=secret.secret,
                    allowed_hosts=guard_module.DEFAULT_ALLOWED_HOSTS
                    | frozenset(guard_host_of(host) for host in config.allowed_hosts),
                    allowed_origins=frozenset(allowed_origins_for(config.allowed_hosts)),
                ),
            ),
            bus=bus,
            controls=app.controls(),
        )
    except Exception as exc:  # noqa: BLE001  # no dashboard is a degradation, not a crash
        app._record(APP_BOOTSTRAP_DEGRADED, f"dashboard: {_describe(exc)}")
    return app
