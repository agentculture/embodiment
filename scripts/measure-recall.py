#!/usr/bin/env python3
"""Recall measurement for Gwen's memory (plan task t18, embodiment#94).

Measures, on a COPY of a store and never on the live one:

* ``gen``     - builds a labelled query set from the store's active records with a
                chat role (one *close* and one *reworded* question per record) plus a
                set of unrelated questions. The set is written to ``--out`` ONLY;
                nothing it contains is ever printed.
* ``measure`` - keyword (+ the daemon's d5 exact-substring fallback, exactly as the
                daemon runs it) versus semantic recall over cached ``gemma2-embed``
                vectors: hit@1 / hit@5 per query kind, latency, a threshold sweep with
                a two-fold cross-check, and the arrival distribution of the recall call.
* ``volume``  - pads copies of the store with SYNTHETIC filler records to several sizes
                and measures semantic recall latency against store size.
* ``stt``     - (lapse l3) transcription time against clip duration on synthetic audio.
* ``senses``  - (lapse l5) chat-completion latency for short turns.

Privacy: the repo is public and the store is one person's memory. This script holds no
data. Every command takes the store path, the output directory and the roles as
arguments; records, queries, labels and per-query results live in ``--out`` (keep it
outside the repo). What is printed - and what ``results-*.json`` holds - is aggregates
only: counts, rates, latencies, thresholds, n.

All paths use the real code: ``RoomMemory.recall(mode="semantic")``, ``EmbedderClient``,
``backfill_tick`` and the daemon's own ``_lexical_can_index`` / ``_fallback_terms``.
Recall runs with ``reinforce=False`` so the measurement does not edit the copy it reads.

Run every gateway-touching command under ``grant run`` so the key is injected, never
exported or read::

    grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- \\
        uv run python scripts/measure-recall.py gen --store S.jsonl --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import statistics
import struct
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from embodiment import memory as memory_module
from embodiment.daemon.app import (
    MEMORY_MODE_EXACT,
    _fallback_terms,
    _lexical_can_index,
    http_complete,
)
from embodiment.memory import PRIVATE, RoomMemory, vector_cache_for
from embodiment.vectors import EmbedderClient
from embodiment.voice import VoiceConfig, http_synthesize

TOP_K = 5
SCOPE = "gwen"
KINDS = ("close", "reworded")
PCM_RATE = 24000

GEN_PROMPT = (
    "Below is one remembered fact, in Hebrew. Write two short Hebrew questions that someone "
    "would ask to get this fact back.\n"
    '"close": a natural question; it may reuse words from the fact.\n'
    '"reworded": the same question, but do NOT reuse any content word of the fact - use '
    "synonyms, different word forms, or prefixes.\n"
    'Answer with JSON only: {"close": "...", "reworded": "..."}\n\nFact:\n'
)
UNRELATED_PROMPT = (
    "Write {n} short, varied Hebrew questions that a person might ask a voice assistant "
    "about everyday general-knowledge topics (weather, sport, cooking, geography, science, "
    "history, travel, music, health, technology). Topic hint for variety: {hint}. "
    "Each question must be self-contained and impersonal (no names, no 'my'). "
    'Answer with JSON only: {{"questions": ["...", "..."]}}'
)
FILLER_PROMPT = (
    "Write {n} distinct short Hebrew sentences, each a plain fact a person might ask an "
    "assistant to remember (a preference, a place, a date, a small plan). They are made up. "
    "Topic hint for variety: {hint}. "
    'Answer with JSON only: {{"facts": ["...", "..."]}}'
)
HINTS = [
    "kitchen", "garden", "pets", "sports", "travel", "music", "school", "work meetings",
    "shopping", "health", "weather", "movies", "books", "cars", "friends", "holidays",
    "technology", "neighbours", "hobbies", "finances",
]  # fmt: skip


# --------------------------------------------------------------------------- helpers


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile; ``None`` for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def latency_summary(ms: list[float]) -> dict[str, Any]:
    if not ms:
        return {"n": 0}
    return {
        "n": len(ms),
        "median_ms": round(statistics.median(ms), 1),
        "p90_ms": round(percentile(ms, 0.9), 1),
        "max_ms": round(max(ms), 1),
    }


def gateway_key() -> str:
    return os.environ.get("EMBODIMENT_GATEWAY_KEY", "")


def read_store(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_store(directory: Path, records: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{SCOPE}__private.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_active(record: dict[str, Any]) -> bool:
    return record.get("metadata", {}).get("lifecycle") == "active"


def synthetic_record(text: str, index: int) -> dict[str, Any]:
    """A filler record shaped like a stored one; the id says it is not real."""
    return {
        "id": f"filler-{index:06d}",
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "content": text,
        "scope": {"name": SCOPE, "visibility": "private"},
        "metadata": {
            "type": "explicit-ask",
            "record_metadata": {},
            "created": "2026-01-01T00:00:00+00:00",
            "last_recall": None,
            "recall_count": 0,
            "links": [],
            "supersedes": None,
            "lifecycle": "active",
            "added_by": SCOPE,
        },
    }


def chat_json(prompt: str, role: str, deadline: float = 180.0) -> dict[str, Any]:
    reply = http_complete(
        [{"role": "user", "content": prompt}],
        api_key=gateway_key(),
        role=role,
        max_tokens=4000,
        deadline=deadline,
    )
    match = re.search(r"\{.*\}", reply.content, re.S)
    return json.loads(match.group(0)) if match else {}


def make_room(
    directory: Path, embed_model: str, gateway: str, threshold: float
) -> tuple[RoomMemory, EmbedderClient]:
    embedder = EmbedderClient(gateway, gateway_key(), model=embed_model)
    room = RoomMemory(
        directory,
        scope=SCOPE,
        added_by=SCOPE,
        embedder=embedder,
        vector_cache=vector_cache_for(directory, embed_model),
        similarity_threshold=threshold,
    )
    return room, embedder


def backfill_all(room: RoomMemory, budget: float = 600.0) -> dict[str, Any]:
    """Embed every uncached active record. Returns aggregates only."""
    started = time.monotonic()
    embedded = 0
    while time.monotonic() - started < budget:
        report = room.backfill_tick(max_records=64, budget=60.0)
        embedded += report.get("embedded", 0)
        if report.get("offline"):
            return {"embedded": embedded, "complete": False, "reason": "embedder offline"}
        if not report.get("remaining"):
            return {
                "embedded": embedded,
                "complete": True,
                "seconds": round(time.monotonic() - started, 1),
            }
    return {"embedded": embedded, "complete": False, "reason": "budget"}


def ids_of(result: Any) -> list[str]:
    return [r.get("id") for r in (result.records or []) if isinstance(r, dict)]


def codes_of(result: Any) -> list[str]:
    return [d.code for d in (result.degradations or ())]


def load_labels(out: Path) -> tuple[list[dict[str, Any]], list[str]]:
    labelled = [
        json.loads(line)
        for line in (out / "labelled.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    unrelated = [
        json.loads(line)["query"]
        for line in (out / "unrelated.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    return labelled, unrelated


# ------------------------------------------------------------------------------- gen


def cmd_gen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records = [r for r in read_store(Path(args.store)) if is_active(r)]
    labelled = failed = 0
    with (out / "labelled.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            pair: dict[str, Any] = {}
            for attempt in range(3):  # a chat role sometimes answers prose; ask again
                try:
                    pair = chat_json(GEN_PROMPT + record["content"], args.role)
                    if pair.get("close") and pair.get("reworded"):
                        break
                except Exception as exc:  # noqa: BLE001 - degrade, record, continue
                    print(f"generation attempt {attempt + 1} failed:", type(exc).__name__)
            if not (pair.get("close") and pair.get("reworded")):
                failed += 1
                continue
            for kind in KINDS:
                query = str(pair.get(kind) or "").strip()
                if query:
                    row = {
                        "target": record["id"],
                        "rtype": record["metadata"].get("type"),
                        "kind": kind,
                        "query": query,
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    labelled += 1
    unrelated: list[str] = []
    for hint in HINTS[: args.unrelated_calls]:
        try:
            got = chat_json(UNRELATED_PROMPT.format(n=10, hint=hint), args.role)
        except Exception as exc:  # noqa: BLE001
            print("unrelated generation failed:", type(exc).__name__)
            continue
        unrelated += [str(q).strip() for q in got.get("questions", []) if str(q).strip()]
    unrelated = list(dict.fromkeys(unrelated))
    with (out / "unrelated.jsonl").open("w", encoding="utf-8") as fh:
        for query in unrelated:
            fh.write(json.dumps({"query": query}, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "active_records": len(records),
                "labelled_queries": labelled,
                "records_failed": failed,
                "unrelated_queries": len(unrelated),
            }
        )
    )
    return 0


# --------------------------------------------------------------------------- measure


def keyword_arm(room: RoomMemory, query: str, budget: float) -> dict[str, Any]:
    """The daemon's ``_recall`` as it runs for a lexical store: keyword, then d5."""
    started = time.monotonic()
    result = room.recall(
        query, deadline=budget, mode="keyword", top_k=TOP_K, visibility=PRIVATE, reinforce=False
    )
    got = ids_of(result)
    path = "keyword"
    codes = codes_of(result)
    if not got and not _lexical_can_index(query):
        path = "fallback-empty"
        for word in _fallback_terms(query):
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                path = "fallback-out-of-budget"
                break
            result = room.recall(
                word,
                deadline=max(0.01, remaining),
                mode=MEMORY_MODE_EXACT,
                top_k=TOP_K,
                visibility=PRIVATE,
                reinforce=False,
            )
            codes += codes_of(result)
            if ids_of(result):
                got, path = ids_of(result), "fallback-hit"
                break
    return {"ms": (time.monotonic() - started) * 1000, "got": got, "path": path, "codes": codes}


