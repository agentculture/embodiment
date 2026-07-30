#!/usr/bin/env python3
"""league_commander — a commander model commands unit-agent models in League.

Task **t28**, issue **#36**. The operator's proposal::

    Gemma 4 31B
      └─ coordinator / architect / final judge
           ├─ Qwen 3.6 27B: primary developer

The question this harness exists to answer is **not** "is B good". It is: if B
is better, is that because *Gemma is the better commander* or because
*hierarchy beats a flat mind*? Those are two claims, and a two-arm comparison
cannot separate them — hence the mandatory mirror (C) and two flat baselines.

Read ``docs/live-test-results/league-commander-preregistration.md`` first: it
carries the decision rule, the validity gates, the ceiling risk and the
escalation ladder, all fixed before the first measured dial.

**Software presence, not a robot body.** This commands units in someone else's
simulation. It drives no hardware. ``embodiment`` is named for the loop and the
presence an app gains; in this mesh ``reachy-mini-cli`` owns the physical robot.
C2, stated rather than left to inference.

No embodiment source change
---------------------------
``embodiment.loop.run`` already lets a host pick a **different model per
level**, and this file is the proof by use::

    run(complete=<commander model>, subagent=<seam driving a child>,
        spawn_allowance=1, lineage=…)

* **Commander** = the top-level ``run()``. It already holds final authority:
  its ``order`` tool call is what reaches the arena. Nothing else can submit.
* **Unit agent** = the child the injected ``SubagentFn`` drives, with its own
  ``complete``. It has one tool, ``propose``, and no way to reach the arena.
* ``SubagentCall.allowance`` → ``spawn_allowance``, ``.lineage`` → ``lineage``,
  ``.max_steps`` → ``max_steps``. That is the whole protocol, verbatim from
  ``embodiment/subagent.py``.
* ``spawn_allowance`` defaults to ``NO_SPAWNS`` — it **must** be strictly
  positive or no child can exist. Here it is exactly ``1``: the child is minted
  with ``attenuate(1) == 0``, so depth is bounded at 1 and the whole subtree at
  ``2**1 - 1 == 1``.
* Levels are typed with ``embodiment.framing`` — ``ROLE_CORTEX`` for the
  top-level acting loop, ``ROLE_SUBAGENT`` for the child, per colleague#352.
  With no configured identity those functions are byte-identical no-ops, so the
  framing seam is exercised without smuggling a persona into the measurement.

Why the continuous lane
-----------------------
``league cmatch`` asks for exactly ONE unit's action at a decision point — the
instant that unit goes idle. ``league match`` asks for a whole-team turn order.
So the commander/unit split is **native** here rather than simulated: the arena
poses the unit-level question, the unit agent answers it, the commander
adjudicates. The arena is reached through its own CLI in a subprocess, never by
importing ``league``.

Usage::

    # hermetic — scripted minds, no network, no rig
    uv run python examples/league_commander.py play --root /tmp/lc --arm all

    # the measured series
    uv run python examples/league_commander.py play --root /tmp/lc --arm all --live \\
        --out docs/live-test-results/league-commander.jsonl \\
        --config-out docs/live-test-results/league-commander-config.json \\
        --transcripts docs/live-test-results/league-commander-transcripts.jsonl

    # the pre-registered fold, applied to whatever came back
    uv run python examples/league_commander.py analyse \\
        --out docs/live-test-results/league-commander.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import statistics

# The arena is a separate program reached through its CLI. That is the whole
# point: fixed argv, no shell, and no `import league` anywhere under examples/.
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    ROLE_CORTEX,
    ROLE_SUBAGENT,
    LoopAborted,
    LoopControls,
    ModelResponse,
    SpawnRequest,
    SubagentCall,
    SubagentResult,
    Task,
    ToolCall,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    frame_cortex,
    frame_subagent,
    run,
)

# ── the pre-registered constants ─────────────────────────────────────────────
# Every value here is asserted by literal in
# tests/test_league_commander_preregistration.py. Changing one after the first
# dial means editing a test that says so out loud in a diff a reviewer sees.

ARM_B = "B"
ARM_C = "C"
ARM_A_QWEN = "A-qwen"
ARM_A_GEMMA = "A-gemma"
ARMS = (ARM_B, ARM_C, ARM_A_QWEN, ARM_A_GEMMA)
HIERARCHICAL_ARMS = (ARM_B, ARM_C)
FLAT_ARMS = (ARM_A_QWEN, ARM_A_GEMMA)

QWEN = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
GEMMA = "nvidia/Gemma-4-31B-IT-NVFP4"

#: ``arm -> (commander model or None, unit model)``.
ARM_MODELS: dict[str, tuple[Optional[str], str]] = {
    ARM_B: (GEMMA, QWEN),
    ARM_C: (QWEN, GEMMA),
    ARM_A_QWEN: (None, QWEN),
    ARM_A_GEMMA: (None, GEMMA),
}

SCENARIO = "c-skirmish-1"
ESCALATION_SCENARIO = "c-frontier-1"
OPPONENT_DRIVER = "bot"
OUR_DRIVER = "stateless"
SEEDS = (101, 102, 103, 104, 105)
N_MATCHES = 5
ESCALATION_N = 3

#: Scenario -> the roster roles it requires, in the order league reports them.
SCENARIO_ROLES = {
    SCENARIO: ("defender", "harvester"),
    ESCALATION_SCENARIO: ("defender", "harvester", "scout"),
}

SPAWN_ALLOWANCE = 1
COMMANDER_MAX_STEPS = 6
UNIT_MAX_STEPS = 3
FLAT_MAX_STEPS = 4

MAX_TOKENS = 16000
TEMPERATURE = 0.7
REQUEST_TIMEOUT = 900.0
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 30.0
CONTENTION_SECONDS = 600.0

MARGIN_EFFECT = 3.0
NO_EFFECT_BAND = 1.0
DIRECTION_MIN = 4
GRADE_EFFECT = 200.0

#: ``DIRECTION_MIN`` is stated at ``N_MATCHES``; escalation E1 runs at
#: ``ESCALATION_N``. Deriving the fraction rather than hard-coding a second
#: literal is what keeps the smaller run's bar from being chosen after the fact.
DIRECTION_FRACTION = DIRECTION_MIN / N_MATCHES


def direction_required(matches: int) -> int:
    """How many matches must point the same way, at a given n."""
    return math.ceil(DIRECTION_FRACTION * matches)


LENGTH_FRACTION_MAX = 0.10
NO_ORDER_FRACTION_MAX = 0.20
SPAWN_GRANT_MIN_FRACTION = 0.90

VERDICT_EFFECT = "EFFECT"
VERDICT_NO_EFFECT = "NO_EFFECT"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_VOID = "VOID"
VERDICT_ABSENT = "ABSENT"
VERDICTS = (
    VERDICT_EFFECT,
    VERDICT_NO_EFFECT,
    VERDICT_INCONCLUSIVE,
    VERDICT_VOID,
    VERDICT_ABSENT,
)

VOID_TRUNCATED = "TRUNCATED"
VOID_DEGRADED = "DEGRADED"
VOID_NOT_HIERARCHICAL = "NOT_HIERARCHICAL"
VOID_REASONS = (VOID_TRUNCATED, VOID_DEGRADED, VOID_NOT_HIERARCHICAL)

# ── levels, so cost can be reported per level and not merely per match ───────

LEVEL_COMMANDER = "commander"
LEVEL_UNIT = "unit"
LEVEL_FLAT = "flat"
LEVELS = (LEVEL_COMMANDER, LEVEL_UNIT, LEVEL_FLAT)

API_KEY_ENV = "COLLEAGUE_API_KEY"
DEFAULT_BASE_URL = "http://localhost:8001/v1"

#: The loop's own knobs. Synthesis is off: a decision either goes through the
#: ``order`` tool or it does not exist, and a forced prose turn would spend a
#: model turn producing something the arena cannot execute.
CONTROLS = LoopControls(synthesis=False, incompletion=False, write_intent=False)


# ── the tool schemas ────────────────────────────────────────────────────────
# `tool_choice` is broken on the reference rig, so it is never sent. The schema
# alone is what puts the tools on the wire.

ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "order",
        "description": (
            "Submit this unit's action to the arena. This is the only call that "
            "reaches the game; your decision is final."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "menu_index": {
                    "type": "integer",
                    "description": "Index into the numbered menu you were shown.",
                },
                "why": {"type": "string", "description": "One sentence of reasoning."},
            },
            "required": ["menu_index"],
        },
    },
}

CONSULT_TOOL = {
    "type": "function",
    "function": {
        "name": "consult_unit",
        "description": (
            "Ask this unit's own agent for a proposal before you decide. It "
            "proposes; you decide. You may consult once per decision point."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What you want the unit agent to weigh.",
                }
            },
            "required": ["question"],
        },
    },
}

PROPOSE_TOOL = {
    "type": "function",
    "function": {
        "name": "propose",
        "description": (
            "Propose ONE menu entry to your commander. You do not act; the "
            "commander decides whether to issue your proposal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "menu_index": {
                    "type": "integer",
                    "description": "Index into the numbered menu you were shown.",
                },
                "why": {"type": "string", "description": "One sentence of reasoning."},
            },
            "required": ["menu_index"],
        },
    },
}

COMMANDER_TOOLS = [CONSULT_TOOL, ORDER_TOOL]
UNIT_TOOLS = [PROPOSE_TOOL]
FLAT_TOOLS = [ORDER_TOOL]

# ── the prompts ─────────────────────────────────────────────────────────────

_RULES = (
    "The match runs on a timeline, not turns. You are asked for exactly ONE "
    "unit's action at the instant that unit goes idle. Each menu entry carries "
    "its in-game duration and the absolute completion_time it would land at, so "
    "a race is plannable: if the enemy defender completes its take at t=15 and "
    "you can complete yours at t=13, you win the post. Points come from owned "
    "control points, delivered resources and completed missions."
)

COMMANDER_SYSTEM = (
    "You are the COMMANDER of a team in a real-time strategy match. You hold "
    "final authority: the `order` tool call is the only thing that reaches the "
    "arena, and it is yours to make.\n\n"
    "Each of your units is driven by its own separate agent — a different mind "
    "from yours. Consult the unit whose decision point this is with "
    "`consult_unit`, read its proposal, then issue `order`. You may accept the "
    "proposal or override it. The decision is yours and the unit cannot act "
    "without you.\n\n" + _RULES
)

UNIT_SYSTEM = (
    "You are a UNIT AGENT in a real-time strategy match. You control one unit "
    "and you report to a commander.\n\n"
    "You propose; the commander decides. Call `propose` with the menu index you "
    "believe is right and one sentence saying why. You have no way to act on "
    "the arena — your proposal is advice your commander may override.\n\n" + _RULES
)

FLAT_SYSTEM = (
    "You command a team in a real-time strategy match. You decide every unit's "
    "action yourself; there is no one else to consult. The `order` tool call is "
    "what reaches the arena.\n\n" + _RULES
)

DEFAULT_QUESTION = "What should this unit do now, and why?"


def render_menu(menu: list[dict[str, Any]]) -> str:
    """Number the menu so a model can answer with an index rather than prose."""
    if not menu:
        return "(no legal actions — you must park this unit)"
    lines = []
    for index, entry in enumerate(menu):
        kind = entry.get("kind", "?")
        target = entry.get("target", entry.get("target_id", entry.get("target_ref", "")))
        duration = entry.get("duration")
        completion = entry.get("completion_time")
        lines.append(
            f"[{index}] {kind} -> {target} (duration {duration}, " f"completes at t={completion})"
        )
    return "\n".join(lines)


def briefing_block(briefing: dict[str, Any]) -> str:
    """The identical board text every arm sees. Only the framing differs."""
    you = briefing.get("you") or {}
    menu = briefing.get("menu") or []
    return (
        f"Decision point at game_time={briefing.get('game_time')}.\n"
        f"You are unit {you.get('unit_id')} ({you.get('role')}) of team "
        f"{you.get('team_id')}, at {you.get('pos')}, carrying "
        f"{you.get('carrying')}.\n\n"
        f"MENU (answer with one of these indices):\n{render_menu(menu)}\n\n"
        f"OUTLOOK (who completes an action next):\n"
        f"{json.dumps(briefing.get('outlook') or [], sort_keys=True)}\n\n"
        f"BOARD:\n{json.dumps(briefing.get('board') or {}, sort_keys=True)}\n"
    )


def commander_instruction(briefing: dict[str, Any]) -> str:
    you = briefing.get("you") or {}
    return (
        f"{briefing_block(briefing)}\n"
        f"Consult {you.get('unit_id')}'s agent, then issue the order."
    )


def unit_instruction(briefing: dict[str, Any], question: str) -> str:
    return (
        f"{briefing_block(briefing)}\n"
        f"Your commander asks: {question}\n"
        f"Propose one menu index."
    )


def flat_instruction(briefing: dict[str, Any]) -> str:
    return f"{briefing_block(briefing)}\nIssue this unit's order."


# ── the league CLI seam ─────────────────────────────────────────────────────


class LeagueError(RuntimeError):
    """A league CLI call that did not return a usable payload."""


@dataclass
class LeagueCli:
    """The arena, reached through its own public CLI. Fixed argv, no shell."""

    root: Path
    binary: str = "league"

    def __call__(self, *args: str) -> dict[str, Any]:
        # Split with shlex, never handed to a shell — the same idiom
        # ``league_seat.py`` uses, so a test can point this at a stand-in.
        argv = [*shlex.split(self.binary), *args]
        # Fixed argv built from this module's own literals plus ids the arena
        # itself handed back; no shell, and `root` is the operator's own --root.
        completed = subprocess.run(  # nosec B603 B607
            argv,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        raw = completed.stdout.strip() or completed.stderr.strip()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise LeagueError(f"{' '.join(argv)}: unparsable output {raw[:400]!r}") from exc
        if isinstance(payload, dict) and "code" in payload and "message" in payload:
            raise LeagueError(f"{' '.join(argv)}: {payload['message']}")
        if not isinstance(payload, dict):
            raise LeagueError(f"{' '.join(argv)}: expected an object, got {type(payload).__name__}")
        return payload


# ── the live seam: one HTTP round trip per model turn ───────────────────────


@dataclass
class CallRecord:
    """One model call, recorded whatever it did. ``finish_reason`` is always here.

    A ``length`` finish is TRUNCATION — an instrument event — and is never
    scored as a bad decision or a lost match.
    """

    arm: str
    match_key: str
    decision_index: int
    unit_id: str
    level: str
    model: str
    seconds: float = 0.0
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_chars: int = 0
    content_chars: int = 0
    tool_names: list[str] = field(default_factory=list)
    retries: int = 0
    retry_reasons: list[str] = field(default_factory=list)
    contention: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_completion(payload: dict[str, Any]) -> tuple[ModelResponse, str]:
    """Shape one OpenAI-compatible completion, and hand back its finish_reason.

    ``reasoning`` is carried separately from ``content`` because the reference
    cortex is a thinking model; folding them together would report a thought as
    if it were a reply.
    """
    choices = payload.get("choices") or [{}]
    choice = choices[0] or {}
    message = choice.get("message") or {}
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
    response = ModelResponse(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or message.get("reasoning") or ""),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )
    return response, str(choice.get("finish_reason") or "")


def gateway_seam(
    base_url: str,
    model: str,
    api_key: str,
    *,
    tools: list[dict[str, Any]],
    sink: Callable[[CallRecord], None],
    context: dict[str, Any],
    transcript: Optional[Callable[[dict[str, Any]], None]] = None,
    timeout: float = REQUEST_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """One OpenAI-compatible endpoint, with every call recorded.

    Roles are addressed **by model id**; nothing is inferred from the name.

    **Retries are for contention, never for a better number.** A transport
    failure (timeout, connection reset, HTTP 5xx) is retried and every retry is
    recorded on the call. A *model* answer — empty content, a ``length`` finish,
    an out-of-range index — is data and is never retried.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
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
        record = CallRecord(
            arm=str(context.get("arm", "")),
            match_key=str(context.get("match_key", "")),
            decision_index=int(context.get("decision_index", 0)),
            unit_id=str(context.get("unit_id", "")),
            level=str(context.get("level", "")),
            model=model,
        )
        started = time.monotonic()
        for attempt in range(MAX_RETRIES + 1):
            try:
                # The scheme is pinned to http(s) above and the endpoint is the
                # operator's own --base-url; audited once, here.
                with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                elapsed = time.monotonic() - started
                record.retries = attempt + 1
                record.retry_reasons.append(reason)
                record.contention = record.contention or elapsed >= CONTENTION_SECONDS
                if attempt >= MAX_RETRIES:
                    record.seconds = round(elapsed, 3)
                    record.error = reason
                    sink(record)
                    raise
                sleep(RETRY_WAIT_SECONDS)

        response_model, finish_reason = parse_completion(payload)
        record.seconds = round(time.monotonic() - started, 3)
        record.finish_reason = finish_reason
        record.prompt_tokens = response_model.prompt_tokens
        record.completion_tokens = response_model.completion_tokens
        record.reasoning_chars = len(response_model.reasoning)
        record.content_chars = len(response_model.content)
        record.tool_names = [call.name for call in response_model.tool_calls]
        record.contention = record.contention or record.seconds >= CONTENTION_SECONDS
        sink(record)
        if transcript is not None:
            transcript(
                {
                    "call": record.to_dict(),
                    "messages": messages,
                    "response": response_model.to_dict(),
                }
            )
        return response_model

    return complete


