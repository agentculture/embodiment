# Build Plan — Gwen: loop, presence, continuity

slug: `gwen-loop-presence-continuity` · status: `exported` · from frame: `gwen-loop-presence-continuity`

> embodiment ships colleague's bounded tool loop as a reusable stdlib-only package — one loop, usable as a library or CLI — where Qwen is the working cortex and an optional Gemma 4 31B muse injects guidance and critique as a subconsciousness; configured identity makes it Gwen per colleague#352 (byte-identical prompts when absent), and an eidetic/coherence subprocess adapter makes its presence continuous across sessions with every degradation observable

## Tasks

### t1 — Carve the data contract into embodiment/contract.py

- acceptance:
  - embodiment.contract imports stdlib only and defines Task, TaskResult, Step, ToolCall, ModelResponse, WorkAborted, ContextPacket, SensesRecord and the OK/ERROR/INCOMPLETE/NO_RESULT_PRODUCED constants with field parity to colleague's contract.py public shapes
  - no colleague import anywhere; unit tests construct and round-trip every dataclass

### t2 — Zero-deps guard and extras discipline

- covers: c3, h3, c24, h19
- acceptance:
  - a runtime sys.modules-diff test proves importing every embodiment module introduces no third-party top-level module, with no allow-list
  - a test asserts pyproject [project].dependencies == [] and that base import pulls no websocket/audio/third-party module (the realtime boundary)
  - any optional extra imports lazily inside a function; a guard test proves module-scope purity

### t3 — Port context windowing and media handling

- depends on: t1
- acceptance:
  - window_messages, classify_degradable, count_tokens_chars and the media flatten/build/rejection-retry helpers ported with their tests, stdlib-only

### t4 — Extract the bounded tool loop with injection points

- depends on: t1, t3
- covers: c2, h2
- acceptance:
  - the loop is driven only by an injected complete callable and an injected tool-executor protocol; module-scope imports are stdlib plus embodiment-internal only
  - termination test matrix: model finish, empty tool-call turn, max_steps budget — no other exit path exists; hooks add none and cannot extend the budget
  - the four-event hook lifecycle (task_start/pre_tool/post_tool/finish) ported with only pre_tool control-bearing (deny/rewrite); every colleague-policy import from the triage (lint, escalation, memory, autosplit, backpressure, coherence-gate, …) is gone or an injectable callback

### t5 — No-shell host fixture

- depends on: t4
- covers: c35, h21
- acceptance:
  - a test drives the full loop with an injected executor exposing zero shell tools and asserts normal completion
  - a source grep test proves no shell_cli / shell-cli reference exists in embodiment

### t6 — Port the presence policy verbatim

- covers: c43
- acceptance:
  - UpdateCadence, should_update, ClarifyPolicy, should_clarify, is_go_word and the *_from_env builders port byte-faithfully with their tests, including never-raise on malformed env values
  - the no-forbidden-imports AST test (time/threading/datetime/subprocess) ports and passes

### t7 — Redesign the presence engine around cortex + muse

- depends on: t1, t6
- covers: c21, h18, h24
- acceptance:
  - PresenceIO keeps eight plain callables all defaulted to no-ops; narrate stays exception-swallowed so a voice hook can never disturb the text path
  - the engine drives cadence step/phase-based with no TTY, no thread, no clock; the no-front-import AST guard ports and passes
  - the engine consumes the cortex loop and the optional muse seam instead of a senses-loop driver, and the CHANGELOG/spec text names this redesign; the engine exposes no external presence event stream (loop-only)

### t8 — Verbatim perception and never-raise

- depends on: t7
- covers: c6, h6
- acceptance:
  - the perception intake sets the packet original from the caller's input verbatim; a test with hostile model output asserts byte-identity
  - fault-injection tests (dead port, request error, overflow, lossy JSON) show every public presence entry point returns a degraded value and never raises

### t9 — Degradation ledger

- depends on: t4, t7
- covers: c5, h5
- acceptance:
  - an enumeration test walks every degradation path (budget exit, muse absence/failure, adapter failure, presence downgrade) and asserts each appends a host-visible record
  - a source scan proves no silent except-pass exists on any presence path

### t10 — Optional muse seam — the advisory subconsciousness

- depends on: t4, t7
- covers: c41, h22, c42, h23
- acceptance:
  - the muse arrives as an optional injected completion seam; its output enters the loop only as advisory text in the guidance/message stream
  - a test asserts no muse-sourced path can deny or rewrite a tool call (the pre_tool hook registry receives nothing muse-sourced)
  - the default CI fixture runs the loop museless end-to-end; muse configured-but-dead and muse mid-run-failure each produce a recorded degradation
  - the muse endpoint arrives via explicit injected configuration (role-by-name style), never parsed from model names

### t11 — Resolved-identity seam

