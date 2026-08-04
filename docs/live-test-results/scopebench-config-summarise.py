#!/usr/bin/env python3
"""Read cycle-2 raw records and print per-episode cost and ratchet facts.

A reading tool, not a scorer — the pre-registered verdict is computed by
``examples/scopebench_live.py report --rule config`` and nothing here duplicates
it. This exists so a human (or the operator's agent) can see what a partially
complete series has actually produced, including how long each cell cost, while
it is still running.

Handles the raw format's first line being a ``header`` object rather than an
episode row.

Run::

    uv run python docs/live-test-results/scopebench-config-summarise.py <file.jsonl> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header: dict[str, Any] = {}
    episodes: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "header" in record:
            header = record["header"]
        elif "cost" in record or "episode" in record:
            episodes.append(record)
    return header, episodes


def summarise(path: Path) -> None:
    header, episodes = rows(path)
    arm = header.get("arm") or "?"
    lane = header.get("lane") or "-"
    print(f"\n=== {path.name}   arm={arm} lane={lane} episodes={len(episodes)}")
    if not episodes:
        print("   (no episode rows yet)")
        return

    total_s = 0.0
    total_tok = 0
    applied = checks = failures = 0
    voids: dict[str, int] = {}
    for record in episodes:
        cost = record.get("cost") or {}
        ratchet = record.get("ratchet") or {}
        protocol = record.get("protocol") or {}
        seconds = float(cost.get("seconds") or 0.0)
        tokens = int(cost.get("tokens") or 0)
        total_s += seconds
        total_tok += tokens
        applied += int(ratchet.get("changes_applied") or 0)
        checks += int(ratchet.get("ratchet_checks") or 0)
        failures += int(ratchet.get("ratchet_failures") or 0)
        validity = record.get("validity") or record.get("verdict") or "valid"
        if isinstance(validity, str) and validity.startswith("void"):
            voids[validity] = voids.get(validity, 0) + 1
        print(
            f"   {str(record.get('episode'))[:24]:24} {seconds:8.1f}s "
            f"tok={tokens:6} applied={ratchet.get('changes_applied', 0)} "
            f"checks={ratchet.get('ratchet_checks', 0)} "
            f"accept={protocol.get('protocol_acceptance')} {validity}"
        )

    count = len(episodes)
    print(
        f"   -- totals: {total_s:.0f}s ({total_s / 60:.1f} min), {total_tok} tokens, "
        f"mean {total_s / count:.0f}s/episode"
    )
    print(
        f"   -- ratchet: applied={applied} checks={checks} failures={failures}"
        + ("   <- checks==0 means condition 8 is ABSENT" if checks == 0 else "")
    )
    if voids:
        print(f"   -- voids: {voids}")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("usage: scopebench-config-summarise.py <raw.jsonl> [...]", file=sys.stderr)
        print("hint: raw records live in docs/live-test-results/scopebench-config-raw/", file=sys.stderr)
        return 2
    for path in paths:
        if path.exists():
            summarise(path)
        else:
            print(f"\n=== {path}  (missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
