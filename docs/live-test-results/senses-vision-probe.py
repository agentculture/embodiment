#!/usr/bin/env python3
"""Can the senses seat actually see? — measured, not inherited from an advert.

Context. The spark gateway's ``/capabilities`` advert for the ``senses`` role
says ``coolthor/gemma-4-12B-it-NVFP4A16``, ``ready=false``, and declares nothing
perceptual. The Orin that actually serves the seat says
``unsloth/gemma-4-12B-it-qat-w4a16`` and answers. So the advert is behind the
deployment, and this repo already has a documented instance of exactly that gap
pointing the other way (``cortex-vision-probe.md`` — *"the advert says it cannot
/ the measurement says it can"*, lobes-cli#166).

The standing rule is *resolve roles by name from ``/capabilities``, never parse
model names*, which means a capability the advert does not declare may not be
used by a consumer even when the checkpoint has it. This probe does not change
that rule. It establishes **what is true underneath**, so the advert can be
fixed against a measurement rather than an assumption.

Probe discipline, inherited from the two probes before it:

* The vision probe **discarded its first stimulus** because "red, green, blue"
  is the most probable guess for three colour bands and a blind model would
  score it. So this stimulus uses **five** bands in a deliberately improbable
  order, and asks for a **positional** lookup — a blind model must guess both
  the count and the position.
* A **text-only control** runs the identical question with the image removed.
  Without it, a lucky guess and a real reading are indistinguishable.
* Both the ``image_url`` and ``video_url`` content-part routes are exercised,
  because ``media.py``'s measured finding is that a GIF sent as ``image_url``
  flattens to one frame while ``video_url`` preserves motion at fewer prompt
  tokens (``video-perception-probe.md``).

Run::

    uv run python docs/live-test-results/senses-vision-probe.py

Writes ``senses-vision-probe.jsonl``. Facts only; the verdict is written by a
human into the report beside it.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

ORIN_URL = "http://orin.tail0be7e0.ts.net:8000/v1"
SENSES_MODEL = "unsloth/gemma-4-12B-it-qat-w4a16"
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: Five bands, top to bottom. Deliberately not a rainbow and not RGB order — a
#: blind model guessing the most probable palette does not land on this.
BANDS = [
    ("magenta", (255, 0, 144)),
    ("olive", (128, 128, 0)),
    ("teal", (0, 128, 128)),
    ("white", (255, 255, 255)),
    ("navy", (0, 0, 128)),
]

#: The band asked about, 1-indexed from the top. Third avoids both endpoints,
#: which are the two positions a guesser is most likely to describe.
ASK_INDEX = 3
TRUTH_COLOUR = BANDS[ASK_INDEX - 1][0]
TRUTH_COUNT = len(BANDS)

QUESTION = (
    "Look at the image. Answer with exactly two comma-separated values and "
    "nothing else: the number of horizontal colour bands, and the colour of "
    f"the band that is {ASK_INDEX}rd from the top. Example format: 4, purple"
)


def build_stimulus() -> bytes:
    """A PNG of five stacked bands. Built here so the stimulus is reproducible."""
    from PIL import Image

    width, band_height = 240, 48
    image = Image.new("RGB", (width, band_height * len(BANDS)))
    for index, (_, rgb) in enumerate(BANDS):
        for y in range(index * band_height, (index + 1) * band_height):
            for x in range(width):
                image.putpixel((x, y), rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_motion_stimulus() -> bytes:
    """A 3-frame GIF: one square moving left to right.

    Sent through the ``video_url`` route, which ``video-perception-probe.md``
    measured as the path that preserves motion (an ``image_url`` GIF flattens to
    one frame). A single-frame PNG is NOT a valid video stimulus — sending one
    produces a server-side cast error that says nothing about the deployment's
    video support, which is a way of measuring nothing.
    """
    from PIL import Image

    size, box = 96, 24
    frames = []
    for step in range(3):
        frame = Image.new("RGB", (size, size), (255, 255, 255))
        left = step * (size - box) // 2
        for y in range(size // 2 - box // 2, size // 2 + box // 2):
            for x in range(left, left + box):
                frame.putpixel((x, y), (0, 0, 0))
        frames.append(frame)
    buffer = io.BytesIO()
    frames[0].save(
        buffer, format="GIF", save_all=True, append_images=frames[1:], duration=300, loop=0
    )
    return buffer.getvalue()


MOTION_QUESTION = (
    "This is a short animation. Answer with exactly one word and nothing else: "
    "which direction does the black square move? Options: left, right, up, down."
)
MOTION_TRUTH = "right"

#: The completion budget. Small on purpose — every answer here is a word or two,
#: and a large budget on the interaction tier is the failure #63 recorded (22
#: minutes of generation on one turn, which is why hosts bound this seat at
#: 1024).
MAX_TOKENS = 128


def derive_request_timeout(max_tokens: int = MAX_TOKENS) -> tuple[float, str]:
    """The clock, derived from the committed rate config — never chosen.

    ``REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate``, plus
    the committed non-generation allowance, because a client bound also covers
    queue wait and prefill and this budget is far too small for the rate to have
    absorbed them.

    **There is no committed rate for the ``senses`` role**, so the divisor is the
    slowest rate measured for *any* role. That over-protects, which is the safe
    direction for a bound whose failure mode is censoring the evidence — and it
    is stated here rather than hidden in a number. The trigger to re-derive is a
    committed senses rate existing at all.
    """
    from rate_config import load_rate_config

    config = load_rate_config()
    rates = {role: config.rate(role).slowest_tok_s for role in ("cortex", "worker")}
    role, divisor = min(rates.items(), key=lambda kv: kv[1])
    allowance = config.non_generation_allowance.seconds
    bound = max_tokens / divisor + allowance
    return bound, (
        f"{max_tokens} tokens / {divisor} tok/s + {allowance:.1f}s non-generation "
        f"allowance = {bound:.1f}s (slowest committed rate, role {role!r}; there is "
        "no committed rate for 'senses', so the all-role floor stands in)"
    )


def data_uri(payload: bytes, media_type: str) -> str:
    return f"data:{media_type};base64," + base64.b64encode(payload).decode("ascii")


def ask(
    base_url: str,
    model: str,
    api_key: str,
    parts: list[dict[str, Any]],
    timeout_s: float,
) -> dict[str, Any]:
    """One completion. Never raises — a transport failure is a recorded fact."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.0,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return {"error": f"HTTP {exc.code}", "detail": detail, "latency_s": None}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "latency_s": None}
    latency = time.monotonic() - started
    choice = (body.get("choices") or [{}])[0]
    usage = body.get("usage") or {}
    return {
        "content": (choice.get("message") or {}).get("content") or "",
        "finish_reason": choice.get("finish_reason"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "latency_s": round(latency, 2),
    }


def score(content: str) -> dict[str, Any]:
    """Did it read the image? Count and colour scored separately, on purpose."""
    lowered = (content or "").lower()
    return {
        "count_correct": str(TRUTH_COUNT) in lowered,
        "colour_correct": TRUTH_COLOUR in lowered,
        "named_a_wrong_band": any(name in lowered for name, _ in BANDS if name != TRUTH_COLOUR),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=ORIN_URL)
    parser.add_argument("--model", default=SENSES_MODEL)
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="override the derived request bound (seconds); omit to derive it",
    )
    parser.add_argument("--out", default=str(Path(__file__).with_name("senses-vision-probe.jsonl")))
    args = parser.parse_args()

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"error: no {API_KEY_ENV} in the environment", file=sys.stderr)
        print(f"hint: export {API_KEY_ENV}=<the gateway's configured key>", file=sys.stderr)
        return 2

    timeout_s = args.timeout
    if timeout_s is None:
        timeout_s, provenance = derive_request_timeout()
    else:
        provenance = f"operator override: {timeout_s}s"
    print(f"clock: {provenance}", file=sys.stderr)

    png = build_stimulus()
    gif = build_motion_stimulus()
    image_part = {"type": "image_url", "image_url": {"url": data_uri(png, "image/png")}}
    video_part = {"type": "video_url", "video_url": {"url": data_uri(gif, "image/gif")}}

    cells = [
        ("image_url", [{"type": "text", "text": QUESTION}, image_part]),
        ("video_url_motion", [{"type": "text", "text": MOTION_QUESTION}, video_part]),
        # The control: identical question, no image at all. A model that scores
        # here is guessing, and a score in the image cells means nothing.
        ("text_only_control", [{"type": "text", "text": QUESTION}]),
    ]

    print(
        f"truth: {TRUTH_COUNT} bands, #{ASK_INDEX} from top is {TRUTH_COLOUR}",
        file=sys.stderr,
    )
    records = []
    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as handle:
        for cell, parts in cells:
            for run in range(1, args.runs + 1):
                result = ask(args.base_url, args.model, api_key, parts, timeout_s)
                record = {
                    "cell": cell,
                    "run": run,
                    "model": args.model,
                    "base_url": args.base_url,
                    "timeout_s": round(timeout_s, 1),
                    "timeout_provenance": provenance,
                    "truth_count": TRUTH_COUNT,
                    "truth_colour": TRUTH_COLOUR,
                    **result,
                }
                if "content" in result:
                    if cell == "video_url_motion":
                        lowered = (result["content"] or "").lower()
                        record.update(
                            {
                                "truth_motion": MOTION_TRUTH,
                                "motion_correct": MOTION_TRUTH in lowered,
                            }
                        )
                    else:
                        record.update(score(result["content"]))
                records.append(record)
                handle.write(json.dumps(record) + "\n")
                handle.flush()
                summary = result.get("error") or repr(result.get("content", ""))[:70]
                print(f"  {cell:20} run {run}: {summary}", file=sys.stderr)

    print("\n--- summary (facts only) ---", file=sys.stderr)
    for cell, _ in cells:
        rows = [r for r in records if r["cell"] == cell]
        ok = [r for r in rows if "content" in r]
        if cell == "video_url_motion":
            both = sum(1 for r in ok if r.get("motion_correct"))
        else:
            both = sum(1 for r in ok if r.get("count_correct") and r.get("colour_correct"))
        errors = len(rows) - len(ok)
        print(
            f"{cell:20} fully correct {both}/{len(rows)}"
            f"  (count {sum(1 for r in ok if r.get('count_correct'))},"
            f" colour {sum(1 for r in ok if r.get('colour_correct'))},"
            f" transport errors {errors})",
            file=sys.stderr,
        )
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
