# Gwen personas, model choice, culture residency

> Gwen becomes a configurable companion: the operator designs personas (style, length, language, prompt) and talks with one, picks the reply model per conversation from what the lobes gateway currently serves, and Gwen holds a residency on Culture chat where she can choose to ask the mesh a question and bring the answer back — with less brevity enforced by fixed prompt text
> instruction: the acceptance is the after-state walked live on the rig

## Audience

- The operator, the one person who talks with Gwen on this rig (gwen.culture.dev and the room mic), who wants to design how she talks and which mind answers; secondarily the mesh agents who receive her questions
  - instruction: check the README and explain root name the operator as the user and spark-gwen's mesh audience

## Before → After

- Before: Today Gwen answers from one fixed Hebrew prompt that orders short spoken sentences and no lists (turn.py:185-190), in typed chat as well as voice; the reply model is always senses; the operator cannot change either without editing code, and Gwen has no way to ask the mesh
  - instruction: cite turn.py:185-190, app.py:1230-1231 and the absence of any mesh code in embodiment/
- After: The operator opens a persona editor, writes a persona (name, language, length and style, free prompt, default model, optional Gemma translator), starts a new conversation with it and gets answers in that voice; switches the model from a list of what lobes serves right now; and when Gwen judges a question is better asked elsewhere she posts it to the mesh as spark-gwen and the answer appears in the conversation when it arrives
  - instruction: walk the after-state end to end on the rig as the live acceptance: one persona created, one conversation per chat-ready role, one mesh question answered

## Why it matters

- Gwen's usefulness is capped by a prompt the operator cannot change and one model the operator cannot choose; the rig serves several stronger or different minds and the mesh holds more agents, and none of them is reachable from a conversation with her
  - instruction: no value claim: the spec states this as the reason, and the acceptance measures behaviour, not usefulness (CLAUDE.md lesson 2)

## Requirements

- Brevity stops being enforced by fixed prompt text: the base `SYSTEM_PROMPT` (turn.py:185-190, 'answer in short sentences', 'ask a short clarification') and the daemon's 'briefly' lines (RESTART and `FOLLOW_UP` instructions, app.py:1150-1153) move into the persona, so length and tone are the operator's setting, not the code's
  - instruction: move the length and tone sentences of `SYSTEM_PROMPT` and the briefly-lines into the default persona's text; keep the default byte-identical (see the first success signal)
  - honesty: with the default persona nothing changes; with any other persona the base brevity lines are absent from the composed prompt, and a test reads the composed prompt to prove it
- Typed and spoken replies get different speakability rules: today one assembler (`_compose_prompt`, app.py:6388-6428) serves both paths, so a typed chat answer inherits the voice-only 'no formatting, no lists, short sentences' constraint; a typed turn may be long and formatted, a spoken one stays speakable
  - instruction: split `_compose_prompt`'s base into a shared part and a voice-only speakability part chosen by the turn's path
  - honesty: a typed turn's composed prompt carries no voice-only speakability line, and a spoken turn's still does, both checked by test
- A persona is operator-authored data (name, language, style and length guidance, free prompt text, default model role) kept 0600 in the private state dir, created and edited through guarded /api/personas routes, and chosen per conversation; each turn's transcript record names the persona used
  - instruction: a personas.json (0600) next to reading-positions.json; guarded GET/POST/PUT/DELETE /api/personas; persona and role through `add_assistant` metadata
  - honesty: personas persist 0600 under the state dir, survive a restart, and every assistant transcript record carries persona and role fields
- The operator chooses the reply model per conversation from a list built from the gateway's GET /capabilities: every role whose path is /v1/chat/completions and that is ready, re-read on a poll because lobes publishes no change event; the choice is sent as the role name in the request's model field, never parsed from a model id
  - instruction: a CapabilitiesPoller reading /capabilities; the seam reads the conversation's role from a holder instead of config.role; summarise uses the same holder
  - honesty: no code path derives a role, a capability or a language from a model id string, checked by test or grep
- Every turn's trace records the role actually called and the model and host the gateway reported for it, and a chosen role that is unavailable (404 `role_infeasible`, 503 `role_unverified`, 429 shed) is a recorded degradation shown on the page, never a silent substitution
  - instruction: capture model and host from the poller snapshot at call time; test with a proxied fake role
  - honesty: every assistant transcript record names role, model and `hosted_by` as lobes reported them for that turn