- covers: c15, h13, c19, h17
- acceptance:
  - identity resolves only from explicit host configuration (culture.yaml nick/suffix, then identity.json — mirroring colleague's resolution order and its reserved insertion slot); a test changes model names and asserts the resolved identity is unchanged
  - embodiment's own whoami/doctor output is byte-identical with and without a Gwen configuration present in a consuming-rig fixture

### t13 — Shared subprocess adapter for eidetic and coherence

- depends on: t1
- covers: c9, h8, c10, h9, c26, h20
- acceptance:
  - one adapter module shells to both CLIs with --json, relying on their shared exit-code and stdout/stderr contract; every eidetic invocation sets EIDETIC_DATA_DIR or an explicit cwd
  - coherence verdicts parse from the JSON payload including the unavailable field — a test proves an unreachable embedder (exit 0) still surfaces as a recorded degradation
  - the adapter carries its own drift test pinning scope/visibility conventions per eidetic docs/contract.md section 4
  - embodiment source contains no store, no scoring, no embedding logic; each subsystem is optional and its absence degrades to a recorded no-continuity mode

### t14 — Continuity lifecycle checkpoints

- depends on: t4, t13
- covers: c11, h10, c12, h11
- acceptance:
  - named checkpoints exist — before consequential action, before final completion, before durable memory — each covered by a test showing coherence consulted and memory writes gated there
  - provenance flows perception to action to durable record (added_by, links, supersedes populated on writes)
  - a test proves a coherence verdict cannot alter a tool-approval decision and vice versa — separate injection points

### t15 — Non-colleague demo app

- depends on: t4, t7, t13, t14
- covers: c13, h12, c36, h27, c37, h28
- acceptance:
  - a minimal demo host in-repo runs two separate process runs and the second demonstrably recalls the first (continuity across interactions)
  - the demo uses only embodiment's public API — no colleague-specific types or imports — and an app author can follow it from the README alone

### t16 — Explain catalog and CLI surface — the software-presence boundary

- covers: c8, h7
- acceptance:
  - embodiment explain output renders the software-presence boundary statement and a catalog test asserts its presence
  - the explain catalog covers the new loop/presence/identity/continuity seams so the rubric gate and introspection tests stay green

### t17 — Relationship docs for app authors

- depends on: t13, t14
- covers: c38, h29
- acceptance:
  - docs describe the embodiment/eidetic/coherence ownership split in app-author language, the Colleague=runtime / Gwen=teammate / Qwen=cortex / Gemma-31B=muse table, and the before-to-after story citing the survey (scope entries) rather than asserting it

### t18 — Record the framing divergence on colleague#352

- covers: c44, h25
- acceptance:
  - a comment exists on colleague#352, signed '- embodiment (Claude)', recording that embodiment implements the cortex+muse framing and leaves senses framing to colleague — posted before the identity-framing implementation merges

### t19 — Seam-proposal issue on agentculture/colleague

- depends on: t4, t13
- covers: c4, h4
- acceptance:
  - a tracked issue on agentculture/colleague proposes importing embodiment, names the loop and memory/coherence wiring it replaces, and records the C1b base-dependency question as colleague's decision
  - no embodiment-authored commit or PR lands directly on agentculture/colleague

### t12 — Role-framed prompt composition — Gwen per colleague#352

- depends on: t4, t11, t18
- covers: c16, h14, c17, h15, c18, h16
- acceptance:
  - golden test: with no identity configured, every prompt surface byte-equals the pre-identity output
  - with identity on versus off, tool schemas, routing, and approval decisions are identical — only speaker-framing text differs, proven by a composition diff test
  - cortex framing applies only to the top-level acting loop (typed subagents never get it); a museless run mentions no second mind; tests cover unnamed, named-single-model, and named-dual-model composition

### t20 — Integration verification — the announcement checklist

- depends on: t2, t5, t9, t12, t15
- covers: c45, h26, c39, h30, c40, h31
- acceptance:
  - one CI-runnable checklist verifies every announcement clause: zero-deps guard green, byte-identical-prompt golden green, museless default fixture green, no-shell fixture green, degradation ledger green, and the continuity demo across two separate interactions green
  - the three success signals are checkable: CI status for the test families, the demo app passing in-repo, and the two issue links (seam proposal, #352 comment) resolving on GitHub

## Risks

- [follow_up] C1b residual: whether colleague allow-lists embodiment as a base dependency alongside agentfront (and shell-cli) is colleague's decision — recorded in the seam-proposal issue, outside embodiment's control (task t19)
- [unknown_nonblocking] the reference muse deployment is declared-UNVALIDATED (lobes thor-muse, lobes#108): rig validation of the 31B muse is outside embodiment's control; museless remains the honest default until acceptance evidence lands (task t10)
- [unknown_nonblocking] the contract.py carve boundary may shift during extraction — which Task/TaskResult fields are truly loop-essential is confirmed only when the loop's tests pass without colleague imports (task t1)
- [unknown_nonblocking] subprocess overhead at checkpoint boundaries is unmeasured; if latency matters, the follow-up is the injected-port variant (env-armed embedder pattern), never a base dependency (task t13)
