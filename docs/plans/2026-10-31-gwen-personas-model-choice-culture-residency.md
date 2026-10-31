# Build Plan — Gwen personas, model choice, culture residency

slug: `gwen-personas-model-choice-culture-residency` · status: `exported` · from frame: `gwen-personas-model-choice-culture-residency`

> Gwen becomes a configurable companion: the operator designs personas (style, length, language, prompt) and talks with one, picks the reply model per conversation from what the lobes gateway currently serves, and Gwen holds a residency on Culture chat where she can choose to ask the mesh a question and bring the answer back — with less brevity enforced by fixed prompt text

## Tasks

### t1 — Persona model and private store (embodiment/personas.py)

- instruction: Pure data + file IO, no daemon imports. Mirror daemon/reading.py for the 0600 atomic-write pattern. Language None means no language line at all. Persona text is operator-authored and capped in size (a named constant with its reason).
- covers: c6, h13, c20, c24, h28
- acceptance:
  - embodiment/personas.py defines Persona (id, name, language he/en/none, length brief/conversational/detailed/unset, `allow_formatting`, `translate_via` role-name-or-None, `mesh_enabled`, text, `default_role`) and a built-in read-only Gwen persona; tests/`test_personas.py` passes
  - PersonaStore keeps personas.json 0600 under the state dir with a `schema_version` field; create/update/delete/list survive a reload; the built-in persona cannot be edited or deleted; a corrupt file degrades to built-in only with a recorded reason, never raises
  - `translate_via` is read only from the persona; no function in the module takes or inspects a model id string (AST or grep test)

### t2 — Capabilities poller: the chat roles lobes serves (embodiment/capabilities.py)

- instruction: Network only, keyed like realtime/client.py's `_fetch_capabilities` but reading every chat role. Role names come from the response keys; never parse model names.
- covers: c31, h6, h15
- acceptance:
  - CapabilitiesPoller.snapshot() returns every /capabilities role whose path is /v1/chat/completions, each with role, model, ready, host (member/`hosted_by` or local) and the snapshot's age; tests/`test_capabilities.py` passes against a fake gateway
  - the poll interval is a named constant (60 s) with its reason; a role that turns not-ready shows not-ready in the next snapshot; a failed poll keeps the last snapshot, marks it stale and records a degradation, never raises
  - no code derives a role, capability or language from a model id (test)

### t3 — Per-role voice latency store and probe (embodiment/`voice_latency.py`)

- instruction: Injected callables only; no gateway client here. Follow daemon/app.py:`derive_recall_deadline` for how a derived clock is written and documented.
- covers: c51
- acceptance:
  - VoiceLatency keeps per-role and per-translator-combination samples 0600 under the state dir; state is unmeasured, probing or measured; p90 and a derived spoken deadline come from the samples; tests/`test_voice_latency.py` passes
  - probe(role, complete) runs a fixed small set of short spoken-style completions through an injected complete callable and records first-token or completion latency; live spoken turns can add samples; the derived deadline formula is a named function with its reason (lesson 1), never senses' 60 s by default

### t4 — Gwen's own mesh client, stdlib IRC to agentirc (embodiment/`mesh_client.py`)

- instruction: asyncio streams only. Read agentirc/client.py (nick prefix rule, 433) and agentirc/protocol.py for the wire. No culture CLI subprocess. One connection per daemon; the daemon is the only sender as spark-gwen.
- covers: c19, h31, c39, h36, c12, h18
- acceptance:
  - MeshClient connects as spark-gwen (nick from config, never from a model name), joins configured channels, sends PRIVMSG and delivers inbound PRIVMSG and mentions to a callback; tests/`test_mesh_client.py` passes against a fake asyncio IRC server
  - on `ERR_NICKNAMEINUSE` it records mesh-nick-taken, sends nothing and stays off; a dropped connection records a degradation and reconnects within a bounded, derived backoff; close(deadline) never raises
  - it reports the servers visible on its connection (for status and c53) and adds no dependency (tests/`test_zero_deps.py` unchanged)

### t5 — Open asks and mesh bounds (embodiment/`mesh_asks.py`)

