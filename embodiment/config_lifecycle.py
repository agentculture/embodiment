"""Propose → verify → apply, with per-seat quiescence: no seat is reconfigured mid-run.

Task ``t4`` of the config-not-minds cycle (spec claims ``c34``/``h23``). It
encodes one operator decision, quoted here because the whole module is a reading
of it:

    *"mid drive — code makes a prompt change a proposal, a test suite verifies
    it, and the proposal is in when the affected subagent is available for test
    suite + not in a run and test suite passed."*

So the session continues mid-drive, and **no seat ever has its configuration
changed under it**. A change is a *proposal* until a suite has run against the
configuration the seat would actually get; it is *applied* only when the seat
that owns it is idle. The advisory lane's insertion mechanism proved a constant
system-prompt sha across all 8 drives of live session 1 — that check does not
survive the redesign literally (there is no inserted message any more), so this
module makes it survive **per seat**: configuration identity is constant within
any single run, and changes only ever land between runs.

Two names, two levels, deliberately not the same thing
------------------------------------------------------
:mod:`embodiment.lifecycle` is continuity's checkpoint lifecycle. This is the
*configuration* lifecycle — the states one change unit passes through. They
share a word and nothing else, and neither imports the other.

The pattern is ``drone.py``'s stage → smoke → save, moved to runtime
---------------------------------------------------------------------
``drone.create`` stages the three artifacts in a temporary directory, runs the
smoke invocation **against the staged copy** so that what is proven is what will
be saved, and only a pass moves it into place — "an unsaved failure is cheap, a
saved broken drone is a trap". Every clause transfers:

===========================  ==================================================
``drone.py`` (authoring)     this module (runtime)
===========================  ==================================================
staged in a temp directory   :attr:`VerificationRequest.candidate` — the
                             configuration the seat *would* run under
smoke against the staged     the injected verifier is handed the candidate, not
copy                         the change; a suite that graded the change alone
                             would be grading something the seat never sees
a failure is never saved     a failed suite transitions the proposal to
                             :data:`STATE_REJECTED` and it can never be applied
the source hash is stamped   :attr:`VerificationResult.candidate_sha` is
after writing and re-checked  *stamped by the gate*, and re-checked at apply:
                             a verification is evidence about the baseline it
                             ran against and about no other
staleness refusal before     :func:`embodiment.config_change.revalidate` runs
any code runs                before any change is installed
===========================  ==================================================

The one clause that does **not** transfer is the two-rename swap. There is no
filesystem here: installing a configuration is rebinding one frozen object in a
dict, which is already atomic with respect to everything that can observe it.

What the gate actually is
-------------------------
Two conditions, both required, and one of them is a *timing* fact rather than a
judgement about the change:

* **suite-pass** — the proposal is in :data:`STATE_VERIFIED`, its verification
  passed, and the candidate that verification ran against is still the candidate
  about to be installed.
* **seat-idle** — the seat that owns the change has no open run.

Verification is gated on seat-idle too, which is stricter than "apply is gated
on seat-idle" and is the operator's own wording: *available for test suite **+**
not in a run*. It matters for a reason the shorter reading misses — a suite that
runs while the seat is working is grading a configuration against a baseline the
seat is halfway through using, and its verdict would be about a moment that has
already passed.

A busy seat is a DEFERRAL, not a degradation
--------------------------------------------
:class:`ConfigDeferral` deliberately does **not** subclass
:class:`~embodiment.config_change.ConfigDegradation`, even though
:class:`~embodiment.config_change.ConfigRefusal` does and the two carry the same
field names and ``to_dict`` keys. The reason is C3-shaped rather than tidiness:
a seat being busy is the gate *working*, and folding it into the degradation
stream would make a correctly-behaving session report hundreds of degradations —
a count that rises during normal operation is a count nobody reads, and C3 needs
that stream to stay worth reading. Nothing is dropped: every deferral is
recorded in :attr:`ConfigLifecycle.deferrals`, countable and attributable.

No thread, no clock, no IO — and no timestamps either
------------------------------------------------------
The split is ``presence.py`` / ``presence_engine.py``'s: this is the policy
half. The verifier is *injected*, never constructed, exactly as ``loop.run``
takes a ``complete`` callable and never builds a client. A consequence worth
stating because a reader will look for it: :class:`ConfigTransition` carries a
``sequence`` and no timestamp. "When" is a clock, and there is no clock in this
layer — task ``t5``'s ledger stamps time where time is available, and order is
what this layer can honestly record.

Where the rest of the tier lands
--------------------------------
The schemas and the authority lattice are ``t3``'s
(:mod:`embodiment.config_change`, :mod:`embodiment.capability`); the ledger,
events and fail-closed persistence are ``t5``'s; revert and ratchet are
``t6``'s; ledger-derived introspection is ``t7``'s; the knowledge block's
eidetic composition is ``t8``'s. This module writes nothing anywhere and holds
no store.

**The seam this module offers the rest of them is :attr:`ConfigLifecycle.transitions`**
— an append-only tuple of frozen records, never drained by this module. That is
deliberately a *read*, not a callback: ``scope_events.py``'s translation pattern
is pure functions over records, so a stream of records is exactly its input, and
a register/drain split is the defect class (#54) this cycle exists to dissolve
rather than re-import.

Stdlib only (``dataclasses``, ``hashlib``, ``typing``) plus ``t3``'s two schema
modules.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

from embodiment.capability import CAPABILITY_KIND_PERMISSION, CapabilityCatalog
from embodiment.config_change import (
    CHANGE_SEATS,
    CapabilitySelection,
    ConfigChange,
    ConfigRefusal,
    KnowledgeChange,
    PromptChange,
    change_from_payload,
    revalidate,
)

__all__ = [
    # states
    "STATE_PROPOSED",
    "STATE_VERIFIED",
    "STATE_APPLIED",
    "STATE_REJECTED",
    "CHANGE_STATES",
    "TERMINAL_STATES",
    # the gate's own vocabulary (C3)
    "CHANGE_UNKNOWN_PROPOSAL",
    "CHANGE_DUPLICATE_PROPOSAL",
    "CHANGE_UNVERIFIED",
    "CHANGE_ALREADY_APPLIED",
    "CHANGE_TERMINAL",
    "CHANGE_VERIFICATION_FAILED",
    "CHANGE_VERIFIER_UNAVAILABLE",
    "CHANGE_VERIFIER_UNREADABLE",
    "CHANGE_STALE_VERIFICATION",
    "CHANGE_SEAT_BUSY",
    "CHANGE_UNKNOWN_RUN",
    "CHANGE_UNKNOWN_SEAT",
    "LIFECYCLE_REFUSAL_CODES",
    "LIFECYCLE_DEFERRAL_CODES",
    "LIFECYCLE_CODES",
    "LIFECYCLE_RULES",
    # the configuration a seat runs under
    "PromptSection",
    "KnowledgeEntry",
    "SeatConfig",
    "apply_change",
    "compose_prompt",
    # the run handle
    "SeatRun",
    # verification
    "VerificationRequest",
    "VerificationResult",
    "VerifierFn",
    # records
    "Proposal",
    "ConfigTransition",
    "ConfigDeferral",
    "ApplyOutcome",
    "AdvanceReport",
    # the gate
    "ConfigLifecycle",
    "seat_configs",
]


# ── the three states, plus the one terminal failure ───────────────────────────
#
# Four names, and the acceptance criterion is that the first three are DISTINCT
# and RECORDED — not that they are three flags on one object. A state is where a
# proposal is; a transition is how it got there, and the transitions are the
# record. `STATE_REJECTED` is the fourth because a failed suite has to land
# somewhere that can never be applied from.

#: Admitted, not yet graded. Nothing can be applied from here.
STATE_PROPOSED = "proposed"
#: A suite ran against this change's candidate configuration and passed.
STATE_VERIFIED = "verified"
#: Installed. The seat's effective configuration moved. Terminal.
STATE_APPLIED = "applied"
#: A suite ran and reported failure, or the host's catalog moved under the unit.
#: Terminal: re-verifying cannot un-fail a suite, and a re-authored change is a
#: new proposal with a new id rather than a second chance for this one.
STATE_REJECTED = "rejected"

#: Every state, in lifecycle order.
CHANGE_STATES = (STATE_PROPOSED, STATE_VERIFIED, STATE_APPLIED, STATE_REJECTED)
#: The states nothing moves out of.
TERMINAL_STATES = (STATE_APPLIED, STATE_REJECTED)


# ── the gate's vocabulary (constraint C3) ─────────────────────────────────────
#
# Two families, one lane. `config-change-*` is t3's prefix and these are the same
# lane's codes, so they share it; `config-seat-*` names a fault in the host's own
# run bookkeeping rather than in a change, because the fix lives somewhere else
# entirely. One code per distinct FIX — scope.py's principle, inherited through
# t3.

#: No proposal is registered under that id.
CHANGE_UNKNOWN_PROPOSAL = "config-change-unknown-proposal"
#: A proposal is already registered under that id. **Ids must be new** — the
#: rule that #58 showed is worthless unless it is stated where the author can
#: read it, which is why it is also in :data:`LIFECYCLE_RULES`.
CHANGE_DUPLICATE_PROPOSAL = "config-change-duplicate-proposal"
#: Apply was called on a proposal no suite has passed. Fail closed.
CHANGE_UNVERIFIED = "config-change-unverified"
#: Apply was called on a change that is already installed.
CHANGE_ALREADY_APPLIED = "config-change-already-applied"
#: The proposal is in a terminal state; nothing moves out of one.
CHANGE_TERMINAL = "config-change-terminal"
#: The suite ran and reported failure. The drone-smoke analogue, and the one
#: refusal that is *evidence about the change itself*.
CHANGE_VERIFICATION_FAILED = "config-change-verification-failed"
#: No verifier is wired. Absence of evidence never approves.
CHANGE_VERIFIER_UNAVAILABLE = "config-change-no-verifier"
#: The verifier raised, or answered in a shape this module cannot read. Distinct
#: from :data:`CHANGE_VERIFICATION_FAILED` because the fix is different: that one
#: is fixed by changing the proposal, this one by fixing the suite harness.
CHANGE_VERIFIER_UNREADABLE = "config-change-verifier-unreadable"
#: The configuration moved between verify and apply, so the suite's verdict is
#: about a baseline that is no longer in force. Recoverable: the proposal is
#: demoted to :data:`STATE_PROPOSED` and can be verified again.
CHANGE_STALE_VERIFICATION = "config-change-stale-verification"

#: The seat that owns this change has an open run. A **deferral**, not a
#: degradation — see the module docstring.
CHANGE_SEAT_BUSY = "config-change-seat-busy"

#: ``end_run`` was handed a run this lifecycle has no record of opening.
CHANGE_UNKNOWN_RUN = "config-seat-unknown-run"
#: A run was opened for a seat outside :data:`~embodiment.config_change.CHANGE_SEATS`.
#: Nothing can ever be applied to it, so a wiring mistake here would otherwise be
#: a seat that silently never receives configuration.
CHANGE_UNKNOWN_SEAT = "config-seat-unknown"

#: Everything recorded as a :class:`~embodiment.config_change.ConfigRefusal`.
LIFECYCLE_REFUSAL_CODES = (
    CHANGE_UNKNOWN_PROPOSAL,
    CHANGE_DUPLICATE_PROPOSAL,
    CHANGE_UNVERIFIED,
    CHANGE_ALREADY_APPLIED,
    CHANGE_TERMINAL,
    CHANGE_VERIFICATION_FAILED,
    CHANGE_VERIFIER_UNAVAILABLE,
    CHANGE_VERIFIER_UNREADABLE,
    CHANGE_STALE_VERIFICATION,
    CHANGE_UNKNOWN_RUN,
    CHANGE_UNKNOWN_SEAT,
)
#: Everything recorded as a :class:`ConfigDeferral`.
LIFECYCLE_DEFERRAL_CODES = (CHANGE_SEAT_BUSY,)
#: The whole vocabulary this module can mint.
LIFECYCLE_CODES = LIFECYCLE_REFUSAL_CODES + LIFECYCLE_DEFERRAL_CODES

#: The admission rules, in words, so a host can compose them into whatever
#: authority text it hands its strategist.
#:
#: This exists because of embodiment#58: ``SCOPE_AUTHORITY`` never stated the
#: ``scope_id``-must-be-new rule it graded proposals on, and 47 of 93 proposals
#: in one ScopeBench arm were refused as duplicates — both of the live session's
#: completed reviews among them. A rule a proposer cannot read is a rule that
#: only produces refusals. These are strings, not a prompt: framing is
#: ``framing.py``'s and the strategist's authority text is the host's.
LIFECYCLE_RULES = (
    "Every change_id must be new. A change_id already used in this session is "
    "refused as a duplicate, whatever its content.",
    "A change is a proposal until a verification suite has passed against it. "
    "Nothing is applied unverified.",
    "A change applies only while the seat it configures has no run open. A "
    "change proposed mid-run is not lost — it lands once that seat is idle.",
    "A verification is evidence about the configuration it ran against. If "
    "another change lands first, the proposal is verified again before it can "
    "apply.",
)


# ── bounds ────────────────────────────────────────────────────────────────────

#: Cap on a recorded reason's text. ``scope.py`` / ``config_change.py``'s value.
_MAX_REASON_LEN = 500
#: How a canonical row escapes its own separators, so a prompt containing tabs
#: and newlines cannot forge extra rows into the digest.
_ESCAPES = (("\\", "\\\\"), ("\t", "\\t"), ("\n", "\\n"), ("\r", "\\r"))
#: What :func:`compose_prompt` joins sections with.
_SECTION_JOIN = "\n\n"


# ── coercion helpers (never raise) ────────────────────────────────────────────


def _text(value: Any) -> str:
    """Coerce *value* to text. Never raises — ``scope.py``'s ``_plain``, cited."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


