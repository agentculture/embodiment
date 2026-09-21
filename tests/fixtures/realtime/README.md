# Realtime event fixtures — where every field came from

These are **transcriptions**, not captures. Each file is a server→client event
shaped exactly as `lobes-cli` mints it, built by reading the sibling repo's
schema and docs — never by importing `lobes` (forbidden: see
`tests/test_zero_deps.py`'s `_FORBIDDEN_DISTRIBUTIONS`) and never by copying a
live session log (a real log carries real speech).

Sibling repo root: `/home/spark/git/lobes-cli`, read at commit-time of task
`t6`. Line numbers are that tree's.

## The envelope

Every event carries `session_id`, `event_id`, `timestamp_ms` and `type`.
`type` is last in each file because `lobes/realtime/_session.py:627`
(`event_to_dict`) writes `out["type"] = event.type` **after** the field loop,
and `json.dumps` preserves insertion order. Ids follow
`lobes/realtime/protocol.py:42-59` (`event_<24 hex>`, `item_<24 hex>`,
`sess_<24 hex>`, `resp_<24 hex>`), with the hex digits replaced by repeated
letters so a fixture is never mistaken for a captured session.

`timestamp_ms` is a monotonic process clock
(`lobes/realtime/protocol.py:62`); `at_ms` is 32 ms-quantised **audio-stream**
time and a different clock — `lobes/realtime/_session.py:381-400` and
`docs/realtime-pipeline.md` §"Boundary events now carry `at_ms` and `reason`".
The `at_ms` values here are multiples of 32 for that reason.

| File | Source |
|---|---|
| `session_created.json` | `lobes/realtime/_session.py:342-349` (`SessionCreatedEvent`) with the nested `config` from `_config_to_dict`, `lobes/realtime/_session.py:597-625`. The six unconditional keys are in that function's literal order; `language` appears only because it differs from `DEFAULT_LANGUAGE` (`_session.py:173`, `"en"`), which is exactly the condition at `_session.py:622-623`. `tools`/`tool_choice` are absent — this client declares none. Values `pcm16` / `24000` / `1` / `server_vad` / `aec` are the accepted set in `docs/realtime-pipeline.md` §"Connect URL and session config". |
| `session_updated.json` | `lobes/realtime/_session.py:351-368` (`SessionUpdatedEvent`). The `session` body holds only what took effect — `apply_session_update`, `_session.py:894-936` — and `language` is one of the three `SUPPORTED_SESSION_UPDATE_FIELDS` (`_session.py:192`). |
| `session_closed.json` | `lobes/realtime/_session.py:371-377` (`SessionClosedEvent`). |
| `speech_started.json` | `lobes/realtime/_session.py:380-400` (`SpeechStartedEvent`). |
| `speech_stopped_silence.json` | `lobes/realtime/_session.py:402-422` (`SpeechStoppedEvent`), `reason="silence"` — the `VAD_SILENCE_MS`-confirmed stop, `docs/realtime-pipeline.md` §"Event flow" step 3. |
| `speech_stopped_max_turn.json` | Same class, `reason="max_turn"` — the force-commit, `docs/realtime-pipeline.md` §"Max-turn cap: force-commit, not an error". `VAD_MAX_TURN_MS` defaults to 30000 ms, hence `at_ms: 30016` (the first 32 ms boundary at or past the cap). |
| `transcription_completed.json` | `lobes/realtime/_session.py:424-431` (`TranscriptionCompletedEvent`). The text is a two-word Hebrew greeting written for this fixture, chosen because the rig's `stt` lane advertises `"language": "he"`. |
| `error_*.json` (seven) | `lobes/realtime/_session.py:434-442` (`ErrorEvent`); one file per member of `ErrorCode`, `lobes/realtime/_session.py:262-303`. The `message` strings are paraphrases of that enum's own documentation — the wire contract is the **code**, and `_session.py:269-282` is explicit that the free-text message is where a sub-reason is named, so no test asserts on message text. |
| `unknown_response_created.json` | `lobes/realtime/_session.py:445-458` (`ResponseCreatedEvent`). Present precisely so a *known-to-lobes but unconsumed-here* event is proved to decode as `UnknownEvent`. An ears-only session never receives it (`docs/realtime-pipeline.md` §"Conversation is opt-in"), so if one ever arrives it is a server this client does not fully know — which must degrade, never raise. |

## What is deliberately absent

No `response.audio.delta`, `response.text.done`, `response.done` or
`response.interrupted` fixture. Those belong to the conversation surface this
client structurally cannot arm (`tests/test_realtime_wire.py::TestEarsOnly`),
and a fixture for one would be a codec this module has no business owning.
