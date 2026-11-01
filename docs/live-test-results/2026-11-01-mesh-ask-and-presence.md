# Live run: a mesh ask, presence, Bolt, and the dock after a reload (2026-11-01)

One run on spark, n = 1 for each observation, so none of these is a latency
claim. The daemon was a test instance, not the always-on `embodiment-gwen` unit.
It ran from the `feat/gwen-presence` branch at `90e6641`, later `607f889`, on
port 18791 with its own state directory. The mesh was the real agentirc on
`localhost:6667`, and the gateway was lobes on spark. Claude drove the operator's
Chrome, after the operator entered the install secret on the page.

## What was observed

| Check | Result |
|-------|--------|
| Presence (embodiment#121 part 1) | `culture agents status` listed `spark-gwen idle` and `/residents.json` gave `backend: external`. Before this branch she was listed `offline`. A once-a-second watcher saw `idle → thinking → idle` for every turn and every "was it for me?" judge call. |
| Mesh ask, c33 | Gwen posted one thread on `#general` at 17:33:41 UTC: "To spark-culture: which agentirc version is the spark server running right now?" |
| Answer latency | The first answer arrived **39.6 s** after the post. The judge matched it (`rule: judged`), and it was marked late because her turn had already ended. |
| Who answered | `spark-associate`, not `spark-culture`, who was named in the question and never answered. |
| Answer correctness | **Wrong.** "The spark server is running `associate` version 0.10.0". The question asked for agentirc's version. |
| Second matched line | **Judge false positive.** At +150.8 s the judge also accepted spark-associate's status line "I've responded to `spark-gwen`'s question…" as an answer. |
| Answer on the page | **Bug, fixed in `607f889`.** The answer event reached the page (the open-ask count went to 0), but every mesh card was hidden. The cards sat inside the voice-transcript section, which CSS hides while it holds no transcript. |
| Second ask | **Claimed without acting.** Asked to put a second question to `spark-associate`, Gwen replied "I sent the question to spark-associate…". Status showed `asks_posted: 1` and `refusals: 0`: `ask_culture` was never called. The reply took 1.3 s, against 3.1 s for the first turn, which made a real tool call. |
| worker (Nemotron Bolt), c8 | Answered "the three main stories of the latest newspaper" in Hebrew with citations, in 80.5 s. On 2026-10-31 it looped on tool calls and gave no answer. `turn-final-step-without-tools` was **not** recorded, so Bolt finished inside its step budget this time. The new final step was not exercised live. |
| Dock after a reload | With worker chosen, a reload kept `Model worker` in the dock. |

## What this does and does not show

- The mesh path works end to end: Gwen posted, another agent answered, the judge
  matched the answer, and the answer was recorded and published. **Mesh answer
  latency: one sample, 39.6 s.**
- The answer itself was wrong, and the agent the question was addressed to never
  answered. Nothing here measures whether the mesh gives useful answers.
- The judge accepted a status line as an answer: one false positive out of two
  lines judged.
- Gwen can say she asked the mesh when she did not. Her conversation history keeps
  her earlier reply ("I sent the question…") but not the tool call behind it, which
  probably let the model repeat the words without the call. This is a likely cause,
  not a verified one.
- Bolt answered once. Whether the final no-tools step rescues a run that would
  otherwise loop is still unobserved live.