def _escape(value: Any) -> str:
    """Render *value* as one canonical field: no separator can survive inside it."""
    text = _text(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def _ids(value: Any) -> tuple[str, ...]:
    """Coerce a capability-id payload into the SET it is: deduped and sorted.

    t3 made a selection a set rather than a delta on purpose — deltas compound
    invisibly and ``c7``'s ratchet condition exists to make compounding
    detectable. Sorting is what makes "same set, same identity" true of
    :attr:`SeatConfig.config_sha`, so a host reordering its own list does not
    read as a configuration change.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        entries: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        entries = tuple(value)
    else:
        entries = (value,)
    return tuple(sorted({_text(entry).strip() for entry in entries} - {""}))


# ── the configuration a seat runs under ───────────────────────────────────────


@dataclass(frozen=True)
class PromptSection:
    """One named section of a seat's prompt. Order is meaningful and preserved."""

    section: str = ""
    text: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "section", _text(self.section).strip())
        object.__setattr__(self, "text", _text(self.text))

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "text": self.text}


@dataclass(frozen=True)
class KnowledgeEntry:
    """One attributed claim in a seat's knowledge block.

    ``origin`` is carried into the effective configuration rather than left in
    the change record, because ``c30``'s attribution rule is about what senses
    *holds*, not only about what was once written: a knowledge entry whose writer
    cannot be named from the seat's own configuration is an anonymous channel
    into the operator's ear. Where these live durably is task ``t8``'s (eidetic
    owns the memory mechanics); this is the in-session effective view.
    """

    entry_id: str = ""
    text: str = ""
    origin: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _text(self.entry_id).strip())
        object.__setattr__(self, "text", _text(self.text))
        object.__setattr__(self, "origin", _text(self.origin).strip())

    def to_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, "text": self.text, "origin": self.origin}


