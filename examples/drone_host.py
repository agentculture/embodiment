#!/usr/bin/env python3
"""A host that wires a live worker onto a drone's scoped calls, and meters it.

The CLI's ``evoke`` deliberately ships no live worker: it answers from
``--answers`` or not at all, and a host injects one through
:func:`embodiment.drone.invoke`'s ``ask`` parameter. This is that host, and it
exists to settle a number rather than to be a product.

`c30`'s third success signal is *"a drone's second evocation makes 0 cortex
calls at <=5% of its authoring tokens"*. The authoring turn is measured in
`docs/live-test-results/drone-authoring-cost.json` (4,785 completion tokens,
`examples/author_drone.py`). This evokes the same drone twice against the live
worker and reports what each run actually spent, so the ratio is measured on
both sides instead of asserted on one.

Zero cortex calls is structural rather than hopeful: a drone's only outward
seam is ``DroneRequest.ask``, which reaches the worker this module wires and
nothing else. There is no escalation path in v1 (`c46`).

    EMBODIMENT_LIVE_RIG=1 EMBODIMENT_DRONES_ENABLED=1 \\
      uv run python examples/drone_host.py index-gaps --repo .
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment import drone as drone_lib  # noqa: E402
from examples import worker_seam as ws  # noqa: E402


class MeteredAsk:
    """An :data:`~embodiment.drone.AskFn` backed by the worker, counting tokens.

    One seam per call, matching every other concurrent user of this transport:
    ``WorkerSeam.meter`` has no lock, so sharing an instance would race it.
    """

    def __init__(self, config: ws.WorkerConfig, *, max_tokens: int = 4000) -> None:
        self._config = config
        self._max_tokens = max_tokens
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def __call__(self, question_id: str, payload: Mapping[str, Any]) -> Any:
        question = self._questions[question_id]
        prompt = (
            f"{question['prompt']}\n\n"
            f"Document: {json.dumps(dict(payload), ensure_ascii=False)}\n"
            "Answer with one word only."
        )
        seam = ws.WorkerSeam(
            base_url=self._config.base_url,
            model=self._config.model,
            api_key=self._config.api_key,
            role=f"drone-{question_id}",
            max_tokens=self._max_tokens,
            temperature=0.3,
            # The scoped lane does not stream: tens of tokens, nothing to watch
            # arrive, and its baselines were measured non-streaming.
            stream=False,
        )
        reply = seam([{"role": "user", "content": prompt}])
        self.calls += 1
        self.prompt_tokens += seam.meter.prompt_tokens
        self.completion_tokens += seam.meter.completion_tokens
        return (reply.content or "").strip().split()[0].strip(".,").lower() or None

    def bind(self, questions: Mapping[str, Mapping[str, Any]]) -> "MeteredAsk":
        self._questions = questions
        return self


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--authoring-tokens", type=int, default=4785)
    parser.add_argument("--max-tokens", type=int, default=4000)
    args = parser.parse_args(argv)

    root = Path(args.repo).resolve()
    resolution = ws.resolve_worker_config()
    if resolution.config is None:
        for degradation in resolution.degradations:
            print(f"worker ABSENT: {degradation.code}: {degradation.detail}", file=sys.stderr)
        return 2

    loaded = drone_lib.load(args.name, root / drone_lib.DRONES_DIRNAME)
    questions = {q["id"]: q for q in loaded.manifest.get("questions", [])}
    opt_in = drone_lib.DroneOptIn(enabled=True, source="host", detail="drone_host.py")

    results = []
    for run in range(1, args.runs + 1):
        meter = MeteredAsk(resolution.config, max_tokens=args.max_tokens).bind(questions)
        record = drone_lib.invoke(
            loaded, root=root, args={"repo": str(root)}, ask=meter, opt_in=opt_in
        )
        results.append(
            {
                "run": run,
                "outcome": record.outcome,
                "ran": record.ran,
                "calls_asked": len(record.calls),
                "calls_accepted": sum(1 for c in record.calls if c.accepted),
                "call_acceptance": (
                    sum(1 for c in record.calls if c.accepted) / len(record.calls)
                    if record.calls
                    else None
                ),
                "worker_calls": meter.calls,
                "prompt_tokens": meter.prompt_tokens,
                "completion_tokens": meter.completion_tokens,
                "cortex_calls": 0,  # structural: ask() reaches the worker, nothing else
                "answer": record.answer,
            }
        )
        print(json.dumps(results[-1], indent=1))

    if len(results) >= 2:
        second = results[1]
        ratio = second["completion_tokens"] / args.authoring_tokens
        verdict = {
            "kind": "drone-evocation-cost",
            "authoring_completion_tokens": args.authoring_tokens,
            "second_evocation_completion_tokens": second["completion_tokens"],
            "second_evocation_prompt_tokens": second["prompt_tokens"],
            "ratio_of_authoring": round(ratio, 5),
            "target": 0.05,
            "meets_target": ratio <= 0.05,
            "cortex_calls": second["cortex_calls"],
        }
        print(json.dumps(verdict, indent=1))
        out = os.environ.get("EVOCATION_OUT")
        if out:
            Path(out).write_text(json.dumps({"runs": results, "verdict": verdict}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