- Gwen holds her own Culture mesh identity (a new nick such as spark-gwen), distinct from this repo's agent spark-embodiment (culture.yaml: suffix embodiment, backend colleague), so a reply addressed to Gwen never reaches the colleague harness and vice versa
  - instruction: a separate agent definition for spark-gwen; doctor reports both identities
  - honesty: spark-gwen and spark-embodiment never share a socket, a culture.yaml entry or a nick
- Gwen can choose, inside a turn, to ask the mesh: an `ask_culture` tool posts the operator's question to an allow-listed channel and returns at once; the answer arrives later and is delivered to the page and, when the floor allows, spoken, through the existing unprompted path (daemon/`recall_events.py`) and a new event kind
  - instruction: tool posts and returns an ask id; the resident delivers answers by ask id onto the bus and through Unprompted; new event kind in schema.json, bus.py and `EVENT_KINDS`
  - honesty: `ask_culture` returns within its own bounded deadline and never holds the turn open waiting for an answer
- The UI is designed first as a mockup artifact the operator approves, built from the page's own components and tokens (web/src/styles/app.css, library.css); persona, language and model are a small-font control row under the dock, where the page has room for more controls, opening menus upward; personas are edited in their own tab; no invented chrome such as a conversation header
  - instruction: publish the mockup artifact; record the operator's approval on the frame before implementation
  - honesty: no UI code for these features is written before the operator approves the mockup artifact
- Gwen decides on her own judgement when to ask the mesh (operator decision q1, 2026-10-31); asks raised by her own triggers, such as system events, are a later stage, not this one
  - instruction: self-triggered asks (system events) are a parked follow-up; this stage has exactly one posting path
  - honesty: Gwen posts only when the model calls `ask_culture` in a turn; no other code path posts in this stage
- Gwen's mesh presence is a resident spark-gwen whose persistent connection Gwen's own daemon holds (q1 = A): an attributed nick, mentions and replies arrive without polling, and every mesh message goes through Gwen's own turn path, so no harness backend and no second model ever speaks as her (operator decisions q2 and q1)
  - instruction: a mesh client inside the daemon (stdlib asyncio, the IRC wire agentirc speaks), started and closed with the daemon under derived deadlines; a dropped connection is a recorded degradation and a bounded reconnect
  - honesty: the only process that sends as spark-gwen is Gwen's daemon, and every outgoing mesh line is produced by a Gwen turn; with the daemon stopped, spark-gwen is absent from the channel, by a live check
- Language is a persona setting with a per-conversation override: Hebrew, English or None (None sends no language line); Hebrew becomes optional and today's Gwen, Hebrew, stays the default persona (operator decisions q3 and 2026-10-31 mockup review)
  - instruction: language is a persona field that selects the language line and the stt/tts lane names; voice in a non-Hebrew language is limited to what lobes serves (stt-en exists; tts is Hebrew-only today)
  - honesty: the default persona speaks Hebrew exactly as today; a persona set to English gets no Hebrew instruction in its composed prompt
- A persona may pair a model that does not speak Hebrew with a Gemma translation hop: Hebrew in is translated to English for that model and its answer translated back to Hebrew, and the trace names both roles (operator decision q3)
  - instruction: translation is two extra senses completions around the chosen one; tool results and fenced data are translated as data, never as instructions
  - honesty: with the translator on, the chosen model receives only English text and the operator receives only Hebrew, and the trace names both roles and both latencies
- A chosen model that is unavailable fails the turn with its own error message, distinct from the models-down message, and the page offers to switch model; nothing falls back silently (operator decision q4)
  - instruction: a new code such as text-model-unavailable distinct from the existing gateway-down code; the page renders a switch-model action on it
  - honesty: the model-unavailable message differs from the models-down message by code and by text, and the page offers the switch
- Culture learns to register an external resident (an agent with a nick and a manifest entry but no harness backend); the ask is drafted as an issue for agentculture/culture and posted only on the operator's go-ahead (CLAUDE.md: propose to siblings, don't push)
  - instruction: draft through the communicate skill; record the issue link on the frame once the operator posts it
  - honesty: nothing is posted to agentculture/culture without the operator's explicit go-ahead, and embodiment's side degrades to a recorded 'mesh-unregistered' state until culture supports the registration