def _sections(value: Any) -> tuple[PromptSection, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        entry if isinstance(entry, PromptSection) else PromptSection(*_pair(entry))
        for entry in value
    )


def _pair(entry: Any) -> tuple[str, str]:
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return _text(entry[0]), _text(entry[1])
    return _text(entry), ""


def _entries(value: Any) -> tuple[KnowledgeEntry, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(entry for entry in value if isinstance(entry, KnowledgeEntry))


@dataclass(frozen=True)
class SeatConfig:
    """Everything one seat runs under — and the thing a run is pinned to.

    Frozen, and handed to a run by value at :meth:`ConfigLifecycle.begin_run`.
    That is the first of the two layers holding the per-seat invariant: even a
    bug elsewhere cannot mutate a configuration a run is already using. The
    second layer is the apply gate, which will not install anything while the
    seat has an open run.

    :attr:`config_sha` is the per-seat analogue of the system-prompt sha live
    session 1 measured. It is a content hash over the canonical rendering, so it
    is order-sensitive exactly where order is meaningful (prompt sections,
    knowledge entries) and order-insensitive where the field is a set
    (capability ids).
    """

    seat: str = ""
    prompt: tuple[PromptSection, ...] = ()
    knowledge: tuple[KnowledgeEntry, ...] = ()
    tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "seat", _text(self.seat).strip())
        object.__setattr__(self, "prompt", _sections(self.prompt))
        object.__setattr__(self, "knowledge", _entries(self.knowledge))
        object.__setattr__(self, "tools", _ids(self.tools))
        object.__setattr__(self, "permissions", _ids(self.permissions))

    def section(self, name: Any) -> Optional[PromptSection]:
        """The named prompt section, or ``None``."""
        wanted = _text(name).strip()
        for entry in self.prompt:
            if entry.section == wanted:
                return entry
        return None

    def entry(self, entry_id: Any) -> Optional[KnowledgeEntry]:
        """The named knowledge entry, or ``None``."""
        wanted = _text(entry_id).strip()
        for entry in self.knowledge:
            if entry.entry_id == wanted:
                return entry
        return None

    def canonical_text(self) -> str:
        """One row per element, every field escaped. What :attr:`config_sha` hashes.

        Legible on purpose: task ``t7``'s introspection report needs to render an
        effective configuration a human can read beside its digest, and a digest
        whose preimage nobody can print is a digest nobody can check.
        """
        rows = [f"seat\t{_escape(self.seat)}"]
        rows += [f"prompt\t{_escape(s.section)}\t{_escape(s.text)}" for s in self.prompt]
        rows += [
            f"knowledge\t{_escape(k.entry_id)}\t{_escape(k.origin)}\t{_escape(k.text)}"
            for k in self.knowledge
        ]
        rows += [f"tool\t{_escape(name)}" for name in self.tools]
        rows += [f"permission\t{_escape(name)}" for name in self.permissions]
        return "\n".join(rows)

    @property
    def config_sha(self) -> str:
        """This configuration's content hash — the per-seat constant-sha subject."""
        return hashlib.sha256(self.canonical_text().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "config_sha": self.config_sha,
            "prompt": [entry.to_dict() for entry in self.prompt],
            "knowledge": [entry.to_dict() for entry in self.knowledge],
            "tools": list(self.tools),
            "permissions": list(self.permissions),
        }


def compose_prompt(config: Any) -> str:
    """The reference composition: a seat's prompt sections, in order.

    A *reference* — a host with its own composition replaces it, and
    ``framing.py`` owns anything Gwen-shaped. What matters to this module's
    invariant is only that composition is a **pure function of the
    configuration**, so a constant :attr:`SeatConfig.config_sha` across a run
    implies constant prompt bytes across that run. The converse does not hold
    (renaming a section changes the digest and not the bytes), which is the
    honest direction for a safety claim to run in.

    The knowledge block is deliberately *not* composed here: where attributed
    claims enter a seat's context, and under what grounding clause, is task
    ``t8``'s and task ``t11``'s, not this gate's.
    """
    if not isinstance(config, SeatConfig):
        return ""
    return _SECTION_JOIN.join(entry.text for entry in config.prompt if entry.text)


# ── applying one typed unit: whole-surface, never a merge ─────────────────────


