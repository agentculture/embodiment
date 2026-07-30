"""Tool-session latency against the drive tail (plan task t16, lapse ``l1``).

The pre-registration is
``docs/live-test-results/muse-latency-preregistration.md`` and the thresholds
are pinned in ``tests/test_muse_latency_preregistration.py``. Read the first
before reading this; this module executes that contract and decides nothing on
its own.

The question, in one sentence: claim ``c29`` says the close-time late drop goes
to **zero per run** across a live series, and that target was fixed from
*tools-off* latency baselines — so once the muse holds tools, is zero still
reachable, or is it unreachable by design?

Under deviation ``d1`` the actor never waits on the muse. A session that takes
longer than the drive's remaining tail simply does not land, and the terminal
drain recovers nothing. So the answer is a race between two measurable things:

* **lane 1** — how long a muse session takes to produce its FIRST insight, with
  the pad wired and without it (``--lane sessions``);
* **lane 2** — how much tail a real drive leaves the last session it started
  (``--lane drives``).

``--analyse`` folds the two into completion odds and applies the pre-registered
decision rule.

One lane is **absent, by construction, and is reported as absent rather than
implied**: there is no tools-on-in-drive measurement here, because
``ThreadedMuseRunner.__init__`` builds its ``MuseLoop`` with no ``tools=``
argument — its own docstring calls the injected seam "the *tools-off* thinking
seam". No supported path puts a tool bench inside a live drive, and task t16 may
not change ``embodiment/``. The completion odds below are therefore an
**estimate combining two measured distributions**, under the assumption the
pre-registration states and this module repeats in its own output: the tail is
actor-determined, so a tools-off drive measures the tail a tools-on drive would
have had.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/muse_latency.py --lane sessions \\
        --out docs/live-test-results/muse-latency-sessions.jsonl
    uv run python examples/muse_latency.py --lane drives \\
        --out docs/live-test-results/muse-latency-drives.jsonl
    uv run python examples/muse_latency.py --analyse \\
        --sessions docs/live-test-results/muse-latency-sessions.jsonl \\
        --drives docs/live-test-results/muse-latency-drives.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    MuseControls,
    PresenceEngine,
    PresenceIO,
    Task,
    ThreadedMuseRunner,
    frame_muse,
    run,
)
from embodiment.contract import ModelResponse, ToolCall  # noqa: E402
from embodiment.muse import DEFAULT_STALE_LAG, MuseLoop  # noqa: E402
from embodiment.muse_pad import MUSE_PAD_PROTOCOL, MusePad  # noqa: E402
from embodiment.presence_engine import BOUNDARY_CADENCE_TICK, BoundaryContext  # noqa: E402
from examples.proof import (  # noqa: E402
    MUSE_MAX_TURNS,
    PROBLEM,
    TOOLS,
    GuidanceRelay,
    ProofBench,
    gateway,
)

# ── the pre-registered constants (copies; the pin test asserts equality) ──────

ARM_OFF_2 = "off-2"
ARM_OFF_4 = "off-4"
ARM_PAD_4 = "pad-4"
ARM_PRIMED_4 = "primed-4"
ARMS = (ARM_OFF_2, ARM_OFF_4, ARM_PAD_4, ARM_PRIMED_4)

GOVERNING_ARM_PRIMARY = ARM_PAD_4
GOVERNING_ARM_FALLBACK = ARM_PRIMED_4
TOOLS_ON_ARMS = (ARM_PAD_4, ARM_PRIMED_4)

PRIMING = (
    "Before you comment, record your thinking on the pad: write an intend entry "
    "for what you are about to consider, and an observe or conclude entry for "
    "what you make of it. Use the tools; do not describe using them."
)

MUSE_MAX_TURNS_BASELINE = 2
MUSE_MAX_TURNS_DEFAULT = 4
MUSE_MAX_TOOL_ROUNDS = 3
MUSE_MAX_TOKENS = 1200
TEMPERATURE = 0.3

ARM_CONFIG = {
    ARM_OFF_2: (False, "none", MUSE_MAX_TURNS_BASELINE),
    ARM_OFF_4: (False, "none", MUSE_MAX_TURNS_DEFAULT),
    ARM_PAD_4: (True, "pad", MUSE_MAX_TURNS_DEFAULT),
    ARM_PRIMED_4: (True, "pad+priming", MUSE_MAX_TURNS_DEFAULT),
}

MIN_DRIVES = 4
MIN_SESSIONS_PER_ARM = 6
N_DRIVES = 4
N_SEQUENCES = 2
BOUNDARY_STEPS = (3, 7, 11, 15)
N_SESSIONS_PER_ARM = N_SEQUENCES * len(BOUNDARY_STEPS)
LATE_BOUNDARY_STEPS = BOUNDARY_STEPS[-2:]

SERIES_CONFIDENCE = 0.8
KEEP_THRESHOLD = 0.95
MAX_ERROR_FRACTION = 1 / 3
MIN_TOOL_EXERCISE_FRACTION = 0.5

DECISIONS = ("KEEP", "REPLACE", "INCONCLUSIVE")

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"

#: The baseline this probe exists to test the reachability of.
BASELINE_LATE_DROPS_PER_RUN = 1.0


# ── the fixed transcript lane 1's boundaries are cut from ─────────────────────

#: One committed transcript, so the same boundary text reaches every cell. It is
#: the ``proof.py`` factorial task's own shape — compute, conjecture, prove —
#: because that is the workload every number in this repo's muse series was
#: measured on, and a new workload would make the comparison a new experiment.
TRANSCRIPT: tuple[dict[str, str], ...] = (
    {"role": "user", "content": PROBLEM[:400]},
    {"role": "assistant", "content": "Phase 1. I will compute S(n) for small n with sum_terms."},
    {"role": "tool", "content": "S(1) = 1"},
    {"role": "tool", "content": "S(2) = 5"},
    {"role": "assistant", "content": "Two points is not evidence. Extending to n=3 and n=4."},
    {"role": "tool", "content": "S(3) = 23"},
    {"role": "tool", "content": "S(4) = 119"},
    {"role": "assistant", "content": "Looking at nearby factorials: 2!, 3!, 4!, 5!."},
    {"role": "tool", "content": "4! = 24"},
    {"role": "tool", "content": "5! = 120"},
    {
        "role": "assistant",
        "content": "Phase 2. Conjecture: S(n) = (n+1)! - 1. Testing with compare.",
    },
    {"role": "tool", "content": "your value 719 for n=5: MATCHES"},
    {"role": "tool", "content": "your value 5039 for n=6: MATCHES"},
    {
        "role": "assistant",
        "content": "Two independent confirmations. One more at n=7 before proving.",
    },
    {"role": "tool", "content": "your value 40319 for n=7: MATCHES"},
    {"role": "assistant", "content": "Phase 3. Proving by induction on n, base case n=1."},
)

#: What the actor's own state line said at each measured boundary step.
TASK_STATE = {
    3: "3 tool call(s); last: sum_terms",
    7: "7 tool call(s); last: factorial",
    11: "11 tool call(s); last: compare",
    15: "15 tool call(s); last: compare",
}

#: The feed tail at each measured boundary step — the actor's last output.
FEED_TAIL = {
    3: "S(2) = 5",
    7: "S(4) = 119",
    11: "your value 719 for n=5: MATCHES",
    15: "your value 40319 for n=7: MATCHES",
}


def boundary_at(step: int) -> BoundaryContext:
    """The boundary the pump would have built at *step*, from the fixed transcript.

    ``history`` grows with the step exactly as the pump's own ``_history()``
    would, which is the context-growth axis lane 1 measures along.
    """
    return BoundaryContext(
        kind=BOUNDARY_CADENCE_TICK,
        step_count=step,
        reason="step cadence",
        phase_changed=step in (7, 15),
        task_state=TASK_STATE[step],
        feed_tail=FEED_TAIL[step],
        flight="proof-factorial",
        history=[dict(entry) for entry in TRANSCRIPT[: min(step + 1, len(TRANSCRIPT))]],
    )


# ── the timed seam ────────────────────────────────────────────────────────────


class TimedMuseSeam:
    """One muse endpoint, timestamped. Thread-safe; usable from the muse thread.

    Holds both shapes the muse needs: :data:`~embodiment.muse.MuseCompleteFn`
    (``__call__``) and :data:`~embodiment.muse.MuseToolCompleteFn`
    (:meth:`with_tools`). Every call appends one record — start, end, how many
    messages went out, whether tools were on the wire, how many calls came back,
    the completion-token count and the finish reason — so a session's shape can
    be reconstructed afterwards without instrumenting the package.

    A session's OPENING call is the one carrying exactly two messages:
    ``embodiment.muse._build_messages`` returns the authority system message and
    the rendered boundary, and nothing else, until the loop appends turns. That
    is the only signal needed to group calls into sessions from outside.
    """

    OPENING_MESSAGES = 2

    def __init__(self, base_url: str, model: str, key: str, *, temperature: float) -> None:
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        if not endpoint.startswith(("http://", "https://")):
            raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")
        self._endpoint = endpoint
        self._model = model
        self._key = key
        self._temperature = temperature
        self._lock = threading.RLock()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        return self._dial(messages, None)

    def with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        return self._dial(messages, tools)

    def _dial(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": MUSE_MAX_TOKENS,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools
        record: dict[str, Any] = {
            "started": time.monotonic(),
            "messages": len(messages),
            "opening": len(messages) <= self.OPENING_MESSAGES,
            "tools_on_wire": bool(tools),
        }
        try:
            request = urllib.request.Request(
                self._endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._key}",
                },
            )
            with urllib.request.urlopen(request, timeout=600) as response:  # nosec B310
                payload = json.load(response)
        except Exception as exc:  # a failed call is data — recorded, never retried
            record["ended"] = time.monotonic()
            record["error"] = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.calls.append(record)
            raise
        choice = payload["choices"][0]
        message = choice["message"]
        calls = [
            ToolCall(
                id=call.get("id", ""),
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in (message.get("tool_calls") or [])
        ]
        record["ended"] = time.monotonic()
        record["finish_reason"] = choice.get("finish_reason")
        record["tool_calls_returned"] = len(calls)
        record["completion_tokens"] = (payload.get("usage") or {}).get("completion_tokens")
        record["content_chars"] = len(message.get("content") or "")
        with self._lock:
            self.calls.append(record)
        return ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
            tool_calls=calls,
        )

    def sessions(self) -> list[dict[str, Any]]:
        """Group the recorded calls into sessions, oldest first."""
        with self._lock:
            calls = [dict(call) for call in self.calls]
        grouped: list[dict[str, Any]] = []
        for call in calls:
            if call.get("opening") or not grouped:
                grouped.append({"started": call["started"], "calls": []})
            grouped[-1]["calls"].append(call)
            grouped[-1]["ended"] = call.get("ended")
        return grouped


# ── lane 1: the session-latency distribution ──────────────────────────────────


def _controls(arm: str) -> MuseControls:
    _tools_on, _framing, max_turns = ARM_CONFIG[arm]
    return MuseControls(max_turns=max_turns, max_tool_rounds=MUSE_MAX_TOOL_ROUNDS)


def _framing(arm: str) -> Optional[str]:
    _tools_on, framing, _max_turns = ARM_CONFIG[arm]
    if framing == "none":
        return None
    if framing == "pad":
        return MUSE_PAD_PROTOCOL
    return f"{MUSE_PAD_PROTOCOL}\n\n{PRIMING}"


def run_session(
    arm: str, seam: TimedMuseSeam, pad: Optional[MusePad], step: int, sequence: int
) -> dict[str, Any]:
    """One measured session. Never raises: a failure is recorded as a row."""
    tools_on, framing_key, max_turns = ARM_CONFIG[arm]
    first_insight: list[float] = []

    def sink(_insight: Any) -> None:
        if not first_insight:
            first_insight.append(time.monotonic())

    loop = MuseLoop(
        seam,
        controls=_controls(arm),
        system=_framing(arm),
        sink=sink,
        tools=pad.bench(seam.with_tools) if (tools_on and pad is not None) else None,
    )
    before = len(seam.calls)
    started = time.monotonic()
    outcome = loop.think(boundary_at(step))
    ended = time.monotonic()
    calls = [dict(call) for call in seam.calls[before:]]
    tokens = sum(int(call.get("completion_tokens") or 0) for call in calls)
    row = {
        "lane": "sessions",
        "arm": arm,
        "tools_on": tools_on,
        "framing": framing_key,
        "max_turns": max_turns,
        "max_tool_rounds": MUSE_MAX_TOOL_ROUNDS,
        "sequence": sequence,
        "boundary_step": step,
        "wall_seconds": round(ended - started, 3),
        "ttfi_seconds": (round(first_insight[0] - started, 3) if first_insight else None),
        "turns": outcome.turns,
        "tool_rounds": outcome.tool_rounds,
        "insights": len(outcome.insights),
        "exit_reason": outcome.exit_reason,
        "completion_tokens": tokens or None,
        "model_calls": len(calls),
        "call_seconds": [round(c["ended"] - c["started"], 3) for c in calls if c.get("ended")],
        "call_errors": [c["error"] for c in calls if c.get("error")],
        "finish_reasons": [c.get("finish_reason") for c in calls],
        "tool_calls_returned": sum(int(c.get("tool_calls_returned") or 0) for c in calls),
        "degradations": [record.code for record in outcome.degradations],
        "degraded": outcome.degraded,
        "prompt_messages": [c.get("messages") for c in calls],
    }
    if pad is not None:
        row["pad"] = pad.counts().to_dict()
    return row


#: The pad filename this probe uses per cell. ``MusePad.in_directory`` would put
#: every cell's pad in one file; each cell needs its own.
MUSE_PAD_FILE = "muse-pad.jsonl"


def lane_sessions(args: argparse.Namespace, key: str) -> list[dict[str, Any]]:
    """Every cell of lane 1, in a fixed order, one row per session."""
    rows: list[dict[str, Any]] = []
    workdir = Path(args.workdir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    for arm in ARMS:
        tools_on, _framing_key, _max_turns = ARM_CONFIG[arm]
        for sequence in range(N_SEQUENCES):
            seam = TimedMuseSeam(
                args.base_url, args.muse_model, key, temperature=args.muse_temperature
            )
            pad = MusePad(workdir / f"{arm}-seq{sequence}-{MUSE_PAD_FILE}") if tools_on else None
            for step in BOUNDARY_STEPS:
                row = run_session(arm, seam, pad, step, sequence)
                rows.append(row)
                print(
                    f"  {arm} seq{sequence} step{step:>2}  "
                    f"wall={row['wall_seconds']:>6.1f}s  "
                    f"ttfi={row['ttfi_seconds']}  "
                    f"turns={row['turns']} rounds={row['tool_rounds']} "
                    f"exit={row['exit_reason']}",
                    file=sys.stderr,
                    flush=True,
                )
    return rows


# ── lane 2: the drive tail ────────────────────────────────────────────────────


class TimedPresence:
    """A forwarding proxy that timestamps the two beats the tail is measured between.

    Public seams only. :func:`embodiment.loop.run` duck-types its presence sink
    (``_presence_terminal`` probes ``on_terminal_boundary`` by name), so a proxy
    that forwards every call is indistinguishable from the engine itself — and
    no package change is needed to learn when the terminal beat fired.
    """

    def __init__(self, engine: PresenceEngine) -> None:
        self._engine = engine
        self.progress_at: list[float] = []
        self.terminal_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self._engine.active

    def acknowledge(self, packet: Any) -> Any:
        return self._engine.acknowledge(packet)

    def on_operator_message(self, text: str) -> Any:
        return self._engine.on_operator_message(text)

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False) -> Any:
        self.progress_at.append(time.monotonic())
        return self._engine.on_progress_boundary(step_count=step_count, phase_changed=phase_changed)

    def on_terminal_boundary(self, *, step_count: int = 0) -> Any:
        self.terminal_at = time.monotonic()
        return self._engine.on_terminal_boundary(step_count=step_count)


def run_drive(args: argparse.Namespace, key: str, index: int) -> dict[str, Any]:
    """One live ``proof.py``-shaped drive, instrumented for its tail."""
    bench = ProofBench()
    lines: list[str] = []
    seam = TimedMuseSeam(args.base_url, args.muse_model, key, temperature=args.muse_temperature)
    runner = ThreadedMuseRunner(
        seam,
        system=frame_muse(None, identity=args.identity),
        controls=MuseControls(max_turns=MUSE_MAX_TURNS),
    )
    relay = GuidanceRelay(
        gateway(
            args.base_url,
            args.cortex_model,
            key,
            tools=TOOLS,
            temperature=args.cortex_temperature,
        )
    )
    presence = TimedPresence(
        PresenceEngine(
            io=PresenceIO(
                render=lines.append,
                task_state=bench.state,
                append_guidance=relay.append_guidance,
            ),
            muse=runner,
            speaker=args.identity or "presence",
        )
    )
    task = Task(id=f"proof-factorial-{index}", repo_path="", instruction=PROBLEM)
    started = time.monotonic()
    error: Optional[str] = None
    outcome = None
    try:
        with runner:
            outcome = run(
                relay.complete,
                task,
                executor=bench,
                max_steps=args.max_steps,
                presence=presence,
                model=args.cortex_model,
            )
    except Exception as exc:  # an aborted drive is data
        error = f"{type(exc).__name__}: {exc}"
    ended = time.monotonic()
    state = runner.snapshot()
    sessions = seam.sessions()
    terminal_at = presence.terminal_at
    before_terminal = [
        s for s in sessions if terminal_at is not None and s["started"] <= terminal_at
    ] or []
    last = before_terminal[-1] if before_terminal else None
    return {
        "lane": "drives",
        "drive_index": index,
        "error": error,
        "elapsed_seconds": round(ended - started, 3),
        "exit_reason": getattr(outcome, "exit_reason", None),
        "status": getattr(getattr(outcome, "result", None), "status", None),
        "model_turns": getattr(
            getattr(getattr(outcome, "result", None), "stats", None), "model_turns", None
        ),
        "tool_call_count": len(getattr(getattr(outcome, "result", None), "steps", []) or []),
        "max_steps": args.max_steps,
        "stale_lag": DEFAULT_STALE_LAG,
        "muse_max_turns": MUSE_MAX_TURNS,
        "progress_boundaries": len(presence.progress_at),
        "muse_sessions": len(sessions),
        "muse_sessions_before_terminal": len(before_terminal),
        "terminal_beat_fired": terminal_at is not None,
        # THE measurement: the window the last session actually had.
        "tail_seconds": (
            round(terminal_at - last["started"], 3)
            if (terminal_at is not None and last is not None)
            else None
        ),
        "last_session_seconds": (
            round(last["ended"] - last["started"], 3)
            if (last is not None and last.get("ended"))
            else None
        ),
        "last_session_completed_in_tail": (
            bool(
                last.get("ended") is not None
                and terminal_at is not None
                and last["ended"] <= terminal_at
            )
            if last is not None
            else None
        ),
        "session_seconds": [
            round(s["ended"] - s["started"], 3) for s in sessions if s.get("ended")
        ],
        "session_starts_before_terminal": (
            [round(terminal_at - s["started"], 3) for s in before_terminal]
            if terminal_at is not None
            else []
        ),
        "muse_counts": state.get("counts"),
        # ``snapshot()["deliveries"]`` is already plain dicts; ``degradations`` is not.
        "muse_deliveries": list(state.get("deliveries", []) or []),
        "muse_degradation_codes": _fold_codes(state),
        "guidance_appended": len(relay.appended),
        "guidance_reached_cortex": len(relay.reached_cortex),
        "guidance_undelivered": len(relay.pending),
        "presence_lines": len(lines),
    }


def _fold_codes(state: dict[str, Any]) -> dict[str, int]:
    """Tally the runner's ledger by code. An absent code reports nothing, not zero."""
    tally: dict[str, int] = {}
    for record in state.get("degradations", []) or []:
        code = getattr(record, "code", "")
        tally[code] = tally.get(code, 0) + 1
    return tally


