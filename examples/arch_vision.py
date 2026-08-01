#!/usr/bin/env python3
"""arch_vision — the perception-routing screen: three ways an image reaches a mind.

Plan task **t8** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`), **rescoped
mid-flight by deviation `d1`** — read the deviation, not the plan's original
wording, as the design.

What changed, and why it matters
--------------------------------
The task was originally a *vision rung*: an asymmetry in which the worker could
see a map and the deciding mind could not. That premise is dead. The rig's
cortex was upgraded mid-build and its vision was **measured**
(`docs/live-test-results/cortex-vision-probe.md`), so the asymmetry evaporated
and what is left is a genuinely different question — a **perception-routing**
factor, orthogonal to the architecture factor:

=============  ==============================================================
route          what the deciding mind is handed
=============  ==============================================================
``native``     the fog-scoped map, straight to it as an image content part
``described``  the senses role describes that image; the mind reads only text
``both``       the description **and** the native image
=============  ==============================================================

A two-stage screen, and a crossing refused by rule
--------------------------------------------------
Claim ``c41``: **stage 1** runs the three routes on the **flat arm alone** —
does the senses role still earn its place on visual input once the deciding
mind can see? **Stage 2** carries *only the winning route* into the four
architectures. A full 4x3 crossing is refused **by rule, not by budget**: 12
cells at achievable n cannot resolve an interaction, and that is precisely how
two prior experiments in this repo died.

That rule is worth nothing if the two-stage shape can quietly become a crossing
plus a favourite, so the post-hoc pick is made **unexpressible** rather than
discouraged:

* :func:`run_stage2` **has no route parameter and no selection parameter.** It
  takes the stage-1 artifact and re-derives the route itself. There is no
  argument to pass, no flag on the CLI, and ``tests/test_arch_vision.py``
  proves it over this file's own AST — including a vacuity guard that plants a
  ``route`` parameter and checks the guard fails.
* :func:`select_route` **refuses stage-2 records outright**
  (:class:`PostHocError`), so stage-2 data cannot be fed back into the choice.
* It also refuses an **incomplete or underpowered** screen
  (:class:`IncompleteScreen`), so a screen cannot be stopped early at a
  convenient moment.
* Every stage-2 record carries a **seal** binding the route to a digest of the
  stage-1 evidence that produced it. :func:`verify_stage2` re-derives the
  selection from the committed screen and refuses the pair if either side was
  edited afterwards — including the case where the *route* still matches and
  only the evidence moved.

The routes live in committed config, and the config is checked
--------------------------------------------------------------
``docs/live-test-results/arch-arms-sampling.json`` gains an additive
``perception_routes`` block stating, in a closed vocabulary, **exactly what
each mind receives** on each route. "Stated in config" would be a weak claim if
the statement were only read back, so the test suite compares the declaration
against the payload :func:`build_delivery` actually produces, and this module
defines no fallback for any of it — the same discipline t5 applies to the
sampling table.

Fog scoping binds every route; information matching does not
-------------------------------------------------------------
Honesty condition ``h31``. The information-matching rule (``c22``/``h16``)
exists so an image cell cannot smuggle extra state past its text twin. Routing
arms *deliberately* vary information content — a description is lossy
compression of the image by design — so matching would forbid the very contrast
this rung measures. The exemption is therefore declared **per rung**, it
relaxes matching **only**, and :func:`load_perception_config` refuses a config
that tries to widen it to visibility.

The fog rule (``h15``) keeps binding every route unchanged: **no route may show
a mind a cell its visibility excludes.** :func:`scan_delivery` runs over the
bytes and the strings that actually reach a mind, using **t9's own** fixture
(``examples/map_render.fog_leak_fixture``) rather than a second copy, with two
vacuity controls in the suite — a fogless image that must leak, and a lying
description that must be caught.

Capability facts come from the probes, never from the advert
-------------------------------------------------------------
Claim ``c43``. The gateway advert lists the cortex's responsibilities with no
image understanding, while the measurement says it reads image parts
accurately enough to count regions and index into them. A consumer obeying this
repo's own *resolve by name, never parse model names* rule would conclude the
deciding mind is blind and **silently drop the native route**. So
:data:`CAPABILITY_FACTS` cites committed measured probes, records the
disagreement rather than absorbing it, and no code path here reads the advert.

No live dial happens here
-------------------------
The whole screen is exercisable hermetically with scripted minds. The live lane
is gated on ``EMBODIMENT_LIVE_RIG`` (t5's :func:`arch_arms.require_live_rig`)
and refuses even with the gate open: task t12 runs the series. No model id
appears in this file — every identity comes from the sampling table.

Usage::

    uv run python examples/arch_vision.py plan
    uv run python examples/arch_vision.py problems --markdown
    uv run python examples/arch_vision.py stage1 --raw-dir raw/ --out s1.jsonl
    uv run python examples/arch_vision.py select --stage1 s1.jsonl
    uv run python examples/arch_vision.py stage2 --stage1 s1.jsonl --raw-dir raw/ \\
        --out s2.jsonl
    uv run python examples/arch_vision.py verify --stage1 s1.jsonl --stage2 s2.jsonl
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import ToolOutcome, UnknownToolError  # noqa: E402
from embodiment.context import is_media_rejection  # noqa: E402
from embodiment.media import build_part, flatten_parts, validate_attachment  # noqa: E402
from examples import arch_arms as aa  # noqa: E402
from examples import map_render  # noqa: E402

__all__ = [
    "ADVERT_SOURCE",
    "BASIS_CORRECTNESS",
    "BASIS_COST",
    "BASIS_PRECEDENCE",
    "CAPABILITY_FACTS",
    "CONFIG_KEY",
    "CONFIG_PATH",
    "CONTENTS",
    "CONTENT_DESCRIBE_INSTRUCTION",
    "CONTENT_MAP_IMAGE",
    "CONTENT_QUESTION",
    "CONTENT_SENSES_DESCRIPTION",
    "CapabilityFact",
    "DEGRADED_MEDIA_REJECTED",
    "DEGRADED_SENSES_ABSENT",
    "DEGRADED_SENSES_EMPTY",
    "DESCRIPTION_HEADER",
    "DESCRIPTION_UNAVAILABLE",
    "Delivery",
    "FOG_SCAN_APPLIES_TO",
    "FogLeak",
    "HIDDEN",
    "IncompleteScreen",
    "MODALITY_IMAGE",
    "MODALITY_VIDEO",
    "PROBLEMS",
    "PerceptionConfig",
    "PostHocError",
    "QUESTIONS",
    "Question",
    "RESULT_KIND",
    "REPO_ROOT",
    "ROUTE_BOTH",
    "ROUTE_DESCRIBED",
    "ROUTE_NATIVE",
    "ROUTE_ORDER",
    "RULE_FOG_SCOPING",
    "RULE_INFORMATION_MATCHING",
    "RUNG",
    "RUNG_BRIEFING",
    "RUNG_ID",
    "RUNG_SNAPSHOT",
    "RUNG_SNAPSHOT_HASH",
    "RouteEvidence",
    "RouteSelection",
    "RouteSpec",
    "RoutedSeam",
    "RoutedSeams",
    "SEAL_SCHEMA",
    "SELECTION_RULE_KEY",
    "SENSES_INSTRUCTION",
    "SENSES_SYSTEM",
    "STAGE_CARRY",
    "STAGE_SCREEN",
    "SelectionRule",
    "VERDICT_CORRECT",
    "VERDICT_NO_ANSWER",
    "VERDICT_WRONG",
    "VisionBench",
    "VisionStimulus",
    "analyse_stage2",
    "assert_fog_scoped",
    "assert_rung_stimulus",
    "build_delivery",
    "build_parser",
    "build_stimulus",
    "capability",
    "describe_with_senses",
    "gather_evidence",
    "load_perception_config",
    "main",
    "native_route_supported",
    "problems_markdown",
    "read_records",
    "run_stage1",
    "run_stage2",
    "scan_delivery",
    "select_route",
    "senses_payload",
    "verify_stage2",
]

REPO_ROOT = aa.REPO_ROOT

#: The same committed table t5 reads. This module reads an ADDITIVE block of it
#: (:data:`CONFIG_KEY`) and shares t5's no-fallback discipline: a missing cell
#: raises rather than quietly becoming a default.
CONFIG_PATH = aa.DEFAULT_CONFIG_PATH
CONFIG_KEY = "perception_routes"
SELECTION_RULE_KEY = "selection_rule"

#: The rung id. One decision point, four questions about one fog-scoped map.
RUNG_ID = "V1"

#: The two stages. A record's stage is what makes the screen unfakeable: the
#: selection function refuses to look at anything from the carry stage.
STAGE_SCREEN = 1
STAGE_CARRY = 2

RESULT_KIND = "perception-screen"


class PostHocError(ValueError):
    """A route was chosen — or could have been — after the data that judges it.

    Raised when stage-2 records reach the selection, when a sealed selection
    does not match the evidence it claims, or when a committed stage-2 artifact
    disagrees with the screen that supposedly produced it.
    """


class IncompleteScreen(ValueError):
    """The screen cannot be graded: a route is missing, or n is below the floor."""


class FogLeak(RuntimeError):
    """A route's payload would show a mind a cell its visibility excludes (h15).

    Never softened into a degradation. Degrading is right when a *capability*
    is missing and the run can honestly continue with less; this is the
    opposite — the run would continue with MORE than the mind is entitled to,
    and every number after it would look ordinary.
    """


# ── the three routes ─────────────────────────────────────────────────────────

ROUTE_NATIVE = "native"
ROUTE_DESCRIBED = "described"
ROUTE_BOTH = "both"

#: Presentation order, and the order the screen runs them in. The *contents* of
#: each route live in the committed config, never here.
ROUTE_ORDER: tuple[str, ...] = (ROUTE_NATIVE, ROUTE_DESCRIBED, ROUTE_BOTH)

#: h15 binds every one of them, with no exemption. Named as data so a test can
#: assert the fog scan's coverage rather than trusting a sentence.
FOG_SCAN_APPLIES_TO: tuple[str, ...] = ROUTE_ORDER

#: The closed vocabulary the config declares content in. A route may only claim
#: to hand a mind one of these, and :meth:`Delivery.contents_for` derives the
#: same tokens from the payload that was actually built — so the declaration is
#: checkable rather than decorative.
CONTENT_QUESTION = "question"
CONTENT_MAP_IMAGE = "map-image"
CONTENT_SENSES_DESCRIPTION = "senses-description"
CONTENT_DESCRIBE_INSTRUCTION = "describe-instruction"
CONTENTS: tuple[str, ...] = (
    CONTENT_QUESTION,
    CONTENT_MAP_IMAGE,
    CONTENT_SENSES_DESCRIPTION,
    CONTENT_DESCRIBE_INSTRUCTION,
)

#: The two rules the exemption is written in terms of. ``c40``/``h31``: the
#: first may be relaxed for this rung, the second may never be.
RULE_INFORMATION_MATCHING = "information-matching"
RULE_FOG_SCOPING = "fog-scoping"

#: How a selection was reached. Only :data:`BASIS_CORRECTNESS` is a separation;
#: the other two are tie-breaks and must never be quoted as a route winning.
BASIS_CORRECTNESS = "correctness"
BASIS_COST = "total-tokens"
BASIS_PRECEDENCE = "precedence"

SEAL_SCHEMA = "embodiment.arch_vision.selection/1"


# ── capability facts: the committed probes, never the advert ─────────────────

MODALITY_IMAGE = "image"
MODALITY_VIDEO = "video"

#: Named so a reader knows exactly which source is being refused, and so the
#: refusal is checkable. This module builds no request to it.
ADVERT_SOURCE = "/capabilities"


@dataclass(frozen=True)
class CapabilityFact:
    """One measured fact about what a role can perceive, and where it was measured.

    ``evidence`` is a literal fragment of the committed probe. The test suite
    reads the cited file and fails if the fragment is no longer there, so a
    citation cannot rot into a claim about a document that has moved on.
    """

    role: str
    modality: str
    measured: bool
    advert_declares: bool
    source: str
    evidence: str
    note: str

    @property
    def disagrees(self) -> bool:
        """The advert and the measurement do not agree — recorded, not absorbed."""
        return self.measured is not self.advert_declares

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "modality": self.modality,
            "measured": self.measured,
            "advert_declares": self.advert_declares,
            "disagrees": self.disagrees,
            "source": self.source,
            "evidence": self.evidence,
            "note": self.note,
        }


_CORTEX_VISION_PROBE = "docs/live-test-results/cortex-vision-probe.md"
_VIDEO_PROBE = "docs/live-test-results/video-perception-probe.md"

CAPABILITY_FACTS: dict[tuple[str, str], CapabilityFact] = {
    (aa.ROLE_CORTEX, MODALITY_IMAGE): CapabilityFact(
        role=aa.ROLE_CORTEX,
        modality=MODALITY_IMAGE,
        measured=True,
        advert_declares=False,
        source=_CORTEX_VISION_PROBE,
        evidence="4, purple",
        note=(
            "The whole native route rests on this. A four-band image with an "
            "unguessable palette, asked for a count AND a positional lookup, after "
            "a first probe was thrown out for asking a question a blind model "
            "would have guessed. The advert declares no image understanding for "
            "this role; the measurement is what this rung reads."
        ),
    ),
    (aa.ROLE_WORKER, MODALITY_IMAGE): CapabilityFact(
        role=aa.ROLE_WORKER,
        modality=MODALITY_IMAGE,
        measured=True,
        advert_declares=True,
        source=_VIDEO_PROBE,
        evidence="ONLY ONE FRAME",
        note=(
            "Measured in passing: handed an animated image the worker read exactly "
            "one frame and said so through the probe's escape hatch, which "
            "establishes that it takes image parts at all. The advert agrees here, "
            "so this fact is recorded for completeness rather than to overrule it."
        ),
    ),
    (aa.ROLE_CORTEX, MODALITY_VIDEO): CapabilityFact(
        role=aa.ROLE_CORTEX,
        modality=MODALITY_VIDEO,
        measured=True,
        advert_declares=False,
        source=_VIDEO_PROBE,
        evidence="left-to-right",
        note=(
            "Recorded, and deliberately NOT used: motion is out of scope for this "
            "rung (t17 owns it). It is here because the advert was wrong about it "
            "too, which is the pattern c43 is about rather than a single slip."
        ),
    ),
}


def capability(role: str, modality: str) -> CapabilityFact:
    """The measured fact for one role and modality. Absence raises rather than guessing."""
    key = (role, modality)
    if key not in CAPABILITY_FACTS:
        raise aa.ConfigError(
            f"no committed measurement for {role!r}/{modality!r}; this rung reads "
            f"probes, and {ADVERT_SOURCE} is not a substitute for one"
        )
    return CAPABILITY_FACTS[key]


def native_route_supported() -> bool:
    """Is the native route dialable? Answered from the probe, not from the advert."""
    return capability(aa.ROLE_CORTEX, MODALITY_IMAGE).measured


# ── the committed configuration ──────────────────────────────────────────────


def _required(raw: Any, key: str, where: str) -> Any:
    """Read a required key. There is no default — that is the whole point."""
    if not isinstance(raw, Mapping) or key not in raw:
        raise aa.ConfigError(
            f"{where}: missing required key {key!r}. The committed "
            f"{CONFIG_KEY} block is the only source for it; this harness carries "
            "no default to fall back on."
        )
    return raw[key]


@dataclass(frozen=True)
class RouteSpec:
    """One route, exactly as the committed config declares it."""

    id: str
    native_image: bool
    senses_description: bool
    cortex_receives: tuple[str, ...]
    senses_receives: tuple[str, ...]
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "native_image": self.native_image,
            "senses_description": self.senses_description,
            "cortex_receives": list(self.cortex_receives),
            "senses_receives": list(self.senses_receives),
            "why": self.why,
        }


@dataclass(frozen=True)
class SelectionRule:
    """The pre-registered rule stage 1 is graded by, tie-breaks included."""

    id: str
    primary: str
    tie_breakers: tuple[str, ...]
    precedence: tuple[str, ...]
    min_attempts_per_route: int
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "primary": self.primary,
            "tie_breakers": list(self.tie_breakers),
            "precedence": list(self.precedence),
            "min_attempts_per_route": self.min_attempts_per_route,
            "why": self.why,
        }


@dataclass(frozen=True)
class PerceptionConfig:
    """The whole ``perception_routes`` block, parsed and validated once."""

    path: Optional[Path]
    rung: str
    order: tuple[str, ...]
    routes: Mapping[str, RouteSpec]
    rule: SelectionRule
    exemption: Mapping[str, Any]
    capability_source: Mapping[str, Any]
    stage1_arms: tuple[str, ...]
    stage2_arms: tuple[str, ...]

    def spec(self, route: str) -> RouteSpec:
        if route not in self.routes:
            raise aa.ConfigError(
                f"{CONFIG_KEY}.routes: no entry for route {route!r}; "
                f"declared routes are {sorted(self.routes)}"
            )
        return self.routes[route]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "rung": self.rung,
            "order": list(self.order),
            "routes": {name: spec.to_dict() for name, spec in self.routes.items()},
            "rule": self.rule.to_dict(),
            "exemption": dict(self.exemption),
            "capability_source": dict(self.capability_source),
            "stage1_arms": list(self.stage1_arms),
            "stage2_arms": list(self.stage2_arms),
        }


def _flatten_why(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return " ".join(str(line) for line in value)
    return str(value)


def _route_spec(route: str, raw: Any) -> RouteSpec:
    where = f"{CONFIG_KEY}.routes.{route}"
    declared: list[str] = []
    for role_key in ("cortex_receives", "senses_receives"):
        entries = _required(raw, role_key, where)
        if not isinstance(entries, Sequence) or isinstance(entries, str):
            raise aa.ConfigError(f"{where}.{role_key} must be a list of content tokens")
        declared.extend(str(entry) for entry in entries)
    unknown = sorted({token for token in declared if token not in CONTENTS})
    if unknown:
        raise aa.ConfigError(
            f"{where}: unknown content token(s) {unknown}; the vocabulary is {list(CONTENTS)}"
        )
    return RouteSpec(
        id=route,
        native_image=bool(_required(raw, "native_image", where)),
        senses_description=bool(_required(raw, "senses_description", where)),
        cortex_receives=tuple(str(entry) for entry in _required(raw, "cortex_receives", where)),
        senses_receives=tuple(str(entry) for entry in _required(raw, "senses_receives", where)),
        why=_flatten_why(_required(raw, "why", where)),
    )


def _validate_exemption(raw: Any, rung: str) -> Mapping[str, Any]:
    """The exemption relaxes matching only. Widening it to visibility is refused.

    This is ``h31`` expressed as a load rule rather than a sentence: a config
    that listed the fog rule as relaxed would silently turn off the one
    invariant the routing rung is not allowed to trade away, and the failure
    would look exactly like a passing run.
    """
    where = f"{CONFIG_KEY}.information_matching_exemption"
    exemption = dict(_required(raw, "information_matching_exemption", CONFIG_KEY))
    applies = str(_required(exemption, "applies_to_rung", where))
    if applies != rung:
        raise aa.ConfigError(f"{where}.applies_to_rung is {applies!r}, not this rung {rung!r}")
    relaxes = [str(entry) for entry in _required(exemption, "relaxes", where)]
    if relaxes != [RULE_INFORMATION_MATCHING]:
        raise aa.ConfigError(
            f"{where}.relaxes is {relaxes}; the exemption relaxes exactly "
            f"[{RULE_INFORMATION_MATCHING!r}] and nothing else — {RULE_FOG_SCOPING} is "
            "not an information rule and can never be relaxed by it"
        )
    still = [str(entry) for entry in _required(exemption, "still_binds", where)]
    if RULE_FOG_SCOPING not in still:
        raise aa.ConfigError(
            f"{where}.still_binds must name {RULE_FOG_SCOPING!r}: no route may show a "
            "mind a cell its visibility excludes, exemption or not"
        )
    exemption["why"] = _flatten_why(_required(exemption, "why", where))
    exemption["relaxes"] = relaxes
    exemption["still_binds"] = still
    return exemption


def load_perception_config(path: Optional[Path] = None) -> PerceptionConfig:
    """Read and validate the committed routing block. Eager and total.

    Every route's declared contents, the selection rule and the exemption are
    parsed at load, so a malformed block fails before the first dial rather
    than three hours into a series.
    """
    resolved = Path(path) if path is not None else CONFIG_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise aa.ConfigError(f"no sampling table at {resolved}") from missing
    except ValueError as broken:
        raise aa.ConfigError(f"{resolved} is not readable JSON: {broken}") from broken

    block = _required(raw, CONFIG_KEY, str(resolved))
    rung = str(_required(block, "rung", CONFIG_KEY))
    declared = _required(block, "routes", CONFIG_KEY)
    routes = {name: _route_spec(name, declared[name]) for name in ROUTE_ORDER if name in declared}
    missing = [name for name in ROUTE_ORDER if name not in routes]
    if missing:
        raise aa.ConfigError(
            f"{CONFIG_KEY}.routes is missing {missing}; the screen runs every route "
            "or it is not a screen"
        )

    rule_raw = _required(block, SELECTION_RULE_KEY, CONFIG_KEY)
    where = f"{CONFIG_KEY}.{SELECTION_RULE_KEY}"
    precedence = tuple(str(entry) for entry in _required(rule_raw, "precedence", where))
    if sorted(precedence) != sorted(ROUTE_ORDER):
        raise aa.ConfigError(f"{where}.precedence must order every route, got {list(precedence)}")
    rule = SelectionRule(
        id=str(_required(rule_raw, "id", where)),
        primary=str(_required(rule_raw, "primary", where)),
        tie_breakers=tuple(str(entry) for entry in _required(rule_raw, "tie_breakers", where)),
        precedence=precedence,
        min_attempts_per_route=int(_required(rule_raw, "min_attempts_per_route", where)),
        why=_flatten_why(_required(rule_raw, "why", where)),
    )

    stages = _required(block, "stages", CONFIG_KEY)
    return PerceptionConfig(
        path=resolved,
        rung=rung,
        order=ROUTE_ORDER,
        routes=routes,
        rule=rule,
        exemption=_validate_exemption(block, rung),
        capability_source=dict(_required(block, "capability_source", CONFIG_KEY)),
        stage1_arms=tuple(str(a) for a in _required(stages, "stage1_arms", "stages")),
        stage2_arms=tuple(str(a) for a in _required(stages, "stage2_arms", "stages")),
    )


# ── the stimulus: one fog-scoped map, committed beside its hash ──────────────

#: t9's own adversarial fixture, not a second copy. A fog fixture maintained in
#: two places is a fog fixture that drifts.
_FIXTURE = map_render.fog_leak_fixture()
RUNG_BRIEFING: dict[str, Any] = _FIXTURE.fogged
HIDDEN: tuple[dict[str, Any], ...] = _FIXTURE.hidden
RUNG_SNAPSHOT: dict[str, Any] = map_render.fog_snapshot(RUNG_BRIEFING)
RUNG_SNAPSHOT_HASH: str = map_render.snapshot_hash(RUNG_BRIEFING)


@dataclass(frozen=True)
class VisionStimulus:
    """One decision point: the fogged briefing, its map, and the hash of both."""

    briefing: Mapping[str, Any]
    team_id: str
    snapshot: Mapping[str, Any]
    snapshot_hash: str
    render: map_render.MapRender
    artifact: map_render.MapArtifact
    png_path: Path
    png_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "snapshot_hash": self.snapshot_hash,
            "png": self.png_path.name,
            "png_sha256": self.png_sha256,
            "meta": self.artifact.meta_path.name,
        }


def build_stimulus(
    out_dir: Any,
    *,
    briefing: Any = None,
    team_id: Optional[str] = None,
    seat: Optional[str] = None,
    board_px: int = map_render.DEFAULT_BOARD_PX,
) -> VisionStimulus:
    """Render this rung's map and commit it, through t9's own seam.

    The PNG and its sidecar land in *out_dir* so any dial can be re-inspected
    later against the hash of exactly the state that was drawn — which is also
    the key a text twin would pair on, in the lanes where twins apply.
    """
    payload = RUNG_BRIEFING if briefing is None else briefing
    artifact = map_render.write_turn_map(
        payload, out_dir, seat=seat, team_id=team_id, board_px=board_px
    )
    render = map_render.render_map(payload, team_id=team_id, board_px=board_px)
    return VisionStimulus(
        briefing=payload,
        team_id=render.team_id,
        snapshot=map_render.fog_snapshot(payload, team_id),
        snapshot_hash=artifact.snapshot_hash,
        render=render,
        artifact=artifact,
        png_path=artifact.png_path,
        png_sha256=artifact.png_sha256,
    )


def assert_rung_stimulus(stimulus: VisionStimulus) -> None:
    """Refuse a stimulus that is not the rung's own state.

    The graders' truth is computed from :data:`RUNG_SNAPSHOT`. If the image a
    mind reads were drawn from anything else, every cell would be graded
    against a board nobody showed it — and the numbers would still look fine.
    """
    if stimulus.snapshot_hash != RUNG_SNAPSHOT_HASH:
        raise aa.ConfigError(
            f"the stimulus hashes {stimulus.snapshot_hash[:12]}… but this rung's "
            f"graded truth is computed from {RUNG_SNAPSHOT_HASH[:12]}…; the map a "
            "mind reads and the answer it is graded against must be one state"
        )


# ── the delivery: what each mind is actually handed ──────────────────────────

SENSES_SYSTEM = (
    "You describe images for a teammate who cannot see them. Report only what is "
    "actually drawn. Blank ground on this map means UNOBSERVED, not empty, and "
    "naming something that is not drawn is worse than saying less."
)

SENSES_INSTRUCTION = (
    "Describe this tactical map for a teammate who must act on it without seeing "
    "it. Cover every unit, control point, resource node and mission that is drawn: "
    "its label, its position, and whose it is. Say plainly which areas are "
    "unobserved. Do not name anything that is not drawn."
)

DESCRIPTION_HEADER = "MAP DESCRIPTION (from the senses role, which saw the image):"

#: Written into the description slot when the senses lane could not produce one.
#: Explicit rather than empty: a blank description and a description that failed
#: read identically to a model, and only one of them is a degradation.
DESCRIPTION_UNAVAILABLE = "[no description available: the senses lane degraded]"

DEGRADED_SENSES_ABSENT = "vision-senses-absent"
DEGRADED_SENSES_EMPTY = "vision-senses-empty"
DEGRADED_MEDIA_REJECTED = "vision-media-rejected"


def _image_part(stimulus: VisionStimulus) -> dict[str, Any]:
    """The map as an OpenAI content part, built by the package's own builder."""
    return build_part(validate_attachment(str(stimulus.png_path)))


