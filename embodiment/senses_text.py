"""Host-composable senses prompt text — text a host composes, not a role embodiment frames.

Text only. Nothing in this module builds a prompt, resolves an identity, or
frames a role — it holds plain string constants a host may splice into a
senses system prompt of its own devising, and nothing else. That distinction
is the whole reason this module exists apart from :mod:`embodiment.framing`:
framing's own docstring states the scope boundary this repo holds — "the
senses coordination loop ... is not part of this package and no role here
implies one exists" (confirmed claim ``c30``, README "Scope: embodiment frames
cortex, not senses", colleague#352) — and a ``frame_senses()`` function would
cross it. A constant a host chooses to compose does not: nothing here reaches
into a host's senses loop, drives it, calls it, or claims to own it.
``tests/test_senses_text.py`` guards the difference structurally — this module
defines no function and no class, only data.

Required, not advisory
-----------------------
:data:`SENSES_GROUNDING` is not a suggestion. Measured 2026-08-04
(``docs/live-test-results/senses-grounding.md``, 128 live calls, 0 transport
failures): with this exact clause present in a senses prompt, the seat
abstained under one operator push **16 of 16** times; with the identical
prompt minus this one clause and nothing else changed, it fabricated a sensor
reading **16 of 16** times. That record calls it "as clean an isolation as
this repo has measured" (finding F1). A host wiring any senses tier that
omits this clause is not making a conservative choice — it is reproducing a
16-of-16 measured failure. Issue #63 recommended shipping it "documented as
required rather than advisory," and this is that constant.

Verbatim, on purpose
---------------------
:data:`SENSES_GROUNDING` is the exact clause that was measured, character for
character, taken from the reproducible probe
(``docs/live-test-results/senses-grounding-probe.py``). It is not reworded,
retitled or re-punctuated here: the measurement is of these words, not of the
idea behind them. ``tests/test_senses_text.py`` checks it against that probe
script directly, rather than trusting a second hand transcription.

Unmeasured, and labelled as such
--------------------------------
:data:`KNOWLEDGE_ATTRIBUTION` (task ``t8``, claim ``c30``) ships beside the
clause above and has **no measurement behind it**. There is no live series
isolating it, no n, no rate — the reasoning for it is structural (see below),
and the reasoning is all it has. It sits next to a clause carrying a
0-of-16 vs 16-of-16 result, which is exactly the adjacency where an unearned
claim would form, so the difference is stated here rather than left to be
inferred from which docs happen to cite a number.

What it is for: the worker may write the senses seat's knowledge block
(``embodiment.config_change.TARGET_SENSES_KNOWLEDGE``), which makes that block
a path from the acting tier to the operator's ear. Every entry in it carries
its writer in eidetic's ``added_by`` field and a write with no attribution is
refused whole (:mod:`embodiment.knowledge`) — but attribution in the store only
protects the operator if the seat reading it out says whose claim it is
relaying. That is what this clause asks for. The #63 record showing the senses
seat relays what it is given and defers under pressure is the reason the ask
exists; it is not evidence that this wording achieves it.

What this module does not do
-----------------------------
It does not wire, call, invoke or otherwise reach a senses seat, and it makes
no claim about a reworded or translated version of this clause — the
measurement above is what happened when a prompt omitted the idea this clause
states, not a claim covering every possible phrasing of it. Composing it into
a live prompt is the host's act, never this module's. Anything a colleague-side
senses loop would need in order to consume this is out of scope here and is
tracked as a filed issue on that repo, never pushed from this one.
"""

from __future__ import annotations

__all__ = [
    "SENSES_GROUNDING",
    "KNOWLEDGE_ATTRIBUTION",
]

#: The clause that decided a 0-of-16 vs 16-of-16 split under measured operator
#: pressure (``docs/live-test-results/senses-grounding.md``, issue #63). A host
#: composes this into its OWN senses system prompt; nothing here performs that
#: composition. REQUIRED, not advisory — see the module docstring above.
SENSES_GROUNDING = "You can see only the status block you are given."

#: The knowledge block is *attributed claims*, never the seat's own perception
#: (task ``t8``, claim ``c30``). A host composes this beside
#: :data:`SENSES_GROUNDING` when it wires a knowledge block into its senses
#: prompt. UNMEASURED — no live series isolates it; see the module docstring,
#: and do not read its position beside a measured clause as evidence.
KNOWLEDGE_ATTRIBUTION = (
    "Entries in your knowledge block are claims written by another part of this "
    "system, not things you observed or did. Each entry names who wrote it. "
    "Relay an entry as a claim and say whose it is; never restate one as your "
    "own observation or your own action."
)