def _apply_prompt(config: SeatConfig, change: PromptChange) -> SeatConfig:
    """Replace the named section in place, or append it.

    Empty text keeps the section *declared*. Clearing a section is a real change
    and removing the row instead would make revert-to-baseline need a second
    verb — ``t6``'s job is easier if every change is expressible as one unit.
    """
    incoming = PromptSection(section=change.section, text=change.text)
    if config.section(change.section) is None:
        return replace(config, prompt=config.prompt + (incoming,))
    return replace(
        config,
        prompt=tuple(
            incoming if entry.section == change.section else entry for entry in config.prompt
        ),
    )


def _apply_knowledge(config: SeatConfig, change: KnowledgeChange) -> SeatConfig:
    """Upsert one attributed claim, dropping anything it supersedes.

    ``supersedes`` is honoured first, so a unit that supersedes its own
    ``entry_id`` reads as a straight replacement rather than as a self-cancelling
    pair.
    """
    superseded = _text(change.supersedes).strip()
    kept = tuple(
        entry
        for entry in config.knowledge
        if not (superseded and entry.entry_id == superseded)
        if entry.entry_id != change.entry_id
    )
    incoming = KnowledgeEntry(entry_id=change.entry_id, text=change.text, origin=change.origin)
    if config.entry(change.entry_id) is not None and not superseded:
        return replace(
            config,
            knowledge=tuple(
                incoming if entry.entry_id == change.entry_id else entry
                for entry in config.knowledge
            ),
        )
    return replace(config, knowledge=kept + (incoming,))


def _apply_selection(config: SeatConfig, change: CapabilitySelection) -> SeatConfig:
    """Replace the whole capability surface of this unit's kind.

    ``capability_ids`` is the seat's **entire** surface after the change, so this
    assigns rather than unions — the check ``t3`` asked ``t4`` to confirm. An
    empty selection therefore clears the surface, which is what "the seat may
    call nothing" has to look like if revert is to be an ordinary change.
    """
    if change.KIND == CAPABILITY_KIND_PERMISSION:
        return replace(config, permissions=change.capability_ids)
    return replace(config, tools=change.capability_ids)


def apply_change(config: Any, change: Any) -> SeatConfig:
    """The candidate configuration *config* would become under *change*.

    Pure and total: no IO, no recording, no seat routing (the caller routes — the
    lifecycle dispatches on :attr:`ConfigChange.seat`). A change family this
    function does not know returns *config* unchanged and by identity, which is
    detectable rather than plausible: ``tests/test_config_lifecycle.py`` walks
    every entry of :data:`~embodiment.config_change.CHANGE_UNITS` and asserts
    each one moves the digest, so a new target family that forgot its branch here
    fails a test rather than becoming a silent no-op.
    """
    if not isinstance(config, SeatConfig):
        config = SeatConfig()
    if isinstance(change, PromptChange):
        return _apply_prompt(config, change)
    if isinstance(change, KnowledgeChange):
        return _apply_knowledge(config, change)
    if isinstance(change, CapabilitySelection):
        return _apply_selection(config, change)
    return config


# ── the run handle ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeatRun:
    """One open run of one seat, pinned to the configuration it started under.

    A host's seat reads its configuration **from this handle**, not from the
    lifecycle. That is what makes the per-seat invariant structural rather than
    procedural: the handle is frozen, so the configuration a run was handed
    cannot be swapped, and the apply gate independently refuses to install
    anything while the run is open. Either layer alone would hold; both together
    mean a host has to work at it to observe a mid-run change.
    """

    seat: str = ""
    run_id: str = ""
    config: SeatConfig = field(default_factory=SeatConfig)

    @property
    def config_sha(self) -> str:
        """The pinned configuration's digest — constant for this run's whole life."""
        return self.config.config_sha

    @property
    def prompt(self) -> str:
        """The reference composition of the pinned configuration."""
        return compose_prompt(self.config)

    def to_dict(self) -> dict[str, Any]:
        return {"seat": self.seat, "run_id": self.run_id, "config_sha": self.config_sha}


# ── verification ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VerificationRequest:
    """What the injected suite is asked: *would this seat still be sound like this?*

    It carries the **candidate** — the configuration the seat would actually run
    under — because ``drone.py``'s smoke runs against the staged copy and not
    against the source handed in. A suite graded on the change alone would be
    grading something no seat ever sees.
    """

    seat: str = ""
    change: Optional[ConfigChange] = None
    baseline: SeatConfig = field(default_factory=SeatConfig)
    candidate: SeatConfig = field(default_factory=SeatConfig)

    @property
    def target(self) -> str:
        return self.change.target if isinstance(self.change, ConfigChange) else ""

    @property
    def change_id(self) -> str:
        return self.change.change_id if isinstance(self.change, ConfigChange) else ""


@dataclass(frozen=True)
class VerificationResult:
    """What the suite answered, plus what the gate stamped onto it.

    ``candidate_sha`` and ``baseline_sha`` are **stamped by the gate**, never
    read from the verifier's answer — t3's ``_stamp`` discipline, and for the
    same reason: a suite that could name the configuration it tested could claim
    to have tested one it did not. They are what the apply gate re-checks, so a
    verification cannot be spent on a baseline that has since moved.
    """

    passed: bool = False
    summary: str = ""
    suite: str = ""
    checks_run: int = 0
    checks_failed: int = 0
    baseline_sha: str = ""
    candidate_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "suite": self.suite,
            "checks_run": self.checks_run,
            "checks_failed": self.checks_failed,
            "baseline_sha": self.baseline_sha,
            "candidate_sha": self.candidate_sha,
        }


class VerifierFn(Protocol):
    """The injected suite. One call, one :class:`VerificationResult`.

    Structural, so a host wires a function, a bound method or a class with
    ``__call__`` — the shape ``loop.py`` uses for ``CompleteFn`` and
    ``ToolExecutor``. It may do anything a host's suite needs (subprocess,
    network, a real seat call); none of that is this module's business, and none
    of it can reach a host's main path — an exception is caught, recorded, and
    read as *no evidence*.
    """

    def __call__(self, request: VerificationRequest) -> VerificationResult:  # pragma: no cover
        ...


# ── records ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Proposal:
    """One change unit and where it is in the lifecycle."""

    change: ConfigChange
    state: str = STATE_PROPOSED
    sequence: int = 0
    verification: Optional[VerificationResult] = None

    @property
    def change_id(self) -> str:
        return self.change.change_id

    @property
    def seat(self) -> str:
        return self.change.seat

    @property
    def target(self) -> str:
        return self.change.target

    @property
    def origin(self) -> str:
        return self.change.origin

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "seat": self.seat,
            "target": self.target,
            "origin": self.origin,
            "state": self.state,
            "sequence": self.sequence,
            "change": self.change.to_dict(),
            "verification": (
                self.verification.to_dict()
                if isinstance(self.verification, VerificationResult)
                else None
            ),
        }


