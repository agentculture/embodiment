"""Per-role voice latency: what a role is known to cost before it may speak.

A role (optionally with a ``translate_via`` role, key ``"worker+senses"``) may not
answer a spoken turn until its latency has been **measured** on this rig. This module
keeps the samples (0600, atomic, bounded: the discipline of
:mod:`embodiment.daemon.reading`), derives the p90 and the spoken deadline from them,
and runs a small fixed probe. It holds **no gateway client**: the completion is an
injected callable ``complete(prompt) -> Optional[float]``; the probe times the call
with an injected clock and, when the callable returns a number, keeps it as the
first-token latency. Pure logic over one file. Never raises.

States: ``unmeasured`` (fewer than :data:`MEASURED_MIN_SAMPLES` samples; also what a
failed probe leaves), ``probing`` (a probe is running, in memory only), ``measured``.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

STORE_NAME = "voice-latency.json"

UNMEASURED = "unmeasured"
PROBING = "probing"
MEASURED = "measured"

#: Samples that make a role "measured". With the nearest-rank p90, ten samples is the
#: smallest n where the p90 (the 9th) is a different number from the max; below it a
#: "p90" is just the slowest run seen. A judgement call, not a statistical guarantee:
#: ten is a floor on evidence, and live turns keep adding samples after it.
MEASURED_MIN_SAMPLES = 10

#: The fixed probe: short, spoken-style Hebrew turns (what a voice reply answers), so
#: the sample is a reply-sized completion, not a long generation.
PROBE_PROMPTS = (
    "שלום, מה שלומך היום?",
    "מה השעה עכשיו בערך?",
    "תזכירי לי מה אמרתי קודם.",
    "ספרי לי משהו קצר ומעניין.",
    "תודה, זה הכל לעכשיו.",
)
#: Rounds over :data:`PROBE_PROMPTS`; 5 prompts x 2 rounds = :data:`MEASURED_MIN_SAMPLES`,
#: so one clean probe is exactly enough and a single failed call leaves it unmeasured.
PROBE_ROUNDS = 2

#: Samples kept per key; the most recent win, so a drifting rig is followed.
MAX_SAMPLES = 50

#: Derived spoken deadline = p90 x DEADLINE_HEADROOM + DEADLINE_SLACK_S, clamped to
#: [DEADLINE_FLOOR_S, DEADLINE_CAP_S]. The reason (lesson 1: a clock sized against the
#: wrong quantity becomes the measurement): a deadline AT the p90 cuts ~10% of turns by
#: construction and would censor the tail it is meant to show, so it gets 2x headroom
#: for the tail plus a second for gateway jitter; the cap keeps a very slow role from
#: holding a spoken turn silent for long. Judgement constants, not measured. Never
#: senses' 60 s (that bound sizes a different quantity: a text completion).
DEADLINE_HEADROOM = 2.0
DEADLINE_SLACK_S = 1.0
DEADLINE_FLOOR_S = 2.0
DEADLINE_CAP_S = 20.0

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def combo_key(role: Any, translate_via: Any = None) -> Optional[str]:
    """``role`` or ``role+translate_via``; None when a name is not a safe role name."""
    if not isinstance(role, str) or not _NAME.match(role):
        return None
    if translate_via in (None, ""):
        return role
    if not isinstance(translate_via, str) or not _NAME.match(translate_via):
        return None
    return f"{role}+{translate_via}"


def percentile90(values: list[float]) -> Optional[float]:
    """Nearest-rank p90 (ceil(0.9 n)-th smallest); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.9 * len(ordered))) - 1]


def derive_spoken_deadline(p90_s: Any) -> Optional[float]:
    """The bound on ONE spoken completion, from the role's measured p90.

    ``p90 x`` :data:`DEADLINE_HEADROOM` ``+`` :data:`DEADLINE_SLACK_S`, clamped to
    [:data:`DEADLINE_FLOOR_S`, :data:`DEADLINE_CAP_S`]. An UNKNOWN p90 (None, not a
    number, negative, non-finite) gives None, not a default: no latency has been
    observed, and a guessed bound would be the measurement (the caller refuses the
    role instead). Pure; never raises.
    """
    if isinstance(p90_s, bool) or not isinstance(p90_s, (int, float)):
        return None
    if not math.isfinite(p90_s) or p90_s < 0:
        return None
    raw = float(p90_s) * DEADLINE_HEADROOM + DEADLINE_SLACK_S
    return min(DEADLINE_CAP_S, max(DEADLINE_FLOOR_S, raw))


