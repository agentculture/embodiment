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
:func:`embodiment.ledger.read`, following
:class:`embodiment.recall_bundle.BundleDegradation`'s precedent: a host reads
them off the object it constructed. Task t15 was where that fold would have
belonged if it belonged anywhere, and the decision recorded there is **not to
fold**. The ledger gathers the lanes embodiment *drives* — the loop, the muse,
the runner, continuity, events. A workspace is constructed by the host and
closed by the host (see the lifecycle section below), so it is read off the
object the host owns, exactly as :class:`embodiment.muse_pad.MusePadCounts` is.
Folding it would put a lane embodiment does not own the lifetime of into the
stream that answers *"what did embodiment do?"*.

The lifecycle: who closes this, and what a bounded teardown can promise
----------------------------------------------------------------------
**The host owns the destroy call**, because the host is the only party that
constructed the workspace and wired its :meth:`~MuseWorkspace.bench` onto a
thinking loop. :class:`~embodiment.muse_runner.ThreadedMuseRunner` has no tool
surface at all — no ``tools`` parameter, no bench, and a module docstring that
pins it to stdlib imports — so a runner that destroyed workspaces would have to
import this module, drag ``headspace`` into the thread lane's import graph, and
learn a vocabulary it has no other use for. Ownership follows construction.

What embodiment owns is the part a host cannot get right on its own:

* :meth:`MuseWorkspace.close` is **bounded**, mirroring
  :func:`embodiment.muse_runner._bounded_join`'s discipline — the engine call
  runs on a daemon thread and is joined with a timeout, so a hung container
  daemon delays a drive's teardown by at most that bound.
* The daemon-thread *escape* that makes the runner's join safe is **not
  available here**. An unreapable thread stops mattering when the process
  exits; an unreaped container does not. So where the runner shrugs, this lane
  **records**: a workspace that survives its close is named — id, provider and
  the exact commands that reap it — under :data:`DEGRADED_WORKSPACE_LIVE`. A
  leaked container nobody can name is the worst outcome available, and it is
  the one thing this design refuses.

* :meth:`~MuseWorkspace.close` also **latches the lane shut**, so a muse turn
  that arrives after teardown (the bounded join means the thinking thread can
  outlive the close) cannot provision a *second* workspace nothing will ever
  reap. It is refused in text and recorded under :data:`DEGRADED_LANE_CLOSED`.
  The latch is read on the near side of the run as well as on the way in,
  because a turn can be *inside* :meth:`~MuseWorkspace.execute` when the close
  lands — one thread's check and another's close are not one act.

