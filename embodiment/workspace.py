"""A bounded execution workspace, wired as the muse's second thinking tool (task t13).

:mod:`embodiment.muse_pad` gave the thinking lane somewhere to *write*. This
gives it somewhere to *check*: one command, in a container that reaches nothing,
with the result folded back as text the muse can think about. The premise is
issue #21's — a muse that cannot execute anything has to pretend to be a
calculator, and five of its six measured failures stated a definite wrong number
(``docs/live-test-results/association-work.md``, n=9 per cell). Offloading the
arithmetic is the hypothesis; that it displaces reasoning instead is the
counter-hypothesis, and task t18 measures which one happened. Nothing here
decides that — this module only makes the arm buildable.

The reach is the whole control
------------------------------
**The workspace reaches no repository, no eidetic or coherence store, and no
network.** That sentence is the load-bearing safety property of the muse-tools
lane, so it is held by construction rather than by intent, in four places that
have nothing to do with this docstring:

1. **No policy is ever constructed or passed.** :func:`headspace.api.create` is
   called with ``provider=`` and — only when a host named one — ``workspace_id=``.
   Nothing else. headspace's own posture is closed by default (network disabled,
   no host paths, 512 MiB / 1 CPU / 128 pids / 300 s), and because this module
   never builds a ``Policy`` there is no configuration path along which that
   default could be widened from here.
2. **No bytes cross in or out.** ``headspace.api`` also declares ``put`` and
   ``export``; both take a *host path*, and neither is called here. A test walks
   this module's AST and fails if either name ever appears at a call site.
3. **The tool schema has exactly one parameter** — the argv to run. There is no
   path parameter, no mount parameter, no environment parameter, so the muse
   cannot name a host location even in principle.
4. **No environment is forwarded** — see the next section.

The empirical half is a live probe rather than an assertion:
``tests/test_workspace.py`` runs a real workspace under
``EMBODIMENT_LIVE_RIG=1``, checks that only the loopback interface exists inside
it, and fails a connect to the docker-bridge gateway on the lobes, neo4j and
mongo ports with ``OSError: [Errno 101] Network is unreachable``. The gateway is
discovered from ``ip route``, never hardcoded.

No secrets, and no way to add them
----------------------------------
headspace has an environment channel (``run(environment=JobEnvironment(...))``,
``headspace run --env`` / ``--env-file``): values reach the job, only names are
recorded. It is operator tooling and **the muse tool seam does not expose it at
all** (claim ``c34``). The reason is a composition, not a hypothetical: a job
that prints its environment leaks it into captured output, captured output is
folded into the text the muse reads, muse text becomes counsel, and counsel can
reach a *public* eidetic record — which, inside a git repo, is committed and
travels with every clone.

So there is no ``secrets``/``env`` parameter anywhere on this module's public
surface, no call site that passes ``environment=``, and no schema property a
model could fill in. The leak path is not closed by policy; it is absent.

Failure is reported, never raised
---------------------------------
Constraint **C3**: a host must never watch an attentive-looking muse whose tools
have all been failing. :meth:`MuseWorkspace.execute` therefore **never raises**
for an engine problem — a missing or unreachable container daemon returns
readable text carrying :data:`ENGINE_HINT` and records a
:class:`WorkspaceDegradation` on :attr:`~MuseWorkspace.degradations`. The muse
reads the text as an ordinary tool result and keeps thinking; the host reads the
record and knows the lane degraded.

One name *is* refused by raising: a tool this module does not offer. That is a
broken tool-call protocol rather than a failing tool, it is
:class:`~embodiment.loop.UnknownToolError`'s exact purpose, and
:mod:`embodiment.muse` turns the raise into a recorded
:data:`~embodiment.muse.DEGRADED_TOOL` plus a corrective message — the same
treatment :class:`embodiment.muse_pad.MusePad` gives a hallucinated ``finish``.

These codes are this lane's own and are **not** folded into
:func:`embodiment.ledger.read` today, following
:class:`embodiment.recall_bundle.BundleDegradation`'s precedent: a host reads
them off the object it constructed. Task t15 owns the workspace lifecycle
(bounded teardown at runner close, a degradation naming a workspace left live)
and is where that fold belongs if it belongs anywhere — :meth:`destroy` here is
the plain, unbounded version it will replace.

Which import surface, and why only that one
-------------------------------------------
``headspace.api`` is the **only** supported import surface of headspace-cli. It
declares exactly ``create``, ``run``, ``put``, ``export``, ``destroy`` under
headspace's own semver promise; ``headspace.core`` is private and its
maintainer said so explicitly (headspace-cli#18, answered by 0.11.0). So nothing
here imports ``headspace.core`` — not for ``Policy``, not for ``ResultPackage``,
not for the renderers. The result package is read **duck-typed** through its
documented nine-section field names, which is why :func:`_render` uses
``getattr`` rather than a type.

That import is at module scope, deliberately: measured, ``import headspace.api``
pulls **stdlib only** — no ``docker``, no ``requests`` — so it adds exactly one
third-party top-level module to ``tests/test_zero_deps.py``'s
``_REQUIRED_RUNTIME_IMPORTS`` while ``docker`` stays install-only, exactly as
``neo4j`` and ``pymongo`` already do. ``import embodiment`` alone still costs
nothing, because this module is reached lazily like every other.

Wiring one up::

    workspace = MuseWorkspace()                      # docker, closed by default
    loop = MuseLoop(complete, tools=workspace.bench(tool_complete))
    outcome = loop.think(boundary)
    transcript["workspace"] = workspace.counts().to_dict()
    workspace.destroy()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import headspace.api

from embodiment.muse import MuseToolBench, MuseToolCompleteFn

__all__ = [
    # the lane
    "WORKSPACE_LANE",
    "PROVIDER_DOCKER",
    "PROVIDER_FAKE",
    "NO_REACH",
    "ENGINE_HINT",
    # the protocol
    "WORKSPACE_TOOL_NAME",
    "WORKSPACE_TOOLS",
    "WORKSPACE_TOOL_NAMES",
    "WORKSPACE_ADDENDUM",
    "WORKSPACE_PROTOCOL",
    # degradation vocabulary (C3)
    "STAGE_CREATE",
    "STAGE_RUN",
    "STAGE_DESTROY",
    "DEGRADED_ENGINE_UNAVAILABLE",
    "DEGRADED_RUN_FAILED",
    "DEGRADED_UNREADABLE_RESULT",
    "DEGRADED_DESTROY_FAILED",
    "WORKSPACE_CODES",
    # shapes
    "MuseWorkspace",
    "WorkspaceCounts",
    "WorkspaceDegradation",
]


# ── the lane ──────────────────────────────────────────────────────────────────

#: What every reported surface labels this lane with, so a reader never has to
#: infer whether a record came from the pad or from the workspace.
WORKSPACE_LANE = "workspace"

#: headspace's two backends, named here so a caller never has to import them
#: from ``headspace.core`` (private) or spell a literal. ``docker`` is the
#: product; ``fake`` is the in-memory backend that needs no daemon and is what
#: the CI-safe tests use.
PROVIDER_DOCKER = "docker"
PROVIDER_FAKE = "fake"

#: What the workspace deliberately cannot reach. Prose here, mechanism in the
#: module docstring's four numbered points — this tuple exists so the protocol
#: text and the tests quote one source instead of three paraphrases.
NO_REACH = (
    "the repository (no host path is ever mounted or copied in)",
    "the eidetic and coherence stores (no bytes cross in or out)",
    "the network (headspace's closed-by-default posture is never widened here)",
)

#: What to tell a host whose engine is missing. A degradation that names the
#: remedy costs nothing extra and is the difference between "it broke" and
#: "start the daemon".
ENGINE_HINT = (
    "start a container engine the headspace docker provider can reach, or "
    f"construct MuseWorkspace(provider={PROVIDER_FAKE!r}) for the in-memory "
    "backend that needs no daemon"
)


# ── the protocol ──────────────────────────────────────────────────────────────

#: Deliberately NOT ``run``: :data:`embodiment.muse_pad.MUSE_PAD_TOOLS` already
#: offers ``read``, and task t17's arm C wires the pad and the workspace on one
#: bench. Two benches sharing a name would make the composition ambiguous at
#: exactly the moment the experiment depends on knowing which tool was called.
WORKSPACE_TOOL_NAME = "workspace_run"

#: The schema put on the wire. **One tool, one parameter.** Every widening of
#: this schema is a widening of the muse's reach, so it is kept where a reviewer
#: reads it in one glance.
WORKSPACE_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": WORKSPACE_TOOL_NAME,
            "description": (
                "Run one command in a bounded, disposable workspace and read back what "
                "it printed. Use it to CHECK something you would otherwise have to "
                "assert from memory - arithmetic, a counterexample, an enumeration. "
                "The workspace has no network and no access to any repository or "
                "store, so the command must be self-contained; a python:3.12 image is "
                "available, e.g. ['python3', '-c', 'print(sum(range(10)))']."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The argv to execute, already split - no shell is "
                            "involved, so quoting and pipes will not work."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
)

#: The names :meth:`MuseWorkspace.execute` will honour. Every other name is
#: refused rather than guessed at.
WORKSPACE_TOOL_NAMES: tuple[str, ...] = (WORKSPACE_TOOL_NAME,)

#: What the muse is told about the workspace, beyond the schema. Kept separate
#: from :data:`WORKSPACE_PROTOCOL` so a host composing several tool protocols
#: appends one paragraph rather than re-deriving the whole text.
WORKSPACE_ADDENDUM = (
    "The workspace is disposable and isolated. It cannot reach:\n"
    + "".join(f"  - {item}\n" for item in NO_REACH)
    + "It receives no secrets and no environment variables, so nothing you run "
    "there can read a credential. Run a command when a check would settle a "
    "question you would otherwise have to assume the answer to; say what you "
    "expected before you run it, and what you actually got afterwards."
)

#: The workspace protocol as the muse receives it. Pass it as
#: ``MuseLoop(system=...)``, where it is APPENDED to
#: :data:`~embodiment.muse.MUSE_AUTHORITY` and never substituted for it.
WORKSPACE_PROTOCOL = (
    "You can run one command at a time in a bounded workspace.\n\n"
    f"  {WORKSPACE_TOOL_NAME}(command)  execute an argv and read back what it printed.\n\n"
    + WORKSPACE_ADDENDUM
)


# ── degradation vocabulary (C3) ───────────────────────────────────────────────

#: Provisioning the workspace.
STAGE_CREATE = "create"
#: Running one command in it.
STAGE_RUN = "run"
#: Tearing it down.
STAGE_DESTROY = "destroy"

#: No workspace could be provisioned: no container engine, an unreachable
#: daemon, or a policy the host cannot enforce. The muse is told so in words and
#: keeps thinking without one.
DEGRADED_ENGINE_UNAVAILABLE = "workspace-engine-unavailable"
#: A provisioned workspace could not run the command — the engine broke under
#: it. A command that ran and *failed* is not this: that is an ordinary result
#: with a failing status, and the muse reads it as evidence.
DEGRADED_RUN_FAILED = "workspace-run-failed"
#: A result package came back in a shape this module could not read.
DEGRADED_UNREADABLE_RESULT = "workspace-result-unreadable"
#: Teardown failed and the workspace may still exist. Named so a host can go and
#: look; task t15 owns bounding this path.
DEGRADED_DESTROY_FAILED = "workspace-destroy-failed"

#: This lane's whole vocabulary, in one tuple, so a host can enumerate it.
WORKSPACE_CODES: tuple[str, ...] = (
    DEGRADED_ENGINE_UNAVAILABLE,
    DEGRADED_RUN_FAILED,
    DEGRADED_UNREADABLE_RESULT,
    DEGRADED_DESTROY_FAILED,
)

#: Cap on one degradation's reason text, mirroring every sibling lane's cap.
_MAX_REASON_LEN = 500

#: Cap on the text one workspace result contributes to the muse's context.
#: Matches :attr:`embodiment.muse.MuseControls.max_tool_result_chars`'s default
#: so this module's own truncation — which names itself — happens first, and the
#: seam's blunter clip is left with nothing to do.
DEFAULT_MAX_RESULT_CHARS = 2000

_RESULT_TRUNCATED = "\n[... workspace result truncated]"


# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceDegradation:
    """One recorded, host-visible workspace degradation (constraint C3).

    Field-compatible with :class:`embodiment.recall_bundle.BundleDegradation`
    and :class:`embodiment.loop.LoopDegradation` — ``code`` then ``reason`` —
    so a host that already folds one shape folds this one.

    Fields
    ------
    code:
        The stable machine token, from :data:`WORKSPACE_CODES`. Branch on this,
        never on ``reason``.
    reason:
        Short human-readable cause, capped at 500 characters. Carries the
        engine's own message and remediation when it had one.
    stage:
        :data:`STAGE_CREATE`, :data:`STAGE_RUN` or :data:`STAGE_DESTROY`.
    workspace_id:
        The workspace this concerns, or ``""`` when none was ever provisioned —
        which is itself the fact a create failure reports.
    """

    code: str
    reason: str = ""
    stage: str = ""
    workspace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe row for a committed transcript."""
        return {
            "lane": WORKSPACE_LANE,
            "code": self.code,
            "reason": self.reason,
            "stage": self.stage,
            "workspace_id": self.workspace_id,
        }