## Honesty conditions

- every part of the announcement maps to at least one requirement claim and one success signal in this frame
- the four measured tool prompts are byte-identical under every persona, by test
- a spoken reply over 4000 characters is truncated and the truncation is a recorded degradation, unchanged by any persona
- the tool registry, write guard and identity framing are identical with and without a persona, by test
- stt, tts, the realtime session and the embedder are byte-identical in their requests whatever role is chosen
- a mesh answer containing an instruction to remember, forget or mark a to-do causes none of those writes, by test
- outgoing text is only the question Gwen composed; a test plants recall, library and transcript text in the turn and asserts none of it is posted; channels outside the allow-list are refused
- the translator flag is read only from the persona; no code inspects a model name to decide it
- no code in the repo runs culture agents register or edits ~/.culture/server.yaml
- no part of the feature assumes a second human user: personas, model choice and mesh asks are all scoped to the one Access-allow-listed operator
- the before-state is quoted from the code at the commit the spec is exported from, not from memory
- each step of the after-state is exercised once live on the rig, through the page, and the result is recorded with what was measured and what was not
- the spec makes no claim that answers get better; any quality comparison between models is published as measured, including when it is negative
- the byte comparison runs against the real composed prompt (the `_compose_prompt` output passed to the seam), not against a constant
- the poll interval is a named constant with its reason, not a magic number (lesson 1), and the list shows roles by name with the model and host lobes reported
- the test asserts on the requests actually sent to the fake gateway, so a silent second call to senses would fail it
- the probe records the post, the reply and its delivery as counters that incremented (lesson 3), not as a status that looked healthy

## Success signals

- With the default persona selected, the composed system prompt for a spoken and for a typed turn is byte-identical to the prompt before this change, for 100% of the pinned prompt tests
  - instruction: tests/`test_turn.py`:986-1002 and tests/`test_daemon_app.py`:2664 stay green unmodified; add a test that composes with the default persona and compares bytes
- The model list equals the set of /capabilities roles with path /v1/chat/completions and ready=true within one poll interval of at most 60 s, and a role that turns not-ready is greyed within the same interval
  - instruction: a test with a fake gateway flips a role's ready and asserts the list within the poll; one live check against lobes
- A turn on an unavailable chosen role yields the distinct model-unavailable error in 100% of the 404, 503 and 429 cases tested, and 0 replies are produced by any other role
  - instruction: tests over `http_complete` with each status; assert no second request and the error code on the page
- At least 1 live end-to-end mesh ask on the rig: Gwen posts as spark-gwen to an allow-listed channel, another agent answers, and the answer reaches the same conversation on the page, with its latency recorded
  - instruction: live probe, recorded under docs/live-test-results/ with n stated

## Scope / boundaries

- The measured tool-obligation prompts stay fixed text a persona cannot edit: `REMEMBER_TOOL_PROMPT` was measured 5 of 5 against 3 of 5 for a bland line (d7, app.py:470-480), and `LIBRARY_TOOL_PROMPT` exists because typed turns called the tool 0 of 6 without it (d1, app.py:493); only their wording about reply length is style
  - instruction: the persona layer is composed outside the tool-prompt block; a test iterates personas and compares the tool block
- The voice output caps are a separate, mechanical bound and remain: `Voice.speak` truncates at 4000 characters and 500 per sentence (voice.py:340-344); a persona that asks for long spoken answers meets that cap, and the cap is reported, not hidden
  - instruction: no change to voice.py caps; test that a long persona reply still hits the existing cap path
- A persona changes style only, never authority: it does not pass through identity.py resolution, does not alter the tool registry, the measured tool prompts, the write guard or routing, and with no persona selected the composed prompt is byte-identical to today's (tests/`test_turn.py`:986-1002, tests/`test_daemon_app.py`:2664)
  - instruction: persona text is a separate prompt section; it never sets TurnConfig.identity and never touches `_turn_tools`
- Model choice changes who writes the reply, not the ears or the voice: stt, tts and the realtime session are untouched, and the embedder for recall stays gemma2-embed
  - instruction: test the realtime and voice requests carry no chosen-role field