def lane_drives(args: argparse.Namespace, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(args.n_drives):
        row = run_drive(args, key, index)
        rows.append(row)
        print(
            f"  drive {index}  exit={row['exit_reason']} "
            f"elapsed={row['elapsed_seconds']:.0f}s "
            f"sessions={row['muse_sessions']} tail={row['tail_seconds']}s "
            f"late={(row['muse_counts'] or {}).get('insights_dropped_late')}",
            file=sys.stderr,
            flush=True,
        )
    return rows


# ── the fold ──────────────────────────────────────────────────────────────────


def _quantiles(values: list[float]) -> dict[str, Any]:
    """Min / p25 / median / p75 / max / mean, or nulls when there is nothing."""
    if not values:
        return {
            "n": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
            "mean": None,
        }
    ordered = sorted(values)

    def _at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return ordered[index]

    return {
        "n": len(ordered),
        "min": round(ordered[0], 2),
        "p25": round(_at(0.25), 2),
        "median": round(statistics.median(ordered), 2),
        "p75": round(_at(0.75), 2),
        "max": round(ordered[-1], 2),
        "mean": round(statistics.fmean(ordered), 2),
    }


def _odds(latencies: list[float], tails: list[float]) -> Optional[float]:
    """The pre-registered estimator: the fraction of (session, tail) pairs that land."""
    if not latencies or not tails:
        return None
    landed = sum(1 for value in latencies for tail in tails if value <= tail)
    return round(landed / (len(latencies) * len(tails)), 4)


def _ceil_2(value: float) -> float:
    return math.ceil(value * 100) / 100


def analyse(sessions: list[dict[str, Any]], drives: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold both lanes and apply the pre-registered decision rule."""
    tails = [row["tail_seconds"] for row in drives if row.get("tail_seconds") is not None]
    usable_drives = [
        row
        for row in drives
        if row.get("tail_seconds") is not None and row.get("terminal_beat_fired")
    ]

    arms: dict[str, Any] = {}
    for arm in ARMS:
        rows = [row for row in sessions if row["arm"] == arm]
        completed = [row for row in rows if row.get("ttfi_seconds") is not None]
        late_rows = [row for row in completed if row["boundary_step"] in LATE_BOUNDARY_STEPS]
        ttfi = [row["ttfi_seconds"] for row in completed]
        ttfi_late = [row["ttfi_seconds"] for row in late_rows]
        wall = [row["wall_seconds"] for row in rows]
        exercised = [row for row in rows if row.get("tool_rounds", 0) >= 1]
        degraded = [row for row in rows if row.get("degraded")]
        by_step = {
            str(step): _quantiles(
                [row["ttfi_seconds"] for row in completed if row["boundary_step"] == step]
            )
            for step in BOUNDARY_STEPS
        }
        arms[arm] = {
            "sessions": len(rows),
            "sessions_with_an_insight": len(completed),
            "tools_on": ARM_CONFIG[arm][0],
            "tool_rounds_total": sum(int(row.get("tool_rounds") or 0) for row in rows),
            "sessions_exercising_tools": len(exercised),
            "tool_exercise_fraction": (round(len(exercised) / len(rows), 4) if rows else None),
            "degraded_sessions": len(degraded),
            "degraded_fraction": (round(len(degraded) / len(rows), 4) if rows else None),
            "degradation_codes": sorted({c for row in rows for c in row.get("degradations", [])}),
            "ttfi_seconds": _quantiles(ttfi),
            "ttfi_by_boundary_step": by_step,
            "wall_seconds": _quantiles(wall),
            "turns": _quantiles([float(row.get("turns") or 0) for row in rows]),
            "completion_tokens": _quantiles(
                [float(row["completion_tokens"]) for row in rows if row.get("completion_tokens")]
            ),
            "P_all": _odds(ttfi, tails),
            "P_late": _odds(ttfi_late, tails),
            "P_late_n_sessions": len(ttfi_late),
        }

    governing = GOVERNING_ARM_PRIMARY
    v3_primary = (arms[GOVERNING_ARM_PRIMARY]["tool_exercise_fraction"] or 0.0) >= (
        MIN_TOOL_EXERCISE_FRACTION
    )
    if not v3_primary:
        governing = GOVERNING_ARM_FALLBACK
    cell = arms[governing]

    gates = {
        "V1_drives": len(usable_drives) >= MIN_DRIVES,
        "V2_sessions": cell["sessions_with_an_insight"] >= MIN_SESSIONS_PER_ARM,
        "V3_tools_exercised": (cell["tool_exercise_fraction"] or 0.0) >= MIN_TOOL_EXERCISE_FRACTION,
        "V4_degradation": (cell["degraded_fraction"] or 0.0) <= MAX_ERROR_FRACTION,
    }
    p_all, p_late = cell["P_all"], cell["P_late"]
    if not all(gates.values()) or p_all is None or p_late is None:
        decision = "INCONCLUSIVE"
        replacement = None
    elif p_all >= KEEP_THRESHOLD and p_late >= KEEP_THRESHOLD:
        decision = "KEEP"
        replacement = None
    else:
        decision = "REPLACE"
        replacement = _ceil_2(1 - p_late)

    # DV-CHECK: the estimator against the drives' own observed late drops.
    observed_late = sum(
        int((row.get("muse_counts") or {}).get("insights_dropped_late") or 0) for row in drives
    )
    p_off2 = arms[ARM_OFF_2]["P_all"]
    predicted_late = round(len(usable_drives) * (1 - p_off2), 2) if p_off2 is not None else None

    return {
        "n_drives": len(drives),
        "usable_drives": len(usable_drives),
        "tail_seconds": _quantiles([float(value) for value in tails]),
        "tails": tails,
        "arms": arms,
        "governing_arm": governing,
        "governing_arm_is_fallback": governing != GOVERNING_ARM_PRIMARY,
        "validity_gates": gates,
        "keep_threshold": KEEP_THRESHOLD,
        "decision": decision,
        "baseline_late_drops_per_run": BASELINE_LATE_DROPS_PER_RUN,
        "replacement_target_late_drops_per_run": replacement,
        "dv_check": {
            "arm": ARM_OFF_2,
            "predicted_late_drops_across_series": predicted_late,
            "observed_late_drops_across_series": observed_late,
            "drives": len(usable_drives),
        },
        "absent_lanes": [
            "tools-on-in-drive: ThreadedMuseRunner has no tools= seam, so no live drive "
            "can run a tool-wielding muse. The odds are an estimate from two measured "
            "distributions, never a directly observed rate."
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def _read(path: Optional[str]) -> list[dict[str, Any]]:
    if not path:
        return []
    handle = Path(path).expanduser()
    if not handle.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in handle.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lane", choices=("sessions", "drives"), default=None)
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--out", default=None, help="JSONL the chosen lane appends to")
    parser.add_argument("--sessions", default=None, help="--analyse: the sessions JSONL")
    parser.add_argument("--drives", default=None, help="--analyse: the drives JSONL")
    parser.add_argument("--workdir", default="results/muse_latency")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--cortex-temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--muse-temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--identity", default="Gwen")
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--n-drives", type=int, default=N_DRIVES)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.analyse:
        fold = analyse(_read(args.sessions), _read(args.drives))
        print(json.dumps(fold, indent=2, default=str))
        return 0

    if args.lane is None:
        print("error: pass --lane sessions, --lane drives, or --analyse", file=sys.stderr)
        print("hint: see docs/live-test-results/muse-latency-preregistration.md", file=sys.stderr)
        return 1

    key = os.environ.get("COLLEAGUE_API_KEY", "").strip()
    if not key:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        print("hint: export it before running either lane", file=sys.stderr)
        return 2

    rows = (lane_sessions if args.lane == "sessions" else lane_drives)(args, key)
    out = Path(args.out).expanduser() if args.out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=str) + "\n")
    print(json.dumps({"lane": args.lane, "rows": len(rows), "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