def _num(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


class VoiceLatency:
    """Samples per role/combination, kept across restarts. Never raises."""

    def __init__(
        self,
        path: Any,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._data: Optional[dict[str, dict[str, Any]]] = None
        self._probing: set[str] = set()
        self.save_failures = 0
        self.unreadable = 0

    # -- storage -----------------------------------------------------------------

    @staticmethod
    def _clean(rec: Any) -> Optional[dict[str, Any]]:
        if not isinstance(rec, dict) or not isinstance(rec.get("samples"), list):
            return None
        samples = [
            s for s in rec["samples"] if isinstance(s, dict) and _num(s.get("completion_s"))
        ][-MAX_SAMPLES:]
        failures = rec.get("failures")
        return {
            "samples": samples,
            "failures": failures if isinstance(failures, int) and failures >= 0 else 0,
            "last_failure": (
                rec["last_failure"] if isinstance(rec.get("last_failure"), str) else None
            ),
        }

    def _read_raw(self) -> Any:
        """The file's parsed JSON; ``{}`` when absent, or unreadable (set aside, counted)."""
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError):
            return {}
        except (OSError, ValueError):
            self.unreadable += 1
            try:
                os.replace(self._path, self._path.with_name(f"{self._path.name}.unreadable"))
            except OSError:
                self.save_failures += 1
            return {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            raw = self._read_raw()
            self._data = {}
            for key, rec in raw.items() if isinstance(raw, dict) else ():
                cleaned = self._clean(rec)
                if cleaned is not None and combo_key(*key.split("+", 1)) == key:
                    self._data[key] = cleaned
        return self._data

    def _save(self, data: dict[str, dict[str, Any]]) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".voice-latency-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            self.save_failures += 1
            return False
        return True

    def _rec(self, data: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
        return data.setdefault(key, {"samples": [], "failures": 0, "last_failure": None})

    # -- reads -------------------------------------------------------------------

    def _state(self, key: str) -> str:
        if key in self._probing:
            return PROBING
        rec = self._load().get(key)
        if rec and len(rec["samples"]) >= MEASURED_MIN_SAMPLES:
            return MEASURED
        return UNMEASURED

    def state(self, role: Any, translate_via: Any = None) -> str:
        key = combo_key(role, translate_via)
        if key is None:
            return UNMEASURED
        with self._lock:
            return self._state(key)

    def p90(self, role: Any, translate_via: Any = None) -> Optional[float]:
        """The p90 completion latency, or None below :data:`MEASURED_MIN_SAMPLES`."""
        key = combo_key(role, translate_via)
        if key is None:
            return None
        with self._lock:
            rec = self._load().get(key)
            if not rec or len(rec["samples"]) < MEASURED_MIN_SAMPLES:
                return None
            return percentile90([s["completion_s"] for s in rec["samples"]])

    def spoken_deadline(self, role: Any, translate_via: Any = None) -> Optional[float]:
        """The derived bound for one spoken completion; None while not measured."""
        return derive_spoken_deadline(self.p90(role, translate_via))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            out = {}
            for key, rec in self._load().items():
                n = len(rec["samples"])
                p90 = (
                    percentile90([s["completion_s"] for s in rec["samples"]])
                    if n >= MEASURED_MIN_SAMPLES
                    else None
                )
                out[key] = {
                    "state": self._state(key),
                    "n": n,
                    "p90_s": p90,
                    "deadline_s": derive_spoken_deadline(p90),
                    "failures": rec["failures"],
                    "last_failure": rec["last_failure"],
                }
            return out

    # -- writes ------------------------------------------------------------------

    def add_sample(
        self,
        role: Any,
        completion_s: Any,
        translate_via: Any = None,
        first_token_s: Any = None,
        censored: bool = False,
    ) -> bool:
        """Record one latency (a probe call or a live spoken turn); False if not kept.

        *censored*: the call was cut off at *completion_s* (its deadline), so the true
        latency was at least that; kept, so a slow rig raises the p90 (lesson 1)."""
        key = combo_key(role, translate_via)
        if key is None or not _num(completion_s):
            return False
        sample: dict[str, Any] = {"completion_s": float(completion_s), "at": self._wall()}
        if _num(first_token_s):
            sample["first_token_s"] = float(first_token_s)
        if censored:
            sample["censored"] = True
        with self._lock:
            data = self._load()
            rec = self._rec(data, key)
            rec["samples"] = (rec["samples"] + [sample])[-MAX_SAMPLES:]
            return self._save(data)

    def _fail(self, key: str, exc: BaseException) -> None:
        """Count a failed call; only the exception CLASS is kept, never its message."""
        with self._lock:
            data = self._load()
            rec = self._rec(data, key)
            rec["failures"] += 1
            rec["last_failure"] = type(exc).__name__
            self._save(data)

    def probe(
        self,
        role: Any,
        complete: Callable[[str], Any],
        translate_via: Any = None,
    ) -> dict[str, Any]:
        """Run the fixed prompts through *complete* and record each latency.

        ``complete(prompt)`` is the caller's (it knows the role and the gateway). The
        probe times it; a number it returns is kept as the first-token latency. A call
        that raises is a failure (counted, class name only), never a sample, and the
        probe goes on. Returns ``{"state", "samples", "failures"}``; never raises, and
        a probe that cannot reach :data:`MEASURED_MIN_SAMPLES` leaves ``unmeasured``.
        """
        key = combo_key(role, translate_via)
        if key is None:
            return {"state": UNMEASURED, "samples": 0, "failures": 0}
        with self._lock:
            if key in self._probing:
                return {"state": PROBING, "samples": 0, "failures": 0}
            self._probing.add(key)
        good = failed = 0
        try:
            for _ in range(PROBE_ROUNDS):
                for prompt in PROBE_PROMPTS:
                    try:
                        started = self._clock()
                        first = complete(prompt)
                        elapsed = self._clock() - started
                    except Exception as exc:  # noqa: BLE001 - a probe never raises
                        failed += 1
                        self._fail(key, exc)
                        continue
                    if self.add_sample(role, elapsed, translate_via, first):
                        good += 1
                    else:
                        failed += 1
        finally:
            with self._lock:
                self._probing.discard(key)
        return {"state": self.state(role, translate_via), "samples": good, "failures": failed}
