"""Shared config-record preamble for experiment harnesses.

Every experiment harness calls ``write_config_preamble`` BEFORE its first
result line so the run is reproducible from the JSON alone.  Records:

- Per-role temperature (cortex and muse SEPARATELY — an earlier series ran
  both at 0.2 by accident)
- Muse controls (max_turns, staleness_policy)
- Model ids (cortex_model, muse_model)
- n (number of runs)
"""

from __future__ import annotations

import json
from typing import Any, Optional


def write_config_preamble(
    path: str,
    *,
    cortex_model: str,
    cortex_temperature: float,
    muse_model: Optional[str] = None,
    muse_temperature: Optional[float] = None,
    max_turns: int = 14,
    staleness_policy: str = "default",
    n: int = 1,
) -> dict[str, Any]:
    """Write a JSON config preamble and return the config dict.

    Call this BEFORE the first result line so the run is reproducible from
    the JSON alone.
    """
    config: dict[str, Any] = {
        "cortex_model": cortex_model,
        "cortex_temperature": cortex_temperature,
        "muse_model": muse_model,
        "muse_temperature": muse_temperature,
        "max_turns": max_turns,
        "staleness_policy": staleness_policy,
        "n": n,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return config
