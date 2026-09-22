#!/usr/bin/env python3
"""check-culture-design.py — verify web/src/culture-design/ against its pin.

Plan task ``t17`` (spec target ``c21``). Ports culture-nodes'
``scripts/check-culture-design.mjs`` to this repo's convention (Python,
stdlib only — no Node dependency, matches embodiment's "no new dependency
without human approval" rule): it verifies ``web/src/culture-design/`` stays
faithful to the ``agentculture/org`` revision pinned in
``web/src/culture-design/README.md``.

One check, fatal on failure: ``tokens.css``'s copied body (everything after
its header comment's own closing ``*/``) is byte-identical to org's
``site-astro/src/styles/global.css`` AT THE RECORDED PIN, read via
``git show <pin>:<path>`` — never org's working tree, so this check is
stable even after org's HEAD moves on. embodiment's culture-design layer
copies only ``tokens.css`` (see the README) — this script covers exactly
what is copied, no more.

Run with::

    uv run python scripts/check-culture-design.py

Exit code 0 on success, 1 on any failure. No third-party dependency: only
the standard library plus a ``git`` binary on ``PATH``.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - fixed argv, no shell, verifies pinned source
import sys
from pathlib import Path

__all__ = [
    "CheckFailure",
    "read_pin_from_readme",
    "read_pin_from_tokens_header",
    "split_tokens_css",
    "read_org_file_at_pin",
    "run_checks",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
CULTURE_DESIGN_DIR = REPO_ROOT / "web" / "src" / "culture-design"
README_PATH = CULTURE_DESIGN_DIR / "README.md"
TOKENS_CSS_PATH = CULTURE_DESIGN_DIR / "tokens.css"

# Overridable for anyone running this from a different checkout layout, same
# escape hatch culture-nodes' own checker offers via CULTURE_DESIGN_ORG_REPO.
_ORG_REPO_ENV = "CULTURE_DESIGN_ORG_REPO"
_DEFAULT_ORG_REPO = "/home/spark/git/org"
_ORG_TOKENS_PATH = "site-astro/src/styles/global.css"

_SENTINEL = "---- verbatim copy of site-astro/src/styles/global.css follows ----"
_PIN_RE = re.compile(r"^pin:\s+([0-9a-f]{40})\s*$", re.MULTILINE)
_HEADER_PIN_RE = re.compile(r"^\s*\*\s*Pinned commit:\s*([0-9a-f]{40})\s*$", re.MULTILINE)


class CheckFailure(Exception):
    """Raised for any check failure; the message is the human-readable reason."""


def read_pin_from_readme(readme_text: str) -> str:
    """Extract the pinned commit hash from the README's own ``pin: <sha>`` line.

    The README is the single source of truth for the pin, mirroring the ADR
    role in culture-nodes' checker, so the script never hardcodes it
    separately (that would let the two silently drift apart).
    """
    match = _PIN_RE.search(readme_text)
    if not match:
        raise CheckFailure(f"could not find a 'pin: <40-hex-char-sha>' line in {README_PATH}")
    return match.group(1)


def read_pin_from_tokens_header(tokens_css: str) -> str:
    """Extract the pin tokens.css's own header comment claims."""
    match = _HEADER_PIN_RE.search(tokens_css)
    if not match:
        raise CheckFailure("tokens.css header comment is missing a 'Pinned commit: <sha>' line")
    return match.group(1)


def split_tokens_css(tokens_css: str) -> tuple[str, str]:
    """Split tokens.css into (header, verbatim_body).

    The verbatim body is everything after the header comment's own closing
    ``*/`` line, found via the sentinel comment inside the header itself.
    """
    sentinel_idx = tokens_css.find(_SENTINEL)
    if sentinel_idx < 0:
        raise CheckFailure(
            f'tokens.css is missing the verbatim-copy sentinel comment ("{_SENTINEL}")'
        )
    close_idx = tokens_css.find("*/", sentinel_idx)
    if close_idx < 0:
        raise CheckFailure("tokens.css header comment is never closed with */")
    after_close = tokens_css.find("\n", close_idx)
    if after_close < 0:
        raise CheckFailure("tokens.css has no content after its header comment")
    header = tokens_css[: after_close + 1]
    body = tokens_css[after_close + 1 :]
    return header, body


def _org_repo() -> str:
    import os

    return os.environ.get(_ORG_REPO_ENV, _DEFAULT_ORG_REPO)


def read_org_file_at_pin(rel_path: str, pin: str, *, org_repo: str | None = None) -> str:
    """Read *rel_path* from the org repo AT *pin*, via ``git show`` — never
    org's working tree, so this is stable even after org's HEAD moves on."""
    repo = org_repo if org_repo is not None else _org_repo()
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, git on PATH by design
            ["git", "-C", repo, "show", f"{pin}:{rel_path}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise CheckFailure(f"git is not on PATH: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise CheckFailure(
            f"git show {pin}:{rel_path} in {repo} failed: {exc.stderr.strip()}"
        ) from exc
    return result.stdout


def run_checks(*, org_repo: str | None = None) -> list[tuple[str, bool, str]]:
    """Run every check and return a list of (label, passed, detail) tuples.

    Never raises for an expected failure mode (a missing file, a hash
    mismatch, a missing pin line) — each of those becomes a failed check in
    the returned list. Only a genuinely unexpected error propagates.
    """
    results: list[tuple[str, bool, str]] = []

    if not README_PATH.is_file():
        return [("README.md exists", False, f"missing: {README_PATH}")]
    if not TOKENS_CSS_PATH.is_file():
        return [("tokens.css exists", False, f"missing: {TOKENS_CSS_PATH}")]

    readme_text = README_PATH.read_text(encoding="utf-8")
    tokens_css = TOKENS_CSS_PATH.read_text(encoding="utf-8")

    try:
        pin = read_pin_from_readme(readme_text)
    except CheckFailure as exc:
        return [("README.md declares a pin", False, str(exc))]
    results.append(("README.md declares a pin", True, pin))

    try:
        header_pin = read_pin_from_tokens_header(tokens_css)
        if header_pin != pin:
            raise CheckFailure(f"tokens.css header says {header_pin}, README says {pin}")
        results.append(("tokens.css header pin matches README pin", True, ""))
    except CheckFailure as exc:
        results.append(("tokens.css header pin matches README pin", False, str(exc)))

    try:
        _, body = split_tokens_css(tokens_css)
        org_source = read_org_file_at_pin(_ORG_TOKENS_PATH, pin, org_repo=org_repo)
        if body != org_source:
            raise CheckFailure(f"tokens.css body differs from org {_ORG_TOKENS_PATH}@{pin[:12]}")
        results.append(
            (f"tokens.css is byte-identical to org {_ORG_TOKENS_PATH}@{pin[:12]}", True, ""),
        )
    except CheckFailure as exc:
        results.append(
            (f"tokens.css is byte-identical to org {_ORG_TOKENS_PATH}@{pin[:12]}", False, str(exc)),
        )

    return results


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI flags today; kept for a conventional main() signature
    print(f"culture-design check — org repo: {_org_repo()}")
    results = run_checks()
    failures = 0
    for label, passed, detail in results:
        if passed:
            print(f"ok   - {label}" + (f" ({detail})" if detail else ""))
        else:
            failures += 1
            print(f"FAIL - {label}")
            if detail:
                print(f"       {detail}")
    print()
    print(f"{len(results) - failures}/{len(results)} checks passed")
    if failures:
        print(f"{failures} check(s) FAILED")
        return 1
    print("culture-design check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