@dataclass(frozen=True)
class ConfigTransition:
    """One recorded state change (constraint C3), and ``t5``'s translation input.

    ``sequence`` and no timestamp: there is no clock in this layer, so order is
    what can be recorded honestly. ``config_sha`` is the *seat's* effective
    digest after the transition, which is what makes "which change produced this
    configuration" answerable from the stream alone — task ``t7``'s requirement,
    left derivable rather than pre-computed here.
    """

    sequence: int = 0
    change_id: str = ""
    seat: str = ""
    target: str = ""
    origin: str = ""
    from_state: str = ""
    to_state: str = ""
    reason: str = ""
    verdict: str = ""
    config_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "change_id": self.change_id,
            "seat": self.seat,
            "target": self.target,
            "origin": self.origin,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "verdict": self.verdict,
            "config_sha": self.config_sha,
        }


@dataclass(frozen=True)
class ConfigDeferral:
    """The gate working: a seat was busy, so nothing was changed under it.

    Carries :class:`~embodiment.config_change.ConfigRefusal`'s field names and
    ``to_dict`` keys so one reader folds both — and deliberately does **not**
    subclass :class:`~embodiment.config_change.ConfigDegradation`, so a host
    counting degradations counts faults and not normal operation. See the module
    docstring for why that separation is the C3-honest one.
    """

    code: str = CHANGE_SEAT_BUSY
    reason: str = ""
    step_index: int = 0
    model_turns: int = 0
    seat: str = ""
    target: str = ""
    change_id: str = ""
    origin: str = ""
    blocking_runs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
            "seat": self.seat,
            "target": self.target,
            "change_id": self.change_id,
            "origin": self.origin,
            "blocking_runs": list(self.blocking_runs),
        }


@dataclass(frozen=True)
class ApplyOutcome:
    """What one apply attempt did. Exactly one of the three outcomes is true."""

    change_id: str = ""
    seat: str = ""
    state: str = ""
    applied: bool = False
    deferred: bool = False
    config_sha: str = ""
    refusal: Optional[ConfigRefusal] = None
    deferral: Optional[ConfigDeferral] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "seat": self.seat,
            "state": self.state,
            "applied": self.applied,
            "deferred": self.deferred,
            "config_sha": self.config_sha,
            "refusal": self.refusal.to_dict() if self.refusal is not None else None,
            "deferral": self.deferral.to_dict() if self.deferral is not None else None,
        }


@dataclass(frozen=True)
class AdvanceReport:
    """What one :meth:`ConfigLifecycle.advance` pass did, by change id."""

    verified: tuple[str, ...] = ()
    applied: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": list(self.verified),
            "applied": list(self.applied),
            "rejected": list(self.rejected),
            "deferred": list(self.deferred),
            "refused": list(self.refused),
        }


# ── the gate ──────────────────────────────────────────────────────────────────


