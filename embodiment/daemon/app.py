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

What t16 (gwen-remembers-recordings) added
-------------------------------------------
* **Recall is off the voice turn's critical path.** The turn thread no longer
  waits on memory: :class:`~embodiment.daemon.recall_events.BackgroundRecall`
  starts the recall on its own job and asks for the reply at once. A memory
  that lands before the first sentence regenerates the reply with it; one that
  lands mid-reply regenerates only the unspoken remainder; one that lands after
  is at most one follow-up — OFF until t18 measures the window and threshold.
  The voice is handed ONE sentence at a time, so the dashboard's ``reply``
  events now arrive per sentence.
* **Typed turns** (the chat page, a transcribed clip) run the same
  orchestration with no speaker: :class:`~embodiment.daemon.text_turns.TextTurns`
  joins the same session; a late memory becomes one later ``reply`` event
  carrying the typed turn's ``turn_serial`` — never audio.
* **The drain worker thread.** :meth:`DaemonApp.pump` only kicks it; it runs
  one bounded :meth:`~embodiment.daemon.drain.Drain.drain_once` per kick and
  forwards the embedder's, the vector cache's and memory's own degradation
  lists to the ledger. Closed with a share BEFORE memory, after the summary.
* **Access.** :func:`main` reads the Access env (team domain, audience, email
  allow-list) and injects the verifier into the guard; status says only
  ``off`` / ``on`` / ``incomplete``.

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
import math
import os
import queue
import re
import threading
import time
import unicodedata
import urllib.request
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from embodiment import memory as memory_module
from embodiment import safe_reason, vectors
from embodiment.audio.endpoint import SAMPLE_RATE_HZ as PLAYBACK_RATE_HZ
from embodiment.audio.endpoint import NullEndpoint
from embodiment.audio.features import FeatureExtractor
from embodiment.bus import Bus, fold_degradation
from embodiment.contract import ModelResponse
from embodiment.daemon import drain as drain_module
from embodiment.daemon.lifecycle import DEFAULT_SHUTDOWN_DEADLINE, ENV_SHUTDOWN_DEADLINE
from embodiment.daemon.recall_events import (
    PURPOSE_FOLLOW_UP,
    PURPOSE_REMAINDER,
    PURPOSE_REPLY,
    PURPOSE_RESTART,
    BackgroundRecall,
    CompletionRequest,
    Recalled,
    RecallEventsConfig,
)
from embodiment.daemon.state import DaemonState, resolve_state_dir
from embodiment.daemon.text_turns import TextTurns, TextTurnsConfig
from embodiment.http import access as access_module
from embodiment.http import guard as guard_module
from embodiment.http import server as server_module
from embodiment.ingest.spool import Spool
from embodiment.library import tools as library_tools
from embodiment.library.client import LibraryClient, LibraryConfig, LibraryError
from embodiment.library.epub import extract as epub_extract
from embodiment.library.index import DocumentIndex
from embodiment.library.sync import RECOVERED as LIBRARY_RECOVERED
from embodiment.library.sync import LibrarySync
from embodiment.memory import PRIVATE, RoomMemory, SemanticRecall, render_recalled
from embodiment.perception import perceive
from embodiment.realtime import wire
from embodiment.realtime.client import RealtimeConfig, RealtimeEars
from embodiment.session import ASK_RECORD_TYPE, Session, default_ask_detector
from embodiment.tools import BOUND_REGISTRY_ATTR, ToolRegistry, bind_tools
from embodiment.turn import FALLBACK_TEXT, SYSTEM_PROMPT, TurnConfig, TurnResult
from embodiment.turn import turn as run_one_turn
from embodiment.voice import BARGE_IN_BOUND_S, Voice, VoiceConfig, http_synthesize, split_sentences

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
    "APP_FRAMES_REFUSED",
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
    "APP_RECALL_STORE_LARGE",
    "RECALL_RESULTS_DOC",
    "RECALL_EMBED_P90_S",
    "RECALL_SCORE_S_PER_RECORD",
    "RECALL_SLACK_S",
    "RECALL_DEADLINE_CAP_S",
    "RECALL_COMFORTABLE_RECORDS",
    "RECALL_P90_CEILING_RECORDS",
    "RECALL_STORE_WARNINGS",
    "derive_recall_deadline",
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
    "AppFactories",
    "AppCloseReport",
    "EarHandover",
    "DaemonApp",
    "http_complete",
    "ENV_HTTP_BIND",
    "ENV_BIND_PUBLIC",
    "ENV_ALLOWED_HOSTS",
    "ENV_PUBLIC_HOSTNAME",
    "APP_CLOSE_SHARE",
    "close_budget_for",
    "guard_host_of",
    "allowed_origins_for",
    "loopback_origin_hosts",
    "SUMMARY_PROMPT",
    "SUMMARY_MAX_TOKENS",
    "ENV_RECALL_MODE",
    "ENV_S2S_ROLE",
    "RECALL_MODE_SEMANTIC",
    "RECALL_MODE_KEYWORD",
    "RECALL_MODES",
    "ACCESS_MODE_OFF",
    "ACCESS_MODE_ON",
    "ACCESS_MODE_INCOMPLETE",
    "APP_RECALL_MODE_UNKNOWN",
    "APP_DRAIN_FAILED",
    "APP_VECTOR_CACHE_DEGRADED",
    "APP_TEXT_TURN_BUSY",
    "APP_TEXT_FOLLOW_UP_UNROUTED",
    "AppLanes",
    "chat_source",
    "drain_budget_for",
    "access_mode_of",
    "recall_mode_from_env",
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

#: How WIDE the ears step is, as a share of the shutdown budget. Named
#: because two places must agree on it: the step itself, and the bound the ear
#: is handed. It became a width rather than a position when the summary moved
#: to the front (``d9``) — the ear's own bound is derived from how much time
#: the step has, never from where in the close it happens to sit.
_EARS_STEP_FRACTION = 0.18

#: Where the ears step ENDS in the budget, after the summary and the endpoint.
_EARS_STEP_END = 0.81

#: The share of the close budget the end-of-session SUMMARY is allowed, and
#: it runs FIRST (decision 19 / ``d9``). At the new 4.0 s watchdog this is
#: 4.0 x 0.8 x 0.55 = 1.76 s for a step that used to be scheduled inside
#: ~0.24 s, and every step after it still gets more absolute time than it had
#: before, because the budget itself doubled. The summary is a model call and the
#: only step whose work cannot be resumed later — an ear, a voice, a server
#: and a bus all close again on the next stop, but the conversation that was
#: never summarised is gone. Running it last meant it ran with whatever the
#: other six steps left, which on a 2 s watchdog was ~0.24 s and a recorded
#: ``session-summary-timeout``. It is safe first because ``_stopping`` is set
#: before any step, so no new turn can start while it runs.
_SUMMARY_STEP_FRACTION = 0.55

#: The share of the RUNNER's watchdog bound that the app's whole close gets.
#: The watchdog (:data:`embodiment.daemon.lifecycle.DEFAULT_SHUTDOWN_DEADLINE`)
#: is the one clock; every step share in :meth:`DaemonApp.close` is a
#: fraction of the budget derived here, so the last step (the bus, at 1.0)
#: is scheduled strictly before the watchdog fires and the summary and the
#: memory close — the two that used to sit past it — land inside it. A
#: **judgement call**: a fifth left over covers the runner's own bookkeeping
#: after ``run`` returns (``shutdown``, the ledger, the pidfile) and the
#: ``_bounded`` join granularity, on this rig measured well under 50 ms.
APP_CLOSE_SHARE = 0.8


def close_budget_for(runner_deadline: object) -> float:
    """The app's close budget, derived from the runner's watchdog bound.

    Strictly below *runner_deadline* for any positive value; a value that is
    not a positive number degrades to the runner's default rather than to a
    budget nothing else agrees with. Never raises.
    """
    try:
        bound = float(runner_deadline)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        bound = float(DEFAULT_SHUTDOWN_DEADLINE)
    if not (math.isfinite(bound) and bound > 0):
        bound = float(DEFAULT_SHUTDOWN_DEADLINE)
    return max(0.05, bound * APP_CLOSE_SHARE)


#: How the ``start`` verb hands its HTTP flags to the daemon CHILD: ``start``
#: re-execs a fresh interpreter, so a flag parsed in the CLI reaches
#: :func:`main` only through the environment.
ENV_HTTP_BIND = "EMBODIMENT_HTTP_BIND"
ENV_BIND_PUBLIC = "EMBODIMENT_BIND_PUBLIC"
#: Comma-separated.
ENV_ALLOWED_HOSTS = "EMBODIMENT_ALLOWED_HOSTS"
#: The ONE hostname the guard treats as public: a request whose ``Host`` is
#: this name must carry a Cloudflare Access assertion, and the shipped
#: verifier refuses every one (``http-access-verifier-missing``). Unset, no
#: Host is public and nothing is asked — which is what a default install is.
#: This is how ``--public-hostname`` reaches :class:`GuardConfig`; before it
#: existed the documented refusal was unreachable (review finding 3).
ENV_PUBLIC_HOSTNAME = "EMBODIMENT_PUBLIC_HOSTNAME"
#: The recall mode, by name: ``semantic`` (the default — cached-vector recall
#: through the ``gemma2-embed`` role, t12) or ``keyword`` (the one-flag
#: rollback to keyword recall plus the d5 exact-substring fallback). Nothing
#: else is accepted: eidetic's own ``approximate``/``hybrid`` re-embed every
#: candidate per recall (s47) and are never asked for from here.
ENV_RECALL_MODE = "EMBODIMENT_RECALL_MODE"
#: The speech-to-speech role, by NAME, for the chat page's (disabled) button.
#: Never inferred from a model name; unset means no such role is configured.
#: Turning speech-to-speech on is not in this cycle — the name is reported only.
ENV_S2S_ROLE = "EMBODIMENT_S2S_ROLE"

#: The two recall modes this daemon will ask memory for.
RECALL_MODE_SEMANTIC = memory_module.SEMANTIC_MODE
RECALL_MODE_KEYWORD = memory_module.FAST_MODE
RECALL_MODES = (RECALL_MODE_SEMANTIC, RECALL_MODE_KEYWORD)

#: What ``status()["access"]["mode"]`` can say. ``off``: the Access env is
#: unset and the guard is byte-identical to before t7. ``on``: a verifier is
#: injected. ``incomplete``: the env is partial or malformed, and the injected
#: verifier refuses every assertion (recorded at start). Never the team
#: domain, the audience or an email.
ACCESS_MODE_OFF = "off"
ACCESS_MODE_ON = "on"
ACCESS_MODE_INCOMPLETE = "incomplete"

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
#: Deviation d1 (live 2026-10-24): with the library configured, typed turns never
#: called search_library (0 of 6) although senses calls it when asked directly. One
#: fixed line, added ONLY to a typed turn that carries a library collector - every
#: other prompt (voice, no library) stays byte-identical.
LIBRARY_TOOL_PROMPT = (
    "יש לך ספרייה של המשתמש: מסמכים, עיתונים יומיים ותמלולי הקלטות. "
    "לפני שאת עונה על שאלה על חדשות, על משהו שקרא או הקליט, או על מסמך — "
    "קראי לכלי search_library, וצטטי כל קטע שבו השתמשת כ־[n]. "
    "אל תמציאי מידע שאינו בקטעים שהכלי החזיר."
)

REMEMBER_TOOL_PROMPT = (
    "כשהמשתמש מבקש ממך לזכור, לשמור, לרשום או לא לשכוח משהו — בכל ניסוח, "
    "כולל «תזכרי את כל זה» או «שמרי את מה שאמרתי» — עלייך לקרוא לכלי remember "
    "עם העובדה, לפני שאת עונה. אל תאשרי שזכרת בלי לקרוא לכלי. "
    "כשהמשתמש מבקש ממך לשכוח משהו, קראי לכלי forget עם מזהה הרשומה — הוא מופיע "
    "כ־id= ברשומה שנזכרה למעלה, או חוזר מהכלי remember. "
    "אם אין מזהה כזה, קראי ל־forget עם fact — העובדה במילים שבהן נשמרה — ואל תמציאי מזהה. "
    "לעולם אל תגידי שזכרת או ששכחת משהו אלא אם הכלי החזיר ok. "
    "אם אין רשומה מתאימה לשכוח, אמרי שלא מצאת אותה. "
    "אחרי שהכלי החזיר ok, עני משפט אחד קצר בעברית שמאשר מה נשמר או נשכח — "
    "תמיד בעברית מדוברת, אף פעם לא באנגלית ולא במילה כמו ok."
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
    "Forget one stored fact the user asked you to forget. Call this whenever the "
    "user asks you to forget, drop or erase something you remembered — however "
    "they phrase it. Pass record_id when you have one: the id shown as id= on a "
    "recalled record, or the id remember returned. When nothing was recalled and "
    "you have no id, pass fact instead, in the words the fact was stored in. "
    "Never invent an id."
)
FORGET_ID_DESCRIPTION = "The record id to forget, exactly as shown (id=...)."
FORGET_FACT_DESCRIPTION = (
    "The fact to forget, in the words it was stored in — use this only when no "
    "record id is available, for example when nothing was recalled above."
)

#: How many records the text route may look at before it decides. Decision 20
#: (``d10``): the model gets an id only when something was recalled INTO the
#: prompt, and a spoken "forget that" often arrives with nothing recalled at
#: all — live, that produced a ``forget`` call refused ``unknown-id`` on a
#: perfectly ordinary ask. The daemon resolves the text itself instead of
#: letting the model invent an id: exactly one exact match is archived, none
#: is ``not-found``, several is ``ambiguous``. The daemon decides, so a
#: hostile recalled record still cannot talk the model into archiving
#: something else.
FORGET_TEXT_TOP_K = 10

#: What the daemon says itself when the reply that followed a *successful*
#: tool round has nothing this voice can speak. Found live during ``t21``
#: step 2: five of five asks were stored and the room heard silence, because
#: the model answered the tool result with two Latin letters — speakable by
#: :func:`embodiment.turn.is_speakable`'s definition (they are letters),
#: silence by this rig's (the synthesiser is Hebrew and returned no bytes).
#: One sentence per tool, fixed text rather than a second model call: the
#: turn has already spent its budget, and a confirmation that itself needs
#: the gateway to work is no floor at all.
TOOL_CONFIRMATION = {REMEMBER_TOOL_NAME: "שמרתי.", FORGET_TOOL_NAME: "שכחתי."}

#: The Unicode block whose presence makes a reply speakable *here*. The check
#: is deliberately this blunt: it asks whether the voice has anything to say,
#: not whether the sentence is good Hebrew.
_HEBREW_BLOCK = (0x0590, 0x05FF)


def _has_hebrew(text: str) -> bool:
    """True when *text* holds at least one Hebrew character."""
    return any(_HEBREW_BLOCK[0] <= ord(char) <= _HEBREW_BLOCK[1] for char in text)


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
#: The realtime client REFUSED a frame — ``send_audio`` returned False: the
#: session is gone, or its send queue is full. Counted apart from a forwarded
#: frame (review finding 12): a callback that counted every non-raising call
#: as forwarded made a deaf session look like a busy one.
APP_FRAMES_REFUSED = "app-frames-refused"
#: The model called ``remember`` with something that could not be stored.
APP_REMEMBER_REFUSED = "app-remember-refused"
#: The model called ``forget`` with an id that could not be archived: not an
#: id, unknown, already archived, or the store failed. Counted with a fixed
#: reason token; the id the model supplied is never in the record.
APP_FORGET_REFUSED = "app-forget-refused"
#: The reply that followed a tool round had nothing this voice can say, and
#: the daemon spoke the fixed sentence the tool result licenses instead. The
#: record names the tool and a character count — never the reply.
APP_TOOL_REPLY_UNSPOKEN = "app-tool-reply-unspoken"
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
#: The active store the last semantic recall scored grew past a measured
#: ceiling (:data:`RECALL_COMFORTABLE_RECORDS`, then
#: :data:`RECALL_P90_CEILING_RECORDS`). Recorded once per upward crossing, never
#: per recall; the reason carries the ceiling and the count, nothing else.
APP_RECALL_STORE_LARGE = "app-recall-store-large"

