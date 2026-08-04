"""embodiment.config_revert — revert-to-baseline, and the ratchet re-check.

Task ``t6`` (issue #75, spec claims ``c7``/``h7``). Two requirements land in
this module, and they share one concept — a FIXED :class:`~embodiment.
config_lifecycle.SeatConfig` snapshot a host chooses to anchor against:

* "reverting to baseline is always possible and exercised by test."
* "successive gate-passing changes can be re-evaluated against a fixed
  baseline, so drift each individual gate misses is detectable."

Why this is the one failure mode with no analogue upstream
------------------------------------------------------------
The advisory tier degrades by being *ignored* — a directive nobody obeys is a
directive that changed nothing, and the failure is legible turn by turn.
Configuration degrades the opposite way: **advice evaporates, configuration
accumulates.** A run of individually gate-passing changes can compound into
something none of them would have passed alone, and it already happened in
miniature in the advisory design — live session 1 §4.2 (embodiment#66) records
a one-off task instruction becoming a durable persisted constraint. This
module is where that failure mode gets a fix rather than a footnote: a
always-expressible way back to a known point, and a check that looks past the
most recent change to the point the whole run started from.

Revert is an ORDINARY change, not an inferred inverse
--------------------------------------------------------
``t3`` made capability selection a **set, not a delta** specifically so that
"go back to baseline" never has to invert a history of edits — the whole
surface after a selection change *is* the change, so asserting the baseline's
own set undoes any number of prior selections in one step, from any starting
point. ``t3`` also added :data:`~embodiment.config_change.ORIGIN_HOST` for
exactly this: the host is the ground authority and may write every target,
which is what lets a revert be authored uniformly regardless of which origin
made the drift. :func:`compute_revert_changes` is the pure function that reads
a captured baseline :class:`~embodiment.config_lifecycle.SeatConfig` — "a
complete frozen snapshot," per ``t4``'s own docstring — and a seat's current
one, and returns the ordinary, host-originated payloads that close the gap.
:func:`revert_to_baseline` is the same computation driven through ``t4``'s own
propose → verify → apply gate: **revert bypasses nothing.** It is gated on
seat-idle exactly like any other change, so "no seat ever has its
configuration changed under it" holds for a revert precisely because a revert
is not a special case of that rule.

"Always possible" means always EXPRESSIBLE, not always instantaneous
------------------------------------------------------------------------
However tangled the history — however many strategist edits landed on top of
each other — the diff against ONE fixed object is always computable, and the
changes that express it are always proposable. Whether they land *immediately*
still depends on the same two facts any other change depends on: a verifier is
wired and passes, and the seat is idle. That is not a weaker claim than "always
possible" — it is the same claim :mod:`embodiment.config_lifecycle` already
makes for every other change, and revert does not get a side door around it.

The one honest structural gap, reported rather than hidden
----------------------------------------------------------------
``config_lifecycle.py``'s apply primitives are additive by design — a prompt
section can be *cleared* (text set to ``""``) but never removed from the
tuple, and a knowledge entry can be *replaced* but ``KnowledgeChange`` requires
non-blank text, so it can never be removed outright either (see that module's
``_apply_prompt`` / ``_apply_knowledge``). Both are load-bearing choices of
task ``t4``, not bugs, and this module cannot and does not change them (they
live in a file this task does not touch). Two consequences follow, and both
are made **observable** rather than silently accepted:

* A prompt section a later change introduced beyond the baseline is cleared to
  empty text on revert. :func:`~embodiment.config_lifecycle.compose_prompt`
  filters empty-text sections when it renders a seat's prompt, so the seat's
  actual behaviour matches baseline exactly — but the section's row can remain
  in :attr:`~embodiment.config_lifecycle.SeatConfig.canonical_text`, so
  ``config_sha`` may not match baseline bit-for-bit in this one case.
  :attr:`RevertOutcome.residual_prompt_sections` names it.
* A knowledge entry a later change introduced beyond the baseline **cannot be
  removed by any composition of typed changes** — there is no delete verb, by
  construction. :attr:`RevertOutcome.residual_knowledge_entries` names it, and
  :attr:`RevertOutcome.matches_baseline` reads ``False`` when either residual
  is non-empty, so a host is never told a revert was exact when it was not.

An honest gap beats a workaround: this module reports the gap countably rather
than inventing a delete verb the propose/verify/apply lifecycle was not built
to admit, and rather than quietly declaring victory on a config_sha that does
not actually match.

Attribution fidelity for reverted knowledge entries
--------------------------------------------------------
A knowledge entry's ``origin`` is both *who may write this target*
(:data:`~embodiment.config_change.CHANGE_AUTHORITY`) and *the entry's stored
attribution* (``config_lifecycle._apply_knowledge`` writes
``change.origin`` straight onto the resulting
:class:`~embodiment.config_lifecycle.KnowledgeEntry`) — the schema conflates
the two. A revert that always authored knowledge writes as
:data:`~embodiment.config_change.ORIGIN_HOST` would restore the right text
under the WRONG attribution. So :func:`compute_revert_changes` restores the
baseline entry's own recorded origin whenever that origin still has authority
over the target (checked against ``CHANGE_AUTHORITY`` itself, not assumed),
and falls back to the host only when it does not — which can only happen for a
baseline built by direct construction rather than through the admission path
(:mod:`embodiment.config_change`'s own gate would never have produced such an
entry in the first place).

The ratchet re-check: same verifier, a DIFFERENT baseline argument
------------------------------------------------------------------------
:meth:`~embodiment.config_lifecycle.ConfigLifecycle.verify` always compares a
proposal's candidate against ``self.effective(seat)`` — the state *right
before this one proposal*, i.e. the previous change. That is precisely the
comparison a ratchet cannot see through: three changes that each grew a prompt
by twenty characters against what came immediately before each pass a
"grew by less than fifty characters" gate three times running, while the
seat's prompt grew by sixty against where the run actually started.
:class:`RatchetGuard` re-runs the SAME injected suite with the SAME
:class:`~embodiment.config_lifecycle.VerificationRequest` shape, but supplies
a baseline that is **fixed at capture time and never moved by a check** — so a
failure here is drift no individual gate could have caught, by construction
rather than by luck. ``tests/test_config_revert.py``'s
``TestRatchetCatchesWhatIncrementalGatesMiss`` is the demonstration.

Where the rest of the tier lands
---------------------------------
This module holds no store and starts no thread: :class:`ConfigBaseline` is an
in-memory registry a host is free to persist however it persists everything
else in this tier (through ``t5``'s :class:`~embodiment.config_ledger.
ConfigLedger`, or its own seam) — nothing here assumes a persistence port of
its own. Recording that a change was *reverted*, as a durable historical fact,
is ``t5``'s :meth:`~embodiment.config_ledger.ConfigLedger.record_reverted` —
this module never touches the ledger, and the two do not overlap: the ledger
remembers that a change once governed and no longer does; this module is what
actually moves a seat's live configuration back to a known point. "Revert
restores configuration; it does not unsay what was already said" is the
ledger's job to keep honest, not this module's.

Stdlib only (``dataclasses``, ``itertools``, ``typing``), plus
:mod:`embodiment.config_change`, :mod:`embodiment.config_lifecycle` and
:mod:`embodiment.capability` (constraint C1). No import of
``embodiment.scope``, ``embodiment.scoped_run``, ``embodiment.
strategist_runner`` or ``embodiment.config_ledger`` — the advisory lane stays
byte-stable as the comparator arm, and this module's own ledger sibling stays
free of a dependency it does not need.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from embodiment.capability import CapabilityCatalog
from embodiment.config_change import (
    CHANGE_AUTHORITY,
    ORIGIN_HOST,
    SEAT_SENSES,
    SEAT_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_SENSES_PERMISSIONS,
    TARGET_SENSES_PROMPTS,
    TARGET_WORKER_KNOWLEDGE,
    TARGET_WORKER_PERMISSIONS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    ConfigDegradation,
)
from embodiment.config_lifecycle import (
    STATE_REJECTED,
    STATE_VERIFIED,
    ConfigLifecycle,
    SeatConfig,
    VerificationRequest,
    VerificationResult,
    VerifierFn,
)

__all__ = [
    # revert
    "CONFIG_REVERT_SEAT_MISMATCH",
    "CONFIG_REVERT_CODES",
    "compute_revert_changes",
    "revert_to_baseline",
    "RevertOutcome",
    # the shared fixed-baseline registry
    "ConfigBaseline",
    # ratchet
    "CONFIG_RATCHET_NO_BASELINE",
    "CONFIG_RATCHET_NO_VERIFIER",
    "CONFIG_RATCHET_FAILED",
    "CONFIG_RATCHET_CODES",
    "RatchetResult",
    "RatchetGuard",
]

# ── this module's own degradation vocabulary (C3) ───────────────────────────
#
# Prefixed distinctly from config_change.py's `config-change-*` and
# config_lifecycle.py's `config-change-*` / `config-seat-*`, even though every
# code here folds into the same ConfigDegradation stream those do — one code
# per distinct FIX, the tier's own rule, inherited here.

#: A revert was requested against a baseline whose own ``seat`` field disagrees
#: with the seat named in the call. Refused before anything is proposed.
CONFIG_REVERT_SEAT_MISMATCH = "config-revert-seat-mismatch"
CONFIG_REVERT_CODES = (CONFIG_REVERT_SEAT_MISMATCH,)

#: No fixed baseline is registered for this seat. Absence of a baseline is
#: never read as "nothing to check" — it is recorded.
CONFIG_RATCHET_NO_BASELINE = "config-ratchet-no-baseline"
#: No verifier was available (or it raised, or answered unreadably) to re-check
#: the seat's current state against its fixed baseline. No evidence either way.
CONFIG_RATCHET_NO_VERIFIER = "config-ratchet-no-verifier"
#: The suite ran and reported failure comparing CURRENT state to the FIXED
#: baseline — drift an individual per-change gate could not have seen.
CONFIG_RATCHET_FAILED = "config-ratchet-failed"
CONFIG_RATCHET_CODES = (
    CONFIG_RATCHET_NO_BASELINE,
    CONFIG_RATCHET_NO_VERIFIER,
    CONFIG_RATCHET_FAILED,
)

#: Cap on a recorded reason's text — the tier's own value, inherited.
_MAX_REASON_LEN = 500

# ── target maps: which change-unit target each SeatConfig field feeds ───────
#
# Derived from config_change.py's CHANGE_AUTHORITY rather than hand-guessed:
# these are exactly the seven targets that module declares, partitioned by
# seat. Kept as plain dicts (not re-derived from CHANGE_TARGETS at import
# time) so a reader can see the whole mapping in one place.

_WORKER_TARGETS: dict[str, str] = {
    "tools": TARGET_WORKER_TOOLS,
    "prompts": TARGET_WORKER_PROMPTS,
    "knowledge": TARGET_WORKER_KNOWLEDGE,
    "permissions": TARGET_WORKER_PERMISSIONS,
}
_SENSES_TARGETS: dict[str, str] = {
    "prompts": TARGET_SENSES_PROMPTS,
    "permissions": TARGET_SENSES_PERMISSIONS,
    "knowledge": TARGET_SENSES_KNOWLEDGE,
}
_TARGETS_FOR_SEAT: dict[str, dict[str, str]] = {
    SEAT_WORKER: _WORKER_TARGETS,
    SEAT_SENSES: _SENSES_TARGETS,
}


# ── coercion helpers (never raise; own copies — see the module docstring) ───


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


def _authorized(origin: Any, target: str) -> bool:
    """Whether *origin* has authority to write *target*, per ``CHANGE_AUTHORITY``."""
    return target in CHANGE_AUTHORITY.get(_text(origin).strip(), frozenset())


# ── computing the diff: pure, no lifecycle required ──────────────────────────


def _default_id_factory() -> Callable[[str, str], str]:
    """A fresh, call-local id generator. Unique WITHIN one return value only.

    A caller reverting the SAME lifecycle more than once must supply its own
    ``id_factory`` (or use :func:`revert_to_baseline`, which builds one that
    stays unique across repeated calls by anchoring on the lifecycle's own
    growing proposal count). This default exists so
    :func:`compute_revert_changes` is usable standalone without that concern.
    """
    counter = itertools.count()

    def factory(target: str, qualifier: str) -> str:
        n = next(counter)
        suffix = f"-{qualifier}" if qualifier else ""
        return f"revert-{n}-{target}{suffix}"

    return factory


def compute_revert_changes(
    current: Any,
    baseline: Any,
    *,
    origin: str = ORIGIN_HOST,
    reason: str = "revert to baseline",
    id_factory: Optional[Callable[[str, str], str]] = None,
) -> tuple[dict[str, Any], ...]:
    """The ordinary, host-originated change payloads that bring *current*
    toward *baseline*. Pure: no lifecycle, no IO, no store. Never raises.

    Returns raw payload dicts (the same shape
    :func:`~embodiment.config_change.change_from_payload` reads), not typed
    :class:`~embodiment.config_change.ConfigChange` instances — so a caller
    handing these to :meth:`~embodiment.config_lifecycle.ConfigLifecycle.propose`
    gets the FULL admission contract (shape checks, authority checks, and for
    capability-shaped targets, catalog validation and stamping) run for free,
    exactly as any other proposal would.

    Emits at most one payload per target that actually differs — an already
    matching target contributes nothing, so a *current* already equal to
    *baseline* returns an empty tuple. See the module docstring for the one
    honest gap: a knowledge entry introduced after *baseline* cannot be
    expressed as removed by any payload this function can build.
    """
    if not isinstance(current, SeatConfig):
        current = SeatConfig()
    if not isinstance(baseline, SeatConfig):
        baseline = SeatConfig()
    seat = _text(current.seat).strip() or _text(baseline.seat).strip()
    targets = _TARGETS_FOR_SEAT.get(seat)
    if not targets:
        return ()

    factory = id_factory or _default_id_factory()
    reason = reason[:_MAX_REASON_LEN]
    payloads: list[dict[str, Any]] = []

    # ── capabilities: whole-surface assignment, so this is always exact ─────
    for field_name in ("tools", "permissions"):
        target = targets.get(field_name)
        if not target:
            continue
        want = getattr(baseline, field_name)
        have = getattr(current, field_name)
        if want != have:
            payloads.append(
                {
                    "target": target,
                    "change_id": factory(target, ""),
                    "origin": origin,
                    "reason": reason,
                    "capability_ids": list(want),
                }
            )

    # ── prompts: every section named in either config, baseline text wins ──
    prompt_target = targets.get("prompts")
    if prompt_target:
        names = list(
            dict.fromkeys(
                [section.section for section in baseline.prompt]
                + [section.section for section in current.prompt]
            )
        )
        for name in names:
            base_entry = baseline.section(name)
            current_entry = current.section(name)
            want_text = base_entry.text if base_entry is not None else ""
            have_text = current_entry.text if current_entry is not None else None
            if have_text != want_text:
                payloads.append(
                    {
                        "target": prompt_target,
                        "change_id": factory(prompt_target, name),
                        "origin": origin,
                        "reason": reason,
                        "section": name,
                        "text": want_text,
                    }
                )

    # ── knowledge: restore every baseline entry; see the module docstring on
    #    why entries added after baseline cannot be removed this way ────────
    knowledge_target = targets.get("knowledge")
    if knowledge_target:
        for entry in baseline.knowledge:
            current_entry = current.entry(entry.entry_id)
            if (
                current_entry is None
                or current_entry.text != entry.text
                or current_entry.origin != entry.origin
            ):
                entry_origin = (
                    entry.origin if _authorized(entry.origin, knowledge_target) else origin
                )
                payloads.append(
                    {
                        "target": knowledge_target,
                        "change_id": factory(knowledge_target, entry.entry_id),
                        "origin": entry_origin,
                        "reason": reason,
                        "entry_id": entry.entry_id,
                        "text": entry.text,
                    }
                )

    return tuple(payloads)


def _residual_prompt_sections(current: SeatConfig, baseline: SeatConfig) -> tuple[str, ...]:
    baseline_names = {section.section for section in baseline.prompt}
    return tuple(
        section.section for section in current.prompt if section.section not in baseline_names
    )


def _residual_knowledge_entries(current: SeatConfig, baseline: SeatConfig) -> tuple[str, ...]:
    baseline_ids = {entry.entry_id for entry in baseline.knowledge}
    return tuple(
        entry.entry_id for entry in current.knowledge if entry.entry_id not in baseline_ids
    )


# ── driving the diff through the real gate ───────────────────────────────────


@dataclass(frozen=True)
class RevertOutcome:
    """What one :func:`revert_to_baseline` call did.

    ``proposed`` is every change_id this call admitted;
    ``applied``/``deferred``/``rejected``/``refused`` partition it (a change_id
    appears in exactly one, matching the accounting
    :class:`~embodiment.config_lifecycle.AdvanceReport` already holds itself
    to). ``matches_baseline`` is the honest verdict: ``True`` only when the
    seat's effective configuration is now bit-for-bit identical to *baseline*
    — ``False`` whenever a residual remains, never asserted from "everything I
    proposed was applied" alone.
    """

    seat: str = ""
    baseline_sha: str = ""
    proposed: tuple[str, ...] = ()
    applied: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    refused_at_propose: int = 0
    residual_prompt_sections: tuple[str, ...] = ()
    residual_knowledge_entries: tuple[str, ...] = ()
    matches_baseline: bool = False
    degradations: tuple[ConfigDegradation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "baseline_sha": self.baseline_sha,
            "proposed": list(self.proposed),
            "applied": list(self.applied),
            "deferred": list(self.deferred),
            "rejected": list(self.rejected),
            "refused": list(self.refused),
            "refused_at_propose": self.refused_at_propose,
            "residual_prompt_sections": list(self.residual_prompt_sections),
            "residual_knowledge_entries": list(self.residual_knowledge_entries),
            "matches_baseline": self.matches_baseline,
            "degradations": [entry.to_dict() for entry in self.degradations],
        }


def revert_to_baseline(
    lifecycle: ConfigLifecycle,
    seat: Any,
    baseline: SeatConfig,
    *,
    origin: str = ORIGIN_HOST,
    reason: str = "revert to baseline",
    catalog: Optional[CapabilityCatalog] = None,
) -> RevertOutcome:
    """Propose, verify and apply every ordinary change needed to bring *seat*
    back to *baseline*, through ``lifecycle``'s OWN gate. Never raises.

    Nothing here bypasses seat-idle or suite-pass — a revert that lands while
    the seat is busy behaves exactly like any other change would: it stays
    proposed, deferred, and lands once the seat goes idle (a host that wants
    it to land should call :meth:`~embodiment.config_lifecycle.ConfigLifecycle.advance`
    again once it is). This call touches ONLY the proposals it itself makes —
    it never sweeps up a host's other pending, unrelated proposals the way a
    bare ``lifecycle.advance()`` would, so a revert cannot have a surprising
    side effect on work that has nothing to do with it.

    Change ids are generated from ``len(lifecycle.proposals())`` at call time,
    which only ever grows for a given lifecycle — so two separate calls (e.g.
    a first revert that gets deferred, then a retry) never collide on id, and
    a call that finds nothing to revert (the seat already matches *baseline*)
    proposes nothing at all.
    """
    seat_name = _text(seat).strip()
    if not isinstance(baseline, SeatConfig):
        baseline = SeatConfig(seat=seat_name)
    if baseline.seat and seat_name and baseline.seat != seat_name:
        mismatch = ConfigDegradation(
            code=CONFIG_REVERT_SEAT_MISMATCH,
            reason=(
                f"revert_to_baseline was called for seat {seat_name!r} with a baseline "
                f"whose own seat is {baseline.seat!r}; a baseline that disagrees with the "
                "seat being reverted is refused before anything is proposed, rather than "
                "silently applied to the wrong seat"
            )[:_MAX_REASON_LEN],
        )
        return RevertOutcome(
            seat=seat_name, baseline_sha=baseline.config_sha, degradations=(mismatch,)
        )

    current = lifecycle.effective(seat_name)
    epoch = len(lifecycle.proposals())

    def id_factory(target: str, qualifier: str) -> str:
        suffix = f"-{qualifier}" if qualifier else ""
        return f"revert-{epoch}-{target}{suffix}"

    payloads = compute_revert_changes(
        current, baseline, origin=origin, reason=reason, id_factory=id_factory
    )

    proposed: list[str] = []
    for payload in payloads:
        proposal = lifecycle.propose(payload, catalog=catalog)
        if proposal is not None:
            proposed.append(proposal.change_id)
    refused_at_propose = len(payloads) - len(proposed)

    seat_busy_before = not lifecycle.is_idle(seat_name)
    applied: list[str] = []
    deferred: list[str] = []
    rejected: list[str] = []
    refused: list[str] = []
    for change_id in proposed:
        lifecycle.verify(change_id)
        proposal = lifecycle.proposal(change_id)
        state = proposal.state if proposal is not None else ""
        if state == STATE_REJECTED:
            rejected.append(change_id)
            continue
        if state == STATE_VERIFIED:
            outcome = lifecycle.apply(change_id, catalog=catalog)
            if outcome.applied:
                applied.append(change_id)
            elif outcome.deferred:
                deferred.append(change_id)
            else:
                refused.append(change_id)
            continue
        # Stayed PROPOSED: either the seat was busy (verify self-deferred) or
        # no evidence was available (no verifier, or one that raised/answered
        # unreadably). Both are already recorded by config_lifecycle itself;
        # this just files the change_id under the right bucket for the caller.
        if seat_busy_before:
            deferred.append(change_id)
        else:
            refused.append(change_id)

    effective = lifecycle.effective(seat_name)
    residual_prompt = _residual_prompt_sections(effective, baseline)
    residual_knowledge = _residual_knowledge_entries(effective, baseline)
    return RevertOutcome(
        seat=seat_name,
        baseline_sha=baseline.config_sha,
        proposed=tuple(proposed),
        applied=tuple(applied),
        deferred=tuple(deferred),
        rejected=tuple(rejected),
        refused=tuple(refused),
        refused_at_propose=refused_at_propose,
        residual_prompt_sections=residual_prompt,
        residual_knowledge_entries=residual_knowledge,
        matches_baseline=(effective.config_sha == baseline.config_sha),
    )


# ── the shared fixed-baseline registry ────────────────────────────────────────


class ConfigBaseline:
    """One FIXED :class:`~embodiment.config_lifecycle.SeatConfig` per seat.

    The anchor both :func:`revert_to_baseline` and :class:`RatchetGuard`
    compare against. Deliberately dumb: nothing here captures a baseline
    automatically on any lifecycle event, because a baseline that moved itself
    would not be a *fixed* one — the whole point of ``c7``'s ratchet
    requirement is a point that does not drift along with the changes it is
    meant to catch drifting. A host decides when "now" is worth calling
    baseline (often: once, at startup, before any strategist activity) and
    calls :meth:`capture` or :meth:`set` exactly then.
    """

    def __init__(self, seats: Optional[Mapping[str, SeatConfig]] = None) -> None:
        self._seats: dict[str, SeatConfig] = {}
        if isinstance(seats, Mapping):
            for seat, config in seats.items():
                if isinstance(config, SeatConfig):
                    self._seats[_text(seat).strip()] = config

    def set(self, seat: Any, config: SeatConfig) -> None:
        """Fix (or re-fix) the baseline for *seat*. Never raises on junk input."""
        if isinstance(config, SeatConfig):
            self._seats[_text(seat).strip()] = config

    def get(self, seat: Any) -> Optional[SeatConfig]:
        """The fixed baseline for *seat*, or ``None`` when none has been set."""
        return self._seats.get(_text(seat).strip())

    def capture(self, lifecycle: ConfigLifecycle, seat: Any) -> SeatConfig:
        """Convenience: fix *seat*'s CURRENT effective configuration as baseline, now."""
        config = lifecycle.effective(seat)
        self.set(seat, config)
        return config

    def seats(self) -> tuple[str, ...]:
        """Every seat with a fixed baseline, in no particular order."""
        return tuple(self._seats)


