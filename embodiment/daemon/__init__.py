"""embodiment.daemon — the background daemon's own subpackage.

Planned by the ``realtime-embodiment-app`` redesign (see the top-level
``CLAUDE.md``). Task t4 lands the first module, :mod:`embodiment.daemon.state`
— the state directory, the bounded operational log, and the crash-durable
degradation ledger. Later tasks in the same plan (``t5`` and beyond) add
lifecycle verbs (``start``/``stop``/``status``) alongside it.

This package is not wired into ``embodiment``'s own lazy public surface
(``embodiment/__init__.py``) yet — that wiring, and the CLI verbs that reach
it, belong to the tasks that build the rest of the daemon.
"""

from __future__ import annotations

from embodiment.daemon.state import (
    DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
    DEFAULT_TRANSCRIPT_LOG_MAX_BYTES,
    STATE_DIR_ENV_VAR,
    DaemonState,
    DegradationLedger,
    DegradationRecord,
    OperationalLog,
    TranscriptLog,
    candidate_state_dirs,
    resolve_fallback_state_dir,
    resolve_state_dir,
)

__all__ = [
    "STATE_DIR_ENV_VAR",
    "DEFAULT_OPERATIONAL_LOG_MAX_BYTES",
    "DEFAULT_TRANSCRIPT_LOG_MAX_BYTES",
    "resolve_state_dir",
    "resolve_fallback_state_dir",
    "candidate_state_dirs",
    "DaemonState",
    "DegradationRecord",
    "DegradationLedger",
    "OperationalLog",
    "TranscriptLog",
]
