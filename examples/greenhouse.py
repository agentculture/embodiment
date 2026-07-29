#!/usr/bin/env python3
"""greenhouse — a tiny non-colleague app that gains an embodied presence.

Run it twice and the second run remembers the first::

    python examples/greenhouse.py "New plant card - name: Marlow; sensor: \
s-fig-01; water below: 30% moisture. It is the fig by the north window. \
Check it in and log the visit."
    python examples/greenhouse.py --moisture 22 "Does Marlow need water today?"

The second process never sees ``s-fig-01`` in its own utterance. It reads that
sensor anyway, because :mod:`embodiment.lifecycle` wrote the first visit into a
durable store and this host recalled it before building the task. That is the
whole demonstration: *an embodiment without memory is a sequence of awakenings*
(embodiment issue #2), and here the sequence closes.

**Software presence, not a robot body.** This is a potting-shed assistant, not
a gardening robot. It reads simulated sensors and writes its own journal file;
it drives no hardware. The package is named ``embodiment`` because it gives an
application a loop and a presence — in this mesh ``reachy-mini-cli`` owns the
physical robot and ``reachy-lobes`` its local brain.

What an app author is meant to copy
-----------------------------------
Four seams, in the order this file wires them:

1. **A tool surface** (:class:`Greenhouse`) — three domain tools, no shell, no
   filesystem beyond the app's own home. embodiment never constructs an
   executor: what the mind may do is entirely the host's to decide.
2. **A model seam** (``complete``) — one callable performing one model turn.
   Here it is either :func:`scripted_cortex` (hermetic, the default) or
   :func:`gateway_seam` against a real OpenAI-compatible endpoint (``--live``).
3. **A continuity seam** — ``build_continuity_fn(LifecycleConfig(...))``,
   injected as ``run(..., continuity=...)``. It recalls at the considered beat
   and writes at the remembered beat. ``data_dir`` is its **mandatory** store
   anchor; without one every eidetic call refuses rather than guessing.
4. **A presence surface** (:class:`PresenceIO`) — where the pump's lines go.
   Optionally a second, advisory mind (``--muse``) running beside the actor.

The one thing embodiment deliberately does NOT do
-------------------------------------------------
It never injects recalled memory into the model conversation. What the acting
mind is *told* is host policy, so this file does that itself, in
:func:`build_task` — one recall, rendered into ``Task.context``. embodiment
owns *when* something is recalled, considered, acted on and remembered; it does
not own the prompt.

Every model this host actually ran is named in its own report, and a run with
no muse reports ``"muse": null`` — a single-model run never claims a second
mind exists.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from embodiment import (
    Boundary,
    BundleRequest,
    LifecycleConfig,
    LoopAborted,
    LoopControls,
    ModelResponse,
    MuseControls,
    PresenceEngine,
    PresenceIO,
    Task,
    ThreadedMuseRunner,
    ToolCall,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    build_continuity_fn,
    continuity,
    flat_fetch,
    frame_cortex,
    frame_muse,
    perceive,
    run,
    speaker_label,
)

# ── who this app is, and where it keeps things ───────────────────────────────

#: eidetic scope for everything this demo remembers. Its own, never the
#: repo agent's — a demo must not write into the mesh's shared memory.
SCOPE = "greenhouse-demo"

#: eidetic's ``type`` vocabulary is free-form; a visit is a care log.
RECORD_TYPE = "care-log"

#: Stamped on every durable record as provenance. A real host resolves this
#: from its own identity source; a demo states it plainly.
ADDED_BY = "greenhouse-demo"

#: ``hybrid`` (eidetic's own default) dials an embedding endpoint. This demo
#: is hermetic by default, so it ranks lexically and says so.
RECALL_MODE = "keyword"

#: How many prior records are offered to the mind as context.
RECALL_TOP_K = 3

#: How many prior records the MUSE is given as raw material to compile from.
#: Wider than :data:`RECALL_TOP_K` on purpose: the cortex is handed a short
#: rendered context because it is acting, while the muse is handed material
#: because it is reflecting over it. Equal values would make the compiled-from
#: provenance a no-op — every cited id would already be one lifecycle recalled
#: on its own, so the durable record's ``links`` could never gain anything.
MUSE_BUNDLE_TOP_K = 10

#: The model id recorded on a hermetic run's stats. It names no real model,
#: because no real model ran.
SCRIPTED_CORTEX = "scripted-greenhouse-mind"
SCRIPTED_MUSE = "scripted-greenhouse-muse"

# ── the live rig (opt-in, never a default) ───────────────────────────────────

#: Read from the environment on ``--live``. Never hardcoded, never printed.
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: An OpenAI-compatible gateway. The reference rig is a ``lobes`` gateway.
DEFAULT_BASE_URL = "http://localhost:8001/v1"

#: The reference rig's roles, addressed BY NAME through the gateway. A thinking
#: cortex emits a long ``reasoning`` field before any ``content`` — budget
#: ``--max-tokens`` generously or a truncated thought looks like an empty turn.
CORTEX_MODEL = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
MUSE_MODEL = "nvidia/Gemma-4-31B-IT-NVFP4"

#: The senses role model for the perception seam (``--perceive``).
SENSES_MODEL = "coolthor/gemma-4-12B-it-NVFP4A16"

#: Generous by design: the measured cortex spent 209 completion tokens on a
#: three-word answer, and at 64 it returned ``content: None`` mid-thought.
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MUSE_MAX_TOKENS = 512

# ── the greenhouse itself ────────────────────────────────────────────────────

#: The simulated bed. Deterministic: a demo that changes its answer between
#: runs cannot prove anything about memory.
SENSORS = {"s-fig-01": 42, "s-herb-02": 61, "s-monstera-03": 18}

#: The system prompt, before any identity framing. Deliberately free of the
#: ``name:`` / ``sensor:`` / ``water below:`` markers a plant card uses, so the
#: prompt itself can never be mistaken for remembered knowledge.
BASE_SYSTEM = (
    "You look after a small greenhouse. You can read a moisture sensor, log a "
    "care visit, and finish. Work only from what you were told or what you "
    "remember; never invent a sensor id or a watering threshold. When you do "
    "not know a plant, say so plainly and ask for its card. What you remember "
    "is a past visit, never a present reading: take the ids and thresholds "
    "from memory, but always take the moisture from a fresh reading before "
    "deciding about water. Finish with a summary the next visit can start "
    "from — restate the plant, its sensor id and its watering threshold, then "
    "say what you did. Only what you put in that summary is remembered."
)

#: The tool surface as the wire sees it. Only the live seam sends this; the
#: hermetic mind reads its prompt instead.
TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_sensor",
            "description": "Read the current moisture percentage from one soil sensor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor": {"type": "string", "description": "Sensor id, e.g. s-fig-01."}
                },
                "required": ["sensor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_care",
            "description": "Record one care visit in the greenhouse journal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant": {"type": "string"},
                    "action": {"type": "string", "description": "watered | checked"},
                    "note": {"type": "string"},
                },
                "required": ["plant", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the visit with a summary worth remembering.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

TOOL_SCHEMA_NAMES = tuple(entry["function"]["name"] for entry in TOOL_SCHEMA)


class Greenhouse:
    """The app's tool surface: three domain tools, no shell anywhere.

    embodiment reads only ``execute``; the ``changed`` ledger below is one of
    three optional attributes it picks up defensively when a host keeps one.
    """

    def __init__(self, home: Path, *, moisture: Optional[int] = None) -> None:
        self.home = Path(home)
        self.journal = self.home / "journal.jsonl"
        self._moisture = moisture
        self.changed: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- the protocol embodiment actually uses -------------------------------

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))
        if name == "read_sensor":
            return self._read_sensor(str(arguments.get("sensor", "")))
        if name == "log_care":
            return self._log_care(arguments)
        if name == "finish":
            summary = str(arguments.get("summary", "")).strip()
            return ToolOutcome(result="visit closed", finished=True, finish_summary=summary)
        raise UnknownToolError(f"this greenhouse has no tool called {name!r}")

    # -- what a host reads between turns -------------------------------------

    def state(self) -> str:
        """A one-line run snapshot for the presence pump."""
        if not self.calls:
            return "arriving at the greenhouse"
        return f"{len(self.calls)} action(s); last: {self.calls[-1][0]}"

    # -- the tools -----------------------------------------------------------

    def _read_sensor(self, sensor: str) -> ToolOutcome:
        if sensor not in SENSORS:
            # A ToolError costs one self-correcting step, never the run.
            raise ToolError(f"no sensor {sensor!r} in this greenhouse")
        reading = SENSORS[sensor] if self._moisture is None else self._moisture
        return ToolOutcome(result=f"{sensor} reads {reading}% moisture")

    def _log_care(self, arguments: dict[str, Any]) -> ToolOutcome:
        entry = {
            "kind": "care",
            "plant": str(arguments.get("plant", "")),
            "action": str(arguments.get("action", "")),
            "note": str(arguments.get("note", "")),
        }
        append_journal(self.journal, entry)
        self.changed.append(str(self.journal))
        return ToolOutcome(
            result=f"logged: {entry['action']} {entry['plant']}",
            changed_file=str(self.journal),
        )


# ── the app's own little store, separate from embodiment's memory ────────────


def append_journal(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSON line to the greenhouse journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def count_visits(path: Path) -> int:
    """How many visits this greenhouse has already had."""
    if not path.exists():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") == "visit":
            total += 1
    return total


def default_home() -> Path:
    """Where the demo keeps its store when the operator names no directory.

    Under the platform temp directory on purpose: a demo must never write into
    the checkout it is being read from.
    """
    return Path(tempfile.gettempdir()) / "embodiment-greenhouse-demo"


# ── the hermetic mind: a function of its prompt, not a fixture ───────────────

_NAME_RE = re.compile(r"name:\s*([A-Za-z][\w-]*)")
_SENSOR_RE = re.compile(r"sensor:\s*(s-[a-z0-9]+-\d+)")
_THRESHOLD_RE = re.compile(r"water below:\s*(\d+)")
_READING_RE = re.compile(r"(\d+)%\s*moisture")

REFUSAL = (
    "I have no plant card for that plant. Give me its name, sensor id and "
    "watering threshold and the next visit starts from there."
)


def message_text(message: dict[str, Any]) -> str:
    """The readable text of one wire message, list-content included."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def read_card(transcript: str) -> Optional[dict[str, Any]]:
    """The plant card this mind can see — from the utterance, or from memory."""
    name = _NAME_RE.search(transcript)
    sensor = _SENSOR_RE.search(transcript)
    threshold = _THRESHOLD_RE.search(transcript)
    if not (name and sensor and threshold):
        return None
    return {"name": name.group(1), "sensor": sensor.group(1), "threshold": int(threshold.group(1))}


