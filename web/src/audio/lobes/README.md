# audio/lobes

Browser mic capture and playback, cited verbatim from the sibling `lobes-cli`
checkout's own realtime-voice front-end — the cite-don't-import pattern this
repo already uses for `web/src/culture-design/` (see that directory's
README), applied here to code instead of CSS: embodiment never imports
`lobes-cli`'s package (`CLAUDE.md`: "`lobes-cli` is reached over the network
only — never imported"), so the browser code that speaks its `/v1/realtime`
wire is copied in, once, at a pinned commit, and owned by this repo from
that point on.

## Pinned commit

```text
lobes-cli repo: /home/spark/git/lobes-cli (agentculture/lobes-cli)
pin:            d2690a55ea7398f048d14d159970cb7c75c8ba59
source path:    site/src/scripts/
```

Obtained with:

```bash
git -C /home/spark/git/lobes-cli rev-parse HEAD
```

at the time task t18 was executed (2026-09-22). lobes-cli's own HEAD is free
to move on without this pin changing — re-pinning is a deliberate, manual act
(see below), never automatic.

## Contents

Copied **verbatim**, byte-identical to the pinned source — do not hand-edit
any file below; re-run the extraction against a new pin instead:

- `mic-capture.ts` / `mic-capture.test.ts` — the task brief's own citation:
  getUserMedia + AudioWorklet capture, resample to the wire rate, batch into
  `input_audio_buffer.append` events.
- `pcm-wire.ts` / `pcm-wire.test.ts` — the task brief's own citation: the
  PCM16 <-> float <-> base64 codec, the linear resampler and the frame
  accumulator. `pcm-wire.test.ts`'s own cases ("encodes a known float block
  to the exact base64 the server decodes", "decodes a delta to the exact
  PCM16 sample values that were sent") already prove each half of the codec
  in isolation. This task's acceptance criterion 2 ("pcm16 encode/decode
  round-trips a known buffer exactly") is proved as its own, explicitly
  named test in `web/src/audio/pcm16-roundtrip.test.ts` — a small
  embodiment-owned test that chains `encodeAudioPayload` into
  `decodeAudioDelta` on one fixed buffer and asserts the values survive
  exactly at 16-bit precision, rather than relying on the citation's own
  tests (which prove the halves, not stated as one round trip) to stand in
  for this task's own acceptance criterion.
- `audio-playback.ts` / `audio-playback.test.ts` — the task brief's own
  citation: `response.audio.delta` decode + gapless scheduled playback on an
  `AudioContext`, with an unconditional, immediate `stop()` for barge-in.
- `audio-test-doubles.ts` — the task brief's own citation ("for the test
  pattern"): `FakeAudioContext` and friends, the fakes `mic-capture.test.ts`
  / `audio-playback.test.ts` use to exercise the Web Audio graph under
  jsdom, which has none.
- `audio-graph.ts` — **not named in the task brief's citation list**, copied
  anyway because it is a load-bearing, unnamed transitive dependency: both
  `mic-capture.ts` and `audio-playback.ts` import their `AudioContextLike` /
  `AudioNodeLike` / `BrowserAudioDeps` types and the real-browser
  `browserAudioDeps` implementation from it. Citing the three named files
  without it would not compile. Flagged here as a brief/reality gap per the
  task-agent preamble, not silently added.

## A public asset this code needs to actually run (also not in the brief's file list)

`mic-capture.ts`'s `DEFAULT_WORKLET_URL` (`/worklets/pcm-capture-processor.js`)
names a `/public` asset the browser's `audioWorklet.addModule()` fetches at
runtime — it is not bundled, and `mic-capture.ts` does not work without it
present at that URL. Cited verbatim (same pin) to
`web/public/worklets/pcm-capture-processor.js` — outside `web/src/`, which
the brief's "files you own" list did not anticipate for a `web/src/audio/
lobes/**`-scoped citation. Also flagged as a brief/reality gap: the brief
says "nothing outside web/", which this still satisfies (it is under `web/`,
just not under `web/src/`), but it is a file the brief's explicit ownership
list ("`web/src/**`... `web/src/audio/lobes/**`") did not name.

## Adaptation

Only a thin wrapper of embodiment's own sits on top of these files
(`web/src/audio/browserEar.ts`) — it composes `MicCapture` and `DeltaPlayer`
against a WebSocket dialed at t14's `RemoteEndpoint` (`embodiment/audio/
remote.py`) instead of lobes' own server, and sends the `{"type":"auth",
"secret":...}` first message `remote.py`'s docstring requires before any
`input_audio_buffer.append` frame. None of the cited files above were
changed to make that work — the wrapper is the adaptation point, per this
task's instruction ("adapt only in a thin wrapper of your own").

## Re-pin procedure

1. `git -C /home/spark/git/lobes-cli rev-parse HEAD` for the new commit.
2. Re-copy each file above from `site/src/scripts/<name>` (and the worklet
   from `site/public/worklets/pcm-capture-processor.js`), byte-identical.
3. Update the `pin:` line in this README.
4. Run `cd web && npx vitest run src/audio/lobes` to confirm the cited test
   files (also re-copied) still pass against embodiment's own vitest setup.

No automated drift checker (a `scripts/check-culture-design.py` analogue)
exists for this citation yet — `tokens.css` had one because it is a single
generated file a byte-diff can verify; this directory is eight files across
two repos' independent `tsconfig`/`vitest` setups, and building an
equivalent checker was judged out of scope for this task (a decision the
brief did not dictate; flagged in this task's final report).
