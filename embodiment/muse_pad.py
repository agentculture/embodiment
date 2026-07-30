"""The muse's working memory, wired as its first thinking tool (task t12).

:mod:`embodiment.muse` gained a tool seam in task t10 — a :class:`schema
<embodiment.muse.MuseToolBench>`, a tool-carrying completion, and a way to run
one call — and deliberately shipped **no tools of its own**: what the bench holds
is the host's to state, so "the muse never acts on the repository" is a property
of there being nothing here that could, not of this file's good intentions.

This module supplies the first thing to put on that bench. It is not a new tool:
it is :mod:`embodiment.scratchpad`, the *acting* loop's working memory, offered
to the thinking lane. The pad's whole design — ``intend`` before the act,
``observe`` after it, an open intent meaning "I was interrupted here" — is what
makes a dropped thought survivable, and a thinking lane that runs beside the
actor on its own thread (deviation ``d1``) drops thoughts for a living.

What is reused, and the one thing that is not
---------------------------------------------
:data:`~embodiment.scratchpad.KINDS`, :data:`~embodiment.scratchpad.PROTOCOL`
and the tool schemas in :data:`~embodiment.scratchpad.SCRATCHPAD_TOOLS` are
reused **unchanged** — the schema dicts here are the very objects that module
holds, not copies of them, so ``tests/test_muse_pad.py`` can assert reuse by
identity rather than by an equality that would drift.

One divergence, declared in :data:`OMITTED_TOOLS` and recorded here because a
silent second protocol is the failure the acceptance criterion forbids:

* **``finish`` is not offered to the muse.** It submits *the final answer*, and
  the muse has none to submit — the acting loop holds final authority and writes
  the one summary (`colleague#352
  <https://github.com/agentculture/colleague/issues/352>`_). The seam agrees
  structurally: :func:`embodiment.muse._result_text` ignores a tool outcome's
  ``finished`` flag on purpose, because the only things that end a thinking
  session are the four ``MUSE_EXIT_*`` reasons. Offering ``finish`` would
  therefore advertise a capability the seam refuses to honour, and
  :meth:`Scratchpad.execute <embodiment.scratchpad.Scratchpad.execute>` would
  quietly stamp an ``answer`` onto the pad — and persist it — that nothing ever
  reads. So the name is **refused at this boundary** rather than delegated: a
  hallucinated ``finish`` raises, the muse reads the refusal as ordinary text
  (:data:`~embodiment.muse.DEGRADED_TOOL` is recorded), and it keeps thinking.

Nothing else diverges. ``read``, and the four kinds, are the actor's schemas
verbatim.

Where the pad lives, and why it is not recallable
-------------------------------------------------
The muse pad is a **separate lane** from the actor's: its own file
(:data:`MUSE_PAD_FILENAME`), its own label (:data:`MUSE_PAD_LANE`), its own
banner when rendered. Two lanes can share a directory and still never share a
file.

It is also, deliberately, **not a memory**. Claim ``c10`` is a hard boundary:
a hostile record arriving wearing remembered authority beat the cortex 6/6
against a withheld control that resisted 6/6, with labelling and provenance
framing working correctly the whole time (``docs/live-test-results/
memory-echo-chamber.md``, n=6 per arm). Until an echo probe re-runs clean, no
recall path may surface pad entries. Three mechanisms hold that, none of them
prose:

1. **Nothing here reaches a memory surface.** This module imports no
   :mod:`embodiment.continuity`, :mod:`embodiment.recall_bundle` or
   :mod:`embodiment.lifecycle`, and exports no ``remember`` / ``recall`` verb —
   there is no call for a pad entry to travel out on.
2. **Nothing there reaches here.** Those modules import neither this one nor
   :mod:`embodiment.scratchpad`. Both directions are pinned by AST.
3. **A pad pointed inside a store is refused at construction**
   (:class:`MusePadRefused`). A public eidetic record inside a git repo is
   committed and travels with every clone, so a pad file that landed there would
   be recallable *and* shared. That is a host wiring mistake, made once, at a
   place where raising is the right answer — the never-raise rule governs the
   muse's thinking, not a host constructing itself wrong.

Measuring protocol adherence, which is the point
------------------------------------------------
Handed this pad and its ordering rule, the reference muse wrote **five intents,
zero observations, zero conclusions** — the exact failure the pad exists to
prevent. Five stacked intents make the record lie about where the mind got to.

Fixing that is not this module's job; the three-arm validation (tasks t17/t18)
measures whether the pad helps at all, and prompt-tuning ahead of that
measurement would only make the result about the tuning. Making the failure
*visible* is this module's job, so :class:`MusePadCounts` reports one count per
:data:`~embodiment.scratchpad.KINDS` entry (zeros included — a zero is a
measurement) plus :attr:`~MusePadCounts.open_intents`, the number of intents no
observation ever answered. Read them when a session returns::

    pad = MusePad.in_directory(run_dir)
    outcome = MuseLoop(complete, tools=pad.bench(tool_complete)).think(boundary)
    transcript["pad"] = pad.counts().to_dict()

Those are t18's dependent variables. They are **not** degradations: a muse that
is bad at the protocol is not embodiment breaking, and recording it in the
stream a host reads to answer *"what went wrong?"* would cry wolf on every
healthy run — the failure :mod:`embodiment.ledger` documents at length. Genuine
faults (a pad that cannot persist, a refused tool name) already ride the muse's
own :data:`~embodiment.muse.DEGRADED_TOOL`, so no new code enters the ledger.

Top-level only
--------------
This module contains no depth logic and must not grow any:
:func:`embodiment.muse._bench_for` is the single gate, it fails closed, and it
records :data:`~embodiment.muse.DEGRADED_TOOLS_WITHHELD` when a bench is handed
to a muse below the top. A bench built here and wired to a subagent-depth muse
is simply never called, and this module has no way to notice or object — which
is the correct amount of authority for it to have.

Stdlib plus two embodiment modules. :mod:`embodiment.loop` is reached lazily and
only for :class:`~embodiment.loop.UnknownToolError`, mirroring what
:mod:`embodiment.scratchpad` already does; the actor loop is never driven from
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from embodiment.muse import MuseToolBench, MuseToolCompleteFn
from embodiment.scratchpad import KINDS, PROTOCOL, SCRATCHPAD_TOOLS, Scratchpad, render

__all__ = [
    # the lane
    "MUSE_PAD_LANE",
    "MUSE_PAD_FILENAME",
    "MUSE_PAD_BANNER",
    # the protocol, reused and one thing not
    "MUSE_PAD_TOOLS",
    "MUSE_PAD_TOOL_NAMES",
    "MUSE_PAD_PROTOCOL",
    "MUSE_PAD_ADDENDUM",
    "OMITTED_TOOLS",
    "STORE_MARKERS",
    # shapes
    "MusePad",
    "MusePadCounts",
    "MusePadRefused",
]


# ── the lane ──────────────────────────────────────────────────────────────────

#: What every reported surface labels this pad with. The muse lane is not the
#: actor lane and a reader must never have to infer which one it is holding.
MUSE_PAD_LANE = "muse"

#: The muse pad's own filename, so the thinking lane and the acting lane can
#: share a run directory and still never share a file.
MUSE_PAD_FILENAME = "muse-pad.jsonl"

#: Prefixed to :meth:`MusePad.render`'s output. It states the boundary in the
#: same breath as the content, which is where a boundary is least likely to be
#: forgotten.
MUSE_PAD_BANNER = "── MUSE PAD (advisory lane — never recalled, never remembered) ──"


# ── the protocol: reused, minus one declared omission ─────────────────────────

#: The actor tools the muse is NOT offered, and the whole of the divergence from
#: :data:`~embodiment.scratchpad.SCRATCHPAD_TOOLS`. See the module docstring for
#: why ``finish`` is the one: the muse has no final answer to submit, and the
#: thinking seam ignores a tool's ``finished`` flag by design.
OMITTED_TOOLS = ("finish",)


def _tool_name(tool: Any) -> str:
    """The name off one OpenAI-shaped tool mapping, or ``""``."""
    function = tool.get("function") if isinstance(tool, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    return name if isinstance(name, str) else ""


#: The schema put on the wire. These are :data:`SCRATCHPAD_TOOLS`' **own dict
#: objects**, filtered and reordered by nothing — reuse provable by identity, so
#: a schema change in :mod:`embodiment.scratchpad` arrives here rather than
#: drifting away from a copy. :func:`embodiment.muse._call_seam` shallow-copies
#: each mapping before it reaches a seam, so sharing them costs nothing.
MUSE_PAD_TOOLS: tuple[dict[str, Any], ...] = tuple(
    tool for tool in SCRATCHPAD_TOOLS if _tool_name(tool) not in OMITTED_TOOLS
)

#: The names :meth:`MusePad.execute` will honour, in schema order. Every other
#: name — ``finish`` included — is refused rather than delegated.
MUSE_PAD_TOOL_NAMES: tuple[str, ...] = tuple(_tool_name(tool) for tool in MUSE_PAD_TOOLS)

#: What the muse is told about the pad *beyond* what the actor is told. Kept as
#: a separate constant, and appended, so :data:`~embodiment.scratchpad.PROTOCOL`
#: reaches the muse byte-for-byte as the actor reads it.
MUSE_PAD_ADDENDUM = (
    "This pad is yours and it is private. It is not a channel to the acting "
    "loop: nothing you write on it is executed, read by the acting loop, or "
    "remembered anywhere. Only a 'GUIDANCE:' line reaches the acting loop, "
    "exactly as before.\n"
    "There is no finish tool here, because you have no final answer to submit — "
    "the acting loop writes that and holds final authority over it."
)

#: The pad protocol as the muse receives it: the actor's own text, verbatim,
#: plus the muse-scoped addendum. Pass it as ``MuseLoop(system=…)``, where it is
#: appended to :data:`~embodiment.muse.MUSE_AUTHORITY` and never substituted for
#: it.
MUSE_PAD_PROTOCOL = f"{PROTOCOL}\n\n{MUSE_PAD_ADDENDUM}"


# ── the store boundary (claim c10) ────────────────────────────────────────────

#: Directory names that mark a memory store. A pad path with one of these
#: anywhere in it is refused: a public eidetic record inside a git repo is
#: committed and travels with every clone, so a pad file landing there would be
#: both recallable and shared — the two things claim ``c10`` forbids.
STORE_MARKERS = frozenset({".eidetic", ".coherence"})


class MusePadRefused(ValueError):
    """A muse pad was pointed somewhere it must not write.

    Raised at construction, on purpose. The never-raise rule that governs
    :meth:`embodiment.muse.MuseLoop.think` protects a host's main path from the
    *model's* faults; it is not a reason to accept a host wiring its pad into a
    committed memory store and to degrade quietly instead. This is a programming
    error, it happens once, and it happens where it can be fixed.
    """


def _checked_path(path: Any) -> Optional[Path]:
    """Resolve *path*, refusing anything inside a memory store. ``None`` passes."""
    if path is None:
        return None
    candidate = Path(path)
    parts = set(candidate.parts) | set(candidate.expanduser().resolve().parts)
    offending = sorted(parts & STORE_MARKERS)
    if offending:
        raise MusePadRefused(
            f"a muse pad may not be written inside a memory store: {candidate} "
            f"lies under {offending[0]!r}. The muse pad stays non-recallable until "
            "the echo probe re-runs clean (claim c10); choose a path outside the store."
        )
    return candidate


# ── what the muse actually did with it ────────────────────────────────────────


def _open_intents(entries: Any) -> int:
    """How many intents no observation ever answered.

    The generalisation of :attr:`embodiment.scratchpad.Scratchpad.open_intent`,
    which scans backwards and stops at the first ``observe`` — so an observation
    closes every intent before it, and this counts what is left after the last
    one. Five stacked intents with nothing after them therefore report ``5``,
    which is the measured failure this metric exists to name.
    """
    open_count = 0
    for entry in entries:
        kind = getattr(entry, "kind", "")
        if kind == "observe":
            open_count = 0
        elif kind == "intend":
            open_count += 1
    return open_count


@dataclass(frozen=True)
class MusePadCounts:
    """Protocol adherence, read off a finished session. t18's dependent variables.

    Deliberately **not** a quality score and deliberately not a degradation
    record: everything here can be read off the pad without interpretation, and
    a muse that used the protocol badly is not embodiment breaking.

    Fields
    ------
    lane:
        Always :data:`MUSE_PAD_LANE`. A reader never has to infer whose pad this
        is.
    entries:
        How many entries the pad holds.
    kinds:
        One count per :data:`~embodiment.scratchpad.KINDS` entry, in that order,
        **zeros included** — "zero observations" is the finding, so it has to be
        reportable rather than absent.
    open_intents:
        Intents no observation answered. ``0`` is the protocol being followed;
        the measured failure reported ``5``.
    rejected_calls:
        Calls the pad refused as unusable (an empty text, a revision of an id it
        does not have). The muse gets a corrective message and keeps going.
    off_protocol_calls:
        Calls naming a tool the muse pad does not offer — ``finish``, or a
        hallucination. Counted here *and* recorded as a
        :data:`~embodiment.muse.DEGRADED_TOOL` by the thinking loop.
    sequence:
        The kinds in order, so a transcript can show the shape of the run and
        not only its totals.
    persisted:
        Whether the pad has a file on disk behind it.
    """

    lane: str = MUSE_PAD_LANE
    entries: int = 0
    kinds: dict[str, int] = field(default_factory=lambda: {kind: 0 for kind in KINDS})
    open_intents: int = 0
    rejected_calls: int = 0
    off_protocol_calls: int = 0
    sequence: tuple[str, ...] = ()
    persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe row for a committed transcript."""
        return {
            "lane": self.lane,
            "entries": self.entries,
            "kinds": dict(self.kinds),
            "open_intents": self.open_intents,
            "rejected_calls": self.rejected_calls,
            "off_protocol_calls": self.off_protocol_calls,
            "sequence": list(self.sequence),
            "persisted": self.persisted,
        }


