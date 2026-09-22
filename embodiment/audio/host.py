"""The host loudspeaker/microphone :class:`~embodiment.audio.endpoint.AudioEndpoint`.

Plan task ``t7``, round 4. **Deviation d4 (operator decision):**
``sounddevice``/PortAudio is WITHDRAWN. This module now drives the
microphone and speaker as SUBPROCESSES — ``pw-record``/``pw-play``
(pipewire) or ``arecord``/``aplay`` (ALSA) — the same shape
``../lobes-cli/scripts/realtime-he-accept.py`` and
``../shabbos-goy/shabbos_goy/audio/pipewire.py`` already run in production
against the SAME hardware (the Seeed reSpeaker XVF3800 4-mic array). This is
a rewrite, not a patch: rounds 1-3's sounddevice-specific mechanics (open
retries, ``Raw*Stream``, per-direction stream objects) are gone, but their
LESSONS carry over unchanged and are re-applied against a process boundary
instead of a PortAudio one — named below wherever a round 1-3 finding
recurs in the new shape.

Cited, not imported (both sibling repos are read-only references; this
package still does not depend on either): ``select_channel`` (channel
selection never averages), the capture/playback ``argv`` shapes, and the
mic/speaker device-pairing discipline are the SAME functions, same
reasoning, as ``lobes-cli/scripts/realtime-he-accept.py``'s functions of the
same name (``select_channel``, ``build_capture_argv``,
``build_playback_argv``, ``validate_device_pair``/``DeviceMismatchError``).

The reference shape this module matches
------------------------------------------
Both sibling projects already answered "how does this exact device talk to
Linux": 2-channel capture at a FIXED 16 kHz (the reSpeaker refuses 24 kHz and
48 kHz on both directions — measured, round 3b), channel 1 selected (never a
downmix — the array's own measured evidence is that channel 1 carries less
echo residual and scores the lower WER), and playback at 24 kHz mono with
ALSA's ``plughw:`` layer (or pipewire) doing the down-conversion to whatever
the hardware wants. This module keeps that shape exactly:

- **Capture** always requests :data:`CAPTURE_RATE_HZ` (16000) /
  :data:`CAPTURE_CHANNELS` (2) from the subprocess, regardless of what the
  underlying device natively wants — ``plughw:``/pipewire do that
  conversion, the same way they already do for both sibling projects. What
  THIS module still owns is the channel selection
  (:func:`_select_channel`, cited from lobes-cli, reused unchanged).
- **Playback** always writes :data:`PLAYBACK_RATE_HZ` (24000, the fixed
  contract rate) / mono to the subprocess's stdin — ALSA/pipewire resample
  DOWN as needed, so this module has never owned a 24k -> 16k output
  resampler (round 3b built one; round 4 deleted it outright rather than
  leave a resampler nobody's code path reached).

**Round 6 — decision 15 (operator, issue #85), superseding the original
"resample to 24 kHz in Python" instruction, together with d4:** the ears now
send the device's NATIVE 16 kHz straight to the gateway — ``../shabbos-goy``
already runs this exact array this way, daily, validated by the operator —
rather than this module resampling capture up to the package's historical
24 kHz wire contract. Concretely: :meth:`HostEndpoint._capture_loop` delivers
:data:`CAPTURE_RATE_HZ` frames AS READ (channel-selected, never resampled);
the round 2/3/3b capture resampler (``Resampler``, its windowed-sinc
FIR/polyphase machinery, and their tests) is DELETED, not merely unused —
nothing in this module calls it any more, and a resampler nobody's code path
reaches is a resampler nobody's tests protect. A reference copy of the
polyphase design is kept in this task's own delivery notes/scratchpad, not
in this module. Callers that need to know the rate they are being handed
read :attr:`HostEndpoint.sample_rate` (part of
:class:`~embodiment.audio.endpoint.AudioEndpoint` since this decision) rather
than assuming :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` — the daemon
passes it to the realtime session as ``input_sample_rate``.

Round 7 (BLOCKER) — a pipewire target must be a NODE NAME, verified, never trusted
----------------------------------------------------------------------------------
An operator listening in the room found Gwen's reply coming out of the HDMI
MONITOR, not the reSpeaker — the array's hardware AEC never saw the far-end
reference, her own voice re-entered the mic as speech, and the daemon's
barge-in cut her off mid-sentence. Cause: the pipewire backend was passing
the ALSA CARD INDEX (e.g. ``"1"``) as ``pw-play --target 1``/``pw-record
--target 1``. Pipewire's ``--target`` takes a node name or id, never an ALSA
index; an unresolvable target silently falls back to the DEFAULT sink/source
rather than erroring — on the affected box the default sink was the HDMI, not
the array. This module now:

1. **Resolves BOTH the sink and the source by NAME**, never a card index —
   :meth:`HostEndpoint._resolve_pipewire_targets` parses ``pw-dump`` (JSON;
   :func:`_pw_find_device_node` matches a node's ``node.name`` /
   ``node.description`` / ``device.serial`` / ``alsa.card`` against a needle
   — the fixed default ``"XVF3800"``, or the operator's own configured
   substring/serial when ``device`` is given EXPLICITLY). An auto-detected
   ALSA card number is NEVER used as that needle even though ``device`` is
   also used for the alsa backend's ``plughw:<card>,0`` — a bare digit like
   ``"1"`` is a dangerously loose pipewire name substring (measured: it
   matched ``...pci-0000_00_01.0-hdmi...`` by accident in this task's own
   tests). The same-device rule from earlier rounds stays: a sink and source
   that resolve to different pipewire ``device.id``s is
   :data:`DEGRADED_DEVICE_UNRESOLVED`, exactly like an ambiguous name match.
2. **Verifies, does not trust.** After a capture/playback subprocess starts,
   :meth:`HostEndpoint._verify_pipewire_link` re-reads ``pw-dump`` (bounded
   retries, :data:`_PW_VERIFY_TOTAL_S`, since pipewire's own routing takes a
   moment), finds the Stream node OUR child created
   (:func:`_pw_find_stream_node` — see the round 9 section below for how
   this actually identifies OUR child, not just any pw-play/pw-record), and
   follows the Link (:func:`_pw_link_target_id`) to confirm it landed on the
   resolved node — never assumed from the ``--target`` argument alone. A
   mismatch is :data:`DEGRADED_DEVICE_MISMATCH` (a NAMED, COUNTED degradation
   — the node name itself is never put in a reason string), exposed as
   ``status()['playback_target_verified']``/``['capture_target_verified']``.
   A playback mismatch is NOT left running:
   :meth:`HostEndpoint._handle_playback_mismatch` SIGKILLs the wrongly-routed
   stream at once, the same discipline as a barge-in — audio must never keep
   flowing to a device this module could not confirm.
3. **``pw-dump`` missing or unparsable degrades to the ``alsa`` backend**
   (``plughw:`` is unambiguous — no name resolution needed) when ``arecord``/
   ``aplay`` are available, recorded as one event
   (:data:`DEGRADED_PIPEWIRE_UNAVAILABLE`); with no alsa fallback either,
   that becomes a construction-time fault. This module never guesses at an
   unverifiable pipewire target.

Round 9 — identifying OUR child's stream node, not just A pw-play's
--------------------------------------------------------------------------
Round 7's own fallback for "which Stream node is OUR subprocess" matched by
``application.name in {"pw-play", "pw-record"}`` whenever ``pw-dump`` set no
``application.process.id`` on the node itself — which the third review
measured as the PRODUCTION path on this box, not a rare fallback: a bare
``pw-play`` here sets neither pid field on its own Stream node. That name
match returns the FIRST such node, so with two ``pw-play`` processes running
at once (this project's OWN acoustic self-test plays a clip through a second
``pw-play`` on the HDMI sink while a reply is live on the array) it could
silently confirm the WRONG stream and report ``verified=True`` either way —
the hazard round 7 exists to catch, reintroduced by round 7's own fallback.

The pid is one hop further, not absent: measured live, the Stream node's
``client.id`` names a ``PipeWire:Interface:Client`` object, and THAT object
carries ``pipewire.sec.pid``/``application.process.id`` — pipewire's own
connection-layer pid, not client-declared metadata. :func:`_pw_client_pids`
reads it; :func:`_pw_find_stream_node` now resolves, in order: (1) the
node's own ``application.process.id`` when a pipewire build does set it, (2)
the node's ``client.id`` through :func:`_pw_client_pids` — the path actually
exercised on this box — and only then (3) a name match, and ONLY when it
yields EXACTLY ONE un-pid-resolved candidate. More than one is
``ambiguous=True``: :meth:`HostEndpoint._verify_pipewire_link` counts it
(``playback_target_ambiguous_count``/``capture_target_ambiguous_count``,
direction-tagged) and treats it exactly like "not yet found" — it can expire
into an honest ``verified=False``, never a ``True`` built on a guess.

Round 8 — a stream can be correctly routed and still be too quiet to hear
--------------------------------------------------------------------------
The operator's own ears, with Gwen live on the array: her replies were "very
very silent." The stream WAS correctly routed (round 7's own fix held) — the
pipewire SINK itself was sitting at volume 0.41 while the unrelated HDMI sink
sat at 0.97. Nothing in ``status()`` said so: a presence that speaks at 41%
looks attentive and is not (C3 — degradation must be observable to the host).
Cited, not imported: ``shabbos-goy``'s ``audio/pipewire.py`` already reads
(and, there, also owns) a sink's volume through ``wpctl`` against this same
hardware. This module only ever READS it — changing the system volume is the
operator's mixer, not this endpoint's job; a later, explicit config knob may
act on what is read here.

After the pipewire sink is resolved (:meth:`HostEndpoint._resolve_pipewire_targets`),
:meth:`HostEndpoint._run_wpctl_get_volume` runs ``wpctl get-volume <sink id>``
(a fixed argv, :data:`_WPCTL_TIMEOUT_S` bound, same discipline as
:meth:`HostEndpoint._run_pw_dump`) and parses its one line of output
(``"Volume: 0.41"``, optionally followed by ``"[MUTED]"``) with
:data:`_WPCTL_VOLUME_RE`. Every failure path — the binary missing, a timeout,
a non-zero exit, or output that does not match the expected shape — is
counted (never silently swallowed) and reports ``(None, None)``, never
raises. The result is exposed as ``status()['playback_volume']`` (a float, or
``None`` when it could not be read) and ``status()['playback_muted_by_system']``
(a bool, or ``None``). When the volume is below the named floor
(:data:`PLAYBACK_VOLUME_FLOOR`, 0.7) or the sink reports itself system-muted,
this module records ONE ``degraded`` event under
:data:`DEGRADED_PLAYBACK_QUIET` — today that means once per construction-time
pipewire-target resolution, since :meth:`HostEndpoint.attach` does not
currently retrigger resolution; "once per attach" and "once per resolution"
are the same event today and will only diverge if a future round makes
``attach()`` re-resolve.

The ``alsa`` backend has no equivalent step: ``amixer -c <card> sget``
parsing was explicitly left out of this round's scope, so
``playback_volume``/``playback_muted_by_system`` are always ``None`` on that
backend rather than a half-implemented guess.

Round 8 addendum — barge-in "isn't perfect": pw-play's own 100 ms buffer
--------------------------------------------------------------------------
The operator reported Gwen sounding briefly after a barge-in. Measured
(``pw-play --help``, ``pw-dump``'s ``node.latency``): pw-play defaults to a
100 ms internal buffer, on top of this module's own :data:`_PACE_LEAD_S`
(previously 100 ms), on top of the server VAD's own onset (~32-100 ms, not
this module's). Two changes, both requesting less buffering rather than
detecting an actual underrun (this module still cannot see one — see below):
:data:`PLAYER_LATENCY_MS` (40 ms) is now passed explicitly to the player
(``pw-play --latency``/``aplay --buffer-time``, see :func:`_build_playback_argv`),
and :data:`_PACE_LEAD_S` is lowered to 60 ms. Exposed as
``status()['playback_latency_ms']``. This module still cannot observe an
actual underrun directly — ``pw-play`` prints nothing on one — so an xrun
would only ever surface here as :data:`DEGRADED_WRITE_FAILED`/EPIPE, same as
any other dead-player fault; if a future round adds real xrun detection, tune
:data:`PLAYER_LATENCY_MS` back up rather than trusting silence as evidence of
none. **Not yet measured against real hardware** — the coordinator's own
before/after probe (play a tone, call `stop_playback()` mid-reply, time until
the array's own capture RMS drops back to quiet) is explicitly deferred until
the coordinator gives the word; only the argv/constant change and its unit
test are in this round.

No voice, never a raise
------------------------
- :data:`DEGRADED_NO_BACKEND` — neither ``pw-record``/``pw-play`` nor
  ``arecord``/``aplay`` are on ``PATH`` (checked once, at construction, via
  ``shutil.which`` — never a subprocess spawn just to probe for one).
- :data:`DEGRADED_DEVICE_UNRESOLVED` — no explicit device was configured and
  auto-detecting the reSpeaker by name (``/proc/asound/cards``, matching
  ``"XVF3800"``) found more than one candidate. Zero candidates is NOT a
  fault — it falls back to the ALSA/pipewire default device, the same as no
  device being named at all. The candidate COUNT is recorded, never a raw
  device name (config content is not reason-field content, the same
  discipline round 3b applied to a name-substring match).
- :data:`DEGRADED_OPEN` — the capture or playback subprocess could not even
  be started (``OSError`` from ``Popen`` — usually the binary vanishing
  between the ``which`` check and the spawn). Tracked PER DIRECTION
  (round 2 finding 6b's lesson: one direction's fault must never block the
  other), with the SAME cooldown-before-retry discipline round 3 finding 1
  built for a dead output device — a live TTS reply calls
  :meth:`~HostEndpoint.play` roughly every 20 ms, and retrying a `Popen`
  that keeps failing on every single call would be exactly the "200
  attempts across 100 calls" defect round 3 fixed, just with a process spawn
  in place of a PortAudio open.
- :data:`DEGRADED_WRITE_FAILED` — the playback subprocess died mid-reply
  (``BrokenPipeError``/``OSError`` writing to its stdin — round 3 finding 2,
  recurring at a pipe instead of a PortAudio stream). Same fix: close, drop,
  cooldown, never a silent ``callback_errors`` bump.
- :data:`DEGRADED_CAPTURE_ENDED` — the capture subprocess exited (EOF on its
  stdout) without :meth:`~HostEndpoint.stop_capture` asking it to.
- :data:`DEGRADED_DEVICE_MISMATCH` — round 7 (BLOCKER): a started stream
  verifiably linked to a DIFFERENT pipewire node than the one this module
  resolved and asked for. See the round 7 section below.
- :data:`DEGRADED_PIPEWIRE_UNAVAILABLE` — round 7: ``pw-dump`` is missing or
  unparsable, so no pipewire target could be verified.
- :data:`DEGRADED_PLAYBACK_QUIET` — round 8: the resolved pipewire sink is
  below :data:`PLAYBACK_VOLUME_FLOOR` or system-muted, read via ``wpctl``,
  never changed by this module. See the round 8 section above.

All eight never raise. The first two, and any pipewire target-resolution
fault (round 7) or volume read (round 8), are checked once, at construction.

Playback still buffers the WHOLE reply (round 2 finding 1, unchanged)
--------------------------------------------------------------------------
Every chunk :meth:`~HostEndpoint.play` receives is part of one sentence,
still queued in a byte-bounded FIFO (:data:`_PLAYBACK_BUFFER_SECONDS`, 120 s)
rather than a drop-oldest queue — a TTS stream delivering faster than
realtime must never silently lose the middle of a reply. Hitting the bound
refuses the NEW chunk and records one ``degraded`` event per overflow
episode under :data:`DEGRADED_PLAYBACK_OVERFLOW` (round 3 finding 3).

Barge-in is simpler with a process boundary, not just different
---------------------------------------------------------------------
Round 2/3 spent real effort making ``stop_playback()`` cut sounding audio
without a cheap way to interrupt a blocking device write — slicing every
``stream.write()`` to 20 ms, a "generation" stamp checked between slices, an
``abort()``-if-available-else-reopen dance. A subprocess makes the CUT
structurally simpler: closing stdin and SIGKILLing the player stops the
sound at the OS level almost immediately, no per-write slicing needed for
the cut itself. The trade-off: there is no cheap "keep the pipe open across
a barge-in" the way round 3's follow-up fix kept a sounddevice stream open
by calling ``abort()`` — every barge-in costs a fresh process spawn on the
next ``play()``.

Round 5: pacing is a SEPARATE problem the cut alone did not solve
---------------------------------------------------------------------
A live device probe of round 4 found that ``play()`` handed a whole reply to
the writer, which wrote it into the player's stdin as fast as the pipe would
take it — a 3 s reply landed in ``pw-play``'s stdin within 0.3 s. Three
consequences: (a) ``playback_written_samples``/``playing`` stopped
describing what was actually SOUNDING — ``playing`` went ``False`` while
~2.7 s of audio was still buffered in the pipe/player; (b) a barge-in had
nothing left in THIS module's own FIFO to discard, so ``stop_playback()``
had to fall back to killing a process that already held seconds of unplayed
audio; (c) that fallback used :meth:`~HostEndpoint._terminate_process`'s
graceful SIGTERM-then-wait path, which could take up to
:data:`_TERMINATE_TIMEOUT_S` (3 s) — measured at 1.3 s on one run, far past
the ~200 ms barge-in bound the voice works to.

Both halves are fixed. :meth:`HostEndpoint._writer_loop` now PACES itself
against a monotonic clock, writing :data:`_WRITE_SLICE_MS` (20 ms) slices
and staying at most :data:`_PACE_LEAD_S` (100 ms) ahead of real playback
time — slicing across `play()` call boundaries, since a caller's own
chunking has nothing to do with a pacing granularity. This means the FIFO
(:attr:`HostEndpoint._playback_chunks` plus the writer's own
:attr:`~HostEndpoint._writer_pending` tail) genuinely holds "the rest of the
reply" at any moment, so ``written`` approximates played, ``playing`` is
true while audio is actually sounding, and :meth:`~HostEndpoint.stop_playback`
has a real, non-zero count to discard. And ``stop_playback()`` itself no
longer goes through the graceful path at all: it closes stdin and SIGKILLs
the player AT ONCE (:meth:`~HostEndpoint._kill_process_fast`, bounded by
:data:`_BARGE_IN_KILL_TIMEOUT_S`) — a player process is disposable, and the
next ``play()`` simply respawns one. :data:`_PACE_LEAD_S`'s ~100 ms is the
DOCUMENTED, unmeasured overshoot this buys: audio already written into the
pipe/player by the moment of a barge-in can still be heard for about that
long, which is the accepted cost of never letting the player underrun
between writer wake-ups. :meth:`~HostEndpoint.close`, unlike
``stop_playback()``, keeps its graceful natural-EOF drain window — a normal
end of session is not an interruption.

Mute is enforced in the capture path (plan obligation ``o8``), unchanged
-----------------------------------------------------------------------
The drop happens in :meth:`HostEndpoint._capture_loop`, BEFORE channel
selection — the only code path that ever calls the ``on_frame`` callback a
caller registered through
:meth:`~HostEndpoint.start_capture`. Bounded event log
(:data:`_MAX_RETAINED_EVENTS`) + an exact, never-capped mute-transition
counter: round 2 finding 6a's fix, unchanged by the rewrite.

Threads, and what changed about blocking them free (wave 1 lesson 6)
--------------------------------------------------------------------
Two threads this module owns: the capture reader and the playback writer.
Both read/write BLOCKING file objects (a subprocess's stdout/stdin pipe) —
there is no bounded ``queue.get(timeout=...)`` equivalent for a pipe read.
So :meth:`HostEndpoint._stop_capture` TERMINATES the capture subprocess
FIRST, before joining its reader thread: killing the process is what
unblocks the thread's ``stdout.read()`` call, not the ``threading.Event`` the
thread also checks between reads. The writer is simpler: the playback
subprocess is already torn down by :meth:`~HostEndpoint._stop_playback_internal`
(called first, in :meth:`~HostEndpoint.close`) before the writer is joined,
so a blocked ``stdin.write()`` fails fast (a broken pipe) rather than
hanging.

Exit codes and stderr: counted and classified, never copied into a record
---------------------------------------------------------------------------
A dead subprocess's stderr can carry a device path or name (ALSA/pipewire
error text routinely does). :func:`_describe_process_exit` reads it, but —
the same discipline :mod:`embodiment.safe_reason` applies to an exception
message — never returns the text itself: only its length and an 8-hex
fingerprint (:func:`embodiment.safe_reason.name_fingerprint`), plus the
process's own exit code (an integer, never text).

Never ``shell=True``; argv lists only; the binaries' absence is a
degradation, never an exception (``shutil.which`` at construction, never a
speculative spawn); no audio is ever written to disk.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, no user input reaches argv
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from embodiment.audio.endpoint import (
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    EndpointCloseReport,
    EndpointDegradation,
    FrameCallback,
)
from embodiment.safe_reason import describe_exception, name_fingerprint

__all__ = [
    "DEGRADED_NO_BACKEND",
    "DEGRADED_DEVICE_UNRESOLVED",
    "DEGRADED_OPEN",
    "DEGRADED_WRITE_FAILED",
    "DEGRADED_CAPTURE_ENDED",
    "DEGRADED_PLAYBACK_OVERFLOW",
    "DEGRADED_DEVICE_MISMATCH",
    "DEGRADED_PIPEWIRE_UNAVAILABLE",
    "DEGRADED_PLAYBACK_QUIET",
    "CAPTURE_RATE_HZ",
    "CAPTURE_CHANNELS",
    "CAPTURE_CHANNEL_INDEX",
    "PLAYBACK_RATE_HZ",
    "PLAYBACK_CHANNELS",
    "PLAYBACK_VOLUME_FLOOR",
    "PLAYER_LATENCY_MS",
    "HostEndpoint",
]

#: Neither pipewire's nor ALSA's command-line tools are on PATH.
DEGRADED_NO_BACKEND = "audio-host-no-backend"
#: Auto-detecting the reSpeaker by name matched more than one candidate.
DEGRADED_DEVICE_UNRESOLVED = "audio-host-device-unresolved"
#: The capture or playback subprocess could not be started (tracked per direction).
DEGRADED_OPEN = "audio-host-open-failed"
#: A write to the playback subprocess's stdin raised — it died mid-reply.
DEGRADED_WRITE_FAILED = "audio-host-write-failed"
#: The capture subprocess exited (EOF) without stop_capture() asking it to.
DEGRADED_CAPTURE_ENDED = "audio-host-capture-ended"
#: A play() chunk was refused because the playback buffer is full.
DEGRADED_PLAYBACK_OVERFLOW = "audio-host-playback-overflow"
#: Round 7 (BLOCKER): the started stream verifiably linked to a DIFFERENT
#: pipewire node than the one this module resolved and asked for — the
#: round 7 defect itself (Gwen's reply came out of the HDMI monitor, not the
#: reSpeaker, because an unresolvable `--target` silently falls back to the
#: pipewire default sink/source).
DEGRADED_DEVICE_MISMATCH = "audio-host-device-mismatch"
#: `pw-dump` is missing or its output could not be parsed as JSON — this
#: module refuses to pass an unverifiable target to pw-play/pw-record and
#: either falls back to the alsa backend (if available) or, if not, treats
#: this as a construction-time fault (see `_resolve_pipewire_targets`).
DEGRADED_PIPEWIRE_UNAVAILABLE = "audio-host-pipewire-unavailable"

#: Fixed capture request, regardless of the device's own native rate — the
#: reSpeaker XVF3800 refuses anything else (measured, round 3b); ALSA's
#: ``plughw:``/pipewire perform the conversion for any device that needs one.
#: Since decision 15 (round 6), this is ALSO the rate frames are delivered to
#: ``on_frame`` at — see :attr:`HostEndpoint.sample_rate` — with no resample
#: to :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` in between.
CAPTURE_RATE_HZ = 16000
#: Always request 2 channels: this module selects ONE (see
#: :data:`CAPTURE_CHANNEL_INDEX`), never averages. A device with only one
#: channel still gets a mono request via ``plughw:``'s own upmix; this
#: module's own select-channel step degrades harmlessly on mono input
#: (``_select_channel`` is a passthrough when ``channels <= 1``).
CAPTURE_CHANNELS = 2
#: Which of the (up to) 2 requested channels this module keeps. Cited from
#: ``lobes-cli``'s own measured evidence: channel 1 carries less echo
#: residual and scored the lower WER on this exact hardware; a downmix would
#: mix the worse channel's residual back in.
CAPTURE_CHANNEL_INDEX = 1
#: The fixed wire contract rate — matches
#: :data:`embodiment.audio.endpoint.SAMPLE_RATE_HZ`. ALSA/pipewire resample
#: DOWN to whatever the device wants; this module no longer owns that leg.
PLAYBACK_RATE_HZ = SAMPLE_RATE_HZ
PLAYBACK_CHANNELS = 1

#: How long close()'s GRACEFUL playback teardown waits for SIGTERM before
#: sending SIGKILL. NOT used by stop_playback() (round 5) — a barge-in kills
#: at once (see :data:`_BARGE_IN_KILL_TIMEOUT_S`); a player process is
#: disposable and the next play() simply respawns one.
_TERMINATE_TIMEOUT_S = 3.0

#: How many SECONDS of audio the playback buffer holds before a NEW `play()`
#: chunk is refused (round 2 finding 1, unchanged) — sized in seconds, not
#: bytes or chunk count.
_PLAYBACK_BUFFER_SECONDS = 120.0

#: Capture reader chunk size: ~20 ms at the fixed capture rate/channel count.
_CAPTURE_CHUNK_BYTES = int(CAPTURE_RATE_HZ * 0.02) * CAPTURE_CHANNELS * SAMPLE_WIDTH_BYTES

#: How long a reader/writer thread waits on an idle queue before re-checking
#: its stop signal. Bounds shutdown latency without busy-waiting.
_POLL_INTERVAL_S = 0.1

#: Round 5: the writer paces itself against a monotonic clock in slices this
#: size, rather than dumping a whole `play()` chunk into the pipe/player at
#: once — round 4's own probe measured a 3 s reply written to pw-play's
#: stdin within 0.3 s, meaning `stop_playback()` had nothing left in the
#: FIFO to discard and had to fall back to killing a process that already
#: had ~2.7 s of audio queued past what the OS/player could possibly have
#: sounded yet.
_WRITE_SLICE_MS = 20

#: How far AHEAD of real playback time the writer is allowed to stay written
#: into the pipe/player before pausing (round 5). A judgement call, stated
#: because it is one: large enough that the player's own internal buffering
#: never underruns between writer wake-ups (a stall is audible as a click or
#: gap — the failure mode a lead protects against), small enough that a
#: barge-in's kill only has to discard/lose about this much already-written-
#: but-not-yet-sounding audio, keeping `stop_playback()` well under the
#: 200 ms barge-in bound the voice works to. Round 8 addendum: the operator
#: measured Gwen sounding briefly after a barge-in — pw-play's OWN default
#: node latency (100 ms) plus this 100 ms pacing lead plus the server VAD's
#: own onset (~32-100 ms, not this module's to shrink) summed to ~200 ms of
#: audible overshoot. Lowered to 60 ms here, alongside
#: :data:`PLAYER_LATENCY_MS` shrinking the player's own buffer — still
#: documented, still unmeasured on a real barge-in until the coordinator's
#: probe runs (see the module docstring's round 8 addendum).
_PACE_LEAD_S = 0.06

#: Round 8 addendum — the player's OWN internal buffer, requested explicitly
#: rather than left at pw-play's 100 ms default. The array's sink runs a
#: 1024/48000 quantum, ~21 ms; 40 ms leaves roughly two quanta of headroom
#: against an underrun while roughly halving the worst-case barge-in
#: overshoot pw-play's default contributed. Passed to pw-play as
#: ``--latency <PLAYER_LATENCY_MS>ms``; the alsa backend's closest equivalent
#: is ``aplay --buffer-time=<PLAYER_LATENCY_MS * 1000>`` (aplay's
#: ``--buffer-time`` is in MICROSECONDS, not ms — the conversion happens once,
#: here, never re-derived at each call site).
PLAYER_LATENCY_MS = 40

#: How long stop_playback() waits for SIGKILL to land before giving up and
#: reporting the process as not-confirmed-dead (round 5). Bounds
#: stop_playback() itself, not just the audio's own cutoff — SIGKILL cannot
#: be ignored by the child, so this is a wait for the OS to reap it, not a
#: grace period the child gets to use.
_BARGE_IN_KILL_TIMEOUT_S = 0.15

#: Round 3 finding 1's cooldown, unchanged in shape, now guarding a process
#: spawn instead of a PortAudio open: 2 s doubling to a 30 s cap.
_OPEN_COOLDOWN_BASE_S = 2.0
_OPEN_COOLDOWN_MAX_S = 30.0

#: How many events (mute changes, per-direction recovery) this module keeps
#: in the retained log before evicting the oldest (round 2 finding 6a). The
#: EXACT count of genuine mute transitions is tracked separately and never
#: capped — only the detailed per-event log has a memory bound.
_MAX_RETAINED_EVENTS = 1000

#: How long a single `pw-dump` invocation is allowed to run before this
#: module gives up on it (round 7). A judgement call: `pw-dump` is a local
#: IPC query, not a network call — a few seconds is generous headroom for a
#: busy box, well short of blocking a caller indefinitely.
_PW_DUMP_TIMEOUT_S = 5.0

#: Round 7's link-verification retry budget: pipewire's own routing takes a
#: moment after a stream is created, so a single immediate `pw-dump` can
#: read the link before it exists. Polled every `_PW_VERIFY_POLL_S` up to
#: `_PW_VERIFY_TOTAL_S` total before a still-missing link is treated as a
#: genuine mismatch rather than a race.
_PW_VERIFY_TOTAL_S = 0.3
_PW_VERIFY_POLL_S = 0.05

#: Round 8 — the operator's ears found Gwen "very very silent": the array's
#: pipewire SINK sat at volume 0.41 while HDMI sat at 0.97, and nothing in
#: `status()` said so. A presence that speaks at 41% looks attentive and is
#: not (C3). Cited from ``shabbos-goy``'s ``audio/pipewire.py`` ("capture,
#: playback, own volume") — cite, don't import: that module owns its volume
#: through `wpctl`; this one only ever READS it. Below this floor, or when
#: the sink reports itself system-muted, this module records
#: `DEGRADED_PLAYBACK_QUIET` — it never changes the volume itself; that stays
#: the operator's mixer (a later, explicit config knob may act on it).
PLAYBACK_VOLUME_FLOOR = 0.7
#: The pipewire sink this module resolved is quiet or system-muted — read via
#: `wpctl get-volume`, recorded once per attach (mapped today to the single
#: construction-time pipewire-target resolution, since `attach()` itself does
#: not currently retrigger resolution).
DEGRADED_PLAYBACK_QUIET = "audio-host-playback-quiet"
#: `wpctl get-volume` is a local IPC query like `pw-dump`; the same generous,
#: bounded budget applies.
_WPCTL_TIMEOUT_S = 5.0

#: Round 8's "Volume: 0.41" / "Volume: 1.00 [MUTED]" line, tolerant of any
#: amount of internal whitespace and independent of the MUTED suffix's
#: presence.
_WPCTL_VOLUME_RE = re.compile(r"Volume:\s*([0-9]*\.?[0-9]+)")


def _default_which(name: str) -> str | None:
    """The ONE place ``shutil.which`` is spelled out — tests inject a fake."""
    return shutil.which(name)


def _select_backend(which: Callable[[str], str | None]) -> str | None:
    """``"pipewire"`` / ``"alsa"`` / ``None`` — checked once, at construction.

    Never spawns anything: a PATH lookup only. Preference order matches both
    sibling projects: pipewire first (this rig runs behind it), ALSA as the
    fallback.
    """
    if which("pw-record") and which("pw-play"):
        return "pipewire"
    if which("arecord") and which("aplay"):
        return "alsa"
    return None


def _find_xvf3800_cards(cards_path: Path) -> list[str]:
    """ALSA card numbers whose ``/proc/asound/cards`` line names the reSpeaker.

    Never raises: a missing or unreadable file (a non-Linux host, a
    container without ``/proc/asound``) degrades to an empty list, which
    :meth:`HostEndpoint._resolve_device` reads as "fall back to the
    ALSA/pipewire default", not as a fault.
    """
    try:
        text = cards_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for line in text.splitlines():
        if "XVF3800" not in line:
            continue
        head = line.strip().split(None, 1)
        if head and head[0].isdigit():
            found.append(head[0])
    return found


def _build_capture_argv(backend: str, device: object, rate: int, channels: int) -> list[str]:
    """Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``build_capture_argv``.

    Round 7: for the ``pipewire`` backend, *device* MUST be a resolved
    pipewire ``node.name`` (:func:`HostEndpoint._resolve_pipewire_targets`)
    — never an ALSA card index. ``pw-play``/``pw-record --target`` accepts a
    node name or id; an unresolvable value silently falls back to the
    DEFAULT sink/source rather than erroring, which is exactly the round 7
    blocker (a card index landed on the wrong device by luck). The ``alsa``
    backend is unaffected: *device* there is still the ALSA card number from
    ``/proc/asound/cards``.
    """
    if backend == "alsa":
        target = f"plughw:{device},0" if device is not None else "default"
        return [
            "arecord",
            "-D",
            target,
            "-f",
            "S16_LE",
            "-r",
            str(rate),
            "-c",
            str(channels),
            "-t",
            "raw",
            "-q",
        ]
    argv = ["pw-record"]
    if device is not None:
        argv += ["--target", str(device)]
    argv += ["--rate", str(rate), "--channels", str(channels), "--format", "s16", "-"]
    return argv


def _build_playback_argv(backend: str, device: object, rate: int, channels: int) -> list[str]:
    """Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``build_playback_argv``.

    Round 7: same node-name-not-card-index rule as :func:`_build_capture_argv`.
    Round 8 addendum: requests :data:`PLAYER_LATENCY_MS` explicitly rather
    than leaving pw-play at its own 100 ms default — see that constant's
    docstring. ``aplay``'s closest equivalent is ``--buffer-time``, which
    aplay documents in MICROSECONDS; if a future alsa-utils build ever
    rejects the flag, that is a construction-time surprise this module has
    not yet needed to degrade around, since ``--buffer-time`` has shipped in
    alsa-utils for well over a decade.
    """
    if backend == "alsa":
        target = f"plughw:{device},0" if device is not None else "default"
        return [
            "aplay",
            "-D",
            target,
            "-f",
            "S16_LE",
            "-r",
            str(rate),
            "-c",
            str(channels),
            "-t",
            "raw",
            "-q",
            "--buffer-time",
            str(PLAYER_LATENCY_MS * 1000),
        ]
    argv = ["pw-play"]
    if device is not None:
        argv += ["--target", str(device)]
    argv += [
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        "--format",
        "s16",
        "--latency",
        f"{PLAYER_LATENCY_MS}ms",
        "-",
    ]
    return argv


# ---------------------------------------------------------------------------
# pipewire node resolution + link verification (round 7)
# ---------------------------------------------------------------------------

#: pipewire media.class values this module cares about.
_PW_SINK_CLASS = "Audio/Sink"
_PW_SOURCE_CLASS = "Audio/Source"
_PW_STREAM_OUTPUT_CLASS = "Stream/Output/Audio"  # a pw-play process
_PW_STREAM_INPUT_CLASS = "Stream/Input/Audio"  # a pw-record process


def _pw_nodes_by_class(dump: list[object], media_class: str) -> list[dict[str, object]]:
    """Every ``PipeWire:Interface:Node`` object in *dump* with the given ``media.class``."""
    found: list[dict[str, object]] = []
    for obj in dump:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        if props.get("media.class") == media_class:
            found.append({"id": obj.get("id"), "props": props})
    return found


def _pw_find_device_node(
    dump: list[object], needle: str, media_class: str
) -> dict[str, object] | None:
    """The Sink/Source node whose name/description/serial/alsa.card matches *needle*.

    Matched against ``node.name``, ``node.description``, ``device.serial``
    and ``alsa.card`` — the same broad match the round 3b addendum used for
    ALSA device names, now applied to pipewire's own node properties. The
    FIRST match wins; a needle that matches more than one node is a config
    problem the operator resolves by narrowing it, not something this
    function tries to disambiguate further.
    """
    needle_lower = needle.lower()
    for node in _pw_nodes_by_class(dump, media_class):
        props = node["props"]
        assert isinstance(props, dict)
        haystacks = (
            props.get("node.name"),
            props.get("node.description"),
            props.get("device.serial"),
            props.get("alsa.card"),
        )
        if any(needle_lower in str(h).lower() for h in haystacks if h):
            return {
                "id": node["id"],
                "name": props.get("node.name"),
                "device_id": props.get("device.id"),
            }
    return None


def _pw_client_pids(dump: list[object]) -> dict[object, int]:
    """``PipeWire:Interface:Client`` object id -> its OS pid (round 9 finding 1).

    Prefers ``pipewire.sec.pid`` — the value pipewire's OWN security layer
    attached to the connection, not client-declared metadata — falling back
    to ``application.process.id`` on the Client object when the former is
    absent. This is the pid a Stream Node's ``client.id`` resolves through:
    measured live on this box, a bare ``pw-play`` sets NEITHER pid field on
    its own Stream node, only on the Client object that owns it.
    """
    pids: dict[object, int] = {}
    for obj in dump:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Client":
            continue
        info = obj.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        raw = props.get("pipewire.sec.pid")
        if raw is None:
            raw = props.get("application.process.id")
        try:
            if raw is not None:
                pids[obj.get("id")] = int(raw)
        except (TypeError, ValueError):
            continue
    return pids


def _pw_find_stream_node(
    dump: list[object], media_class: str, pid: int
) -> "tuple[dict[str, object] | None, bool]":
    """The Stream node OUR subprocess created — ``(node, ambiguous)``.

    Round 9 finding 1 (BLOCKER, superseding round 7's own fallback): a bare
    ``pw-play`` on this box sets NEITHER ``application.process.id`` NOR any
    other pid field on its own Stream node — round 7's fallback matched by
    ``application.name`` alone, and with two ``pw-play`` processes running at
    once (this project's own acoustic self-test plays a clip through a
    second ``pw-play`` on the HDMI sink while a reply is live on the array)
    that fallback could silently confirm the WRONG stream and report
    ``verified=True``. The pid IS discoverable, just one hop further: the
    Stream node's ``client.id`` names a ``PipeWire:Interface:Client`` object,
    and THAT object carries ``pipewire.sec.pid``/``application.process.id``
    (see :func:`_pw_client_pids`) — measured live and now the primary path.

    Resolution order: (1) the node's own ``application.process.id``, when a
    pipewire build/config DOES set it; (2) the node's ``client.id`` resolved
    through :func:`_pw_client_pids`; (3) a name match
    (``application.name in {"pw-play", "pw-record"}``), but ONLY when it
    yields EXACTLY ONE candidate among nodes neither pid path resolved —
    more than one is reported ``ambiguous=True`` with ``node=None``, never a
    guess. The caller must treat ``ambiguous`` the same as "not yet found":
    it is never allowed to become ``verified=True``.
    """
    candidates = _pw_nodes_by_class(dump, media_class)
    for node in candidates:
        props = node["props"]
        assert isinstance(props, dict)
        raw_pid = props.get("application.process.id")
        try:
            if raw_pid is not None and int(raw_pid) == pid:
                return node, False
        except (TypeError, ValueError):
            continue
    client_pids = _pw_client_pids(dump)
    for node in candidates:
        props = node["props"]
        assert isinstance(props, dict)
        client_pid = client_pids.get(props.get("client.id"))
        if client_pid is not None and client_pid == pid:
            return node, False
    name_matches = [
        node
        for node in candidates
        if str(node["props"].get("application.name") or "") in ("pw-play", "pw-record")
    ]
    if len(name_matches) == 1:
        return name_matches[0], False
    if len(name_matches) > 1:
        return None, True
    return None, False


def _pw_link_target_id(dump: list[object], stream_node_id: object, *, as_output: bool) -> object:
    """What *stream_node_id* is linked to, via a ``PipeWire:Interface:Link`` object.

    ``as_output=True`` (playback): the stream is the link's OUTPUT side —
    returns what it feeds (the sink's node id). ``as_output=False``
    (capture): the stream is the link's INPUT side — returns what feeds it
    (the source's node id). ``None`` if no matching link exists (yet, or at
    all — a caller retries with a bounded budget rather than treating a
    single miss as final, since pipewire's own routing takes a moment).
    """
    for obj in dump:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info")
        if not isinstance(info, dict):
            continue
        out_id = info.get("output-node-id")
        in_id = info.get("input-node-id")
        if as_output and out_id == stream_node_id:
            return in_id
        if not as_output and in_id == stream_node_id:
            return out_id
    return None


def _describe_process_exit(proc: "subprocess.Popen[bytes]") -> str:
    """The exit code and stderr's LENGTH+FINGERPRINT — never the stderr text itself.

    ALSA/pipewire error text routinely names a device path; this module
    applies the same discipline :mod:`embodiment.safe_reason` applies to an
    exception message.
    """
    code = proc.poll()
    text = ""
    try:
        if proc.stderr is not None:
            raw = proc.stderr.read()
            if raw:
                text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - reading a dead process's stderr is best-effort
        text = ""
    return f"exit={code} stderr: {len(text)} chars, fp:{name_fingerprint(text)}"


def _select_channel(np: Any, raw: bytes, channels: int, channel_index: int) -> bytes:
    """Pick ONE channel out of interleaved multi-channel pcm16 — never average.

    Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``select_channel``
    (same reasoning: the reSpeaker XVF3800's channel 1 carries less echo
    residual and scored the lowest WER; a downmix mixes the worse channel's
    residual back in). Never raises: malformed input degrades to ``b""``.
    """
    if channels <= 1:
        return raw
    frame_bytes = SAMPLE_WIDTH_BYTES * channels
    usable = raw[: len(raw) - (len(raw) % frame_bytes)]
    if not usable:
        return b""
    idx = min(max(channel_index, 0), channels - 1)
    samples = np.frombuffer(usable, dtype="<i2").reshape(-1, channels)
    return np.ascontiguousarray(samples[:, idx]).tobytes()


def _import_numpy() -> Any:
    """Lazy numpy import point — never at module scope. Used only by
    :func:`_select_channel` (round 6 deleted the capture resampler that was
    this function's other caller — see the module docstring, decision 15);
    numpy itself is an existing approved runtime import
    (``embodiment.continuity``, via ``coherence-cli`` — see ``tests/test_zero_deps.py``),
    so this module pays nothing extra by using it, but stays lazy on its own merits: a
    host that never captures audio should not pay to import it either."""
    import numpy

    return numpy


class HostEndpoint:
    """Subprocess-driven :class:`~embodiment.audio.endpoint.AudioEndpoint` (round 4, d4).

    Args:
        device: names the SAME physical device for BOTH directions (the
            array's hardware AEC needs its own output as the far-end
            reference — lobes-cli refuses a split mic/speaker pair for
            exactly this reason, and this module structurally cannot
            construct one, since there is only ever one setting). An ALSA
            card number (``"1"``) for the ``alsa`` backend, or a pipewire
            target name/id for ``pipewire``. ``None`` (the default)
            auto-detects the reSpeaker via ``/proc/asound/cards``,
            falling back to the ALSA/pipewire default device if none (or
            more than one) is found.
        backend: force ``"pipewire"`` or ``"alsa"``. ``None`` (the default)
            auto-selects via :func:`_select_backend`.
        which: injection seam for :func:`shutil.which` (tests fake PATH
            lookups without touching the real ``PATH``).
        popen: injection seam for :class:`subprocess.Popen` (tests substitute
            a real small Python child process — never a mock — per the
            round 4 brief).
        cards_path: injection seam for ``/proc/asound/cards``.
        queue_maxsize: unused placeholder kept for signature stability with
            earlier rounds; the playback bound is seconds-based (see
            :data:`_PLAYBACK_BUFFER_SECONDS`) and capture has no bounded
            queue any more — the reader thread does the whole pipeline
            inline (round 4: no PortAudio realtime-callback constraint to
            keep a queue between).
        numpy_importer: injection seam for the lazy numpy import.
    """

    def __init__(
        self,
        *,
        device: object = None,
        backend: str | None = None,
        which: Callable[[str], str | None] = _default_which,
        popen: Callable[..., "subprocess.Popen[bytes]"] = subprocess.Popen,
        cards_path: Path = Path("/proc/asound/cards"),
        queue_maxsize: int = 0,
        numpy_importer: Callable[[], Any] = _import_numpy,
    ) -> None:
        self._which = which
        self._popen = popen
        self._cards_path = cards_path
        self._np_importer = numpy_importer

        self._counter_lock = threading.RLock()
        self._muted = False
        self._events: "deque[dict[str, object]]" = deque(maxlen=_MAX_RETAINED_EVENTS)
        self._mute_event_count = 0
        self._attached = False
        self._capturing = False
        self._closed = False
        self._on_frame: FrameCallback | None = None

        self._capture_proc: "subprocess.Popen[bytes] | None" = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        self._playback_proc: "subprocess.Popen[bytes] | None" = None
        self._playback_chunks: "deque[bytes]" = deque()
        self._playback_queued_bytes = 0
        self._writer_pending = b""
        self._playback_written_samples = 0
        self._playback_total_pushed_samples = 0
        self._playback_stop_discarded_total = 0
        self._playback_overflow_count = 0
        self._playback_overflow_episode_active = False
        self._playback_generation = 0
        self._playing = False
        self._writer_thread: threading.Thread | None = None
        self._writer_stop = threading.Event()

        self._capture_muted_dropped = 0
        self._callback_errors = 0
        self._playback_dropped_no_device = 0
        self._output_degrade_attempts = 0
        self._out_open_cooldown_until = 0.0
        self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S

        self._last_close_report: EndpointCloseReport | None = None

        # Round 7b finding 2: a child SIGKILL could not confirm dead within
        # its wait bound becomes a zombie until reaped — tracked here and
        # retried (non-blocking) at every later stop/close.
        self._unreaped: "list[subprocess.Popen[bytes]]" = []

        self._degradation: EndpointDegradation | None = None
        self._degradation_in: EndpointDegradation | None = None
        self._degradation_out: EndpointDegradation | None = None

        # Round 7: resolved pipewire node identity (never an ALSA card
        # index) and the verify-don't-trust bookkeeping.
        self._pw_sink_node_id: object = None
        self._pw_sink_node_name: str | None = None
        self._pw_source_node_id: object = None
        self._pw_source_node_name: str | None = None
        self._playback_target_verified: bool | None = None
        self._capture_target_verified: bool | None = None
        self._playback_target_mismatch_count = 0
        self._capture_target_mismatch_count = 0
        # Round 9 finding 1: the stream-node pid match landed on more than
        # one un-pid-resolved pw-play/pw-record candidate — never a guess,
        # counted and treated as unverified.
        self._playback_target_ambiguous_count = 0
        self._capture_target_ambiguous_count = 0

        # Round 8: the resolved sink's own volume, read (never set) via
        # `wpctl`. None until a pipewire sink is resolved and successfully
        # queried; stays None forever on the alsa backend.
        self._playback_volume: float | None = None
        self._playback_muted_by_system: bool | None = None
        self._playback_volume_unparsable_count = 0
        self._playback_quiet_count = 0

        self._backend = backend or _select_backend(self._which)
        if self._backend is None:
            self._degradation = EndpointDegradation(
                DEGRADED_NO_BACKEND, "no pipewire or alsa audio backend found on PATH"
            )

        # Whether `device` was configured EXPLICITLY, not auto-detected —
        # decides the pipewire matching needle below. An auto-detected ALSA
        # card number (a bare digit or two) is a terrible pipewire node.name
        # substring: "1" matched "...pci-0000_00_01.0-hdmi..." by accident
        # in testing. Only an explicit override is trusted as a name/serial
        # needle; auto-detect always falls back to the fixed "XVF3800".
        self._device_explicit = device is not None

        self._device: object = device
        if self._degradation is None and device is None:
            matches = _find_xvf3800_cards(self._cards_path)
            if len(matches) == 1:
                self._device = matches[0]
            elif len(matches) > 1:
                self._device = None
                self._degradation = EndpointDegradation(
                    DEGRADED_DEVICE_UNRESOLVED,
                    f"device auto-detect matched {len(matches)} candidates, need exactly 1",
                )
            # zero matches: fall back to the ALSA/pipewire default (None), not a fault.

        if self._degradation is None and self._backend == "pipewire":
            self._resolve_pipewire_targets()

    # -- shared helpers ------------------------------------------------

    def _record_event(self, event: dict[str, object]) -> None:
        with self._counter_lock:
            self._events.append(event)
            if event.get("type") == "mute":
                self._mute_event_count += 1

    def _enter_output_degradation_reason(self, code: str, reason: str) -> None:
        """One recorded episode + a cooldown (round 3 finding 1/2), from a PLAIN reason string.

        Used directly by round 7's device-mismatch path (there is no
        exception to describe — the fault is a fact this module observed,
        not a caught error) and via :meth:`_enter_output_degradation` for
        every exception-carrying caller — one code path (lesson 8).
        """
        is_new_episode = self._degradation_out is None
        self._degradation_out = EndpointDegradation(code, reason)
        if is_new_episode:
            self._record_event({"type": "degraded", "code": code, "direction": "out"})
        with self._counter_lock:
            self._output_degrade_attempts += 1
        self._out_open_cooldown_until = time.monotonic() + self._out_open_backoff_s
        self._out_open_backoff_s = min(_OPEN_COOLDOWN_MAX_S, self._out_open_backoff_s * 2.0)

    def _enter_output_degradation(self, code: str, exc: BaseException, action: str) -> None:
        """Round 3 finding 1/2, unchanged: one recorded episode + a cooldown."""
        self._enter_output_degradation_reason(code, f"{action}: {describe_exception(exc)}")

    def _terminate_process(
        self, proc: "subprocess.Popen[bytes]", timeout: float = _TERMINATE_TIMEOUT_S
    ) -> int:
        """Terminate, wait, kill if needed. Returns 1 if it never confirmed dead.

        Round 7b finding 2: a child that STILL will not confirm dead after
        the kill wait is not abandoned outright — it would become a zombie
        until this process happens to reap it. It goes into
        :attr:`_unreaped` instead, retried (non-blocking) by
        :meth:`_reap_unreaped` at every later stop/close.
        """
        if proc.poll() is not None:
            return 0
        try:
            proc.terminate()
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
        try:
            proc.wait(timeout=max(0.0, timeout))
            return 0
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=max(0.0, timeout))
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
            if proc.poll() is not None:
                return 0
            self._unreaped.append(proc)
            return 1

    def _kill_process_fast(
        self, proc: "subprocess.Popen[bytes]", timeout: float = _BARGE_IN_KILL_TIMEOUT_S
    ) -> int:
        """SIGKILL at once, no SIGTERM grace period (round 5's barge-in path).

        A player process is disposable: the next play() simply respawns one.
        Waiting out a SIGTERM grace period before escalating (as
        :meth:`_terminate_process` does for a graceful close) is exactly what
        made round 4's own barge-in miss its 200 ms bound. Returns 1 if the
        OS never confirmed the kill within *timeout* (SIGKILL itself cannot
        be ignored, so this bounds the WAIT for reaping, not a grace period
        the child gets to use) — that child also joins :attr:`_unreaped`
        (round 7b finding 2), same as :meth:`_terminate_process`.
        """
        if proc.poll() is not None:
            return 0
        try:
            proc.kill()
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
        try:
            proc.wait(timeout=max(0.0, timeout))
            return 0
        except subprocess.TimeoutExpired:
            self._unreaped.append(proc)
            return 1

    def _reap_unreaped(self) -> None:
        """Non-blocking: poll() every previously-unconfirmed-dead child and
        drop it once the OS confirms it exited. Never blocks, never raises.
        """
        still: "list[subprocess.Popen[bytes]]" = []
        for proc in self._unreaped:
            try:
                if proc.poll() is None:
                    still.append(proc)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
        self._unreaped = still

    # -- lifecycle ---------------------------------------------------------

    def attach(self) -> None:
        if self._attached:
            return
        self._closed = False
        self._attached = True

    def detach(self) -> None:
        self.stop_playback()
        self._stop_capture(timeout=2.0)
        self._stop_writer(timeout=2.0)
        self._attached = False

    def close(self, deadline: float) -> EndpointCloseReport:
        deadline = max(0.0, float(deadline))
        start = time.monotonic()

        self._reap_unreaped()

        # A normal end of session (unlike stop_playback()'s barge-in) gets a
        # brief chance to exit on its own after EOF — up to a THIRD of the
        # deadline, so plenty is still left for the capture/writer joins
        # below even if the player uses its whole share.
        samples_discarded, playback_close_failures = self._stop_playback_internal(
            drain_timeout=deadline / 3.0
        )

        remaining = max(0.0, deadline - (time.monotonic() - start))
        capture_stopped, capture_close_failures = self._stop_capture(timeout=remaining / 2.0)

        remaining = max(0.0, deadline - (time.monotonic() - start))
        writer_stopped = self._stop_writer(timeout=remaining)

        # Once more (round 7b finding 2): catch anything that finished
        # dying during the teardown just above, so the report's count is as
        # fresh as this close() call can make it.
        self._reap_unreaped()

        self._attached = False
        self._closed = True
        elapsed = time.monotonic() - start
        report = EndpointCloseReport(
            capture_thread_stopped=capture_stopped,
            writer_thread_stopped=writer_stopped,
            samples_discarded=samples_discarded,
            elapsed_s=elapsed,
            streams_close_failed=playback_close_failures + capture_close_failures,
            children_unreaped=len(self._unreaped),
        )
        self._last_close_report = report
        return report

    # -- capture -------------------------------------------------------

    def start_capture(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        if self._degradation is not None or self._closed:
            return
        if self._capturing:
            return

        capture_target = self._pw_source_node_name if self._backend == "pipewire" else self._device
        argv = _build_capture_argv(self._backend, capture_target, CAPTURE_RATE_HZ, CAPTURE_CHANNELS)
        was_degraded = self._degradation_in is not None
        try:
            proc = self._popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            self._degradation_in = EndpointDegradation(
                DEGRADED_OPEN, f"capture subprocess failed to start: {describe_exception(exc)}"
            )
            return

        if was_degraded:
            self._degradation_in = None
            self._record_event({"type": "recovered", "direction": "in"})

        self._capture_proc = proc
        self._capture_stop.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(proc,),
            name="embodiment-audio-host-capture",
            daemon=True,
        )
        self._capture_thread.start()
        self._capturing = True
        self._attached = True

        # Round 7: verify, don't trust — confirm the stream actually linked
        # to the resolved source node rather than assuming --target worked.
        verified = self._verify_pipewire_link(proc, playback=False)
        if verified is not None:
            self._capture_target_verified = verified
            if not verified:
                with self._counter_lock:
                    self._capture_target_mismatch_count += 1
                self._degradation_in = EndpointDegradation(
                    DEGRADED_DEVICE_MISMATCH, "capture stream linked to an unexpected device"
                )

    def stop_capture(self) -> None:
        self._stop_capture(timeout=2.0)

    def _stop_capture(self, timeout: float) -> tuple[bool, int]:
        """Terminate the subprocess FIRST — that is what unblocks the reader's
        pipe read (see the module docstring's threads section)."""
        self._reap_unreaped()
        self._capture_stop.set()
        proc = self._capture_proc
        self._capture_proc = None
        half = max(0.0, timeout) / 2.0
        close_failures = 0
        if proc is not None:
            close_failures = self._terminate_process(proc, timeout=half)

        thread = self._capture_thread
        self._capture_thread = None
        stopped = True
        if thread is not None:
            thread.join(timeout=half)
            stopped = not thread.is_alive()
        self._capturing = False
        return stopped, close_failures

    def _capture_loop(self, proc: "subprocess.Popen[bytes]") -> None:
        """This module's own thread: read, mute-check, select channel, deliver.

        Mute drops bytes here, BEFORE channel selection and BEFORE delivery
        — before encode, matching plan obligation o8. Frames are delivered
        at :data:`CAPTURE_RATE_HZ` (16 kHz), the device's own native rate —
        decision 15 (operator, issue #85): NO resample to
        :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` happens here any
        more (see the module docstring). Never raises out.
        """
        stdout = proc.stdout
        ended_cleanly = False
        while not self._capture_stop.is_set():
            try:
                raw = stdout.read(_CAPTURE_CHUNK_BYTES) if stdout is not None else b""
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                break
            if not raw:
                ended_cleanly = True
                break

            if self._muted:
                with self._counter_lock:
                    self._capture_muted_dropped += 1
                continue

            try:
                np = self._np_importer()
                selected = _select_channel(np, raw, CAPTURE_CHANNELS, CAPTURE_CHANNEL_INDEX)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                continue

            callback = self._on_frame
            if callback is None:
                continue
            try:
                callback(selected)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1

        if ended_cleanly and not self._capture_stop.is_set():
            # The subprocess exited (EOF) without stop_capture() asking it to
            # — a real fault (device unplugged, subprocess crashed), not a
            # teardown. Recorded and named — see status()'s degradation_in.
            self._degradation_in = EndpointDegradation(
                DEGRADED_CAPTURE_ENDED, f"capture subprocess ended: {_describe_process_exit(proc)}"
            )

    # -- playback --------------------------------------------------------

    def _playback_buffer_limit_samples(self) -> int:
        return int(_PLAYBACK_BUFFER_SECONDS * PLAYBACK_RATE_HZ)

    def play(self, frames: bytes) -> None:
        if not isinstance(frames, (bytes, bytearray)):
            return
        if self._degradation is not None or self._closed:
            return

        if self._playback_proc is None:
            if (
                self._degradation_out is not None
                and time.monotonic() < self._out_open_cooldown_until
            ):
                # Round 3 finding 1, recurring at a process spawn: a dead
                # player does NOT get retried on every play() call.
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return

            was_degraded = self._degradation_out is not None
            playback_target = (
                self._pw_sink_node_name if self._backend == "pipewire" else self._device
            )
            argv = _build_playback_argv(
                self._backend, playback_target, PLAYBACK_RATE_HZ, PLAYBACK_CHANNELS
            )
            try:
                proc = self._popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
            except OSError as exc:
                self._enter_output_degradation(
                    DEGRADED_OPEN, exc, "playback subprocess failed to start"
                )
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return
            if was_degraded:
                self._degradation_out = None
                self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S
                self._record_event({"type": "recovered", "direction": "out"})
            self._playback_proc = proc
            self._start_writer()
            self._attached = True

            # Round 7 (BLOCKER): verify, don't trust. An unresolvable
            # `--target` silently falls back to pipewire's default sink —
            # exactly how a reply ended up on the HDMI monitor instead of
            # the reSpeaker. Confirm the stream actually linked to the
            # resolved node; if not, kill it AT ONCE (never let audio keep
            # flowing to the wrong device) rather than merely flag it.
            verified = self._verify_pipewire_link(proc, playback=True)
            if verified is not None:
                self._playback_target_verified = verified
                if not verified:
                    self._handle_playback_mismatch()
                    return

        chunk_bytes = bytes(frames)
        if not chunk_bytes:
            return

        limit_samples = self._playback_buffer_limit_samples()
        with self._counter_lock:
            if self._playback_proc is None:
                # A concurrent stop_playback()/write failure tore down the
                # process while this call was above this lock — never
                # silently queue a chunk for a player that no longer exists
                # (round 3's play()/stop_playback() interleaving fix,
                # recurring at a process reference instead of a stream one).
                self._playback_dropped_no_device += 1
                return
            in_flight = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            if in_flight + len(chunk_bytes) // SAMPLE_WIDTH_BYTES > limit_samples:
                if not self._playback_overflow_episode_active:
                    self._playback_overflow_episode_active = True
                    self._record_event(
                        {"type": "degraded", "code": DEGRADED_PLAYBACK_OVERFLOW, "direction": "out"}
                    )
                self._playback_overflow_count += 1
                return
            self._playback_overflow_episode_active = False
            self._playback_chunks.append(chunk_bytes)
            self._playback_queued_bytes += len(chunk_bytes)
            self._playback_total_pushed_samples += len(chunk_bytes) // SAMPLE_WIDTH_BYTES
            self._playing = True

    def stop_playback(self) -> int:
        """Barge-in: close stdin, SIGKILL AT ONCE (round 5 — no SIGTERM grace period).

        No drain grace period, no polite terminate-then-wait — that is the
        whole point of a barge-in: stop the sound NOW. A player process is
        disposable; the next :meth:`play` simply respawns one. Measured
        target: returns in under 200 ms with :attr:`playing` already
        ``False``. The writer's pacing (:data:`_PACE_LEAD_S`) means only
        about 100 ms of already-written-but-not-yet-sounding audio can
        possibly still be heard after this returns — a documented,
        unmeasured overshoot, not an unbounded one.

        :meth:`close`, by contrast, gives the player a brief chance to exit
        naturally on EOF first (see :meth:`_stop_playback_internal`'s
        ``drain_timeout``), since a normal end of session is not an
        interruption.
        """
        discarded, _close_failures = self._stop_playback_internal(drain_timeout=0.0)
        return discarded

    def _stop_playback_internal(self, *, drain_timeout: float = 0.0) -> tuple[int, int]:
        self._reap_unreaped()
        with self._counter_lock:
            discarded = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._writer_pending = b""
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
            proc = self._playback_proc
            self._playback_proc = None

        close_failures = 0
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
            if drain_timeout > 0.0:
                try:
                    proc.wait(timeout=drain_timeout)
                except subprocess.TimeoutExpired:
                    close_failures = self._terminate_process(proc)
            else:
                # Barge-in: SIGKILL at once, no SIGTERM grace period (round 5).
                close_failures = self._kill_process_fast(proc)
        return discarded, close_failures

    @property
    def playing(self) -> bool:
        return self._playing

    def _handle_write_failure(self, exc: BaseException) -> None:
        """Round 3 finding 2, recurring at a pipe: never silent, never hammered again."""
        with self._counter_lock:
            proc = self._playback_proc
            self._playback_proc = None
            discarded = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._writer_pending = b""
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
        if proc is not None:
            self._terminate_process(proc)
        self._enter_output_degradation(DEGRADED_WRITE_FAILED, exc, "playback write failed")

    def _handle_playback_mismatch(self) -> None:
        """Round 7 (BLOCKER): the just-started playback stream linked to the
        WRONG pipewire node. Kill it immediately — SIGKILL, same as a
        barge-in, never a graceful drain — so no more audio flows to the
        wrong device, discard whatever was already queued for it, and enter
        the same cooldown-gated degradation as any other output fault.
        """
        with self._counter_lock:
            proc = self._playback_proc
            self._playback_proc = None
            discarded = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._writer_pending = b""
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
            self._playback_target_mismatch_count += 1
        if proc is not None:
            self._kill_process_fast(proc)
        self._enter_output_degradation_reason(
            DEGRADED_DEVICE_MISMATCH, "playback stream linked to an unexpected device"
        )

    def _run_pw_dump(self, timeout: float = _PW_DUMP_TIMEOUT_S) -> "list[object] | None":
        """One ``pw-dump`` invocation, parsed as JSON. ``None`` on ANY failure.

        Missing binary, a non-zero exit, a timeout, or output that is not a
        JSON array all degrade to ``None`` uniformly — every caller treats
        "cannot verify" the same way regardless of which of those it was.

        Round 9 finding 3: *timeout* defaults to the full
        :data:`_PW_DUMP_TIMEOUT_S` (5 s) for a standalone caller (e.g.
        target resolution, which has no smaller budget of its own), but
        :meth:`_verify_pipewire_link` passes the REMAINING verify budget so a
        wedged ``pw-dump`` cannot stall an attach for up to ~10 s inside a
        300 ms verification window — a clock sized against the wrong
        quantity was exactly the failure mode a wedged read timeout produced
        elsewhere in this project's own history.
        """
        try:
            proc = self._popen(
                ["pw-dump"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            return None
        try:
            out, _err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=timeout)
            except Exception:
                # Cleanup-after-kill: SIGKILL cannot be ignored, so this is a
                # wait for the OS to reap an already-doomed process, not a
                # second fault. Still recorded (C3), not a bare `pass`.
                with self._counter_lock:
                    self._callback_errors += 1
            return None
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return None
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return None
        return data if isinstance(data, list) else None

    def _resolve_pipewire_targets(self) -> None:
        """Resolve the Sink/Source node NAMES for the pipewire backend (round 7 BLOCKER).

        Never trusts an ALSA card index as a pipewire ``--target``:
        ``pw-play``/``pw-record`` silently fall back to the system DEFAULT
        sink/source when ``--target`` does not resolve to a real node — this
        is exactly how a reply ended up on the HDMI monitor instead of the
        reSpeaker (a card index means nothing to pipewire). If ``pw-dump``
        itself is unavailable or unparsable, this degrades to the ``alsa``
        backend (``plughw:`` is unambiguous) when alsa tools exist, or a
        construction-time fault when they do not — never a guess at a
        pipewire target this module could not verify.
        """
        dump = self._run_pw_dump()
        if dump is None:
            if self._which("arecord") and self._which("aplay"):
                self._backend = "alsa"
                self._record_event(
                    {"type": "degraded", "code": DEGRADED_PIPEWIRE_UNAVAILABLE, "direction": "both"}
                )
            else:
                self._degradation = EndpointDegradation(
                    DEGRADED_PIPEWIRE_UNAVAILABLE, "pw-dump unavailable and no alsa fallback"
                )
            return

        # An auto-detected ALSA card number is never trusted as a pipewire
        # name/serial needle (see `self._device_explicit`'s docstring at its
        # assignment) — only an EXPLICIT override is; auto-detect always
        # matches the same "XVF3800" default /proc/asound/cards itself used.
        needle = str(self._device) if self._device_explicit else "XVF3800"
        sink = _pw_find_device_node(dump, needle, _PW_SINK_CLASS)
        source = _pw_find_device_node(dump, needle, _PW_SOURCE_CLASS)
        if sink is None or source is None:
            missing = [name for name, node in (("sink", sink), ("source", source)) if node is None]
            self._degradation = EndpointDegradation(
                DEGRADED_DEVICE_UNRESOLVED,
                f"pipewire {'/'.join(missing)} match failed for the configured device",
            )
            return
        if (
            sink.get("device_id") is not None
            and source.get("device_id") is not None
            and sink.get("device_id") != source.get("device_id")
        ):
            self._degradation = EndpointDegradation(
                DEGRADED_DEVICE_UNRESOLVED, "pipewire sink and source resolved to different devices"
            )
            return

        self._pw_sink_node_id = sink.get("id")
        self._pw_sink_node_name = sink.get("name")
        self._pw_source_node_id = source.get("id")
        self._pw_source_node_name = source.get("name")

        # Round 8: the sink can be correctly resolved AND too quiet to hear —
        # a routing fix alone does not prove the reply was audible. Read-only,
        # once per resolution (today the only "attach" this module has).
        self._check_playback_volume()

    def _run_wpctl_get_volume(self, sink_id: object) -> "tuple[float | None, bool | None]":
        """``wpctl get-volume <sink_id>``, parsed. ``(None, None)`` on ANY failure.

        Round 8. Cited, not imported: ``shabbos-goy``'s ``audio/pipewire.py``
        already reads a sink's volume through ``wpctl`` against this same
        hardware. This module never writes it — see the module docstring's
        round 8 section. Missing binary, a timeout, a non-zero exit, or output
        that does not match ``"Volume: <float>"`` all degrade to
        ``(None, None)`` uniformly and are counted
        (``self._playback_volume_unparsable_count``), never raised.
        """
        try:
            proc = self._popen(
                ["wpctl", "get-volume", str(sink_id)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        try:
            out, _err = proc.communicate(timeout=_WPCTL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=_WPCTL_TIMEOUT_S)
            except Exception:
                # Cleanup-after-kill: SIGKILL cannot be ignored, so this is a
                # wait for the OS to reap an already-doomed process, not a
                # second fault of ITS own — round 9 finding 2: it shares the
                # generic callback_errors signal (same precedent as
                # `_run_pw_dump`'s own cleanup-after-kill branch) so the ONE
                # fault here — the wpctl timeout — increments
                # playback_volume_unparsable_count exactly once, below,
                # regardless of whether this cleanup also raised.
                with self._counter_lock:
                    self._callback_errors += 1
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        except Exception:
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        if proc.returncode != 0:
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        text = out.decode("utf-8", "replace")
        match = _WPCTL_VOLUME_RE.search(text)
        if match is None:
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        try:
            volume = float(match.group(1))
        except ValueError:
            with self._counter_lock:
                self._playback_volume_unparsable_count += 1
            return None, None
        muted = "[MUTED]" in text
        return volume, muted

    def _check_playback_volume(self) -> None:
        """Read the resolved sink's volume and record if it is too quiet (round 8).

        Read-only: never changes the system volume, that stays the
        operator's mixer. Recorded once per resolution — see the module
        docstring's round 8 section for why that currently means "once per
        attach."
        """
        if self._pw_sink_node_id is None:
            return
        volume, muted = self._run_wpctl_get_volume(self._pw_sink_node_id)
        self._playback_volume = volume
        self._playback_muted_by_system = muted
        quiet = (volume is not None and volume < PLAYBACK_VOLUME_FLOOR) or bool(muted)
        if quiet:
            with self._counter_lock:
                self._playback_quiet_count += 1
            self._record_event(
                {"type": "degraded", "code": DEGRADED_PLAYBACK_QUIET, "direction": "out"}
            )

    def _verify_pipewire_link(
        self, proc: "subprocess.Popen[bytes]", *, playback: bool
    ) -> "bool | None":
        """Confirm *proc*'s pipewire stream is linked to the resolved target (round 7).

        Returns ``True``/``False`` once checked; ``None`` when verification
        does not apply (not the pipewire backend, or target resolution never
        completed — e.g. the alsa fallback). Polls up to
        :data:`_PW_VERIFY_TOTAL_S` since pipewire's own routing takes a
        moment after a stream is created. Never raises.

        Round 9 finding 1: when :func:`_pw_find_stream_node` reports
        ``ambiguous=True`` (more than one un-pid-matched pw-play/pw-record
        candidate — see its own docstring), that is counted
        (``playback_target_ambiguous_count``/``capture_target_ambiguous_count``,
        direction-tagged) and treated exactly like "not yet found": it can
        expire into an honest ``False`` at the deadline, never a ``True``.

        Round 9 finding 3: each ``pw-dump`` call is bounded by whatever is
        LEFT of the verify budget, not the full :data:`_PW_DUMP_TIMEOUT_S`
        (5 s) — a wedged ``pw-dump`` must not stall this well past the
        300 ms budget it is meant to police. The deadline is also checked
        BEFORE spending time on a dump, not only after one returns.
        """
        if self._backend != "pipewire":
            return None
        expected_id = self._pw_sink_node_id if playback else self._pw_source_node_id
        if expected_id is None:
            return None
        media_class = _PW_STREAM_OUTPUT_CLASS if playback else _PW_STREAM_INPUT_CLASS
        deadline = time.monotonic() + _PW_VERIFY_TOTAL_S
        was_ambiguous = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            dump = self._run_pw_dump(timeout=min(remaining, _PW_DUMP_TIMEOUT_S))
            if dump is not None:
                stream, ambiguous = _pw_find_stream_node(dump, media_class, proc.pid)
                was_ambiguous = was_ambiguous or ambiguous
                if stream is not None:
                    linked_id = _pw_link_target_id(dump, stream["id"], as_output=playback)
                    if linked_id is not None:
                        return linked_id == expected_id
            if time.monotonic() >= deadline:
                break
            time.sleep(_PW_VERIFY_POLL_S)
        if was_ambiguous:
            with self._counter_lock:
                if playback:
                    self._playback_target_ambiguous_count += 1
                else:
                    self._capture_target_ambiguous_count += 1
        return False

    def _start_writer(self) -> None:
        if self._writer_thread is not None:
            return
        self._writer_stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="embodiment-audio-host-writer", daemon=True
        )
        self._writer_thread.start()

    def _stop_writer(self, timeout: float = 2.0) -> bool:
        self._writer_stop.set()
        thread = self._writer_thread
        self._writer_thread = None
        stopped = True
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
            stopped = not thread.is_alive()
        return stopped

    def _writer_loop(self) -> None:
        """Write PACED 20 ms slices, staying at most :data:`_PACE_LEAD_S` ahead
        of real playback time (round 5) — never a whole `play()` chunk dumped
        into the pipe at once. This is what makes ``written``/``playing``
        describe what is actually sounding, and what leaves
        :meth:`stop_playback` something real to discard from the FIFO
        instead of a player already holding seconds of unplayed audio.

        Slicing happens ACROSS chunk boundaries (chunks queued by separate
        `play()` calls are concatenated into :attr:`_writer_pending`, a flat
        byte buffer), because a caller's own chunking has nothing to do with
        a 20 ms pacing granularity.
        """
        slice_bytes = int(PLAYBACK_RATE_HZ * _WRITE_SLICE_MS / 1000) * SAMPLE_WIDTH_BYTES
        clock_start = 0.0
        samples_written_for_clock = 0
        active_proc: "subprocess.Popen[bytes] | None" = None

        while not self._writer_stop.is_set():
            with self._counter_lock:
                proc = self._playback_proc
                if proc is not active_proc:
                    # A fresh process (first play(), or a respawn after a
                    # barge-in/write-failure): pacing restarts from now,
                    # never carries a stale clock across processes.
                    active_proc = proc
                    clock_start = time.monotonic()
                    samples_written_for_clock = 0

                slice_: bytes | None = None
                if proc is not None and proc.stdin is not None:
                    while len(self._writer_pending) < slice_bytes:
                        try:
                            extra = self._playback_chunks.popleft()
                        except IndexError:
                            break
                        self._playback_queued_bytes -= len(extra)
                        self._writer_pending += extra
                    if self._writer_pending:
                        slice_ = self._writer_pending[:slice_bytes]

            if slice_ is None:
                time.sleep(_POLL_INTERVAL_S)
                continue

            target_time = clock_start + samples_written_for_clock / PLAYBACK_RATE_HZ
            lead = target_time - time.monotonic()
            if lead > _PACE_LEAD_S:
                time.sleep(min(_POLL_INTERVAL_S, lead - _PACE_LEAD_S))
                continue

            try:
                proc.stdin.write(slice_)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except Exception as exc:
                self._handle_write_failure(exc)
                active_proc = None  # force a fresh clock for whatever comes next
                continue

            with self._counter_lock:
                # Only advance state if this write's bytes are still the
                # front of `_writer_pending` — a concurrent stop_playback()/
                # write-failure may have cleared it while this thread was
                # blocked inside write() above.
                if self._writer_pending[: len(slice_)] == slice_:
                    self._writer_pending = self._writer_pending[len(slice_) :]
                self._playback_written_samples += len(slice_) // SAMPLE_WIDTH_BYTES
                self._playing = bool(self._playback_chunks) or bool(self._writer_pending)
            samples_written_for_clock += len(slice_) // SAMPLE_WIDTH_BYTES

    # -- mute --------------------------------------------------------------

    def mute(self, muted: bool) -> None:
        """Round 7b finding 1: the check, the set, and the event are ONE
        critical section under ``_counter_lock`` (an ``RLock``, so nesting
        into ``_record_event`` below is safe) — otherwise two concurrent
        callers reading the same ``self._muted`` before either writes it
        could both decide "this is a change" and each record an event for
        what is, logically, ONE transition. Not reproduced under load (3000
        calls through a barrier, 0 duplicates — the window is a few
        bytecodes under the GIL) but the guarantee should not rest on that.
        """
        muted = bool(muted)
        with self._counter_lock:
            if muted == self._muted:
                return
            self._muted = muted
            self._record_event({"type": "mute", "muted": muted})

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        """The retained (bounded) event log, in order. Never carries audio content."""
        with self._counter_lock:
            return tuple(self._events)

    @property
    def sample_rate(self) -> int:
        """The rate (Hz) of frames delivered to ``on_frame`` — :data:`CAPTURE_RATE_HZ`.

        Decision 15 (operator, issue #85): the ears send the device's
        NATIVE 16 kHz; a caller (the daemon) reads this and passes it to the
        realtime session as ``input_sample_rate`` instead of assuming
        :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` (24 kHz). See the
        module docstring.
        """
        return CAPTURE_RATE_HZ

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._counter_lock:
            counters = {
                "capture_muted_dropped": self._capture_muted_dropped,
                "callback_errors": self._callback_errors,
                "mute_event_count": self._mute_event_count,
                "events_retained": len(self._events),
                "playback_overflow_count": self._playback_overflow_count,
                "playback_written_samples": self._playback_written_samples,
                "playback_total_pushed_samples": self._playback_total_pushed_samples,
                "playback_stop_discarded_total": self._playback_stop_discarded_total,
                "playback_queued_bytes": self._playback_queued_bytes,
                "playback_dropped_no_device": self._playback_dropped_no_device,
                "output_degrade_attempts": self._output_degrade_attempts,
                "playback_target_mismatch_count": self._playback_target_mismatch_count,
                "capture_target_mismatch_count": self._capture_target_mismatch_count,
                "playback_target_ambiguous_count": self._playback_target_ambiguous_count,
                "capture_target_ambiguous_count": self._capture_target_ambiguous_count,
                "playback_volume_unparsable_count": self._playback_volume_unparsable_count,
                "playback_quiet_count": self._playback_quiet_count,
            }
        return {
            "attached": self._attached,
            "capturing": self._capturing,
            "playing": self._playing,
            "muted": self._muted,
            "closed": self._closed,
            "backend": self._backend,
            "device": self._device,
            "degradation": self._degradation.to_dict() if self._degradation else None,
            "degradation_in": self._degradation_in.to_dict() if self._degradation_in else None,
            "degradation_out": self._degradation_out.to_dict() if self._degradation_out else None,
            "playback_target_verified": self._playback_target_verified,
            "capture_target_verified": self._capture_target_verified,
            "playback_volume": self._playback_volume,
            "playback_muted_by_system": self._playback_muted_by_system,
            "playback_latency_ms": PLAYER_LATENCY_MS,
            "close_report": self._last_close_report.to_dict() if self._last_close_report else None,
            **counters,
        }
