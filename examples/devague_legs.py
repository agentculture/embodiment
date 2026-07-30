"""The devague-legs live experiment — Experiment 2, the ``/deviate`` slice.

Contract: ``docs/live-test-results/devague-legs-preregistration.md``, committed
before the first dial (plan task t6, issue #20). This module is task t9's
harness. It executes the pre-registered protocol; it does not re-open it.

**What this harness runs.** Experiment 2 only: the cheapest-first ``/deviate``
slice. Twelve cases — six positive (real, already-approved deviation records
from ``.devague/deliveries/``) and six negative (delivered tasks no deviation
touches) — judged independently and blind by both minds. 24 completions.

**What this harness deliberately does NOT run**, stated here rather than left
to inference:

- Experiment 1 (the same-mind control on ``/think`` + ``/spec-to-plan``) is a
  separate, much larger lane. If it did not run, the results document reports
  it ABSENT in its own right. This module never fabricates a stand-in for it.
- **No state-mutating ``devague`` command is issued anywhere**, by operator
  instruction. The pre-registration's "Reproducing" section sketches the
  capture as ``devague deviate "<judgment>" --origin llm``; writing twelve
  synthetic judgments into this repo's real deviation ledger would corrupt the
  very ground truth the case pool is drawn from, so the judgments are captured
  into a committed JSONL artifact carrying the same ``origin``/``status``
  fields instead. That is a weaker exercise of devague's own confirm gate than
  the pre-registration describes, it is named as such in the results, and the
  gate's contract is evidenced statically instead (see
  :func:`static_confirm_gate_evidence`). Only read-only introspection
  (``devague --version``, ``devague deviate --list --json``) is issued live.

**The non-negotiable structural claim.** Every model-authored judgment is
captured ``origin="llm"`` and lands ``status="proposed"``; zero become
confirmed without a recorded human action. :func:`assert_no_confirms` fails the
run outright on any violation, and the repo's real ledgers are hashed before
and after so "no confirms happened" is provable by digest, not by assertion.

Usage::

    COLLEAGUE_API_KEY=... uv run python examples/devague_legs.py \
        --out docs/live-test-results/devague-legs-deviate.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - the grader is the external devague CLI, by design (task t7)
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.challenge_config import write_config_preamble  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── the pre-registered constants ─────────────────────────────────────────────
# Copies, not imports: ``tests/test_devague_legs_preregistration.py`` states the
# contract ("it either imports these names directly, or defines its own copies
# and asserts them equal to these in the same diff"). The equality assertion
# lives in ``tests/test_devague_legs_harness.py``, so moving a number here is a
# failing test, not a quiet edit.

PINNED_DEVAGUE_VERSION = "0.22.0"
CHEAPEST_FIRST_LEG = "deviate"
LLM_ORIGIN_STATUS = "proposed"
HUMAN_CONFIRM_STATUS = "confirmed"

DEVIATE_POSITIVE_CASES = (
    "gwen-loop-presence-continuity:d1",
    "gwen-loop-presence-continuity:d2",
    "gwen-loop-presence-continuity:d3",
    "gwen-loop-presence-continuity:d4",
    "gwen-loop-presence-continuity:d5",
    "function-first-loops-muse-redesign:d1",
)
DEVIATE_NEGATIVE_CASES = (
    "gwen-loop-presence-continuity:t1",
    "function-first-loops-muse-redesign:t1",
    "gwen-loop-presence-continuity:t3",
    "function-first-loops-muse-redesign:t2",
    "gwen-loop-presence-continuity:t5",
    "function-first-loops-muse-redesign:t3",
)

#: The negative pool's task titles, quoted verbatim from the pre-registration's
#: own case table. The *delivery text* is read live from the committed delivery
#: document — only the title is a literal here, and it is the contract's own.
NEGATIVE_CASE_TITLES = {
    "gwen-loop-presence-continuity:t1": "Carve the data contract into `embodiment/contract.py`",
    "function-first-loops-muse-redesign:t1": "Reframe the muse prompt",
    "gwen-loop-presence-continuity:t3": "Port context windowing and media handling",
    "function-first-loops-muse-redesign:t2": "Counsel-kind self-labelling",
    "gwen-loop-presence-continuity:t5": "No-shell host fixture",
    "function-first-loops-muse-redesign:t3": "Kind-aware delivery in the runner",
}

#: devague 0.22.0's classification vocabulary for a deviation record.
CLASSIFICATIONS = ("acceptable", "risky", "needs-follow-up")

# ── rig settings ─────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
#: One temperature for both minds. An A/B in which the arms run at different
#: temperatures measures the temperature.
DEFAULT_TEMPERATURE = 0.3
#: The cortex spends ~1000 tokens reasoning before it emits anything usable; a
#: small budget returns an empty body with ``finish_reason=length``, which is
#: indistinguishable from model failure. Measured on this rig 2026-07-30.
CORTEX_MAX_TOKENS = 4000
MUSE_MAX_TOKENS = 2000
REQUEST_TIMEOUT_S = 600.0

MIND_CORTEX = "cortex"
MIND_MUSE = "muse"
MINDS = (MIND_CORTEX, MIND_MUSE)

# ── the delivery ledgers the case pool is grounded in ────────────────────────

DELIVERY_LEDGERS = {
    "gwen-loop-presence-continuity": REPO_ROOT
    / ".devague/deliveries/gwen-loop-presence-continuity.json",
    "function-first-loops-muse-redesign": REPO_ROOT
    / ".devague/deliveries/function-first-loops-muse-redesign.json",
}
DELIVERY_DOCS = {
    "gwen-loop-presence-continuity": REPO_ROOT
    / "docs/deliveries/2026-07-25-gwen-loop-presence-continuity.md",
    "function-first-loops-muse-redesign": REPO_ROOT
    / "docs/deliveries/2026-07-25-function-first-loops-muse-redesign.md",
}


# ── run configuration ────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> str:
    """Run a read-only command from the repo root and return stdout."""
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell, operator's own tools
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def devague_version() -> str:
    """The graded instrument's own report of itself. Recorded, never assumed."""
    return _run(["devague", "--version"])