def scripted_cortex(messages: list[dict[str, Any]]) -> ModelResponse:
    """The hermetic model seam: one turn, decided entirely by the prompt.

    Nothing is memoised and nothing is hard-coded about Marlow. Handed a
    conversation with no plant card it refuses; handed one where a card arrived
    through recalled memory it acts on it. That is what makes the two-process
    test a proof rather than a re-enactment.
    """
    transcript = "\n".join(message_text(message) for message in messages)
    card = read_card(transcript)
    if card is None:
        return _call("finish", summary=REFUSAL)

    results = [message_text(m) for m in messages if m.get("role") == "tool"]
    reading = _last_reading(results)
    if reading is None:
        return _call("read_sensor", sensor=card["sensor"])

    if not any("logged:" in text for text in results):
        watered = reading < card["threshold"]
        return _call(
            "log_care",
            plant=card["name"],
            action="watered" if watered else "checked",
            note=f"{reading}% moisture",
        )

    watered = reading < card["threshold"]
    verb = "watered" if watered else "checked (no water needed)"
    return _call(
        "finish",
        summary=(
            f"Plant card - name: {card['name']}; sensor: {card['sensor']}; "
            f"water below: {card['threshold']}% moisture. "
            f"Visit: {reading}% moisture, {verb}; logged in the journal."
        ),
    )