# ── the scripted seams: the hermetic default ────────────────────────────────


def scripted_seam(
    tool_name: str,
    *,
    options: int,
    pick: Callable[[int], int],
    sink: Callable[[CallRecord], None],
    context: dict[str, Any],
    model: str,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A mind that answers by rule. No network, no rig.

    This is what makes the whole harness — the arms, the delegation, the
    ledger, the fold — testable without a model. ``consult_then_order`` is the
    scripted commander: it consults once, then orders, which is exactly the
    two-turn shape the live commander is asked for.
    """

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        record = CallRecord(
            arm=str(context.get("arm", "")),
            match_key=str(context.get("match_key", "")),
            decision_index=int(context.get("decision_index", 0)),
            unit_id=str(context.get("unit_id", "")),
            level=str(context.get("level", "")),
            model=model,
            finish_reason="tool_calls",
            prompt_tokens=10,
            completion_tokens=5,
        )
        text = "\n".join(str(m.get("content") or "") for m in messages)
        arguments: dict[str, Any] = {"menu_index": pick(options), "why": f"scripted {tool_name}"}
        name = tool_name
        if tool_name == "consult_then_order":
            consulted = "proposes menu index" in text or "no proposal" in text
            name = "order" if consulted else "consult_unit"
            if name == "consult_unit":
                arguments = {"question": DEFAULT_QUESTION}
        record.tool_names = [name]
        sink(record)
        return ModelResponse(
            content="",
            tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=arguments)],
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
        )

    return complete


# ── the tool surfaces ───────────────────────────────────────────────────────


def _menu_index(arguments: dict[str, Any], options: int) -> int:
    """Coerce and range-check a menu index; a bad one is a self-correcting step.

    Raising :class:`ToolError` rather than returning an error string is what a
    refusal actually means to the loop — one self-correcting step inside the
    budget, not a success carrying an error message.
    """
    raw = arguments.get("menu_index")
    try:
        index = int(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"menu_index must be an integer, got {raw!r}") from exc
    if not 0 <= index < options:
        raise ToolError(f"menu_index {index} is out of range; pick 0..{options - 1}")
    return index


class UnitExecutor:
    """The unit agent's whole surface: ``propose``. It cannot reach the arena."""

    def __init__(self, options: int) -> None:
        self.options = options
        self.proposed_index: Optional[int] = None
        self.why: str = ""
        self.calls: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name != "propose":
            raise UnknownToolError(f"{name} is not available to a unit agent")
        index = _menu_index(arguments, self.options)
        self.proposed_index = index
        self.why = str(arguments.get("why") or "").strip()
        return ToolOutcome(
            result=f"proposal recorded: menu index {index}",
            finished=True,
            finish_summary=f"proposes menu index {index}: {self.why}",
        )


class CommanderExecutor:
    """``consult_unit`` mints the child; ``order`` is the only path to the arena."""

    def __init__(
        self,
        *,
        briefing: dict[str, Any],
        decision_id: str,
        unit_model: str,
    ) -> None:
        self.briefing = briefing
        self.decision_id = decision_id
        self.unit_model = unit_model
        self.options = len(briefing.get("menu") or [])
        self.question: str = ""
        self.child_executor: Optional[UnitExecutor] = None
        self.ordered_index: Optional[int] = None
        self.order_why: str = ""
        self.calls: list[str] = []
        self.invalid_orders = 0

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name == "consult_unit":
            question = str(arguments.get("question") or "").strip() or DEFAULT_QUESTION
            self.question = question
            child_executor = UnitExecutor(self.options)
            self.child_executor = child_executor
            child_task = Task(
                id=f"{self.decision_id}-unit",
                repo_path="",
                instruction=unit_instruction(self.briefing, question),
            )
            return ToolOutcome(
                result="consulting the unit agent…",
                spawn=SpawnRequest(
                    task=child_task,
                    executor=child_executor,
                    role=ROLE_SUBAGENT,
                    max_steps=UNIT_MAX_STEPS,
                    system_prompt=frame_subagent(UNIT_SYSTEM, identity=None),
                    model=self.unit_model,
                ),
            )
        if name == "order":
            try:
                index = _menu_index(arguments, self.options)
            except ToolError:
                self.invalid_orders += 1
                raise
            self.ordered_index = index
            self.order_why = str(arguments.get("why") or "").strip()
            return ToolOutcome(
                result=f"order submitted: menu index {index}",
                finished=True,
                finish_summary=f"ordered menu index {index}",
            )
        raise UnknownToolError(f"{name} is not available to the commander")


class FlatExecutor:
    """The flat arm's whole surface: ``order``. No commander, no delegation."""

    def __init__(self, options: int) -> None:
        self.options = options
        self.ordered_index: Optional[int] = None
        self.order_why: str = ""
        self.calls: list[str] = []
        self.invalid_orders = 0

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name != "order":
            raise UnknownToolError(f"{name} is not available in the flat arm")
        try:
            index = _menu_index(arguments, self.options)
        except ToolError:
            self.invalid_orders += 1
            raise
        self.ordered_index = index
        self.order_why = str(arguments.get("why") or "").strip()
        return ToolOutcome(
            result=f"order submitted: menu index {index}",
            finished=True,
            finish_summary=f"ordered menu index {index}",
        )


# ── one decision point ──────────────────────────────────────────────────────


@dataclass
class DecisionRecord:
    """What happened at one decision point, at both levels."""

    arm: str
    match_key: str
    decision_index: int
    unit_id: str
    role: str
    game_time: int
    options: int
    hierarchical: bool
    commander_model: str = ""
    unit_model: str = ""
    spawn_allowance: int = 0
    proposed_index: Optional[int] = None
    ordered_index: Optional[int] = None
    overridden: Optional[bool] = None
    exit_reason: str = ""
    child_exit_reason: str = ""
    commander_turns: int = 0
    unit_turns: int = 0
    spawn_outcomes: list[str] = field(default_factory=list)
    spawn_allowance_remaining: int = 0
    lineage_depth: int = 0
    invalid_orders: int = 0
    no_order: bool = False
    degradations: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Minds:
    """The two model seams for one arm, built per decision point."""

    commander: Optional[Callable[[list[dict[str, Any]]], ModelResponse]]
    unit: Callable[[list[dict[str, Any]]], ModelResponse]
    commander_model: str
    unit_model: str


def decide(
    *,
    arm: str,
    match_key: str,
    decision_index: int,
    briefing: dict[str, Any],
    build_minds: Callable[[dict[str, Any]], Minds],
    context: dict[str, Any],
) -> DecisionRecord:
    """Drive ONE decision point and report both levels.

    The hierarchical path is the whole point of the experiment::

        run(commander_complete, …, subagent=unit_seam, spawn_allowance=1)

    Nothing but the commander's ``order`` reaches the arena, at any depth.
    """
    you = briefing.get("you") or {}
    unit_id = str(you.get("unit_id") or "")
    options = len(briefing.get("menu") or [])
    context.update(
        {
            "arm": arm,
            "match_key": match_key,
            "decision_index": decision_index,
            "unit_id": unit_id,
        }
    )
    minds = build_minds(briefing)
    hierarchical = minds.commander is not None
    record = DecisionRecord(
        arm=arm,
        match_key=match_key,
        decision_index=decision_index,
        unit_id=unit_id,
        role=str(you.get("role") or ""),
        game_time=int(briefing.get("game_time") or 0),
        options=options,
        hierarchical=hierarchical,
        commander_model=minds.commander_model,
        unit_model=minds.unit_model,
        spawn_allowance=SPAWN_ALLOWANCE if hierarchical else 0,
    )
    if options == 0:
        record.no_order = True
        record.exit_reason = "no-legal-actions"
        return record

    decision_id = f"{match_key}-d{decision_index}"
    if not hierarchical:
        _decide_flat(record, briefing, minds, decision_id, context)
        return record
    _decide_hierarchical(record, briefing, minds, decision_id, context)
    return record


def _decide_flat(
    record: DecisionRecord,
    briefing: dict[str, Any],
    minds: Minds,
    decision_id: str,
    context: dict[str, Any],
) -> None:
    executor = FlatExecutor(record.options)
    task = Task(id=decision_id, repo_path="", instruction=flat_instruction(briefing))
    context["level"] = LEVEL_FLAT
    try:
        outcome = run(
            minds.unit,
            task,
            executor=executor,
            max_steps=FLAT_MAX_STEPS,
            system_prompt=FLAT_SYSTEM,
            spawn_allowance=0,
            controls=CONTROLS,
            model=minds.unit_model,
        )
    except LoopAborted as aborted:
        record.error = aborted.outcome.result.error
        record.exit_reason = aborted.outcome.exit_reason
        record.commander_turns = 0
        record.unit_turns = aborted.outcome.result.stats.model_turns
        record.no_order = executor.ordered_index is None
        record.ordered_index = executor.ordered_index
        record.invalid_orders = executor.invalid_orders
        return
    record.exit_reason = outcome.exit_reason
    record.unit_turns = outcome.result.stats.model_turns
    record.spawn_allowance_remaining = outcome.spawn_allowance_remaining
    record.degradations = [getattr(d, "code", str(d)) for d in outcome.degradations]
    record.ordered_index = executor.ordered_index
    record.invalid_orders = executor.invalid_orders
    record.no_order = executor.ordered_index is None


def _decide_hierarchical(
    record: DecisionRecord,
    briefing: dict[str, Any],
    minds: Minds,
    decision_id: str,
    context: dict[str, Any],
) -> None:
    executor = CommanderExecutor(
        briefing=briefing,
        decision_id=decision_id,
        unit_model=minds.unit_model,
    )
    task = Task(id=decision_id, repo_path="", instruction=commander_instruction(briefing))

    def unit_seam(call: SubagentCall) -> Optional[SubagentResult]:
        """Drive the child. ``call.allowance``/``.lineage``/``.max_steps`` through."""
        child_executor = call.executor
        record.lineage_depth = call.depth
        context["level"] = LEVEL_UNIT
        try:
            child = run(
                minds.unit,
                call.task,
                executor=child_executor,
                max_steps=call.max_steps,
                spawn_allowance=call.allowance,
                lineage=call.lineage,
                subagent=None,
                system_prompt=call.system_prompt,
                controls=CONTROLS,
                model=call.model or minds.unit_model,
            )
        except LoopAborted as aborted:
            context["level"] = LEVEL_COMMANDER
            record.child_exit_reason = aborted.outcome.exit_reason
            return SubagentResult(
                model_turns=aborted.outcome.result.stats.model_turns,
                result="the unit agent failed to answer; decide without it",
                exit_reason=aborted.outcome.exit_reason,
                degradations=list(aborted.outcome.degradations),
            )
        context["level"] = LEVEL_COMMANDER
        record.child_exit_reason = child.exit_reason
        record.unit_turns += child.result.stats.model_turns
        proposed = getattr(child_executor, "proposed_index", None)
        why = getattr(child_executor, "why", "")
        record.proposed_index = proposed
        text = (
            f"unit {record.unit_id} proposes menu index {proposed}: {why}"
            if proposed is not None
            else f"unit {record.unit_id} returned no proposal; decide without it"
        )
        return SubagentResult(
            model_turns=child.result.stats.model_turns,
            result=text,
            exit_reason=child.exit_reason,
            degradations=list(child.degradations),
        )

    context["level"] = LEVEL_COMMANDER
    try:
        outcome = run(
            minds.commander,  # type: ignore[arg-type]
            task,
            executor=executor,
            max_steps=COMMANDER_MAX_STEPS,
            system_prompt=frame_cortex(COMMANDER_SYSTEM, identity=None, muse=False),
            subagent=unit_seam,
            spawn_allowance=SPAWN_ALLOWANCE,
            controls=CONTROLS,
            model=minds.commander_model,
        )
    except LoopAborted as aborted:
        record.error = aborted.outcome.result.error
        record.exit_reason = aborted.outcome.exit_reason
        record.commander_turns = aborted.outcome.result.stats.model_turns
        record.spawn_outcomes = [s.outcome for s in aborted.outcome.spawns]
        record.ordered_index = executor.ordered_index
        record.invalid_orders = executor.invalid_orders
        record.no_order = executor.ordered_index is None
        _grade_override(record)
        return
    record.exit_reason = outcome.exit_reason
    record.commander_turns = outcome.result.stats.model_turns
    record.spawn_outcomes = [s.outcome for s in outcome.spawns]
    record.spawn_allowance_remaining = outcome.spawn_allowance_remaining
    record.degradations = [getattr(d, "code", str(d)) for d in outcome.degradations]
    record.ordered_index = executor.ordered_index
    record.invalid_orders = executor.invalid_orders
    record.no_order = executor.ordered_index is None
    _grade_override(record)


def _grade_override(record: DecisionRecord) -> None:
    """Did the commander do anything? Diagnostic only — never a quality measure."""
    if record.proposed_index is None or record.ordered_index is None:
        record.overridden = None
        return
    record.overridden = record.proposed_index != record.ordered_index


# ── one match ───────────────────────────────────────────────────────────────


@dataclass
class MatchRecord:
    """One match, with cost reported PER LEVEL rather than only per match."""

    kind: str = "match"
    arm: str = ""
    match_key: str = ""
    match_id: str = ""
    scenario: str = ""
    seed: int = 0
    team_id: str = ""
    opponent_driver: str = ""
    our_driver: str = ""
    commander_model: str = ""
    unit_model: str = ""
    spawn_allowance: int = 0
    status: str = ""
    winner: Optional[str] = None
    blue_points: int = 0
    red_points: int = 0
    margin: int = 0
    blue_grade: float = 0.0
    decisions: int = 0
    no_order: int = 0
    invalid_orders: int = 0
    overrides: int = 0
    override_opportunities: int = 0
    spawn_granted: int = 0
    spawn_other: list[str] = field(default_factory=list)
    tokens_by_level: dict[str, dict[str, int]] = field(default_factory=dict)
    calls_by_level: dict[str, int] = field(default_factory=dict)
    seconds_by_level: dict[str, float] = field(default_factory=dict)
    finish_reasons: dict[str, int] = field(default_factory=dict)
    length_finishes: int = 0
    retries: int = 0
    contention_calls: int = 0
    errors: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    decision_records: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def team_ids(arm: str) -> tuple[str, str]:
    """One roster per arm, so the arena's own record separates the arms too."""
    slug = arm.lower().replace("-", "")
    return f"blue{slug}", f"red{slug}"


def register_teams(cli: LeagueCli, arm: str, scenario: str, unit_model: str) -> None:
    """Declare A MODEL PER UNIT so the arena keeps its own fairness record."""
    ours, theirs = team_ids(arm)
    roles = SCENARIO_ROLES[scenario]
    our_agents: list[str] = []
    their_agents: list[str] = []
    for index, role in enumerate(roles, start=1):
        our_agents += ["--agent", f"b{index}:{unit_model}:{role}"]
        their_agents += ["--agent", f"r{index}:house-bot:{role}"]
    cli("team", "register", ours, "--name", f"arm-{arm}", *our_agents, "--apply", "--json")
    cli("team", "register", theirs, "--name", "house-bot", *their_agents, "--apply", "--json")


def play_match(
    *,
    cli: LeagueCli,
    arm: str,
    match_index: int,
    scenario: str,
    seed: int,
    build_minds: Callable[[dict[str, Any]], Minds],
    context: dict[str, Any],
    calls: list[CallRecord],
    commander_model: str,
    unit_model: str,
    max_decisions: int = 400,
) -> MatchRecord:
    """Play one match to its end, one decision point at a time."""
    ours, theirs = team_ids(arm)
    match_key = f"{arm}-{match_index}"
    match_id = f"{arm.lower().replace('-', '')}{match_index}"
    started = time.monotonic()
    first_call = len(calls)

    existing = None
    try:
        existing = cli("cmatch", "show", match_id, "--json")
    except LeagueError:
        existing = None
    if existing is None:
        cli(
            "cmatch",
            "new",
            "--scenario",
            scenario,
            "--team",
            ours,
            "--team",
            theirs,
            "--driver",
            f"{ours}:{OUR_DRIVER}",
            "--driver",
            f"{theirs}:{OPPONENT_DRIVER}",
            "--seed",
            str(seed),
            "--id",
            match_id,
            "--apply",
            "--json",
        )

    record = MatchRecord(
        arm=arm,
        match_key=match_key,
        match_id=match_id,
        scenario=scenario,
        seed=seed,
        team_id=ours,
        opponent_driver=OPPONENT_DRIVER,
        our_driver=OUR_DRIVER,
        commander_model=commander_model,
        unit_model=unit_model,
        spawn_allowance=SPAWN_ALLOWANCE if commander_model else 0,
    )

    decision_index = 0
    for _ in range(max_decisions):
        show = cli("cmatch", "show", match_id, "--json")
        if show.get("status") != "active":
            break
        due = [d for d in (show.get("due") or []) if str(d).startswith(ours)]
        if not due:
            cli("cmatch", "tick", match_id, "--apply", "--json")
            continue
        for decision in show.get("decisions") or []:
            unit_id = str(decision.get("unit_id") or "")
            if not unit_id.startswith(ours):
                continue
            briefing = decision.get("briefing") or {}
            outcome = decide(
                arm=arm,
                match_key=match_key,
                decision_index=decision_index,
                briefing=briefing,
                build_minds=build_minds,
                context=context,
            )
            decision_index += 1
            record.decision_records.append(outcome.to_dict())
            menu = briefing.get("menu") or []
            chosen = (
                menu[outcome.ordered_index]
                if outcome.ordered_index is not None and outcome.ordered_index < len(menu)
                else None
            )
            cli(
                "cmatch",
                "act",
                match_id,
                "--unit",
                unit_id,
                "--action-json",
                json.dumps(chosen),
                "--apply",
                "--json",
            )

    score = cli("match", "score", match_id, "--json")
    _fold_match(record, score, ours, theirs, calls[first_call:])
    record.wall_seconds = round(time.monotonic() - started, 3)
    return record


def _fold_match(
    record: MatchRecord,
    score: dict[str, Any],
    ours: str,
    theirs: str,
    calls: list[CallRecord],
) -> None:
    outcome = score.get("outcome") or {}
    record.status = str(score.get("status") or "")
    record.winner = score.get("winner")
    record.blue_points = int(outcome.get(ours) or 0)
    record.red_points = int(outcome.get(theirs) or 0)
    record.margin = record.blue_points - record.red_points
    units = ((score.get("units") or {}).get("units")) or {}
    record.blue_grade = float(
        sum(float(u.get("grade") or 0.0) for k, u in units.items() if str(k).startswith(ours))
    )

    record.decisions = len(record.decision_records)
    for decision in record.decision_records:
        record.no_order += 1 if decision.get("no_order") else 0
        record.invalid_orders += int(decision.get("invalid_orders") or 0)
        if decision.get("overridden") is not None:
            record.override_opportunities += 1
            record.overrides += 1 if decision.get("overridden") else 0
        for spawn in decision.get("spawn_outcomes") or []:
            if spawn == "granted":
                record.spawn_granted += 1
            else:
                record.spawn_other.append(str(spawn))
        if decision.get("error"):
            record.errors.append(str(decision["error"]))

    for call in calls:
        level = call.level or "unknown"
        bucket = record.tokens_by_level.setdefault(
            level, {"prompt": 0, "completion": 0, "total": 0}
        )
        bucket["prompt"] += call.prompt_tokens
        bucket["completion"] += call.completion_tokens
        bucket["total"] += call.prompt_tokens + call.completion_tokens
        record.calls_by_level[level] = record.calls_by_level.get(level, 0) + 1
        record.seconds_by_level[level] = round(
            record.seconds_by_level.get(level, 0.0) + call.seconds, 3
        )
        reason = call.finish_reason or "none"
        record.finish_reasons[reason] = record.finish_reasons.get(reason, 0) + 1
        record.length_finishes += 1 if call.finish_reason == "length" else 0
        record.retries += call.retries
        record.contention_calls += 1 if call.contention else 0


# ── the series ──────────────────────────────────────────────────────────────


def build_live_minds(
    arm: str,
    *,
    base_url: str,
    api_key: str,
    sink: Callable[[CallRecord], None],
    context: dict[str, Any],
    transcript: Optional[Callable[[dict[str, Any]], None]],
) -> Callable[[dict[str, Any]], Minds]:
    commander_model, unit_model = ARM_MODELS[arm]

    def factory(_briefing: dict[str, Any]) -> Minds:
        commander = None
        if commander_model:
            commander = gateway_seam(
                base_url,
                commander_model,
                api_key,
                tools=COMMANDER_TOOLS,
                sink=sink,
                context=context,
                transcript=transcript,
            )
        unit = gateway_seam(
            base_url,
            unit_model,
            api_key,
            tools=UNIT_TOOLS if commander_model else FLAT_TOOLS,
            sink=sink,
            context=context,
            transcript=transcript,
        )
        return Minds(
            commander=commander,
            unit=unit,
            commander_model=commander_model or "",
            unit_model=unit_model,
        )

    return factory


def build_scripted_minds(
    arm: str,
    *,
    sink: Callable[[CallRecord], None],
    context: dict[str, Any],
) -> Callable[[dict[str, Any]], Minds]:
    commander_model, unit_model = ARM_MODELS[arm]

    def factory(briefing: dict[str, Any]) -> Minds:
        options = len(briefing.get("menu") or [])
        commander = None
        if commander_model:
            # Picks the LAST entry where the unit picks the first, so the
            # scripted hierarchy exercises the override path rather than
            # agreeing by construction.
            commander = scripted_seam(
                "consult_then_order",
                options=options,
                pick=lambda n: max(0, n - 1),
                sink=sink,
                context=context,
                model=commander_model,
            )
        unit = scripted_seam(
            "propose" if commander_model else "order",
            options=options,
            pick=lambda _n: 0,
            sink=sink,
            context=context,
            model=unit_model,
        )
        return Minds(
            commander=commander,
            unit=unit,
            commander_model=commander_model or "",
            unit_model=unit_model,
        )

    return factory


def completed_keys(path: Path) -> set[str]:
    """Which matches this ledger already holds — a killed series resumes."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == "match":
            keys.add(str(record.get("match_key") or ""))
    return keys


def append_line(path: Optional[Path], payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def run_series(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cli = LeagueCli(root=root, binary=args.league)
    out = Path(args.out).expanduser().resolve() if args.out else None
    transcripts = Path(args.transcripts).expanduser().resolve() if args.transcripts else None
    logs_dir = Path(args.logs).expanduser().resolve() if args.logs else None
    done = completed_keys(out) if out else set()

    api_key = ""
    if args.live:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
            print(
                f"hint: export {API_KEY_ENV}=… and point --base-url at your gateway",
                file=sys.stderr,
            )
            return 2

    arms = list(ARMS) if args.arm == "all" else [args.arm]
    scenario = args.scenario
    n_matches = args.n

    if args.config_out:
        _write_config(Path(args.config_out).expanduser().resolve(), args, arms, scenario, n_matches)

    calls: list[CallRecord] = []
    context: dict[str, Any] = {}

    def sink(record: CallRecord) -> None:
        calls.append(record)

    def write_transcript(payload: dict[str, Any]) -> None:
        append_line(transcripts, payload)

    transcript_sink = write_transcript if transcripts is not None else None

    summaries: list[MatchRecord] = []
    for arm in arms:
        commander_model, unit_model = ARM_MODELS[arm]
        register_teams(cli, arm, scenario, unit_model)
        build_minds = (
            build_live_minds(
                arm,
                base_url=args.base_url,
                api_key=api_key,
                sink=sink,
                context=context,
                transcript=transcript_sink,
            )
            if args.live
            else build_scripted_minds(arm, sink=sink, context=context)
        )
        for index in range(n_matches):
            match_key = f"{arm}-{index}"
            if match_key in done:
                print(f"skip {match_key} (already in the ledger)", file=sys.stderr)
                continue
            record = play_match(
                cli=cli,
                arm=arm,
                match_index=index,
                scenario=scenario,
                seed=SEEDS[index % len(SEEDS)],
                build_minds=build_minds,
                context=context,
                calls=calls,
                commander_model=commander_model or "",
                unit_model=unit_model,
            )
            summaries.append(record)
            append_line(out, record.to_dict())
            if logs_dir is not None:
                _copy_log(root, record, logs_dir)
            print(
                f"{match_key}: {record.blue_points}-{record.red_points} "
                f"(margin {record.margin:+d}, winner {record.winner}, "
                f"{record.decisions} decisions, {record.wall_seconds}s)",
                file=sys.stderr,
            )

    print(json.dumps({"matches": [r.to_dict() for r in summaries]}, sort_keys=True, default=str))
    return 0


def _copy_log(root: Path, record: MatchRecord, logs_dir: Path) -> None:
    """The arena's OWN artifact, committed beside our report."""
    source = root / ".league" / "matches" / record.match_id / "log.jsonl"
    if not source.exists():
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, logs_dir / f"{record.match_key}.jsonl")


