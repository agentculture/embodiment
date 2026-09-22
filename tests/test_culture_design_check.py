"""Tests for scripts/check-culture-design.py (plan task t17, spec target c21).

Acceptance criterion #2 (verbatim): "the design-token check script passes
against the pinned `org` commit and fails on a one-byte edit."

These tests import the script as a module (it lives outside the
``embodiment`` package, under ``scripts/``, by design — it is a standalone
checker, not part of the library host apps import) and exercise it against
a temporary copy of ``web/src/culture-design/`` so a real edit never touches
the committed file, and against a fake "org repo" (a throwaway git repo
this test creates) so the test never depends on ``/home/spark/git/org``
actually being present on the machine running the suite.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess  # nosec B404 - fixed argv, no shell, test-only git repo setup
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-culture-design.py"
REAL_TOKENS_CSS = REPO_ROOT / "web" / "src" / "culture-design" / "tokens.css"
REAL_README = REPO_ROOT / "web" / "src" / "culture-design" / "README.md"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_culture_design", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None  # nosec B101 - test setup invariant
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def script() -> ModuleType:
    return _load_script()


@pytest.fixture()
def org_repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway git repo standing in for /home/spark/git/org, with one
    commit holding the pinned source path. Returns (repo_dir, commit_sha)."""
    repo = tmp_path / "org"
    repo.mkdir()
    (repo / "site-astro" / "src" / "styles").mkdir(parents=True)
    source = repo / "site-astro" / "src" / "styles" / "global.css"
    source.write_text(":root {\n  --bg: #f4f5fb;\n}\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(  # nosec B603 B607 - fixed argv, no shell, test fixture
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    sha = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _write_culture_design(
    root: Path, *, pin: str, header_pin: str | None = None, body: str
) -> None:
    culture_design = root / "web" / "src" / "culture-design"
    culture_design.mkdir(parents=True, exist_ok=True)
    (culture_design / "README.md").write_text(
        f"# culture-design\n\n## Pinned commit\n\npin: {pin}\n", encoding="utf-8"
    )
    hp = header_pin if header_pin is not None else pin
    tokens = (
        "/*\n"
        " * culture-design/tokens.css\n"
        f" * Pinned commit:  {hp}\n"
        " * ---- verbatim copy of site-astro/src/styles/global.css follows ----\n"
        " */\n"
        f"{body}"
    )
    (culture_design / "tokens.css").write_text(tokens, encoding="utf-8")


def test_committed_tokens_css_is_a_verbatim_copy_with_sentinel() -> None:
    """The real, committed tokens.css has the sentinel + a Pinned commit line
    -- a basic shape assertion independent of network/git access."""
    text = REAL_TOKENS_CSS.read_text(encoding="utf-8")
    assert "---- verbatim copy of site-astro/src/styles/global.css follows ----" in text
    assert "Pinned commit:" in text


def test_committed_readme_declares_the_pin() -> None:
    text = REAL_README.read_text(encoding="utf-8")
    assert re.search(r"^pin:\s+b4d939ba0aa354a5ae53065319a773e0013de698\s*$", text, re.MULTILINE)


def test_read_pin_from_readme(script: ModuleType) -> None:
    pin = script.read_pin_from_readme("# x\n\npin: " + "a" * 40 + "\n")
    assert pin == "a" * 40


def test_read_pin_from_readme_missing_raises_check_failure(script: ModuleType) -> None:
    with pytest.raises(script.CheckFailure):
        script.read_pin_from_readme("# no pin here\n")


def test_read_pin_from_tokens_header(script: ModuleType) -> None:
    pin = script.read_pin_from_tokens_header(f"/*\n * Pinned commit: {'b' * 40}\n */\n")
    assert pin == "b" * 40


def test_split_tokens_css_separates_header_and_body(script: ModuleType) -> None:
    css = (
        "/*\n"
        " * header\n"
        " * ---- verbatim copy of site-astro/src/styles/global.css follows ----\n"
        " */\n"
        ":root { --x: 1; }\n"
    )
    header, body = script.split_tokens_css(css)
    assert "sentinel" not in header  # just documenting: no crash, sane split
    assert body == ":root { --x: 1; }\n"
    assert header + body == css


def test_split_tokens_css_without_sentinel_raises(script: ModuleType) -> None:
    with pytest.raises(script.CheckFailure):
        script.split_tokens_css("no header here at all\n")


def test_run_checks_passes_when_body_matches_org_at_pin(
    script: ModuleType, org_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, sha = org_repo
    org_body = (repo / "site-astro" / "src" / "styles" / "global.css").read_text(encoding="utf-8")
    fake_repo_root = tmp_path / "embodiment"
    _write_culture_design(fake_repo_root, pin=sha, body=org_body)

    module = _load_script()
    module.CULTURE_DESIGN_DIR = fake_repo_root / "web" / "src" / "culture-design"
    module.README_PATH = module.CULTURE_DESIGN_DIR / "README.md"
    module.TOKENS_CSS_PATH = module.CULTURE_DESIGN_DIR / "tokens.css"

    results = module.run_checks(org_repo=str(repo))
    assert results, "expected at least one check result"
    assert all(passed for _, passed, _ in results), results


def test_run_checks_fails_on_a_one_byte_edit(
    script: ModuleType, org_repo: tuple[Path, str], tmp_path: Path
) -> None:
    """Acceptance criterion #2's second half: a one-byte edit to the copied
    body must fail the check."""
    repo, sha = org_repo
    org_body = (repo / "site-astro" / "src" / "styles" / "global.css").read_text(encoding="utf-8")
    edited_body = org_body[:-2] + "X" + org_body[-1]  # flip exactly one byte
    assert edited_body != org_body
    assert len(edited_body) == len(org_body)

    fake_repo_root = tmp_path / "embodiment"
    _write_culture_design(fake_repo_root, pin=sha, body=edited_body)

    module = _load_script()
    module.CULTURE_DESIGN_DIR = fake_repo_root / "web" / "src" / "culture-design"
    module.README_PATH = module.CULTURE_DESIGN_DIR / "README.md"
    module.TOKENS_CSS_PATH = module.CULTURE_DESIGN_DIR / "tokens.css"

    results = module.run_checks(org_repo=str(repo))
    assert any(
        not passed for _, passed, _ in results
    ), "a one-byte edit to the copied body must fail at least one check"


def test_run_checks_fails_when_header_pin_disagrees_with_readme(
    script: ModuleType, org_repo: tuple[Path, str], tmp_path: Path
) -> None:
    repo, sha = org_repo
    org_body = (repo / "site-astro" / "src" / "styles" / "global.css").read_text(encoding="utf-8")
    other_sha = "0" * 40
    fake_repo_root = tmp_path / "embodiment"
    _write_culture_design(fake_repo_root, pin=sha, header_pin=other_sha, body=org_body)

    module = _load_script()
    module.CULTURE_DESIGN_DIR = fake_repo_root / "web" / "src" / "culture-design"
    module.README_PATH = module.CULTURE_DESIGN_DIR / "README.md"
    module.TOKENS_CSS_PATH = module.CULTURE_DESIGN_DIR / "tokens.css"

    results = module.run_checks(org_repo=str(repo))
    labels_failed = {label for label, passed, _ in results if not passed}
    assert "tokens.css header pin matches README pin" in labels_failed


def test_run_checks_missing_readme_fails_without_raising(
    script: ModuleType, tmp_path: Path
) -> None:
    module = _load_script()
    module.CULTURE_DESIGN_DIR = tmp_path / "nope"
    module.README_PATH = module.CULTURE_DESIGN_DIR / "README.md"
    module.TOKENS_CSS_PATH = module.CULTURE_DESIGN_DIR / "tokens.css"

    results = module.run_checks()
    assert results and results[0][1] is False


def test_main_against_the_real_committed_layer_and_the_real_org_checkout() -> None:
    """End-to-end: run the actual script, unmodified paths, against the real
    committed tokens.css and the real /home/spark/git/org sibling checkout
    at its pinned commit -- exactly what a human runs from the CLI. Skips if
    the org sibling checkout isn't present on this machine (CI/other hosts)."""
    if not (Path("/home/spark/git/org") / ".git").exists():
        pytest.skip("sibling checkout /home/spark/git/org not present on this machine")
    module = _load_script()
    results = module.run_checks()
    assert all(passed for _, passed, _ in results), results


def test_git_show_unreadable_repo_raises_check_failure(script: ModuleType, tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(script.CheckFailure):
        script.read_org_file_at_pin("some/path.css", "a" * 40, org_repo=str(not_a_repo))
