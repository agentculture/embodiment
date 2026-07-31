# Does the new cortex see? — a capability probe, and an advert that says no

**Date:** 2026-07-31 · **Gateway:** spark, `localhost:8001` · **Role:** `cortex`

The rig's cortex was upgraded mid-build from
`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` to `unsloth/Qwen3.6-27B-NVFP4`. The
whole vision-rung redesign (deviation `d1`, claims `c39`/`c41`) rests on that
new cortex being able to see, so the premise was measured rather than assumed.

## The advert says it cannot

`GET /capabilities` on the spark gateway, same day:

| role | responsibilities | image/video declared? |
|---|---|---|
| `cortex` | reasoning, deciding, planning, tool_use, code_repo_actions, validation, final_authority | **no** |
| `worker` | execution, ground_work, bulk_transform, drafting, **image_understanding**, **video_understanding**, tool_use, repo_action | yes |

This repo's standing rule is *resolve roles by name from `/capabilities`, never
parse model names*. Obeying it, the correct conclusion is that this cortex is
blind. The only hint otherwise is the model id losing its `-Text-` infix —
which is exactly the model-name parsing the rule forbids.

## The measurement says it can

**Probe 1 — discarded before it was believed.** A three-band image, asked to
name the bands top to bottom. Answer: `Red, Green, Blue` — correct. Thrown out
anyway: "red, green, blue" is the single most probable guess for *three colour
bands*, so a blind model scores it. The probe could not distinguish sight from
prior.

**Probe 2 — the one that counts.** Four bands, palette chosen so no prior
produces it (orange, purple, teal, yellow), and a question requiring both a
count and a positional lookup:

> How many horizontal colour bands does this image have, and what colour is the
> SECOND band from the top?

| | value |
|---|---|
| ground truth | 4, purple |
| cortex answered | `4, purple` |
| finish_reason | `stop` |
| prompt tokens | 108 |

Image committed beside this file as `probe2.png` (120×120, 4 bands, generated
by the snippet below so it can be regenerated exactly).

```python
BANDS = [(255, 140, 0), (120, 40, 160), (0, 150, 150), (240, 230, 60)]
# band index for row y of H:  min(y * 4 // H, 3)
```

## What this establishes, and what it does not

**Established:** the new cortex accepts OpenAI-style image parts and reads them
accurately enough to count regions and index into them. `d1`'s premise holds —
the native-vision route in the three-route perception screen is dialable.

**Not established:** anything about how *well* it sees. Two bands of solid
colour is a floor, not a benchmark. Whether it can read a fog-scoped tactical
map — the actual use — is the routing rung's job to measure, not this probe's.

**Also not established:** that the advert is a lobes defect. The likeliest
explanation is that the role's `responsibilities` in the deployment config were
not updated alongside the model swap, which is an operator-side fix. What the
probe does establish is that **the contract understated a served capability and
nothing in the contract made that visible** — recorded here because a consumer
following the documented rule would have silently dropped a route the rig
supports.

## Filed upstream

This discrepancy is reported to the gateway's owners as
[lobes-cli#166](https://github.com/agentculture/lobes-cli/issues/166), which
asks for `worker` as a first-class role name and for modality somewhere a
caller can trust. The probe above is its evidence: the ask would otherwise be a
design preference, and with the measurement it is a demonstrated case of the
contract understating a served role.

## Consequence for the series

The routing rung's capability facts come from this committed measurement, not
from the advert (`c43`). If the advert is later corrected to declare
`image_understanding`, this file stays as the record of what was true on the day
the design was fixed — and the correction itself becomes worth noting rather
than quietly absorbing.