def _write_config(
    path: Path,
    args: argparse.Namespace,
    arms: list[str],
    scenario: str,
    n_matches: int,
) -> None:
    payload = {
        "task": "t28",
        "issue": 36,
        "written": time.strftime("%Y-%m-%d"),
        "live": bool(args.live),
        "base_url": args.base_url,
        "arms": arms,
        "arm_models": {a: list(ARM_MODELS[a]) for a in arms},
        "scenario": scenario,
        "scenario_roles": list(SCENARIO_ROLES[scenario]),
        "opponent_driver": OPPONENT_DRIVER,
        "our_driver": OUR_DRIVER,
        "n_matches": n_matches,
        "seeds": list(SEEDS[:n_matches]),
        "seed_is_metadata_only": True,
        "spawn_allowance": SPAWN_ALLOWANCE,
        "commander_max_steps": COMMANDER_MAX_STEPS,
        "unit_max_steps": UNIT_MAX_STEPS,
        "flat_max_steps": FLAT_MAX_STEPS,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "retry_wait_seconds": RETRY_WAIT_SECONDS,
        "contention_seconds": CONTENTION_SECONDS,
        "controls": {
            "synthesis": CONTROLS.synthesis,
            "incompletion": CONTROLS.incompletion,
            "write_intent": CONTROLS.write_intent,
        },
        "roles": {"top_level": ROLE_CORTEX, "child": ROLE_SUBAGENT},
        "identity": None,
        "thresholds": {
            "margin_effect": MARGIN_EFFECT,
            "no_effect_band": NO_EFFECT_BAND,
            "direction_min": DIRECTION_MIN,
            "grade_effect": GRADE_EFFECT,
            "length_fraction_max": LENGTH_FRACTION_MAX,
            "no_order_fraction_max": NO_ORDER_FRACTION_MAX,
            "spawn_grant_min_fraction": SPAWN_GRANT_MIN_FRACTION,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ── the pre-registered fold ─────────────────────────────────────────────────


def load_matches(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == "match":
            records.append(record)
    return records


def arm_summary(arm: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
    """One arm's numbers, plus the validity gates applied to them."""
    mine = [m for m in matches if m.get("arm") == arm]
    if not mine:
        return {"arm": arm, "verdict": VERDICT_ABSENT, "matches": 0}

    margins = [int(m.get("margin") or 0) for m in mine]
    grades = [float(m.get("blue_grade") or 0.0) for m in mine]
    decisions = sum(int(m.get("decisions") or 0) for m in mine)
    no_order = sum(int(m.get("no_order") or 0) for m in mine)
    spawn_granted = sum(int(m.get("spawn_granted") or 0) for m in mine)
    calls_by_level: dict[str, int] = {}
    tokens_by_level: dict[str, dict[str, int]] = {}
    seconds_by_level: dict[str, float] = {}
    finish_reasons: dict[str, int] = {}
    for match in mine:
        for level, count in (match.get("calls_by_level") or {}).items():
            calls_by_level[level] = calls_by_level.get(level, 0) + int(count)
        for level, bucket in (match.get("tokens_by_level") or {}).items():
            target = tokens_by_level.setdefault(level, {"prompt": 0, "completion": 0, "total": 0})
            for key in ("prompt", "completion", "total"):
                target[key] += int(bucket.get(key) or 0)
        for level, seconds in (match.get("seconds_by_level") or {}).items():
            seconds_by_level[level] = round(seconds_by_level.get(level, 0.0) + float(seconds), 3)
        for reason, count in (match.get("finish_reasons") or {}).items():
            finish_reasons[reason] = finish_reasons.get(reason, 0) + int(count)

    total_calls = sum(calls_by_level.values())
    length_finishes = int(finish_reasons.get("length", 0))
    length_fraction = (length_finishes / total_calls) if total_calls else 0.0
    no_order_fraction = (no_order / decisions) if decisions else 0.0
    grant_fraction = (spawn_granted / decisions) if decisions else 0.0

    void_reasons: list[str] = []
    if length_fraction > LENGTH_FRACTION_MAX:
        void_reasons.append(VOID_TRUNCATED)
    if no_order_fraction > NO_ORDER_FRACTION_MAX:
        void_reasons.append(VOID_DEGRADED)
    if arm in HIERARCHICAL_ARMS and grant_fraction < SPAWN_GRANT_MIN_FRACTION:
        void_reasons.append(VOID_NOT_HIERARCHICAL)

    overrides = sum(int(m.get("overrides") or 0) for m in mine)
    opportunities = sum(int(m.get("override_opportunities") or 0) for m in mine)
    return {
        "arm": arm,
        "verdict": VERDICT_VOID if void_reasons else "",
        "void_reasons": void_reasons,
        "matches": len(mine),
        "margins": margins,
        "mean_margin": round(statistics.fmean(margins), 3),
        "median_margin": statistics.median(margins),
        "wins": sum(1 for m in mine if m.get("winner") == m.get("team_id")),
        "losses": sum(1 for m in mine if m.get("winner") not in (None, m.get("team_id"))),
        "draws": sum(1 for m in mine if m.get("winner") is None),
        "blue_grades": grades,
        "mean_grade": round(statistics.fmean(grades), 3),
        "median_grade": statistics.median(grades),
        "decisions": decisions,
        "no_order": no_order,
        "no_order_fraction": round(no_order_fraction, 4),
        "spawn_granted": spawn_granted,
        "spawn_grant_fraction": round(grant_fraction, 4),
        "overrides": overrides,
        "override_opportunities": opportunities,
        "override_rate": round(overrides / opportunities, 4) if opportunities else None,
        "calls_by_level": calls_by_level,
        "tokens_by_level": tokens_by_level,
        "seconds_by_level": seconds_by_level,
        "finish_reasons": finish_reasons,
        "length_fraction": round(length_fraction, 4),
        "retries": sum(int(m.get("retries") or 0) for m in mine),
        "contention_calls": sum(int(m.get("contention_calls") or 0) for m in mine),
        "commander_model": mine[0].get("commander_model") or "",
        "unit_model": mine[0].get("unit_model") or "",
    }


def compare(
    high: dict[str, Any],
    low: dict[str, Any],
    *,
    key: str = "mean_margin",
    values_key: str = "margins",
    median_key: str = "median_margin",
    effect: float = MARGIN_EFFECT,
    band: float = NO_EFFECT_BAND,
) -> dict[str, Any]:
    """The pre-registered rule, applied without amendment."""
    if not high.get("matches") or not low.get("matches"):
        return {"verdict": VERDICT_ABSENT, "reason": "an arm did not run"}
    if high.get("void_reasons") or low.get("void_reasons"):
        return {
            "verdict": VERDICT_VOID,
            "reason": "a validity gate failed",
            "void": {high["arm"]: high.get("void_reasons"), low["arm"]: low.get("void_reasons")},
        }
    delta = float(high[key]) - float(low[key])
    if delta < 0:
        high, low = low, high
        delta = -delta
    threshold = float(low[median_key])
    direction = sum(1 for value in high[values_key] if float(value) >= threshold)
    required = direction_required(int(high["matches"]))
    identical = sorted(high[values_key]) == sorted(low[values_key])
    verdict = VERDICT_INCONCLUSIVE
    if delta >= effect and direction >= required:
        verdict = VERDICT_EFFECT
    elif delta < band and not identical:
        verdict = VERDICT_NO_EFFECT
    return {
        "verdict": verdict,
        "higher": high["arm"],
        "lower": low["arm"],
        "delta": round(delta, 3),
        "direction_matches": direction,
        "direction_required": required,
        "identical_sets": identical,
        "threshold": effect,
    }


def analyse(args: argparse.Namespace) -> int:
    path = Path(args.out).expanduser().resolve()
    matches = load_matches(path)
    arms = {arm: arm_summary(arm, matches) for arm in ARMS}

    ran = [a for a in ARMS if arms[a].get("matches")]
    absent = [a for a in ARMS if not arms[a].get("matches")]

    def best(candidates: list[str], key: str) -> Optional[dict[str, Any]]:
        live = [arms[a] for a in candidates if arms[a].get("matches")]
        if not live:
            return None
        return max(live, key=lambda s: float(s[key]))

    report: dict[str, Any] = {
        "task": "t28",
        "ledger": str(path),
        "arms_run": ran,
        "arms_absent": absent,
        "summaries": arms,
    }

    best_hier = best(list(HIERARCHICAL_ARMS), "mean_margin")
    best_flat = best(list(FLAT_ARMS), "mean_margin")
    report["h1_hierarchy_vs_flat"] = (
        compare(best_hier, best_flat)
        if best_hier and best_flat
        else {"verdict": VERDICT_ABSENT, "reason": "a lane did not run"}
    )
    report["h2_gemma_vs_qwen_commander"] = (
        compare(arms[ARM_B], arms[ARM_C])
        if arms[ARM_B].get("matches") and arms[ARM_C].get("matches")
        else {"verdict": VERDICT_ABSENT, "reason": "an arm did not run"}
    )
    if best_hier and best_flat:
        report["e3_secondary_grade"] = compare(
            best_hier,
            best_flat,
            key="mean_grade",
            values_key="blue_grades",
            median_key="median_grade",
            effect=GRADE_EFFECT,
            band=GRADE_EFFECT / 3.0,
        )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="Play the series.")
    play.add_argument("--root", required=True, help="Working directory for the .league store.")
    play.add_argument("--arm", default="all", choices=("all", *ARMS))
    play.add_argument("--scenario", default=SCENARIO, choices=tuple(SCENARIO_ROLES))
    play.add_argument("--n", type=int, default=N_MATCHES)
    play.add_argument("--live", action="store_true", help="Dial the real gateway.")
    play.add_argument("--base-url", default=DEFAULT_BASE_URL)
    play.add_argument("--league", default="league", help="The league binary.")
    play.add_argument("--out", default="", help="Ledger JSONL (append-only, resumable).")
    play.add_argument("--transcripts", default="", help="Raw per-call transcript JSONL.")
    play.add_argument("--logs", default="", help="Directory to copy each match log into.")
    play.add_argument("--config-out", default="", help="Write the full configuration record.")
    play.set_defaults(func=run_series)

    fold = sub.add_parser("analyse", help="Apply the pre-registered rule to the ledger.")
    fold.add_argument("--out", required=True, help="Ledger JSONL written by `play`.")
    fold.set_defaults(func=analyse)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