- Mesh text is untrusted data: answers from other agents enter the prompt fenced and attributed the way recalled memory and library text do, never as instructions, and they cannot trigger remember, forget or `set_todo_done` without the operator's own words
  - instruction: fence mesh text with `render_recalled`-style attribution; arm the write guard on any turn holding mesh text
- What leaves the room is bounded: only the question Gwen composes from the operator's words goes out, never recordings, transcripts, recalled memory or library text, and only to channels on an operator allow-list
  - instruction: an `EMBODIMENT_CULTURE_CHANNELS` allow-list; a post filter; refusals are recorded degradations
- Whether a model needs the translation hop is set explicitly on the persona, never inferred from the model's name (CLAUDE.md: roles resolve by name, never by parsing model names)
  - instruction: persona field `translate_via` (a role name or null)
- Registering the spark-gwen resident (culture agents register, the server.yaml manifest) is the operator's provisioning act: the repo ships the agent definition and the commands, the way tunnel prints cultureflare commands and runs none
  - instruction: a command prints the registration steps, like tunnel; test it runs nothing

## Non-goals

- No new Python dependency: the daemon's mesh client uses the standard library against the IRC wire agentirc speaks, so tests/`test_zero_deps.py`'s approved set is unchanged
  - instruction: tests/`test_zero_deps.py` stays green unmodified
- Serving a model lobes does not serve today (for example Nemotron Super) is a separate lobes task; the picker lists only what lobes serves (operator decision q5)

## Assumptions

- Switching the speaker away from senses needs per-role request tuning: cortex is a reasoning model whose lane uses a qwen3 reasoning parser and saves about 75% of tokens with thinking off (lobes docs/qwen3.8-27b-nvfp4.md:161-172); the 60 s completion deadline (app.py:1228) and the 4000-token window budget (session.py:273) were sized for senses

## Scope exploration

- `s1` — `embodiment/turn.py SYSTEM_PROMPT`: the only base prompt; it hard-codes short sentences and no lists; AppConfig.`system_prompt` (app.py:1231) defaults to it and nothing reads a prompt from env, file or config (`from_env` app.py:7370-7390)
  - seeds: `c2`
- `s2` — `daemon/app.py _compose_prompt`: one assembler for voice and typed turns: base prompt, clock, tool prompts, window, continuation, recalled memory, joined by blank lines; typed turns bypass Voice and its caps
  - seeds: `c3`
- `s3` — `daemon/app.py tool prompts`: REMEMBER, LIBRARY, CONVERSATIONS and TODOS prompts are measured obligations; REMEMBER's 'answer one short sentence' clause is the one length rule inside them
  - seeds: `c4`
- `s4` — `embodiment/voice.py caps`: `MAX_REPLY_CHARS` 4000 and `MAX_SENTENCE_CHARS` 500 apply to spoken replies only; `max_tokens` 16000 (turn.py:301) is not a brevity cause
  - seeds: `c5`
- `s5` — `daemon/state.py TranscriptLog`: TranscriptLog.write (state.py:1318) takes arbitrary metadata fields; today it records via, timestamps and recording, but no persona, role or model; no per-conversation config object exists (Session and the d5 restore carry turns only)
  - seeds: `c6`
- `s6` — `embodiment/framing.py + identity.py`: identity is a resolved nick that `frame_cortex` wraps with a grants-nothing sentence (framing.py:131-160); the daemon never sets TurnConfig.identity, so persona must be a separate style layer, not an identity
  - seeds: `c7`
- `s7` — `lobes /capabilities (probed 2026-10-31)`: chat roles: senses Gemma-4-26B ready, cortex Qwen3.8-27B ready (proxied spark2), worker Nemotron-3.5-Bolt-74B ready, hand LFM2.5-1.2B ready (32768 context), muse Gemma-4-31B and associate Nemotron-3.5-Lightning-30B not ready; no 'Nemotron Super' is served; lobes routes model= by role, served id or role-member lane and proxies chat to peers (lobes gateway/`_routing.py`:541-563)
  - seeds: `c8`
- `s8` — `daemon/app.py http_complete + seam()`: the role is the model field (app.py:1524-1530); every turn goes through one seam() closure (app.py:7478-7487) plus summarise() (7498-7504), both reading the frozen config.role; that is the single switch point
  - seeds: `c8`