And a workspace *will* sometimes survive, through no fault of this module.
Measured on headspace 0.11.0: ``destroy`` refuses a workspace whose job is still
running (*"illegal lifecycle transition: 'running' -> 'destroyed'"*), and the
``stop`` that clears it is deliberately **outside** the supported
``headspace.api`` surface — ``create``, ``run``, ``put``, ``export``,
``destroy`` — because it has to run in a different process from the ``run`` it
interrupts (headspace-cli#18). Through the one supported import surface, a
workspace with a job in flight cannot be reaped from inside this process at all.
And because the actor never waits on the muse (``d1``), *"the drive ended while
a thinking session still had a command running"* is the **ordinary** case, not
an edge one.

Three options, two rejected:

1. **Wait for the job, bounded.** headspace's default wall clock is 300s, so a
   bounded wait mostly expires rather than succeeds — and one long enough to
   work would stall every drive end, which is ``d1``'s whole objection.
2. **Shell out to the CLI's ``stop``.** Mixing an import and a subprocess for
   one lifecycle, having taken this dependency under ``d2`` *specifically* to
   stop subprocessing.
3. **Attempt the teardown, and when it is refused, hand the operator the id and
   the commands that finish the job.** What ships.

That makes the record's *content* load-bearing rather than decorative, which is
why :func:`_reap_command` and :data:`REAP_NOTE` are built from measurements and
why an ``EMBODIMENT_LIVE_RIG`` test runs the recorded remedy and asserts it
reaps. A first draft chained the two commands with ``&&`` and would have left
every operator who trusted it holding the container: ``stop --apply`` exits **5**
on success. The relaxation is asked for in headspace-cli#22; this is built for
today's surface, not that one.

A host that wants that teardown tied to the muse lane's close wires it in one
argument — ``ThreadedMuseRunner(complete, closers=(workspace.close,))`` — which
is an opaque zero-argument callable to the runner and stays so. The total
default close budget is then ``DEFAULT_JOIN_TIMEOUT`` (1.0s) plus
:data:`DEFAULT_DESTROY_TIMEOUT` (2.0s): three seconds, paid once at drive end,
never on the actor's hot path (deviation ``d1``).

One budget is **advisory rather than enforced**, and saying so is the point:
under the default local volume driver headspace *measures* a workspace's
storage and warns, but does not cap it, so a runaway write is reported and not
stopped. Nothing here can change that — an enforced quota is a property of the
volume driver, not of a caller — so what this lane does instead is refuse to
swallow the warning: :func:`_render` carries the result package's ``warnings``
section into the text the muse reads, and a host's transcript keeps it. The CPU,
memory, pid and wall-clock bounds are headspace's own closed defaults and are
enforced.

``destroy(force=…)`` is deliberately never passed, so headspace's own default —
refuse rather than discard un-exported declared artifacts — is the only reachable
policy. The refusal cannot fire from here anyway: ``run`` is never called with
``declares``, so this lane declares no artifacts. Passing ``force=True`` would
pre-authorise discarding artifacts a *future* change might declare, which is
exactly the kind of standing permission the no-reach mechanisms exist to avoid.

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

    with MuseWorkspace() as workspace:                # docker, closed by default
        loop = MuseLoop(complete, tools=workspace.bench(tool_complete))
        outcome = loop.think(boundary)
        transcript["workspace"] = workspace.counts().to_dict()
    # closed: teardown bounded, anything left live named on `degradations`

…or, tied to the muse lane's own close::

    workspace = MuseWorkspace()
    with ThreadedMuseRunner(complete, closers=(workspace.close,)) as muse:
        ...
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

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
    "STAGE_CLOSE",
    "DEGRADED_ENGINE_UNAVAILABLE",
    "DEGRADED_RUN_FAILED",
    "DEGRADED_UNREADABLE_RESULT",
    "DEGRADED_DESTROY_FAILED",
    "DEGRADED_DESTROY_TIMEOUT",
    "DEGRADED_WORKSPACE_LIVE",
    "DEGRADED_LANE_CLOSED",
    "WORKSPACE_CODES",
    # lifecycle
    "DEFAULT_DESTROY_TIMEOUT",
    "TEARDOWN_THREAD_NAME",
    "CLOSED_TEXT",
    "LIVE_WORKSPACE_HINT",
    "REAP_NOTE",
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
#: Ending the lane — the drive is over and nothing more will be provisioned.
#: Distinct from :data:`STAGE_DESTROY`, which is one teardown attempt: a close
#: *contains* a destroy, and only the close can say what survived it.
STAGE_CLOSE = "close"

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
#: Teardown was attempted and the engine refused it. The workspace may still
#: exist; the record names it.
DEGRADED_DESTROY_FAILED = "workspace-destroy-failed"
#: Teardown did not finish inside its bound. Deliberately a *different* code
#: from :data:`DEGRADED_DESTROY_FAILED`: a refusal is an answer, a timeout is
#: the absence of one, and the remediations differ — read the engine's message
#: versus go and look at whether the daemon is wedged. The teardown thread is a
#: daemon and may yet succeed after this fires, which is why the reason says the
#: workspace *may* still exist rather than claiming it does.
DEGRADED_DESTROY_TIMEOUT = "workspace-destroy-timeout"
#: The lane closed and a workspace survived it. **This is the record an operator
#: greps for**: it names the id, the provider and the exact command that reaps
#: it, whatever the cause was. It is emitted *beside* the cause record
#: (:data:`DEGRADED_DESTROY_FAILED` or :data:`DEGRADED_DESTROY_TIMEOUT`), never
#: instead of it — one answers "what do I have to clean up?", the other "why".
DEGRADED_WORKSPACE_LIVE = "workspace-left-live"
#: A tool call reached a closed lane. Reachable because the muse's thread is
#: joined with a *bound*, so a thinking session can outlive the close that
#: tore its workspace down. Nothing is provisioned — a second container minted
#: after the drive ended is precisely the leak this lane exists to prevent — and
#: the muse is told so in words.
DEGRADED_LANE_CLOSED = "workspace-lane-closed"

#: This lane's whole vocabulary, in one tuple, so a host can enumerate it.
WORKSPACE_CODES: tuple[str, ...] = (
    DEGRADED_ENGINE_UNAVAILABLE,
    DEGRADED_RUN_FAILED,
    DEGRADED_UNREADABLE_RESULT,
    DEGRADED_DESTROY_FAILED,
    DEGRADED_DESTROY_TIMEOUT,
    DEGRADED_WORKSPACE_LIVE,
    DEGRADED_LANE_CLOSED,
)

#: Cap on one degradation's reason text, mirroring every sibling lane's cap.
_MAX_REASON_LEN = 500

#: Cap on the text one workspace result contributes to the muse's context.
#: Matches :attr:`embodiment.muse.MuseControls.max_tool_result_chars`'s default
#: so this module's own truncation — which names itself — happens first, and the
#: seam's blunter clip is left with nothing to do.
DEFAULT_MAX_RESULT_CHARS = 2000

_RESULT_TRUNCATED = "\n[... workspace result truncated]"

#: Bound on one teardown attempt, in seconds. Mirrors
#: :data:`embodiment.muse_runner.DEFAULT_JOIN_TIMEOUT`'s *discipline* rather
#: than its value, and is deliberately larger than its 1.0s: that bound covers a
#: local thread hand-off, this one covers a round trip to a container engine
#: which, on the reference rig, contends for the same box as a 27B cortex (an
#: unprofiled load — plan risk, recorded there). Two seconds gives the engine
#: room without letting a drive's teardown become a stall, and overrunning it
#: costs a *record*, never a lost container.
#:
#: The whole default close budget is therefore ``DEFAULT_JOIN_TIMEOUT + 2.0`` =
#: **3.0s**, paid once at drive end and never on the actor's hot path.
DEFAULT_DESTROY_TIMEOUT = 2.0

#: The teardown thread's name, fixed so a host's stack dump names it — the same
#: reason :data:`embodiment.muse_runner.THREAD_NAME` is fixed.
TEARDOWN_THREAD_NAME = "embodiment-workspace-teardown"

#: What the muse is told when a tool call reaches a closed lane. Held as a
#: constant so a host can recognise it without matching on prose.
CLOSED_TEXT = (
    "the workspace lane is closed, so nothing was run — the drive it belonged to has ended"
)

#: Why a workspace can outlive its close through no fault of this module, told
#: to the host in the record rather than left to be rediscovered. Kept SHORT on
#: purpose: it rides inside a reason capped at :data:`_MAX_REASON_LEN`, and the
#: full argument — including the two options rejected in favour of naming the
#: leak — belongs in this module's docstring, where it costs a reader nothing.
LIVE_WORKSPACE_HINT = (
    "headspace 0.11.0 cannot destroy a workspace whose job is still running, "
    "and its 'stop' verb is not importable (headspace-cli#18, #22)"
)

#: The two things about the remedy that are not guessable, both **measured on
#: this rig** rather than read off a help page — and both fatal to an operator
#: who assumed otherwise:
#:
#: * ``headspace stop --apply`` **exits 5 on success**. That is its documented
#:   contract (a job ended this way reports ``cancelled``), which means a
#:   remedy chained with ``&&`` stops dead at the *successful* first command and
#:   never runs the ``destroy``. The commands are joined with ``;``.
#: * ``stop`` returns as soon as the job has been **signalled**, not once it has
#:   ended — the run invocation that started the job is what observes the
#:   ending. So an immediate ``destroy`` can still be refused with the same
#:   ``'running' -> 'destroyed'`` error; observed here, and clean on the retry
#:   two seconds later.
#:
#: An operator who follows this reaps the workspace. One who follows a plain
#: ``stop && destroy`` does not, and has no idea why.
REAP_NOTE = (
    "'stop' exits 5 on success and returns once the job is signalled rather "
    "than ended, so the commands are chained with ';' not '&&' and 'destroy' "
    "may need repeating until the job reports cancelled"
)


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
    except Exception:  # noqa: BLE001  # a property on a foreign object runs arbitrary code
        return None


def _text(value: Any) -> str:
    """One value as a single-line-safe string, or ``""``. Never raises."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # __str__ on a foreign object runs arbitrary code
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


def _reap_command(workspace_id: str, provider: str) -> str:
    """The exact two commands that remove a workspace this lane could not.

    A degradation that names the remedy costs nothing extra and is the
    difference between "something leaked" and "run this" — the same argument
    :data:`ENGINE_HINT` already makes, applied to the outcome that actually
    leaves state behind on a host's machine.

    Every part of this was measured against the real engine rather than read
    off a help page, because three separate details would each have produced a
    remedy that fails in the operator's hands: ``destroy`` refuses a workspace
    whose job is still running, ``stop`` without ``--apply`` is a preview that
    ends nothing, and ``stop`` **exits 5 on success** — so the separator is
    ``;`` and not ``&&``. See :data:`REAP_NOTE`, which ships beside this in the
    record and carries the last of those plus the retry.
    """
    return (
        f"headspace stop --apply {workspace_id} --provider {provider}; "
        f"headspace destroy {workspace_id} --provider {provider}"
    )


def _finished(thread: Any, timeout: float) -> bool:
    """Join *thread* under a bound; return whether it finished. Never raises.

    :func:`embodiment.muse_runner._bounded_join`'s discipline, inherited rather
    than imported — importing it would put the muse *thread* module in this
    lane's import graph for four lines of join. The one difference is the
    return value, and it is the whole point of this task: the runner can shrug
    at a thread it could not reap, because a daemon thread stops existing when
    the process does. A container does not. So this reports whether the
    teardown actually finished, and the caller turns a ``False`` into a record
    naming what is still out there.
    """
    try:
        thread.join(timeout=timeout)
        return not thread.is_alive()
    except RuntimeError:  # pragma: no cover - a thread object that refuses a join
        return False


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
        destroy_timeout: bound on ONE teardown attempt, in seconds. See
            :data:`DEFAULT_DESTROY_TIMEOUT` for why it is 2.0 and what the whole
            close budget adds up to.
        thread_factory: builds the teardown thread; :class:`threading.Thread` by
            default. Injected for the same reason
            :class:`~embodiment.muse_runner.ThreadedMuseRunner` injects one — so
            a test can drive the "no thread could be started" path through the
            public constructor instead of reaching inside.
    """

    def __init__(
        self,
        *,
        provider: str = PROVIDER_DOCKER,
        workspace_id: Optional[str] = None,
        api: Any = None,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
        destroy_timeout: float = DEFAULT_DESTROY_TIMEOUT,
        thread_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._provider = str(provider)
        self._requested_id = str(workspace_id) if workspace_id else None
        self._api = api if api is not None else headspace.api
        self._cap = int(max_result_chars)
        self._destroy_timeout = float(destroy_timeout)
        self._thread_factory: Callable[..., Any] = thread_factory or threading.Thread
        self._workspace_id = ""
        self._degradations: list[WorkspaceDegradation] = []
        self._statuses: dict[str, int] = {}
        self._runs = 0
        self._rejected = 0
        self._off_protocol = 0
        # The lane is driven from TWO threads once a host wires it behind
        # ``ThreadedMuseRunner``: the muse's worker calls ``execute``, and the
        # host's own thread calls ``close``. The lock guards the two state
        # transitions where that actually matters — latching the lane shut, and
        # claiming or releasing the workspace id — and is deliberately NOT held
        # across any engine call, so a create or a run in flight can never make
        # a close wait past its bound.
        self._lock = threading.RLock()
        self._closed = False

    # ── what it is ────────────────────────────────────────────────────────────

    @property
    def provider(self) -> str:
        """The backend this workspace was asked for."""
        return self._provider

    @property
    def workspace_id(self) -> str:
        """The provisioned workspace id, or ``""`` before anything was provisioned.

        After a clean teardown this returns to ``""``. After one that failed or
        timed out it keeps reporting the id, because the workspace may still be
        out there and an empty string would be a claim nobody checked.
        """
        with self._lock:
            return self._workspace_id

    @property
    def closed(self) -> bool:
        """Whether the lane has been closed. A closed lane provisions nothing."""
        with self._lock:
            return self._closed

    @property
    def schema(self) -> tuple[dict[str, Any], ...]:
        """The tool schema this lane offers — :data:`WORKSPACE_TOOLS`."""
        return WORKSPACE_TOOLS

    @property
    def degradations(self) -> tuple[WorkspaceDegradation, ...]:
        """Every recorded degradation, in the order it happened (constraint C3)."""
        with self._lock:
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

        # Checked AFTER the argument validation: a malformed call is malformed
        # whether the lane is open or shut, and recording a lifecycle
        # degradation for it would blame the wrong thing.
        if self.closed:
            self._degrade(
                DEGRADED_LANE_CLOSED,
                "a tool call arrived after the lane closed; nothing was provisioned",
                STAGE_RUN,
                self.workspace_id,
            )
            return CLOSED_TEXT

        workspace_id = self._ensure_workspace()
        # Read again, because the check above is a *reading* and not a latch the
        # rest of this method sits inside: it happens on the muse's thread while
        # ``close`` happens on the host's, so the lane can shut in between — and
        # the gap it leaves is not a hair's breadth, it spans a whole
        # provisioning round trip. Without this second look, a call that found
        # the lane open would be handed an id whose workspace the close had
        # already torn down, and run against it.
        #
        # Whichever side refuses writes the record, so a closing lane still
        # costs exactly one: ``_ensure_workspace`` owns the two ways it comes
        # back empty (closed before the id was claimed, or closed while a create
        # was on the wire), this owns the one where it hands over an id the close
        # has just orphaned. Both answer in :data:`CLOSED_TEXT` rather than the
        # provisioning message below, which would name a missing engine for
        # something the drive ending caused.
        #
        # The window is narrowed to a single statement, not closed. Closing it
        # would mean holding the lock across ``run`` — the one thing ``close``
        # must never wait on. What still slips through meets a destroyed
        # workspace and comes back as an ordinary :data:`DEGRADED_RUN_FAILED`:
        # readable text and a record, never a silent success.
        if self.closed:
            if workspace_id:
                self._degrade(
                    DEGRADED_LANE_CLOSED,
                    f"the lane closed while workspace {workspace_id} was in hand; "
                    "nothing was run",
                    STAGE_RUN,
                    workspace_id,
                )
            return CLOSED_TEXT
        if not workspace_id:
            return (
                "no workspace is available, so nothing was run: "
                f"{self._last_reason()}. {ENGINE_HINT}"
            )

        self._runs += 1
        try:
            package = self._api.run(workspace_id, command, provider=self._provider)
        except Exception as exc:  # noqa: BLE001  # an engine failure is text, never a raise
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

    def destroy(self, *, timeout: Optional[float] = None) -> bool:
        """Tear the workspace down under a bound. Returns whether one went away.

        The mid-drive verb: the lane stays **open**, so a later tool call
        provisions a fresh workspace exactly as the first one did. Use
        :meth:`close` at drive end, which is this plus latching the lane shut
        and accounting for anything that survived.

        Never raises: teardown runs on a close path, and a close path that
        raises loses whatever was being closed. Never hangs either — the engine
        call runs on a daemon thread joined under *timeout* (default
        :data:`DEFAULT_DESTROY_TIMEOUT`), so a wedged container daemon costs a
        bounded delay and a record rather than a parked host.

        Two distinct failures, two codes, both naming the workspace so a host
        can go and look: :data:`DEGRADED_DESTROY_FAILED` when the engine
        refused, :data:`DEGRADED_DESTROY_TIMEOUT` when it did not answer in
        time.
        """
        with self._lock:
            workspace_id = self._workspace_id
        if not workspace_id:
            return False
        return self._teardown(workspace_id, timeout)

    def close(self, *, timeout: Optional[float] = None) -> bool:
        """End the lane at drive end. Returns whether nothing is left live.

        Idempotent, never raises, never hangs, and safe to call from any thread
        — a host wires it as ``ThreadedMuseRunner(complete,
        closers=(workspace.close,))`` and it runs after that runner's bounded
        join, or drives it directly through ``with``.

        Three things happen, in this order:

        1. **The lane latches shut.** Nothing will be provisioned again, so a
           thinking session that outlived the bounded join cannot mint a second
           container after the drive ended. A tool call that arrives anyway is
           refused in text and recorded (:data:`DEGRADED_LANE_CLOSED`).
        2. **The workspace is torn down under a bound** (:meth:`destroy`'s
           mechanism and its two failure codes).
        3. **Whatever survived is named.** If the workspace is still live after
           that attempt, one :data:`DEGRADED_WORKSPACE_LIVE` record carries the
           id, the provider and the exact ``headspace destroy`` line that reaps
           it. That record is the acceptance condition of this whole path: a
           leaked container an operator cannot name is the worst outcome
           available, and it is the one this refuses to produce.

        Returns ``True`` when nothing is left behind — including the ordinary
        case where nothing was ever provisioned — and ``False`` when a
        workspace outlived the close, which is exactly when a record was
        written.
        """
        with self._lock:
            first = not self._closed
            self._closed = True
            workspace_id = self._workspace_id
        if not first or not workspace_id:
            return not workspace_id
        if self._teardown(workspace_id, timeout):
            return True
        self._name_as_live(workspace_id)
        return False

    def __enter__(self) -> "MuseWorkspace":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ── what it can answer about itself ───────────────────────────────────────

    def counts(self) -> WorkspaceCounts:
        """What the muse did with the workspace — read it when a session returns."""
        with self._lock:
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

    def _teardown(self, workspace_id: str, timeout: Optional[float]) -> bool:
        """One bounded teardown attempt. Returns whether the workspace went away.

        The engine call runs on a **daemon** thread so a wedged daemon cannot
        park the host: the join is bounded, and a thread still running when the
        bound expires is simply left to the process. That much is
        ``_bounded_join``'s discipline verbatim. What is *not* inherited is its
        conclusion — an unreaped thread stops mattering at process exit and an
        unreaped container does not — so the caller is told, and says so.
        """
        bound = self._destroy_timeout if timeout is None else float(timeout)
        failure: list[Exception] = []

        def _run_destroy() -> None:
            try:
                self._api.destroy(workspace_id, provider=self._provider)
            except Exception as exc:  # noqa: BLE001  # carried back, never raised out of a thread
                failure.append(exc)

        try:
            thread = self._thread_factory(
                target=_run_destroy, name=TEARDOWN_THREAD_NAME, daemon=True
            )
            thread.start()
        except Exception as exc:  # noqa: BLE001  # no thread is a degradation, not a crash
            self._degrade(
                DEGRADED_DESTROY_FAILED,
                f"no teardown thread could be started, so nothing was attempted: "
                f"{type(exc).__name__}: {exc}",
                STAGE_DESTROY,
                workspace_id,
            )
            return False

        if not _finished(thread, bound):
            self._degrade(
                DEGRADED_DESTROY_TIMEOUT,
                f"teardown did not finish within {bound}s; the workspace may still exist",
                STAGE_DESTROY,
                workspace_id,
            )
            return False
        if failure:
            self._degrade(
                DEGRADED_DESTROY_FAILED, _failure_text(failure[0]), STAGE_DESTROY, workspace_id
            )
            return False
        with self._lock:
            if self._workspace_id == workspace_id:
                self._workspace_id = ""
        return True

    def _name_as_live(self, workspace_id: str) -> None:
        """Record the one thing an operator needs: what to reap, and how.

        The **remedy is written before the explanation**, because the reason is
        capped at :data:`_MAX_REASON_LEN` like every other lane's and a long
        workspace id could otherwise push the two commands past the cut. What
        survives a truncation here has to be the part someone can act on.
        """
        self._degrade(
            DEGRADED_WORKSPACE_LIVE,
            f"the lane closed with workspace {workspace_id} still live on provider "
            f"{self._provider}; reap it with: "
            f"{_reap_command(workspace_id, self._provider)} — {REAP_NOTE} — "
            f"{LIVE_WORKSPACE_HINT}",
            STAGE_CLOSE,
            workspace_id,
        )

    def _ensure_workspace(self) -> str:
        """Provision on first use; return the id, or ``""`` when none could be.

        The create call is the first of the four no-reach mechanisms and is
        deliberately tiny: ``provider=``, plus ``workspace_id=`` only when a
        host named one. **No ``policy``**, so headspace's own closed-by-default
        posture — network disabled, no host paths, small budgets — is the only
        posture reachable from here. And no ``profile``, so the pinned default
        image is the only image.

        The lock is released across the create call, so a close can land while
        one is in flight. That race is real — the muse thinks on its own thread
        — and it is resolved on the far side rather than by holding a lock a
        close would then wait on: a workspace that arrives into a closed lane is
        torn down again immediately instead of becoming a container nothing
        will ever reap.

        A closed lane returns ``""`` too, and records why — see the first check.
        """
        # Closed-checked here as well as in :meth:`execute`, which the comment
        # this replaces argued was unnecessary. It was wrong: ``execute``'s check
        # runs on the muse's thread and ``close`` on the host's, so the lane can
        # be shut by the time control arrives here even though the caller found
        # it open. Without this, that call would be handed back the id of a
        # workspace the close was tearing down — or, once the teardown had
        # cleared it, would mint a *second* container after the drive ended,
        # which is the leak the latch exists to prevent.
        #
        # Reading ``_closed`` and ``_workspace_id`` under one acquisition is what
        # makes the refusal a fact rather than two readings that were each true
        # at a different moment. The lock is let go again before anything reaches
        # the engine, so ``close`` still waits on nothing here.
        with self._lock:
            closed, existing = self._closed, self._workspace_id
            if not closed and existing:
                return existing
        if closed:
            self._degrade(
                DEGRADED_LANE_CLOSED,
                "the lane closed while a tool call was in flight; nothing was provisioned "
                "and nothing was run",
                STAGE_RUN,
                existing,
            )
            return ""
        try:
            if self._requested_id is None:
                package = self._api.create(provider=self._provider)
            else:
                package = self._api.create(provider=self._provider, workspace_id=self._requested_id)
        except Exception as exc:  # noqa: BLE001  # a missing engine is text, never a raise
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
        with self._lock:
            if not self._closed:
                self._workspace_id = workspace_id
                return workspace_id
        # The lane closed while this create was on the wire. The workspace is
        # real and nothing is going to use it, so it is torn down here rather
        # than left for an operator to find — and if THAT fails, it is named
        # like any other survivor.
        self._degrade(
            DEGRADED_LANE_CLOSED,
            f"the lane closed while workspace {workspace_id} was being provisioned; "
            "it was torn down again and nothing was run",
            STAGE_CREATE,
            workspace_id,
        )
        if not self._teardown(workspace_id, None):
            self._name_as_live(workspace_id)
        return ""

    def _degrade(self, code: str, reason: str, stage: str, workspace_id: str) -> None:
        """Record one host-visible transition. The only way this lane reports harm.

        Appends under the lock: ``execute`` runs on the muse's thread and
        ``close`` on the host's, so two records can genuinely be minted at once
        and a list append is not the place to find that out.
        """
        with self._lock:
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
        with self._lock:
            records = self._degradations
            return records[-1].reason if records else "no engine was reachable"
