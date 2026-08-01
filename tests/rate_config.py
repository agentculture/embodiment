"""Loader for the committed generation-rate measurements every timeout bound derives from.

Task t1 of the ``error-derived-timeouts-bee-hive-architecture`` plan. This is a
**test-side** module on purpose: the q4 decision keeps the timeout bound
per-harness with one shared CI test enforcing it, and explicitly does *not*
move transport policy into ``embodiment/``. Nothing under ``embodiment/``
imports this, and this imports nothing from ``embodiment/``.

Why a file instead of a constant
--------------------------------

Issue [#42](https://github.com/agentculture/embodiment/issues/42) fixed the
rule — ``REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate`` —
and the challenge pass then found the rule's *input* was the weak link:

* ``c39`` — a bare rate literal in test code goes stale silently after a rig
  change. Nothing fails; the bound just quietly stops protecting (or starts
  over-protecting) and no one is told.
* ``c40`` — the rate is condition-dependent. 21.5 tok/s was measured
  single-stream on a server that admits two concurrent sequences; a number with
  no stated concurrency is not a measurement, it is a rumour.

So the rate lives in ``docs/live-test-results/timeout-rate-measurements.json``
with its date, its ``n``, its model id, its endpoint and its concurrency
condition, and this loader is the only way in. **There is no default.** A
missing file raises :class:`RateConfigError` naming the missing measurement and
the procedure that re-derives it — the ``examples/arch_arms.py`` habit, for the
same reason it exists there: a value that appears when the file is gone is a
value nobody measured.

Contract for the bound test (task t2)
-------------------------------------

``load_rate_config()`` returns a fully-validated :class:`RateConfig`. Every
field is parsed eagerly at load, so a malformed or half-updated config fails at
the first call rather than at the assertion that happens to read the missing
key. ``config.rate("cortex").bound_input_tok_s`` is the number a client-timeout
bound divides into; ``config.rate("worker").at_width(8)`` is the number a
fan-out bound at width 8 divides into.

What task ``t2`` added
----------------------

Three fields, each because the bound needed an input the original schema had no
place for. All three are optional, so ``t1``'s two entries load unchanged.

* ``bound_input`` — **which measured figure the bound divides into**, when it
  is not ``slowest_tok_s``. The muse's per-call implied rates are measured over
  31–236-token completions, where the slowest reading is fixed cost wearing a
  rate's units; dividing 16000 by it claims 3092 s for a generation the
  regression puts near 1187 s. Which figure is the honest divisor is a
  *judgement*, so it is stated and cited in the config rather than left
  implicit in whichever number a test happened to reach for.
  :attr:`RateMeasurement.bound_input_tok_s` is the divisor either way.
* ``rate_includes_non_generation`` — whether queue wait and prefill are already
  inside the measured rate. Where they are, adding the allowance below
  double-counts; where they are not, omitting it under-protects.
* ``non_generation_allowance`` (top level) — the measured seconds a request
  spends *not* generating. The rule's right-hand side bounds generation; the
  clock in front of it does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "docs" / "live-test-results"

#: The single committed rate input. Named here once so a test can monkeypatch
#: it to prove absence refuses without going near the real file.
DEFAULT_CONFIG_PATH = RESULTS_DIR / "timeout-rate-measurements.json"

#: The re-derivation procedure, which ships beside the config and is named in
#: the refusal message — an error that says "missing" and stops has told the
#: reader what broke but not what to do.
PROCEDURE_DOC_NAME = "timeout-rate-measurements.md"
PROCEDURE_DOC_PATH = RESULTS_DIR / PROCEDURE_DOC_NAME

#: The schema version this loader understands.
SUPPORTED_VERSION = 1


class RateConfigError(Exception):
    """The rate measurement is missing, unreadable, or incomplete.

    Every path that could otherwise produce a silent default raises this
    instead. It is deliberately one exception type: a caller that wants to
    distinguish "absent" from "malformed" is a caller about to write a
    fallback, and there is no honest fallback for an unmeasured rate.
    """


def _optional_float(value: Any) -> Optional[float]:
    """A rounded citation figure, or ``None`` where no doc publishes one."""
    return None if value is None else float(value)


def _require(payload: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in payload:
        raise RateConfigError(
            f"{where}: no {key!r}. Every generation-rate measurement records its "
            "rate, date, n, model, endpoint and concurrency condition — a field "
            f"that is absent was never measured. See {PROCEDURE_DOC_NAME}."
        )
    return payload[key]


@dataclass(frozen=True)
class WidthRate:
    """One measured concurrency width, and what the rate was at it.

    ``slowest_tok_s`` is the raw per-call floor at this width, retry overhead
    and all; ``slowest_retry_clean_tok_s`` excludes every call whose record
    carries ``retries > 0``. The two differ materially at width 14 and the
    config's own ``caveats`` say why — see the worker entry.
    """

    width: int
    n_calls: int
    slowest_tok_s: float
    mean_tok_s: float
    fastest_tok_s: float
    retry_bearing_calls: int
    slowest_retry_clean_tok_s: float

    @classmethod
    def from_dict(cls, width: int, raw: Any, *, where: str) -> "WidthRate":
        if not isinstance(raw, Mapping):
            raise RateConfigError(f"{where}: a width entry must be a JSON object")
        return cls(
            width=width,
            n_calls=int(_require(raw, "n_calls", where)),
            slowest_tok_s=float(_require(raw, "slowest_tok_s", where)),
            mean_tok_s=float(_require(raw, "mean_tok_s", where)),
            fastest_tok_s=float(_require(raw, "fastest_tok_s", where)),
            retry_bearing_calls=int(_require(raw, "retry_bearing_calls", where)),
            slowest_retry_clean_tok_s=float(_require(raw, "slowest_retry_clean_tok_s", where)),
        )


@dataclass(frozen=True)
class BoundInput:
    """Which measured figure a bound divides into, and why that one.

    Present only where the divisor is *not* ``slowest_tok_s``. ``field_name``
    names the measurement it must equal, and :meth:`from_dict` checks that
    equality — so the divisor cannot drift away from the figure it claims to be
    while still looking cited.
    """

    field_name: str
    tok_s: float
    cited_as: Optional[float]
    why: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any, *, where: str, fields: Mapping[str, float]) -> "BoundInput":
        if not isinstance(raw, Mapping):
            raise RateConfigError(f"{where}: bound_input must be a JSON object")
        field_name = str(_require(raw, "from", where))
        if field_name not in fields:
            known = ", ".join(sorted(fields))
            raise RateConfigError(
                f"{where}.from names {field_name!r}, which is not a measured "
                f"figure on this role. Measured figures are {known}."
            )
        tok_s = float(_require(raw, "tok_s", where))
        if abs(tok_s - fields[field_name]) > 1e-9:
            raise RateConfigError(
                f"{where}.tok_s is {tok_s} but {field_name} is {fields[field_name]}. "
                "The divisor must BE the figure it names, not a copy of it that "
                "has drifted — that is claim c39 inside one file."
            )
        why = tuple(str(line) for line in raw.get("why", ()))
        if not any(line.strip() for line in why):
            raise RateConfigError(
                f"{where}: choosing a divisor other than the slowest measured "
                "rate is a judgement, and a judgement with no stated reason is "
                "a number nobody can audit. Give bound_input a 'why'."
            )
        return cls(
            field_name=field_name,
            tok_s=tok_s,
            cited_as=_optional_float(raw.get("cited_as")),
            why=why,
        )


@dataclass(frozen=True)
class NonGenerationAllowance:
    """Seconds a request spends *not* generating, measured rather than modelled.

    The #42 rule bounds generation. The clock in front of a request also covers
    queue wait and prompt processing, and `corrections.md` §9 found one
    committed call where that gap was larger than the entire slack a passing
    constant had left. This is that gap, as a number a bound can add.
    """

    seconds: float
    cited_as: Optional[float]
    measured_on: str
    measured_role: str
    measured_model: str
    why: tuple[str, ...]
    remeasure_when: tuple[str, ...]
    sources: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any, *, where: str) -> "NonGenerationAllowance":
        if not isinstance(raw, Mapping):
            raise RateConfigError(f"{where}: non_generation_allowance must be a JSON object")
        return cls(
            seconds=float(_require(raw, "seconds", where)),
            cited_as=_optional_float(raw.get("cited_as")),
            measured_on=str(_require(raw, "measured_on", where)),
            measured_role=str(_require(raw, "measured_role", where)),
            measured_model=str(_require(raw, "measured_model", where)),
            why=tuple(str(line) for line in _require(raw, "why", where)),
            remeasure_when=tuple(str(line) for line in raw.get("remeasure_when", ())),
            sources=tuple(str(line) for line in _require(raw, "sources", where)),
        )


@dataclass(frozen=True)
class UnmeasuredRole:
    """A role a constant fronts that nobody has timed.

    Recorded rather than omitted: an omission reads as coverage. Every field is
    required because a half-filled entry is the same silence with more words.
    """

    role: str
    model: str
    fronted_by: tuple[str, ...]
    max_tokens: int
    reached_through: str
    why_unmeasured: tuple[str, ...]
    would_be_produced_by: str

    @classmethod
    def from_dict(cls, role: str, raw: Any, *, where: str) -> "UnmeasuredRole":
        if not isinstance(raw, Mapping):
            raise RateConfigError(f"{where}: the {role!r} entry must be a JSON object")
        return cls(
            role=role,
            model=str(_require(raw, "model", where)),
            fronted_by=tuple(str(x) for x in _require(raw, "fronted_by", where)),
            max_tokens=int(_require(raw, "max_tokens", where)),
            reached_through=str(_require(raw, "reached_through", where)),
            why_unmeasured=tuple(str(x) for x in _require(raw, "why_unmeasured", where)),
            would_be_produced_by=str(_require(raw, "would_be_produced_by", where)),
        )


@dataclass(frozen=True)
class RateMeasurement:
    """One role's measured generation rate, with everything that qualifies it."""

    role: str
    slowest_tok_s: float
    mean_tok_s: float
    fastest_tok_s: float
    #: The rounded figures the prose docs publish. Kept beside the precise ones
    #: so a test can check the config and its own citation still agree — a rate
    #: that has drifted away from the doc that publishes it is c39 mid-flight.
    #: ``None`` where no doc quotes a rounded figure.
    cited_as: Optional[float]
    cited_fastest_as: Optional[float]
    #: Total tokens over total generation seconds. Reported because it is the
    #: honest "how fast is this thing overall" number; never the bound's input,
    #: which is always the slowest single call.
    aggregate_tok_s: Optional[float]
    n_calls: int
    n_rate_bearing: int
    measured_on: str
    model: str
    base_url: str
    max_tokens: int
    concurrency: int
    condition: str
    remeasure_when: tuple[str, ...]
    caveats: tuple[str, ...]
    sources: tuple[str, ...]
    pending_sources: tuple[str, ...]
    by_width: Mapping[int, WidthRate]
    #: Which measured figure this role's bound divides into, when it is not
    #: ``slowest_tok_s``. See :class:`BoundInput`.
    bound_input: Optional[BoundInput]
    #: Whether queue wait and prefill are already inside the measured rate.
    #: Absent means ``False`` — the safe reading, since it adds the allowance.
    rate_includes_non_generation: bool
    rate_includes_non_generation_why: tuple[str, ...]

    @property
    def bound_input_tok_s(self) -> float:
        """The divisor. ``slowest_tok_s`` unless the config names another figure."""
        return self.slowest_tok_s if self.bound_input is None else self.bound_input.tok_s

    @property
    def bound_input_field(self) -> str:
        """Which measured figure :attr:`bound_input_tok_s` is."""
        return "slowest_tok_s" if self.bound_input is None else self.bound_input.field_name

    @property
    def retry_clean_floor_tok_s(self) -> Optional[float]:
        """The slowest call at any width that never retried, or ``None``.

        The figure that makes ``rate_includes_non_generation`` checkable rather
        than merely asserted: a rate claiming to contain queue and prefill has
        to be slower than the clean floor by at least the allowance, at the
        budget in question. Only roles measured per width carry one.
        """
        if not self.by_width:
            return None
        return min(entry.slowest_retry_clean_tok_s for entry in self.by_width.values())

    def at_width(self, width: int) -> WidthRate:
        """The measurement at exactly this concurrency width, or a refusal.

        Deliberately no interpolation and no nearest-neighbour. The rate falls
        with width and the shape of that fall is not a straight line, so a
        made-up value at an unmeasured width is the c39 defect wearing a
        function call. Measure the width, or dial a width that was measured.
        """
        if width not in self.by_width:
            known = ", ".join(str(w) for w in sorted(self.by_width))
            raise RateConfigError(
                f"{self.role}: no rate measured at concurrency width {width}. "
                f"Measured widths are {known}. Rates are not interpolated — "
                f"re-measure at this width per {PROCEDURE_DOC_NAME}, or derive "
                "the bound from a width that was measured."
            )
        return self.by_width[width]

    @classmethod
    def from_dict(cls, role: str, raw: Any, *, where: str) -> "RateMeasurement":
        if not isinstance(raw, Mapping):
            raise RateConfigError(f"{where}: the {role!r} entry must be a JSON object")

        condition = _require(raw, "condition", where)
        if not isinstance(condition, Mapping):
            raise RateConfigError(f"{where}.condition: must be a JSON object")

        widths_raw = raw.get("by_width") or {}
        if not isinstance(widths_raw, Mapping):
            raise RateConfigError(f"{where}.by_width: must be a JSON object keyed by width")
        by_width = {
            int(width): WidthRate.from_dict(int(width), entry, where=f"{where}.by_width.{width}")
            for width, entry in widths_raw.items()
        }

        measured_figures = {
            "slowest_tok_s": float(_require(raw, "slowest_tok_s", where)),
            "mean_tok_s": float(_require(raw, "mean_tok_s", where)),
            "fastest_tok_s": float(_require(raw, "fastest_tok_s", where)),
        }
        bound_input_raw = raw.get("bound_input")
        bound_input = (
            None
            if bound_input_raw is None
            else BoundInput.from_dict(
                bound_input_raw, where=f"{where}.bound_input", fields=measured_figures
            )
        )
        includes = bool(raw.get("rate_includes_non_generation", False))
        includes_why = tuple(str(x) for x in raw.get("rate_includes_non_generation_why", ()))
        if includes and not any(line.strip() for line in includes_why):
            raise RateConfigError(
                f"{where}: rate_includes_non_generation is true with no stated "
                "reason. That flag suppresses the queue allowance on every bound "
                "this role fronts; it is a claim about how the rate was measured "
                "and it has to say which measurement supports it."
            )

        return cls(
            role=role,
            slowest_tok_s=float(_require(raw, "slowest_tok_s", where)),
            mean_tok_s=float(_require(raw, "mean_tok_s", where)),
            fastest_tok_s=float(_require(raw, "fastest_tok_s", where)),
            cited_as=_optional_float(raw.get("cited_as")),
            cited_fastest_as=_optional_float(raw.get("cited_fastest_as")),
            aggregate_tok_s=_optional_float(raw.get("aggregate_tok_s")),
            n_calls=int(_require(raw, "n_calls", where)),
            n_rate_bearing=int(_require(raw, "n_rate_bearing", where)),
            measured_on=str(_require(raw, "measured_on", where)),
            model=str(_require(raw, "model", where)),
            base_url=str(_require(raw, "base_url", where)),
            max_tokens=int(_require(raw, "max_tokens", where)),
            concurrency=int(_require(condition, "concurrency", f"{where}.condition")),
            condition=str(_require(condition, "description", f"{where}.condition")),
            remeasure_when=tuple(str(x) for x in raw.get("remeasure_when", ())),
            caveats=tuple(str(x) for x in raw.get("caveats", ())),
            sources=tuple(str(x) for x in _require(raw, "sources", where)),
            pending_sources=tuple(str(x) for x in raw.get("pending_sources", ())),
            by_width=by_width,
            bound_input=bound_input,
            rate_includes_non_generation=includes,
            rate_includes_non_generation_why=includes_why,
        )