# ── the pad ───────────────────────────────────────────────────────────────────


class MusePad:
    """The muse's working memory and the bench it rides on.

    Composition over a :class:`~embodiment.scratchpad.Scratchpad`, never a
    subclass: the actor's pad keeps its own behaviour exactly, and everything
    muse-specific — the refused ``finish``, the lane label, the counters — lives
    on this side of the seam where it can be read in one place.

    Args:
        path: where the pad persists, or ``None`` for a pad that lives only as
            long as the process. A path inside a memory store raises
            :class:`MusePadRefused`. An existing file is *reopened*, not
            truncated — a working memory that starts blank after a restart
            protects against nothing.

    Wiring one up::

        pad = MusePad.in_directory(run_dir)
        loop = MuseLoop(complete, system=MUSE_PAD_PROTOCOL, tools=pad.bench(tool_complete))
        outcome = loop.think(boundary)
        counts = pad.counts()
    """

    def __init__(self, path: Any = None) -> None:
        self._path = _checked_path(path)
        self._pad = Scratchpad() if self._path is None else Scratchpad.load(self._path)
        self._off_protocol = 0

    @classmethod
    def in_directory(cls, directory: Any) -> "MusePad":
        """A pad at :data:`MUSE_PAD_FILENAME` inside *directory*.

        The convenience that keeps the muse lane's file distinct from whatever
        the acting loop keeps in the same run directory.
        """
        return cls(Path(directory) / MUSE_PAD_FILENAME)

    # ── what it is ────────────────────────────────────────────────────────────

    @property
    def path(self) -> Optional[Path]:
        """Where this pad persists, or ``None`` when it does not."""
        return self._path

    @property
    def pad(self) -> Scratchpad:
        """The underlying scratchpad, for a host that wants to render or resume it."""
        return self._pad

    @property
    def schema(self) -> tuple[dict[str, Any], ...]:
        """The tool schema this pad offers — :data:`MUSE_PAD_TOOLS`."""
        return MUSE_PAD_TOOLS

    # ── the tool seam ─────────────────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run one pad call. Satisfies :data:`~embodiment.muse.MuseToolExecuteFn`.

        A name the muse pad does not offer raises
        :class:`~embodiment.loop.UnknownToolError` — including ``finish``, which
        the actor's dispatcher *would* honour. Delegating it would stamp an
        answer onto the pad that nothing reads and persist it; refusing it here
        keeps the divergence real rather than documentary. The thinking loop
        catches the raise, records
        :data:`~embodiment.muse.DEGRADED_TOOL`, hands the muse readable text and
        carries on, exactly as it treats any failing tool.
        """
        if name not in MUSE_PAD_TOOL_NAMES:
            self._off_protocol += 1
            from embodiment.loop import UnknownToolError

            raise UnknownToolError(
                f"the muse pad has no {name!r} tool; it offers " f"{', '.join(MUSE_PAD_TOOL_NAMES)}"
            )
        return self._pad.execute(name, dict(arguments) if isinstance(arguments, dict) else {})

    def bench(self, complete: MuseToolCompleteFn) -> MuseToolBench:
        """The bench to hand :class:`~embodiment.muse.MuseLoop` as ``tools=``.

        *complete* is the host's tool-carrying seam
        (:data:`~embodiment.muse.MuseToolCompleteFn`) — this module holds no
        endpoint, no model and no network, and never constructs one.

        The bench reaches the wire only on the **top-level** muse; a muse at
        subagent depth is handed nothing and the withholding is recorded by
        :mod:`embodiment.muse`, not here.
        """
        return MuseToolBench(schema=MUSE_PAD_TOOLS, complete=complete, execute=self.execute)

    # ── what it can answer about itself ───────────────────────────────────────

    def counts(self) -> MusePadCounts:
        """Protocol adherence at this moment — read it when a session returns."""
        entries = list(self._pad.entries)
        kinds = {kind: 0 for kind in KINDS}
        sequence: list[str] = []
        for entry in entries:
            kind = str(getattr(entry, "kind", ""))
            sequence.append(kind)
            if kind in kinds:
                kinds[kind] += 1
        return MusePadCounts(
            entries=len(entries),
            kinds=kinds,
            open_intents=_open_intents(entries),
            rejected_calls=len(self._pad.rejected),
            off_protocol_calls=self._off_protocol,
            sequence=tuple(sequence),
            persisted=bool(self._path is not None and self._path.exists()),
        )

    def render(self) -> str:
        """The pad as a reader sees it, under this lane's banner.

        The label rides the rendering rather than the entries: the on-disk
        record is :mod:`embodiment.scratchpad`'s, unchanged, and the file it
        lives in is what separates the lanes.
        """
        body = render(self._pad)
        return f"{MUSE_PAD_BANNER}\n{body or '(empty)'}"