@dataclass(frozen=True)
class WorkspaceCounts:
    """What the muse did with the workspace, read off a finished session.

    Deliberately counts rather than judges: *whether* the muse reached for
    execution, and what came back, are task t18's dependent variables — the
    arithmetic-offloaded versus reasoning-displaced classification is made from
    the transcript, never from a score invented here.

    Like :class:`embodiment.muse_pad.MusePadCounts`, none of this is a
    degradation: a muse that never ran a command is not embodiment breaking, and
    recording it in the stream a host reads to answer *"what went wrong?"* would
    cry wolf on every healthy run.

    Fields
    ------
    lane:
        Always :data:`WORKSPACE_LANE`.
    provider:
        The backend name this workspace was asked for.
    workspace_id:
        The provisioned id, or ``""`` when none was.
    created:
        Whether a workspace was ever provisioned.
    runs:
        Commands actually handed to the engine.
    statuses:
        One count per headspace status token observed (``success``,
        ``failure``, ``timeout``, …), in first-seen order.
    rejected_calls:
        Calls refused as unusable — an empty or non-list ``command``. The muse
        gets a corrective message and keeps going.
    off_protocol_calls:
        Calls naming a tool this lane does not offer. Counted here *and*
        recorded as a :data:`~embodiment.muse.DEGRADED_TOOL` by the thinking
        loop.
    degradations:
        How many :class:`WorkspaceDegradation` records this lane holds.
    """

    lane: str = WORKSPACE_LANE
    provider: str = PROVIDER_DOCKER
    workspace_id: str = ""
    created: bool = False
    runs: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    rejected_calls: int = 0
    off_protocol_calls: int = 0
    degradations: int = 0

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe row for a committed transcript."""
        return {
            "lane": self.lane,
            "provider": self.provider,
            "workspace_id": self.workspace_id,
            "created": self.created,
            "runs": self.runs,
            "statuses": dict(self.statuses),
            "rejected_calls": self.rejected_calls,
            "off_protocol_calls": self.off_protocol_calls,
            "degradations": self.degradations,
        }


# ── reading a result package without importing its type ───────────────────────


def _attr(obj: Any, name: str) -> Any:
    """One attribute, or ``None``. A hostile object degrades the read, not the run."""
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - a property on a foreign object runs arbitrary code
        return None


def _text(value: Any) -> str:
    """One value as a single-line-safe string, or ``""``. Never raises."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - __str__ on a foreign object runs arbitrary code
        return ""


