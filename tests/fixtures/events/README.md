# Event bus fixtures

These files are a **CONTRACT**, not test-only scaffolding. `embodiment/bus.py`
(plan task `t13`) and the realtime dashboard web app (plan task `t17`) both
validate against these exact files — `tests/test_bus.py` on this side, and the
web app's own test suite on the other. If either side's understanding of an
event's shape changes, it changes here first, and both suites re-run against
the new file. Do not edit these fixtures without checking what depends on
them.

- `schema.json` — the structural contract: the envelope's own required/optional
  fields, and each event `kind`'s required `data` fields. A hand-rolled, minimal
  schema representation (not full JSON-Schema draft syntax) — see its own
  `_comment` field for how to read it.
- `state.json`, `mic.json`, `turn.json`, `transcript.json`, `reply.json`,
  `degradation.json`, `features.json`, `clients.json`, `heartbeat.json` — one
  full, valid example envelope per event kind, each satisfying `schema.json`'s
  contract for that kind.

`transcript.json` and `reply.json` are the only two fixtures that carry speech
(the `text` field) — this mirrors `embodiment.bus.SPEECH_KINDS` and the
privacy boundary documented in `embodiment/bus.py`'s module docstring: every
other kind's fixture carries only status, counts, and degradation codes.