class ConfigLifecycle:
    """propose → verify → apply, gated on seat-idle AND suite-pass.

    Stateful policy with no mechanics: no thread, no clock, no IO, no store. A
    host drives it with four calls — :meth:`propose`, :meth:`begin_run`,
    :meth:`end_run`, :meth:`advance` — and reads three append-only streams:
    :attr:`transitions`, :attr:`degradations` and :attr:`deferrals`.

    The whole host pattern is two lines around a drive::

        run = life.begin_run("worker")
        result = loop.run(..., system_prompt=compose_prompt(run.config))
        life.end_run(run)
        life.advance()          # everything the gate now allows lands here

    Nothing in that pattern can reconfigure the seat between ``begin_run`` and
    ``end_run``, which is the point.

    Args:
        verifier: the injected suite. With none wired, nothing can ever reach
            :data:`STATE_VERIFIED` — fail closed, and recorded every time.
        catalog: the host's capability declaration, re-checked at apply through
            :func:`~embodiment.config_change.revalidate`. A per-call ``catalog``
            overrides it, because the declaration in force at apply time is the
            one that matters.
        seats: initial per-seat configurations. A seat with no entry starts
            empty.
    """

    def __init__(
        self,
        *,
        verifier: Optional[VerifierFn] = None,
        catalog: Optional[CapabilityCatalog] = None,
        seats: Optional[Mapping[str, SeatConfig]] = None,
    ) -> None:
        self.verifier = verifier
        self.catalog = catalog if isinstance(catalog, CapabilityCatalog) else None
        self._configs: dict[str, SeatConfig] = {}
        if isinstance(seats, Mapping):
            for seat, config in seats.items():
                if isinstance(config, SeatConfig):
                    self._configs[_text(seat).strip()] = config
        self._proposals: dict[str, Proposal] = {}
        self._runs: dict[str, dict[str, SeatRun]] = {}
        self._transitions: list[ConfigTransition] = []
        self._degradations: list[ConfigRefusal] = []
        self._deferrals: list[ConfigDeferral] = []
        # Two counters, not one. ``Proposal.sequence`` is propose order and
        # ``ConfigTransition.sequence`` is transition order; sharing a counter
        # would make both numbers mean "events of some kind so far", which is a
        # number nobody can use for either job.
        self._transition_sequence = 0
        self._propose_sequence = 0
        self._run_counter = 0

    # ── reading ──────────────────────────────────────────────────────────────

    def effective(self, seat: Any) -> SeatConfig:
        """The configuration *seat* runs under right now. Never ``None``."""
        name = _text(seat).strip()
        return self._configs.get(name) or SeatConfig(seat=name)

    def is_idle(self, seat: Any) -> bool:
        """Whether *seat* has no open run — half the apply gate."""
        return not self._runs.get(_text(seat).strip())

    def open_runs(self, seat: Any = None) -> tuple[SeatRun, ...]:
        """Every open run, or every open run of one seat."""
        if seat is None:
            return tuple(run for runs in self._runs.values() for run in runs.values())
        return tuple(self._runs.get(_text(seat).strip(), {}).values())

    def proposal(self, change_id: Any) -> Optional[Proposal]:
        """One proposal by id, or ``None``."""
        return self._proposals.get(_text(change_id).strip())

    def proposals(self, *, state: Any = None, seat: Any = None) -> tuple[Proposal, ...]:
        """Every proposal, in propose order, optionally filtered."""
        wanted_state = _text(state).strip() if state is not None else None
        wanted_seat = _text(seat).strip() if seat is not None else None
        return tuple(
            proposal
            for proposal in sorted(self._proposals.values(), key=lambda entry: entry.sequence)
            if wanted_state is None or proposal.state == wanted_state
            if wanted_seat is None or proposal.seat == wanted_seat
        )

    @property
    def transitions(self) -> tuple[ConfigTransition, ...]:
        """Every recorded state change — ``t5``'s translation input, never drained."""
        return tuple(self._transitions)

    @property
    def degradations(self) -> tuple[ConfigRefusal, ...]:
        """Every refusal (constraint C3). Deferrals are NOT in here, by design."""
        return tuple(self._degradations)

    @property
    def deferrals(self) -> tuple[ConfigDeferral, ...]:
        """Every time the gate held a change back because a seat was working."""
        return tuple(self._deferrals)

    # ── recording ────────────────────────────────────────────────────────────

    def _next(self) -> int:
        value = self._transition_sequence
        self._transition_sequence += 1
        return value

    def _next_proposal(self) -> int:
        value = self._propose_sequence
        self._propose_sequence += 1
        return value

    def _record(
        self,
        change: Optional[ConfigChange],
        from_state: str,
        to_state: str,
        reason: str,
        *,
        verdict: str = "",
    ) -> ConfigTransition:
        seat = change.seat if change is not None else ""
        transition = ConfigTransition(
            sequence=self._next(),
            change_id=change.change_id if change is not None else "",
            seat=seat,
            target=change.target if change is not None else "",
            origin=change.origin if change is not None else "",
            from_state=from_state,
            to_state=to_state,
            reason=reason[:_MAX_REASON_LEN],
            verdict=verdict,
            config_sha=self.effective(seat).config_sha if seat else "",
        )
        self._transitions.append(transition)
        return transition

    def _refuse(
        self,
        code: str,
        reason: str,
        *,
        change: Optional[ConfigChange] = None,
        change_id: str = "",
        seat: str = "",
        target: str = "",
        origin: str = "",
    ) -> ConfigRefusal:
        if change is not None:
            change_id = change_id or change.change_id
            seat = seat or change.seat
            target = target or change.target
            origin = origin or change.origin
        refusal = ConfigRefusal(
            code=code,
            reason=reason[:_MAX_REASON_LEN],
            seat=seat,
            target=target,
            change_id=change_id,
            origin=origin,
        )
        self._degradations.append(refusal)
        return refusal

    def _defer(self, proposal: Proposal, reason: str) -> ConfigDeferral:
        deferral = ConfigDeferral(
            code=CHANGE_SEAT_BUSY,
            reason=reason[:_MAX_REASON_LEN],
            seat=proposal.seat,
            target=proposal.target,
            change_id=proposal.change_id,
            origin=proposal.origin,
            blocking_runs=tuple(sorted(self._runs.get(proposal.seat, {}))),
        )
        self._deferrals.append(deferral)
        return deferral

    def _store(self, proposal: Proposal) -> Proposal:
        self._proposals[proposal.change_id] = proposal
        return proposal

    # ── the seat's own run lifecycle ─────────────────────────────────────────

    def begin_run(self, seat: Any, *, run_id: Any = "") -> SeatRun:
        """Open a run for *seat* and hand back the configuration it is pinned to.

        The handle is frozen and carries the configuration by value. While any
        run of a seat is open, nothing can be applied to that seat — including
        by an overlapping second run, which therefore sees the same
        configuration the first one did.

        Never raises. A seat outside
        :data:`~embodiment.config_change.CHANGE_SEATS` still gets a working
        handle, and a degradation naming it: nothing could ever be applied to
        such a seat, so a typo here would otherwise be a seat that silently never
        receives configuration.
        """
        name = _text(seat).strip()
        if name not in CHANGE_SEATS:
            self._refuse(
                CHANGE_UNKNOWN_SEAT,
                f"a run was opened for the seat {name!r}, which is not one of "
                f"{', '.join(CHANGE_SEATS)}; the run is tracked, but no configuration "
                "change can ever target that seat",
                seat=name,
            )
        self._run_counter += 1
        identifier = _text(run_id).strip() or f"{name or 'seat'}-run-{self._run_counter}"
        run = SeatRun(seat=name, run_id=identifier, config=self.effective(name))
        self._runs.setdefault(name, {})[identifier] = run
        return run

    def end_run(self, run: Any) -> None:
        """Close a run. Takes the handle :meth:`begin_run` returned, or its id.

        Never raises. Closing an unknown or already-closed run is recorded — a
        run this lifecycle still believes is open would hold its seat forever,
        which is the failure this gate can produce and must therefore name.
        """
        if isinstance(run, SeatRun):
            seat, identifier = run.seat, run.run_id
        else:
            seat, identifier = "", _text(run).strip()
            for candidate, runs in self._runs.items():
                if identifier in runs:
                    seat = candidate
                    break
        if not self._runs.get(seat, {}).pop(identifier, None):
            self._refuse(
                CHANGE_UNKNOWN_RUN,
                f"end_run was handed {identifier!r}, which this lifecycle has no open "
                "run for; it was either never begun or already ended",
                seat=seat,
            )
            return
        if not self._runs.get(seat):
            self._runs.pop(seat, None)

    # ── propose ──────────────────────────────────────────────────────────────

    def propose(
        self, unit: Any, *, catalog: Optional[CapabilityCatalog] = None
    ) -> Optional[Proposal]:
        """Admit one change unit into the lifecycle. ``None`` means refused.

        *unit* is either a raw payload — read through
        :func:`~embodiment.config_change.change_from_payload`, so t3's whole
        admission contract applies unchanged — or an already-accepted
        :class:`~embodiment.config_change.ConfigChange`.

        Every refusal lands in :attr:`degradations`; nothing is dropped. A
        ``change_id`` already used in this session is refused as a duplicate,
        and that rule is stated in :data:`LIFECYCLE_RULES` rather than left for a
        proposer to discover through refusals (embodiment#58's lesson).
        """
        if isinstance(unit, ConfigChange):
            change: Optional[ConfigChange] = unit
        else:
            change, refusal = change_from_payload(unit, catalog=self._catalog(catalog))
            if refusal is not None:
                self._degradations.append(refusal)
                return None
        if change is None:  # pragma: no cover - change_from_payload's contract
            return None
        if change.change_id in self._proposals:
            self._refuse(
                CHANGE_DUPLICATE_PROPOSAL,
                f"a change already exists under the id {change.change_id!r}; every "
                "change_id must be new, whatever the unit's content",
                change=change,
            )
            return None
        proposal = self._store(
            Proposal(change=change, state=STATE_PROPOSED, sequence=self._next_proposal())
        )
        self._record(change, "", STATE_PROPOSED, "admitted into the configuration lifecycle")
        return proposal

    # ── verify ───────────────────────────────────────────────────────────────

    def verify(self, change_id: Any) -> Optional[Proposal]:
        """Run the injected suite against the candidate, if the seat is idle.

        Returns the proposal in whatever state the attempt left it, or ``None``
        when no proposal is registered under *change_id*. Never raises: a
        verifier that raises or answers in an unreadable shape is recorded and
        read as *no evidence*, which leaves the proposal proposable — absence of
        evidence must never approve a change, and it must not condemn one
        either. Only a suite that **ran and reported failure** rejects.
        """
        proposal = self.proposal(change_id)
        if proposal is None:
            self._refuse(
                CHANGE_UNKNOWN_PROPOSAL,
                f"no proposal is registered under {_text(change_id)!r}",
                change_id=_text(change_id).strip(),
            )
            return None
        if proposal.state in TERMINAL_STATES:
            self._refuse(
                CHANGE_TERMINAL if proposal.state == STATE_REJECTED else CHANGE_ALREADY_APPLIED,
                f"{proposal.change_id!r} is {proposal.state}; nothing moves out of a "
                "terminal state, and a re-authored change is a new proposal with a new id",
                change=proposal.change,
            )
            return proposal
        if not self.is_idle(proposal.seat):
            self._defer(
                proposal,
                f"the {proposal.seat} seat has a run open, so its suite was not run; the "
                "proposal stays proposed and will be verified once the seat is idle",
            )
            return proposal
        return self._run_suite(proposal)

    def _run_suite(self, proposal: Proposal) -> Proposal:
        """Call the verifier and read its answer. Never raises."""
        baseline = self.effective(proposal.seat)
        candidate = apply_change(baseline, proposal.change)
        if self.verifier is None:
            self._refuse(
                CHANGE_VERIFIER_UNAVAILABLE,
                "no verification suite is wired, so nothing can be verified and nothing "
                "can be applied; wire a verifier when constructing the lifecycle",
                change=proposal.change,
            )
            return proposal
        request = VerificationRequest(
            seat=proposal.seat, change=proposal.change, baseline=baseline, candidate=candidate
        )
        try:
            answer = self.verifier(request)
        except Exception as exc:  # noqa: BLE001  # a host's suite never reaches its own main path
            self._refuse(
                CHANGE_VERIFIER_UNREADABLE,
                f"the verification suite raised {type(exc).__name__}: {exc}; that is no "
                "evidence either way, so the proposal stays proposed and unapplied",
                change=proposal.change,
            )
            return proposal
        if not isinstance(answer, VerificationResult):
            self._refuse(
                CHANGE_VERIFIER_UNREADABLE,
                f"the verification suite answered with {type(answer).__name__}, not a "
                "VerificationResult; an unreadable verdict is no evidence, so the "
                "proposal stays proposed and unapplied",
                change=proposal.change,
            )
            return proposal
        return self._read_verdict(proposal, answer, baseline, candidate)

    def _read_verdict(
        self,
        proposal: Proposal,
        answer: VerificationResult,
        baseline: SeatConfig,
        candidate: SeatConfig,
    ) -> Proposal:
        """Stamp the verdict with the shas it actually ran against, and record it."""
        verification = replace(
            answer, baseline_sha=baseline.config_sha, candidate_sha=candidate.config_sha
        )
        if not verification.passed:
            updated = self._store(
                replace(proposal, state=STATE_REJECTED, verification=verification)
            )
            self._record(
                proposal.change,
                proposal.state,
                STATE_REJECTED,
                f"the verification suite failed: {verification.summary or 'no summary given'}",
                verdict="failed",
            )
            self._refuse(
                CHANGE_VERIFICATION_FAILED,
                f"the {verification.suite or 'verification'} suite failed for "
                f"{proposal.change_id!r} ({verification.summary or 'no summary given'}); "
                "the change was not applied and cannot be",
                change=proposal.change,
            )
            return updated
        updated = self._store(replace(proposal, state=STATE_VERIFIED, verification=verification))
        self._record(
            proposal.change,
            proposal.state,
            STATE_VERIFIED,
            f"the verification suite passed: {verification.summary or 'no summary given'}",
            verdict="passed",
        )
        return updated

    # ── apply ────────────────────────────────────────────────────────────────

    def _catalog(self, override: Optional[CapabilityCatalog]) -> Optional[CapabilityCatalog]:
        return override if isinstance(override, CapabilityCatalog) else self.catalog

    def apply(self, change_id: Any, *, catalog: Optional[CapabilityCatalog] = None) -> ApplyOutcome:
        """Install one verified change, if the gate allows it. Never raises.

        The checks run in this order, and the order is part of the contract:

        1. a proposal exists (:data:`CHANGE_UNKNOWN_PROPOSAL`);
        2. it is in :data:`STATE_VERIFIED` — a terminal proposal and an
           unverified one are different refusals with different fixes;
        3. the seat is idle (:data:`CHANGE_SEAT_BUSY`) — checked before anything
           that could change the proposal's state, because a busy seat is a
           timing fact and a deferral must never spend a proposal;
        4. the host's catalog still declares what the unit selected
           (:func:`~embodiment.config_change.revalidate`) — **before** the
           freshness check, because it is the terminal one: re-verifying cannot
           re-declare a capability the host withdrew, so a doubly-stale proposal
           is reported by the fault that actually needs re-authoring;
        5. the verification is still evidence about the configuration now in
           force (:data:`CHANGE_STALE_VERIFICATION`) — recoverable, so this
           demotes to :data:`STATE_PROPOSED` rather than rejecting.
        """
        proposal = self.proposal(change_id)
        if proposal is None:
            return ApplyOutcome(
                change_id=_text(change_id).strip(),
                refusal=self._refuse(
                    CHANGE_UNKNOWN_PROPOSAL,
                    f"no proposal is registered under {_text(change_id)!r}",
                    change_id=_text(change_id).strip(),
                ),
            )
        blocked = self._state_refusal(proposal)
        if blocked is not None:
            return self._outcome(proposal, refusal=blocked)
        if not self.is_idle(proposal.seat):
            return self._outcome(
                proposal,
                deferral=self._defer(
                    proposal,
                    f"the {proposal.seat} seat has a run open; its configuration is not "
                    "changed under it, and this change lands once that run ends",
                ),
            )
        stale_catalog = revalidate(proposal.change, self._catalog(catalog))
        if stale_catalog is not None:
            return self._reject_stale_catalog(proposal, stale_catalog)
        candidate = apply_change(self.effective(proposal.seat), proposal.change)
        verification = proposal.verification
        if verification is None or verification.candidate_sha != candidate.config_sha:
            return self._demote_stale_verification(proposal, candidate)
        return self._install(proposal, candidate)

    def _state_refusal(self, proposal: Proposal) -> Optional[ConfigRefusal]:
        """The refusal this proposal's state earns, or ``None`` when it may apply."""
        if proposal.state == STATE_VERIFIED:
            return None
        if proposal.state == STATE_APPLIED:
            return self._refuse(
                CHANGE_ALREADY_APPLIED,
                f"{proposal.change_id!r} is already applied; applying it twice would "
                "record a second transition for a configuration that did not move",
                change=proposal.change,
            )
        if proposal.state == STATE_REJECTED:
            return self._refuse(
                CHANGE_TERMINAL,
                f"{proposal.change_id!r} was rejected and cannot be applied; author a "
                "new change with a new id",
                change=proposal.change,
            )
        return self._refuse(
            CHANGE_UNVERIFIED,
            f"{proposal.change_id!r} is {proposal.state}: no verification suite has "
            "passed against it, so there is nothing to apply it on the strength of",
            change=proposal.change,
        )

    def _reject_stale_catalog(self, proposal: Proposal, refusal: ConfigRefusal) -> ApplyOutcome:
        """The host's declaration moved. Terminal — re-verifying cannot fix it."""
        self._degradations.append(refusal)
        updated = self._store(replace(proposal, state=STATE_REJECTED))
        self._record(
            proposal.change,
            proposal.state,
            STATE_REJECTED,
            f"refused against the catalog in force at apply time ({refusal.code}); the "
            "unit selects capability ids that were validated against a declaration that "
            "has since moved, and re-authoring is the only fix",
            verdict="stale",
        )
        return self._outcome(updated, refusal=refusal)

    def _demote_stale_verification(self, proposal: Proposal, candidate: SeatConfig) -> ApplyOutcome:
        """Another change landed first, so this verdict is about a baseline that is gone."""
        was = proposal.verification.candidate_sha if proposal.verification else "(none)"
        refusal = self._refuse(
            CHANGE_STALE_VERIFICATION,
            f"{proposal.change_id!r} was verified against candidate {was[:12]} and the "
            f"candidate now in force is {candidate.config_sha[:12]}; a suite result is "
            "evidence about the configuration it ran against and no other, so the "
            "proposal was demoted and must be verified again",
            change=proposal.change,
        )
        updated = self._store(
            replace(proposal, state=STATE_PROPOSED, verification=proposal.verification)
        )
        self._record(
            proposal.change,
            proposal.state,
            STATE_PROPOSED,
            "the configuration moved between verify and apply; the verification is stale",
            verdict="stale",
        )
        return self._outcome(updated, refusal=refusal)

    def _install(self, proposal: Proposal, candidate: SeatConfig) -> ApplyOutcome:
        """Rebind the seat's configuration. The whole 'save' half of stage→smoke→save."""
        self._configs[proposal.seat] = candidate
        updated = self._store(replace(proposal, state=STATE_APPLIED))
        self._record(
            proposal.change,
            proposal.state,
            STATE_APPLIED,
            "applied to an idle seat after its verification suite passed",
            verdict="passed",
        )
        return self._outcome(updated, applied=True)

    def _outcome(
        self,
        proposal: Proposal,
        *,
        applied: bool = False,
        refusal: Optional[ConfigRefusal] = None,
        deferral: Optional[ConfigDeferral] = None,
    ) -> ApplyOutcome:
        return ApplyOutcome(
            change_id=proposal.change_id,
            seat=proposal.seat,
            state=proposal.state,
            applied=applied,
            deferred=deferral is not None,
            config_sha=self.effective(proposal.seat).config_sha,
            refusal=refusal,
            deferral=deferral,
        )

    # ── the pump ─────────────────────────────────────────────────────────────

    def advance(self, *, catalog: Optional[CapabilityCatalog] = None) -> AdvanceReport:
        """Move every pending proposal as far as its gate allows. One bounded pass.

        For each non-terminal proposal in propose order: defer it if its seat is
        busy, verify it if it has no fresh verdict, and apply it if it now has
        one. Verification happens **immediately before** the apply that uses it,
        so the suite always grades the configuration that will actually be in
        force — which is also why a single pass suffices and why this terminates
        (each proposal is considered exactly once, and a change that lands cannot
        make an earlier one pending again).

        That ordering is a partial, deliberate answer to the plan's parked risk
        ``r1`` (cross-type composition): it does not evaluate *combinations* of
        changes, but it does guarantee that each change's evidence was produced
        against the accumulated configuration rather than against a baseline that
        no longer exists. Whether a bench can measure composition at all stays
        open.

        **The seat-idle check here is a recording guarantee, not a safety one.**
        :meth:`verify` and :meth:`apply` each hold the gate independently, so
        deleting this check changes nothing about what gets applied — it changes
        what the host is *told*: without it a busy seat's proposal falls out of
        :attr:`AdvanceReport.deferred` and into
        :attr:`AdvanceReport.refused`, which reads as a judgement about the
        change rather than as the timing fact it is. Stated because the mutation
        proof for this task found it: a check in three places is a check whose
        copies mask each other, and each copy needs its own test.
        """
        verified: list[str] = []
        applied: list[str] = []
        rejected: list[str] = []
        deferred: list[str] = []
        refused: list[str] = []
        for proposal in self.proposals():
            if proposal.state in TERMINAL_STATES:
                continue
            if not self.is_idle(proposal.seat):
                self._defer(
                    proposal,
                    f"the {proposal.seat} seat has a run open; nothing is verified or "
                    "applied for a working seat",
                )
                deferred.append(proposal.change_id)
                continue
            current = self._freshen(proposal)
            if current.state == STATE_REJECTED:
                rejected.append(current.change_id)
                continue
            if current.state != STATE_VERIFIED:
                refused.append(current.change_id)
                continue
            verified.append(current.change_id)
            outcome = self.apply(current.change_id, catalog=catalog)
            if outcome.applied:
                applied.append(current.change_id)
            elif self._require(current.change_id).state == STATE_REJECTED:
                rejected.append(current.change_id)
            else:
                refused.append(current.change_id)
        return AdvanceReport(
            verified=tuple(verified),
            applied=tuple(applied),
            rejected=tuple(rejected),
            deferred=tuple(deferred),
            refused=tuple(refused),
        )

    def _freshen(self, proposal: Proposal) -> Proposal:
        """Verify *proposal* unless it already holds a verdict about the live baseline."""
        if proposal.state == STATE_VERIFIED and proposal.verification is not None:
            candidate = apply_change(self.effective(proposal.seat), proposal.change)
            if proposal.verification.candidate_sha == candidate.config_sha:
                return proposal
        return self.verify(proposal.change_id) or proposal

    def _require(self, change_id: str) -> Proposal:
        """The stored proposal, which :meth:`advance` has already established exists."""
        found = self.proposal(change_id)
        assert found is not None  # nosec B101 - advance only reaches this for stored ids
        return found


def seat_configs(configs: Optional[Sequence[SeatConfig]] = None) -> dict[str, SeatConfig]:
    """A ``{seat: config}`` map from a sequence of configurations, for construction.

    A convenience for a host wiring baselines — ``ConfigLifecycle(seats=...)``
    wants a mapping and a host usually has a list. Later configurations for one
    seat win, so the call reads like the last-writer-wins assignment it is.
    """
    found: dict[str, SeatConfig] = {}
    for config in configs or ():
        if isinstance(config, SeatConfig):
            found[config.seat] = config
    return found
