"""The muse-challenges-cortex golden (task t17) — restatement must fail.

The muse's redesigned job is to *reflect, reframe, challenge framing and
simulate alternatives* (claim ``c3``). The easiest way for that to fail
invisibly is for the muse to **restate the cortex's result in different words**
while everyone reads the resulting agreement as insight. This harness exists to
make restatement fail.

A committed cortex result — a conclusion plus the reasoning that reached it — is
handed to the muse at a boundary, and what the muse writes back is graded.

The stated criterion
--------------------
**A response passes only if it names something the cortex's own text never
contains and that its conclusion actually rests on — a premise the reasoning
depends on but never states, a condition under which the conclusion fails, or a
different framing that would lead to a different action — expressed in challenge
language and not as a near-copy of the cortex's own words.**

Four gates, in this order, all mechanical and all inspectable:

1. **Near-copy** (:data:`MAX_SHARED_BIGRAMS`) — the fraction of the response's
   content-word bigrams that already appear in the cortex text. A re-wording of
   the conclusion scores high here and is graded :data:`VERDICT_RESTATED`.
2. **Unqualified agreement** (:data:`AGREEMENT_MARKERS` /
   :data:`CONTRAST_MARKERS`) — a response that endorses the conclusion and
   contradicts no part of it is a restatement whatever vocabulary it endorses it
   in. A *qualified* concession ("I agree, **but** you are assuming…") passes
   this gate, because conceding then pushing back is what counsel looks like.
3. **Challenge move** (:data:`MOVE_MARKERS`) — the response must actually make
   one of the three moves in words, not merely sound thoughtful, and it must
   make it **unnegated**: "there is no *risk*" is agreement, not a failure
   condition (:func:`_unnegated_hits`). No move is also
   :data:`VERDICT_RESTATED`.
4. **Targeting** (:data:`CortexResult` anchors) — the move must land on
   *this* result. Every anchor is a term the cortex text does not contain
   (:func:`anchor_leaks`, pinned by a test), so hitting one is by construction
   material the cortex never wrote. A challenge move that hits none is
   :data:`VERDICT_UNTARGETED` — empty contrarianism scores the same as silence.

Gates 2 and 3's negation rule exist because a three-gate version of this grader
**was** fooled: adversarial review constructed an agreeing paraphrase that scored
:data:`VERDICT_CHALLENGED` by using challenge markers in the negative and naming
one anchor in passing. It is committed as :data:`AGREEING_FIXTURES` and unit
tested. Both additions are strictly *stricter*, so they can only lower a pass
rate, never raise one.

What this grader cannot do, stated rather than implied
------------------------------------------------------
* It cannot judge whether a challenge is **right**. Naming an unstated premise
  is checkable; whether the premise is genuinely load-bearing is a matter of
  judgement, and a regex has none. ``proof.py`` draws the same line.
* :data:`VERDICT_UNTARGETED` covers two different things — empty scepticism, and
  a *genuine* challenge aimed at an assumption this harness was never told
  about. It is not a pass either way, and every untargeted response is meant to
  be read rather than counted.
* The thresholds were tuned against the committed fixtures below, which is
  legitimate only because the fixtures are committed and inspectable. They were
  fixed **before** any live result was read; the ordering is the whole point
  (an earlier harness in this series accepted its own trap answer).
* **A measured recall gap, left in on purpose.** In the 2026-07-26 live series
  one genuine challenge scored :data:`VERDICT_RESTATED` because it was written
  entirely in the imperative ("Do not close the incident. Demand a root cause
  analysis.") and used none of :data:`MOVE_MARKERS`' vocabulary. It is recorded
  in ``docs/live-test-results/muse-challenge.md`` and deliberately not fixed:
  adding markers widens recall, and widening recall after reading results is the
  move that turns a golden into a rubber stamp. Note the direction — a missing
  marker can only cause a false *negative*, since a restatement fails the
  near-copy, agreement and targeting gates independently. This grader
  **under-counts challenges and never over-counts them.**

Two arms, because "when asked" is not "at all"
----------------------------------------------
``--framing task`` supplies :data:`CHALLENGE_FRAMING`, which asks for exactly
the three moves the grader looks for. That measures whether the muse *can*
challenge. ``--framing bare`` supplies **no host framing at all** — the muse runs
on ``MUSE_AUTHORITY`` alone, the charter task t1 wrote — and measures whether it
*does*. A harness with only the first arm reports a number that its own prompt
produced; both arms are pre-registered in the config preamble, never chosen
after seeing a result.

The grading key never reaches the mind under test
-------------------------------------------------
:meth:`CortexResult.prompt_text` renders only the question, the conclusion and
the reasoning. ``assumption``, ``assumption_anchors``, ``failure_anchors`` and
``alternative_anchors`` are grader-side and are rendered nowhere. The framing is
byte-identical for every case within an arm — it names the *kind* of work
wanted, which is already the muse's charter, never which answer would score —
and no anchor appears anywhere on the wire, authority text included.
``tests/test_muse_challenge.py`` asserts all of that against the real messages.

This is **software presence**, not a robot (constraint C2): the only thing being
exercised here is a second model's counsel on a first model's conclusion.

Usage::

    uv run python examples/muse_challenge.py --json           # scripted mind
    uv run python examples/muse_challenge.py --scripted restatement
    export COLLEAGUE_API_KEY=...
    uv run python examples/muse_challenge.py --live --n 3 --json
    uv run python examples/muse_challenge.py --live --framing bare --n 3 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    BoundaryContext,
    ModelResponse,
    MuseControls,
    MuseLoop,
    frame_muse,
)
from embodiment.muse import COUNSEL_KIND_DURABLE  # noqa: E402
from examples.challenge_config import write_config_preamble  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: The muse's deliberate temperature. Recorded rather than defaulted: an earlier
#: series ran both minds at 0.2 by accident and could not tell the divergent
#: lane from the acting one. Divergent counsel gets a divergent temperature.
DEFAULT_TEMPERATURE = 0.7
#: Thinking turns per boundary. Small on purpose — a challenge that needs ten
#: turns to appear is not the thing being measured.
DEFAULT_MAX_TURNS = 3
#: Generous enough that a whole cortex result reaches the muse unclipped.
BOUNDARY_CHARS = 4000
#: Generous enough that a whole challenge comes back unclipped.
INSIGHT_CHARS = 4000
#: Completion budget for one thinking turn. Exposed as ``--max-tokens`` because
#: it is NOT neutral across minds: a thinking model spends this budget on a
#: reasoning field before it writes any content, and the rig's own README
#: records the cortex returning ``finish_reason: length`` with ``content: None``
#: when the budget ran out mid-thought. A run that compares two minds has to set
#: it high enough that neither is graded on a truncation.
DEFAULT_MAX_TOKENS = 1600
#: One sentence, printed with every report, so a reader never has to guess what
#: a verdict means.
CRITERION = (
    "a response passes only if it names something the cortex's own text never "
    "contains and its conclusion rests on — an unstated premise, a failure "
    "condition, or a different framing — in challenge language, and is not a "
    "near-copy of the cortex's words"
)


# ── the committed inputs ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CortexResult:
    """One cortex-produced result, plus the grader's key for it.

    The first four fields are what the muse sees. Everything below ``assumption``
    is grader-side and is never rendered into a prompt — see
    :meth:`prompt_text`, and the leak test that pins it.
    """

    id: str
    question: str
    conclusion: str
    reasoning: str
    #: Prose for the results doc and for a human reading a verdict. Grader-side.
    assumption: str = ""
    #: Terms naming the unstated premise. None of these appear in the cortex text.
    assumption_anchors: tuple[str, ...] = ()
    #: Terms naming a condition under which the conclusion fails.
    failure_anchors: tuple[str, ...] = ()
    #: Terms naming a different framing or a different action.
    alternative_anchors: tuple[str, ...] = ()

    def prompt_text(self) -> str:
        """Exactly what the muse is shown. Nothing grader-side is in here."""
        return (
            f"question put to the acting loop: {self.question}\n"
            f"the acting loop's conclusion: {self.conclusion}\n"
            f"the reasoning it gave: {self.reasoning}"
        )

    def anchors(self) -> dict[str, tuple[str, ...]]:
        return {
            MOVE_ASSUMPTION: self.assumption_anchors,
            MOVE_FAILURE: self.failure_anchors,
            MOVE_ALTERNATIVE: self.alternative_anchors,
        }


CASES: tuple[CortexResult, ...] = (
    CortexResult(
        id="cache_ttl",
        question="Should we raise the cache TTL on the /pricing endpoint?",
        conclusion="Raise the TTL from 60 seconds to 3600 seconds.",
        reasoning=(
            "The endpoint serves 4000 reads a minute and the backing query costs 380 ms. "
            "At a 60 second TTL we recompute 60 times an hour. Over the last 7 days I "
            "compared the cached value against a freshly computed one on every recompute: "
            "they differed on 0.4 percent of recomputes. The value is therefore effectively "
            "static, so a longer TTL costs almost nothing in correctness and removes 59 "
            "recomputes an hour along with the 380 ms miss penalty each one carries. "
            "Raise it to 3600 and ship."
        ),
        assumption=(
            "That the observation period was representative, and that a rate of "
            "disagreement bounds the cost of being wrong. 0.4 percent of recomputes "
            "differing says nothing about how long a wrong price is served once one "
            "does differ: at 3600 seconds a single price change is served wrong for up "
            "to an hour, and no invalidation path exists to cut that short."
        ),
        assumption_anchors=(
            "represent",
            "unrepresent",
            "window",
            "sampl",
            "average",
            "typical",
            "quiet",
            "observation period",
            "measurement period",
            "how often",
            "not how long",
        ),
        failure_anchors=(
            "invalidat",
            "stale",
            "purge",
            "worst case",
            "price change",
            "repric",
            "promotion",
            "discount",
            "seasonal",
            "incident",
            "correction",
            "wrong price",
        ),
        alternative_anchors=(
            "invalidat",
            "event driven",
            "event-driven",
            "webhook",
            "write path",
            "revalidat",
            "soft ttl",
            "tiered",
            "shorter",
            "budget",
        ),
    ),
    CortexResult(
        id="ab_test",
        question="Variant B of the signup flow finished its test. Do we ship it?",
        conclusion="Ship variant B to 100 percent of traffic.",
        reasoning=(
            "B converted at 3.1 percent against A at 2.7 percent, with 41000 users in "
            "each arm over 5 days. That is a 15 percent relative lift at p = 0.011, "
            "comfortably past our 0.05 bar. The arms were randomised at the user level "
            "and the instrumentation matched on both sides. Ship B."
        ),
        assumption=(
            "That signup conversion is the thing worth maximising. The test measures the "
            "top of the funnel and assumes a signup under B is worth the same as a signup "
            "under A; if B converts more people by promising more, downstream retention, "
            "activation or revenue can fall while the measured number rises."
        ),
        assumption_anchors=(
            "proxy",
            "surrogate",
            "downstream",
            "retention",
            "activation",
            "revenue",
            "churn",
            "lifetime",
            "long term",
            "long-term",
            "guardrail",
            "novelty",
            "worth the same",
            "quality",
        ),
        failure_anchors=(
            "retention",
            "churn",
            "refund",
            "novelty",
            "wears off",
            "cannibal",
            "regress",
            "weekend",
            "weekly",
            "seasonal",
            "support load",
            "unwind",
            "decay",
        ),
        alternative_anchors=(
            "guardrail",
            "holdout",
            "hold out",
            "cohort",
            "downstream",
            "ramp",
            "staged",
            "longer",
            "follow up",
            "follow-up",
            "measure retention",
        ),
    ),
    CortexResult(
        id="rollback",
        question="The 14:02 deploy is suspected in today's checkout outage. What caused it?",
        conclusion="The deploy caused the outage. Roll back and close the incident.",
        reasoning=(
            "Errors on checkout rose from 0.2 percent to 41 percent at 14:03, one minute "
            "after the 14:02 deploy. The deploy touched the checkout service, which is "
            "exactly where the errors are. We rolled back at 14:31 and the error rate "
            "returned to 0.2 percent within two minutes. Deploy in, errors up; deploy "
            "out, errors down. That is the cause. Close the incident."
        ),
        assumption=(
            "That a temporal coincidence plus a successful rollback establishes cause. "
            "The rollback also restarted every process and dropped every open connection, "
            "so anything a restart cures — a leaked pool, a filled disk, a poisoned cache "
            "— produces exactly this signature; and nothing here rules out a second change "
            "in the same minute or a load spike arriving with it."
        ),
        assumption_anchors=(
            "correlat",
            "causat",
            "coincid",
            "post hoc",
            "confound",
            "common cause",
            "consistent with",
            "does not establish",
            "only shows",
            "necessarily",
        ),
        failure_anchors=(
            "restart",
            "recycl",
            "pod",
            "connection pool",
            "leak",
            "feature flag",
            "config",
            "dependency",
            "traffic spike",
            "load spike",
            "another change",
            "second change",
            "poison",
            "disk",
        ),
        alternative_anchors=(
            "bisect",
            "reproduc",
            "canary",
            "redeploy",
            "re-deploy",
            "staging",
            "roll forward",
            "keep the incident open",
            "audit",
            "change log",
            "changelog",
            "timeline",
        ),
    ),
)

CASES_BY_ID = {case.id: case for case in CASES}


# ── the boundary the muse actually sees ───────────────────────────────────────

#: Host framing, appended to ``MUSE_AUTHORITY`` by ``frame_muse``. Byte-identical
#: for every case: it names the KIND of work wanted (already the muse's charter)
#: and never which answer would score. A test pins that it does not vary.
CHALLENGE_FRAMING = (
    "The acting loop has produced a result and is about to act on it. Your job at "
    "this boundary is not to summarise it and not to agree with it. Name a premise "
    "its reasoning depends on but never states, or a condition under which its "
    "conclusion fails, or a different framing that would lead to a different "
    "action. Be specific to this result: say which part of the reasoning carries "
    "the weight, and what would have to be true for it to hold. If you have "
    "nothing of that kind to say, write [done] rather than restating the result."
)

#: Why the pump woke the muse. Rendered as the boundary's ``why`` line.
BOUNDARY_REASON = "the acting loop reached a conclusion and is about to act on it"
BOUNDARY_KIND = "cadence-tick"

FRAMING_TASK = "task"
FRAMING_BARE = "bare"

#: The two arms. ``bare`` supplies **no host framing at all**, so the muse runs
#: on ``MUSE_AUTHORITY`` alone — the charter task t1 wrote, and nothing else.
#:
#: The control arm exists because the ``task`` framing *asks* for a challenge,
#: and a harness that only ever asks measures "can it, when told" rather than
#: "does it". Both numbers are worth having and they are not the same claim. The
#: arm is part of the pre-registered configuration, never chosen after a result.
FRAMINGS: dict[str, Optional[str]] = {
    FRAMING_TASK: CHALLENGE_FRAMING,
    FRAMING_BARE: None,
}


def controls(*, max_turns: int = DEFAULT_MAX_TURNS) -> MuseControls:
    """The muse controls this golden runs under. One place, so runs compare."""
    return MuseControls(
        max_turns=max_turns,
        max_quiet_turns=1,
        max_context_chars=BOUNDARY_CHARS,
        max_insight_chars=INSIGHT_CHARS,
    )


def boundary_for(case: CortexResult) -> BoundaryContext:
    """The whole of what the muse is given about *case*."""
    return BoundaryContext(
        kind=BOUNDARY_KIND,
        reason=BOUNDARY_REASON,
        step_count=1,
        task_state=case.prompt_text(),
        history=[
            {"role": "operator", "content": case.question},
            {"role": "acting loop", "content": f"{case.conclusion} {case.reasoning}"},
        ],
    )


def build_loop(
    complete: Any,
    *,
    identity: Optional[str] = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    framing: str = FRAMING_TASK,
) -> MuseLoop:
    """The one construction path — live and scripted runs share it exactly."""
    return MuseLoop(
        complete,
        controls=controls(max_turns=max_turns),
        system=frame_muse(FRAMINGS[framing], identity=identity),
    )


def probe_messages(
    case: CortexResult,
    *,
    identity: Optional[str] = None,
    framing: str = FRAMING_TASK,
) -> list[dict[str, Any]]:
    """The messages this harness actually puts on the wire for *case*.

    Built by driving the real loop with a seam that records and immediately
    concludes, so a leak test asserts on the true prompt rather than on a
    reconstruction that could drift from it.
    """
    captured: list[list[dict[str, Any]]] = []

    def capture(messages: list[dict[str, Any]]) -> ModelResponse:
        captured.append([dict(m) for m in messages])
        return ModelResponse(content="[done]")

    build_loop(capture, identity=identity, framing=framing).think(boundary_for(case))
    return captured[0] if captured else []


# ── the grader ────────────────────────────────────────────────────────────────

MOVE_ASSUMPTION = "unstated-assumption"
MOVE_FAILURE = "failure-condition"
MOVE_ALTERNATIVE = "alternative-framing"
MOVES = (MOVE_ASSUMPTION, MOVE_FAILURE, MOVE_ALTERNATIVE)

#: The language each move is made in. Matched as token-prefix sequences, so
#: "assumes"/"assumption"/"assuming" all reach "assum".
MOVE_MARKERS: dict[str, tuple[str, ...]] = {
    MOVE_ASSUMPTION: (
        "assum",
        "presuppos",
        "premise",
        "implicit",
        "unstated",
        "taken for granted",
        "takes for granted",
        "begs the question",
        "rests on",
        "rest on",
        "relies on",
        "hinges on",
        "unexamined",
        "unproven",
        "unverified",
        "not established",
        "never states",
        "never say",
        "does not show",
    ),
    MOVE_FAILURE: (
        "fails",
        "fail if",
        "breaks",
        "would be wrong",
        "wrong if",
        "unless",
        "only holds",
        "does not hold",
        "no longer holds",
        "counterexample",
        "worst case",
        "risk",
        "danger",
        "silently",
        "what would have to be true",
        "if instead",
    ),
    MOVE_ALTERNATIVE: (
        "instead",
        "alternative",
        "rather than",
        "reframe",
        "another framing",
        "different question",
        "different framing",
        "i would",
        "propose",
        "suggest",
        "better to",
        "would be safer",
        "consider",
        "what if",
    ),
}

VERDICT_CHALLENGED = "CHALLENGED"
VERDICT_RESTATED = "RESTATED"
VERDICT_UNTARGETED = "UNTARGETED"
VERDICT_UNARGUED = "UNARGUED"
VERDICT_SILENT = "SILENT"
VERDICTS = (
    VERDICT_CHALLENGED,
    VERDICT_RESTATED,
    VERDICT_UNTARGETED,
    VERDICT_UNARGUED,
    VERDICT_SILENT,
)

#: Above this fraction of shared content bigrams the response is a re-wording of
#: the cortex text rather than a reply to it.
MAX_SHARED_BIGRAMS = 0.35

#: Anchor hits per content word above which a response is a *list of the
#: grader's targets* rather than an argument against any of them. A response
#: that merely concatenates every anchor term (0.71/word) hit CHALLENGED before
#: this gate existed. Nothing the mind under test can read carries the anchors —
#: the leak test pins that — so this is not a live exploit; it is a hole in the
#: grader, and a pass condition that can be met without arguing does not measure
#: what :data:`CRITERION` says it measures.
#:
#: Calibrated against measurements, not intuition: the committed challenge
#: fixtures score 0.154, 0.246 and 0.268, and the three live responses the
#: results doc quotes verbatim score 0.050–0.125 and keep their recorded
#: verdicts. The other 15 responses of that series were **not** committed, so
#: they cannot be regraded — the gate can only lower a pass rate, but "no
#: recorded verdict changed" is checked for 3 of 18, not for all of them.
#: Committing raw responses is what would have made that checkable, and t18
#: should. The limit sits well above every measurement here and well below the
#: 0.71 salad, so it is a backstop
#: against pathological input rather than a judgement about density. A first
#: draft at 0.30 left the rollback fixture only 12 percent of headroom, which
#: would have made a genuine dense challenge a coin flip; that is recorded here
#: because the tempting fix — leaving it tight and calling it strictness — would
#: have cost real passes. Like every other gate it can only LOWER a pass rate.
MAX_ANCHOR_DENSITY = 0.45
#: Below this many content words a bigram fraction is not a measurement — two
#: bigrams sharing one is 0.5 and means nothing. Short replies skip the near-copy
#: gate and are judged by the move and targeting gates alone.
_MIN_TOKENS_FOR_RATIO = 4

_WORD_RE = re.compile(r"[a-z0-9]+")

#: A polarity-sensitive marker within two tokens after one of these is agreement
#: wearing challenge vocabulary ("no risk", "never an assumption worth asking").
_NEGATORS = frozenset(
    "no not never nor without little minimal negligible hardly barely nothing none t".split()
)

#: The markers negation actually *flips*: each asserts that a problem exists, so
#: "no risk" and "not an assumption" are endorsements.
#:
#: Everything else in :data:`MOVE_MARKERS` is exempt, and the exemption is
#: load-bearing rather than cautious. Half the assumption vocabulary is
#: *inherently* negative ("never states", "not established", "does not show") and
#: would filter itself; and a live response challenged a result with "it **hasn't
#: considered** the quality of the conversion", which a blanket negation rule
#: scored as agreement. Negating "consider" does not make a sentence agree —
#: negating "risk" does.
_POLARITY_SENSITIVE = frozenset(
    (
        "assum",
        "presuppos",
        "premise",
        "implicit",
        "rests on",
        "rest on",
        "relies on",
        "hinges on",
        "fails",
        "fail if",
        "breaks",
        "would be wrong",
        "wrong if",
        "counterexample",
        "worst case",
        "risk",
        "danger",
        "silently",
        "alternative",
    )
)

#: Explicit agreement with the conclusion. Agreement is not counsel, however
#: thoughtfully it is phrased.
AGREEMENT_MARKERS = (
    "i agree",
    "agreed",
    "i concur",
    "concur with",
    "the conclusion holds",
    "conclusion is right",
    "conclusion is correct",
    "this is right",
    "this is correct",
    "looks right",
    "looks good",
    "makes sense",
    "sound reasoning",
    "reasoning is sound",
    "ship it",
    "endorse",
    "no objection",
    "nothing wrong",
    "right call",
    "right trade",
)

#: A contrast connective means the agreement was partial — "I agree the TTL
#: should rise, BUT you are assuming…" is a challenge and must survive the
#: agreement gate. Only unqualified agreement is graded a restatement.
CONTRAST_MARKERS = (
    "but",
    "however",
    "although",
    "though",
    "yet",
    "that said",
    "on the other hand",
    "nevertheless",
    "nonetheless",
    "except",
    "unless",
    "before you",
    "caveat",
    "one concern",
    "my worry",
    "hold on",
)

#: Function words carry no evidence of restatement either way.
_STOPWORDS = frozenset("""
    the and for that this with was were are but not you your our its it's from have has had
    can could would should will shall may might must into onto than then there their they them
    what when which while who whom whose why how all any both each few more most other some
    such only own same too very just about above after again against because been before being
    below between during further here once over under until upon does did doing done
    """.split())


def word_tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _content_tokens(text: str) -> list[str]:
    return [t for t in word_tokens(text) if len(t) >= 3 and t not in _STOPWORDS]


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:]))


def phrase_positions(tokens: list[str], phrase: str) -> list[int]:
    """Every token index at which *phrase* (a sequence of stems) starts.

    Token-prefix matching, so a stem catches inflections without a substring
    match ever firing inside an unrelated word.
    """
    parts = _WORD_RE.findall(phrase.lower())
    if not parts:
        return []
    span = len(parts)
    return [
        start
        for start in range(len(tokens) - span + 1)
        if all(tokens[start + offset].startswith(parts[offset]) for offset in range(span))
    ]


def phrase_present(tokens: list[str], phrase: str) -> bool:
    """Whether *phrase* occurs in *tokens* at all, negated or not."""
    return bool(phrase_positions(tokens, phrase))


def _hits(tokens: list[str], phrases: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in phrases if phrase_present(tokens, phrase)]


def _unnegated_hits(tokens: list[str], phrases: tuple[str, ...]) -> list[str]:
    """Like :func:`_hits`, but a negated POLARITY-SENSITIVE occurrence is dropped.

    Adversarial review found the hole this closes: "there is no **risk** that
    anyone is served a stale number, and the **assumption** of stability is a
    safe one" is agreement, yet it fires two challenge markers and hits an
    anchor. Challenge vocabulary in the negative is agreement, and grading it as
    counsel is exactly the failure this harness exists to prevent.

    Only :data:`_POLARITY_SENSITIVE` phrases are filtered, and only when a
    negator sits in the two tokens before them. ``"t"`` is in the negator set
    because ``don't`` and ``doesn't`` tokenise to ``don``/``doesn`` plus ``t``.
    """
    found: list[str] = []
    for phrase in phrases:
        positions = phrase_positions(tokens, phrase)
        if not positions:
            continue
        if phrase not in _POLARITY_SENSITIVE:
            found.append(phrase)
            continue
        if any(not set(tokens[max(0, at - 2) : at]) & _NEGATORS for at in positions):
            found.append(phrase)
    return found


def anchor_leaks(case: CortexResult) -> list[str]:
    """Grading anchors that appear in the cortex text — every one is a bug.

    An anchor is only evidence of new material if the cortex never wrote it. A
    test asserts this is empty for every committed case, so the guarantee holds
    against future edits rather than against the author's memory.
    """
    tokens = word_tokens(case.prompt_text())
    leaked: list[str] = []
    for phrases in case.anchors().values():
        leaked.extend(phrase for phrase in phrases if phrase_present(tokens, phrase))
    return sorted(set(leaked))


def grade(response: str, case: CortexResult) -> dict[str, Any]:
    """Grade one muse response against *case*. See the module docstring.

    Pure, deterministic and reachable by nothing the mind under test can write.
    """
    tokens = word_tokens(response)
    response_content = _content_tokens(response)
    cortex_content = _content_tokens(case.prompt_text())

    response_bigrams = _bigrams(response_content)
    cortex_bigrams = _bigrams(cortex_content)
    shared = (
        len(response_bigrams & cortex_bigrams) / len(response_bigrams) if response_bigrams else 0.0
    )
    unique = set(response_content)
    novelty = len(unique - set(cortex_content)) / len(unique) if unique else 0.0

    moves = [move for move in MOVES if _unnegated_hits(tokens, MOVE_MARKERS[move])]
    # Anchors are NOT negation-filtered: "no invalidation path exists" is a real
    # challenge, and the anchor is the thing named, not the stance taken.
    anchors = {move: _hits(tokens, phrases) for move, phrases in case.anchors().items()}
    anchors_hit = sorted({phrase for found in anchors.values() for phrase in found})
    agreement = _hits(tokens, AGREEMENT_MARKERS)
    contrast = _hits(tokens, CONTRAST_MARKERS)

    verdict, reason = _verdict(
        response,
        shared,
        moves,
        anchors_hit,
        len(response_content),
        agreeing=bool(agreement) and not contrast,
    )
    return {
        "case": case.id,
        "verdict": verdict,
        "reason": reason,
        "passed": verdict == VERDICT_CHALLENGED,
        "moves": moves,
        "anchors_hit": anchors_hit,
        "anchors_by_move": {move: found for move, found in anchors.items() if found},
        "agreement_markers": agreement,
        "contrast_markers": contrast,
        "shared_bigram_fraction": round(shared, 3),
        "novel_word_fraction": round(novelty, 3),
        "response_words": len(response_content),
        # A regex cannot judge whether the challenge is CORRECT, and pretending
        # otherwise would be worse than saying so (the proof.py precedent).
        "challenge_soundness": "not machine-graded — read it",
    }


def _verdict(
    response: str,
    shared: float,
    moves: list[str],
    anchors_hit: list[str],
    words: int,
    *,
    agreeing: bool = False,
) -> tuple[str, str]:
    """The four gates, in order. Returns ``(verdict, reason)``."""
    if not response.strip():
        return VERDICT_SILENT, "the muse wrote nothing"
    if words >= _MIN_TOKENS_FOR_RATIO and shared > MAX_SHARED_BIGRAMS:
        return (
            VERDICT_RESTATED,
            f"near-copy: {shared:.2f} of its bigrams are the cortex's own "
            f"(limit {MAX_SHARED_BIGRAMS})",
        )
    if agreeing:
        return (
            VERDICT_RESTATED,
            "unqualified agreement: it endorses the conclusion and contradicts "
            "no part of it, whatever vocabulary it endorses it in",
        )
    if not moves:
        return (
            VERDICT_RESTATED,
            "no challenge move: it names no unstated premise, no failure "
            "condition and no alternative framing",
        )
    if not anchors_hit:
        return (
            VERDICT_UNTARGETED,
            f"a {'/'.join(moves)} move that lands on nothing this result rests "
            "on — empty scepticism, or a target the grader was not told about; read it",
        )
    density = len(anchors_hit) / words if words else 0.0
    if words and density > MAX_ANCHOR_DENSITY:
        return (
            VERDICT_UNARGUED,
            f"anchor-dense but argument-free: {len(anchors_hit)} anchors in "
            f"{words} content words ({density:.2f}/word, limit "
            f"{MAX_ANCHOR_DENSITY}) — a list of the grader's targets is not a "
            "challenge to them",
        )
    return VERDICT_CHALLENGED, f"{'/'.join(moves)} naming {', '.join(anchors_hit[:4])}"


# ── committed fixtures: the grader is verified against these, not against a run ─

#: A genuine challenge per case: names the unstated premise in challenge
#: language and proposes a different action. These must PASS.
CHALLENGE_FIXTURES: dict[str, str] = {
    "cache_ttl": (
        "Something in the argument is doing more work than it admits.\n"
        "GUIDANCE[durable]: The 0.4 percent figure is an average over an observation "
        "period you chose, and the case for shipping assumes that period was "
        "representative — that no repricing, promotion or discount fell inside it.\n"
        "GUIDANCE[durable]: It also turns a rate into a guarantee. How often the value "
        "differs is not how long a wrong price is served once it does; at 3600 seconds "
        "one repricing is served wrong for an hour and there is no invalidation path to "
        "cut it short. I would reframe this as a staleness budget rather than a hit "
        "rate: keep the TTL short and add event-driven invalidation on the write path."
    ),
    "ab_test": (
        "The number moved. Whether the thing worth moving moved is a separate question.\n"
        "GUIDANCE[durable]: This rests on signup conversion being a proxy for value, "
        "which the test never checks. If B converts more people by promising more, "
        "downstream retention and revenue can fall while this number rises.\n"
        "GUIDANCE[durable]: Five days is also a novelty window — a lift that is partly "
        "curiosity decays once the flow stops being new. Instead of shipping to 100 "
        "percent I would keep a holdout and read retention on the cohort before "
        "calling it."
    ),
    "rollback": (
        "The timeline is tidy, which is exactly when I get suspicious.\n"
        "GUIDANCE[durable]: The argument rests on an unstated premise — that a "
        "coincidence in time plus a successful rollback establishes causation, when it "
        "is only consistent with it. The rollback restarted every process and dropped "
        "every connection, so a leaked pool or a poisoned cache would produce the same "
        "signature.\n"
        "GUIDANCE[durable]: Nothing rules out a second change in the same minute — a "
        "config push, a feature flag, a dependency's own deploy — or a traffic spike "
        "arriving with it. I would keep the incident open and bisect the change log "
        "for 14:00 to 14:05 before closing it."
    ),
}

#: The trap: the cortex's own conclusion, re-worded. These must FAIL.
RESTATEMENT_FIXTURES: dict[str, str] = {
    "cache_ttl": (
        "GUIDANCE: I agree with the conclusion. The endpoint takes 4000 reads a minute "
        "and the backing query costs 380 ms, so at a 60 second TTL there are 60 "
        "recomputes an hour. Since the cached value differed from a freshly computed "
        "one on only 0.4 percent of recomputes across the last 7 days, the value is "
        "effectively static, and a 3600 second TTL therefore costs almost nothing in "
        "correctness while removing 59 recomputes an hour and their 380 ms miss "
        "penalty. Raising the TTL from 60 seconds to 3600 seconds is right."
    ),
    "ab_test": (
        "GUIDANCE: The conclusion holds. Variant B converted at 3.1 percent against "
        "variant A at 2.7 percent, across 41000 users in each arm over 5 days, which "
        "is a 15 percent relative lift at p = 0.011 and well past the 0.05 bar. The "
        "arms were randomised at the user level and the instrumentation matched on "
        "both sides, so shipping variant B to 100 percent of traffic is right."
    ),
    "rollback": (
        "GUIDANCE: Agreed. Errors on checkout went from 0.2 percent to 41 percent at "
        "14:03, a minute after the 14:02 deploy, and the deploy touched the checkout "
        "service, which is exactly where the errors are. The rollback at 14:31 "
        "returned the error rate to 0.2 percent within two minutes. Deploy in, errors "
        "up; deploy out, errors down. The deploy caused the outage, so roll back and "
        "close the incident."
    ),
}

#: The second trap: a restatement with challenge vocabulary bolted on. The gates
#: are ordered so this lands on the near-copy gate, not on the move gate.
BOLTED_ON_FIXTURES: dict[str, str] = {
    case_id: text + " This does rest on an assumption, but it is a safe one."
    for case_id, text in RESTATEMENT_FIXTURES.items()
}

#: The fourth trap, and the one that actually broke a version of this grader:
#: **agreement wearing challenge vocabulary.** Heavily re-worded (so the
#: near-copy gate does not fire), endorsing the conclusion, and using challenge
#: markers in the negative — "there is no *risk*", "the *assumption* is a safe
#: one" — while naming an anchor in passing. Found by adversarial review, not by
#: a live run. Must FAIL.
AGREEING_FIXTURES: dict[str, str] = {
    "cache_ttl": (
        "GUIDANCE: I agree with this. The pricing figure barely moves, so there is no "
        "risk that anyone is served a stale number, and the assumption of stability "
        "is a safe one. Cutting 59 needless refreshes an hour is clearly the right "
        "trade."
    ),
    "ab_test": (
        "GUIDANCE: Agreed — ship it. The lift is real, and the danger of a downstream "
        "retention effect is negligible at this sample size, so the assumption that a "
        "signup is a signup holds fine here."
    ),
    "rollback": (
        "GUIDANCE: I concur with closing it. The assumption that the deploy is the "
        "cause is well supported, nothing points to a config change or a dependency "
        "failure, and the correlation is strong enough that no further risk remains."
    ),
}

#: The third trap: fluent contrarianism aimed at nothing. Must not pass.
GENERIC_FIXTURE = (
    "GUIDANCE[durable]: Are you sure? I would push back here. Every conclusion rests "
    "on assumptions that have not been examined, and there is usually an alternative "
    "framing worth considering. What would have to be true for this to be wrong? "
    "Consider whether a different approach might serve better before committing."
)


# ── the scripted mind (default suite) and the live seam ───────────────────────


def scripted_muse(*, mode: str = "challenge") -> Any:
    """A hermetic mind that is a function of its prompt, not a fixture.

    It answers only when it can see the case's own conclusion in the messages it
    was handed, so an end-to-end run that stops feeding the boundary through
    fails rather than passing on a canned reply.
    """
    library = CHALLENGE_FIXTURES if mode == "challenge" else RESTATEMENT_FIXTURES

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        text = "\n".join(str(message.get("content", "")) for message in messages)
        for case in CASES:
            if case.conclusion in text:
                return ModelResponse(content=f"{library[case.id]}\n[done]")
        return ModelResponse(content="[done]")

    return complete


def gateway(
    base_url: str,
    model: str,
    key: str,
    *,
    temperature: float,
    max_tokens: int = DEFAULT_MAX_TOKENS,
):
    """One tools-off completion against the lobes gateway. Never sees a tool."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:  # nosec B310
            payload = json.load(response)
        message = payload["choices"][0]["message"]
        usage = payload.get("usage") or {}
        return ModelResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning_content") or message.get("reasoning") or "",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    return complete