def senses_payload(stimulus: VisionStimulus) -> tuple[dict[str, Any], ...]:
    """What the senses role is handed: the instruction, then the image. Nothing else."""
    return ({"type": "text", "text": SENSES_INSTRUCTION}, _image_part(stimulus))


@dataclass(frozen=True)
class Delivery:
    """One route's payload, as content parts and text. Built once per attempt."""

    route: str
    description: str
    acting_parts: tuple[dict[str, Any], ...]
    senses_parts: tuple[dict[str, Any], ...]
    snapshot_hash: str
    png_sha256: str
    note: str = ""

    def image_parts(self) -> tuple[dict[str, Any], ...]:
        return tuple(part for part in self.acting_parts if part.get("type") == "image_url")

    def acting_text(self, question: str) -> str:
        """The text an acting mind reads: the description, then the question."""
        if not self.description:
            return question
        return f"{DESCRIPTION_HEADER}\n{self.description}\n\n{question}"

    def contents_for(self, role: str) -> tuple[str, ...]:
        """The content tokens this payload actually carries, derived, not declared."""
        if role == aa.ROLE_SENSES:
            tokens: list[str] = []
            for part in self.senses_parts:
                if part.get("type") == "image_url":
                    tokens.append(CONTENT_MAP_IMAGE)
                elif part.get("text") == SENSES_INSTRUCTION:
                    tokens.append(CONTENT_DESCRIBE_INSTRUCTION)
            return tuple(tokens)
        tokens = [CONTENT_QUESTION]
        if self.description:
            tokens.append(CONTENT_SENSES_DESCRIPTION)
        if self.image_parts():
            tokens.append(CONTENT_MAP_IMAGE)
        return tuple(tokens)

    def fingerprint(self) -> str:
        """A digest of what is delivered — deliberately not of which route it is.

        Two routes that fingerprinted alike would be one route measured twice,
        and a fingerprint that folded in the route id could never say so.
        """
        payload = {
            "description": self.description,
            "acting": [part.get("type") for part in self.acting_parts],
            "senses": [part.get("type") for part in self.senses_parts],
            "png": self.png_sha256 if self.image_parts() else "",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def without_image(self, note: str) -> "Delivery":
        """The same delivery with the image dropped and the drop written down."""
        return Delivery(
            route=self.route,
            description=self.description,
            acting_parts=tuple(
                part for part in self.acting_parts if part.get("type") != "image_url"
            ),
            senses_parts=self.senses_parts,
            snapshot_hash=self.snapshot_hash,
            png_sha256=self.png_sha256,
            note=note,
        )

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rewrite the first user turn so it carries this route's payload.

        A new list is returned rather than the loop's own being mutated: the
        loop re-sends its whole transcript every turn, so injecting fresh each
        call is idempotent and leaves windowing and re-parse paths meeting the
        plain strings they expect.
        """
        out: list[dict[str, Any]] = []
        injected = False
        for message in messages:
            if not injected and message.get("role") == "user":
                out.append({**message, "content": self._content(message.get("content"))})
                injected = True
            else:
                out.append(dict(message))
        if not injected:
            out.append({"role": "user", "content": self._content("")})
        return out

    def _content(self, existing: Any) -> Any:
        text = existing if isinstance(existing, str) else flatten_parts(existing or [])
        text = self.acting_text(text)
        if self.note:
            text = f"{text}\n\n{self.note}"
        if not self.acting_parts:
            return text
        return [{"type": "text", "text": text}, *[dict(part) for part in self.acting_parts]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "snapshot_hash": self.snapshot_hash,
            "png_sha256": self.png_sha256,
            "description_chars": len(self.description),
            "acting_parts": [part.get("type") for part in self.acting_parts],
            "senses_parts": [part.get("type") for part in self.senses_parts],
            "cortex_receives": list(self.contents_for(aa.ROLE_CORTEX)),
            "senses_receives": list(self.contents_for(aa.ROLE_SENSES)),
            "fingerprint": self.fingerprint(),
            "note": self.note,
        }


def build_delivery(
    spec: RouteSpec,
    stimulus: VisionStimulus,
    *,
    description: str = "",
    senses_parts: Sequence[Mapping[str, Any]] = (),
) -> Delivery:
    """Build one route's payload. The spec governs; a caller cannot overreach it.

    Passing a description for a route whose config says it carries none is
    silently ignored rather than honoured — the committed declaration is the
    authority on what a mind receives, and a harness that could override it
    would make the declaration advisory.
    """
    return Delivery(
        route=spec.id,
        description=description.strip() if spec.senses_description else "",
        acting_parts=(_image_part(stimulus),) if spec.native_image else (),
        senses_parts=(
            tuple(dict(part) for part in senses_parts) if spec.senses_description else ()
        ),
        snapshot_hash=stimulus.snapshot_hash,
        png_sha256=stimulus.png_sha256,
    )


def describe_with_senses(
    stimulus: VisionStimulus,
    *,
    seams: Any,
    ctx: aa.CallContext,
) -> tuple[str, tuple[str, ...]]:
    """Dial the senses role once on the map, and never raise.

    This inherits the arc's rule verbatim: a senses invocation that fails
    degrades to a recorded transition, never into the caller's main path. A
    routing screen whose described arm aborted on a dead port would report the
    route as untestable when what actually failed was a socket.
    """
    mind = seams.build(aa.ROLE_SENSES, ctx, None)
    messages = [
        {"role": "system", "content": SENSES_SYSTEM},
        {"role": "user", "content": [dict(part) for part in senses_payload(stimulus)]},
    ]
    try:
        reply = mind(messages)
    except Exception as failed:  # noqa: BLE001 - never-raise is the contract here
        return DESCRIPTION_UNAVAILABLE, (
            DEGRADED_SENSES_ABSENT,
            f"{DEGRADED_SENSES_ABSENT}: {type(failed).__name__}: {failed}",
        )
    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        return DESCRIPTION_UNAVAILABLE, (DEGRADED_SENSES_EMPTY,)
    return text, ()


# ── the fog scan (h15): every route, unchanged ───────────────────────────────


def _decode_image(part: Mapping[str, Any]) -> bytes:
    url = str((part.get("image_url") or {}).get("url") or "")
    _, _, encoded = url.partition(",")
    return base64.b64decode(encoded)


def _delivered_texts(delivery: Delivery) -> list[str]:
    """Every string that reaches a mind on this route, question text included."""
    texts = [delivery.acting_text(problem.statement) for problem in PROBLEMS.values()]
    texts += [
        str(part.get("text") or "")
        for part in (*delivery.acting_parts, *delivery.senses_parts)
        if part.get("type") == "text"
    ]
    return texts


def scan_delivery(
    delivery: Delivery,
    stimulus: VisionStimulus,
    hidden: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Every way *hidden* could have reached a mind on this route. Empty is clean.

    The scan runs over the payload — the bytes and the strings actually
    delivered — rather than over the renderer's in-memory state, because this
    rung adds two new paths for state to reach a model and only one of them is
    the image. The description is model output, so the text half is not
    politeness: a mind that names something the image never showed is exactly
    the failure that would destroy the described route, and it would look like
    a good description.
    """
    leaks: list[str] = []
    texts = _delivered_texts(delivery)
    for entity in hidden:
        ident = str(entity.get("id") or "")
        if not ident:
            continue
        for text in texts:
            if ident.lower() in text.lower():
                leaks.append(f"{delivery.route}: delivered text names {ident}")
                break
    side = map_render.GLYPH_PROBE_PX
    for part in (*delivery.acting_parts, *delivery.senses_parts):
        if part.get("type") != "image_url":
            continue
        raster = map_render.decode_png(_decode_image(part))
        for entity in hidden:
            pos = entity.get("pos") or {}
            px, py = stimulus.render.layout.to_pixel(int(pos.get("x", 0)), int(pos.get("y", 0)))
            drawn = raster.indices_in(px - side, py - side, 2 * side + 1, 2 * side + 1)
            if drawn - map_render.PLANE_INDICES:
                leaks.append(f"{delivery.route}: delivered image draws {entity.get('id')}")
    return leaks


def assert_fog_scoped(delivery: Delivery, stimulus: VisionStimulus) -> None:
    """Refuse to run a cell whose payload leaks. h15, at run time and not only in tests.

    A leaked cell is not a bad datum, it is a **defective instrument**: whatever
    the mind then answered, it was answering about a board it was not entitled
    to see, and no analysis downstream can tell that from a good answer. So the
    run stops rather than recording it — the same posture t9 takes towards a
    renderer that draws what the fog removed, and the same posture the twin
    rule takes towards an image with no matching text.

    This is the half of ``h31`` that the information-matching exemption does
    **not** touch. Every route passes through here, unchanged.
    """
    leaks = scan_delivery(delivery, stimulus, HIDDEN)
    if leaks:
        raise FogLeak(
            "this cell's payload would show a mind ground the fog removed: "
            + "; ".join(leaks)
            + ". The routing rung is exempt from information matching, never from "
            "visibility — a leaked cell is a defective instrument, not a result."
        )


# ── the routed seams ─────────────────────────────────────────────────────────


class RoutedSeam:
    """One mind, with the route's payload folded into its first user turn.

    Delivery lives here rather than on the task because the route is a
    *transport* concern: every arm asks the identical question with the
    identical tools, and only how the map arrives differs. Building it here
    keeps the three routes byte-identical everywhere else.
    """

    def __init__(self, inner: Any, owner: "RoutedSeams") -> None:
        self._inner = inner
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        try:
            return self._inner(self._owner.delivery.apply(messages))
        except Exception as failed:
            if not self._owner.on_media_rejection(failed):
                raise
            return self._inner(self._owner.delivery.apply(messages))


class RoutedSeams:
    """t5's seam factory, wrapped so every acting mind gets this route's payload.

    ``degradations`` is the C3 half: a model that refuses the image leaves a
    recorded transition and the run continues text-only, rather than the image
    silently not being there. The flattened delivery replaces this run's, so
    the retry is structurally different from the attempt that failed and no
    later turn re-offers a part the server has already refused.
    """

    def __init__(self, base: Any, delivery: Delivery) -> None:
        self.base = base
        self.delivery = delivery
        self.degradations: list[str] = []

    @property
    def log(self) -> aa.CallLog:
        return self.base.log

    @property
    def live(self) -> bool:
        return bool(getattr(self.base, "live", False))

    def model_for(self, role: str) -> str:
        return self.base.model_for(role)

    def build(self, role: str, ctx: aa.CallContext, tools: Optional[list[dict[str, Any]]]) -> Any:
        return RoutedSeam(self.base.build(role, ctx, tools), self)

    def on_media_rejection(self, failed: Exception) -> bool:
        """Flatten and record, or decline. Only a media refusal is retried."""
        if not is_media_rejection(str(failed)):
            return False
        if not self.delivery.image_parts():
            return False
        self.degradations.append(DEGRADED_MEDIA_REJECTED)
        self.degradations.append(f"{DEGRADED_MEDIA_REJECTED}: {failed}")
        self.delivery = self.delivery.without_image(
            "[the map image was refused by this model and has been dropped from this run]"
        )
        return True


# ── the rung's questions, and truth computed from the snapshot ───────────────

VERDICT_CORRECT = "CORRECT"
VERDICT_WRONG = "WRONG"
VERDICT_NO_ANSWER = "NO ANSWER"

#: This rung's failure class. ``docs/challenge-problems.md``'s vocabulary is
#: protocol / capacity, and a perception failure is neither: the mind can run
#: the plan and has room to run it — it read the board wrongly, or read a board
#: that was never shown.
FAILURE_PERCEPTION = "perception"

DIFFICULTY_SIMPLE = aa.DIFFICULTY_SIMPLE
DIFFICULTY_COMPLEX = aa.DIFFICULTY_COMPLEX

_SOURCE = "examples/arch_vision.py; fixture examples/map_render.fog_leak_fixture()"


#: The exact string the confabulation trap wants back. A model that invents a
#: rival it was never shown is the failure this rung hunts, so the honest answer
#: has to be sayable.
NO_RIVAL = "NONE VISIBLE"

#: What a truth function says when the fixture holds nothing to name. Never an
#: empty string: "there is none" and "I did not look" must not read alike.
NO_ENTITY = "NONE"


def _entities(snapshot: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    board = snapshot.get("board") or {}
    return [e for e in (board.get("entities") or []) if e.get("kind") == kind]


def _acting_team(snapshot: Mapping[str, Any]) -> str:
    return str(snapshot.get("team_id") or "")


def _xy(entity: Mapping[str, Any]) -> tuple[int, int]:
    pos = entity.get("pos") or {}
    return int(pos.get("x", 0)), int(pos.get("y", 0))


def truth_own_units(snapshot: Mapping[str, Any]) -> int:
    """How many of the acting team's units the fogged board actually shows."""
    acting = _acting_team(snapshot)
    return len([e for e in _entities(snapshot, "unit") if e.get("team_id") == acting])


def truth_held_point(snapshot: Mapping[str, Any]) -> str:
    """The control point the acting team owns, by id."""
    acting = _acting_team(snapshot)
    held = [e for e in _entities(snapshot, "control_point") if e.get("team_id") == acting]
    return str(held[0].get("id")) if held else NO_ENTITY


def truth_visible_rival(snapshot: Mapping[str, Any]) -> str:
    """A rival unit the acting team can see — or, on a fogged board, none."""
    acting = _acting_team(snapshot)
    rivals = [e for e in _entities(snapshot, "unit") if e.get("team_id") != acting]
    return str(rivals[0].get("id")) if rivals else NO_RIVAL


def truth_closest_unit(snapshot: Mapping[str, Any]) -> str:
    """Which of the acting team's units is nearest a resource node, by id.

    Two positions and a comparison rather than one lookup — the routable
    "complex" end of this rung, computed here so no hand-written answer can
    disagree with the board that is drawn.
    """
    acting = _acting_team(snapshot)
    nodes = [_xy(node) for node in _entities(snapshot, "resource_node")]
    units = [e for e in _entities(snapshot, "unit") if e.get("team_id") == acting]
    if not nodes or not units:
        return NO_ENTITY

    def nearest(unit: Mapping[str, Any]) -> tuple[int, str]:
        ux, uy = _xy(unit)
        span = min((ux - nx) ** 2 + (uy - ny) ** 2 for nx, ny in nodes)
        return span, str(unit.get("id"))

    return min(nearest(unit) for unit in units)[1]


def _hidden_ids(kind: str) -> tuple[str, ...]:
    """The fixture's own hidden entities, reused as the trap's distractors.

    They are exactly the things a confabulating mind would name, so a grader
    that recognises them can tell "wrong" from "did not answer" — which a
    grader keyed only on the right answer cannot.
    """
    return tuple(str(e.get("id")) for e in HIDDEN if e.get("kind") == kind)


def _no_answer(expected: Any) -> dict[str, Any]:
    return {
        "answer": None,
        "expected": expected,
        "is_correct": False,
        "verdict": VERDICT_NO_ANSWER,
    }


def _graded(answer: Any, expected: Any, correct: bool) -> dict[str, Any]:
    return {
        "answer": answer,
        "expected": expected,
        "is_correct": correct,
        "verdict": VERDICT_CORRECT if correct else VERDICT_WRONG,
    }


def grade_count(raw: str, expected: Any) -> dict[str, Any]:
    """An integer answer. Hedging across two numbers is not an answer."""
    found = sorted({int(match) for match in re.findall(r"\d+", raw or "")})
    if not found:
        return _no_answer(expected)
    if len(found) != 1:
        return _graded(found, expected, False)
    return _graded(found[0], expected, found[0] == int(expected))


def _identifier_grader(candidates: Sequence[str]) -> Callable[[str, Any], dict[str, Any]]:
    """Grade an id answer against a closed candidate set including the distractors."""

    def grade(raw: str, expected: Any) -> dict[str, Any]:
        text = (raw or "").lower()
        found = sorted({name for name in candidates if name.lower() in text})
        if not found:
            return _no_answer(expected)
        if len(found) != 1:
            return _graded(found, expected, False)
        return _graded(found[0], expected, found[0] == str(expected))

    return grade


def grade_rival(raw: str, expected: Any) -> dict[str, Any]:
    """The confabulation trap: naming an unobserved rival is wrong, not blank."""
    text = (raw or "").lower()
    named = sorted({name for name in _hidden_ids("unit") if name.lower() in text})
    if named:
        return _graded(named, expected, False)
    if "none" in text:
        return _graded(NO_RIVAL, expected, str(expected) == NO_RIVAL)
    return _no_answer(expected)


@dataclass(frozen=True)
class Question:
    """One graded question about the rung's map, with its truth *computed*.

    Nothing here types an answer in. A hand-written answer and a rendered map
    can disagree, and the disagreement would be invisible: every cell would
    grade cleanly against a board nobody was shown.
    """

    id: str
    statement: str
    difficulty: str
    truth: Callable[[Mapping[str, Any]], Any]
    grade: Callable[[str, Any], dict[str, Any]]
    wrong: str
    paraphrase: str
    why: str


_YOU = (
    "You are commanding team Blue. You have been given this turn's board as it "
    "is known to you — blank ground is UNOBSERVED, not empty. Answer the "
    "question below and submit it with `finish`.\n\n"
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        id="units",
        statement=_YOU + "How many units of your own team are on the board? Answer with a number.",
        difficulty=DIFFICULTY_SIMPLE,
        truth=truth_own_units,
        grade=grade_count,
        wrong="There are 5 friendly units.",
        paraphrase="Counting them up, I make it 2 friendly units on the board.",
        why="the floor: a count. A mind that cannot do this cannot read the map at all",
    ),
    Question(
        id="holding",
        statement=_YOU + "Which control point does your team currently hold? Answer with its id.",
        difficulty=DIFFICULTY_SIMPLE,
        truth=truth_held_point,
        grade=_identifier_grader(
            tuple(str(e.get("id")) for e in _entities(RUNG_SNAPSHOT, "control_point"))
            + _hidden_ids("control_point")
        ),
        wrong="We hold cp-east.",
        paraphrase="The one under our control is cp-west.",
        why="a lookup with an owner test, and a distractor the fog removed",
    ),
    Question(
        id="rival",
        statement=(
            _YOU + "Name the id of any rival unit you can see. If you can see none, "
            f"answer exactly {NO_RIVAL}."
        ),
        difficulty=DIFFICULTY_SIMPLE,
        truth=truth_visible_rival,
        grade=grade_rival,
        wrong="red-1 is holding the eastern edge.",
        paraphrase="None visible — I cannot see a single rival unit from here.",
        why=(
            "the confabulation trap, and the only question whose right answer is a "
            "refusal. Unobserved ground is where a vision model invents, and a rung "
            "that never asks about absence never measures it"
        ),
    ),
    Question(
        id="closest",
        statement=(
            _YOU + "Which of your units is closest to the resource node? Answer with "
            "its unit id."
        ),
        difficulty=DIFFICULTY_COMPLEX,
        truth=truth_closest_unit,
        grade=_identifier_grader(
            tuple(
                str(e.get("id"))
                for e in _entities(RUNG_SNAPSHOT, "unit")
                if e.get("team_id") == _acting_team(RUNG_SNAPSHOT)
            )
        ),
        wrong="blue-1 is nearest to it.",
        paraphrase="Comparing the two, the closer one is blue-2.",
        why=(
            "two positions and a comparison rather than one lookup — the routable "
            "'complex' end of the rung, and what makes the hybrid arm's routing "
            "decision able to vary at all"
        ),
    ),
)


class VisionBench:
    """The rung's whole tool surface: submit an answer. Nothing else exists.

    Perception here is *delivered*, never fetched: a `look` verb would make the
    routes differ in how much work a mind does as well as in what it receives,
    and the screen would stop measuring routing.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answer = ""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in aa.FINAL_AUTHORITY_TOOLS:
            raise UnknownToolError(
                f"{name!r} is not available on this rung; its tools are "
                f"{list(aa.FINAL_AUTHORITY_TOOLS)}"
            )
        self.answer = str(arguments.get("answer", "")).strip()
        return ToolOutcome(result="submitted", finished=True, finish_summary=self.answer)

    def state(self) -> str:
        return f"{len(self.calls)} tool call(s) on the perception rung"


_FINISH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": aa.FINAL_AUTHORITY_TOOLS[0],
        "description": "Submit your answer to the question you were asked.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
}


def _problem_for(question: Question) -> aa.Problem:
    expected = question.truth(RUNG_SNAPSHOT)

    def grade(raw: str) -> dict[str, Any]:
        return question.grade(raw, expected)

    return aa.Problem(
        id=question.id,
        statement=question.statement,
        difficulty=question.difficulty,
        failure_class=FAILURE_PERCEPTION,
        source=_SOURCE,
        tools=(dict(_FINISH_SCHEMA),),
        make_bench=VisionBench,
        grade=grade,
    )


PROBLEMS: dict[str, aa.Problem] = {question.id: _problem_for(question) for question in QUESTIONS}

RUNG = aa.Rung(
    id=RUNG_ID,
    problems=tuple(question.id for question in QUESTIONS),
    why=(
        "one fog-scoped decision point, four questions about it: three lookups and "
        "one two-position comparison, so the rung is heterogeneous and the hybrid "
        "arm's routing has room to vary. The right answer to one of them is a "
        "refusal, which is the only way to measure what a mind does with ground it "
        "cannot see"
    ),
)


def problems_markdown() -> str:
    """The rung's statements and verified answers, in the shape the problems doc wants.

    ``docs/challenge-problems.md`` owns the authoritative statements and says
    that a challenge is added there first. This rung's problems are *generated*
    from a committed fixture rather than authored, so this renders them for
    transcription instead of retyping them into a second place where they could
    disagree with the map that is actually drawn.
    """
    lines = [
        f"## The perception-routing rung ({RUNG_ID})",
        "",
        "Four questions about ONE fog-scoped board — the fixture in",
        "`examples/map_render.fog_leak_fixture()`, rendered by `examples/map_render.py`.",
        "Every answer below is **computed** from that fixture's fog snapshot",
        f"(`{RUNG_SNAPSHOT_HASH[:16]}…`) by `examples/arch_vision.py`, never typed in:",
        "a hand-written answer and a rendered map can disagree silently.",
        "",
        f"Acting team: `{_acting_team(RUNG_SNAPSHOT)}`.",
        "",
    ]
    for question in QUESTIONS:
        expected = question.truth(RUNG_SNAPSHOT)
        quoted = [f"> {line}" if line.strip() else ">" for line in question.statement.splitlines()]
        lines += [
            f"### {question.id}",
            "",
            *quoted,
            "",
            f"**Answer: {expected}.** ({question.difficulty}) {question.why}.",
            "",
        ]
    return "\n".join(lines)


# ── stage 1: the screen ──────────────────────────────────────────────────────


def read_records(source: Any) -> list[dict[str, Any]]:
    """Every record in an artifact, or the records themselves."""
    if isinstance(source, (str, Path)):
        return aa.read_log(Path(source))
    return [dict(record) for record in source]


def _emit(out: Optional[Path], sink: list[dict[str, Any]], payload: Mapping[str, Any]) -> None:
    record = dict(payload)
    sink.append(record)
    if out is not None:
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _open_artifact(out: Optional[Path]) -> None:
    if out is None:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)


def _preamble(
    *,
    stage: int,
    perception: PerceptionConfig,
    config: aa.ArchConfig,
    senses_hash: str,
    live: bool,
    arms: Sequence[str],
    stimulus: VisionStimulus,
) -> dict[str, Any]:
    return {
        "kind": aa.KIND_PREAMBLE,
        "series": "orchestrator-worker-architectures",
        "task": "t8",
        "result_kind": RESULT_KIND,
        "stage": stage,
        "rung": RUNG.to_dict(PROBLEMS),
        "arms": list(arms),
        "perception": perception.to_dict(),
        "capability_facts": [fact.to_dict() for fact in CAPABILITY_FACTS.values()],
        "fog_scan_applies_to": list(FOG_SCAN_APPLIES_TO),
        "senses_config_hash": senses_hash,
        "config_path": str(config.path) if config.path else None,
        "live": live,
        "stimulus": stimulus.to_dict(),
        "scripted_note": (
            "" if live else "scripted minds: this run exercises wiring and claims nothing"
        ),
    }


def _run_cells(
    *,
    stage: int,
    route: str,
    arms: Sequence[str],
    config: aa.ArchConfig,
    perception: PerceptionConfig,
    seams: Any,
    stimulus: VisionStimulus,
    senses_hash: str,
    attempts: int,
    identity: Optional[str],
    stamp: Mapping[str, Any],
    out: Optional[Path],
    sink: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every arm at one route, recorded as it goes. The one place a cell is run."""
    spec = perception.spec(route)
    live = bool(getattr(seams, "live", False))
    cells: list[dict[str, Any]] = []
    for arm_id in arms:
        cell = aa.CellResult(arm=arm_id, rung=RUNG_ID, route=route, senses_config_hash=senses_hash)
        for _ in range(max(1, attempts)):
            for problem_id in RUNG.problems:
                ctx = aa.CallContext(
                    arm=arm_id,
                    rung=RUNG_ID,
                    problem=problem_id,
                    route=route,
                    senses_hash=senses_hash,
                    live=live,
                )
                description, degraded = "", ()
                payload: tuple[Mapping[str, Any], ...] = ()
                if spec.senses_description:
                    description, degraded = describe_with_senses(stimulus, seams=seams, ctx=ctx)
                    payload = senses_payload(stimulus)
                delivery = build_delivery(
                    spec, stimulus, description=description, senses_parts=payload
                )
                assert_fog_scoped(delivery, stimulus)
                routed = RoutedSeams(seams, delivery)
                attempt = aa.run_attempt(
                    arm=aa.ARMS[arm_id],
                    rung=RUNG_ID,
                    problem=PROBLEMS[problem_id],
                    seams=routed,
                    config=config,
                    senses_hash=senses_hash,
                    route=route,
                    identity=identity,
                )
                cell.attempts.append(attempt)
                record = attempt.to_dict()
                record["degradation_codes"] = [
                    *attempt.degradation_codes,
                    *degraded,
                    *routed.degradations,
                ]
                record.update(stamp)
                record["snapshot_hash"] = stimulus.snapshot_hash
                record["delivery"] = routed.delivery.to_dict()
                _emit(out, sink, record)
        payload_cell = cell.to_dict()
        payload_cell.update(stamp)
        payload_cell["snapshot_hash"] = stimulus.snapshot_hash
        cells.append(payload_cell)
        _emit(out, sink, payload_cell)
    return cells


def run_stage1(
    *,
    config: aa.ArchConfig,
    perception: PerceptionConfig,
    seams: Any,
    log: aa.CallLog,
    raw_dir: Any,
    out: Optional[Path] = None,
    attempts: int = 1,
    identity: Optional[str] = None,
) -> dict[str, Any]:
    """The screen: every route, on the flat arm alone.

    There is no argument for running a subset. A screen you can stop after two
    routes is a screen whose third route can be omitted once the first two look
    the way someone hoped.
    """
    senses_hash = aa.assert_senses_identical(config)
    stimulus = build_stimulus(raw_dir, seat=RUNG_ID)
    assert_rung_stimulus(stimulus)
    stamp = {"stage": STAGE_SCREEN, "result_kind": RESULT_KIND}
    sink: list[dict[str, Any]] = []
    _open_artifact(out)
    log.subscribe(lambda record: _emit(out, sink, {**record.to_dict(), **stamp}))
    _emit(
        out,
        sink,
        _preamble(
            stage=STAGE_SCREEN,
            perception=perception,
            config=config,
            senses_hash=senses_hash,
            live=bool(getattr(seams, "live", False)),
            arms=perception.stage1_arms,
            stimulus=stimulus,
        ),
    )
    cells: list[dict[str, Any]] = []
    for route in perception.order:
        cells += _run_cells(
            stage=STAGE_SCREEN,
            route=route,
            arms=perception.stage1_arms,
            config=config,
            perception=perception,
            seams=seams,
            stimulus=stimulus,
            senses_hash=senses_hash,
            attempts=attempts,
            identity=identity,
            stamp=stamp,
            out=out,
            sink=sink,
        )
    return {
        "kind": RESULT_KIND,
        "stage": STAGE_SCREEN,
        "rung": RUNG_ID,
        "arms": list(perception.stage1_arms),
        "routes": list(perception.order),
        "cells": cells,
        "records": sink,
        "snapshot_hash": stimulus.snapshot_hash,
        "live": bool(getattr(seams, "live", False)),
    }


# ── the selection: pre-registered, sealed, and blind to stage 2 ──────────────


@dataclass(frozen=True)
class RouteEvidence:
    """What one route did in the screen. The only input the rule is allowed."""

    route: str
    attempted: int
    correct: int
    total_tokens: int
    truncated_calls: int
    degraded: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "attempted": self.attempted,
            "correct": self.correct,
            "total_tokens": self.total_tokens,
            "truncated_calls": self.truncated_calls,
            "degraded": self.degraded,
        }