def _lines(value: Any) -> list[str]:
    """One list-shaped section as strings, dropping what cannot be read."""
    if not isinstance(value, (list, tuple)):
        return []
    return [text for text in (_text(item) for item in value) if text]


def _excerpts(value: Any) -> list[str]:
    """The captured output off a result package's evidence section.

    ``Evidence`` is ``headspace.core``'s type and stays unimported: the label
    and excerpt are read off whatever object arrived.
    """
    found: list[str] = []
    if not isinstance(value, (list, tuple)):
        return found
    for item in value:
        excerpt = _text(_attr(item, "excerpt"))
        if not excerpt:
            continue
        label = _text(_attr(item, "label")) or "output"
        found.append(f"{label}:\n{excerpt}")
    return found


def _render(package: Any, cap: int) -> str:
    """One result package as the compact text the muse reads.

    Reads the documented nine-section field names duck-typed — status, summary,
    findings, evidence, warnings — rather than importing
    ``headspace.core.result``, which is private. Sections the package does not
    carry are simply absent; nothing is invented to fill a heading.

    The four sections dropped on purpose are ``artifacts`` (this lane declares
    none, so it is always empty), ``resource_usage`` and ``provenance`` (facts
    about the run, not about the answer — they belong in a host's transcript,
    not in a thinking context that is already budgeted) and ``attention`` (it
    asks for an operator decision, and the muse is not one).
    """
    blocks: list[str] = []
    status = _text(_attr(package, "status"))
    if status:
        blocks.append(f"status: {status}")
    summary = _text(_attr(package, "outcome_summary"))
    if summary:
        blocks.append(summary)
    for heading, items in (
        ("findings", _lines(_attr(package, "key_findings"))),
        ("warnings", _lines(_attr(package, "warnings"))),
    ):
        if items:
            blocks.append(heading + ":\n" + "\n".join(f"  - {item}" for item in items))
    blocks.extend(_excerpts(_attr(package, "evidence")))
    text = "\n".join(blocks).strip()
    if not text:
        return ""
    return text if cap <= 0 or len(text) <= cap else text[:cap] + _RESULT_TRUNCATED