# ── one run, and a series ─────────────────────────────────────────────────────


@dataclass
class RunReport:
    """One boundary's worth of counsel, graded."""

    case: str
    framing: str = FRAMING_TASK
    grade: dict[str, Any] = field(default_factory=dict)
    response: str = ""
    kinds: list[str] = field(default_factory=list)
    durable: int = 0
    exit_reason: str = ""
    turns: int = 0
    tokens: Optional[int] = None
    degradations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framing": self.framing,
            "grade": self.grade,
            "response": self.response,
            "kinds": self.kinds,
            "durable_insights": self.durable,
            "exit_reason": self.exit_reason,
            "turns": self.turns,
            "tokens": self.tokens,
            "degradations": self.degradations,
        }


def run_case(
    case: CortexResult,
    complete: Any,
    *,
    identity: Optional[str] = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    framing: str = FRAMING_TASK,
) -> RunReport:
    """Hand *case* to the muse once and grade everything it wrote."""
    outcome = build_loop(complete, identity=identity, max_turns=max_turns, framing=framing).think(
        boundary_for(case)
    )
    # Both halves: GUIDANCE lines land on `guidance`, narration on `text`.
    response = "\n".join(
        part for insight in outcome.insights for part in (insight.text, insight.guidance) if part
    )
    kinds = [insight.kind for insight in outcome.insights]
    return RunReport(
        case=case.id,
        framing=framing,
        grade=grade(response, case),
        response=response,
        kinds=kinds,
        durable=sum(1 for kind in kinds if kind == COUNSEL_KIND_DURABLE),
        exit_reason=outcome.exit_reason,
        turns=outcome.turns,
        tokens=outcome.tokens,
        degradations=[record.to_dict() for record in outcome.degradations],
    )


