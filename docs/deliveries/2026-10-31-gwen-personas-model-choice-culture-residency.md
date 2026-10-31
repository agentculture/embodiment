# Delivery Summary — Gwen personas, model choice, culture residency

plan: `gwen-personas-model-choice-culture-residency` · run: `partial` · date: `2026-10-31`
baseline: `devague summary skeleton`

## Intent

> Gwen becomes a configurable companion: the operator designs personas (style, length, language, prompt) and talks with one, picks the reply model per conversation from what the lobes gateway currently serves, and Gwen holds a residency on Culture chat where she can choose to ask the mesh a question and bring the answer back — with less brevity enforced by fixed prompt text

After: The operator opens a persona editor, writes a persona (name, language, length and style, free prompt, default model, optional Gemma translator), starts a new conversation with it and gets answers in that voice; switches the model from a list of what lobes serves right now; and when Gwen judges a question is better asked elsewhere she posts it to the mesh as spark-gwen and the answer appears in the conversation when it arrives

The run executed the plan's 19 tasks in waves, merged as one PR,
[#117](https://github.com/agentculture/embodiment/pull/117) (0.24.0, squash-merged to
`main` as `86aeabd`). The run is **partial**: everything in t1–t18 merged, but t19
(the live acceptance, "published as measured") is not finished. No live mesh ask has
been answered by another agent on record, and no results document is published.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Persona model and private store (embodiment/personas.py)
- `t2` — Capabilities poller: the chat roles lobes serves (embodiment/capabilities.py)
- `t3` — Per-role voice latency store and probe (embodiment/`voice_latency.py`)
- `t4` — Gwen's own mesh client, stdlib IRC to agentirc (embodiment/`mesh_client.py`)
- `t5` — Open asks and mesh bounds (embodiment/`mesh_asks.py`)
- `t6` — Translation hop around a chosen model (embodiment/translate.py)
- `t7` — CLI: spark-gwen identity in doctor and a mesh verb that prints, never runs (cli/`_commands`/mesh.py)
- `t8` — Culture-side ask: scratch probe, then draft the external-resident issue (no post)
- `t9` — Persona-aware prompt parts: voice/typed split and tool-prompt language (embodiment/`persona_prompt.py`)
- `t10` — The `ask_culture` tool and its prompt line (embodiment/`mesh_tools.py`)
- `t11` — Daemon wiring A: personas in the prompt, per-conversation persona, restore
- `t12` — Daemon wiring B: per-conversation model, distinct unavailable error, trace, voice gate
- `t13` — Daemon wiring C: translator and the operator's words as the record
- `t14` — HTTP routes: personas, models, conversation settings, status fields (http/server.py)
- `t15` — Daemon wiring D: mesh resident, inbound agent turns, asks, events, counters
- `t16` — Web UI part 1: the settings row under the dock and the Personas tab (mockup v2)
- `t17` — Web UI part 2: mesh cards, agent conversations, open-asks count
- `t18` — Docs and pinned tests: README, explain root, CLAUDE.md
- `t19` — Live acceptance on the rig, published as measured

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `embodiment/personas.py`: 0600 store, built-in Gwen the default, language he/en/none, an unreadable file set aside (`personas.json.unreadable`) before a save |
| `t2` | delivered | `embodiment/capabilities.py`: 60 s poll of `/capabilities`, ready chat roles only, `/api/models` |
| `t3` | delivered | `embodiment/voice_latency.py`: background probe, the voice gate (`senses` exempt), live spoken samples added (a cut-off kept as a censored sample) |
| `t4` | delivered | `embodiment/mesh_client.py`: stdlib agentirc client as `spark-gwen`, threads, `THREAD CREATE` sized to the 8192-byte line limit |
| `t5` | delivered, amended (`d2`, `d7`) | `embodiment/mesh_asks.py` (bounds, 0600 asks, late and after-expiry answers, continuation lines) plus `embodiment/mesh_judge.py` ("was it for me?") |
| `t6` | delivered | `embodiment/translate.py`: fenced data translated as data, the header guard, a per-conversation translation cache |
| `t7` | delivered | `doctor` reports `spark-gwen` as distinct from the repo's own nick; `embodiment mesh` prints the setup via `MeshConfig.from_env` and runs nothing |
| `t8` | delivered beyond plan | the external-resident ask exists as [agentculture/culture#506](https://github.com/agentculture/culture/issues/506) (open); the task said "draft (no post)" |
| `t9` | delivered, amended (`d1`) | `embodiment/persona_prompt.py`: the spoken prompt byte-identical, the typed prompt minus the two speech-only lines, tool prompts in the persona's language |
| `t10` | delivered | `embodiment/mesh_tools.py`: `ask_culture`, mesh answers fenced as untrusted, the write guard |
| `t11` | delivered | per-conversation persona in the prompt, kept across `Session.restore` |
| `t12` | partial | per-conversation model, `app-text-model-unavailable` with no fallback, `chat_role`/`chat_model`/`chat_host` traces, the voice gate all landed and pass their tests; **live, choosing `worker` (Nemotron Bolt) gives no answer** (it loops on tool calls, `e26`), so model choice does not yet work for every offered role |
| `t13` | delivered, amended (`d6`) | the translator wired into typed and spoken turns; searches get the operator's words; `remember` keeps them beside the model's fact |
| `t14` | delivered | `/api/personas`, `/api/models`, conversation settings, status fields; plus `GET /api/chat/{request_id}` (`d4`) |
| `t15` | delivered, amended (`d3`) | the daemon holds `spark-gwen`; an agent turn starts only when the message is addressed to her; mesh events, counters, spoken notices |
| `t16` | delivered | the dock settings row (Persona / Language / Model) and the Personas tab, built to mockup v2 |
| `t17` | delivered | mesh cards, agent conversations, the open-asks count |
| `t18` | delivered | README, `explain` root and CLAUDE.md updated; `tests/test_release_docs.py` pins them |
| `t19` | partial | live on the rig through the browser: personas saved, chat with `cortex` (72.5 s, streamed reasoning, recovered after leaving the page). It also produced `d3`, `d4`, `d5` and five fixes. **Not done:** a live mesh ask answered by another agent (c33); the results document under `docs/live-test-results/`; latency published as measured |

All 19 tasks are accounted for: 16 delivered as planned or amended by an approved
deviation, `t8` delivered beyond its stated scope, and `t12` and `t19` partial.

## Mid-work Decisions

Each approved deviation is quoted from `devague deviate --list`:

- `d1` — Built-in Gwen's typed prompt is no longer byte-identical to today's: it drops both the voice-only speakability line and the 'short sentences' brevity wording; her spoken prompt stays byte-identical. c30 narrows to 'spoken turns byte-identical; typed turns differ from today exactly by those two lines', pinned by a test; t9's `compose_base` typed output for the built-in persona changes accordingly; t11's first criterion is read with this narrowing — operator, 2026-10-31: 'Gwen prompts C, short sentences feats speech not text' (c30 and c3 conflicted for built-in Gwen on the typed path; option C chosen over A and B)
- `d2` — An answer that arrives after its ask expired still lands in its conversation, marked `after_expiry` and late, and is delivered as an event; only exact matches (thread or reply-to) reach an expired ask — operator, 2026-10-31: 'Whether a mesh answer arriving after its ask expired should still land, marked late. Yes. Via event'
- `d3` — An agent turn starts only when a message is ADDRESSED to Gwen (a direct message, or a line starting with 'spark-gwen:', 'spark-gwen,' or '@spark-gwen') and never from a system- nick; a notice that only names her is not a turn. Narrows t15's 'an agent mention starts a normal Gwen turn' — t19 live, 2026-10-31: on joining #general Gwen answered three automated notices in Hebrew, because any line naming her nick counted as a mention
- `d4` — A typed answer outlives its HTTP request: the page names each chat with a `request_id`; the daemon keeps the result (pending then done, 128, in memory) at GET /api/chat/{`request_id`} and tags the reply event; on http-control-timeout the page waits ('Still working on this') instead of failing, and questions left pending are recovered on the next visit — t19 live: a cortex turn answered at 80.2 s after the route gave up at 65 s; operator chose option A ('A then C')
- `d5` — Every chat completion streams (stream: true): the per-call 60 s read timeout becomes a bound on SILENCE between chunks, not on length (lesson 1); reasoning and answer tokens of a typed turn are published to the page as they arrive and shown live; no reasoning summary yet — t19 live: cortex calls cut off by the 60 s completion deadline 2 of 3 runs; operator: 'stream the tokens for now, so we can see reasoning and no timeout'
- `d6` — On a translated turn, remember stores the chosen model's English fact as the record and keeps the operator's own sentence beside it verbatim (metadata.original); forget by text matches either field (exact, normalised); searches still receive the operator's words — operator, 2026-10-31, review of #117 finding 5: 'suggestion accepted'
- `d7` — Which ask a mesh message answers is decided by a small senses call ('was it for me?') whenever an ask is listening in that channel or thread; the deterministic rules decide only when the judge cannot be asked (budget 60 per 10 min, 10 s bound) or its reply cannot be read, recorded as app-mesh-judge-failed — operator, 2026-10-31, review of #117 finding 3: 'A small llm call for common sense (Was it for me?)'
- `d8` — t8 posted the external-resident ask as agentculture/culture#506 instead of only drafting it; the post was on the operator's go-ahead — operator, 2026-10-31 (confirmed after the fact)

Decisions no deviation record covers:

- Voice through the translator stays gated on the chosen role's own measured latency; the role-plus-translator combination is not measured. Operator, 2026-10-31: "no need to measure, we can open issue and fix if not good". Tracked in [#118](https://github.com/agentculture/embodiment/issues/118).
- The two `/code-review medium` passes on #117 produced 11 confirmed findings, all fixed before merge (`d9b0c8a`, `21dfa21`); three of them became operator decisions (`d6`, `d7`, and the `#118` decision above).
- 28 SonarCloud findings were cleared before merge (`e76dfe6`, `146518e`), with no behaviour change. They had held the PR's `test` job red since `21dfa21`, unnoticed until the operator asked.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t11` (`d1`) | operator, 2026-10-31: 'Gwen prompts C, short sentences feats speech not text' (c30 and c3 conflicted for built-in Gwen on the typed path; option C chosen over A and B) | acceptable |
| `t5` (`d2`) | operator, 2026-10-31: 'Whether a mesh answer arriving after its ask expired should still land, marked late. Yes. Via event' | acceptable |
| `t15` (`d3`) | t19 live, 2026-10-31: on joining #general Gwen answered three automated notices in Hebrew, because any line naming her nick counted as a mention | acceptable |
| `t19` (`d4`) | t19 live: a cortex turn answered at 80.2 s after the route gave up at 65 s and the answer reached only the log; operator chose option A ('A then C') | acceptable |
| `t19` (`d5`) | t19 live: cortex calls cut off by the 60 s completion deadline 2 of 3 runs; operator: 'stream the tokens for now, so we can see reasoning and no timeout' | needs-follow-up |
| `t13` (`d6`) | Operator, 2026-10-31, review of #117 finding 5: the whole sentence as the record made two facts one and left forget nothing to find | acceptable |
| `t5` (`d7`) | Operator, 2026-10-31, review of #117 finding 3: 'A small llm call for common sense (Was it for me?)' | acceptable |
| `t8` (`d8`) | Operator, 2026-10-31: confirmed that posting culture#506 was their go-ahead (the plan said 'draft the external-resident issue (no post)') | acceptable |
| `t19` | not finished: no live mesh ask answered by another agent (c33), no published results document, no latency published as measured | needs-follow-up |
| `t12` | its contract is a model choice that answers; live, `worker` loops on tool calls and never answers (`e26`) | needs-follow-up |

## Evidence

All filed by `/validate-delivery` (`devague evidence --list`: e1–e27, each `proposed`):

- tests, file level (`e1`–`e22`, `e25`): 19 test files ran at `86aeabd` (main, after the merge), 410 passed, 0 failed. 15 of the files belong to plan tasks; `test_mesh_judge.py` (`d7`) and the chat-result and streaming files (`d4`, `d5`) belong to deviations. t8, t16 and t17 have no Python file of their own (t16 and t17 are covered by the web list below):
  - `tests/test_personas.py` 33
  - `tests/test_capabilities.py` 16
  - `tests/test_voice_latency.py` 16
  - `tests/test_mesh_client.py` 53
  - `tests/test_mesh_asks.py` 25
  - `tests/test_mesh_judge.py` 10
  - `tests/test_translate.py` 18
  - `tests/test_cli_mesh.py` 13
  - `tests/test_persona_prompt.py` 11
  - `tests/test_mesh_tools.py` 14
  - `tests/test_daemon_personas.py` 18
  - `tests/test_daemon_models.py` 31
  - `tests/test_daemon_translate.py` 10
  - `tests/test_http_personas.py` 43
  - `tests/test_daemon_mesh.py` 39
  - `tests/test_daemon_chat_results.py` 8
  - `tests/test_http_chat_results.py` 8
  - `tests/test_daemon_streaming.py` 15
  - `tests/test_release_docs.py` 29
- tests, node level (`e28`–`e50`, review of #119): for each automated obligation, the specific tests that assert it, 70 node ids, ran at `2a45f6b`, all passed; each record names its tests. They replace the file-level records above as the evidence for those obligations. Evidence records cannot be superseded, so both stay on file.
- tests (web): 10 vitest files ran, 60 passed:
  - `src/api/personas.test.ts`, `models.test.ts`, `mesh.test.ts`, `chatResults.test.ts`, `liveText.test.ts`;
  - `src/components/DockMeta.test.tsx`, `MeshCards.test.tsx`, `PersonasView.test.tsx`, `ChatLateAnswer.test.tsx`, `ChatLive.test.tsx`.
- full gates before merge, at `146518e`: 4926 Python passed (1 skipped), 615 web passed, black, isort, flake8, bandit, `teken cli doctor --strict` 26/26, web build.
- `tests/test_audio_remote.py::TestRound4ReviewFindings::test_control_send_burst_against_a_never_reading_peer_is_bounded` failed once under the parallel suite; it then passed 5 of 5 alone and in the full rerun. Untouched code, so read as a timing flake; no baseline rate measured.
- `tests/test_mesh_client.py::test_send_privmsg_from_another_thread` failed once in CI on #119 (`sent` read as 0) and passed 20 of 20 alone. Cause: the client counts a line only after its write drains, so the test read the counter too early. The same review found that a line whose drain failed or was cancelled was counted nowhere. Fixed in `2a45f6b`: it is now a `dropped_sends`, and the tests wait for the counter and then assert its exact value.
- CI on #117 at `146518e`: test, lint, version-check, test-publish, SonarCloud, GitGuardian all green.
- live (observation, `fidelity`): `e24` (cortex chat end to end, pass) and `e26` (worker/Bolt loops on tool calls, **fail**).
- manual: `e27`, mockup v2 approved by the operator (<https://claude.ai/artifact/PEUzLDEqvbEEc7DWN3LpuG>).
- commits: the PR's 100 commits, squash-merged as `86aeabd` (parent `8f9bc96`).
- PRs / issues: #117 (merged), #118 (open), agentculture/culture#506 (open).

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| Personas are operator-authored, private (0600), and built-in Gwen is the default (c6) | high | `e28` (9 named tests) |
| Default Gwen's spoken prompt is byte-identical to v1; typed differs by exactly two lines (c30, `d1`) | high | `e42` (4 named tests) |
| The model list equals the gateway's ready chat roles (c31) | high | `e31` (3 named tests) |
| An unavailable chosen model fails with its own error and never falls back (c22) | high | `e44` (7 named tests) |
| Every reply traces the role, model and host actually used (c9) | high | `e45` (2 named tests) |
| A non-`senses` role speaks only after its voice latency is measured (c51) | medium | `e32` (4 named tests); per-role voice latency itself is not measured live, and the translator combination is unmeasured (#118) |
| The Gemma translator gives the chosen role English and the operator the persona's language (c21) | medium | `e36` (3 named tests); translation quality is unmeasured |
| Searches use the operator's words; a translated `remember` keeps them beside the model's fact, and `forget` finds either (c37, `d6`) | high | `e37` (4 named tests) |
| Mesh text enters the prompt fenced and attributed, never as instructions (c14) | high | `e41` (4 named tests) |
| Mesh traffic is bounded: one ask per turn, rate caps, open-ask cap (c38) | high | `e34` (5 named tests) |
| Open asks survive a restart and expire visibly (c46) | high | `e35` (2 named tests) |
| The "was it for me?" judge places answers in context, and the rules are its recorded fallback (`d7`) | medium | `b7` · `e14` · `tests/test_mesh_judge.py`, `tests/test_daemon_mesh.py::TestTheJudge`; tested against a scripted judge only, never live Gemma |
| A typed answer outlives its request and streams live; recovered after leaving the page (`d4`, `d5`) | medium | `e23`, `e24` · `tests/test_daemon_chat_results.py`, `tests/test_daemon_streaming.py`; live n = 1; lapse `l5` (web tests written after the code) is pending |
| Per-conversation model choice works for every offered role (c8) | unverified | **fails live**: `e26` (worker/Bolt loops on tool calls, no answer); only the mechanism passes (`e49`: a typed turn asks the chosen role). Not claimed done |
| An agent contacting Gwen shows a card at once (c50) | high | `e48` (a daemon test and `MeshCards > renders an agent-contacted card`) |
| The UI follows the approved mockup v2 (c17, c34) | medium | `e50` (14 named web tests), `e27`; the live-reasoning panel (`d5`) is not in the mockup |
| At least one live end-to-end mesh ask answered by another agent (c33) | unverified | (no evidence — not claimed done) |
| Culture registers an external resident (c35) | unverified | (agentculture/culture#506 open — not claimed done) |

Lapse ledger evidence:

Approved by the operator on 2026-10-31, together with o1–o27, e1–e50 and b1–b7: `l1`–`l5`, all `grader-unverified`. They cap the claims they touch: the typed-answer and streaming row (`l5`) stays medium. `l2`, `l3` and `l5` record tests
written after the code, for t11, t14, and the web side of d4 and d5. `l1` and `l4` record
gate misses that were caught and fixed before merge.

## Remaining Work / Follow-up

- `t19` — run one live mesh ask answered by another agent and record its latency (c33); publish the live acceptance under `docs/live-test-results/`, latency as measured. Owner: the operator plus the next session.
- `t12` / c8 — worker/Bolt loops on tool calls and never answers; give it a final step with no tools (needs the operator's approval), then re-check live.
- c35 — agentculture/culture#506 is open; Gwen is not listed as a resident until Culture ships it. Owner: culture.
- #118 — voice latency through the translator; fix only if it proves slow in use.
- `d5` follow-up — a reasoning summary (deferred by the operator); the chat POST should return immediately when a `request_id` is present, removing the `http-control-timeout` entry logged at 65 s on long turns.
- The dock's persona and model reset after a page reload: restore them.
- Add the live-reasoning panel to the mockup artifact.
- Cleanup: deduplicate the private-file writers.