- `s9` — `CLAUDE.md Gwen design rules`: traces must expose the actual role, model and machine; a single-model run must not claim another mind exists; lobes never substitutes a cheaper model on shed
  - seeds: `c9`
- `s10` — `reasoning models and clocks`: embodiment reads `reasoning_content` into a separate field (app.py:1551) but strips no inline think block and sends no `chat_template_kwargs`
  - seeds: `c10`
- `s11` — `realtime session`: the realtime WS cannot cross machines but chat completions are proxied single-hop by lobes, so a proxied speaker role is reachable
  - seeds: `c11`
- `s12` — `culture.yaml + /run/user/1000 sockets`: the repo's mesh identity is spark-embodiment, not running (no culture-spark-embodiment.sock); live sockets are agentirc, associate, colleague, culture
  - seeds: `c12`
- `s13` — `daemon/recall_events.py Unprompted`: the one entry point for Gwen speaking without being spoken to, built to serve job results too; follow-ups default off; a mesh answer would need its own window and dedup key
  - seeds: `c13`
- `s14` — `culture CLI channel.py`: channel message and channel read fall back to an anonymous peek connection without an agent daemon (`culture_core`/cli/channel.py:368-370, 266-268); ask and topic require a running daemon (`_require_ipc`); being mentioned requires a persistent connection
  - seeds: `c13`
- `s15` — `embodiment/tools.py + write guard`: ToolRegistry turns executor failures into recorded outcomes; `library_guarded_writes` already refuses memory writes in a turn holding library text
  - seeds: `c14`
- `s16` — `CLAUDE.md privacy of the room`: recordings and transcripts are private 0600 under the state dir; mesh channels are shared with every agent on the host
  - seeds: `c15`
- `s17` — `tests/test_zero_deps.py`: dependencies are human-gated; an IRC client library would need approval
  - seeds: `c16`
- `s18` — `web/src/App.tsx`: no settings, persona, model or mesh UI exists; the nearest surfaces are the Status drawer (App.tsx:753-793) and the conversation header; /api routes are guarded automatically (guard.py:449); chat already takes an optional conversation field (server.py:1376)
  - seeds: `c17`
- `s19` — `UI mockup artifact`: mockup v2 (version 1793432718-7d11, <https://claude.ai/artifact/PEUzLDEqvbEEc7DWN3LpuG>) approved by the operator 2026-10-31: 'Perfect! We may need dimension adjustments, but the interactive UI is great.' Sizes may be tuned at implementation; the layout and interactions are the approved design
  - seeds: `c17`

## Decisions

- The UI design is mockup v2 (artifact version 1793432718-7d11), approved by the operator on 2026-10-31 ('Perfect! We may need dimension adjustments, but the interactive UI is great'): layout and interactions are fixed, dimensions may be adjusted during implementation

## Hard questions

- What holds spark-gwen's persistent connection? A Culture harness backend (claude/codex/colleague) brings its own model, a second mind speaking as Gwen, which the design rules forbid; the alternative is Gwen's daemon holding the IRC connection itself (stdlib asyncio against agentirc, no new dependency) and registered with culture as an external resident, if culture supports one (resolved: operator, 2026-10-31: 'q1 A - recommendation accepted.' Gwen's daemon holds the persistent mesh connection itself (stdlib, no new dependency) as spark-gwen; every mesh message goes through Gwen's own turn path; registration as an external resident needs culture support, to be drafted for the operator, not posted)

## Open parks

- [unknown_nonblocking] Hebrew reply quality and tool-call reliability of cortex, worker and hand are unmeasured; Gwen's tool prompts were measured on senses only
- [unknown_nonblocking] Mesh answer latency is unmeasured (agents are model-driven: seconds to minutes), so the window for delivering a late answer cannot be derived yet
- [unknown_nonblocking] Whether CLAUDE.md's role table (2026-10-23: worker = Qwen3.6-35B) should be refreshed: the gateway now reports worker = Nemotron-3.5-Bolt and cortex = Qwen3.8-27B
- [unknown_nonblocking] Which served models answer well in Hebrew, and how much latency the Gemma translation hop adds to a turn, are unmeasured
- [unknown_nonblocking] Whether culture already accepts a registered agent with no harness (none documented: docs/reference/harnesses/index.md and docs/shared/concepts/harnesses.md describe harness-run agents only), and how agentirc authenticates a nick, decides how much the culture-side change is