def _seal(rule: str, route: str, evidence_hash: str) -> str:
    canonical = "|".join((SEAL_SCHEMA, rule, route, evidence_hash))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RouteSelection:
    """The route stage 1 chose, bound to the evidence that chose it.

    The seal is not secrecy — anyone can compute it. It is *linkage*: a stage-2
    artifact carrying this seal can be re-derived from the committed screen, so
    editing either side afterwards shows up, including the case where the route
    still matches and only the evidence moved.
    """

    route: str
    rule: str
    basis: str
    separated: bool
    evidence_hash: str
    seal: str
    ranking: tuple[str, ...]
    evidence: tuple[RouteEvidence, ...]

    def __post_init__(self) -> None:
        if self.seal != _seal(self.rule, self.route, self.evidence_hash):
            raise PostHocError(
                f"this selection claims route {self.route!r} under rule {self.rule!r} "
                "but its seal does not match that evidence; a selection is derived "
                "from a screen, never asserted"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "rule": self.rule,
            "basis": self.basis,
            "separated": self.separated,
            "evidence_hash": self.evidence_hash,
            "seal": self.seal,
            "ranking": list(self.ranking),
            "evidence": [entry.to_dict() for entry in self.evidence],
        }


def gather_evidence(source: Any) -> dict[str, RouteEvidence]:
    """Fold a screen's attempts into per-route evidence, refusing stage-2 data.

    The refusal is the load-bearing part and it happens before any arithmetic:
    a selection computed from records that include the carry stage is a
    post-hoc pick wearing a rule's clothes.
    """
    records = read_records(source)
    for record in records:
        if record.get("stage") == STAGE_CARRY:
            raise PostHocError(
                "this log contains stage-2 records; the route is chosen from the "
                "screen alone, so stage-2 data can never reach the rule that picks it"
            )
    folded: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("kind") != aa.KIND_ATTEMPT or record.get("stage") != STAGE_SCREEN:
            continue
        folded.setdefault(str(record.get("route") or ""), []).append(record)
    evidence: dict[str, RouteEvidence] = {}
    for route, attempts in folded.items():
        costs = [dict(attempt.get("cost") or {}) for attempt in attempts]
        evidence[route] = RouteEvidence(
            route=route,
            attempted=len(attempts),
            correct=sum(1 for attempt in attempts if attempt.get("is_correct")),
            total_tokens=sum(
                int(cost.get("prompt_tokens", 0)) + int(cost.get("completion_tokens", 0))
                for cost in costs
            ),
            truncated_calls=sum(int(cost.get("truncated_calls", 0)) for cost in costs),
            degraded=sum(1 for attempt in attempts if attempt.get("degradation_codes")),
        )
    return evidence


