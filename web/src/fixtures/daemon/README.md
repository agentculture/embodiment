# daemon fixtures (round 5)

These files copy the shape `embodiment.daemon.app.DaemonApp.status()` and
`embodiment.daemon.app.DaemonApp._publish_mic()` actually produce — read
directly from `/home/spark/git/.worktrees.embodiment/realtime-t15/embodiment/daemon/app.py`
(read-only reference; that worktree is task t15's, not this one's to edit)
and cross-checked against that file's own test assertions in
`tests/test_daemon_app.py` (e.g. `status()["ear"]["active"] == "host"`,
`status()["recall"]["mode"] == "lexical"`).

**Provenance note — read before trusting these as a literal capture.** A
real daemon process (`embodiment.daemon.lifecycle`) was running on this
machine while this task was worked (bound to a Tailscale address, with its
own generated install secret on disk). The task-agent preamble this repo's
task agents work under forbids opening any credential file to authenticate
a request — the daemon's install-secret file is exactly that — so these
fixtures were **not** produced with `curl` against that live process, even
though one was reachable. They are reconstructed byte-for-byte from
reading `embodiment/daemon/app.py`'s `_status()`/`_publish_mic()` source
and the field values its own test suite asserts, not a literal capture.
If a genuinely curl-captured fixture is wanted later, it needs an
operator-supplied secret from the environment (never read from the
daemon's own state directory by an agent) — see this repo's `CLAUDE.md`,
"Never open a credential file".

## Files

- `status-ear-attached.json` — `GET /api/status`'s body shape
  (`{"daemon": ..., "http": ...}`, `embodiment/http/server.py`'s
  `_status` handler) with an ear attached, unmuted, and a `lexical` recall
  mode already recorded.
- `status-ear-detached.json` — the same shape with no ear attached, no
  recall call made yet (`recall.mode: null`) — this is what a viewer who
  connects to a completely idle daemon sees.
- `mic-event-attached.json` / `mic-event-detached.json` — one `mic` bus
  event envelope each (`{v, kind, ts, seq, source, data}`), matching
  `_publish_mic`'s real `{hot, ear}` shape, including the `ear: null` case
  the committed `tests/fixtures/events/mic.json` (t13's own fixture,
  always attached) never exercises.
- `degradation-memory.json` — one `degradation` bus event envelope with
  `source: "memory"` — the daemon's REAL source for a recall/memory
  degradation (`self._fold("memory", degradation)`), not `"continuity"`
  (the committed `tests/fixtures/events/degradation.json`'s own,
  unrelated example value).