- instruction: Pure state + file IO. Answer matching is the documented rule in this module's docstring (see plan risk on matching); keep it one function so it can change.
- covers: c38, h35, c46, h43, c55, h48
- acceptance:
  - AskBook keeps open asks 0600 under the state dir with ask id, conversation id, channel, posted time and expiry; an answer is attached to its ask and conversation after a daemon restart; past its window an ask becomes expired, shown, never dropped; tests/`test_mesh_asks.py` passes
  - bounds: at most one ask per turn; a turn started by any mesh message can reply but cannot ask; a cap on open asks; a rate cap on mesh-started turns; each tripped bound returns a recorded refusal; a scripted peer answering every ask with a question yields at most one post per operator turn
  - the default expiry window is hours (a named constant with its reason), so a two-hour-late answer still lands, marked late

### t6 — Translation hop around a chosen model (embodiment/translate.py)

- instruction: No edits to loop.py (it is byte-pinned). This is a seam wrapper the daemon applies when the persona's `translate_via` is set.
- covers: c21, h26, c37, h34
- acceptance:
  - `translated_complete`(chosen, senses) wraps two complete callables: operator text is translated to English for the chosen role and its reply translated back to the persona language; the result carries both roles and both latencies; tests/`test_translate.py` passes
  - the operator's original text travels alongside the translation, so tool executors can use it: a remember call made in a translated turn receives the operator's own words and a search receives the operator's query (test with stub executors)
  - fenced data (recalled memory, library text, mesh text) is translated as quoted data, never promoted to instructions (test)

### t7 — CLI: spark-gwen identity in doctor and a mesh verb that prints, never runs (cli/`_commands`/mesh.py)

- instruction: Model it on cli/`_commands`/tunnel.py. Register in `_build_parser`().
- covers: c25, h29
- acceptance:
  - embodiment mesh prints the nick, the channels, the env names and the culture registration steps (text and --json) and runs nothing (test that no subprocess is spawned); explain/catalog.py has the entry; tests/`test_cli_introspection.py` and the teken rubric pass
  - embodiment doctor reports spark-embodiment and spark-gwen as two identities that share no nick, socket or culture.yaml entry

### t8 — Culture-side ask: scratch probe, then draft the external-resident issue (no post)

- instruction: Never run culture agents register or edit ~/.culture/server.yaml. The probe uses a throwaway agentirc instance on its own port.
- covers: c35, h32
- acceptance:
  - a scratch probe (outside the repo) shows whether a stdlib client with a spark-prefixed nick can join a channel on a test agentirc with no culture registration; the result is recorded on the frame
  - an issue draft for agentculture/culture asks for external-resident listing (manifest and residents view, no harness), sized by the probe; it is shown to the operator and posted only on the operator's go-ahead, via the communicate skill

### t9 — Persona-aware prompt parts: voice/typed split and tool-prompt language (embodiment/`persona_prompt.py`)

- instruction: Pure text. Do not edit turn.py's `SYSTEM_PROMPT` constant; split by composition. The obligation sentences (remember/forget/library/todos) are fixed; only their language and REMEMBER's length clause follow the persona.
- depends on: t1
- covers: c2, h9, c3, h10, c4, h11, c36, h33, h25
- acceptance:
  - `compose_base`(persona, path) returns exactly turn.`SYSTEM_PROMPT` for the built-in persona on the voice path, byte for byte; for the typed path it omits the voice-only speakability lines; tests/`test_persona_prompt.py` passes
  - for a non-default persona no fixed brevity line appears; the persona's length and language lines and its text appear in a fixed order; language None adds no language line
  - English renderings of the four tool prompts exist with the same obligation sentences; `tool_prompt_language`(persona) picks Hebrew for the built-in persona so its tool prompts stay byte-identical

### t10 — The `ask_culture` tool and its prompt line (embodiment/`mesh_tools.py`)

- instruction: Follow library/tools.py for fencing and daemon/todos.py for the guard. The prompt line steering the model is short and fixed, Hebrew and English renderings.
- depends on: t5
- covers: c13, h19, c15, h21, c18, h23, c14, h20, c41, h38
- acceptance:
  - `build_mesh_tools` returns an `ask_culture` ToolSpec whose executor posts only its question argument to an allow-listed channel and returns at once with an ask id; a non-listed channel, the switch off, or a tripped bound is a recorded refusal; tests/`test_mesh_tools.py` passes
  - a test plants recalled memory, library text and transcript text in the turn and asserts none of it reaches the post
  - mesh text returned to a turn is fenced and attributed like `render_recalled` output, and arms the write guard so remember, forget and `set_todo_done` refuse without the operator's own words (test)

### t11 — Daemon wiring A: personas in the prompt, per-conversation persona, restore