@dataclass(frozen=True)
class RateConfig:
    """The whole committed rate input, parsed and validated once."""

    path: Path
    version: int
    rule_formula: str
    rule_source: str
    roles: Mapping[str, RateMeasurement]
    non_generation_allowance: NonGenerationAllowance
    #: Roles a constant fronts that nobody has timed. Never empty by accident:
    #: an absent entry means the gap is unrecorded, not that it does not exist.
    unmeasured_roles: Mapping[str, UnmeasuredRole]

    def rate(self, role: str) -> RateMeasurement:
        if role not in self.roles:
            known = ", ".join(sorted(self.roles))
            declared = self.unmeasured_roles.get(role)
            gap = (
                ""
                if declared is None
                else (
                    f" It is declared unmeasured: {declared.model} is fronted by "
                    f"{', '.join(declared.fronted_by)} and a rate would come from "
                    f"{declared.would_be_produced_by}."
                )
            )
            raise RateConfigError(
                f"no generation-rate measurement for role {role!r}; measured roles "
                f"are {known}. A role with no committed measurement has no derivable "
                f"timeout bound — measure it per {PROCEDURE_DOC_NAME} first.{gap}"
            )
        return self.roles[role]

    def every_measured_rate(self) -> tuple[float, ...]:
        """Every rate value in the file, for the no-literal guard to compare against.

        Rounded citation figures are included deliberately: pasting ``21.5``
        into a bound test is the same staleness defect as pasting ``21.452``,
        and it is the likelier of the two because it is the figure the prose
        publishes.
        """
        values: list[float] = []
        for measurement in self.roles.values():
            values.extend(
                (
                    measurement.slowest_tok_s,
                    measurement.mean_tok_s,
                    measurement.fastest_tok_s,
                )
            )
            values.extend(
                value
                for value in (
                    measurement.cited_as,
                    measurement.cited_fastest_as,
                    measurement.aggregate_tok_s,
                    None if measurement.bound_input is None else measurement.bound_input.tok_s,
                    None if measurement.bound_input is None else measurement.bound_input.cited_as,
                )
                if value is not None
            )
            for at_width in measurement.by_width.values():
                values.extend(
                    (
                        at_width.slowest_tok_s,
                        at_width.mean_tok_s,
                        at_width.fastest_tok_s,
                        at_width.slowest_retry_clean_tok_s,
                    )
                )
        return tuple(values)