def _tracked_at(commit: str, path: str) -> str | None:
    """Return ``path``'s contents at ``commit``, or ``None`` if absent there.

    Reads the commit rather than the working tree, so the evidence a run
    recorded stays checkable after the seam it predates has merged.
    """
    proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, git off PATH
        ["git", "show", f"{commit}:{path}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def base_commit_record(commit: str = "HEAD") -> dict[str, Any]:
    """The base commit plus the evidence that it predates the muse tool seam.

    The pre-registration requires runs to execute against a commit predating
    the muse tool-seam merge. That seam is this cycle's plan task t13
    (``headspace-cli`` as a lazily-imported dependency plus the workspace
    tool). Rather than assert the claim, this records three checkable facts
    that jointly establish it, and the assertion fails loudly if any is false.

    The evidence is read **at** ``commit``, not from the working tree. A live
    run passes the default and records HEAD; a later reader passes the commit
    a run recorded and re-derives the same verdict. Checking the working tree
    instead would make the claim unverifiable the moment the seam merged —
    the recorded evidence would describe a checkout that no longer exists.
    """
    head = _run(["git", "rev-parse", commit])
    subject = _run(["git", "log", "-1", "--pretty=%s", head])
    headspace_module = _tracked_at(head, "embodiment/headspace.py") is not None
    muse_tools_off = "No tool schema is ever passed" in (
        _tracked_at(head, "embodiment/muse.py") or ""
    )
    predates = (not headspace_module) and muse_tools_off
    return {
        "base_commit": head,
        "base_commit_short": head[:7],
        "base_commit_subject": subject,
        "predates_muse_tool_seam": predates,
        "tool_seam_task": "t13 (headspace-cli dependency + workspace tool)",
        "evidence": {
            "embodiment/headspace.py exists": headspace_module,
            "muse.py still declares the tools-off invariant": muse_tools_off,
        },
    }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_digests() -> dict[str, str]:
    """sha256 of every committed deviation ledger this experiment reads."""
    return {plan: sha256_of(path) for plan, path in sorted(DELIVERY_LEDGERS.items())}


def confirm_log(plan: str) -> dict[str, Any]:
    """The live confirm log for *plan*, read through devague's own read-only verb.

    ``devague deviate --list --json`` is introspection: it is exactly the
    externally-authored view of who confirmed what, and issuing it mutates
    nothing.
    """
    return json.loads(_run(["devague", "deviate", "--list", "--plan", plan, "--json"]))


def static_confirm_gate_evidence() -> dict[str, Any]:
    """Static evidence that ``--origin llm`` lands ``proposed`` in devague 0.22.0.

    The live capture step is not issued (see the module docstring), so the
    gate's contract cannot be demonstrated by exercising it in this run. It is
    instead read out of the installed instrument's own source, and reported as
    static evidence — never as a live exercise.
    """
    marker = 'status = "proposed" if origin == "llm" else "approved"'
    found: list[str] = []
    for root in (Path(sys.prefix), Path.home() / ".local/share/uv/tools/devague"):
        for path in root.rglob("devague/delivery.py"):
            try:
                if marker in path.read_text(encoding="utf-8"):
                    found.append(str(path))
            except OSError:  # pragma: no cover - unreadable install
                continue
    return {
        "marker": marker,
        "found_in": sorted(set(found)),
        "verified": bool(found),
        "note": (
            "devague's deviation ledger spells a human-approved record 'approved', "
            "not 'confirmed'; the pre-registration's HUMAN_CONFIRM_STATUS='confirmed' "
            "is the FRAME claim vocabulary, not the deviation ledger's."
        ),
    }


# ── the case pool ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """One blind judgment case."""

    case_id: str
    plan: str
    item: str
    pool: str  # "positive" | "negative"
    ground_truth_warrants: bool
    text: str

    @property
    def char_len(self) -> int:
        return len(self.text)


_ROW_RE_CACHE: dict[str, dict[str, str]] = {}


def _delivery_rows(plan: str) -> dict[str, str]:
    """Parse a delivery document's Actual Delivery table into ``{task: text}``.

    Scoped to the ``## Actual Delivery`` section deliberately. These documents
    carry later tables — a Drift section keyed by the same task refs — and an
    unscoped scan silently overwrites the delivery row with a drift row. That
    happened once here, caught in a dry run before any dial: it handed the
    ``function-first-loops-muse-redesign:t3`` case a drift entry that even
    leaked a ``needs-follow-up`` classification token into a blind prompt.
    """
    if plan in _ROW_RE_CACHE:
        return _ROW_RE_CACHE[plan]
    rows: dict[str, str] = {}
    pattern = re.compile(r"^\|\s*\*{0,2}`(t\d+)`\*{0,2}\s*\|([^|]*)\|(.*)\|\s*$")
    in_section = False
    for line in DELIVERY_DOCS[plan].read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## Actual Delivery"
            continue
        if not in_section:
            continue
        match = pattern.match(line)
        if match and match.group(1) not in rows:
            status = match.group(2).strip().strip("*` ")
            rows[match.group(1)] = f"Status: {status}. {match.group(3).strip()}"
    _ROW_RE_CACHE[plan] = rows
    return rows


def build_cases() -> list[Case]:
    """The twelve pre-registered cases, read out of committed history.

    Redaction, stated exactly. The protocol says a positive case is presented
    as "the deviation's own ``reason`` text with the ``id``/``status``/
    ``classification`` fields redacted", and it also says "Neither mind is told
    which pool a case is drawn from". Those two requirements interact: the
    positive pool carries ``origin`` and ``affects`` fields the negative pool
    has no counterpart for, so leaving them in would be a structural tell that
    defeats the blinding the same paragraph requires. This harness therefore
    redacts a **superset** of the three named fields — id, status,
    classification, origin and affects — and presents only free prose plus the
    plan slug and item ref, which both pools have. Nothing is added.
    """
    cases: list[Case] = []
    for case_id in DEVIATE_POSITIVE_CASES:
        plan, dev_id = case_id.split(":")
        ledger = json.loads(DELIVERY_LEDGERS[plan].read_text(encoding="utf-8"))
        record = next(d for d in ledger["deviations"] if d["id"] == dev_id)
        text = f"{record['what']}\n\n{record['reason']}"
        cases.append(
            Case(
                case_id=case_id,
                plan=plan,
                item=record.get("task_ref") or "",
                pool="positive",
                ground_truth_warrants=True,
                text=text,
            )
        )
    for case_id in DEVIATE_NEGATIVE_CASES:
        plan, task_ref = case_id.split(":")
        row = _delivery_rows(plan)[task_ref]
        title = NEGATIVE_CASE_TITLES[case_id]
        cases.append(
            Case(
                case_id=case_id,
                plan=plan,
                item=task_ref,
                pool="negative",
                ground_truth_warrants=False,
                text=f"{title}\n\n{row}",
            )
        )
    return cases


# ── the prompt (identical for both minds) ────────────────────────────────────

SYSTEM_PROMPT = (
    "You review execution records from a software project planned with the "
    "`devague` tool.\n\n"
    "In that method, a confirmed plan is a contract. When execution diverges "
    "from it, the divergence must be recorded as a first-class `devague "
    "deviate` record — an append-only ledger entry with a risk "
    "classification — BEFORE work resumes. It must never be folded silently "
    "into drift after the fact.\n\n"
    "Not every notable event warrants one. Work that proceeded within the "
    "confirmed plan does not, even when it was substantial, imperfect, or "
    "worth remarking on. A deviation record is for a divergence from the "
    "plan, not for a progress report.\n\n"
    "Judge one record at a time. Answer with a single JSON object and nothing "
    "else."
)

USER_TEMPLATE = (
    "Plan: {plan}\n"
    "Plan item: {item}\n\n"
    "Record:\n"
    '"""\n'
    "{text}\n"
    '"""\n\n'
    "Does this record describe something that warrants filing a `devague "
    "deviate` record? If so, under which classification?\n\n"
    "Reply with ONLY this JSON object, no prose, no code fence:\n"
    '{{"warrants_deviation": true or false, "classification": "acceptable" or '
    '"risky" or "needs-follow-up" or null, "reason": "one or two sentences"}}'
)


def build_prompt(case: Case) -> tuple[str, str]:
    return SYSTEM_PROMPT, USER_TEMPLATE.format(plan=case.plan, item=case.item, text=case.text)


# ── the model seam ───────────────────────────────────────────────────────────


def complete(
    base_url: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """One tools-off completion. Returns the raw pieces, degrading never raising.

    A transport failure is data: it comes back as ``degraded`` with the error
    text, and the caller records it. Nothing is silently retried for a better
    number.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # From the environment, and never echoed into an artifact.
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.time()
    try:
        # Scheme pinned to http(s) above; the endpoint is the operator's own.
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "raw_content": "",
            "raw_reasoning": "",
            "finish_reason": None,
            "usage": None,
            "latency_s": round(time.time() - started, 2),
            "degraded": f"transport-error: {type(exc).__name__}: {exc}",
        }
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "raw_content": message.get("content") or "",
        "raw_reasoning": message.get("reasoning") or message.get("reasoning_content") or "",
        "finish_reason": choice.get("finish_reason"),
        "usage": payload.get("usage"),
        "latency_s": round(time.time() - started, 2),
        "degraded": None,
    }


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judgment(content: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Extract the judgment object from *content*.

    Returns ``(parsed, degradation)``. Tolerates a ```json fence and leading
    prose; refuses to guess when ``warrants_deviation`` is absent or is not a
    bool — a guessed verdict is a fabricated data point.
    """
    if not content.strip():
        return None, "empty-content"
    match = _JSON_OBJ_RE.search(content)
    if not match:
        return None, "no-json-object"
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return None, f"unparseable-json: {exc}"
    if not isinstance(obj, dict):
        return None, "json-not-an-object"
    verdict = obj.get("warrants_deviation")
    if not isinstance(verdict, bool):
        return None, f"warrants_deviation-not-a-bool: {verdict!r}"
    classification = obj.get("classification")
    if classification is not None and classification not in CLASSIFICATIONS:
        classification = f"off-vocabulary:{classification}"
    return (
        {
            "warrants_deviation": verdict,
            "classification": classification,
            "reason": str(obj.get("reason") or ""),
        },
        None,
    )


# ── the judgment record ──────────────────────────────────────────────────────


def judge(case: Case, mind: str, model: str, base_url: str, api_key: str) -> dict[str, Any]:
    """One mind, one case, one completion. Every byte of prompt and response kept."""
    system, user = build_prompt(case)
    max_tokens = CORTEX_MAX_TOKENS if mind == MIND_CORTEX else MUSE_MAX_TOKENS
    result = complete(base_url, model, api_key, system, user, max_tokens=max_tokens)
    parsed, parse_degraded = (None, result["degraded"])
    if result["degraded"] is None:
        parsed, parse_degraded = parse_judgment(result["raw_content"])
    return {
        "kind": "judgment",
        "case_id": case.case_id,
        "plan": case.plan,
        "item": case.item,
        "pool": case.pool,
        "ground_truth_warrants": case.ground_truth_warrants,
        "case_char_len": case.char_len,
        "mind": mind,
        "model": model,
        # The structural claim: model-authored, proposed, never confirmed.
        "origin": "llm",
        "status": LLM_ORIGIN_STATUS,
        "confirmed_by": None,
        "prompt_system": system,
        "prompt_user": user,
        "raw_content": result["raw_content"],
        "raw_reasoning": result["raw_reasoning"],
        "finish_reason": result["finish_reason"],
        "usage": result["usage"],
        "latency_s": result["latency_s"],
        "parsed": parsed,
        "degraded": parse_degraded,
        "retries": 0,
    }


# ── the non-negotiable assertion ─────────────────────────────────────────────


def assert_no_confirms(
    records: list[dict[str, Any]],
    digests_before: dict[str, str],
    digests_after: dict[str, str],
    logs_before: dict[str, Any],
    logs_after: dict[str, Any],
) -> dict[str, Any]:
    """Fail the run outright on any confirm not attributable to a human.

    Three independent checks, because one assertion about our own records
    would only prove our own bookkeeping:

    1. every captured judgment is ``origin=llm``, ``status=proposed``,
       ``confirmed_by=None``;
    2. the committed deviation ledgers are byte-identical before and after —
       nothing was written to them at all;
    3. devague's own ``deviate --list --json`` confirm log is identical before
       and after, and every ``approved`` record in it is ``origin=user`` or was
       already approved before this run began.
    """
    violations: list[str] = []
    for rec in records:
        if rec.get("origin") != "llm":
            violations.append(f"{rec['case_id']}/{rec['mind']}: origin={rec.get('origin')!r}")
        if rec.get("status") != LLM_ORIGIN_STATUS:
            violations.append(f"{rec['case_id']}/{rec['mind']}: status={rec.get('status')!r}")
        if rec.get("confirmed_by") is not None:
            violations.append(f"{rec['case_id']}/{rec['mind']}: confirmed_by is set")
    for plan, before in digests_before.items():
        if digests_after.get(plan) != before:
            violations.append(f"{plan}: deviation ledger changed during the run")
    for plan, before in logs_before.items():
        if logs_after.get(plan) != before:
            violations.append(f"{plan}: devague confirm log changed during the run")

    pre_existing = {
        plan: [
            {"id": d["id"], "origin": d.get("origin"), "status": d.get("status")}
            for d in log.get("deviations", [])
        ]
        for plan, log in logs_after.items()
    }
    llm_approved = [
        f"{plan}:{d['id']}"
        for plan, entries in pre_existing.items()
        for d in entries
        if d["status"] == "approved" and d["origin"] == "llm"
    ]
    return {
        "captured_judgments": len(records),
        "confirms_not_attributable_to_a_human": 0 if not violations else len(violations),
        "violations": violations,
        "ledger_digests_before": digests_before,
        "ledger_digests_after": digests_after,
        "ledgers_unchanged": digests_before == digests_after,
        "confirm_logs_unchanged": logs_before == logs_after,
        "pre_existing_llm_origin_approved": llm_approved,
        "pre_existing_llm_origin_approved_note": (
            "These predate this run entirely and each carries a recorded human "
            "--confirm in git history; this run added none."
        ),
        "passes": not violations,
    }


# ── scoring ──────────────────────────────────────────────────────────────────


def concordance(records: list[dict[str, Any]], mind: str) -> dict[str, Any]:
    """Hits / misses / false alarms for one mind. Exploratory data, not a verdict."""
    mine = [r for r in records if r["mind"] == mind]
    scored = [r for r in mine if r["parsed"] is not None]
    hits = sum(1 for r in scored if r["pool"] == "positive" and r["parsed"]["warrants_deviation"])
    misses = sum(
        1 for r in scored if r["pool"] == "positive" and not r["parsed"]["warrants_deviation"]
    )
    false_alarms = sum(
        1 for r in scored if r["pool"] == "negative" and r["parsed"]["warrants_deviation"]
    )
    correct_rejections = sum(
        1 for r in scored if r["pool"] == "negative" and not r["parsed"]["warrants_deviation"]
    )
    degraded = [f"{r['case_id']}: {r['degraded']}" for r in mine if r["parsed"] is None]
    return {
        "mind": mind,
        "n_cases": len(mine),
        "n_scored": len(scored),
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_rejections": correct_rejections,
        "accuracy": round((hits + correct_rejections) / len(scored), 3) if scored else None,
        "degraded": degraded,
        "mean_latency_s": (
            round(sum(r["latency_s"] for r in mine) / len(mine), 1) if mine else None
        ),
    }


def length_confound(cases: list[Case]) -> dict[str, Any]:
    """Report the input-length gap between pools, because it is a real confound.

    The pre-registration fixed both inputs before any dial, and this harness
    does not change them. But a positive case is a two-paragraph rationale and
    a negative case is a one-line delivery row, so a mind could score well by
    reading length alone. Measuring the gap is honest reporting; changing the
    inputs after seeing it would not be.
    """
    pos = [c.char_len for c in cases if c.pool == "positive"]
    neg = [c.char_len for c in cases if c.pool == "negative"]
    return {
        "positive_chars": {"min": min(pos), "max": max(pos), "mean": round(sum(pos) / len(pos))},
        "negative_chars": {"min": min(neg), "max": max(neg), "mean": round(sum(neg) / len(neg))},
        "separable_by_length_alone": min(pos) > max(neg),
    }


# ── re-analysis ──────────────────────────────────────────────────────────────


def load_records(path: Path) -> list[dict[str, Any]]:
    """Re-read the committed transcript. Every judgment, raw."""
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            obj = json.loads(line)
            if obj.get("kind") == "judgment":
                records.append(obj)
    return records


def ledger_classification(case_id: str) -> Optional[str]:
    """The human-recorded classification for a positive case, from the ledger."""
    plan, dev_id = case_id.split(":")
    if not dev_id.startswith("d") or plan not in DELIVERY_LEDGERS:
        return None
    ledger = json.loads(DELIVERY_LEDGERS[plan].read_text(encoding="utf-8"))
    record = next((d for d in ledger["deviations"] if d["id"] == dev_id), None)
    return record.get("classification") if record else None


def classification_agreement(records: list[dict[str, Any]], mind: str) -> dict[str, Any]:
    """Post-hoc, NOT pre-registered: does the classification match the ledger's?

    The pre-registration asks for hits/misses/false alarms and nothing else.
    This is a descriptive readout noticed while the run was in flight, labelled
    as post-hoc wherever it is published so it is never read as a
    pre-registered result. It is computable purely from the committed
    transcript, so it required no extra dial and no re-run.
    """
    agree = 0
    total = 0
    detail: list[str] = []
    for rec in records:
        if rec["mind"] != mind or rec["pool"] != "positive" or rec["parsed"] is None:
            continue
        recorded = ledger_classification(rec["case_id"])
        said = rec["parsed"]["classification"]
        total += 1
        if said == recorded:
            agree += 1
        detail.append(f"{rec['case_id']}: said={said!r} ledger={recorded!r}")
    return {"mind": mind, "agreed": agree, "of": total, "detail": detail}


def analyse(path: Path) -> str:
    """Render the results tables from the committed transcript, not by hand.

    Every table in the results document is produced here and pasted, so a
    number in the prose is a number the transcript contains. Three of this
    repo's own corrections were transcription or interpretation errors made
    between a run and its write-up; this closes that gap.
    """
    records = load_records(path)
    lines: list[str] = []
    lines.append("| case | pool | truth | cortex | muse | chars |")
    lines.append("|---|---|---|---|---|---|")
    by_case: dict[str, dict[str, Any]] = {}
    for rec in records:
        by_case.setdefault(rec["case_id"], {})[rec["mind"]] = rec
    for case_id, minds in by_case.items():
        any_rec = next(iter(minds.values()))
        said = {}
        for mind in MINDS:
            rec = minds.get(mind)
            if rec is None:
                said[mind] = "ABSENT"
            elif rec["parsed"] is None:
                said[mind] = f"DEGRADED ({rec['degraded']})"
            else:
                said[mind] = str(rec["parsed"]["warrants_deviation"])
        lines.append(
            f"| `{case_id}` | {any_rec['pool']} | {any_rec['ground_truth_warrants']} "
            f"| {said[MIND_CORTEX]} | {said[MIND_MUSE]} | {any_rec['case_char_len']} |"
        )
    lines.append("")
    lines.append("| mind | hits /6 | misses | false alarms | correct rejections | accuracy |")
    lines.append("|---|---|---|---|---|---|")
    for mind in MINDS:
        score = concordance(records, mind)
        lines.append(
            f"| {mind} | {score['hits']} | {score['misses']} | {score['false_alarms']} "
            f"| {score['correct_rejections']} | {score['accuracy']} |"
        )
    lines.append("")
    for mind in MINDS:
        score = concordance(records, mind)
        lines.append(
            f"- {mind}: n_scored={score['n_scored']}/{score['n_cases']}, "
            f"mean latency {score['mean_latency_s']}s, degraded={score['degraded']}"
        )
    lines.append("")
    lines.append("POST-HOC (not pre-registered) — classification vs the ledger's own:")
    for mind in MINDS:
        agreement = classification_agreement(records, mind)
        lines.append(f"- {mind}: {agreement['agreed']}/{agreement['of']}")
        for row in agreement["detail"]:
            lines.append(f"  - {row}")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    parser.add_argument("--muse-model", default=DEFAULT_MUSE)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs/live-test-results/devague-legs-deviate.jsonl"),
        help="JSONL transcript path; the config lands beside it as *-config.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build cases, dial nothing.")
    parser.add_argument(
        "--analyse",
        action="store_true",
        help="Re-render the results tables from an existing --out transcript.",
    )
    args = parser.parse_args(argv)

    if args.analyse:
        print(analyse(Path(args.out)))
        return 0

    api_key = os.environ.get("COLLEAGUE_API_KEY", "")
    if not api_key and not args.dry_run:
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        print("hint: the gateway requires Authorization: Bearer <key>", file=sys.stderr)
        return 2

    version = devague_version()
    commit = base_commit_record()
    cases = build_cases()

    out = Path(args.out)
    config_path = out.with_name(out.stem + "-config.json")
    config = write_config_preamble(
        str(config_path),
        cortex_model=args.cortex_model,
        cortex_temperature=DEFAULT_TEMPERATURE,
        muse_model=args.muse_model,
        muse_temperature=DEFAULT_TEMPERATURE,
        max_turns=1,
        staleness_policy="n/a-single-completion",
        n=len(cases),
        extra={
            "experiment": "devague-legs Experiment 2 (the /deviate cheapest-first slice)",
            "leg": CHEAPEST_FIRST_LEG,
            "preregistration": "docs/live-test-results/devague-legs-preregistration.md",
            "devague_version_output": version,
            "devague_version_pinned": PINNED_DEVAGUE_VERSION,
            "devague_version_matches_pin": version.split()[-1] == PINNED_DEVAGUE_VERSION,
            "base_url": args.base_url,
            "cortex_max_tokens": CORTEX_MAX_TOKENS,
            "muse_max_tokens": MUSE_MAX_TOKENS,
            "experiment_1_status": "ABSENT unless separately recorded — see the results document",
            "state_mutating_devague_commands_issued": 0,
            **commit,
        },
    )
    print(json.dumps({"kind": "config", **config}, indent=2))
    if not commit["predates_muse_tool_seam"]:
        print("error: base commit does not predate the muse tool seam", file=sys.stderr)
        print("hint: the pre-registration requires a pre-seam commit", file=sys.stderr)
        return 2

    if args.dry_run:
        for case in cases:
            print(f"{case.case_id:<48} {case.pool:<9} {case.char_len:>5} chars")
        print(json.dumps(length_confound(cases), indent=2))
        return 0

    digests_before = ledger_digests()
    logs_before = {plan: confirm_log(plan) for plan in sorted(DELIVERY_LEDGERS)}

    records: list[dict[str, Any]] = []
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "config", **config}) + "\n")
        fh.write(
            json.dumps(
                {
                    "kind": "case-pool",
                    "cases": [
                        {
                            "case_id": c.case_id,
                            "pool": c.pool,
                            "ground_truth_warrants": c.ground_truth_warrants,
                            "char_len": c.char_len,
                            "text": c.text,
                        }
                        for c in cases
                    ],
                    "length_confound": length_confound(cases),
                }
            )
            + "\n"
        )
        for case in cases:
            for mind in MINDS:
                model = args.cortex_model if mind == MIND_CORTEX else args.muse_model
                record = judge(case, mind, model, args.base_url, api_key)
                records.append(record)
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                verdict = record["parsed"]["warrants_deviation"] if record["parsed"] else "DEGRADED"
                print(
                    f"  {case.case_id:<46} {mind:<7} "
                    f"truth={str(case.ground_truth_warrants):<5} said={verdict} "
                    f"({record['latency_s']}s)",
                    file=sys.stderr,
                )

        digests_after = ledger_digests()
        logs_after = {plan: confirm_log(plan) for plan in sorted(DELIVERY_LEDGERS)}
        assertion = assert_no_confirms(
            records, digests_before, digests_after, logs_before, logs_after
        )
        summary = {
            "kind": "summary",
            "confirm_assertion": assertion,
            "static_confirm_gate_evidence": static_confirm_gate_evidence(),
            "concordance": [concordance(records, m) for m in MINDS],
            "length_confound": length_confound(cases),
            "degraded_calls": sum(1 for r in records if r["parsed"] is None),
            "total_calls": len(records),
        }
        fh.write(json.dumps(summary) + "\n")

    print(json.dumps(summary, indent=2))
    if not assertion["passes"]:
        print("error: a model-authored judgment was confirmed", file=sys.stderr)
        print("hint: the structural claim is falsified — report it, do not re-run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
