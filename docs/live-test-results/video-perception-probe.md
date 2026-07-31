# Can either mind perceive motion? — yes, and the content type is the whole story

**Date:** 2026-07-31 · **Roles:** `cortex` (spark) and `worker` (Thor)

Filed against [#39](https://github.com/agentculture/embodiment/issues/39), which
proposed showing a mind a playthrough video and named a cheapest-first step:
probe whether league's already-rendered animated GIFs carry motion.

**This document contains a wrong conclusion that was corrected the same hour.**
The error is kept in place, because the way it was caught is the useful part.

## The stimulus

A 160×80, 6-frame animated GIF: a red square starting at x=10 and stepping
+22px per frame, travelling unambiguously **left to right**. Built with league's
*own* encoder (`league.replay.video._encode_gif`), so the artifact is
byte-representative of a real playthrough rather than a hand-rolled
approximation.

Every question offered an explicit escape hatch, so a single-frame reading would
be **reported rather than guessed**:

> If you can only perceive a single still frame, say exactly: ONLY ONE FRAME.

## Round 1 — `image_url`: both minds see one frame

| role | model | answer | prompt tokens |
|---|---|---|---|
| cortex | `unsloth/Qwen3.6-27B-NVFP4` | `ONLY ONE FRAME` | 132 |
| worker | `unsloth/Qwen3.6-35B-A3B-NVFP4` | `ONLY ONE FRAME` | 132 |

Both flattened it — identical token counts across two models on two boxes.

### The wrong conclusion, recorded as it was written

> *"So the cheap path #39 hoped for **does not exist**: league's GIFs cannot
> carry motion to either mind as-is."*

That was written after testing exactly one content type — `image_url` — and
generalising from it to "video is not reachable cheaply". A frame-sequence
workaround was then measured (three PNGs as three ordered `image_url` parts,
which does work: `left-to-right`, 289 prompt tokens) and briefly became the
plan.

### How it was caught

The operator pushed back on the reasoning rather than the result: *"Cortex has
the same setup as worker — it should accept video understanding the same way,"*
and *"they are now both by the same unsloth quant."* That is an argument about
**configuration symmetry**, and the round-1 data was consistent with it — both
models behaved identically. What the probe had actually measured was the
behaviour of the `image_url` **transport**, not either model's capability. The
escape hatch worked exactly as designed; the inference drawn beside it did not.

## Round 2 — `video_url`: the same bytes carry motion

The **identical GIF bytes**, re-sent as a `video_url` content part:

| role | answer | truth | prompt tokens |
|---|---|---|---|
| cortex | **`left-to-right`** | left-to-right | **86** |
| worker | **"The square moves to the right."** | left-to-right | 86 |

Both models' reasoning traces walk the frames explicitly — *"Frame 1 (00:00):
there is a red square positioned on the left… Frame 2: the red square is now…"*
— so the sequence is genuinely decoded, not inferred from a single still.

Two details worth keeping:

- **The declared MIME was a lie and it did not matter.** The part was sent as
  `data:video/mp4;base64,…` while the bytes were a GIF. The server sniffs the
  content rather than trusting the declared type.
- **Video is the *cheapest* route, not merely a viable one.** 86 prompt tokens
  against 132 for a single image and 289 for a three-frame sequence — the video
  path costs less than one still image, presumably via internal frame sampling
  and downscaling.

## Corrected conclusions

| path | motion? | cost | verdict |
|---|---|---|---|
| GIF as `image_url` | no — one frame | 132 | dead end |
| ordered PNGs as N × `image_url` | yes | 289 for 3 frames | works; scales linearly; unnecessary |
| **GIF as `video_url`** | **yes** | **86** | **the path** |

So #39's hoped-for cheap path **does exist**: league's existing GIFs, unchanged,
delivered as a video content part. What it needs from us is a `video_url`
builder in `media.py` — a genuine but modest addition — and *not* the frame
sampler, budget policy, or ordering machinery the frame-sequence workaround
would have required.

## Honest limits

- **n=1 per condition**; five calls total. This establishes the mechanism, not
  its reliability.
- **A moving square is not a match.** Reading a 20px block cross a blank field
  says nothing about a fogged tactical board with units, control points and
  resource counts. That remains the real question and is untested.
- **Frame sampling is the server's, and is unmeasured.** 86 tokens for a
  6-frame clip implies aggressive internal downsampling. How many frames of a
  40-turn match actually survive to the model is unknown, and it bounds what any
  playthrough experiment can claim.
- **`/capabilities` predicted none of this.** The worker declares
  `video_understanding` and the cortex declares no vision at all
  ([cortex-vision-probe.md](cortex-vision-probe.md)); both read video fine. The
  advert is now 0 for 2 as a guide to what a route can do on this rig.

## Filed upstream

Reported to league as
[league-of-agents#43](https://github.com/agentculture/league-of-agents/issues/43),
because this is a property of *their* artifact: `render_gif` output is directly
consumable as model-readable video, with no re-encode and no frame extraction.
They had no particular reason to have tested that. The round-1 error is
included there too, so the dead end is not re-walked by whoever picks it up.

## The transferable lesson

The round-1 error was not a bad measurement — the numbers were right and the
escape hatch behaved. It was **testing one transport and concluding about a
capability.** The same shape appears in this repo's
[corrections](corrections.md) record more than once: *the mechanism was right
and the verification was the defect.* A capability probe must vary the delivery
path before it reports a capability absent.