#: The roles a complete config must carry. Absence of one is a refusal, not an
#: empty mapping: a bound test that silently found no cortex rate would pass.
#:
#: ``muse`` joined in ``t2``: four of the seven audited constants front Gemma
#: 4 31B, and #42 derived every one of their bounds at the cortex rate. A
#: config that could load without it would let that mistake recur silently.
REQUIRED_ROLES = ("cortex", "worker", "muse")


def load_rate_config(path: Optional[Path] = None) -> RateConfig:
    """Read and validate the committed rate measurements. **Never defaults.**

    :param path: an explicit config file; the committed default when omitted.
    :raises RateConfigError: the file is absent, unreadable, not a JSON object,
        carries an unsupported schema version, is missing a required role, or a
        role is missing any field that qualifies its number.
    """
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise RateConfigError(
            f"no generation-rate measurement at {resolved}. Every timeout bound in "
            "this repo is max_tokens / slowest measured tok/s (issue #42), and "
            "there is no default rate to fall back to: a bound derived from an "
            "invented rate protects nothing and says nothing. Re-measure and "
            f"commit the result per {PROCEDURE_DOC_NAME}, which sits beside it."
        ) from missing
    except ValueError as broken:
        raise RateConfigError(f"{resolved} is not readable JSON: {broken}") from broken

    if not isinstance(raw, Mapping):
        raise RateConfigError(f"{resolved}: the rate measurement must be a JSON object")

    where = str(resolved)
    version = int(_require(raw, "version", where))
    if version != SUPPORTED_VERSION:
        raise RateConfigError(
            f"{resolved}: schema version {version} but this loader understands "
            f"{SUPPORTED_VERSION}. Update tests/rate_config.py deliberately; do not "
            "read an unknown schema as if it were the known one."
        )

    rule = _require(raw, "rule", where)
    if not isinstance(rule, Mapping):
        raise RateConfigError(f"{where}.rule: must be a JSON object")

    roles_raw = _require(raw, "roles", where)
    if not isinstance(roles_raw, Mapping):
        raise RateConfigError(f"{where}.roles: must be a JSON object keyed by role")

    roles = {
        name: RateMeasurement.from_dict(name, entry, where=f"{where}.roles.{name}")
        for name, entry in roles_raw.items()
    }
    for name in REQUIRED_ROLES:
        if name not in roles:
            raise RateConfigError(
                f"{resolved}: no measurement for required role {name!r}. Every one of "
                f"{', '.join(repr(r) for r in REQUIRED_ROLES)} is dialled by "
                "harnesses in examples/, so every one needs a committed rate."
            )

    allowance = NonGenerationAllowance.from_dict(
        _require(raw, "non_generation_allowance", where),
        where=f"{where}.non_generation_allowance",
    )

    unmeasured_raw = raw.get("unmeasured_roles") or {}
    if not isinstance(unmeasured_raw, Mapping):
        raise RateConfigError(f"{where}.unmeasured_roles: must be a JSON object keyed by role")
    unmeasured = {
        name: UnmeasuredRole.from_dict(name, entry, where=f"{where}.unmeasured_roles.{name}")
        for name, entry in unmeasured_raw.items()
        if name != "why"
    }
    overlap = sorted(set(unmeasured) & set(roles))
    if overlap:
        raise RateConfigError(
            f"{resolved}: {', '.join(overlap)} appear(s) both as a measured role and "
            "as an unmeasured one. A role that has been measured is no longer a gap — "
            "delete the unmeasured_roles entry and put the pairs it names under the "
            "bound test's measured walk."
        )

    return RateConfig(
        path=resolved,
        version=version,
        rule_formula=str(_require(rule, "formula", f"{where}.rule")),
        rule_source=str(_require(rule, "source", f"{where}.rule")),
        roles=roles,
        non_generation_allowance=allowance,
        unmeasured_roles=unmeasured,
    )
