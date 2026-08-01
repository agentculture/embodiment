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
key. ``config.rate("cortex").slowest_tok_s`` is the number a client-timeout
bound divides into; ``config.rate("worker").at_width(8)`` is the number a
fan-out bound at width 8 divides into.
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
        )


@dataclass(frozen=True)
class RateConfig:
    """The whole committed rate input, parsed and validated once."""

    path: Path
    version: int
    rule_formula: str
    rule_source: str
    roles: Mapping[str, RateMeasurement]

    def rate(self, role: str) -> RateMeasurement:
        if role not in self.roles:
            known = ", ".join(sorted(self.roles))
            raise RateConfigError(
                f"no generation-rate measurement for role {role!r}; measured roles "
                f"are {known}. A role with no committed measurement has no derivable "
                f"timeout bound — measure it per {PROCEDURE_DOC_NAME} first."
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
REQUIRED_ROLES = ("cortex", "worker")


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
                f"{resolved}: no measurement for required role {name!r}. Both "
                f"{' and '.join(repr(r) for r in REQUIRED_ROLES)} are dialled by "
                "harnesses in examples/, so both need a committed rate."
            )

    return RateConfig(
        path=resolved,
        version=version,
        rule_formula=str(_require(rule, "formula", f"{where}.rule")),
        rule_source=str(_require(rule, "source", f"{where}.rule")),
        roles=roles,
    )