def _failure_text(exc: Exception) -> str:
    """An engine failure as the muse reads it: what broke, and what fixes it.

    ``headspace.cli._errors.CliError`` carries ``message`` and ``remediation``;
    both are read duck-typed, so an ``ImportError`` from a host with no engine
    SDK at all renders just as legibly as a structured refusal.
    """
    message = _text(_attr(exc, "message")) or _text(exc) or exc.__class__.__name__
    remediation = _text(_attr(exc, "remediation"))
    return message + (f" ({remediation})" if remediation else "")


# ── the workspace ─────────────────────────────────────────────────────────────


class MuseWorkspace:
    """A bounded workspace and the bench it rides on.

    Composition over :mod:`headspace.api`, never a subclass of anything in it:
    headspace owns the lifecycle, this owns which of its five declared verbs a
    thinking lane can reach. Exactly one — ``run`` — is offered as a tool, with
    ``create`` and ``destroy`` around it as lifecycle this object drives itself.
    ``put`` and ``export`` are the two that take a *host path*, and neither is
    wired: that absence is the second of the four no-reach mechanisms.

    Provisioning is **lazy**: constructing this object talks to no engine, so a
    muse that never reaches for a command never pays for one, and a host on a
    machine with no daemon can still build the bench.

    Args:
        provider: :data:`PROVIDER_DOCKER` (the product) or :data:`PROVIDER_FAKE`
            (in-memory, no daemon — what the CI-safe tests use).
        workspace_id: a stable id to provision under, or ``None`` to let
            headspace mint one. A name, never a path.
        api: the headspace surface to drive. Defaults to :mod:`headspace.api`
            itself; injectable so a test can record the exact invocation this
            module constructs, which is how "no secrets reach a job" is proved
            rather than promised.
        max_result_chars: cap on the text one result contributes to the muse's
            context. ``0`` disables this module's own cap; the seam still caps.
    """

    def __init__(
        self,
        *,
        provider: str = PROVIDER_DOCKER,
        workspace_id: Optional[str] = None,
        api: Any = None,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        self._provider = str(provider)
        self._requested_id = str(workspace_id) if workspace_id else None
        self._api = api if api is not None else headspace.api
        self._cap = int(max_result_chars)
        self._workspace_id = ""
        self._degradations: list[WorkspaceDegradation] = []
        self._statuses: dict[str, int] = {}
        self._runs = 0
        self._rejected = 0
        self._off_protocol = 0

    # ── what it is ────────────────────────────────────────────────────────────

    @property
    def provider(self) -> str:
        """The backend this workspace was asked for."""
        return self._provider

    @property
    def workspace_id(self) -> str:
        """The provisioned workspace id, or ``""`` before anything was provisioned."""
        return self._workspace_id

    @property
    def schema(self) -> tuple[dict[str, Any], ...]:
        """The tool schema this lane offers — :data:`WORKSPACE_TOOLS`."""
        return WORKSPACE_TOOLS

    @property
    def degradations(self) -> tuple[WorkspaceDegradation, ...]:
        """Every recorded degradation, in the order it happened (constraint C3)."""
        return tuple(self._degradations)

    # ── the tool seam ─────────────────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run one workspace call. Satisfies :data:`~embodiment.muse.MuseToolExecuteFn`.

        Returns plain text — the seam reads a bare string directly, so no
        :class:`~embodiment.loop.ToolOutcome` wrapper is needed and the actor
        loop stays unimported.

        **Never raises for an engine problem.** A missing daemon, a broken
        engine or an unreadable result all come back as text the muse can think
        about, with the transition recorded on :attr:`degradations`.

        A tool name this lane does not offer *does* raise
        :class:`~embodiment.loop.UnknownToolError`: that is a broken tool-call
        protocol rather than a failing tool, and :mod:`embodiment.muse` turns it
        into a recorded :data:`~embodiment.muse.DEGRADED_TOOL` plus a corrective
        message the muse reads before carrying on.
        """
        if name not in WORKSPACE_TOOL_NAMES:
            self._off_protocol += 1
            from embodiment.loop import UnknownToolError

            raise UnknownToolError(
                f"this lane has no {name!r} tool; it offers {', '.join(WORKSPACE_TOOL_NAMES)}"
            )

        command = self._command_from(arguments)
        if command is None:
            self._rejected += 1
            return (
                "command must be a non-empty list of strings, already split — "
                "e.g. ['python3', '-c', 'print(2 + 2)']"
            )

        workspace_id = self._ensure_workspace()
        if not workspace_id:
            return (
                "no workspace is available, so nothing was run: "
                f"{self._last_reason()}. {ENGINE_HINT}"
            )

        self._runs += 1
        try:
            package = self._api.run(workspace_id, command, provider=self._provider)
        except Exception as exc:  # noqa: BLE001 - an engine failure is text, never a raise
            self._degrade(DEGRADED_RUN_FAILED, _failure_text(exc), STAGE_RUN, workspace_id)
            return f"the command could not be run: {_failure_text(exc)}"

        status = _text(_attr(package, "status"))
        if status:
            self._statuses[status] = self._statuses.get(status, 0) + 1
        rendered = _render(package, self._cap)
        if not rendered:
            self._degrade(
                DEGRADED_UNREADABLE_RESULT,
                "the result package carried no readable section",
                STAGE_RUN,
                workspace_id,
            )
            return "the command ran but its result could not be read"
        return rendered

    def bench(self, complete: MuseToolCompleteFn) -> MuseToolBench:
        """The bench to hand :class:`~embodiment.muse.MuseLoop` as ``tools=``.

        *complete* is the host's tool-carrying seam
        (:data:`~embodiment.muse.MuseToolCompleteFn`) — this module holds no
        endpoint, no model and no network, and never constructs one.

        The bench reaches the wire only on the **top-level** muse; a muse at
        subagent depth is handed nothing and the withholding is recorded by
        :mod:`embodiment.muse`, not here.
        """
        return MuseToolBench(schema=WORKSPACE_TOOLS, complete=complete, execute=self.execute)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def destroy(self) -> bool:
        """Tear the workspace down. Returns whether there was one to tear down.

        Never raises: teardown runs on a close path, and a close path that
        raises loses whatever was being closed. A failure is recorded under
        :data:`DEGRADED_DESTROY_FAILED` naming the workspace, so a host can go
        and look for what is left.

        This is the plain version. Bounding it — so a hung engine cannot park a
        host's close forever, the way ``muse_runner``'s ``_bounded_join``
        already refuses to — is task t15's, along with the record for a drive
        that ends with a workspace still live.
        """
        workspace_id = self._workspace_id
        if not workspace_id:
            return False
        try:
            self._api.destroy(workspace_id, provider=self._provider)
        except Exception as exc:  # noqa: BLE001 - a close path never raises
            self._degrade(DEGRADED_DESTROY_FAILED, _failure_text(exc), STAGE_DESTROY, workspace_id)
            return False
        self._workspace_id = ""
        return True

    # ── what it can answer about itself ───────────────────────────────────────

    def counts(self) -> WorkspaceCounts:
        """What the muse did with the workspace — read it when a session returns."""
        return WorkspaceCounts(
            provider=self._provider,
            workspace_id=self._workspace_id,
            created=bool(self._workspace_id),
            runs=self._runs,
            statuses=dict(self._statuses),
            rejected_calls=self._rejected,
            off_protocol_calls=self._off_protocol,
            degradations=len(self._degradations),
        )

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _command_from(arguments: Any) -> Optional[list[str]]:
        """The argv off one call, or ``None`` when it is unusable.

        A string is refused rather than split: splitting it here would invent a
        quoting rule no shell is going to honour, and the schema says "already
        split" for exactly that reason.

        Blank and whitespace-only entries are dropped rather than forwarded. A
        model padding an argv with ``""`` is a common, harmless malformation,
        and an argv that is *only* blanks is refused as unusable — the surviving
        entries keep their own text untouched, so a deliberate leading newline
        inside a ``-c`` payload survives.
        """
        raw = arguments.get("command") if isinstance(arguments, dict) else None
        if not isinstance(raw, (list, tuple)):
            return None
        command = [text for text in (_text(item) for item in raw) if text.strip()]
        return command or None

    def _ensure_workspace(self) -> str:
        """Provision on first use; return the id, or ``""`` when none could be.

        The create call is the first of the four no-reach mechanisms and is
        deliberately tiny: ``provider=``, plus ``workspace_id=`` only when a
        host named one. **No ``policy``**, so headspace's own closed-by-default
        posture — network disabled, no host paths, small budgets — is the only
        posture reachable from here. And no ``profile``, so the pinned default
        image is the only image.
        """
        if self._workspace_id:
            return self._workspace_id
        try:
            if self._requested_id is None:
                package = self._api.create(provider=self._provider)
            else:
                package = self._api.create(provider=self._provider, workspace_id=self._requested_id)
        except Exception as exc:  # noqa: BLE001 - a missing engine is text, never a raise
            self._degrade(DEGRADED_ENGINE_UNAVAILABLE, _failure_text(exc), STAGE_CREATE, "")
            return ""
        workspace_id = _text(_attr(_attr(package, "provenance"), "workspace_id"))
        if not workspace_id:
            self._degrade(
                DEGRADED_ENGINE_UNAVAILABLE,
                "the engine provisioned a workspace but reported no workspace id",
                STAGE_CREATE,
                "",
            )
            return ""
        self._workspace_id = workspace_id
        return workspace_id

    def _degrade(self, code: str, reason: str, stage: str, workspace_id: str) -> None:
        """Record one host-visible transition. The only way this lane reports harm."""
        self._degradations.append(
            WorkspaceDegradation(
                code=code,
                reason=reason[:_MAX_REASON_LEN],
                stage=stage,
                workspace_id=workspace_id,
            )
        )

    def _last_reason(self) -> str:
        """The most recent degradation's reason, for the text the muse reads."""
        return self._degradations[-1].reason if self._degradations else "no engine was reachable"