# ── the recall bound (t23): derived from the quantity it bounds ───────────────
#
# 0.25 s used to bound EVERY recall. Since t16 the spoken turn no longer waits
# on recall — it waits the head start (``recall_events.DEFAULT_HEAD_START_S``,
# still 0.25 s) and the recall runs in the background — so the 0.25 s bound on
# the recall itself had become a clock sized against the wrong quantity
# (lesson 1): at 600 active records it abandoned 6.5% of semantic recalls, at
# 800 every one. The recall now gets its own bound, derived from what a
# semantic recall measurably costs: one query embed plus a scoring pass that is
# linear in the active store. Every number below is from this file:

#: The published measurement every constant in this block cites.
RECALL_RESULTS_DOC = "docs/live-test-results/2026-10-23-recall-gemma2.md"

#: Seconds. The query embed alone, p90: 161.1 ms (median 156.4, max 163.7,
#: n = 30). A network quantity (the proxied ``gemma2-embed`` lane), independent
#: of the store.
RECALL_EMBED_P90_S = 0.1611

#: Seconds per active record. Scoring is linear in the store: median latency at
#: 1000 records (286.4 ms) minus the median embed (156.4 ms), over 1000 records
#: = 0.13 ms (enumeration about a fifth of it, the pure-Python cosine the rest).
#: An uncached record costs only its enumeration; it is counted at full cost.
RECALL_SCORE_S_PER_RECORD = (0.2864 - 0.1564) / 1000

#: Seconds above the model for the lane's tail. Covers every measured call's max
#: over embed-p90-plus-scoring: the largest excess is 129 ms (1000 records, max
#: 420.0 ms) — except ONE 950.8 ms call at 800 records that the doc attributes to
#: lane jitter (1 of 1104 calls). A **judgement call** on that margin.
RECALL_SLACK_S = 0.15

#: Seconds. The most any one recall may take, whatever the store. A recall that
#: lands after the first completion can no longer go into the reply — only the
#: late path (a follow-up, OFF by default, or dropped) — so a longer bound buys
#: the reply nothing: the cap sits under the measured ``senses`` completion
#: (median 3.983 s, n = 30). The chat route's bound (``server.DEFAULT_CHAT_TIMEOUT_S``)
#: runs the recall alongside a completion, never after it, so the cap adds nothing
#: serial to a typed turn. The formula only reaches it beyond ~20 000 active
#: records, twenty times the largest store measured. An operator-suggested value.
RECALL_DEADLINE_CAP_S = 3.0

#: Active records. The largest store measured with no call over 0.25 s (400:
#: max 246.8 ms, 0 of 184) — "comfortable" in the doc's own word.
RECALL_COMFORTABLE_RECORDS = 400
#: Active records. Past this the measured p90 crosses 0.25 s (600: p90 239.7 ms
#: with 12 of 184 calls over; 800: p90 287.6 ms, every call over).
RECALL_P90_CEILING_RECORDS = 600

#: The status warning per level (1 = past comfortable, 2 = past the p90
#: ceiling). Counts and ceilings only; one rig, one lane — never a forecast.
RECALL_STORE_WARNINGS = {
    1: (
        "active store past ~400 records, the largest size measured with no recall "
        "over 0.25 s (one rig); recall latency rises with the store and the derived "
        "deadline grows with it"
    ),
    2: (
        "active store past ~600 records, where recall p90 measured over 0.25 s "
        "(one rig); recalls take longer and a slow embed lane lowers this ceiling"
    ),
}


def derive_recall_deadline(active_records: Optional[int], *, semantic: bool = True) -> float:
    """The bound on ONE background recall, in seconds, from the store it scores.

    ``semantic``: :data:`RECALL_EMBED_P90_S` + *active_records* ×
    :data:`RECALL_SCORE_S_PER_RECORD` + :data:`RECALL_SLACK_S`, clamped to
    [:data:`~embodiment.memory.DEFAULT_DEADLINE`, :data:`RECALL_DEADLINE_CAP_S`].
    An UNKNOWN size (``None``, negative, not a number) gives the cap: no size has
    been observed, and a guessed one would censor the recall that could tell us.

    Keyword (``semantic=False``): :data:`~embodiment.memory.DEFAULT_DEADLINE`,
    today's bound — no embed, and the keyword arm measured 3.3 ms at worst
    (n = 144), two orders under it. Pure; never raises.
    """
    floor = float(memory_module.DEFAULT_DEADLINE)
    if not semantic:
        return floor
    if isinstance(active_records, bool) or not isinstance(active_records, (int, float)):
        return RECALL_DEADLINE_CAP_S
    if not math.isfinite(active_records) or active_records < 0:
        return RECALL_DEADLINE_CAP_S
    raw = RECALL_EMBED_P90_S + float(active_records) * RECALL_SCORE_S_PER_RECORD + RECALL_SLACK_S
    return min(RECALL_DEADLINE_CAP_S, max(floor, raw))


def _store_level(active_records: Optional[int]) -> int:
    """0 below the comfortable ceiling, 1 past it, 2 past the p90 ceiling."""
    if active_records is None:
        return 0
    if active_records > RECALL_P90_CEILING_RECORDS:
        return 2
    if active_records > RECALL_COMFORTABLE_RECORDS:
        return 1
    return 0


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
#: ``EMBODIMENT_RECALL_MODE`` named a mode this daemon does not run; the
#: default (semantic) is kept and the value itself is never recorded.
APP_RECALL_MODE_UNKNOWN = "app-recall-mode-unknown"
#: The drain worker's own fault (not a batch's): a pass raised past the
#: drain's own never-raise, or the worker thread could not start.
APP_DRAIN_FAILED = "app-drain-failed"
#: The vector cache said it could not read or persist itself (t4 keeps these
#: as a plain list; this is where they reach the ledger).
APP_VECTOR_CACHE_DEGRADED = "app-vector-cache-degraded"
#: A typed turn could not get the conversation within its bound — a voice
#: turn held it — and answered nothing rather than racing the session.
APP_TEXT_TURN_BUSY = "app-text-turn-busy"
#: A typed turn's late memory follow-up had no turn left to attach to.
APP_TEXT_FOLLOW_UP_UNROUTED = "app-text-follow-up-unrouted"

# ── the library lane (plan task t13) ──────────────────────────────────────────

#: The library (or its sync, or the document index) degraded; the reason is a
#: fixed code from the library modules, never library text or the key.
APP_LIBRARY_DEGRADED = "app-library-degraded"
#: The library is configured but could not be wired (bad URL, no state dir,
#: a model seam that was bound before the daemon saw it).
APP_LIBRARY_UNAVAILABLE = "app-library-unavailable"
#: A remember/forget the write guard refused (c31): library text was in the
#: turn's context and the operator's own turn did not ask for the write.
APP_LIBRARY_GUARDED_WRITE = "app-library-guarded-write"
#: The refusal token the model reads back from a guarded write.
LIBRARY_GUARDED_REASON = "library-guarded"
#: A library search whose query could not be embedded.
LIBRARY_EMBED_FAILED_CODE = library_tools.CODE_EMBED_FAILED

#: The document index: ``<state>/library/index.json`` (0700 dir, 0600 file — t3's
#: ``DocumentIndex._save``). Never inside an eidetic store (the index refuses one).
LIBRARY_DIRNAME = "library"
LIBRARY_INDEX_NAME = "index.json"
#: How often the catalog is listed again. A **judgement call**, not a measurement:
#: one GET per interval keeps the index within five minutes of the library, and
#: nothing waits on this clock (a page reads the index, never the library).
LIBRARY_SYNC_INTERVAL_S = 300.0
#: Bounds ONE sync tick's work before the thread checks its interval and stop
#: again. A **judgement call**: it bounds no request (the client bounds those) and
#: no turn (every chunk embed yields to the live-turn gate); a stop does not wait
#: on it either — closing ends the tick at the next chunk.
LIBRARY_TICK_BUDGET_S = 60.0
#: Hits one search route returns: the library tools' own cap.
LIBRARY_SEARCH_LIMIT = library_tools.MAX_LIMIT
#: The memory type the to-dos view lists, written by the culture-rules ingest.
TODO_RECORD_TYPE = "action_item"
TODO_SOURCE = "pocket"
#: The forget stems the write guard looks for in the operator's own turn.
_FORGET_STEMS: tuple[str, ...] = ("שכח", "forget")

#: The prompt section for a remainder, restart or follow-up completion: what
#: Gwen already said in this reply. Her own earlier words, in the system
#: prompt, never in the user text (the verbatim invariant). Hebrew because she
#: speaks Hebrew; **unmeasured** wording, fixed text identical for every
#: identity, so the absent-identity rule is untouched. Empty for a first reply,
#: which keeps that prompt byte-identical to what it was before t16.
SPOKEN_HEADER = "כבר אמרת בתשובה הזו:"
REMAINDER_INSTRUCTION = (
    "המשיכי את התשובה מהמקום שבו עצרת, בלי לחזור על מה שכבר נאמר, "
    "והשתמשי בזיכרון שלמטה אם הוא רלוונטי."
)
RESTART_INSTRUCTION = "הזיכרון שלמטה סותר את מה שאמרת. תקני את עצמך בקצרה."
FOLLOW_UP_INSTRUCTION = (
    "עלה בזיכרון משהו שקשור לשאלה האחרונה. אמרי אותו בקצרה, כהמשך לתשובה שכבר נתת."
)

#: How many rendered recalls are kept so a completion can name the ids (and,
#: for the chat page, the records) it actually saw. A memory bound for a
#: long-running daemon, not a measurement: a recall is looked up within one
#: turn, and sixteen covers a voice turn, a few concurrent typed turns and
#: their late arrivals.
_RECALL_INDEX_SIZE = 16
#: How many recent typed turns a late follow-up can still find.
_TEXT_TURN_MEMORY = 16
#: The longest record text one chat source carries to the page.
SOURCE_TEXT_MAX_CHARS = memory_module.DEFAULT_MAX_CHARS

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
    #: The bound on one background recall (voice and typed). ``None``, the
    #: default, DERIVES it per recall from the store the last semantic recall
    #: scored (:func:`derive_recall_deadline`, t23). A number pins it — tests,
    #: or an operator overriding the derivation. How long a spoken turn WAITS
    #: for memory is a different clock: the head start,
    #: ``RecallEventsConfig.head_start_s`` (0.25 s).
    recall_deadline: Optional[float] = None
    recall_top_k: int = 5
    #: :data:`RECALL_MODE_SEMANTIC` (the shipped mode) or
    #: :data:`RECALL_MODE_KEYWORD` (the one-flag rollback). The mode actually
    #: in effect — a semantic request the store demoted — is what
    #: ``status()["recall"]["mode"]`` reports, never this value.
    recall_mode: str = RECALL_MODE_SEMANTIC
    #: The minimum cosine similarity semantic recall returns (c52). Passed to
    #: :class:`~embodiment.memory.RoomMemory` by :func:`main`. **Provisionally
    #: calibrated** by t18 (0.715; ``memory.SIMILARITY_THRESHOLD_CALIBRATED`` stays
    #: False — small n, in-sample; see :data:`RECALL_RESULTS_DOC`).
    similarity_threshold: float = memory_module.DEFAULT_SIMILARITY_THRESHOLD
    #: The memory follow-up gates (t13, c60). Both ``None`` = follow-ups OFF:
    #: every late recall is dropped and counted. They are derived later from
    #: the measured recall-arrival distribution (t18), never guessed here —
    #: CLAUDE.md lesson 1. The threshold is in similarity units, so a keyword
    #: recall (no score) never clears it.
    follow_up_window_s: Optional[float] = None
    follow_up_threshold: Optional[float] = None
    #: ``off`` / ``on`` / ``incomplete`` — what :func:`main` found in the Access
    #: env. Reported in status; it decides nothing here (the guard does).
    access_mode: str = ACCESS_MODE_OFF
    #: The speech-to-speech role NAME, or ``None``. Reported for the chat
    #: page's button only; nothing in this daemon calls it.
    s2s_role: Optional[str] = None
    #: How often :meth:`DaemonApp.run` pumps the bus clock and the out-features.
    poll_interval_s: float = 0.5
    #: The whole-app shutdown bound. Derived from lifecycle's watchdog, never
    #: chosen beside it: :func:`main` reads the runner's value from
    #: :data:`~embodiment.daemon.lifecycle.ENV_SHUTDOWN_DEADLINE` and the
    #: default here is the same derivation from the runner's default.
    shutdown_deadline: float = close_budget_for(DEFAULT_SHUTDOWN_DEADLINE)
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
    #: The public hostname, when the dashboard sits behind a Cloudflare
    #: tunnel. ``None`` means no Host is public. Its Origin (both schemes) is
    #: allow-listed so the dashboard's own POSTs reach the assertion check
    #: and meet the DOCUMENTED refusal rather than ``http-refused-origin``.
    public_hostname: Optional[str] = None


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


@dataclass(eq=False)
class _EarsSession:
    """One dialled realtime session and everything the thread serving it owns.

    Captured at start and carried by the thread, so a thread that outlives
    a redial closes the client and the loop IT was given — never
    ``self._ears`` or ``self._ears_loop``, which by then name the NEW
    session (review finding 7: the old listener's ``finally`` closed the new
    client and cleared the new loop). ``closed`` and ``close_done`` are per
    session for the same reason: the app-level events were ``clear()``-ed by
    the new session, which re-armed the old thread's close.
    """

    client: Any
    rate: int
    closed: threading.Event = field(default_factory=threading.Event)
    close_done: threading.Event = field(default_factory=threading.Event)
    loop: Optional[asyncio.AbstractEventLoop] = None
    thread: Optional[threading.Thread] = None


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
    drain_stopped: bool = True
    backfill_stopped: bool = True
    library_stopped: bool = True
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
            "drain_stopped": self.drain_stopped,
            "backfill_stopped": self.backfill_stopped,
            "library_stopped": self.library_stopped,
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
            ("drain", self.drain_stopped),
            ("backfill", self.backfill_stopped),
            ("library", self.library_stopped),
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
        except (ValueError, RecursionError):
            # RecursionError too: a deeply nested ``[[[[…`` (a few tens of KB,
            # well inside one completion) is what the decoder raises for
            # depth, and it is not a ValueError. Left uncaught it escaped
            # http_complete and folded the whole turn as a failure; here the
            # arguments become {} and the tool refuses on its own vocabulary.
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return arguments


@dataclass(frozen=True)
class AppFactories:
    """The four things the daemon BUILDS rather than is handed.

    They travel together because they are one decision — what this daemon's
    audio and conversation are made of — and because a constructor that takes
    them one by one grows a parameter list nobody reads (SonarCloud ``S107``).
    Every one is optional except the ears: a daemon with no endpoint factory
    runs deaf and says so, which is a degradation, not a crash.
    """

    #: Called with the ear's sample rate; returns the realtime ears client.
    ears: Callable[[int], Any]
    #: Called with nothing; returns the audio endpoint to attach.
    endpoint: Optional[Callable[[], Any]] = None
    #: Called with an endpoint; returns the voice that speaks through it.
    voice: Optional[Callable[[Any], Any]] = None
    #: Called with nothing; returns the conversation session.
    session: Optional[Callable[[], Any]] = None