def _evidence_hash(evidence: Iterable[RouteEvidence]) -> str:
    canonical = json.dumps(
        [entry.to_dict() for entry in sorted(evidence, key=lambda entry: entry.route)],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def select_route(source: Any, *, perception: Optional[PerceptionConfig] = None) -> RouteSelection:
    """Apply the pre-registered rule to a screen, and seal the result.

    Correctness first; a tie falls to total tokens, then to the committed
    precedence. Only a correctness win sets ``separated`` — a tie-broken pick
    is recorded as a tie-broken pick, because "the cheapest of three equals"
    and "this route won" are different sentences and only one of them is true.
    """
    resolved = perception if perception is not None else load_perception_config()
    rule = resolved.rule
    evidence = gather_evidence(source)

    missing = [route for route in resolved.order if route not in evidence]
    if missing:
        raise IncompleteScreen(
            f"the screen has no cell for {missing}; stage 1 runs every route or the "
            "comparison is between whatever happened to finish"
        )
    thin = [
        route for route in resolved.order if evidence[route].attempted < rule.min_attempts_per_route
    ]
    if thin:
        raise IncompleteScreen(
            f"routes {thin} have fewer than {rule.min_attempts_per_route} attempts; "
            "the floor is committed in the rule and is not negotiable after the run"
        )

    def key(route: str) -> tuple[int, int, int]:
        entry = evidence[route]
        return (-entry.correct, entry.total_tokens, rule.precedence.index(route))

    ranking = tuple(sorted(resolved.order, key=key))
    winner, runner_up = evidence[ranking[0]], evidence[ranking[1]]
    if winner.correct > runner_up.correct:
        basis = BASIS_CORRECTNESS
    elif winner.total_tokens < runner_up.total_tokens:
        basis = BASIS_COST
    else:
        basis = BASIS_PRECEDENCE
    digest = _evidence_hash(evidence.values())
    return RouteSelection(
        route=ranking[0],
        rule=rule.id,
        basis=basis,
        separated=basis == BASIS_CORRECTNESS,
        evidence_hash=digest,
        seal=_seal(rule.id, ranking[0], digest),
        ranking=ranking,
        evidence=tuple(evidence[route] for route in ranking),
    )


# ── stage 2: the carry. No route can be handed to it ─────────────────────────


def run_stage2(
    *,
    stage1: Any,
    config: aa.ArchConfig,
    perception: PerceptionConfig,
    seams: Any,
    log: aa.CallLog,
    raw_dir: Any,
    out: Optional[Path] = None,
    attempts: int = 1,
    arms: Optional[Sequence[str]] = None,
    identity: Optional[str] = None,
) -> dict[str, Any]:
    """Carry the screen's winner into the four architectures.

    Note what this signature does **not** have: any way to say which route to
    run. The route is re-derived here, from the committed screen, every time —
    so there is no call site at which a person who has seen stage-2 data could
    express a different choice. That is claim ``c41``'s two-stage shape made
    structural rather than promised.
    """
    selection = select_route(stage1, perception=perception)
    senses_hash = aa.assert_senses_identical(config)
    stimulus = build_stimulus(raw_dir, seat=RUNG_ID)
    assert_rung_stimulus(stimulus)
    running = tuple(arms) if arms else perception.stage2_arms
    stamp = {
        "stage": STAGE_CARRY,
        "result_kind": RESULT_KIND,
        "selection_seal": selection.seal,
        "selection_basis": selection.basis,
        "evidence_hash": selection.evidence_hash,
    }
    sink: list[dict[str, Any]] = []
    _open_artifact(out)
    log.subscribe(lambda record: _emit(out, sink, {**record.to_dict(), **stamp}))
    preamble = _preamble(
        stage=STAGE_CARRY,
        perception=perception,
        config=config,
        senses_hash=senses_hash,
        live=bool(getattr(seams, "live", False)),
        arms=running,
        stimulus=stimulus,
    )
    preamble.update(stamp)
    preamble["selection"] = selection.to_dict()
    _emit(out, sink, preamble)
    cells = _run_cells(
        stage=STAGE_CARRY,
        route=selection.route,
        arms=running,
        config=config,
        perception=perception,
        seams=seams,
        stimulus=stimulus,
        senses_hash=senses_hash,
        attempts=attempts,
        identity=identity,
        stamp=stamp,
        out=out,
        sink=sink,
    )
    return {
        "kind": RESULT_KIND,
        "stage": STAGE_CARRY,
        "rung": RUNG_ID,
        "arms": list(running),
        "selection": selection.to_dict(),
        "cells": cells,
        "records": sink,
        "snapshot_hash": stimulus.snapshot_hash,
        "live": bool(getattr(seams, "live", False)),
    }


def verify_stage2(
    stage1: Any,
    stage2: Any,
    *,
    perception: Optional[PerceptionConfig] = None,
) -> dict[str, Any]:
    """Re-derive the screen's choice and check the carry against it.

    A committed pair is only evidence if a reader who was not there can check
    it. Editing the screen afterwards changes the evidence digest and therefore
    the seal, so tampering shows up even when the chosen route is unchanged.
    """
    selection = select_route(stage1, perception=perception)
    graded = [
        record
        for record in read_records(stage2)
        if record.get("kind") in (aa.KIND_ATTEMPT, aa.KIND_CELL)
    ]
    if not graded:
        raise PostHocError("the stage-2 artifact holds no attempts or cells to verify")
    for record in graded:
        if record.get("stage") != STAGE_CARRY:
            raise PostHocError(
                f"a stage-2 artifact holds a record stamped stage {record.get('stage')!r}"
            )
        if record.get("route") != selection.route:
            raise PostHocError(
                f"the carry ran route {record.get('route')!r} but the screen selects "
                f"{selection.route!r}; a route the screen did not choose is a post-hoc pick"
            )
        if record.get("selection_seal") != selection.seal:
            raise PostHocError(
                "the carry's seal does not match the screen it cites — one of the two "
                "artifacts was edited after the other was written"
            )
    return {
        "ok": True,
        "route": selection.route,
        "rule": selection.rule,
        "basis": selection.basis,
        "separated": selection.separated,
        "seal": selection.seal,
        "evidence_hash": selection.evidence_hash,
        "records_checked": len(graded),
    }


def analyse_stage2(source: Any, *, config: Optional[aa.ArchConfig] = None) -> dict[str, Any]:
    """t5's decision rule, re-applied to a carry artifact. Its refusal comes too.

    A verdict is only stated against BOTH flat arms; ``arch_arms.analyse``
    already refuses otherwise, and reusing it means the routing rung cannot
    quietly grow a second, softer rule.
    """
    records = [record for record in read_records(source) if record.get("stage") == STAGE_CARRY]
    return aa.analyse(records, config=config, registry=PROBLEMS, ladder=(RUNG,))


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan(perception: PerceptionConfig) -> str:
    lines = [
        f"arch_vision — the perception-routing screen, rung {RUNG_ID}",
        "",
        RUNG.why,
        "",
        "routes (what each mind receives — from the committed config):",
    ]
    for route in perception.order:
        spec = perception.spec(route)
        lines.append(f"  {route}")
        lines.append(f"      deciding mind: {', '.join(spec.cortex_receives) or '(nothing)'}")
        lines.append(f"      senses:        {', '.join(spec.senses_receives) or '(not dialled)'}")
        lines.append(f"      {spec.why}")
    lines += [
        "",
        f"stage 1: routes {list(perception.order)} on arms {list(perception.stage1_arms)}",
        f"stage 2: the selected route alone, on arms {list(perception.stage2_arms)}",
        "         (no --route flag exists; the route is re-derived from the screen)",
        "",
        f"selection rule {perception.rule.id}: {perception.rule.primary} first, then "
        f"{', then '.join(perception.rule.tie_breakers)}",
        f"  precedence: {list(perception.rule.precedence)}",
        f"  floor: {perception.rule.min_attempts_per_route} attempts per route",
        "",
        f"exemption: relaxes {perception.exemption['relaxes']} for rung "
        f"{perception.exemption['applies_to_rung']}; still binds "
        f"{perception.exemption['still_binds']}",
        f"fog scan applies to: {list(FOG_SCAN_APPLIES_TO)}",
        "",
        "capability facts (measured, never from the advert):",
    ]
    for fact in CAPABILITY_FACTS.values():
        mark = "  DISAGREES WITH ADVERT" if fact.disagrees else ""
        lines.append(f"  {fact.role}/{fact.modality}: measured={fact.measured}{mark}")
        lines.append(f"      {fact.source} — {fact.evidence!r}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the routes, the rule and the exemption")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--config", default=None)

    problems = subparsers.add_parser("problems", help="the rung's questions and verified answers")
    problems.add_argument("--markdown", action="store_true")

    screen = subparsers.add_parser("stage1", help="the screen: three routes, flat arm")
    screen.add_argument("--raw-dir", required=True, help="where measured maps are committed")
    screen.add_argument("--out", default=None)
    screen.add_argument("--attempts", type=int, default=1)
    screen.add_argument("--config", default=None)
    screen.add_argument("--live", action="store_true", help=f"needs {aa.LIVE_GATE_ENV}")

    chooser = subparsers.add_parser("select", help="apply the committed rule to a screen")
    chooser.add_argument("--stage1", required=True)
    chooser.add_argument("--config", default=None)

    # Deliberately no --route: the carry re-derives it from the screen.
    carry = subparsers.add_parser("stage2", help="carry the screen's winner into the arms")
    carry.add_argument("--stage1", required=True)
    carry.add_argument("--raw-dir", required=True)
    carry.add_argument("--out", default=None)
    carry.add_argument("--attempts", type=int, default=1)
    carry.add_argument("--config", default=None)
    carry.add_argument("--live", action="store_true", help=f"needs {aa.LIVE_GATE_ENV}")

    checker = subparsers.add_parser("verify", help="re-derive the screen and check the carry")
    checker.add_argument("--stage1", required=True)
    checker.add_argument("--stage2", required=True)
    checker.add_argument("--config", default=None)

    analyser = subparsers.add_parser("analyse", help="re-apply t5's rule to a carry artifact")
    analyser.add_argument("--log", required=True)
    analyser.add_argument("--config", default=None)
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def _refuse_live() -> int:
    try:
        aa.require_live_rig()
    except aa.LiveRigClosed as shut:
        return _fail(
            str(shut),
            f"export {aa.LIVE_GATE_ENV}=1 to dial the rig, or drop --live for the "
            "scripted lane (live runs are frozen for this cycle)",
        )
    return _fail(
        "the live lane is not dialled by this task",
        "task t12 runs the series; t8 ships the screen and its scripted lane",
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if getattr(args, "config", None) else None

    if args.command == "problems":
        print(problems_markdown() if args.markdown else json.dumps(_problems_payload(), indent=2))
        return 0

    try:
        perception = load_perception_config(config_path)
    except aa.ConfigError as broken:
        return _fail(str(broken), f"check the {CONFIG_KEY} block in {config_path or CONFIG_PATH}")

    if args.command == "plan":
        if args.json:
            print(json.dumps(_plan_payload(perception), indent=2))
        else:
            print(render_plan(perception))
        return 0

    if getattr(args, "live", False):
        return _refuse_live()

    try:
        config = aa.load_config(config_path)
    except aa.ConfigError as broken:
        return _fail(str(broken), "fix the sampling table, then re-run")

    try:
        return _dispatch(args, config=config, perception=perception)
    except (aa.ConfigError, PostHocError, IncompleteScreen, FogLeak) as refused:
        return _fail(str(refused), "read the refusal above; it names what is missing")
    except OSError as unreadable:
        return _fail(str(unreadable), "point the flag at an artifact this harness wrote")


def _dispatch(
    args: argparse.Namespace, *, config: aa.ArchConfig, perception: PerceptionConfig
) -> int:
    if args.command == "stage1":
        log = aa.CallLog()
        report = run_stage1(
            config=config,
            perception=perception,
            seams=aa.ScriptedSeams(config=config, log=log),
            log=log,
            raw_dir=Path(args.raw_dir),
            out=Path(args.out) if args.out else None,
            attempts=args.attempts,
        )
        print(json.dumps(_summary(report), indent=2, ensure_ascii=False))
        return 0

    if args.command == "select":
        selection = select_route(Path(args.stage1), perception=perception)
        print(json.dumps(selection.to_dict(), indent=2))
        return 0

    if args.command == "stage2":
        log = aa.CallLog()
        report = run_stage2(
            stage1=Path(args.stage1),
            config=config,
            perception=perception,
            seams=aa.ScriptedSeams(config=config, log=log),
            log=log,
            raw_dir=Path(args.raw_dir),
            out=Path(args.out) if args.out else None,
            attempts=args.attempts,
        )
        print(json.dumps(_summary(report), indent=2, ensure_ascii=False))
        return 0

    if args.command == "verify":
        print(
            json.dumps(
                verify_stage2(Path(args.stage1), Path(args.stage2), perception=perception),
                indent=2,
            )
        )
        return 0

    print(json.dumps(analyse_stage2(Path(args.log), config=config), indent=2, ensure_ascii=False))
    return 0


def _summary(report: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in report.items() if key != "records"}
    payload["records"] = len(report.get("records") or [])
    return payload


def _plan_payload(perception: PerceptionConfig) -> dict[str, Any]:
    return {
        "rung": RUNG.to_dict(PROBLEMS),
        "perception": perception.to_dict(),
        "exemption": dict(perception.exemption),
        "capability_facts": [fact.to_dict() for fact in CAPABILITY_FACTS.values()],
        "fog_scan_applies_to": list(FOG_SCAN_APPLIES_TO),
        "native_route_supported": native_route_supported(),
        "snapshot_hash": RUNG_SNAPSHOT_HASH,
    }


def _problems_payload() -> dict[str, Any]:
    return {
        "rung": RUNG_ID,
        "snapshot_hash": RUNG_SNAPSHOT_HASH,
        "questions": [
            {
                "id": question.id,
                "statement": question.statement,
                "difficulty": question.difficulty,
                "answer": question.truth(RUNG_SNAPSHOT),
                "why": question.why,
            }
            for question in QUESTIONS
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