def semantic_arm(room: RoomMemory, query: str, deadline: float) -> dict[str, Any]:
    started = time.monotonic()
    result = room.recall(
        query,
        deadline=deadline,
        mode=memory_module.SEMANTIC_MODE,
        top_k=TOP_K,
        visibility=PRIVATE,
        reinforce=False,
    )
    ms = (time.monotonic() - started) * 1000
    return {
        "ms": ms,
        "got": ids_of(result),
        "scores": [r.get("score") for r in result.records or []],
        "relevance": result.relevance,
        "mode": result.mode,
        "codes": codes_of(result),
    }


def hit_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """hit@1 / hit@5 per query kind (labelled rows only), with n."""
    out: dict[str, Any] = {}
    for kind in KINDS:
        sub = [r for r in rows if r["kind"] == kind]
        out[kind] = {
            "n": len(sub),
            "hit@5": sum(r["target"] in r["got"] for r in sub),
            "hit@1": sum(r["got"][:1] == [r["target"]] for r in sub),
            "empty": sum(not r["got"] for r in sub),
        }
    return out


def sweep(rows: list[dict[str, Any]], unrelated: list[dict[str, Any]], ts: list[float]) -> list:
    """Re-apply a threshold offline to rows recalled at a near-zero threshold."""
    table = []
    for t in ts:
        hits5 = hits1 = 0
        for r in rows:
            kept = [i for i, s in zip(r["got"], r["scores"]) if s is not None and s >= t]
            hits5 += r["target"] in kept
            hits1 += kept[:1] == [r["target"]]
        fp = sum(any(s is not None and s >= t for s in u["scores"]) for u in unrelated)
        table.append(
            {
                "threshold": round(t, 3),
                "hit@5": hits5,
                "hit@1": hits1,
                "n_labelled": len(rows),
                "false_positive": fp,
                "n_unrelated": len(unrelated),
            }
        )
    return table