@dataclass(frozen=True)
class AppLanes:
    """The gwen-remembers-recordings lanes (t16). Every one optional; each degrades alone.

    Grouped for the same reason as :class:`AppFactories`: one decision — what
    feeds and serves Gwen's memory beyond the room — and a constructor that
    takes them one by one grows a parameter list nobody reads.
    """

    #: :class:`embodiment.daemon.drain.Drain` — the ingest spool's single
    #: writer. ``None``: no inbox is drained (and ``status()["ingest"]`` is None).
    drain: Optional[Any] = None
    #: The ``embodiment.vectors.EmbedderClient`` memory embeds through; held
    #: here only so its offline TRANSITIONS reach the ledger (t4 keeps them as a
    #: plain list). Memory owns the calls.
    embedder: Optional[Any] = None
    #: The ``VectorCache``, for the same reason: its degradations are a list.
    vector_cache: Optional[Any] = None
    #: The tts seam for the chat page's speak button (``text_turns.TtsFn``);
    #: ``None`` uses the gateway's ``/v1/audio/speech``.
    tts: Optional[Callable[..., bytes]] = None
    #: The stt seam for uploaded clips (``text_turns.SttFn``); ``None`` uses the
    #: gateway's ``/v1/audio/transcriptions``.
    stt: Optional[Callable[..., str]] = None
    #: How a background recall job is started (``recall_events`` ``spawn``);
    #: ``None`` is a daemon thread per recall. Tests inject a holder or a
    #: synchronous call to choose the moment a recall lands.
    recall_spawn: Optional[Callable[[Callable[[], None]], object]] = None
    #: :class:`LibraryLane` (t13) — the library client, document index and sync.
    #: ``None`` (``EMBODIMENT_LIBRARY_URL`` unset): no library tool is bound, the
    #: library routes answer 503 ``library-not-configured``, and every turn is
    #: exactly what it was before the library existed.
    library: Optional[Any] = None


class _LibraryStopped(Exception):
    """The daemon is closing: a chunk embed is abandoned, not failed."""