- instruction: Touch daemon/app.py (`_compose_prompt`, `_complete_turn`, `_enter_conversation`/`_resumed_session`) and session.py metadata only. New tests in tests/`test_daemon_personas.py`.
- depends on: t9
- covers: c30, h5, c42, h39, c7, h14, c5, h12, c44, h41
- acceptance:
  - with the built-in persona the composed prompt passed to the seam for a spoken and for a typed turn is byte-identical to before (tests/`test_turn.py`:986-1002 and tests/`test_daemon_app.py`:2664 unmodified and green, plus a new byte test on the real composed prompt)
  - each conversation has a persona id recorded in every assistant transcript record; continuing a conversation restores it; a deleted persona on continue is a visible state that answers nothing; a turn snapshots the persona at its start
  - the tool registry, write guard and identity framing are identical with and without a persona; spoken replies still hit the 4000-character cap as a recorded truncation; loop.py is unchanged

### t12 — Daemon wiring B: per-conversation model, distinct unavailable error, trace, voice gate

- instruction: Touch daemon/app.py (`http_complete`, seam closure, summarise, `_voice_complete`, `_text_complete`). Choosing an unmeasured role triggers `voice_latency`.probe in the background. New tests in tests/`test_daemon_models.py`.
- depends on: t11, t2, t3
- covers: c8, c9, h16, c11, h17, c22, h27, c32, h7, c43, h40, h45, h49
- acceptance:
  - seam() and the voice path read the conversation's role from one holder; summarise and recall-side calls stay on senses (test that a session summary names senses with worker chosen)
  - a 404 `role_infeasible`, 503 `role_unverified` or 429 from the chosen role yields a text-model-unavailable code distinct from the gateway-down code, and the fake gateway sees no second request (test over each status)
  - every assistant transcript record names role, model and host as the capabilities snapshot reported them; stt, tts, realtime and embedder requests are unchanged by the chosen role (test)
  - a spoken turn on an unmeasured role makes no completion request to it and shows probing or unavailable; after a recorded probe it uses the role with the derived deadline (test)

### t13 — Daemon wiring C: translator and the operator's words as the record

- instruction: Touch daemon/app.py tool executor wiring only. New tests in tests/`test_daemon_translate.py`.
- depends on: t12, t6
- covers: c37, h34
- acceptance:
  - with a persona's `translate_via` set, a typed and a spoken turn go through translate.`translated_complete`; the chosen role receives English and the operator receives the persona's language; the trace names both roles (test)
  - in a translated turn remember stores the operator's own words and `search_library` and `search_conversations` receive the operator's query; ContextPacket.original is the operator's text (test)

### t14 — HTTP routes: personas, models, conversation settings, status fields (http/server.py)

- instruction: Follow `_todo_set_route` and `_read_route`. ServerConfig gains callables; daemon/app.py passes them. No gateway key or secret in any payload.
- depends on: t13
- covers: c45, h42
- acceptance:
  - guarded GET/POST/PUT/DELETE /api/personas, GET /api/models (the snapshot with voice-latency state), and optional persona, model and language fields on /api/chat, /api/clip and /api/conversation/new; tests/`test_http_personas.py` passes; every route is refused without the secret or a valid Access assertion
  - /api/status adds the capabilities snapshot age and roles, each live conversation's persona and role, and voice-latency states

### t15 — Daemon wiring D: mesh resident, inbound agent turns, asks, events, counters

- instruction: Touch daemon/app.py, bus.py, the events schema and web/src/api/events.ts only. The answer arrives through daemon/`recall_events.py`'s Unprompted entry with its own dedup key.
- depends on: t14, t4, t10
- covers: c50, h44, c53, h46, c33
- acceptance:
  - with `EMBODIMENT_MESH` on (default) the daemon starts MeshClient, binds `ask_culture` on every turn, and an agent mention starts a normal Gwen turn in a conversation labelled with the agent's nick; the reply is visible on the page before or as it is sent (test with a fake IRC server)
  - a mesh event kind (ask posted, answer arrived, ask expired, agent contacted, Gwen replied) is in tests/fixtures/events/schema.json, bus.py and web/src/api/events.ts; with live voice on and the floor free one spoken notice per agent conversation goes through Unprompted
  - status shows mesh state, nick, visible servers and asks posted/answered/expired counters that move on a scripted run; the mesh switch off refuses `ask_culture` and sends nothing