def summarise(reports: list[RunReport]) -> dict[str, Any]:
    """The series roll-up. A negative is a result and is reported as one."""
    counts = {verdict: 0 for verdict in VERDICTS}
    for report in reports:
        counts[report.grade.get("verdict", VERDICT_SILENT)] += 1
    kinds: dict[str, int] = {}
    for report in reports:
        for kind in report.kinds:
            kinds[kind] = kinds.get(kind, 0) + 1
    total = len(reports) or 1
    return {
        "runs": len(reports),
        "framings": sorted({report.framing for report in reports}),
        "verdicts": counts,
        "challenged": counts[VERDICT_CHALLENGED],
        "challenged_fraction": round(counts[VERDICT_CHALLENGED] / total, 3),
        # Self-labelled counsel kind (task t2). Reported rather than asserted: a
        # challenge outlives the step it was written at, so `step` here is a
        # finding about the label, not about the counsel.
        "counsel_kinds": kinds,
        "durable_insights": sum(report.durable for report in reports),
        "degradations": sum(len(report.degradations) for report in reports),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--live", action="store_true", help="dial the real muse")
    parser.add_argument(
        "--scripted",
        choices=("challenge", "restatement"),
        default="challenge",
        help="which hermetic mind to run when --live is absent",
    )
    parser.add_argument("--case", action="append", choices=sorted(CASES_BY_ID), default=None)
    parser.add_argument(
        "--framing",
        choices=sorted(FRAMINGS),
        default=FRAMING_TASK,
        help="task: ask for a challenge. bare: MUSE_AUTHORITY alone (the control arm)",
    )
    parser.add_argument("--n", type=int, default=1, help="repeats per case")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument("--muse-temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="completion budget per thinking turn; raise it when the mind under test thinks",
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--identity", default=None)
    parser.add_argument(
        "--results",
        default="results/muse_challenge_config.json",
        help="where the config preamble is written, BEFORE the first result line",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cases = [CASES_BY_ID[name] for name in (args.case or sorted(CASES_BY_ID))]

    key = ""
    if args.live:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            print(f"error: {API_KEY_ENV} is not set", file=sys.stderr)
            print("hint: export it, or drop --live to run the scripted mind", file=sys.stderr)
            return 2

    # Written BEFORE the first result line — the whole point of the preamble.
    Path(args.results).expanduser().parent.mkdir(parents=True, exist_ok=True)
    config = write_config_preamble(
        str(Path(args.results).expanduser()),
        cortex_model="committed fixtures (examples/muse_challenge.py CASES)",
        cortex_temperature=0.0,
        muse_model=args.muse_model if args.live else f"scripted:{args.scripted}",
        muse_temperature=args.muse_temperature if args.live else None,
        max_turns=args.max_turns,
        staleness_policy="none — a single boundary, nothing ages",
        n=args.n * len(cases),
        extra={
            "framing": args.framing,
            "identity": args.identity,
            "cases": [case.id for case in cases],
            "max_shared_bigrams": MAX_SHARED_BIGRAMS,
            "max_anchor_density": MAX_ANCHOR_DENSITY,
            "max_tokens": args.max_tokens,
            "criterion": CRITERION,
        },
    )

    complete = (
        gateway(
            args.base_url,
            args.muse_model,
            key,
            temperature=args.muse_temperature,
            max_tokens=args.max_tokens,
        )
        if args.live
        else scripted_muse(mode=args.scripted)
    )

    reports = [
        run_case(
            case,
            complete,
            identity=args.identity,
            max_turns=args.max_turns,
            framing=args.framing,
        )
        for _ in range(max(1, args.n))
        for case in cases
    ]
    summary = summarise(reports)

    if args.json:
        print(
            json.dumps(
                {
                    "config": config,
                    "criterion": CRITERION,
                    "runs": [report.to_dict() for report in reports],
                    "summary": summary,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    mind = "live" if args.live else "scripted:" + args.scripted
    print("=" * 78)
    print(f"MUSE-CHALLENGES-CORTEX — {mind}, framing={args.framing}")
    print("=" * 78)
    print(f"criterion: {CRITERION}")
    print("-" * 78)
    for report in reports:
        verdict = report.grade["verdict"]
        print(f"{report.case:12s} {verdict:12s} {report.grade['reason']}")
        print(f"{'':12s} kinds={report.kinds} turns={report.turns} exit={report.exit_reason}")
    print("-" * 78)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