_MUSE_TURNS = (
    "The card is the part worth keeping. A reading is weather; a threshold is knowledge.",
    "GUIDANCE: note why you did not water, not only that you did.\n[done]",
)


def scripted_muse(messages: list[dict[str, Any]]) -> ModelResponse:
    """A hermetic stand-in for the advisory lane. Proposes; never decides."""
    turn = sum(1 for message in messages if message.get("role") == "assistant")
    return ModelResponse(content=_MUSE_TURNS[min(turn, len(_MUSE_TURNS) - 1)])


def _last_reading(results: list[str]) -> Optional[int]:
    for text in reversed(results):
        found = _READING_RE.search(text)
        if found:
            return int(found.group(1))
    return None


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=dict(arguments))],
    )


# ── the live seam: one HTTP round trip per model turn ────────────────────────


def parse_completion(payload: dict[str, Any]) -> ModelResponse:
    """Shape one OpenAI-compatible completion into a :class:`ModelResponse`.

    ``reasoning`` is carried *separately* from ``content`` because the
    reference cortex is a thinking model: it emits a long reasoning field
    before any answer, and a run that folded the two together would report a
    thought as if it were a reply. A turn truncated mid-thought
    (``finish_reason: length``) arrives here as ``content: None`` — it becomes
    an empty answer with the reasoning preserved, never a fabricated one.
    """
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    raw_calls = message.get("tool_calls") or []

    calls: list[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        function = (raw or {}).get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = {}
        calls.append(
            ToolCall(
                id=str((raw or {}).get("id") or f"call-{index}"),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    usage = payload.get("usage") or {}
    return ModelResponse(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or message.get("reasoning") or ""),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


def gateway_seam(
    base_url: str,
    model: str,
    api_key: str,
    *,
    max_tokens: int,
    tools: Optional[list[dict[str, Any]]] = None,
    timeout: float = 300.0,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """Build a model seam that talks to one OpenAI-compatible endpoint.

    Roles are addressed **by name**: this function is handed a model id by the
    caller and infers nothing from it. The muse lane passes no ``tools`` at
    all — that absence is the whole of "tools-off".
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed anywhere.
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        # The scheme is pinned to http(s) above and the endpoint is the
        # operator's own --base-url; audited once, here.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return parse_completion(json.loads(response.read().decode("utf-8")))

    return complete


# ── the perception seam: interpret the operator's utterance ──────────────────


def senses_seam(
    base_url: str,
    model: str,
    api_key: str,
    *,
    max_tokens: int = 256,
    timeout: float = 60.0,
) -> Callable[[str], ModelResponse]:
    """Build a perception-seam callable that talks to the senses model.

    Takes a single string (the operator's verbatim utterance) and returns a
    :class:`ModelResponse` whose ``content`` is the JSON interpretation.
    Follows the same gateway pattern as :func:`gateway_seam`; the senses model
    is addressed **by name** and no role is inferred from the model id.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    system = (
        "You are a perception intake. Given an operator's request, return a "
        "JSON object with these keys: interpretation (a concise reading of "
        "what the request means), confidence (0.0-1.0), task_type (a short "
        "category such as query, task, or maintenance), omissions (a list of "
        "things the request left implicit), and ack (a brief acknowledgment "
        "line). Return ONLY the JSON object, no other text."
    )

    def interpret(text: str) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return parse_completion(json.loads(response.read().decode("utf-8")))

    return interpret


# ── wiring ───────────────────────────────────────────────────────────────────


def build_system_prompt(identity: Optional[str], *, muse: bool) -> str:
    """The cortex's system prompt, framed only when an identity is configured.

    Absent identity ⇒ **byte-identical** prompt. That is the acceptance
    criterion that keeps the whole framing feature honest, and it is one call.
    """
    return frame_cortex(BASE_SYSTEM, identity=identity or None, muse=muse) or BASE_SYSTEM


def is_consequential(boundary: Boundary) -> bool:
    """Which actions are worth a continuity checkpoint. The HOST decides.

    embodiment cannot know that writing the journal matters here and reading a
    sensor does not; guessing from a tool name would be exactly the inference
    this package refuses elsewhere.
    """
    return boundary.tool == "log_care"


def lifecycle_config(
    home: Path, *, coherence: bool = False, recall_top_k: int = RECALL_TOP_K
) -> LifecycleConfig:
    """How this app reaches durable memory.

    ``recall_top_k`` is threaded from ``--recall-top-k`` rather than left at
    :data:`RECALL_TOP_K`. This host performs *two* recalls — its own, whose
    text it renders into ``Task.context``, and the lifecycle's, whose ids
    become the durable record's ``links`` — and until this argument existed the
    flag governed only the first. One flag that silently moves one of two
    recalls is the kind of thing that makes a measurement mean something other
    than it appears to.

    ``data_dir`` is the mandatory anchor: without it eidetic would resolve a
    public record against whatever git repo the *host process* happens to be
    running in — and would quietly commit a greenhouse's memories into it.

    Coherence assessment is **off** by default because it dials an embedding
    endpoint, and this demo's default path touches nothing outside itself.
    ``--coherence`` turns it on; with no embedder answering, the meaning domain
    reports itself unavailable and the lifecycle ledger records that rather
    than pretending it scored anything.
    """
    home = Path(home)
    return LifecycleConfig(
        data_dir=home / "memory",
        scope=SCOPE,
        record_type=RECORD_TYPE,
        added_by=ADDED_BY,
        consequential=is_consequential,
        assess_action=coherence,
        assess_completion=coherence,
        assess_memory=coherence,
        recall_mode=RECALL_MODE,
        recall_top_k=recall_top_k,
        workdir=home / "work",
    )


def build_task(task_id: str, utterance: str, recalled: list[str]) -> Task:
    """Compose the work item — including what the mind is told it remembers.

    embodiment recalls, but it never injects: what the acting mind is *told* is
    host policy. This is that policy, in four lines, and the only reason run 2
    knows anything about run 1.
    """
    context = ""
    if recalled:
        context = "What you already know about this greenhouse:\n" + "\n".join(
            f"- {text}" for text in recalled
        )
    return Task(
        id=task_id,
        # embodiment's Task carries a repo_path for its first consumer; a
        # greenhouse has no repo, and a blank one is read as "no rig to
        # resolve" rather than falling back to the ambient directory.
        repo_path="",
        instruction=utterance,
        context=context,
        engine="greenhouse-demo",
    )


def visit(args: argparse.Namespace) -> dict[str, Any]:
    """One visit to the greenhouse: perceive, recall, act, remember.

    Returns the run report. Prints nothing to stdout — presence lines go to
    stderr, and rendering the report is :func:`main`'s job, so the two streams
    never blend.
    """
    home = Path(args.home).expanduser()
    if args.reset:
        shutil.rmtree(home, ignore_errors=True)
    store = home / "memory"
    store.mkdir(parents=True, exist_ok=True)
    (home / "work").mkdir(parents=True, exist_ok=True)
    journal = home / "journal.jsonl"
    task_id = f"visit-{count_visits(journal) + 1}"
    identity = (args.identity or "").strip() or None

    cortex, muse_complete, cortex_model, muse_model = build_minds(args)

    # 0. Perception seam (opt-in; off by default).
    packet = None
    senses_record = None
    if args.perceive:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            print(
                f"error: --perceive needs {API_KEY_ENV} in the environment",
                file=sys.stderr,
            )
            raise SystemExit(2)
        interpret_fn = senses_seam(
            args.base_url,
            SENSES_MODEL,
            key,
            max_tokens=256,
        )
        packet, senses_record = perceive(args.utterance, interpret=interpret_fn)

    # 1. Recall — the host's own, so the mind can be TOLD what it remembers.
    prior = continuity.recall(
        args.utterance,
        data_dir=store,
        scope=SCOPE,
        top_k=args.recall_top_k,
        mode=RECALL_MODE,
    )
    recalled_text = [str(record.get("text", "")) for record in prior.records]
    recalled_ids = [str(record["id"]) for record in prior.records if record.get("id")]

    # 2. The seams.
    tools = Greenhouse(home, moisture=args.moisture)
    runner: Optional[ThreadedMuseRunner] = None
    if muse_complete is not None:
        # The muse gets recalled material as raw bundle items, so it can
        # COMPILE memory rather than be told a conclusion. eidetic is
        # fetch-only; compilation is the muse's job, one layer up. Fetched once
        # for the whole work item — a work item has one recalled context — and
        # its citation surface comes back out through ``runner.compiled_from``
        # for the durable record's ``links``.
        #
        # Deliberately WIDER than the cortex's own recall (see
        # :data:`MUSE_BUNDLE_TOP_K`). Handing the muse exactly what the host
        # already told the cortex would make this whole seam a no-op: the
        # citation surface would always be a subset of what lifecycle recalled
        # by itself, ``links`` would never gain an id, and the provenance would
        # be true but carry no information.
        bundle = flat_fetch(
            BundleRequest(
                queries=[args.utterance],
                data_dir=store,
                scope=SCOPE,
                top_k=max(args.recall_top_k, MUSE_BUNDLE_TOP_K),
                mode=RECALL_MODE,
            )
        )
        runner = ThreadedMuseRunner(
            muse_complete,
            system=frame_muse(None, identity=identity),
            controls=MuseControls(max_turns=2),
            recall_bundle=bundle,
        )
    # The lifecycle reads the muse's citation surface at the memory boundary,
    # so a record written after a muse-informed drive links to what it compiled.
    lifecycle = build_continuity_fn(
        lifecycle_config(home, coherence=args.coherence, recall_top_k=args.recall_top_k),
        muse=runner,
    )
    presence = PresenceEngine(
        io=PresenceIO(
            render=lambda line: print(line, file=sys.stderr),
            task_state=tools.state,
        ),
        muse=runner,
        speaker=speaker_label(identity),
    )

    # 3. The drive.
    task = build_task(task_id, args.utterance, recalled_text)
    aborted: Optional[str] = None
    try:
        outcome = run(
            cortex,
            task,
            executor=tools,
            max_steps=args.max_steps,
            system_prompt=build_system_prompt(identity, muse=runner is not None),
            presence=presence,
            continuity=lifecycle,
            controls=LoopControls(write_intent=False),
            model=cortex_model,
        )
    except LoopAborted as failure:
        # The seam broke mid-run. The partial work is still on the outcome, so
        # a host can report it rather than losing the visit entirely.
        outcome = failure.outcome
        aborted = str(failure.__cause__ or failure)
    finally:
        if runner is not None:
            runner.close()

    report = build_report(
        args=args,
        home=home,
        store=store,
        task_id=task_id,
        outcome=outcome,
        lifecycle=lifecycle,
        prior=prior,
        recalled_ids=recalled_ids,
        recalled_text=recalled_text,
        presence=presence,
        cortex_model=cortex_model,
        muse_model=muse_model,
        identity=identity,
        aborted=aborted,
        muse_snapshot=muse_snapshot(runner),
        packet=packet,
        senses_record=senses_record,
    )
    append_journal(
        journal,
        {
            "kind": "visit",
            "task_id": task_id,
            "utterance": args.utterance,
            "summary": report["loop"]["summary"],
            "exit": report["loop"]["exit_reason"],
        },
    )
    return report


def build_minds(
    args: argparse.Namespace,
) -> tuple[Any, Optional[Any], str, Optional[str]]:
    """Resolve the model seams. ``--live`` is the ONLY path that reaches a network."""
    if not args.live:
        muse = scripted_muse if args.muse else None
        return scripted_cortex, muse, SCRIPTED_CORTEX, SCRIPTED_MUSE if args.muse else None

    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
        print(
            f"hint: export {API_KEY_ENV}=… and point --base-url at your gateway "
            f"(currently {args.base_url})",
            file=sys.stderr,
        )
        raise SystemExit(2)

    cortex = gateway_seam(
        args.base_url,
        args.cortex_model,
        key,
        max_tokens=args.max_tokens,
        tools=TOOL_SCHEMA,
    )
    muse = None
    if args.muse:
        # Tools-off by construction: no schema is passed, so none can be called.
        muse = gateway_seam(
            args.base_url,
            args.muse_model,
            key,
            max_tokens=args.muse_max_tokens,
        )
    return cortex, muse, args.cortex_model, args.muse_model if args.muse else None


def muse_snapshot(runner: Optional[ThreadedMuseRunner]) -> Optional[dict[str, Any]]:
    """The advisory lane's own record, JSON-safe. ``None`` when none ran.

    The runner's degradation ledger holds dataclasses; a report that must
    survive a pipe renders them rather than dropping them — an advisory mind
    that fell over silently is exactly the thing C3 forbids.
    """
    if runner is None:
        return None
    snapshot = runner.snapshot()
    snapshot["degradations"] = [record.to_dict() for record in snapshot["degradations"]]
    return snapshot


def build_report(**parts: Any) -> dict[str, Any]:
    """Everything this visit did, and everything that degraded while doing it."""
    outcome = parts["outcome"]
    result = outcome.result
    lifecycle = parts["lifecycle"]
    events = [event.to_dict() for event in lifecycle.events]
    remembered = next((e["data"] for e in events if e["kind"] == "remembered"), None)
    prior = parts["prior"]
    status = lifecycle.status

    packet = parts.get("packet")
    senses_record = parts.get("senses_record")
    perception: Optional[dict[str, Any]] = None
    if packet is not None:
        perception = {
            "packet": packet.to_dict(),
            "record": senses_record.to_dict() if senses_record is not None else None,
        }

    return {
        "home": str(parts["home"]),
        "store": str(parts["store"]),
        "task_id": parts["task_id"],
        "pid": os.getpid(),
        "utterance": parts["args"].utterance,
        "mind": {
            "cortex": parts["cortex_model"],
            # None, truthfully, whenever no second mind ran.
            "muse": parts["muse_model"],
            "identity": parts["identity"],
            "speaker": speaker_label(parts["identity"]),
            "presence_mode": parts["presence"].mode,
            "muse_runner": parts["muse_snapshot"],
        },
        "continuity": {
            "mode": status.mode if status is not None else None,
            "recalled": parts["recalled_ids"],
            "recalled_text": parts["recalled_text"],
            "recall_degradation": (
                prior.degradation.to_dict() if prior.degradation is not None else None
            ),
            "remembered": remembered,
            "events": events,
            "dropped_events": lifecycle.dropped_events,
        },
        "loop": {
            "exit_reason": outcome.exit_reason,
            "status": result.status,
            "summary": result.summary,
            "steps": [
                {
                    "index": step.index,
                    "tool": step.tool,
                    "arguments": step.arguments,
                    "result": step.result,
                    "ok": step.ok,
                }
                for step in result.steps
            ],
            "model_turns": result.stats.model_turns,
            "reasoning_chars": result.stats.reasoning_chars,
            "answer_chars": result.stats.answer_chars,
            "aborted": parts["aborted"],
            "degradations": [d.to_dict() for d in outcome.degradations],
        },
        "perception": perception,
    }


# ── rendering ────────────────────────────────────────────────────────────────


def render_text(report: dict[str, Any]) -> None:
    """The human-readable report. Results only — diagnostics went to stderr."""
    mind = report["mind"]
    continuity_block = report["continuity"]
    lines = [
        f"greenhouse — {report['task_id']}",
        f"memory store (LifecycleConfig.data_dir): {report['store']}",
        f"continuity: {continuity_block['mode']}",
        (
            f"mind: cortex={mind['cortex']} "
            f"muse={mind['muse'] or '(none)'} "
            f"identity={mind['identity'] or '(none)'}"
        ),
        "",
    ]
    if continuity_block["recalled"]:
        lines.append(f"recalled {len(continuity_block['recalled'])} prior record(s):")
        for record_id, text in zip(continuity_block["recalled"], continuity_block["recalled_text"]):
            lines.append(f"  [{record_id}] {text}")
    else:
        lines.append("recalled nothing — this greenhouse has no history yet.")
    lines.append("")
    for step in report["loop"]["steps"]:
        lines.append(f"  {step['tool']}({_brief(step['arguments'])}) -> {step['result']}")
    lines.append("")
    lines.append(f"summary: {report['loop']['summary']}")
    lines.append(f"exit: {report['loop']['exit_reason']} ({report['loop']['status']})")
    remembered = continuity_block["remembered"]
    if remembered:
        links = ", ".join(remembered.get("links") or []) or "none"
        lines.append(f"remembered: {remembered.get('record_id')} (links: {links})")
    else:
        lines.append("remembered: nothing this visit")
    print("\n".join(lines))


def _brief(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in arguments.items())


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """The demo's argument surface. ``--live`` is opt-in and has no default."""
    parser = argparse.ArgumentParser(
        prog="greenhouse",
        description=(
            "A tiny non-colleague app with an embodied presence. Run it twice "
            "against one --home and the second run remembers the first."
        ),
    )
    parser.add_argument("utterance", help="what you say to the greenhouse this visit")
    parser.add_argument(
        "--home",
        default=str(default_home()),
        help="the demo's own directory; its memory store lives in <home>/memory",
    )
    parser.add_argument("--reset", action="store_true", help="forget everything first")
    parser.add_argument("--json", action="store_true", help="emit the run report as JSON")
    parser.add_argument(
        "--moisture",
        type=int,
        default=None,
        help="override the simulated reading, so a second visit can be drier",
    )
    parser.add_argument("--max-steps", type=int, default=8, help="the model-turn budget")
    parser.add_argument(
        "--recall-top-k", type=int, default=RECALL_TOP_K, help="how many records to recall"
    )
    parser.add_argument(
        "--identity",
        default="",
        help="the teammate identity to frame prompts with (absent ⇒ prompts unchanged)",
    )
    parser.add_argument(
        "--muse",
        action="store_true",
        help="run a second, advisory mind beside the actor (it proposes, never decides)",
    )
    parser.add_argument(
        "--coherence",
        action="store_true",
        help="assess coherence at each checkpoint (dials an embedding endpoint)",
    )
    live = parser.add_argument_group("live rig (opt-in; nothing here is a default)")
    live.add_argument(
        "--live",
        action="store_true",
        help=f"talk to a real OpenAI-compatible gateway; needs {API_KEY_ENV} in the environment",
    )
    live.add_argument("--base-url", default=DEFAULT_BASE_URL, help="gateway base URL")
    live.add_argument("--cortex-model", default=CORTEX_MODEL, help="the acting model id")
    live.add_argument("--muse-model", default=MUSE_MODEL, help="the advisory model id")
    live.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    live.add_argument("--muse-max-tokens", type=int, default=DEFAULT_MUSE_MAX_TOKENS)
    live.add_argument(
        "--perceive",
        action="store_true",
        help="route the utterance through the perception seam (senses model) before the drive",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = visit(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        render_text(report)
    return 0 if report["loop"]["exit_reason"] == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