class _RememberedCatalog:
    """The library client as the sync sees it, keeping the last full catalog it listed.

    The catalog route answers from this — a page load never reaches the library —
    and ``gets`` stays the client's own counter (the c28 live check reads it).
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self.items: Optional[tuple[Any, ...]] = None

    @property
    def gets(self) -> int:
        return int(getattr(self._client, "gets", 0) or 0)

    def list_items(self, kind: Optional[str] = None, q: Optional[str] = None) -> list[Any]:
        items = self._client.list_items(kind=kind, q=q)
        if kind is None and q is None:
            self.items = tuple(items)
        return items

    def get_item(self, item_id: Any) -> Any:
        return self._client.get_item(item_id)

    def fetch_file(self, item_id: Any) -> bytes:
        return self._client.fetch_file(item_id)


@dataclass(eq=False)
class LibraryLane:
    """The library, wired: see :func:`build_library_lane`. Built only when configured."""

    client: Any
    index: Any
    sync: Any
    #: ``text -> vector`` for a QUERY (a search tool call or the search route).
    #: Never gated: it serves the live turn the gate protects.
    embed_query: Callable[[str], Any]
    #: Set when the daemon closes: the sync's clock reads past every deadline and
    #: the next chunk embed is abandoned, so the tick ends at the next chunk.
    stop: threading.Event
    #: The library search floor: memory's provisional 0.715, **uncalibrated for
    #: library text** (plan risk r6) — status says so.
    threshold: float = memory_module.DEFAULT_SIMILARITY_THRESHOLD
    interval_s: float = LIBRARY_SYNC_INTERVAL_S


def build_library_lane(
    client: Any,
    index_path: Any,
    *,
    embed_document: Callable[[str], Any],
    embed_query: Callable[[str], Any],
    gate: Callable[[], bool],
    extract: Callable[[bytes], Any] = epub_extract,
    threshold: float = memory_module.DEFAULT_SIMILARITY_THRESHOLD,
) -> LibraryLane:
    """The client, the index and the sync, wired as t10 asks.

    The index embeds every chunk through ``sync.gate_embed`` so a chunk waits
    while *gate* (a live turn) is held — not only the gap between items. The two
    are mutually referential (the sync needs the index, the index's embedder is
    the sync's), so the index's embedder is bound one line later. Raises only what
    :class:`DocumentIndex` raises for a path inside an eidetic store.
    """
    stop = threading.Event()
    catalog = _RememberedCatalog(client)
    gated: list[Callable[[str], Any]] = []
    index = DocumentIndex(
        index_path, embed=lambda text: gated[0](text), model_tag=vectors.EMBED_ROLE
    )
    sync = LibrarySync(
        catalog,
        index,
        extract=extract,
        clock=lambda: math.inf if stop.is_set() else time.time(),
        budget=LIBRARY_TICK_BUDGET_S,
        # Never held while stopping: a held gate would park the embed below
        # instead of letting it abandon the chunk.
        gate=lambda: not stop.is_set() and bool(gate()),
    )

    def embed(text: str) -> Any:
        if stop.is_set():
            raise _LibraryStopped()
        return embed_document(text)

    gated.append(sync.gate_embed(embed))
    return LibraryLane(
        client=catalog,
        index=index,
        sync=sync,
        embed_query=embed_query,
        stop=stop,
        threshold=threshold,
    )


@dataclass(eq=False)
class _VoiceTurn:
    """What the background-recall seams need to know about the voice turn in flight.

    Only the turn thread writes it (the reply, remainder and restart
    completions run there, inside :meth:`BackgroundRecall.run_turn`); a
    follow-up runs on a recall thread and never touches it.
    """

    serial: int
    thread: int
    query: str
    window: list[dict[str, str]]
    clock: str
    result: Optional[TurnResult] = None
    first_text: str = ""
    final_text: str = ""
    tool_round: bool = False
    steps: int = 0
    reply_text_at: Optional[float] = None
    first_play_at: Optional[float] = None
    voiced: bool = False
    handed: bool = False
    no_voice_recorded: bool = False
    rendered_ids: tuple[str, ...] = ()


@dataclass(eq=False)
class _TextTurn:
    """One typed turn, from the chat page or a transcribed clip."""

    serial: int
    thread: int
    query: str
    window: list[dict[str, str]]
    clock: str
    replying: bool = True
    result: Optional[TurnResult] = None
    first_text: str = ""
    final_text: str = ""
    tool_round: bool = False
    records: tuple[Any, ...] = ()
    follow_up_records: tuple[Any, ...] = ()
    #: This turn's library citations (t13), or ``None`` with no library. Built
    #: per turn, so the tools it backs number ``[n]`` from 1 for this reply only.
    collector: Optional[Any] = None


@dataclass(frozen=True)
class _RecallSeen:
    """One background recall's outcome, kept so a completion can name what it saw."""

    rendered: str = ""
    ids: tuple[str, ...] = ()
    records: tuple[Any, ...] = ()
    relevance: Optional[float] = None


class DaemonApp:
    """The wiring: one ear, one turn at a time, everything published."""

    def __init__(
        self,
        *,
        state: DaemonState,
        bus: Any,
        memory: Any,
        complete: Callable[..., Any],
        factories: AppFactories,
        config: Optional[AppConfig] = None,
        summarise: Optional[Callable[..., Any]] = None,
        redact: Iterable[str] = (),
        server: Any = None,
        tools: Optional[ToolRegistry] = None,
        clock: Callable[[], float] = time.monotonic,
        now: Optional[Callable[[], datetime]] = None,
        lanes: Optional[AppLanes] = None,
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
                        "record_id": {"type": "string", "description": FORGET_ID_DESCRIPTION},
                        "fact": {"type": "string", "description": FORGET_FACT_DESCRIPTION},
                    },
                    # Neither is required on its own (d10): an id when one was
                    # recalled, the fact's own words when none was.
                    "required": [],
                },
                self._forget_tool,
                description=FORGET_TOOL_DESCRIPTION,
            )
        prebound = getattr(complete, BOUND_REGISTRY_ATTR, None) is self._tools
        #: The schema-aware seam, kept so a typed turn can bind its OWN registry
        #: (memory tools + that turn's library tools, t13). ``None`` when the
        #: caller handed a callable already bound: its tools cannot be widened.
        self._seam: Optional[Callable[..., Any]] = None if prebound else _as_seam(complete)
        self._complete = complete if prebound else bind_tools(self._seam, self._tools)
        self._ears_factory = factories.ears
        self._ears: Any = None
        self._ears_rate: Optional[int] = None
        self._ears_sessions = 0
        self._ears_redials = 0
        self._endpoint_factory = factories.endpoint
        self._voice_factory = factories.voice or (lambda endpoint: None)
        self._session_factory = factories.session
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
        self._frames_refused = 0
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
        self._tool_replies_answered = 0
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
        #: The active records the last semantic recall scored (t23), or None
        #: when unknown: never observed, or forgotten after a missed deadline.
        self._active_records: Optional[int] = None
        self._store_level = 0
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
        self._ears_session: Optional[_EarsSession] = None
        self._ears_connected = False
        self._ears_close_bound: Optional[float] = None
        self._worker_stop = threading.Event()
        self._init_lanes(lanes or AppLanes())

    def _init_lanes(self, lanes: AppLanes) -> None:
        """The t16 lanes: background recall, typed turns, the drain worker.

        Split out of ``__init__`` only to keep that readable; it runs once, last,
        because every seam it builds closes over state set above.
        """
        self._lanes = lanes
        self._drain = lanes.drain
        #: The ONE conversation: a voice turn and a typed turn never interleave
        #: their session writes or their tool-round accounting. :class:`Session`
        #: holds no lock of its own, and both kinds of turn now reach it.
        self._conversation = threading.RLock()
        self._tls = threading.local()
        self._voice_turn: Optional[_VoiceTurn] = None
        self._floor_taken = False
        self._recall_seen: "OrderedDict[str, _RecallSeen]" = OrderedDict()
        self._text_turns_recent: deque[_TextTurn] = deque(maxlen=_TEXT_TURN_MEMORY)
        self._text_serial = 0
        self._text_turns_run = 0
        self._text_follow_ups = 0
        self._text_busy = 0

        events_config = RecallEventsConfig(
            follow_up_window_s=self._config.follow_up_window_s,
            follow_up_threshold=self._config.follow_up_threshold,
        )
        self._recall_events = BackgroundRecall(
            recall=self._background_recall,
            complete=self._voice_complete,
            speak=self._voice_speak,
            stop_playback=self._voice_stop_playback,
            floor_held=self._floor_held,
            clock=self._clock,
            config=events_config,
            record=self._record_recall_event,
            spawn=lanes.recall_spawn,
        )
        # The typed turn runs the same orchestration with no speaker at all: its
        # "voice" collects the reply for the page, nobody can hold its floor,
        # and a whole message is one "sentence" — so a memory that lands after
        # the answer is ONE later chat message on the stream (c64), never audio.
        self._text_recall = BackgroundRecall(
            recall=self._background_recall,
            complete=self._text_complete,
            speak=self._text_speak,
            stop_playback=_no_playback,
            floor_held=_nobody_holds_the_page,
            clock=self._clock,
            config=events_config,
            record=self._record_recall_event,
            spawn=lanes.recall_spawn,
            split=_whole_message,
        )
        self._text_turns = TextTurns(
            run_text_turn=self._run_text_turn,
            session=self._locked_session,
            tts=lanes.tts,
            stt=lanes.stt,
            config=TextTurnsConfig(
                gateway_url=self._config.gateway_url,
                api_key=self._config.api_key,
                language=self._config.language,
            ),
            record=lambda code, reason: self._record(code, reason, source="text"),
            clock=self._clock,
            hold_conversation=self._hold_conversation,
            release_conversation=self._release_conversation,
        )

        self._drain_kick = threading.Event()
        self._drain_stop = threading.Event()
        self._drain_thread: Optional[threading.Thread] = None
        self._drain_kicks = 0
        self._drain_passes = 0
        self._drain_failures = 0
        self._drain_last_report: Optional[dict[str, Any]] = None
        self._embedder_seen = 0
        self._cache_seen = 0
        self._init_library(lanes.library)

    def _init_library(self, lane: Optional[LibraryLane]) -> None:
        """The library lane's state (t13). With no lane every counter stays zero."""
        self._library = lane
        self._library_thread: Optional[threading.Thread] = None
        self._library_ticks = 0
        self._library_tool_calls: dict[str, int] = {}
        self._library_tick_failures = 0
        self._library_seen = (0, 0)
        self._library_guarded_writes = 0
        self._typed_in_flight = 0
        if lane is not None and self._seam is None:
            self._record(
                APP_LIBRARY_UNAVAILABLE,
                "the model seam was bound before the daemon saw it; no library tool can be bound",
            )

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

    def _fold(self, source: str, record: Any, *, once: bool = False) -> None:
        """Record a sibling module's own degradation, keeping its own code."""
        folded = fold_degradation(record, source=source)
        self._record(folded["code"], folded["reason"], source=folded["source"], once=once)

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
        self._start_drain_worker()
        self._start_backfill()
        self._start_library_sync()
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
        """One tick of the daemon's own clock: out-features, the drain, the bus clock.

        The drain pass is SCHEDULED here, never run here (plan risk r7):
        ``forget_source`` and ``Spool.pending`` can overrun a 0.25 s budget, and
        a heartbeat that waits on them is a bus clock that slips. ``pump`` sets
        one event; the drain worker wakes on it and runs ONE
        :meth:`~embodiment.daemon.drain.Drain.drain_once` bounded by
        :func:`~embodiment.daemon.drain.budget_for` of the poll interval — so
        the inbox is drained at the heartbeat, within a budget, and ``pump``
        itself costs an ``Event.set``. A kick that lands while a pass is still
        running is coalesced into the next pass, not queued.
        """
        self._drain_out_features()
        self._kick_drain()
        try:
            self._bus.tick(self._clock())
        except Exception as exc:  # noqa: BLE001  # an injected bus is not trusted
            self._record(APP_PUBLISH_FAILED, f"tick: {_describe(exc)}")

    def shutdown(self, deadline: float = DEFAULT_SHUTDOWN_DEADLINE) -> AppCloseReport:
        """The name :class:`embodiment.daemon.lifecycle.DaemonRunner` calls.

        *deadline* is the RUNNER's watchdog bound, so the close budget is
        derived below it here exactly as :func:`main` derives the config's —
        one clock, whichever path reaches ``close`` first.
        """
        return self.close(deadline=close_budget_for(deadline))

    def close(self, deadline: float = 5.0) -> AppCloseReport:
        """Stop everything within *deadline*, reporting what is unfinished.

        Idempotent and never raises. The order is deliberate. ``_stopping``
        is set before any step, so no new turn can start once close begins —
        which is what lets the **session summary run first** (``d9``): it is a
        model call, and it is the only step whose work is lost if it is
        skipped, because every other part closes again on the next stop. Then
        the ear (nothing new arrives), the ears session, the turn worker, the
        parts a turn depends on, and the bus last — so every degradation
        recorded on the way out is still published.
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
        if self._library is not None:
            # Now, not at its step: the tick winds down (at its next chunk) while
            # the steps before it run, so its join below rarely waits at all.
            self._library.stop.set()

        def step(fraction: float, call: Callable[[float], bool]) -> bool:
            """One shutdown step, under the slice of the budget it is allowed."""
            share = self._share(started, budget, fraction)
            return self._bounded(lambda: call(share), share)

        # FIRST, and with its own share: the one step whose work is lost if it
        # does not happen (d9). Everything below closes again on the next stop.
        session_closed = step(_SUMMARY_STEP_FRACTION, self._close_session)
        endpoint_closed = step(
            0.63,
            lambda d: self.detach_ear(publish=False, deadline=d).attached is False
            and self._reap_endpoints(d),
        )
        ears_stopped = step(_EARS_STEP_END, self._stop_ears)
        turn_stopped = step(0.87, self._stop_turn_thread)
        abandoned = self._turn_queue.qsize()
        # Recall jobs in flight are READS: a late one is dropped as closed and
        # counted, and waiting for it is best effort — it is not "unfinished".
        step(0.89, self._settle_recalls)
        voice_closed = step(0.92, self._close_voice)
        server_stopped = step(0.95, self._stop_server)
        library_stopped = step(0.955, self._stop_library)
        # The drain's share sits BEFORE memory closes, so a batch the worker
        # was part-way through (or one enqueued just before the stop) gets one
        # last bounded pass while the store still takes writes; whatever does
        # not fit stays in the inbox and is applied on the next start.
        drain_stopped = step(0.965, self._stop_drain)
        backfill_stopped = step(0.975, self._stop_backfill)
        memory_closed = step(0.985, self._close_memory)

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
            drain_stopped=drain_stopped,
            backfill_stopped=backfill_stopped,
            library_stopped=library_stopped,
            elapsed_s=self._clock() - started,
        )
        with self._lock:
            self._close_report = report
        if report.unfinished:
            self._record(APP_SHUTDOWN_INCOMPLETE, "unfinished: " + ",".join(report.unfinished))
        self._log("stopped", unfinished=list(report.unfinished))
        return report

    def _bounded(self, call: Callable[[], Any], deadline: float) -> bool:
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
                # Completing IS the success. A step with no verdict of its own
                # returns nothing and counts as done; only an explicit False
                # (a step that really did fail) reads as failure. Before this,
                # such a step had to end in `return True`, a sentinel that says
                # nothing and that every reader had to have explained to them.
                box.append(call() is not False)
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

    def detach_ear(self, *, publish: bool = True, deadline: Optional[float] = None) -> EarHandover:
        """Drop the active ear, whichever it is. Idempotent; never raises."""
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

        def work() -> None:
            # No verdict of its own: every sub-call below is folded through
            # _safely, which records the failure and moves on. The only way
            # this step reads as failure is _bounded's timeout, which the
            # caller counts and records below.
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
                return
            self._safely(endpoint.stop_capture, APP_EAR_DETACH_FAILED, ear)
            self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
            self._safely(lambda: endpoint.close(TEARDOWN_DEADLINE_S), APP_EAR_DETACH_FAILED, ear)

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

            def close_one(ep: Any = endpoint) -> None:
                # No verdict: whether the close RAISED is recorded by _safely,
                # and is a different fact from whether it RETURNED. Only the
                # second one is what this bound is asking about.
                self._safely(lambda: ep.close(share), APP_EAR_DETACH_FAILED, "unreaped")

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
                accepted = ears.send_audio(pcm)
            except Exception as exc:  # noqa: BLE001  # the ears client is a seam
                self._record(APP_CAPTURE_FAILED, _describe(exc), once=True)
            else:
                # The client's own answer: False is a frame that went nowhere,
                # and it is counted as such, not as forwarded.
                if accepted is False:
                    with self._lock:
                        self._frames_refused += 1
                    self._record(
                        APP_FRAMES_REFUSED,
                        "the realtime client refused a frame (no session, or its queue is full)",
                        once=True,
                    )
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
        """One voice turn: the reply starts at once; memory arrives as an event (t16).

        Recall no longer sits in front of the model. :class:`BackgroundRecall`
        starts it on its own job and asks for the reply immediately; when the
        memory lands it is taken up at the next sentence boundary — a reply
        regenerated with it before anything was spoken, a remainder generated
        for what is still unsaid, or (follow-ups configured) one follow-up after
        the reply. ``self._conversation`` keeps a typed turn from interleaving
        with this one's session writes.
        """
        with self._conversation:
            return self._run_voice_turn(text, serial, timing)

    def _run_voice_turn(self, text: str, serial: int, timing: TurnTiming) -> TurnResult:
        self._publish("turn", {"phase": "heard", "step_count": 0})
        packet, record = perceive(text)
        spoken_in = packet.original if isinstance(packet.original, str) else text
        if getattr(record, "degraded", False):
            self._record("app-perception-degraded", "intake degraded")
        self._publish("transcript", {"role": "user", "text": spoken_in})

        session = self._ensure_session()
        if session is not None:
            self._note_ask(session, spoken_in)

        turn = _VoiceTurn(
            serial=serial,
            thread=threading.get_ident(),
            query=spoken_in,
            window=self._window(session),
            clock=self._clock_line(),
        )
        with self._lock:
            self._voice_turn = turn
        self._publish("turn", {"phase": "thinking", "step_count": 0})
        try:
            outcome = self._recall_events.run_turn(spoken_in)
        finally:
            with self._lock:
                if self._voice_turn is turn:
                    self._voice_turn = None
        # No first completion came back (the turn path raised; BackgroundRecall
        # recorded it and voiced the never-silent fallback): a FAILED turn.
        failed = turn.result is None
        with self._lock:
            if failed:
                self._turns_failed += 1
            else:
                self._turns_completed += 1

        final = turn.final_text or " ".join(outcome.spoken) or FALLBACK_TEXT
        result = turn.result or TurnResult(spoken=final)
        result = replace(result, spoken=final, steps=max(result.steps, turn.steps))
        superseded = self._is_superseded(serial)
        if superseded and not turn.handed:
            # Dropped before a word reached the voice: still shown, marked.
            self._publish_reply(final, superseded=True)
        if self._voice is not None:
            self._fold_voice(self._voice)
        timing = replace(
            timing,
            rendered_ids=turn.rendered_ids,
            reply_text_at=turn.reply_text_at,
            first_audio_at=turn.first_play_at,
            spoken_done_at=self._clock() if turn.voiced else None,
            superseded=superseded,
            failed=failed,
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

    # ── the background-recall seams (t13's injected seams, bound here) ───

    def _complete_turn(
        self,
        query: str,
        window: list[dict[str, str]],
        memory: str,
        clock: str,
        continuation: str = "",
        collector: Optional[Any] = None,
    ) -> tuple[TurnResult, bool]:
        """ONE completion through the turn path: (scrubbed result, whether a tool ran).

        Every completion — reply, remainder, restart, follow-up, typed — goes
        through :func:`embodiment.turn.turn` with the same tools, the same scrub
        and the same tool-round answer, so no path speaks a secret or leaves a
        tool round silent. *memory* is :func:`render_recalled` output, the ONE
        place recall enters a prompt; *continuation* is fixed text plus Gwen's
        own earlier sentences, never the user's words. *collector* — a typed turn's
        only (c39) — adds that turn's library tools to this completion.
        """
        complete, tools = self._turn_tools(collector)
        config = TurnConfig(
            role=self._config.role,
            system_prompt=_compose_prompt(
                self._config.system_prompt,
                window,
                memory,
                tool_prompt=self._turn_tool_prompt(collector),
                clock=clock,
                continuation=continuation,
            ),
        )
        before = self._tool_counts()
        result = run_one_turn(query, complete, tools=tools, config=config)
        # Before the session, the transcript, the bus or the voice see it.
        spoken = self._answer_tool_step(self._scrub_reply(result.spoken), before)
        if spoken != result.spoken:
            result = replace(result, spoken=spoken)
        for degradation in result.degradations:
            self._fold("turn", degradation)
        return result, self._tool_counts()[0] != before[0]

    def _voice_complete(self, request: CompletionRequest) -> str:
        """The ``complete`` seam of the voice turn's :class:`BackgroundRecall`."""
        turn = self._voice_turn_here()
        if turn is None or request.purpose == PURPOSE_FOLLOW_UP:
            return self._follow_up_text(request)
        first = turn.result is None
        if not first and turn.tool_round:
            return _replay_after_tool_round(turn.first_text, request)
        result, tooled = self._complete_turn(
            turn.query, turn.window, request.memory, turn.clock, _continuation(request)
        )
        turn.steps += result.steps
        turn.tool_round = turn.tool_round or tooled
        if first:
            turn.result = result
            turn.first_text = result.spoken
            turn.reply_text_at = self._clock()
        elif _fell_back(result):
            # Unusable as a remainder: BackgroundRecall keeps the first
            # completion's unspoken sentences instead (recorded by it).
            return ""
        if request.memory:
            turn.rendered_ids = self._seen(request.memory).ids
        turn.final_text = " ".join((*request.spoken, result.spoken)).strip()
        return result.spoken

    def _follow_up_text(self, request: CompletionRequest) -> str:
        """A late memory's one follow-up (off by default), composed off the turn thread.

        The window is the session as it is NOW, so the follow-up sees the reply
        it follows. An unusable completion is dropped by the unprompted seam —
        the never-silent rule does not apply to speech nobody asked for.
        """
        result, _ = self._complete_turn(
            request.query,
            self._window_now(),
            request.memory,
            self._clock_line(),
            _continuation(request),
        )
        return "" if _fell_back(result) else result.spoken

    def _voice_speak(self, sentence: str) -> None:
        """The ``speak`` seam: ONE sentence to the voice, timed and barge-in checked.

        :meth:`Voice.speak` publishes each sentence as its own ``reply`` event,
        so the dashboard transcript shows a reply sentence by sentence.
        """
        turn = self._voice_turn_here()
        voice = self._voice
        if turn is not None:
            turn.handed = True
        if voice is None:
            if turn is None or not turn.no_voice_recorded:
                self._record(APP_NO_ENDPOINT, "nothing to speak through; the reply was not voiced")
            if turn is not None:
                turn.no_voice_recorded = True
            self._publish_reply(sentence, superseded=False)
            return
        said: list[Any] = []
        self._safely(lambda: said.append(voice.speak(sentence)), APP_TURN_FAILED, "speak")
        if turn is not None:
            turn.voiced = True
            at = next((getattr(r, "first_play_at", None) for r in said), None)
            if turn.first_play_at is None and at is not None:
                turn.first_play_at = at
            if self._is_superseded(turn.serial):
                # The hairline case: speech_started landed while this sentence
                # was being queued. Stop it through the voice's own barge-in.
                self._safely(voice.on_speech_started, APP_TURN_FAILED, "barge-in")
        self._fold_voice(voice)

    def _voice_stop_playback(self) -> None:
        """The ``stop_playback`` seam: the SAME path barge-in uses (c61)."""
        voice = self._voice
        if voice is not None:
            self._safely(voice.on_speech_started, APP_TURN_FAILED, "restart")

    def _floor_held(self) -> bool:
        """The ``floor_held`` seam. Never raises.

        On the voice turn's own thread the floor is held exactly when the turn
        was superseded — a ``speech_started`` already did that, and a reply
        loop must never be stopped by a flag the ear may never clear. Off it
        (a follow-up), anyone speaking, any turn in flight or waiting, or a
        daemon that is stopping, holds the floor: a follow-up never talks over.
        """
        turn = self._voice_turn_here()
        if turn is not None:
            return self._is_superseded(turn.serial)
        with self._lock:
            return bool(
                self._floor_taken
                or self._stopping
                or self._turns_in_flight > 0
                or self._turn_queue.qsize() > 0
            )

    def _voice_turn_here(self) -> Optional[_VoiceTurn]:
        """The voice turn in flight, if THIS thread is the one running it."""
        turn = self._voice_turn
        if turn is not None and turn.thread == threading.get_ident():
            return turn
        return None

    def _background_recall(self, query: str) -> Recalled:
        """The ``recall`` seam: the counted, bounded recall, on the recall job."""
        seen = self._recall(query)
        if seen.rendered:
            with self._lock:
                self._recall_seen[seen.rendered] = seen
                self._recall_seen.move_to_end(seen.rendered)
                while len(self._recall_seen) > _RECALL_INDEX_SIZE:
                    self._recall_seen.popitem(last=False)
        return Recalled(
            rendered=seen.rendered,
            relevance=seen.relevance,
            count=len(seen.ids) if seen.rendered else 0,
        )

    def _seen(self, rendered: str) -> _RecallSeen:
        with self._lock:
            return self._recall_seen.get(rendered) or _RecallSeen(rendered=rendered)

    def _record_recall_event(self, code: str, reason: str) -> None:
        self._record(code, reason, source="recall-events")

    def _window_now(self) -> list[dict[str, str]]:
        with self._lock:
            session = self._session
        if session is None:
            return []
        try:
            messages = session.messages()
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(APP_TURN_FAILED, f"window: {_describe(exc)}")
            return []
        return [m for m in messages if isinstance(m, dict)]

    def _settle_recalls(self, deadline: float) -> None:
        """Close both orchestrators and give their recall jobs a bounded wait."""
        for events in (self._recall_events, self._text_recall):
            events.close()
        share = max(0.0, float(deadline)) / 2
        for events in (self._recall_events, self._text_recall):
            events.wait(share)

    # ── typed turns (t14 handlers, t10 routes) ───────────────────────────

    def _locked_session(self) -> Any:
        """The session :class:`TextTurns` writes to, behind the conversation lock."""
        session = self._ensure_session()
        if session is None:
            return None
        return _LockedSession(session, self._conversation, self._conversation_bound())

    def _conversation_bound(self) -> float:
        """How long a typed turn waits for the conversation: one completion deadline."""
        return max(0.05, self._config.completion_deadline)

    def _hold_conversation(self) -> bool:
        """``hold_conversation`` for :class:`TextTurns`: the conversation, BOUNDED.

        Taken before the typed turn writes its user turn and held until its
        assistant turn is written, so a typed turn neither races a voice turn's
        session writes nor blocks a worker past the bound. ``False`` is busy:
        :class:`TextTurns` records :data:`APP_TEXT_TURN_BUSY` (the same code) and
        answers a retryable 503 having written nothing.
        """
        if self._conversation.acquire(timeout=self._conversation_bound()):
            with self._lock:
                self._typed_in_flight += 1
            return True
        with self._lock:
            self._text_busy += 1
        return False

    def _release_conversation(self) -> None:
        with self._lock:
            self._typed_in_flight = max(0, self._typed_in_flight - 1)
        self._conversation.release()

    def _run_text_turn(self, message: str) -> "_TypedReply":
        """``run_text_turn`` for :class:`TextTurns`: recall + senses, NO speaker.

        It never adds to the session — :class:`TextTurns` does ``add_user``
        before and ``add_assistant`` after, so a typed turn joins the same
        session and transcript log as a spoken one (c58). It runs inside the
        conversation :meth:`_hold_conversation` took; the reply carries the
        typed turn's serial so a clip can answer with it.
        """
        reply = self._run_text_turn_locked(message)
        turn = getattr(self._tls, "text_turn", None)
        return _TypedReply(spoken=reply, turn_serial=turn.serial if turn is not None else None)

    def _run_text_turn_locked(self, message: str) -> str:
        with self._lock:
            self._text_serial += 1
            self._text_turns_run += 1
            serial = self._text_serial
            session = self._session
        turn = _TextTurn(
            serial=serial,
            thread=threading.get_ident(),
            query=message,
            window=self._window(session),
            clock=self._clock_line(),
            collector=library_tools.CitationCollector() if self._library is not None else None,
        )
        self._tls.text_turn = turn
        with self._lock:
            self._text_turns_recent.append(turn)
        self._publish("transcript", {"role": "user", "text": message, "typed": True})
        try:
            outcome = self._text_recall.run_turn(message)
        finally:
            turn.replying = False
        reply = turn.final_text or turn.first_text or " ".join(outcome.spoken)
        # No turn_serial on this event: the page already has the answer from the
        # response, and Chat.tsx appends every serial'd reply as a NEW message.
        self._publish("reply", {"text": reply, "typed": True})
        return reply

    def _text_turn_here(self) -> Optional[_TextTurn]:
        turn = getattr(self._tls, "text_turn", None)
        if turn is not None and turn.replying and turn.thread == threading.get_ident():
            return turn
        return None

    def _text_complete(self, request: CompletionRequest) -> str:
        """The ``complete`` seam of the typed turn's :class:`BackgroundRecall`."""
        if request.purpose == PURPOSE_FOLLOW_UP:
            return self._text_follow_up(request)
        turn = self._text_turn_here()
        if turn is None:
            return ""
        first = turn.result is None
        if not first and turn.tool_round:
            return _replay_after_tool_round(turn.first_text, request)
        result, tooled = self._complete_turn(
            turn.query,
            turn.window,
            request.memory,
            turn.clock,
            _continuation(request),
            collector=turn.collector,
        )
        turn.tool_round = turn.tool_round or tooled
        if first:
            turn.result = result
            turn.first_text = result.spoken
        elif _fell_back(result):
            return ""
        if request.memory:
            turn.records = self._seen(request.memory).records
        turn.final_text = result.spoken
        return result.spoken

    def _text_follow_up(self, request: CompletionRequest) -> str:
        """Compose a typed turn's late memory follow-up; route it to that turn's serial."""
        turn = self._typed_turn_for(request)
        if turn is None:
            self._record(APP_TEXT_FOLLOW_UP_UNROUTED, "no recent typed turn matched", source="text")
            return ""
        self._tls.follow_up_turn = None
        result, _ = self._complete_turn(
            request.query, turn.window, request.memory, self._clock_line(), _continuation(request)
        )
        if _fell_back(result):
            return ""
        turn.follow_up_records = self._seen(request.memory).records
        self._tls.follow_up_turn = turn
        return result.spoken

    def _typed_turn_for(self, request: CompletionRequest) -> Optional[_TextTurn]:
        """The newest finished typed turn this late recall belongs to."""
        with self._lock:
            recent = list(self._text_turns_recent)
        for turn in reversed(recent):
            if turn.replying or turn.query != request.query:
                continue
            if tuple(request.spoken) in ((), (turn.final_text or turn.first_text,)):
                return turn
        return None

    def _text_speak(self, text: str) -> None:
        """The ``speak`` seam of a typed turn: the page, never the room's speaker."""
        if self._text_turn_here() is not None:
            return  # the reply itself: the route returns it; nothing is played
        turn = getattr(self._tls, "follow_up_turn", None)
        self._tls.follow_up_turn = None
        if turn is None:
            self._record(APP_TEXT_FOLLOW_UP_UNROUTED, "a follow-up had no turn", source="text")
            return
        with self._lock:
            self._text_follow_ups += 1
        # A later chat message on the same stream (c64): the serial ties it to
        # the typed turn it follows. Scrubbed like every reply; sources as the
        # page renders them — textContent only, marked untrusted there.
        self._publish(
            "reply",
            {
                "text": self._scrub_reply(text),
                "turn_serial": turn.serial,
                "sources": [chat_source(record) for record in turn.follow_up_records],
                "follow_up": True,
            },
        )

    def _control_chat(self, message: Any) -> dict[str, Any]:
        """``Controls.chat``: one typed turn, its reply, its sources and its serial."""
        self._tls.text_turn = None
        result = self._text_turns.handle_text(message)
        turn = getattr(self._tls, "text_turn", None)
        sources = _typed_sources(turn) if (turn and result.ok) else []
        return {
            "ok": result.ok,
            "reply": result.reply,
            "sources": sources,
            "turn_serial": turn.serial if turn is not None else None,
            "code": result.code,
            "http_status": result.http_status,
        }

    def _control_speak(self, text: Any) -> dict[str, Any]:
        """``Controls.speak``: synthesise for the requesting browser; nothing is played."""
        result = self._text_turns.speak_message(text)
        return {
            "ok": result.ok,
            "audio": result.audio,
            "content_type": result.content_type,
            "code": result.code,
            "http_status": result.http_status,
        }

    def _control_clip(self, audio: Any, declared_duration_s: Optional[float]) -> dict[str, Any]:
        """``Controls.clip``: transcribe in memory, then answer as a typed turn."""
        self._tls.text_turn = None
        result = self._text_turns.handle_clip(audio, declared_duration_s=declared_duration_s)
        turn = getattr(self._tls, "text_turn", None)
        return {
            "ok": result.ok,
            "transcript": result.transcript,
            "reply": result.reply,
            # Carried for the page; the clip route forwards only transcript and reply today.
            "sources": _typed_sources(turn) if (turn and result.ok) else [],
            "turn_serial": result.turn_serial if result.ok else None,
            "code": result.code,
            "http_status": result.http_status,
        }

    # ── the library lane (t13): typed-turn tools, the write guard, the routes ──

    def _turn_tool_prompt(self, collector: Optional[Any]) -> str:
        """The fixed tool lines for ONE completion: remember/forget, plus the library
        line only when this turn carries a library collector (d1)."""
        base = REMEMBER_TOOL_PROMPT if self._config.remember_tool else ""
        if collector is None or self._library is None or self._seam is None:
            return base
        return f"{base}\n{LIBRARY_TOOL_PROMPT}" if base else LIBRARY_TOOL_PROMPT

    def _counted(self, spec: Any) -> Any:
        """*spec* with its fn counting calls by tool name (d1: a counter that moves)."""
        inner = spec.fn

        def counted(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                self._library_tool_calls[spec.name] = self._library_tool_calls.get(spec.name, 0) + 1
            return inner(*args, **kwargs)

        return replace(spec, fn=counted)

    def _turn_tools(self, collector: Optional[Any]) -> tuple[Callable[..., Any], ToolRegistry]:
        """The completion seam and registry for ONE completion.

        No collector (every voice turn, c39; every turn with no library): the shared
        registry, exactly as before the library. With one, a registry built for this
        turn — the memory tools plus ``search_library``/``read_passage`` closed over
        the turn's own collector — bound to the raw seam so the schemas reach the wire.
        """
        lane = self._library
        if collector is None or lane is None or self._seam is None:
            return self._complete, self._tools
        registry = ToolRegistry()
        for spec in self._tools.specs.values():
            registry.add(spec)
        for spec in library_tools.build_library_tools(
            lane.index, lane.embed_query, collector, threshold=lane.threshold
        ):
            registry.add(self._counted(spec))
        return bind_tools(self._seam, registry), registry

    def live_turn_held(self) -> bool:
        """The library sync's gate: True while a live turn holds the conversation.

        A voice turn in flight or queued, a typed turn holding the conversation, or
        the speaker still playing. Never raises.
        """
        with self._lock:
            if self._turns_in_flight > 0 or self._typed_in_flight > 0:
                return True
            if self._turn_queue.qsize() > 0:
                return True
            endpoint = self._ear_endpoint
        try:
            return bool(endpoint is not None and endpoint.playing)
        except Exception:  # noqa: BLE001  # an endpoint fault is recorded on its own path
            return False

    def _library_guard(self, tool: str, *spans: str) -> bool:
        """c31: True (refuse) when library text is in this turn's context and the
        operator's own words in the current user turn do not ask for this write.

        The span checked is the first non-blank of *spans* — the fact, else the id.

        Only a typed turn carries a collector, so a voice turn is never guarded (it
        has no library text). Counted and recorded with the tool's name only.
        """
        span = next((s.strip() for s in spans if s.strip()), "")
        turn = self._text_turn_here()
        collector = turn.collector if turn is not None else None
        if not span or collector is None or not collector.library_text_in_context:
            # No span: the tool's own checks refuse it (``bad-id``); not a guard case.
            return False
        if user_turn_asks(turn.query, tool, span):
            return False
        with self._lock:
            self._library_guarded_writes += 1
            guarded = self._library_guarded_writes
        self._record(
            APP_LIBRARY_GUARDED_WRITE,
            f"{tool}: library text was in context and the user turn did not ask (#{guarded})",
        )
        return True

    def _start_library_sync(self) -> None:
        lane = self._library
        if lane is None or lane.sync is None:
            return
        thread = threading.Thread(
            target=self._library_worker, name="embodiment-library", daemon=True
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001  # no sync: the index stays as it is, said aloud
            self._record(APP_LIBRARY_UNAVAILABLE, f"sync worker: {_describe(exc)}")
            return
        self._library_thread = thread

    def _library_worker(self) -> None:
        """One tick now, then one per interval, until the daemon closes."""
        lane = self._library
        while lane is not None and not lane.stop.is_set():
            self._library_tick()
            lane.stop.wait(max(0.01, float(lane.interval_s)))

    def _library_tick(self) -> None:
        """ONE bounded sync tick plus the forwarding of what it recorded. Never raises."""
        lane = self._library
        if lane is None:
            return
        try:
            lane.sync.tick()
        except Exception as exc:  # noqa: BLE001  # the sync promises not to; count anyway
            with self._lock:
                self._library_tick_failures += 1
            self._record(APP_LIBRARY_DEGRADED, f"tick: {_describe(exc)}", source="library")
        else:
            with self._lock:
                self._library_ticks += 1
        if not lane.stop.is_set():
            # A tick a stop cut short abandoned its chunk on purpose: not a fault.
            self._forward_library_degradations(lane)

    def _forward_library_degradations(self, lane: LibraryLane) -> None:
        """The sync's transitions and the index's faults, each recorded once."""
        transitions = list(getattr(lane.sync, "degradations", ()) or ())
        problems = list(getattr(lane.index, "degradations", ()) or ())
        with self._lock:
            seen_sync, seen_index = self._library_seen
            self._library_seen = (len(transitions), len(problems))
        for code in transitions[seen_sync:]:
            if code == LIBRARY_RECOVERED:
                self._publish("state", {"component": "library", "status": "online"})
            else:
                reason = f"library sync: {safe_reason.safe_label(code)}"
                self._record(APP_LIBRARY_DEGRADED, reason, source="library")
        for problem in problems[seen_index:]:
            self._record(APP_LIBRARY_DEGRADED, f"document index: {problem}", source="library")

    def _stop_library(self, deadline: float) -> bool:
        """Stop the sync thread inside *deadline*. A fetch in flight is bounded by the
        client's own deadline, not this one, and is then reported unfinished."""
        lane = self._library
        if lane is None:
            return True
        lane.stop.set()
        thread = self._library_thread
        if thread is None:
            return True
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _library_list(self) -> dict[str, Any]:
        """``Controls.library_list``: the last catalog the sync listed, never a live GET."""
        lane = self._library
        if lane is None:
            return {"items": []}
        catalog = getattr(lane.client, "items", None) or ()
        return {"items": [_catalog_entry(item, lane.index) for item in catalog]}

    def _library_get(self, item_id: int) -> Optional[dict[str, Any]]:
        """``Controls.library_get``: one indexed, citable item for the reader, or None."""
        lane = self._library
        item = lane.index.item(str(item_id)) if lane is not None else None
        if not item:
            return None
        return {
            "item": {
                "id": str(item.get("id", item_id)),
                **{k: _text_or_empty(item.get(k)) for k in ("title", "kind", "author")},
                "created_at": _text_or_empty(item.get("created_at")),
            },
            "passages": [_reader_passage(p) for p in item.get("passages") or ()],
        }

    def _library_search(self, query: str) -> dict[str, Any]:
        """``Controls.library_search``: the query embedded (ungated), the index searched."""
        lane = self._library
        if lane is None:
            return {"hits": []}
        try:
            vector = lane.embed_query(query)
        except Exception as exc:  # noqa: BLE001  # an embedder fault degrades the search only
            self._record(APP_LIBRARY_DEGRADED, f"search: {_describe(exc)}", once=True)
            vector = None
        if vector is None:
            return {"hits": [], "code": LIBRARY_EMBED_FAILED_CODE}
        hits = lane.index.search(
            vector, kinds=None, limit=LIBRARY_SEARCH_LIMIT, threshold=lane.threshold
        )
        return {"hits": [_search_hit(hit) for hit in hits]}

    def _todos(self) -> dict[str, Any]:
        """``Controls.todos``: memory's ``action_item`` records, grouped by recording."""
        result = self._memory.list_ingest(
            record_type=TODO_RECORD_TYPE, deadline=server_module.TODOS_READ_BOUND_S
        )
        if not getattr(result, "ok", False):
            for degradation in getattr(result, "degradations", ()) or ():
                self._fold("memory", degradation)
            return {"groups": [], "code": server_module.MEMORY_UNAVAILABLE_CODE}
        return {"groups": todo_groups(getattr(result, "records", ()) or ())}

    def _library_status(self) -> dict[str, Any]:
        lane = self._library
        if lane is None:
            return {"configured": False}
        probed = _probe(lane.sync) or {"unavailable": True}
        thread = self._library_thread
        with self._lock:
            worker = {
                "running": bool(thread is not None and thread.is_alive()),
                "ticks": self._library_ticks,
                "tick_failures": self._library_tick_failures,
                "interval_s": lane.interval_s,
                "tick_budget_s": LIBRARY_TICK_BUDGET_S,
            }
            tool_calls = {
                name: self._library_tool_calls.get(name, 0)
                for name in ("search_library", "read_passage")
            }
        return {
            **probed,
            "configured": True,
            "last_sync_at": _finite_or_none(probed.get("last_sync_at")),
            # Memory's provisional threshold, applied to library text it was never
            # measured on (plan risk r6): reported, so nobody reads it as calibrated.
            "similarity_threshold": lane.threshold,
            "threshold_calibrated": False,
            "worker": worker,
            "tool_calls": tool_calls,
        }

    # ── the drain worker and the memory lanes' own degradations ──────────

    def _start_drain_worker(self) -> None:
        """One worker thread: the inbox drain and the lanes' degradation forwarding."""
        lanes = self._lanes
        if self._drain is None and lanes.embedder is None and lanes.vector_cache is None:
            if not callable(getattr(self._memory, "drain_abandoned", None)):
                return
        thread = threading.Thread(target=self._drain_worker, name="embodiment-drain", daemon=True)
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001  # no worker: nothing drains, said aloud
            self._record(APP_DRAIN_FAILED, f"worker: {_describe(exc)}")
            return
        self._drain_thread = thread

    def _kick_drain(self) -> None:
        if self._drain_thread is None:
            return
        with self._lock:
            self._drain_kicks += 1
        self._drain_kick.set()

    def _drain_worker(self) -> None:
        """Wake on each heartbeat's kick, run ONE bounded pass, forward degradations."""
        while not self._drain_stop.is_set():
            self._drain_kick.wait()
            self._drain_kick.clear()
            if self._drain_stop.is_set():
                break
            self._drain_pass(drain_budget_for(self._config.poll_interval_s))

    def _drain_pass(self, budget: float) -> None:
        """One drain pass plus the forwarding. Never raises."""
        drain = self._drain
        if drain is not None:
            try:
                report = drain.drain_once(budget)
            except Exception as exc:  # noqa: BLE001  # the drain promises not to; count anyway
                with self._lock:
                    self._drain_failures += 1
                self._record(APP_DRAIN_FAILED, _describe(exc), source="ingest")
            else:
                to_dict = getattr(report, "to_dict", None)
                with self._lock:
                    self._drain_passes += 1
                    self._drain_last_report = to_dict() if callable(to_dict) else None
        self._forward_lane_degradations()

    def _forward_lane_degradations(self) -> None:
        """t4/t12 keep their degradations as lists; this is where they reach the ledger.

        The embedder's TRANSITIONS (online → offline), the vector cache's own
        faults, and memory's abandoned-worker ledger (which is where the
        backfill records an offline embedder). Each list is read from where
        the last pass stopped, so each fact is recorded once.
        """
        transitions = list(getattr(self._lanes.embedder, "transitions", ()) or ())
        with self._lock:
            start, self._embedder_seen = self._embedder_seen, len(transitions)
        for entry in transitions[start:]:
            state, reason = (tuple(entry) + ("", ""))[:2]
            if state == "offline":
                self._record(
                    memory_module.CODE_EMBEDDER_OFFLINE,
                    f"the embedder ({vectors.EMBED_ROLE}) went offline: {reason}",
                    source="embedder",
                )
            else:
                self._publish("state", {"component": "embedder", "status": "online"})
        problems = list(getattr(self._lanes.vector_cache, "degradations", ()) or ())
        with self._lock:
            start, self._cache_seen = self._cache_seen, len(problems)
        for problem in problems[start:]:
            self._record(APP_VECTOR_CACHE_DEGRADED, problem, source="memory")
        drain_abandoned = getattr(self._memory, "drain_abandoned", None)
        if not callable(drain_abandoned):
            return
        try:
            drained = drain_abandoned()
        except Exception as exc:  # noqa: BLE001  # memory is a seam
            self._record(APP_RECALL_FAILED, f"abandoned: {_describe(exc)}")
            return
        for degradation in getattr(drained, "records", ()) or ():
            self._fold("memory", degradation)
        dropped = getattr(drained, "dropped", 0)
        if isinstance(dropped, int) and dropped > 0:
            self._record("memory-abandoned-dropped", f"{dropped} abandoned record(s) evicted")

    def _stop_drain(self, deadline: float) -> bool:
        """Stop the worker, then ONE last bounded pass while memory still writes."""
        started = self._clock()
        budget = max(0.05, float(deadline))
        self._drain_stop.set()
        self._drain_kick.set()
        thread = self._drain_thread
        if thread is not None:
            thread.join(timeout=budget / 2)
            if thread.is_alive():
                return False
        remaining = budget - (self._clock() - started)
        if self._drain is not None and remaining > 0:
            self._drain_pass(remaining * drain_module.HEARTBEAT_SHARE)
        else:
            self._forward_lane_degradations()
        return True

    def _start_backfill(self) -> None:
        """t12's bounded background backfill: uncached records gain vectors, off the turn."""
        start = getattr(self._memory, "start_backfill", None)
        if not callable(start):
            return
        try:
            start()
        except Exception as exc:  # noqa: BLE001  # memory is a seam
            self._record(APP_BOOTSTRAP_DEGRADED, f"backfill: {_describe(exc)}")

    def _stop_backfill(self, deadline: float) -> bool:
        stop = getattr(self._memory, "stop_backfill", None)
        if not callable(stop):
            return True
        try:
            return bool(stop(max(0.05, deadline)))
        except Exception as exc:  # noqa: BLE001  # memory is a seam
            self._record(APP_SHUTDOWN_INCOMPLETE, f"backfill: {_describe(exc)}")
            return False

    def _tool_counts(self) -> tuple[int, int, int, int]:
        """The four memory-tool counters, as one snapshot to difference against."""
        with self._lock:
            return (
                self._remember_tool_calls + self._forget_tool_calls,
                self._remember_tool_written,
                self._forget_tool_written,
                self._remember_tool_refused + self._forget_tool_refused,
            )

    def _answer_tool_step(self, spoken: str, before: tuple[int, int, int, int]) -> str:
        """Keep d7's promise that a tool round is never silent.

        The turn just called a tool. If the model's answer to the tool result
        holds nothing this voice can say — no Hebrew at all, which is what an
        "ok" is — the room hears nothing and no counter moves, which is
        exactly the failure mode ``C3`` names: attentive-looking and not
        there. So the daemon says it itself.

        What it says is bounded by what actually happened, because a
        confirmation is a claim: the fixed sentence for the tool that
        **wrote**, and otherwise the generic apology. A refused store must
        never be answered with "I saved it".
        """
        after = self._tool_counts()
        if after[0] == before[0] or _has_hebrew(spoken):
            return spoken
        if after[1] > before[1]:
            tool, replacement = REMEMBER_TOOL_NAME, TOOL_CONFIRMATION[REMEMBER_TOOL_NAME]
        elif after[2] > before[2]:
            tool, replacement = FORGET_TOOL_NAME, TOOL_CONFIRMATION[FORGET_TOOL_NAME]
        else:
            tool, replacement = "none", FALLBACK_TEXT
        with self._lock:
            self._tool_replies_answered += 1
            answered = self._tool_replies_answered
        # The reason carries the tool and a COUNT. The reply itself is speech
        # and never enters a record.
        self._record(
            APP_TOOL_REPLY_UNSPOKEN,
            f"{tool}: the reply after the tool round had no Hebrew "
            f"({len(spoken)} chars); spoke the fixed confirmation (#{answered})",
        )
        self._publish(
            "state",
            {"component": "voice", "status": "confirmed", "reason": f"tool-reply-unspoken:{tool}"},
        )
        return replacement

    def _note_timing(self, timing: TurnTiming) -> None:
        """Keep one turn's measurement where a reader can find it. Never raises."""
        with self._lock:
            self._recent_turns.append(timing.to_dict())

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
        if self._library_guard(REMEMBER_TOOL_NAME, stripped):
            return self._refuse_remember(LIBRARY_GUARDED_REASON, recorded=True)
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

    def _forget_tool(self, record_id: object = "", fact: object = "", **_ignored: Any) -> str:
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
        if not isinstance(record_id, str) or not isinstance(fact, str):
            return self._refuse_forget("not-text")
        # Before the d10 lookup: a guarded forget must not even read the store.
        if self._library_guard(FORGET_TOOL_NAME, fact, record_id):
            return self._refuse_forget(LIBRARY_GUARDED_REASON, recorded=True)
        stripped = record_id.strip()
        if not stripped and fact.strip():
            # Decision 20 (d10): no id to pass, because nothing was recalled.
            resolved = self._forget_id_for_text(fact.strip())
            if not isinstance(resolved, str):
                return self._refuse_forget(resolved.reason)
            stripped = resolved
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

    def _forget_id_for_text(self, text: str) -> "str | _ForgetTextRefusal":
        """The id of the ONE record whose text is exactly *text* (``d10``).

        The match is the daemon's, not the model's: an exact-mode recall in
        Gwen's own private store, then an equality check on the record text
        with the Unicode form and the surrounding whitespace normalised —
        substring hits do not count, because "forget the key" must not archive
        "the key is in the blue drawer AND the spare is with the neighbour".
        Zero matches and several matches are distinct refusals, so the model
        can say which happened instead of guessing.
        """
        wanted = _normalised_fact(text)
        try:
            result = self._memory.recall(
                # The NORMALISED text is what goes to the store too: an exact
                # (substring) search for a sentence the transcriber spaced
                # differently finds nothing at all, and "I could not find it"
                # would then mean "you said it slightly differently".
                wanted,
                mode=MEMORY_MODE_EXACT,
                deadline=self._recall_deadline_now(semantic_configured=False),
                top_k=FORGET_TEXT_TOP_K,
            )
        except Exception as exc:  # noqa: BLE001  # memory is a seam; a turn never dies on it
            self._record(APP_FORGET_REFUSED, f"lookup: {_describe(exc)}")
            return _ForgetTextRefusal("store-failed")
        matches = [
            identifier
            for record in self._records_of(result)
            if isinstance(record, dict)
            and _normalised_fact(record.get("text")) == wanted
            and isinstance(identifier := record.get("id"), str)
            and identifier
        ]
        if not matches:
            return _ForgetTextRefusal("not-found")
        if len(set(matches)) > 1:
            return _ForgetTextRefusal("ambiguous")
        return matches[0]

    def _refuse_forget(self, reason: str, *, recorded: bool = False) -> str:
        """Count a forget refusal, say so on the bus, tell the model why. Never the id.

        *reason* is a fixed token from this method's vocabulary — ``not-text``,
        ``bad-id``, ``unknown-id``, ``already-archived``, ``not-found``,
        ``ambiguous``, ``store-failed``, ``store-refused`` — never anything
        the model supplied.
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

    def _recall(self, text: str) -> _RecallSeen:
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
        configured, before = _semantic_view(_probe(self._memory))
        deadline = self._recall_deadline_now(configured)
        result = self._recall_once(text, self._config.recall_mode, deadline)
        self._observe_store(before, result)
        records = self._records_of(result)
        semantic = getattr(result, "mode", None) == memory_module.RECALL_MODE_SEMANTIC
        relevance = _finite_or_none(getattr(result, "relevance", None)) if semantic else None

        # d5 under semantic recall (t16's decision). The exact-substring
        # fallback exists because KEYWORD recall cannot tokenise Hebrew — a
        # blind search. Semantic recall is not blind: zero records from it
        # means nothing cleared the similarity threshold, which is c52's
        # "returns nothing when nothing is relevant" working as specified, and
        # an exact-substring rescue would put back exactly the unrelated
        # memories the threshold keeps out. So the fallback runs only when the
        # recall that ACTUALLY ran was lexical — keyword by configuration, or a
        # semantic request the store demoted (embedder down: still blind).
        if not records and not semantic and not _lexical_can_index(text):
            records = self._recall_blind_fallback(text, started, deadline)

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
        return _RecallSeen(
            rendered=rendered,
            ids=rendered_ids,
            records=tuple(records) if rendered else (),
            relevance=relevance,
        )

    def _recall_deadline_now(self, semantic_configured: bool) -> float:
        """The bound the next recall gets: configured, or derived (t23)."""
        pinned = _finite_or_none(self._config.recall_deadline)
        if pinned is not None:
            return pinned
        semantic = semantic_configured and self._config.recall_mode == RECALL_MODE_SEMANTIC
        with self._lock:
            active = self._active_records
        return derive_recall_deadline(active, semantic=semantic)

    def _observe_store(self, before: Optional[tuple[int, int]], result: Any) -> None:
        """Learn the active store size from the recall that just ran. Never raises.

        No store scan: the size is read from memory's own semantic counters —
        one query embed and one ``cached_hits + uncached_skipped`` bump per
        scored recall, the number of active records it enumerated. A sample is
        taken only when THIS recall scored (mode ``semantic``) and exactly one
        embed happened in its window; a concurrent recall's counts are not read
        as a size. A missed deadline forgets the size, so the next recall gets
        the cap and measures again instead of being censored by a stale count.
        """
        # Nothing here can raise: _probe and _semantic_view never do, and the
        # degradations are the ones _recall_once already iterated.
        if any(
            getattr(d, "code", "") == MEMORY_DEADLINE_EXCEEDED
            for d in getattr(result, "degradations", ()) or ()
        ):
            with self._lock:
                self._active_records = None
            return
        if before is None or getattr(result, "mode", None) != RECALL_MODE_SEMANTIC:
            return
        _, after = _semantic_view(_probe(self._memory))
        if after is None or after[0] - before[0] != 1:
            return
        active = after[1] - before[1]
        if active < 0:
            return
        with self._lock:
            self._active_records = active
            previous, self._store_level = self._store_level, _store_level(active)
            level = self._store_level
        for crossed in range(previous + 1, level + 1):
            ceiling = (RECALL_COMFORTABLE_RECORDS, RECALL_P90_CEILING_RECORDS)[crossed - 1]
            self._record(
                APP_RECALL_STORE_LARGE,
                f"active store {active} records, past the measured {ceiling}",
            )

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

    def _recall_blind_fallback(self, text: str, started: float, deadline: float) -> list[Any]:
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
            remaining = deadline - (self._clock() - started)
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
        session = _EarsSession(client=self._ears, rate=rate)
        self._publish(
            "state",
            {"component": "ears", "status": "dialling", "input_sample_rate": rate},
        )
        thread = threading.Thread(
            target=self._ears_main, args=(session,), name="embodiment-ears", daemon=True
        )
        session.thread = thread
        self._ears_session = session
        self._ears_thread = thread
        thread.start()

    def _stop_ears_session(self, deadline: float) -> bool:
        """Close the current session and reap its thread. Never raises.

        A thread that outlives *deadline* is recorded and left to finish on
        its own: it holds its session record, so whatever it closes on the
        way out is its own client and its own loop, not the next session's.
        """
        stopped = self._stop_ears(max(0.05, deadline))
        if not stopped:
            self._record(
                APP_EARS_CLOSE_INCOMPLETE,
                "the previous session's thread outlived the redial bound; it closes its own client",
            )
        self._ears_thread = None
        self._ears_loop = None
        self._ears_session = None
        return stopped

    def _is_current(self, session: _EarsSession) -> bool:
        """Whether *session* is still the one the app is listening through."""
        return self._ears_session is session

    def _ears_main(self, session: _EarsSession) -> None:
        loop = asyncio.new_event_loop()
        session.loop = loop
        if self._is_current(session):
            self._ears_loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._listen(session))
        except Exception as exc:  # noqa: BLE001  # the ear may die; the daemon may not
            self._record(APP_EARS_THREAD_FAILED, _describe(exc))
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001  # a loop that will not close is counted, not raised
                self._record(APP_EARS_THREAD_FAILED, "event loop would not close")
            # Only OUR loop is cleared: after a redial this attribute names
            # the new session's loop, which is not this thread's to touch.
            if self._ears_loop is loop:
                self._ears_loop = None

    async def _listen(self, session: _EarsSession) -> None:
        ears = session.client
        try:
            connected = await ears.connect()
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
            await self._shut_ears_down(session)
            return
        if not connected:
            if self._is_current(session):
                self._ears_connected = False
            self._record(APP_EARS_UNAVAILABLE, "the gateway did not give us a session")
            self._publish("state", {"component": "ears", "status": "unavailable"})
            return
        if self._is_current(session):
            self._ears_connected = True
        self._publish("state", {"component": "ears", "status": "listening"})
        requested = False
        try:
            async for event in ears.events():
                if self._stopping:
                    break
                self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  # any transport fault ends the stream
            self._record(APP_EARS_STREAM_ENDED, _describe(exc))
        finally:
            if self._is_current(session):
                self._ears_connected = False
            # ``closed`` is set by the daemon's own stop BEFORE the client is
            # asked to close (``_shut_ears_down``), so reading it here — before
            # this path sets it — tells a requested stop from a peer's.
            requested = session.closed.is_set()
            # Whatever ended the stream — a stop, a peer close, a transport
            # fault — the client is closed from inside its own loop, which is
            # the only thread that can close it gracefully.
            await self._shut_ears_down(session)
        if not self._stopping and not requested:
            self._record(APP_EARS_STREAM_ENDED, "the event stream ended; no reconnect in t15")
            self._publish("state", {"component": "ears", "status": "ended"})

    def _on_event(self, event: Any) -> None:
        """Classify ONE server event. Runs on the ears thread; never blocks on a turn."""
        try:
            if isinstance(event, wire.TranscriptionCompleted):
                with self._lock:
                    self._floor_taken = False
                self.submit_transcript(event.text)
            elif isinstance(event, wire.SpeechStarted):
                # Who holds the floor, for a follow-up nobody asked for. Cleared
                # by speech_stopped or the transcription; a stuck flag only ever
                # silences follow-ups, never a reply (see _floor_held).
                with self._lock:
                    self._floor_taken = True
                self._barge_in()
            elif isinstance(event, wire.SpeechStopped):
                with self._lock:
                    self._floor_taken = False
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

        def stop_speaking() -> None:
            # No verdict, for the same reason as _reap_endpoints: a stop that
            # RAISED is recorded by _safely; this bound asks only whether it
            # RETURNED, and conflating the two would report a timeout for a
            # speaker that answered immediately with an error.
            self._safely(voice.on_speech_started, APP_TURN_FAILED, "barge-in")

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

    async def _shut_ears_down(
        self, session: _EarsSession, deadline: Optional[float] = None
    ) -> None:
        """Close *session*'s ears from INSIDE their own loop. Idempotent; never raises.

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

        ``session.close_done`` is set on the way out whichever path ran, so
        a waiter outside the loop never needs a future linked across it.
        """
        if session.closed.is_set():
            return
        session.closed.set()
        bound = deadline
        if bound is None:
            bound = self._ears_close_bound or self._config.shutdown_deadline
        try:
            report = await session.client.close(max(0.05, float(bound)))
        except Exception as exc:  # noqa: BLE001  # the client promises a report, not silence
            self._record(APP_EARS_THREAD_FAILED, f"close: {_describe(exc)}")
        else:
            if getattr(report, "graceful", True) is False:
                # The client's own report is what says whether the handshake
                # completed — not our clock running out on it.
                self._record(APP_EARS_CLOSE_INCOMPLETE, "the close handshake did not complete")
        finally:
            session.close_done.set()

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
        session = self._ears_session
        thread = self._ears_thread
        if session is None or thread is None or not thread.is_alive():
            return True
        loop = self._await_ears_loop(deadline)
        close_bound = max(0.05, deadline * _EARS_CLOSE_WAIT_SHARE)
        if (
            loop is not None
            and loop.is_running()
            and not loop.is_closed()
            and not session.closed.is_set()
            and self._schedule_ears_close(session, loop, close_bound)
        ):
            # Strictly longer than what the client itself was given, so a
            # client answering inside its own bound always wins the race
            # against this wait.
            session.close_done.wait(timeout=close_bound + _EARS_CLOSE_GRACE_S)
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _schedule_ears_close(
        self, session: _EarsSession, loop: asyncio.AbstractEventLoop, bound: float
    ) -> bool:
        """Ask the ears loop to close its client, from its own thread. Never raises.

        The coroutine is built inside the callback, on the loop's thread, so a
        loop that closes before the callback runs leaves no un-awaited
        coroutine behind and nothing to cancel.
        """

        def spawn() -> None:
            loop.create_task(self._shut_ears_down(session, bound))

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
        """The control callables the dashboard server binds to, chat page included."""
        return server_module.Controls(
            start_voice=self._control_start_voice,
            stop_voice=self._control_stop_voice,
            set_mute=self._control_set_mute,
            status=self.status,
            chat=self._control_chat,
            speak=self._control_speak,
            clip=self._control_clip,
            # Unbound without a library: the routes answer 503 library-not-configured.
            library_list=self._library_list if self._library is not None else None,
            library_get=self._library_get if self._library is not None else None,
            library_search=self._library_search if self._library is not None else None,
            # From memory, library or not; a memory with no listing answers 503.
            todos=self._todos if callable(getattr(self._memory, "list_ingest", None)) else None,
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

    def _ingest_status(self) -> Optional[dict[str, Any]]:
        """The drain's own status (the inbox counts first) plus its worker's."""
        if self._drain is None:
            return None
        probed = _probe(self._drain) or {"unavailable": True}
        thread = self._drain_thread
        with self._lock:
            worker = {
                "running": bool(thread is not None and thread.is_alive()),
                "kicks": self._drain_kicks,
                "passes": self._drain_passes,
                "failures": self._drain_failures,
                "budget_s": drain_budget_for(self._config.poll_interval_s),
                "last_report": self._drain_last_report,
            }
        return {**probed, "worker": worker}

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
                "frames_refused": self._frames_refused,
                "frames_from_stale_ear": self._stale_frames,
                "frames_dropped_no_session": self._frames_dropped_no_session,
                "stale_frame_tolerance": _STALE_FRAME_TOLERANCE,
            }
            recall_mode = self._recall_mode
            recall_calls = self._recall_calls
            publish_errors = self._publish_errors
            ledger_errors = self._ledger_errors
            session = self._session
        semantic_status = _probe(self._memory)
        with self._lock:
            active_records, store_level = self._active_records, self._store_level
        access, http = _access_status(self._config, _http_status(self._server, self._config))
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
                # The bound the NEXT recall gets (t23): derived from the store
                # the last semantic recall scored, unless configured. The turn's
                # wait for memory is the head start, under recall_events.
                "deadline_s": self._recall_deadline_now(_semantic_view(semantic_status)[0]),
                "deadline_derived": _finite_or_none(self._config.recall_deadline) is None,
                "deadline_cap_s": RECALL_DEADLINE_CAP_S,
                "active_records": active_records,
                "store_warning": RECALL_STORE_WARNINGS.get(store_level),
                "configured_mode": self._config.recall_mode,
                "semantic": _semantic_available(self._memory),
                # The embedder IN EFFECT, by role name (t12): configured,
                # online at its last real call, and why not. None for a
                # memory with no semantic surface at all.
                "embedder": (semantic_status or {}).get("embedder"),
                "semantic_recall": semantic_status,
            },
            "memory": {
                **_memory_status(self._memory),
                "remember_tool_calls": self._remember_tool_calls,
                "remember_tool_written": self._remember_tool_written,
                "remember_tool_refused": self._remember_tool_refused,
                "forget_tool_calls": self._forget_tool_calls,
                "forget_tool_written": self._forget_tool_written,
                "forget_tool_refused": self._forget_tool_refused,
                "tool_replies_answered": self._tool_replies_answered,
                "asks_detected": self._asks_detected,
                "remembered": self._asks_remembered,
                "remember_failed": self._asks_failed,
                "remember_deferred": self._asks_deferred,
                "ask_not_detected": self._asks_missed,
                "summary_attempted": self._summary_attempted,
                "summary_written": self._summary_written,
                "summary_skip_reason": self._summary_skip_reason,
                "library_guarded_writes": self._library_guarded_writes,
            },
            "session": _session_status(session),
            "recall_events": {
                "voice": self._recall_events.status(),
                "text": self._text_recall.status(),
            },
            "text_turns": {
                "run": self._text_turns_run,
                "follow_ups_published": self._text_follow_ups,
                "busy": self._text_busy,
                "degradations": len(self._text_turns.degradations),
            },
            "ingest": self._ingest_status(),
            "library": self._library_status(),
            # The mode and the guard's counters, ONCE. The team domain, the audience
            # and the allow-listed emails are configuration a dashboard viewer has no
            # business reading.
            "access": access,
            "speech_to_speech": {"role": self._config.s2s_role or None, "enabled": False},
            "clock": {"zone": CLOCK_ZONE_NAME, "zone_available": CLOCK_ZONE_AVAILABLE},
            "ears": _probe(self._ears),
            "voice": _probe(self._voice),
            "http": http,
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
    continuation: str = "",
) -> str:
    """The system prompt for ONE turn: base, clock, tools, the window, then recall.

    *continuation* (t16) is set only for a remainder, restart or follow-up
    completion: fixed instruction text plus what Gwen already said. Empty —
    every first reply — leaves the prompt byte-identical to before.

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
    if continuation:
        parts.append(continuation)
    if recalled:
        parts.append(recalled)
    return "\n\n".join(parts)


def _no_playback() -> None:
    """A typed turn's ``stop_playback``: there is no speaker to stop."""


def _nobody_holds_the_page() -> bool:
    """A typed turn's ``floor_held``: nobody can talk over a chat page."""
    return False


def _whole_message(text: str) -> list[str]:
    """A typed turn's ``split``: the whole message is ONE unit, so a late memory
    is one later chat message, never a reply spliced sentence by sentence."""
    return [text] if isinstance(text, str) and text.strip() else []


def _continuation(request: CompletionRequest) -> str:
    """The prompt section a non-first completion carries: what was said, and why."""
    instruction = {
        PURPOSE_REMAINDER: REMAINDER_INSTRUCTION,
        PURPOSE_RESTART: RESTART_INSTRUCTION,
        PURPOSE_FOLLOW_UP: FOLLOW_UP_INSTRUCTION,
    }.get(request.purpose, "")
    if not instruction and not request.spoken:
        return ""
    lines = [SPOKEN_HEADER, *(f"- {sentence}" for sentence in request.spoken)]
    if instruction:
        lines.append(instruction)
    return "\n".join(lines)


def _replay_after_tool_round(first_text: str, request: CompletionRequest) -> str:
    """A turn that already took a tool round is never regenerated.

    The tool's effect is done (a record written or archived) and the reply is
    the confirmation that licenses it; running the turn again would run the
    tool again. So a memory that lands afterwards gets the SAME words: the
    whole reply before anything was spoken, the unspoken rest of it mid-reply,
    and nothing for a restart (the reply stands). BackgroundRecall still counts
    the arrival — and a "regeneration" that changed no word.
    """
    if request.purpose == PURPOSE_REPLY:
        return first_text
    if request.purpose == PURPOSE_REMAINDER:
        return " ".join(split_sentences(first_text)[len(request.spoken) :])
    return ""


def _fell_back(result: TurnResult) -> bool:
    """True when the turn path produced nothing of its own (never-silent fallback)."""
    return result.spoken == FALLBACK_TEXT and result.degraded


def _finite_or_none(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class _TypedReply:
    """What :meth:`DaemonApp._run_text_turn` hands :class:`TextTurns`."""

    spoken: str
    turn_serial: Optional[int] = None


class _LockedSession:
    """The session as :class:`TextTurns` sees it: every write behind the conversation.

    :class:`TextTurns` already holds the conversation (re-entrant) when it
    writes, so these acquires are immediate; the bound is the backstop that
    keeps a write from ever waiting unbounded if that invariant breaks. A
    write that cannot get the conversation raises, which :class:`TextTurns`
    records as a session failure.
    """

    def __init__(self, session: Any, lock: Any, timeout: float) -> None:
        self._session = session
        self._lock = lock
        self._timeout = timeout

    def _write(self, call: Callable[[], Any]) -> Any:
        if not self._lock.acquire(timeout=self._timeout):
            raise TimeoutError("the conversation was busy")
        try:
            return call()
        finally:
            self._lock.release()

    def add_user(self, text: str) -> Any:
        return self._write(lambda: self._session.add_user(text))

    def add_assistant(self, text: str, metadata: Any = None) -> Any:
        return self._write(lambda: self._session.add_assistant(text, metadata=metadata))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _timestamp_seconds(value: object) -> Optional[int]:
    """A recording timestamp (``ss``, ``mm:ss`` or ``hh:mm:ss``) as whole seconds.

    The chat page treats a source as a RECORDING when ``start`` is a number,
    so a pocket timestamp is converted here; anything else is ``None`` — an
    absent position, never an invented ``0``.
    """
    if not isinstance(value, str) or not value:
        return None
    parts = value.split(":")
    if len(parts) > 3 or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def chat_source(record: Any) -> dict[str, Any]:
    """One recalled record as the chat page's source shape. Never raises.

    Provenance goes through :func:`embodiment.memory.recalled_provenance` — the
    same neutralising funnel as the prompt's header, so the page and the model
    read one sanitised value. The text goes through memory's one sanitiser and
    is capped; the page renders all of it with ``textContent`` and marks it
    untrusted. A room record (Gwen's own, no provenance) is ``source: room``.
    """
    try:
        provenance = memory_module.recalled_provenance(record)
        text = record.get("text") if isinstance(record, Mapping) else ""
        # memory's one sanitiser, through its public delegate.
        clean = memory_module.neutralise_source_text(text, SOURCE_TEXT_MAX_CHARS)
    except Exception:  # noqa: BLE001  # a hostile record costs its source, never the turn
        provenance, clean = None, ""
    out: dict[str, Any] = {
        "source": "room",
        "title": "",
        "date": "",
        "start": None,
        "end": None,
        "section": "",
        "link": "",
        "text": clean,
    }
    if provenance is None:
        return out
    out.update(
        source=provenance["source"],
        title=provenance["title"],
        date=provenance["date"],
        section=provenance["section"],
        link=provenance["link"],
    )
    if provenance["source"] == "pocket":
        out["start"] = _timestamp_seconds(provenance["start"])
        out["end"] = _timestamp_seconds(provenance["end"])
    return out


# ── the library lane's pure helpers (t13) ────────────────────────────────────


def user_turn_asks(user_text: object, tool: str, span: object) -> bool:
    """c31: does the operator's OWN current turn ask for this write?

    ``remember``: the session's explicit-ask detector matches the turn
    (:func:`embodiment.session.default_ask_detector`). Either tool: the turn
    carries that tool's stem AND the span the model quoted — the fact, or the
    record id — occurs in it (NFC, whitespace and case folded). The stem is what
    makes it an ask: a turn that merely *mentions* "otters" does not ask to forget
    them, whatever a library page said. Deliberately biased to refusing.
    """
    if not isinstance(user_text, str) or not user_text.strip():
        return False
    if tool == REMEMBER_TOOL_NAME and _detects_ask(user_text):
        return True
    said = _normalised_fact(user_text).casefold()
    if tool == FORGET_TOOL_NAME:
        stems = _FORGET_STEMS
        # "don't forget X" asks to KEEP X: never a forget ask.
        said_for_stems = said.replace("don't forget", "").replace("dont forget", "")
    else:
        stems, said_for_stems = _ASK_STEMS, said
    if not any(stem in said_for_stems for stem in stems):
        return False
    wanted = _normalised_fact(span).casefold()
    return bool(wanted) and wanted in said


def _detects_ask(text: str) -> bool:
    try:
        return default_ask_detector(text) is not None
    except Exception:  # noqa: BLE001  # a detector fault is a "no", never a crash
        return False


def library_chat_source(citation: Mapping[str, Any]) -> dict[str, Any]:
    """One library :class:`~embodiment.library.tools.Citation` as the chat page's source.

    Every field was already neutralised and capped by the tools; ``item_id`` and
    ``anchor`` let the page open the reader at the cited passage (t7).
    """
    return {
        "source": library_tools.LIBRARY_SOURCE,
        "item_id": str(citation.get("item_id") or ""),
        "anchor": str(citation.get("anchor") or ""),
        "title": str(citation.get("title") or ""),
        "date": str(citation.get("date") or ""),
        "kind": str(citation.get("kind") or ""),
        "start": citation.get("start"),
        "end": citation.get("end"),
        "section": "",
        "link": "",
        "text": str(citation.get("text") or ""),
    }


_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_NEWSPAPER_WORDS = ("newspaper", "עיתון")


def _library_issue(source: Mapping[str, Any]) -> Optional[str]:
    """The newspaper issue date a library source cites, or None for any other item.

    An **assumption from the catalog as seen on 2026-10-24**, not a contract: xteink
    files culture-rules' issues as ``kind=article`` titled "Daily newspaper ...", so
    an item is an issue when its kind is ``newspaper`` or its title names one; the
    issue date is the title's ISO date, else the item's ``created_at`` date.
    """
    title = str(source.get("title") or "")
    kind = str(source.get("kind") or "").lower()
    if kind != "newspaper" and not any(w in title.lower() for w in _NEWSPAPER_WORDS):
        return None
    for text in (title, str(source.get("date") or "")):
        found = _ISO_DATE.search(text)
        if found:
            return found.group(1)
    return None


def merge_sources(
    library: list[dict[str, Any]], recalled: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The reply's sources: library citations first, in collector order — so the
    ``[n]`` the model was shown is ``sources[n-1]`` — then recall. A recalled
    newspaper item from an issue the library also cited is shown once (c38): the
    library's copy stays, because it carries the reader target."""
    issues = {issue for issue in (_library_issue(s) for s in library) if issue}
    kept = [s for s in recalled if not (s.get("source") == "newspaper" and s.get("date") in issues)]
    return [*library, *kept]


def _typed_sources(turn: Any) -> list[dict[str, Any]]:
    recalled = [chat_source(record) for record in turn.records]
    collector = getattr(turn, "collector", None)
    if collector is None:
        return recalled
    return merge_sources([library_chat_source(c) for c in collector.sources()], recalled)


def _text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _catalog_entry(item: Any, index: Any) -> dict[str, Any]:
    item_id = str(getattr(item, "id", ""))
    return {
        "id": item_id,
        "title": _text_or_empty(getattr(item, "title", "")),
        "kind": _text_or_empty(getattr(item, "kind", "")),
        "created_at": _text_or_empty(getattr(item, "created_at", "")),
        "indexed": index.item(item_id) is not None,
    }


def _reader_passage(passage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "anchor": _text_or_empty(passage.get("anchor")),
        "heading": _text_or_empty(passage.get("heading")),
        "text": _text_or_empty(passage.get("text")),
        "start_s": _finite_or_none(passage.get("start_s")),
        "end_s": _finite_or_none(passage.get("end_s")),
        "speaker": _text_or_empty(passage.get("speaker")),
    }


def _search_hit(hit: Any) -> dict[str, Any]:
    return {
        "item_id": str(getattr(hit, "item_id", "")),
        "title": _text_or_empty(getattr(hit, "title", "")),
        "kind": _text_or_empty(getattr(hit, "kind", "")),
        "created_at": _text_or_empty(getattr(hit, "created_at", "")),
        "anchor": _text_or_empty(getattr(hit, "anchor", "")),
        "heading": _text_or_empty(getattr(hit, "heading", "")),
        "text": _text_or_empty(getattr(hit, "text", "")),
        "score": round(float(getattr(hit, "score", 0.0)), 4),
        "start_s": _finite_or_none(getattr(hit, "start_s", None)),
        "end_s": _finite_or_none(getattr(hit, "end_s", None)),
    }


def _todo_key(record: Any) -> Optional[str]:
    """The recording a record is a to-do of, or None if it is not an active one."""
    if not isinstance(record, Mapping) or record.get("type") != TODO_RECORD_TYPE:
        return None
    if record.get("lifecycle", "active") not in (None, "active"):
        return None
    meta = record.get("metadata")
    if not isinstance(meta, Mapping) or meta.get("source") != TODO_SOURCE:
        return None
    recording = meta.get("recording_id")
    return recording if isinstance(recording, str) and recording else None


def todo_groups(records: Iterable[Any]) -> list[dict[str, Any]]:
    """``action_item`` records grouped by ``recording_id``, newest recording first.

    Text goes through memory's one sanitiser and is capped, like a chat source;
    the page renders it with ``textContent``.
    """
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        recording = _todo_key(record)
        if recording is None:
            continue
        meta = record["metadata"]
        group = groups.setdefault(
            recording,
            {
                "recording_id": recording,
                "title": _text_or_empty(meta.get("title")),
                "date": _text_or_empty(meta.get("date")),
                "items": [],
            },
        )
        group["items"].append(
            {
                "text": memory_module.neutralise_source_text(
                    record.get("text") or "", SOURCE_TEXT_MAX_CHARS
                ),
                "created": _text_or_empty(record.get("created")),
            }
        )
    return sorted(groups.values(), key=lambda g: g["date"], reverse=True)


def _access_status(
    config: AppConfig, http: Optional[dict[str, Any]]
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """ONE access block: the daemon's mode plus the guard's counters (t5), lifted out
    of the server's status so ``/api/status`` never carries two that disagree."""
    access: dict[str, Any] = {"mode": config.access_mode}
    if not http or not isinstance(http.get("access"), dict):
        return access, http
    http = dict(http)
    counters = http.pop("access")
    for key in ("admitted", "refused_by_code"):
        if key in counters:
            access[key] = counters[key]
    return access, http


def drain_budget_for(poll_interval_s: object) -> float:
    """One drain pass's budget: :func:`drain.budget_for` of the heartbeat, floored.

    The floor is two of the drain's own minimum record budgets. Without it a
    fast heartbeat (a 0.01 s poll gives 0.005 s) is a budget in which no record
    can ever START — a drain that runs every tick and applies nothing, with
    every counter looking healthy (CLAUDE.md lesson 3). The floor costs the
    heartbeat nothing: the pass runs on the drain worker, not in ``pump``.
    """
    try:
        interval = float(poll_interval_s)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        interval = AppConfig.poll_interval_s
    return max(drain_module.budget_for(interval), 2 * drain_module.MIN_RECORD_BUDGET_S)


def access_mode_of(verifier: object) -> str:
    """``off`` / ``on`` / ``incomplete`` for what :func:`verifier_from_env` returned."""
    if verifier is None:
        return ACCESS_MODE_OFF
    if isinstance(verifier, access_module.AccessVerifier):
        return ACCESS_MODE_ON
    return ACCESS_MODE_INCOMPLETE


def recall_mode_from_env(value: Optional[str]) -> tuple[str, bool]:
    """``(mode, known)``: the configured recall mode, semantic unless told keyword."""
    raw = (value or "").strip().lower()
    if not raw:
        return RECALL_MODE_SEMANTIC, True
    if raw in RECALL_MODES:
        return raw, True
    return RECALL_MODE_SEMANTIC, False


def _semantic_view(status: Optional[dict[str, Any]]) -> tuple[bool, Optional[tuple[int, int]]]:
    """From ``RoomMemory.status()``: (semantic configured, (query embeds, candidates)).

    *candidates* is ``cached_hits + uncached_skipped`` — every active record a
    scored recall enumerated. The pair is ``None`` when the counters are absent
    or not counts. Never raises.
    """
    if not isinstance(status, dict):
        return False, None
    embedder = status.get("embedder")
    configured = isinstance(embedder, dict) and embedder.get("configured") is True
    counters = status.get("semantic")
    if not isinstance(counters, dict):
        return configured, None
    values = [counters.get(k) for k in ("query_embeds", "cached_hits", "uncached_skipped")]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return configured, None
    return configured, (values[0], values[1] + values[2])


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


def loopback_origin_hosts(port: int) -> tuple[str, ...]:
    """The ``host[:port]`` forms a browser puts in ``Origin`` for a loopback dashboard.

    The guard accepts every :data:`~embodiment.http.guard.DEFAULT_ALLOWED_HOSTS`
    entry as a ``Host`` with the port stripped, but an ``Origin`` keeps its
    port and a browser sends one on EVERY ``POST`` — so an Origin list built
    only from the operator's extra hosts refused Start/Stop/Mute on every
    default install (``http-refused-origin`` at ``http://127.0.0.1:8823``).
    Both the bound port and the bare host are listed (a reverse proxy on 80
    or 443 presents the bare form), and an IPv6 literal is bracketed the way
    a browser writes it; the unbracketed spellings the guard keeps for the
    ``Host`` comparison would never match an Origin and are not repeated.
    """
    out: list[str] = []
    for host in sorted(guard_module.DEFAULT_ALLOWED_HOSTS):
        cleaned = host.strip().lower()
        if ":" in cleaned and not cleaned.startswith("["):
            cleaned = f"[{cleaned}]"
        for form in (f"{cleaned}:{int(port)}", cleaned):
            if form not in out:
                out.append(form)
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


@dataclass(frozen=True)
class _ForgetTextRefusal:
    """Why a text-route forget could not name exactly one record (``d10``)."""

    reason: str


def _normalised_fact(text: object) -> str:
    """One spelling for comparing two stored facts: NFC, collapsed whitespace.

    Speech arrives through a transcriber and a model, so the same sentence can
    come back with a different Unicode composition or a doubled space. Neither
    is a different fact — and neither is allowed to become a silent miss when
    the user asks for something to be forgotten.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", text).split())


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
        # Whether a Host is public at all — the name itself is not published,
        # for the same reason the allow-list is a count.
        "public_hostname_configured": bool(config.public_hostname),
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


#: The vector cache's directory under the state dir. 0700, its file 0600 (t4).
VECTOR_DIRNAME = "vectors"


def _private_vector_cache(state_dir: Any, early: list[tuple[str, str, str]]) -> Any:
    """The 0700 vector-cache dir under the state dir, or ``None`` (recorded).

    ``None`` — no state dir, or one that cannot hold a private directory —
    leaves semantic recall unconfigured: every semantic recall then demotes to
    keyword with a recorded degradation, rather than caching vectors somewhere
    nobody pinned (the cwd).
    """
    if state_dir is None:
        early.append((APP_BOOTSTRAP_DEGRADED, "vector cache: no state dir", APP_SOURCE))
        return None
    directory = Path(state_dir) / VECTOR_DIRNAME
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        early.append((APP_BOOTSTRAP_DEGRADED, f"vector cache: {_describe(exc)}", APP_SOURCE))
        return None
    return memory_module.vector_cache_for(directory)


def _library_lane_from_env(
    library: Optional[LibraryConfig],
    state_dir: Any,
    embedder: Any,
    *,
    gate: Callable[[], bool],
    early: list[tuple[str, str, str]],
) -> Optional[LibraryLane]:
    """The library lane :func:`main` wires, or ``None``: unset, or unbuildable (recorded).

    The index lives at ``<state>/library/index.json``; both embedders are the
    gemma2-embed role memory already uses (one shared lane on orin), by name.
    """
    if library is None:
        return None
    if state_dir is None:
        early.append((APP_LIBRARY_UNAVAILABLE, "library: no state dir for the index", APP_SOURCE))
        return None
    try:
        client = LibraryClient(library.base_url, library.key)
        return build_library_lane(
            client,
            Path(state_dir) / LIBRARY_DIRNAME / LIBRARY_INDEX_NAME,
            embed_document=lambda text: embedder.embed(
                text, prompt=memory_module.DOCUMENT_PROMPT
            ).vector,
            embed_query=lambda text: embedder.embed(text, prompt=memory_module.QUERY_PROMPT).vector,
            gate=gate,
        )
    except (LibraryError, ValueError) as exc:
        early.append((APP_LIBRARY_UNAVAILABLE, f"library: {_describe(exc)}", APP_SOURCE))
        return None


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
    # The library (t13): from the env ONLY (``grant run --inject
    # EMBODIMENT_LIBRARY_KEY=…``), never a file this daemon writes. Its key joins
    # every redaction list beside the gateway key and the install secret.
    library_config = LibraryConfig.from_env(os.environ)
    library_key = library_config.key if library_config is not None else ""
    secrets = tuple(s for s in (realtime.api_key, secret.secret, library_key) if s)
    bus = Bus(redact=secrets)

    # Records that arrive before the app exists (an incomplete Access env is
    # recorded by verifier_from_env itself, at build time) wait here and are
    # replayed through the app's ONE recording path once it does.
    early: list[tuple[str, str, str]] = []
    built: list["DaemonApp"] = []

    def sink(source: str) -> Callable[[str, str], None]:
        def record(code: str, reason: str) -> None:
            if built:
                built[0]._record(code, reason, source=source)
            else:
                early.append((code, reason, source))

        return record

    # The Access verifier (t3, t7): all-or-nothing team domain + audience, plus
    # the operator's email allow-list (EMBODIMENT_ACCESS_EMAILS). None when the
    # env is unset, and then the guard is byte-identical to before.
    verifier = access_module.verifier_from_env(on_degrade=sink("http"))
    recall_mode, known_mode = recall_mode_from_env(os.environ.get(ENV_RECALL_MODE))
    if not known_mode:
        early.append(
            (
                APP_RECALL_MODE_UNKNOWN,
                f"{ENV_RECALL_MODE} is not one of {', '.join(RECALL_MODES)}; kept semantic",
                APP_SOURCE,
            )
        )
    config = AppConfig(
        gateway_url=realtime.gateway_url,
        api_key=realtime.api_key,
        # The runner's watchdog bound, exported by the lifecycle child; the
        # close budget is derived strictly below it (one clock, finding 1).
        shutdown_deadline=close_budget_for(
            os.environ.get(ENV_SHUTDOWN_DEADLINE) or DEFAULT_SHUTDOWN_DEADLINE
        ),
        bind=os.environ.get(ENV_HTTP_BIND) or AppConfig.bind,
        bind_public=_env_flag(os.environ.get(ENV_BIND_PUBLIC)),
        allowed_hosts=parse_allowed_hosts(os.environ.get(ENV_ALLOWED_HOSTS)),
        public_hostname=(os.environ.get(ENV_PUBLIC_HOSTNAME) or "").strip().lower() or None,
        recall_mode=recall_mode,
        access_mode=access_mode_of(verifier),
        s2s_role=(os.environ.get(ENV_S2S_ROLE) or "").strip() or None,
    )

    base_dir = Path(state.dir) if state.dir is not None else Path(".")
    data_dir = base_dir / "memory"
    # Semantic recall (t12): the gemma2-embed role BY NAME, its vectors cached
    # 0700/0600 under the state dir (never committed, never beside the store's
    # public half), and the ingest scope read alongside gwen's own (t5).
    embedder = vectors.EmbedderClient(config.gateway_url, config.api_key, model=vectors.EMBED_ROLE)
    vector_cache = _private_vector_cache(state.dir, early)
    memory = RoomMemory(
        data_dir,
        scope=config.memory_scope,
        added_by=config.added_by,
        ingest_scope=memory_module.INGEST_SCOPE,
        semantic=SemanticRecall(
            embedder=embedder,
            vector_cache=vector_cache,
            similarity_threshold=config.similarity_threshold,
        ),
    )
    # The drain is the inbox's single writer. Spool degradations reach the
    # ledger through the drain's own forwarding — spool.on_degradation stays
    # unset, or each would be recorded twice.
    drain = drain_module.Drain(Spool.for_state_dir(state.dir), memory, record=sink("ingest"))
    library = _library_lane_from_env(
        library_config,
        state.dir,
        embedder,
        gate=lambda: bool(built) and built[0].live_turn_held(),
        early=early,
    )
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
        # The RAW seam: the app binds it to ``tools`` itself, and a typed turn
        # binds it again to its own registry (memory + library tools, t13).
        complete=seam,
        tools=tools,
        # The client's own degradations (SESSION_DROPPED, AUDIO_DROPPED,
        # NOT_CONNECTED …) reach the ledger through this hook (finding 14);
        # ``app`` is bound below and the factory only runs on attach, after.
        # Once per code here: the client already collapses its repeating
        # faults, and the count in status() carries the magnitude.
        factories=AppFactories(
            ears=lambda rate: RealtimeEars(
                replace(realtime, input_sample_rate=rate),
                on_degrade=lambda record: app._fold("ears", record, once=True),
            ),
            endpoint=HostEndpoint,
            voice=voice_factory,
        ),
        summarise=summarise,
        # The bus already redacts these on its way out; this is the other
        # direction — what the GATEWAY sends back.
        redact=secrets,
        lanes=AppLanes(drain=drain, embedder=embedder, vector_cache=vector_cache, library=library),
    )
    built.append(app)
    for code, reason, source in early:
        app._record(code, reason, source=source)
    try:
        app._server = server_module.DashboardServer(
            config=server_module.ServerConfig(
                bind=server_module.resolve_bind(config.bind, bind_public=config.bind_public),
                port=config.port,
                bind_public=config.bind_public,
                redact=secrets,
            ),
            guard=guard_module.Guard(
                guard_module.GuardConfig(
                    install_secret=secret.secret,
                    allowed_hosts=guard_module.DEFAULT_ALLOWED_HOSTS
                    | frozenset(guard_host_of(host) for host in config.allowed_hosts),
                    # Loopback FIRST: a default install's own dashboard sends
                    # an Origin on every POST (finding 2), then the operator's.
                    allowed_origins=frozenset(
                        allowed_origins_for(
                            (
                                *loopback_origin_hosts(config.port),
                                *config.allowed_hosts,
                                *((config.public_hostname,) if config.public_hostname else ()),
                            )
                        )
                    ),
                    public_hostname=config.public_hostname,
                ),
                assertion_verifier=verifier,
                on_degrade=sink("http"),
            ),
            bus=bus,
            controls=app.controls(),
            # The server notifies on the FIRST occurrence of each code only;
            # once here keeps the ledger to that as well (finding 14).
            on_degrade=lambda code, reason: app._record(code, reason, source="http", once=True),
        )
    except Exception as exc:  # noqa: BLE001  # no dashboard is a degradation, not a crash
        app._record(APP_BOOTSTRAP_DEGRADED, f"dashboard: {_describe(exc)}")
    return app