# ── the ratchet re-check ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RatchetResult:
    """One re-evaluation of a seat's CURRENT effective config against a FIXED baseline.

    ``drifted`` is a fact about the two digests, independent of whether a
    verifier was even available to grade the drift. ``checked`` distinguishes
    "a suite actually ran and gave an answer" from "no evidence either way" —
    ``passed`` is only ever a real verdict when ``checked`` is ``True``.
    """

    seat: str = ""
    sequence: int = 0
    baseline_sha: str = ""
    candidate_sha: str = ""
    drifted: bool = False
    checked: bool = False
    passed: Optional[bool] = None
    summary: str = ""
    suite: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "sequence": self.sequence,
            "baseline_sha": self.baseline_sha,
            "candidate_sha": self.candidate_sha,
            "drifted": self.drifted,
            "checked": self.checked,
            "passed": self.passed,
            "summary": self.summary,
            "suite": self.suite,
        }


class RatchetGuard:
    """Re-evaluates a seat's cumulative drift against ONE fixed baseline, over time.

    Where :meth:`~embodiment.config_lifecycle.ConfigLifecycle.verify` always
    compares a proposal's candidate against ``self.effective(seat)`` — the
    state right BEFORE that one proposal, i.e. the previous change — this
    class compares the seat's CURRENT cumulative state against a baseline
    fixed once and never moved by a check of its own. That is the whole
    mechanism: three changes that each individually pass an incremental gate
    can still fail the same rule measured end to end, and no per-change gate
    can see that because no per-change gate looks further back than the
    change immediately before it.

    Every check is recorded in :attr:`results`; a failure, a missing
    verifier, or a missing baseline is ALSO recorded in :attr:`degradations`
    (constraint C3) — a ratchet failure is drift no individual gate caught,
    and that is exactly the fact this class exists to make visible rather than
    silently absorb into "well, every change passed at the time."
    """

    def __init__(self, baseline: Optional[ConfigBaseline] = None) -> None:
        self.baseline = baseline if isinstance(baseline, ConfigBaseline) else ConfigBaseline()
        self._results: list[RatchetResult] = []
        self._degradations: list[ConfigDegradation] = []
        self._sequence = 0

    @property
    def results(self) -> tuple[RatchetResult, ...]:
        """Every check this guard has performed, in the order performed."""
        return tuple(self._results)

    @property
    def degradations(self) -> tuple[ConfigDegradation, ...]:
        """Every failure, missing baseline, or missing evidence (constraint C3)."""
        return tuple(self._degradations)

    def _next(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    def _degrade(self, code: str, reason: str) -> ConfigDegradation:
        entry = ConfigDegradation(code=code, reason=reason[:_MAX_REASON_LEN])
        self._degradations.append(entry)
        return entry

    def _record(self, result: RatchetResult) -> RatchetResult:
        self._results.append(result)
        return result

    def check(
        self,
        lifecycle: ConfigLifecycle,
        seat: Any,
        *,
        verifier: Optional[VerifierFn] = None,
    ) -> RatchetResult:
        """Re-run the injected suite comparing *seat*'s CURRENT state to its FIXED
        baseline. Never raises. Uses *verifier* if given, else ``lifecycle.verifier``
        — the same fallback :meth:`~embodiment.config_lifecycle.ConfigLifecycle.apply`
        applies to ``catalog``.
        """
        name = _text(seat).strip()
        anchor = self.baseline.get(name)
        if anchor is None:
            self._degrade(
                CONFIG_RATCHET_NO_BASELINE,
                f"no fixed baseline is registered for seat {name!r}; nothing to "
                "re-evaluate against, so nothing was checked",
            )
            return self._record(RatchetResult(seat=name, sequence=self._next()))

        candidate = lifecycle.effective(name)
        drifted = candidate.config_sha != anchor.config_sha
        suite_fn = verifier if verifier is not None else lifecycle.verifier
        base_kwargs = dict(
            seat=name,
            sequence=self._next(),
            baseline_sha=anchor.config_sha,
            candidate_sha=candidate.config_sha,
            drifted=drifted,
        )

        if suite_fn is None:
            self._degrade(
                CONFIG_RATCHET_NO_VERIFIER,
                f"no verifier is available to re-check seat {name!r} against its "
                "fixed baseline; absence of evidence, not a pass",
            )
            return self._record(RatchetResult(**base_kwargs, checked=False, passed=None))

        request = VerificationRequest(seat=name, change=None, baseline=anchor, candidate=candidate)
        try:
            answer = suite_fn(request)
        except Exception as exc:  # noqa: BLE001  # a raising suite degrades, never crashes here
            self._degrade(
                CONFIG_RATCHET_NO_VERIFIER,
                f"the ratchet re-check suite raised {type(exc).__name__}: {exc} for seat "
                f"{name!r}; that is no evidence either way",
            )
            return self._record(
                RatchetResult(**base_kwargs, checked=False, passed=None, summary=f"{exc!r}")
            )
        if not isinstance(answer, VerificationResult):
            self._degrade(
                CONFIG_RATCHET_NO_VERIFIER,
                f"the ratchet re-check suite answered with {type(answer).__name__}, not a "
                f"VerificationResult, for seat {name!r}; an unreadable verdict is no evidence",
            )
            return self._record(RatchetResult(**base_kwargs, checked=False, passed=None))

        result = self._record(
            RatchetResult(
                **base_kwargs,
                checked=True,
                passed=bool(answer.passed),
                summary=answer.summary,
                suite=answer.suite,
            )
        )
        if not answer.passed:
            self._degrade(
                CONFIG_RATCHET_FAILED,
                f"seat {name!r}'s CURRENT configuration fails the "
                f"{answer.suite or 'verification'} suite when checked against its FIXED "
                f"baseline ({answer.summary or 'no summary given'}); each individual change "
                "may have passed its own incremental gate, but the cumulative drift did not "
                "— this is exactly the compounding the ratchet check exists to catch",
            )
        return result