def pick_threshold(
    rows: list[dict[str, Any]], unrelated: list[dict[str, Any]], max_fp_rate: float
) -> float:
    """The smallest candidate threshold whose FP rate is <= max_fp_rate, maximising hit@5."""
    candidates = sorted({round(s, 4) for u in unrelated for s in u["scores"] if s is not None})
    candidates += [round(s, 4) + 0.0001 for s in list(candidates)]
    candidates += [0.5 + 0.01 * i for i in range(0, 50)]
    best, best_hits = 1.01, -1
    for t in sorted(set(candidates)):
        row = sweep(rows, unrelated, [t])[0]
        if row["false_positive"] / max(1, row["n_unrelated"]) <= max_fp_rate:
            if row["hit@5"] > best_hits or (row["hit@5"] == best_hits and t < best):
                best, best_hits = t, row["hit@5"]
    return best


def cmd_measure(args: argparse.Namespace) -> int:
    out = Path(args.out)
    labelled, unrelated_q = load_labels(out)
    work = out / "measure-store"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    write_store(work, read_store(Path(args.store)))
    shutil.copy2(work / f"{SCOPE}__private.jsonl", out / "pristine-copy.jsonl")

    results: dict[str, Any] = {"n_labelled": len(labelled), "n_unrelated": len(unrelated_q)}

    # ---- keyword + d5 ("before"), each query repeated for a latency sample
    kw_dir = out / "measure-keyword"
    if kw_dir.exists():
        shutil.rmtree(kw_dir)
    write_store(kw_dir, read_store(Path(args.store)))
    kw_room = RoomMemory(kw_dir, scope=SCOPE, added_by=SCOPE)
    keyword_arm(kw_room, "warm", args.budget)
    kw_rows, kw_unrel = [], []
    for _ in range(args.reps):
        for q in labelled:
            kw_rows.append({**q, **keyword_arm(kw_room, q["query"], args.budget)})
        for text in unrelated_q:
            kw_unrel.append(keyword_arm(kw_room, text, args.budget))
    first = kw_rows[: len(labelled)]
    results["keyword_d5"] = {
        "hits": hit_table(first),
        "paths": {
            p: sum(r["path"] == p for r in first) for p in sorted({r["path"] for r in first})
        },
        "unrelated_returning_any": sum(bool(u["got"]) for u in kw_unrel[: len(unrelated_q)]),
        "n_unrelated": len(unrelated_q),
        "latency": latency_summary([r["ms"] for r in kw_rows]),
        "budget_exceeded": sum("recall-deadline-exceeded" in r["codes"] for r in kw_rows),
        "reps": args.reps,
    }
    kw_room.close(2.0)

    # ---- semantic: threshold ~0 so per-record cosines are visible; sweep offline
    room, embedder = make_room(work, args.embed_role, args.gateway, threshold=-1.0)
    results["backfill"] = backfill_all(room)
    semantic_arm(room, "warm", 30.0)
    rows, unrel = [], []
    for q in labelled:
        rows.append({**q, **semantic_arm(room, q["query"], 30.0)})
    for text in unrelated_q:
        unrel.append(semantic_arm(room, text, 30.0))
    ts = [round(0.50 + 0.02 * i, 2) for i in range(0, 26)]
    results["ranking_at_no_threshold"] = hit_table(rows)
    results["sweep"] = sweep(rows, unrel, ts)
    chosen = pick_threshold(rows, unrel, args.max_fp_rate)
    results["calibration"] = {
        "max_fp_rate": args.max_fp_rate,
        "chosen_threshold": chosen,
        "at_chosen": sweep(rows, unrel, [chosen])[0],
        "at_default": sweep(rows, unrel, [memory_module.DEFAULT_SIMILARITY_THRESHOLD])[0],
        "default_threshold": memory_module.DEFAULT_SIMILARITY_THRESHOLD,
    }
    # two-fold cross-check by target record: calibrate on one half, score the other
    targets = sorted({q["target"] for q in labelled})
    random.Random(7).shuffle(targets)  # nosec B311 - a fixed fold split, not security
    folds = [set(targets[::2]), set(targets[1::2])]
    unrel_folds = [unrel[::2], unrel[1::2]]
    cv = []
    for i in (0, 1):
        train_rows = [r for r in rows if r["target"] in folds[i]]
        test_rows = [r for r in rows if r["target"] in folds[1 - i]]
        t = pick_threshold(train_rows, unrel_folds[i], args.max_fp_rate)
        cv.append(
            {"fold": i, "threshold": t, "held_out": sweep(test_rows, unrel_folds[1 - i], [t])[0]}
        )
    results["cross_check"] = cv

    # ---- semantic at the chosen and default thresholds, repeated: latency + arrival
    final: dict[str, Any] = {}
    for name, threshold in (
        ("default", memory_module.DEFAULT_SIMILARITY_THRESHOLD),
        ("chosen", chosen),
    ):
        room._threshold = float(threshold)  # noqa: SLF001 - a measurement harness
        t_rows, t_unrel = [], []
        for _ in range(args.reps):
            for q in labelled:
                t_rows.append({**q, **semantic_arm(room, q["query"], 30.0)})
            for text in unrelated_q:
                t_unrel.append(semantic_arm(room, text, 30.0))
        once = t_rows[: len(labelled)]
        lat = [r["ms"] for r in t_rows + t_unrel]
        final[name] = {
            "threshold": threshold,
            "hits": hit_table(once),
            "unrelated_returning_any": sum(bool(u["got"]) for u in t_unrel[: len(unrelated_q)]),
            "n_unrelated": len(unrelated_q),
            "latency": latency_summary(lat),
            "over_0_25s": sum(m > 250 for m in lat),
            "n_calls": len(lat),
            "demotions": sum("embedder-offline" in r["codes"] for r in t_rows + t_unrel),
        }
    results["semantic"] = final
    # the arrival distribution: how long the recall call takes to land, per call
    results["arrival"] = {
        "semantic_default_store": final["chosen"]["latency"],
        "keyword_d5_default_store": results["keyword_d5"]["latency"],
        "note": "recall-call latency only; the turn-start offset (STT, floor) is not in it",
    }
    # query-embed cost on its own, to split latency into embed and scoring
    embed_ms = []
    for text in unrelated_q[: min(len(unrelated_q), 30)]:
        t0 = time.monotonic()
        embedder.embed(text, prompt=memory_module.QUERY_PROMPT)
        embed_ms.append((time.monotonic() - t0) * 1000)
    results["query_embed_alone"] = latency_summary(embed_ms)
    room.close(2.0)
    (out / "results-measure.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(json.dumps(results, indent=1))
    return 0


# --------------------------------------------------------------------------- volume


def filler_texts(out: Path, role: str, want: int) -> list[str]:
    """Synthetic filler, generated by the chat role and cached in ``--out``."""
    cache = out / "filler.jsonl"
    texts: list[str] = []
    if cache.exists():
        texts = [json.loads(x)["t"] for x in cache.read_text(encoding="utf-8").splitlines() if x]
    seen = set(texts)
    attempt = 0
    while len(texts) < want and attempt < 400:
        hint = f"{HINTS[attempt % len(HINTS)]} (batch {attempt})"
        attempt += 1
        try:
            got = chat_json(FILLER_PROMPT.format(n=25, hint=hint), role)
        except Exception as exc:  # noqa: BLE001
            print("filler generation failed:", type(exc).__name__)
            continue
        fresh = [str(t).strip() for t in got.get("facts", []) if str(t).strip() not in seen]
        for t in fresh:
            seen.add(t)
            texts.append(t)
        with cache.open("w", encoding="utf-8") as fh:
            for t in texts:
                fh.write(json.dumps({"t": t}, ensure_ascii=False) + "\n")
    return texts


def cmd_volume(args: argparse.Namespace) -> int:
    out = Path(args.out)
    labelled, unrelated_q = load_labels(out)
    real = read_store(Path(args.store))
    sizes = [int(s) for s in args.sizes.split(",")]
    fill = filler_texts(out, args.role, max(sizes))
    probes = [q["query"] for q in labelled] + unrelated_q
    results: dict[str, Any] = {"sizes": {}, "n_probe_queries": len(probes)}
    for size in sizes:
        pad = max(0, size - sum(is_active(r) for r in real))
        if pad > len(fill):
            results["sizes"][str(size)] = {"skipped": "not enough synthetic filler generated"}
            continue
        directory = out / f"volume-{size}"
        if directory.exists():
            shutil.rmtree(directory)
        write_store(directory, real + [synthetic_record(t, i) for i, t in enumerate(fill[:pad])])
        room, _ = make_room(directory, args.embed_role, args.gateway, args.threshold)
        fill_report = backfill_all(room, budget=3600.0)
        semantic_arm(room, "warm", 30.0)
        ms, hit5, n_lab = [], 0, 0
        for _ in range(args.reps):
            for text in probes:
                ms.append(semantic_arm(room, text, 30.0)["ms"])
        for q in labelled:
            n_lab += 1
            hit5 += q["target"] in semantic_arm(room, q["query"], 30.0)["got"]
        # scoring cost alone: the same enumeration + cosine pass without the network
        t0 = time.monotonic()
        room._enumerate_active(room._candidate_pools(PRIVATE))  # noqa: SLF001
        enum_ms = (time.monotonic() - t0) * 1000
        results["sizes"][str(size)] = {
            "records_active": sum(is_active(r) for r in real) + pad,
            "backfill": fill_report,
            "latency": latency_summary(ms),
            "over_0_25s": sum(m > 250 for m in ms),
            "enumerate_store_ms": round(enum_ms, 1),
            "hit@5_labelled": f"{hit5}/{n_lab}",
        }
        room.close(2.0)
        (out / "results-volume.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(size, json.dumps(results["sizes"][str(size)]))
    return 0


# ------------------------------------------------------------------------ stt / senses


def wav_bytes(pcm: bytes, rate: int = PCM_RATE) -> bytes:
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data"
    return header + struct.pack("<I", len(pcm)) + pcm


def multipart(fields: dict[str, str], name: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
        body += f"{head}{value}\r\n".encode()
    body += (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def transcribe(gateway: str, role: str, wav: bytes) -> float:
    body, ctype = multipart({"model": role}, "clip.wav", wav)
    request = urllib.request.Request(  # noqa: S310 - operator-supplied http(s) gateway
        gateway.rstrip("/") + "/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": ctype, "Authorization": f"Bearer {gateway_key()}"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310  # nosec B310
        response.read()
    return time.monotonic() - started


def cmd_stt(args: argparse.Namespace) -> int:
    out = Path(args.out)
    _, unrelated_q = load_labels(out)
    config = VoiceConfig(gateway_url=args.gateway, api_key=gateway_key())
    clips: list[tuple[float, float]] = []  # (audio seconds, transcription seconds)
    errors = 0
    sentences = unrelated_q[: args.sentences]
    pcms = []
    for text in sentences:
        try:
            pcms.append(http_synthesize(text, config))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print("synthesis failed:", type(exc).__name__)
    for group in (1, 2, 4):
        for start in range(0, len(pcms) - group + 1, group):
            pcm = b"".join(pcms[start : start + group])
            seconds = len(pcm) / 2 / PCM_RATE
            try:
                clips.append((seconds, transcribe(args.gateway, args.stt_role, wav_bytes(pcm))))
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print("transcription failed:", type(exc).__name__)
    ratio = [t / s for s, t in clips if s > 0]
    result = {
        "n_clips": len(clips),
        "errors": errors,
        "audio_seconds": latency_summary([s * 1000 for s, _ in clips]),
        "transcribe_ms": latency_summary([t * 1000 for _, t in clips]),
        "seconds_per_audio_second": {
            "median": round(statistics.median(ratio), 3) if ratio else None,
            "p90": round(percentile(ratio, 0.9), 3) if ratio else None,
            "max": round(max(ratio), 3) if ratio else None,
        },
    }
    (out / "results-stt.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


def cmd_senses(args: argparse.Namespace) -> int:
    out = Path(args.out)
    _, unrelated_q = load_labels(out)
    ms, errors = [], 0
    for text in (unrelated_q * 3)[: args.n]:
        started = time.monotonic()
        try:
            http_complete(
                [{"role": "user", "content": text}],
                api_key=gateway_key(),
                gateway_url=args.gateway,
                role=args.role,
                max_tokens=args.max_tokens,
                deadline=120.0,
            )
            ms.append((time.monotonic() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print("completion failed:", type(exc).__name__)
    result = {
        "role": args.role,
        "max_tokens": args.max_tokens,
        "errors": errors,
        **latency_summary(ms),
    }
    (out / "results-senses.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


# ------------------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, store: bool = True) -> None:
        if store:
            sp.add_argument("--store", required=True, help="a COPY of <scope>__private.jsonl")
        sp.add_argument("--out", required=True, help="private working dir, OUTSIDE the repo")
        sp.add_argument("--gateway", default="http://localhost:8001")

    g = sub.add_parser("gen")
    common(g)
    g.add_argument("--role", default="senses")
    g.add_argument("--unrelated-calls", type=int, default=6)
    g.set_defaults(fn=cmd_gen)

    m = sub.add_parser("measure")
    common(m)
    m.add_argument("--embed-role", default="gemma2-embed")
    m.add_argument("--budget", type=float, default=0.25)
    m.add_argument("--reps", type=int, default=3)
    m.add_argument("--max-fp-rate", type=float, default=0.05)
    m.set_defaults(fn=cmd_measure)

    v = sub.add_parser("volume")
    common(v)
    v.add_argument("--role", default="senses", help="chat role that writes the filler")
    v.add_argument("--embed-role", default="gemma2-embed")
    v.add_argument("--sizes", default="25,200,1000")
    v.add_argument("--reps", type=int, default=1)
    v.add_argument("--threshold", type=float, default=memory_module.DEFAULT_SIMILARITY_THRESHOLD)
    v.set_defaults(fn=cmd_volume)

    s = sub.add_parser("stt")
    common(s, store=False)
    s.add_argument("--stt-role", default="stt")
    s.add_argument("--sentences", type=int, default=16)
    s.set_defaults(fn=cmd_stt)

    n = sub.add_parser("senses")
    common(n, store=False)
    n.add_argument("--role", default="senses")
    n.add_argument("--n", type=int, default=30)
    n.add_argument("--max-tokens", type=int, default=200)
    n.set_defaults(fn=cmd_senses)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.fn
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
