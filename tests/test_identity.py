"""Tests for the resolved-identity seam (task t11).

``embodiment.identity`` resolves a teammate identity (e.g. "Gwen") for a
*consuming rig* — mirroring colleague's ``identity.py`` resolution order
(culture.yaml top-level ``nick:``, else the first agent block's ``suffix:``,
then ``.colleague/identity.json``'s ``"as"`` key, repo before user-home) —
without importing colleague and without any third-party/PyYAML dependency.

Two acceptance criteria drive this file (see
``docs/specs/2026-07-24-gwen-loop-presence-continuity.md``):

1. Identity resolves *only* from explicit host configuration. It is NEVER
   inferred from a model name — ``resolve_identity`` never reads a ``model:``
   field at all. A test swaps model names (and, separately, plants a
   model-shaped identity string in a ``model:`` field with no nick/suffix
   anywhere) and asserts the resolved identity is unaffected / absent.
2. embodiment's own ``whoami``/``doctor`` CLI output is byte-identical
   whether or not a Gwen configuration exists in some *other*,
   consuming-rig fixture directory — because those commands only ever read
   *this* repo's own ``culture.yaml`` (``find_culture_yaml()`` walks up from
   ``__file__``, never from the caller's cwd or an arbitrary path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.identity import (
    resolve_identity,
    scalar_value,
    scan_first_agent_suffix,
    scan_top_level_nick,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    return repo


def _home(tmp_path: Path, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir()
    return home


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_identity_json(root: Path, as_value: object) -> None:
    identity_file = root / ".colleague" / "identity.json"
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.write_text(json.dumps({"as": as_value}), encoding="utf-8")


# ---------------------------------------------------------------------------
# resolve_identity — culture.yaml top-level nick: (primary source)
# ---------------------------------------------------------------------------


def test_resolve_identity_from_culture_yaml_nick(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick: gwen\nsome_other: field\n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


def test_resolve_identity_nick_strips_whitespace_and_quotes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick:   'gwen'   \n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


def test_resolve_identity_nick_found_mid_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "version: 1\nmesh: main\nnick: gwen\n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


def test_resolve_identity_indented_nick_is_not_top_level(tmp_path: Path) -> None:
    """A nested ``nick:`` (e.g. under ``agents:``) must not count as top-level."""
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "agents:\n  - nick: nested\n    backend: colleague\n")

    # No top-level nick, no suffix: — falls through to nothing (identity.json absent).
    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


def test_resolve_identity_empty_nick_falls_through(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick:\n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


# ---------------------------------------------------------------------------
# resolve_identity — first agent block's suffix: (the canonical template shape)
# ---------------------------------------------------------------------------


def test_resolve_identity_from_agent_suffix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(
        repo / "culture.yaml",
        "agents:\n- suffix: gwen\n  backend: colleague\n  model: whatever-model\n",
    )

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


def test_resolve_identity_top_level_nick_wins_over_suffix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick: top-nick\nagents:\n- suffix: agent-suffix\n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "top-nick"


def test_resolve_identity_suffix_first_agent_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(
        repo / "culture.yaml",
        "agents:\n- suffix: first\n  backend: colleague\n- suffix: second\n",
    )

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "first"


def test_resolve_identity_suffix_strips_quotes_and_whitespace(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "agents:\n- suffix:  'gwen' \n")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


# ---------------------------------------------------------------------------
# resolve_identity — .colleague/identity.json fallback (secondary source)
# ---------------------------------------------------------------------------


def test_resolve_identity_from_identity_json(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_identity_json(repo, "gwen")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "gwen"


def test_resolve_identity_culture_yaml_wins_over_identity_json(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick: yaml-nick\n")
    _write_identity_json(repo, "json-nick")

    assert resolve_identity(repo, user_home=_home(tmp_path)) == "yaml-nick"


def test_resolve_identity_identity_json_missing_as_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identity_file = repo / ".colleague" / "identity.json"
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.write_text(json.dumps({"other": "stuff"}), encoding="utf-8")

    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


def test_resolve_identity_identity_json_empty_as_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_identity_json(repo, "")

    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


def test_resolve_identity_identity_json_malformed_degrades_to_none(tmp_path: Path) -> None:
    """Never raises: malformed JSON degrades to None, matching the never-raise rule."""
    repo = _repo(tmp_path)
    identity_file = repo / ".colleague" / "identity.json"
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.write_text("{not valid json", encoding="utf-8")

    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


def test_resolve_identity_user_level_identity_json(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    home = _home(tmp_path)
    _write_identity_json(home, "user-gwen")

    assert resolve_identity(repo, user_home=home) == "user-gwen"


def test_resolve_identity_repo_identity_json_shadows_user(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    home = _home(tmp_path)
    _write_identity_json(repo, "repo-gwen")
    _write_identity_json(home, "user-gwen")

    assert resolve_identity(repo, user_home=home) == "repo-gwen"


# ---------------------------------------------------------------------------
# resolve_identity — nothing present
# ---------------------------------------------------------------------------


def test_resolve_identity_none_when_nothing_present(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert resolve_identity(repo, user_home=_home(tmp_path)) is None


def test_resolve_identity_default_user_home_is_not_required(tmp_path: Path) -> None:
    """user_home is optional; omitting it must not raise (defaults to Path.home())."""
    repo = _repo(tmp_path)
    _write(repo / "culture.yaml", "nick: gwen\n")

    assert resolve_identity(repo) == "gwen"


# ---------------------------------------------------------------------------
# THE load-bearing invariant: identity is NEVER inferred from a model name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_a,model_b",
    [
        ("sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP", "nvidia/Gemma-4-31B-IT-NVFP4"),
        ("nvidia/Gemma-4-31B-IT-NVFP4", "coolthor/gemma-4-12B-it-NVFP4A16"),
        ("some-model-a", "completely-different-model-b"),
    ],
)
def test_resolve_identity_unaffected_by_model_name_change(
    tmp_path: Path, model_a: str, model_b: str
) -> None:
    """Swapping the model name must never change the resolved identity.

    This is the proof for build-brief requirement 37/h — "Gwen" is a
    configured nick, and the code must never derive it (or anything else)
    from a model string.
    """
    repo = _repo(tmp_path)
    home = _home(tmp_path)
    _write(
        repo / "culture.yaml",
        f"agents:\n- suffix: gwen\n  backend: colleague\n  model: {model_a}\n",
    )

    before = resolve_identity(repo, user_home=home)

    _write(
        repo / "culture.yaml",
        f"agents:\n- suffix: gwen\n  backend: colleague\n  model: {model_b}\n",
    )

    after = resolve_identity(repo, user_home=home)

    assert before == "gwen"
    assert after == "gwen"
    assert before == after


def test_resolve_identity_never_reads_the_model_field_as_identity(tmp_path: Path) -> None:
    """A model value that itself looks like an identity must never be adopted.

    culture.yaml here has a ``model:`` field whose value IS the string "Gwen"
    but has no ``nick:`` and no agent ``suffix:`` anywhere, and no
    identity.json exists either. If resolve_identity ever fell back to
    parsing/guessing from ``model:``, this would incorrectly return "Gwen".
    It must return None instead — proof that model text is never a fallback
    identity source.
    """
    repo = _repo(tmp_path)
    home = _home(tmp_path)
    _write(repo / "culture.yaml", "agents:\n- backend: colleague\n  model: Gwen\n")

    assert resolve_identity(repo, user_home=home) is None


def test_resolve_identity_model_field_never_parsed_even_when_gwen_shaped(tmp_path: Path) -> None:
    """Belt-and-suspenders: a model string built from 'G' + 'wen' fragments.

    Mirrors the literal Gwen = G(emma) + wen(Qwen) naming joke to make sure no
    substring-matching heuristic over the model field could ever produce an
    identity. No nick/suffix/identity.json present -> must resolve to None.
    """
    repo = _repo(tmp_path)
    home = _home(tmp_path)
    _write(
        repo / "culture.yaml",
        "agents:\n- backend: colleague\n  model: G-from-Gemma-wen-from-Qwen\n",
    )

    assert resolve_identity(repo, user_home=home) is None


# ---------------------------------------------------------------------------
# Low-level scan helpers — shared with whoami.py (no third parser)
# ---------------------------------------------------------------------------


def test_scan_top_level_nick_direct() -> None:
    assert scan_top_level_nick("nick: gwen\n") == "gwen"
    assert scan_top_level_nick("agents:\n  nick: nested\n") is None
    assert scan_top_level_nick("nick:\n") is None
    assert scan_top_level_nick("") is None


def test_scan_first_agent_suffix_direct() -> None:
    assert scan_first_agent_suffix("agents:\n- suffix: gwen\n") == "gwen"
    assert scan_first_agent_suffix("agents:\n- suffix: first\n- suffix: second\n") == "first"
    assert scan_first_agent_suffix("agents:\n- backend: colleague\n") is None


def test_scalar_value_direct() -> None:
    assert scalar_value("nick: gwen", "nick") == "gwen"
    assert scalar_value("nick:   'gwen'  ", "nick") == "gwen"
    assert scalar_value("nick:", "nick") is None
    assert scalar_value('nick: "quoted"', "nick") == "quoted"


# ---------------------------------------------------------------------------
# Criterion 2 — embodiment's own whoami/doctor is unaffected by a
# consuming-rig's Gwen configuration.
# ---------------------------------------------------------------------------


def _consuming_rig_fixture(tmp_path: Path) -> Path:
    """Build a fixture consuming-rig directory with its own Gwen configuration.

    This is a DIFFERENT repo from embodiment's own checkout — it has its own
    culture.yaml (a different nick, different backend, different model) and
    its own .colleague/identity.json naming "Gwen". It stands in for "some
    other application that has embodiment installed and has configured Gwen".
    """
    rig = tmp_path / "consuming-rig"
    rig.mkdir()
    _write(
        rig / "culture.yaml",
        "agents:\n- suffix: totally-different-nick\n  backend: acp\n  model: some-other-model\n",
    )
    _write_identity_json(rig, "Gwen")
    return rig


def test_consuming_rig_fixture_resolves_its_own_gwen_identity(tmp_path: Path) -> None:
    """Sanity check: the fixture itself DOES carry a distinguishable identity.

    Establishes that the fixture is not accidentally a no-op before using it
    to prove embodiment's own whoami/doctor ignore it.
    """
    rig = _consuming_rig_fixture(tmp_path)
    assert resolve_identity(rig, user_home=_home(tmp_path)) == "totally-different-nick"


def test_whoami_text_byte_identical_with_and_without_gwen_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_cwd = Path.cwd()

    rc_before = main(["whoami"])
    assert rc_before == 0
    before = capsys.readouterr().out

    rig = _consuming_rig_fixture(tmp_path)
    monkeypatch.chdir(rig)
    try:
        rc_after = main(["whoami"])
    finally:
        monkeypatch.chdir(original_cwd)
    assert rc_after == 0
    after = capsys.readouterr().out

    assert after == before


def test_whoami_json_byte_identical_with_and_without_gwen_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_cwd = Path.cwd()

    rc_before = main(["whoami", "--json"])
    assert rc_before == 0
    before = capsys.readouterr().out

    rig = _consuming_rig_fixture(tmp_path)
    monkeypatch.chdir(rig)
    try:
        rc_after = main(["whoami", "--json"])
    finally:
        monkeypatch.chdir(original_cwd)
    assert rc_after == 0
    after = capsys.readouterr().out

    assert after == before
    payload = json.loads(after)
    assert payload["nick"] == "embodiment"


def test_doctor_json_byte_identical_with_and_without_gwen_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_cwd = Path.cwd()

    rc_before = main(["doctor", "--json"])
    before_out = capsys.readouterr().out

    rig = _consuming_rig_fixture(tmp_path)
    monkeypatch.chdir(rig)
    try:
        rc_after = main(["doctor", "--json"])
    finally:
        monkeypatch.chdir(original_cwd)
    after_out = capsys.readouterr().out

    assert rc_after == rc_before
    assert after_out == before_out


def test_doctor_text_byte_identical_with_and_without_gwen_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_cwd = Path.cwd()

    rc_before = main(["doctor"])
    before_out = capsys.readouterr().out

    rig = _consuming_rig_fixture(tmp_path)
    monkeypatch.chdir(rig)
    try:
        rc_after = main(["doctor"])
    finally:
        monkeypatch.chdir(original_cwd)
    after_out = capsys.readouterr().out

    assert rc_after == rc_before
    assert after_out == before_out