### t16 — Web UI part 1: the settings row under the dock and the Personas tab (mockup v2)

- instruction: Mockup: <https://claude.ai/artifact/PEUzLDEqvbEEc7DWN3LpuG> (version 1793432718-7d11, approved; dimensions may be tuned). Use app.css/library.css tokens; dir=auto on every text node, labels outside it. Files: web/src/components/\*, web/src/api/personas.ts, models.ts, App.tsx.
- depends on: t14
- covers: c17, h22
- acceptance:
  - the dock-meta row shows Persona, Language (Hebrew, English, None) and Model menus opening upward, matching mockup v2's layout and interactions; the model menu greys not-ready roles and shows measured voice speed; Vitest tests for the menus pass
  - the Personas tab lists personas (built-in read-only), edits every persona field, shows the composed-prompt preview with persona and fixed sections, and saves through /api/personas
  - the unavailable banner reuses `chat__degraded` with its own text, the Model control turns amber, and one tap switches model; npm run build and the web lint pass

### t17 — Web UI part 2: mesh cards, agent conversations, open-asks count

- instruction: Touch web/src components for the thread and HistoryPane; no new design beyond mockup v2.
- depends on: t15, t16
- covers: c50
- acceptance:
  - the ask card (waiting, answered, expired, late), the answer card (quoted, marked as another agent's words) and the agent-contacted card render from mesh events as in mockup v2; agent conversations appear in the Conversations list with the nick; the row shows the open-asks count; Vitest tests pass

### t18 — Docs and pinned tests: README, explain root, CLAUDE.md

- instruction: Docs only, plus tests/`test_release_docs.py`. Re-probe /capabilities through grant run.
- depends on: t15
- covers: h2, c27
- acceptance:
  - README and the explain root describe personas, model choice, the translator and spark-gwen, state that English is text-only while lobes has no English voice, and that a newly linked server can read Gwen's memory; tests/`test_release_docs.py` `NOT_YET` and `TOOLS_BOUND` are updated with `ask_culture` and pass
  - CLAUDE.md's cross-repo posting rule says Gwen's own mesh posts are hers by the operator's decision (q1) while issue posts stay the operator's; its role table is refreshed from a fresh /capabilities probe; the before-state quotes code at the export commit; markdownlint passes

### t19 — Live acceptance on the rig, published as measured

- instruction: Run under grant run on a test daemon (own port and state dir), never the public gwen-live unit. Operator listens and speaks for the voice parts.
- depends on: t17, t18, t8
- covers: c1, h30, c26, h1, c28, h3, c29, h4, c33, h8, c36
- acceptance:
  - the after-state is walked once on the rig through the page: a persona created, one conversation per chat-ready role, a translated conversation, voice on a measured role, one mesh ask answered end to end and one agent-initiated conversation; each recorded with counters that moved
  - measured and published under docs/live-test-results/ with n stated: English remember/forget call rate (the d7 re-run), voice latency per role and with the translator, mesh answer latency; no claim that answers got better; bad numbers published as measured

## Risks

- [unknown_nonblocking] How an answer on the mesh is matched to its ask is undecided: IRC has no threads, and whether agentirc carries IRCv3 reply tags (msgid / +reply) is unread; the fallback rule (a message addressing spark-gwen in the ask's channel attaches to that channel's most recent open ask, ambiguity shown on the card) can misattribute when several asks are open (task t5)
- [unknown_nonblocking] h45 was written for the marked policy ('a spoken turn's request names worker'); under the gated c51 it holds only once worker is measured, and t12 tests it in that state alongside h49 (task t12)
- [unknown_nonblocking] daemon/app.py is touched by t11, t12, t13, t14 and t15, so those waves are serial by construction; parallelism lives in wave 0
- [follow_up] The English re-run of the remember/forget measurement (c36, d7 was 5 of 5 in Hebrew) may come back worse; if it does, English personas ship with that number published and the tool line is revised in a follow-up, not hidden (task t19)
- [unknown_nonblocking] This branch stacks on feat/gwen-queries-the-library (0.23.0, embodiment#107, its live acceptance t9 not yet done); the work must rebase or merge onto main once #107 lands, per the squash-merged-stacked-PRs practice
- [follow_up] The culture-side registration (t8's issue) may not land before this ships; Gwen still connects (agentirc accepts any spark- prefixed nick) but is not listed as a resident until culture supports it (task t8)
