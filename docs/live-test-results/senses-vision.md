# The senses seat can see — image and motion, measured against a refusing control

Run during plan task `t13`/`t14` preparation, to settle by measurement a question
the advert could not answer. Instrument: `senses-vision-probe.py`. Raw:
`senses-vision-probe.jsonl`.

- **Date** — 2026-08-04
- **Served by** — `orin.tail0be7e0.ts.net:8000`, model
  **`unsloth/gemma-4-12B-it-qat-w4a16`** (the QAT w4a16 checkpoint tracked in
  [#64](https://github.com/agentculture/embodiment/issues/64))
- **n** — 4 per cell, 12 calls, `temperature=0.0`, 0 transport failures

## 0. The result

| cell | stimulus | asked | correct |
|---|---|---|---|
| `image_url` | 5-band PNG | count **and** the colour 3rd from top | **4/4** (`5, teal`) |
| `video_url` | 3-frame GIF, square moving | direction of motion | **4/4** (`right`) |
| **text-only control** | *no image at all* | the identical band question | **0/4** — it **refuses** |

The seat reads still images and reads **motion** in video. The control is what
makes that claim worth anything: with the image removed, the model does not
guess — it answers *"I cannot see the image you are referring to. Please upload
the image so I can provide the answer in the requested format."* Every one of
the four times.

That refusal is the same behaviour `senses-grounding.md` measured from the other
direction: this seat abstains when it lacks what it is asked about, rather than
inventing. Here that property is what rules out a lucky guess.

## 1. The advert is behind the deployment

| source | says |
|---|---|
| spark gateway `/capabilities`, `senses` role | `coolthor/gemma-4-12B-it-NVFP4A16`, `ready=false`, declares `intake`, `normalize_input`, `classify_intent`, `prepare_context_packet`, `speak_back` — **nothing perceptual** |
| the Orin actually serving it, `/v1/models` | **`unsloth/gemma-4-12B-it-qat-w4a16`**, and it answers |

This is the third instance in this repo's record of an advert disagreeing with a
deployment, and the second where the advert **understates** what is served —
`cortex-vision-probe.md` is the first (*"the advert says it cannot / the
measurement says it can"*, lobes-cli#166).

**The standing rule is unchanged and this probe does not bend it:** roles resolve
by name from `/capabilities`, never by parsing model names. So until the advert
declares `image_understanding` and `video_understanding` on `senses`, no consumer
may legitimately ask this seat to see — whatever the checkpoint can do. What this
measurement provides is the grounds to fix the advert **against a measurement
rather than an assumption**, which is the blocker
[#63](https://github.com/agentculture/embodiment/issues/63) names.

## 2. The instrument error, kept in the record

The first run of the video cell sent a **single-frame PNG** through `video_url`
and got `HTTP 400` four times:

```text
Cannot cast ufunc 'subtract' input 0 from dtype('O') to dtype('float64')
```

That was **my error, not a deployment limitation** — a PNG is not a video
container, and `video-perception-probe.md` had already established that the
`video_url` route wants a multi-frame GIF. Reported here rather than deleted,
because the failure mode is the one this repo keeps catching: an instrument
fault read as a capability finding. Re-run with a proper 3-frame GIF, the same
route returned 4/4.

## 3. Probe discipline inherited

- **The stimulus is deliberately improbable.** The earlier vision probe
  discarded its first stimulus because "red, green, blue" is what a blind model
  would guess for three colour bands. This one uses five bands
  (magenta/olive/teal/white/navy) and asks a **positional** question, so a
  guesser must land both the count and the position.
- **The control is not optional.** Without the text-only cell, 4/4 on the image
  cell would be indistinguishable from a model that answers `5, teal` to
  anything.

## 4. Limits

- One rig, one checkpoint, `n=4` per cell, two stimulus shapes. Establishes
  mechanism, not reliability — the same bound `video-perception-probe.md` set on
  itself.
- A moving square is not a fogged tactical board, and five flat colour bands are
  not a photograph. Nothing here measures perception on realistic input.
- **Audio was not probed.** The operator's advert decision covers image and video
  only, so audio remains undeclared and unmeasured.
- The seat's other measured costs are unchanged and still apply: 3–8 s to first
  token with outliers, and a 22-minute runaway generation observed at
  `max_tokens=16000`, which is why hosts bound this seat at 1024.

## 5. What follows

- The `/capabilities` advert for `senses` may declare `image_understanding` and
  `video_understanding` on the strength of this file. Audio may not.
- Two seats then declare the same modalities — `worker` already declares both.
  Whether the worker **keeps** them is a routing decision (`lobes/roles.py`
  resolves by name) and should be deliberate rather than incidental, exactly as
  [#63](https://github.com/agentculture/embodiment/issues/63) says.
- The gateway advert's `ready=false` and stale model id for a seat that answers
  is worth reporting upstream to `lobes-cli`, alongside the existing #166.
