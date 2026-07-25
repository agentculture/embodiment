"""Tests for the ``greenhouse`` demo host (task t15).

The demo is issue #2's definition of done — *"a demonstration in a
non-colleague app"* — so these tests are the acceptance criteria, not
decoration:

* **Two separate process runs, and the second recalls the first.** Proved by
  spawning two real interpreters (:class:`subprocess`) against one store, never
  two calls in one process: continuity that only holds in-memory is not
  continuity. The recall is proved *load-bearing* three independent ways —
  the host's own recall returns run 1's record id, run 2 acts on a fact
  (the sensor id) that exists nowhere but run 1's memory, and run 2's durable
  record ``links`` back to run 1's. The control experiment runs the same
  utterance against an EMPTY store and shows the mind refusing for want of the
  fact.
* **Public API only.** An AST guard over ``examples/`` — every import root is
  stdlib or ``embodiment``, every imported ``embodiment`` name is in
  :data:`embodiment.__all__`, no ``_``-prefixed access, no ``colleague``.
* **Followable from the README alone.** Every ``greenhouse.py`` command line in
  ``README.md`` is parsed with the demo's own argument parser, so a renamed
  flag breaks the build rather than the reader.
* **Hermetic by default, live by explicit opt-in.** The default path never
  dials: :func:`urllib.request.urlopen` is replaced with a bomb for the whole
  in-process suite. The live rig test is *skipped* — never failed — unless
  ``EMBODIMENT_LIVE_RIG=1`` and ``COLLEAGUE_API_KEY`` are both present and the
  gateway actually answers.
* **Store hygiene.** This repo's own committed ``.eidetic/`` store is compared
  byte-for-byte before and after, mirroring ``tests/test_continuity.py``'s
  trap-#1 acceptance criterion.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex

# The two-process acceptance criterion needs real processes, not two calls
# in one interpreter.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

import embodiment
from examples import greenhouse

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
DEMO = EXAMPLES / "greenhouse.py"
README = REPO_ROOT / "README.md"

#: The two utterances the README walks a reader through.
VISIT_ONE = (
    "New plant card - name: Marlow; sensor: s-fig-01; water below: 30% moisture. "
    "It is the fig by the north window. Check it in and log the visit."
)
VISIT_TWO = "Does Marlow need water today?"

#: The fact that exists ONLY in run 1's memory by the time run 2 starts.
CARRIED_FACT = "s-fig-01"


# ── helpers ───────────────────────────────────────────────────────────────────


def _demo(
    home: Path,
    utterance: str,
    *extra: str,
    expect: int = 0,
    cwd: Optional[Path] = None,
    live: bool = False,
) -> dict[str, Any]:
    """Run the demo in a REAL separate process and return its JSON report."""
    env = dict(os.environ)
    # The demo pins the store per call, but a developer's ambient override would
    # still muddy what this test is proving. Drop it — and drop the API key too
    # unless this is the live lane, so the hermetic path is proved not to need
    # one even on a developer machine that has the real rig's key exported.
    env.pop("EIDETIC_DATA_DIR", None)
    if not live:
        env.pop(greenhouse.API_KEY_ENV, None)
    cmd = [sys.executable, str(DEMO), "--home", str(home), "--json", *extra, utterance]
    # Fixed argv, no shell, interpreter taken from sys.executable.
    proc = subprocess.run(  # nosec B603
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd or home.parent),
        env=env,
        timeout=300,
        check=False,
    )
    detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.returncode == expect, detail
    return json.loads(proc.stdout)


def _tools_used(report: dict[str, Any]) -> list[str]:
    return [step["tool"] for step in report["loop"]["steps"]]


def _arguments_for(report: dict[str, Any], tool: str) -> dict[str, Any]:
    for step in report["loop"]["steps"]:
        if step["tool"] == tool:
            return step["arguments"]
    raise AssertionError(f"{tool} was never called: {_tools_used(report)}")


def _events(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e for e in report["continuity"]["events"] if e["kind"] == kind]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No in-process test may dial anything. Live classes opt out by name.

    Matched on the ``TestLive`` prefix rather than one exact class: a live class
    named anything else silently keeps the bomb, and its "live" assertions then
    pass or fail against the fixture instead of a real endpoint — which is how
    a dead-endpoint test can go green without ever dialling one.
    """
    if "TestLive" in request.node.nodeid:
        return
    import urllib.request

    def _bomb(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the hermetic path must never dial a network")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)


# ── the acceptance criterion: two processes, and the second recalls the first ──


class TestContinuityAcrossTwoProcesses:
    """Run 1 learns something; run 2, in a different interpreter, uses it."""

    def test_second_process_recalls_and_acts_on_the_first(self, tmp_path: Path) -> None:
        home = tmp_path / "greenhouse"

        first = _demo(home, VISIT_ONE)
        second = _demo(home, VISIT_TWO, "--moisture", "22")

        # Two genuinely different processes.
        assert first["pid"] != second["pid"]
        assert first["task_id"] == "visit-1"
        assert second["task_id"] == "visit-2"

        # 1. Run 1 wrote a durable record; run 2's host recall returned it.
        remembered = first["continuity"]["remembered"]
        assert remembered is not None, first["continuity"]["events"]
        first_record = remembered["record_id"]
        assert first_record in second["continuity"]["recalled"]
        assert any(CARRIED_FACT in text for text in second["continuity"]["recalled_text"])

        # 2. The recall was LOAD-BEARING: run 2 read a sensor whose id it could
        #    only have learned from run 1. The utterance never names it.
        assert CARRIED_FACT not in VISIT_TWO
        assert _arguments_for(second, "read_sensor")["sensor"] == CARRIED_FACT
        assert _tools_used(second) == ["read_sensor", "log_care", "finish"]

        # 3. The lived sequence is durable too: run 2's record links to run 1's.
        second_remembered = second["continuity"]["remembered"]
        assert second_remembered is not None
        assert first_record in second_remembered["links"]

        # And the threshold came across, so run 2 decided to water.
        assert _arguments_for(second, "log_care")["action"] == "watered"
        assert "22%" in second["loop"]["summary"]

    def test_the_control_experiment_an_empty_store_cannot_answer(self, tmp_path: Path) -> None:
        """The same second utterance, with nothing remembered, must fail honestly.

        Without this, "run 2 knew the sensor id" proves nothing — the mind could
        have had it hard-coded all along.
        """
        report = _demo(tmp_path / "amnesiac", VISIT_TWO, "--moisture", "22")

        assert report["continuity"]["recalled"] == []
        assert _tools_used(report) == ["finish"]
        assert "no plant card" in report["loop"]["summary"].lower()
        assert CARRIED_FACT not in json.dumps(report["loop"])

    def test_the_store_is_a_real_file_the_second_process_reads(self, tmp_path: Path) -> None:
        """Continuity rides the filesystem, not a shared object."""
        home = tmp_path / "greenhouse"
        first = _demo(home, VISIT_ONE)

        store = Path(first["store"])
        assert store.is_dir()
        written = [p for p in store.rglob("*") if p.is_file()]
        assert written, "no memory file was written"
        blob = "\n".join(p.read_text(encoding="utf-8") for p in written)
        assert CARRIED_FACT in blob
        assert first["continuity"]["remembered"]["record_id"] in blob

    def test_provenance_survives_the_process_boundary(self, tmp_path: Path) -> None:
        """The verbatim request is what the durable record carries.

        Read back through the same public seam a host would use, so this asserts
        on the contract rather than on eidetic's on-disk layout.
        """
        home = tmp_path / "greenhouse"
        _demo(home, VISIT_ONE)

        outcome = embodiment.continuity.recall(
            "Marlow",
            data_dir=home / "memory",
            scope=greenhouse.SCOPE,
            mode=greenhouse.RECALL_MODE,
        )
        assert outcome.ok and outcome.records
        record = outcome.records[0]
        # The operator's own words, byte for byte — never a model's rewording.
        assert record["metadata"]["request"] == VISIT_ONE
        assert record["added_by"] == greenhouse.ADDED_BY
        assert record["scope"]["name"] == greenhouse.SCOPE
        assert record["type"] == greenhouse.RECORD_TYPE
        assert record["metadata"]["model"] == greenhouse.SCRIPTED_CORTEX


# ── store hygiene: never this repo's own committed memory ─────────────────────


class TestStoreHygiene:
    """The demo writes under its own home and nowhere else."""

    @staticmethod
    def _ambient() -> dict[str, str]:
        root = REPO_ROOT / ".eidetic"
        if not root.exists():
            return {}
        return {
            str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    def test_this_repo_s_own_store_is_byte_unchanged(self, tmp_path: Path) -> None:
        before = self._ambient()
        # Run from INSIDE the repo, which is exactly the cwd that would make an
        # unanchored public record land in <repo-root>/.eidetic/memory.
        _demo(tmp_path / "greenhouse", VISIT_ONE, cwd=REPO_ROOT)
        assert self._ambient() == before, "the demo leaked into this repo's own store"

    def test_the_default_home_is_outside_the_repo(self) -> None:
        default = greenhouse.default_home()
        assert not str(default.resolve()).startswith(str(REPO_ROOT.resolve()))

    def test_the_lifecycle_anchor_is_the_demo_s_own_directory(self, tmp_path: Path) -> None:
        config = greenhouse.lifecycle_config(tmp_path)
        assert Path(str(config.data_dir)) == tmp_path / "memory"
        assert config.scope == greenhouse.SCOPE


# ── public API only ───────────────────────────────────────────────────────────


def _demo_sources() -> list[Path]:
    return sorted(EXAMPLES.rglob("*.py"))


def _imports(tree: ast.AST) -> list[tuple[str, tuple[str, ...]]]:
    """Every import as ``(module, imported names)``."""
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", tuple(a.name for a in node.names)))
    return found


class TestPublicApiOnly:
    """No private reach-through, and nothing colleague-shaped."""

    def test_every_import_root_is_stdlib_or_embodiment(self) -> None:
        for path in _demo_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, _names in _imports(tree):
                root = module.split(".")[0]
                allowed = root in sys.stdlib_module_names or root == "embodiment"
                assert allowed, f"{path.name} imports {module!r}: not stdlib, not embodiment"

    def test_every_embodiment_name_is_on_the_curated_surface(self) -> None:
        surface = set(embodiment.__all__)
        for path in _demo_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, names in _imports(tree):
                if module.split(".")[0] != "embodiment":
                    continue
                submodule = module.partition(".")[2]
                if submodule:
                    assert submodule in surface, f"{module} is not a documented submodule"
                for name in names:
                    assert not name.startswith("_"), f"{path.name} imports private {name!r}"
                    if not submodule:
                        assert name in surface, f"{name!r} is not exported by embodiment"
                        assert getattr(embodiment, name) is not None

    def test_no_private_attribute_access_on_embodiment(self) -> None:
        """``embodiment.<sub>._thing`` is the other way a private could leak in."""
        roots = {"embodiment", *(name for name in embodiment.__all__ if not name.startswith("_"))}
        for path in _demo_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
                    continue
                owner = node.value
                named = isinstance(owner, ast.Name) and owner.id in roots
                assert not named, f"{path.name}: private access {owner.id}.{node.attr}"

    def test_nothing_colleague_shaped_appears_as_code(self) -> None:
        for path in _demo_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] != "colleague"
                elif isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[0] != "colleague"
                elif isinstance(node, ast.Name):
                    assert node.id != "colleague"

    def test_the_demo_stays_within_the_repo_s_line_length(self) -> None:
        """CI lints ``embodiment tests``; this keeps ``examples`` honest too."""
        for path in _demo_sources():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                assert len(line) <= 100, f"{path.name}:{number} is {len(line)} chars"


# ── the scripted mind is a function of its prompt, not a fixture ──────────────


class TestScriptedMindNeedsTheMemory:
    """The hermetic mind reads the card out of its prompt or admits it cannot."""

    @staticmethod
    def _messages(text: str) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": greenhouse.BASE_SYSTEM},
            {"role": "user", "content": text},
        ]

    def test_without_a_card_it_refuses(self) -> None:
        response = greenhouse.scripted_cortex(self._messages(VISIT_TWO))
        assert response.tool_calls[0].name == "finish"
        assert "no plant card" in response.tool_calls[0].arguments["summary"].lower()

    def test_with_the_card_in_context_it_reads_the_sensor(self) -> None:
        text = (
            f"{VISIT_TWO}\n\nContext:\n"
            f"- name: Marlow; sensor: {CARRIED_FACT}; water below: 30% moisture"
        )
        response = greenhouse.scripted_cortex(self._messages(text))
        assert response.tool_calls[0].name == "read_sensor"
        assert response.tool_calls[0].arguments["sensor"] == CARRIED_FACT


# ── identity framing: absent identity ⇒ byte-identical prompts ────────────────


class TestFraming:
    def test_absent_identity_leaves_the_prompt_byte_identical(self) -> None:
        assert greenhouse.build_system_prompt(None, muse=False) == greenhouse.BASE_SYSTEM
        assert greenhouse.build_system_prompt("", muse=False) == greenhouse.BASE_SYSTEM

    def test_a_configured_identity_only_adds(self) -> None:
        framed = greenhouse.build_system_prompt("Gwen", muse=False)
        assert framed != greenhouse.BASE_SYSTEM
        assert greenhouse.BASE_SYSTEM in framed

    def test_a_single_model_run_claims_no_second_mind(self, tmp_path: Path) -> None:
        report = _demo(tmp_path / "greenhouse", VISIT_ONE)
        assert report["mind"]["muse"] is None
        assert report["mind"]["presence_mode"] == "cortex-only"


# ── the live rig is opt-in, and never something CI can trip into ──────────────


class TestLiveIsOptIn:
    def test_the_default_run_builds_no_network_seam(self, tmp_path: Path) -> None:
        """With ``urlopen`` bombed, a default run still completes in-process."""
        report = greenhouse.visit(
            greenhouse.build_parser().parse_args(["--home", str(tmp_path), VISIT_ONE])
        )
        assert report["mind"]["cortex"] == greenhouse.SCRIPTED_CORTEX
        assert report["loop"]["exit_reason"] == "finished"

    def test_live_needs_the_flag_and_the_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key ⇒ an environment error (exit 2), not a half-configured dial."""
        monkeypatch.delenv(greenhouse.API_KEY_ENV, raising=False)
        args = greenhouse.build_parser().parse_args(["--home", str(tmp_path), "--live", VISIT_ONE])
        assert args.live is True
        with pytest.raises(SystemExit) as excinfo:
            greenhouse.visit(args)
        assert excinfo.value.code == 2

    def test_the_key_alone_never_switches_the_demo_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A developer with the rig key exported still gets the hermetic path."""
        monkeypatch.setenv(greenhouse.API_KEY_ENV, "not-a-real-key")
        report = greenhouse.visit(
            greenhouse.build_parser().parse_args(["--home", str(tmp_path), VISIT_ONE])
        )
        assert report["mind"]["cortex"] == greenhouse.SCRIPTED_CORTEX

    def test_the_key_is_read_from_the_environment_and_never_echoed(self) -> None:
        source = DEMO.read_text(encoding="utf-8")
        assert greenhouse.API_KEY_ENV in source
        report = json.dumps(greenhouse.build_parser().parse_args(["x"]).__dict__)
        assert "sk-" not in report

    def test_no_live_default_anywhere_in_the_parser(self) -> None:
        defaults = greenhouse.build_parser().parse_args(["anything"])
        assert defaults.live is False
        assert defaults.muse is False
        assert defaults.coherence is False


# ── README alignment: an app author must be able to follow it ─────────────────


class TestReadmeTeachesTheDemo:
    #: What a reader actually copies out of the README, verbatim.
    PREFIX = "uv run python examples/greenhouse.py"

    @classmethod
    def _commands(cls) -> list[list[str]]:
        text = README.read_text(encoding="utf-8")
        return [
            shlex.split(line.strip())
            for line in text.splitlines()
            if line.strip().startswith(cls.PREFIX)
        ]

    def test_the_readme_shows_the_two_runs(self) -> None:
        commands = self._commands()
        assert len(commands) >= 2, "the README must show both process runs"

    def test_every_readme_command_parses_with_the_demo_s_own_parser(self) -> None:
        parser = greenhouse.build_parser()
        for command in self._commands():
            index = next(i for i, part in enumerate(command) if part.endswith("greenhouse.py"))
            parser.parse_args(command[index + 1 :])

    def test_the_readme_names_the_public_seams(self) -> None:
        text = README.read_text(encoding="utf-8")
        needles = (
            "build_continuity_fn",
            "LifecycleConfig",
            "data_dir",
            "examples/greenhouse.py",
        )
        for needle in needles:
            assert needle in text, f"README never mentions {needle}"

    def test_the_readme_states_the_live_opt_in(self) -> None:
        text = README.read_text(encoding="utf-8")
        assert "--live" in text
        assert greenhouse.API_KEY_ENV in text


# ── the live rig (deviation d4) — skipped unless explicitly pointed at it ─────


LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get(greenhouse.API_KEY_ENV, "")


def _gateway_answers(base_url: str) -> bool:
    import urllib.error
    import urllib.request

    # Scheme fixed by the caller's default base URL.
    request = urllib.request.Request(  # nosec B310
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {LIVE_KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{greenhouse.API_KEY_ENV} is not set")
class TestLiveRig:
    """The d4 acceptance bar: the same host, against cortex + muse for real.

    Deliberately asserts the parts this host controls — the store, the recall,
    the report — and NOT a fixed tool trajectory. A thinking model chooses its
    own route: an early live run answered the second question straight from
    memory without re-reading the sensor, which is a real finding about prompts
    (the system prompt now says memory is a past visit, never a present
    reading), not a bug in continuity. A live assertion that pins the route
    would be measuring the model's mood.
    """

    def test_two_live_runs_carry_the_card_across(self, tmp_path: Path) -> None:
        base_url = os.environ.get("EMBODIMENT_DEMO_BASE_URL", greenhouse.DEFAULT_BASE_URL)
        if not _gateway_answers(base_url):
            pytest.skip(f"no lobes gateway answering at {base_url}")

        home = tmp_path / "greenhouse"
        first = _demo(home, VISIT_ONE, "--live", "--identity", "Gwen", live=True)
        second = _demo(
            home, VISIT_TWO, "--live", "--identity", "Gwen", "--moisture", "22", live=True
        )

        assert first["mind"]["cortex"] == greenhouse.CORTEX_MODEL
        assert first["pid"] != second["pid"]

        # Deterministic — this is the continuity claim itself, and none of it
        # depends on what the model chose to say: run 1 wrote a durable record,
        # and run 2's own recall brought that record back across the process
        # boundary.
        assert first["continuity"]["remembered"] is not None
        assert first["continuity"]["remembered"]["record_id"] in second["continuity"]["recalled"]
        assert second["continuity"]["recalled_text"] != [""]

        # Model-shaped: SOMETHING only run 1 could know reached run 2. Asserted
        # over the union of what was recalled and what run 2 did, because which
        # of the two facts survives is a prompt-quality question, not a
        # continuity one. Neither appears in run 2's own utterance.
        carried = json.dumps(second["continuity"]["recalled_text"]) + json.dumps(second["loop"])
        assert CARRIED_FACT in carried or "30" in carried, (
            "run 2 saw nothing only run 1 knew — the live summary carried no "
            f"card forward: {carried}"
        )

    def test_the_muse_is_a_second_mind_only_when_asked_for(self, tmp_path: Path) -> None:
        base_url = os.environ.get("EMBODIMENT_DEMO_BASE_URL", greenhouse.DEFAULT_BASE_URL)
        if not _gateway_answers(base_url):
            pytest.skip(f"no lobes gateway answering at {base_url}")

        report = _demo(
            tmp_path / "muse", VISIT_ONE, "--live", "--muse", "--identity", "Gwen", live=True
        )
        assert report["mind"]["muse"] == greenhouse.MUSE_MODEL
        assert report["mind"]["presence_mode"] == "muse"
        assert report["mind"]["muse"] != report["mind"]["cortex"]


# ── the reasoning field the cortex actually emits (d4's measured finding) ─────


class TestGatewayResponseParsing:
    """The live seam's parsing, exercised without a live gateway."""

    def test_reasoning_is_carried_separately_from_content(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "Marlow is fine.",
                        "reasoning_content": "Let me think about the moisture…",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 209},
        }
        response = greenhouse.parse_completion(payload)
        assert response.content == "Marlow is fine."
        assert response.reasoning == "Let me think about the moisture…"
        assert response.completion_tokens == 209

    def test_a_truncated_thinking_turn_is_not_mistaken_for_an_answer(self) -> None:
        """``max_tokens`` too small: content is None while reasoning ran on."""
        payload = {
            "choices": [
                {
                    "message": {"content": None, "reasoning_content": "still thinking"},
                    "finish_reason": "length",
                }
            ]
        }
        response = greenhouse.parse_completion(payload)
        assert response.content == ""
        assert response.reasoning == "still thinking"
        assert response.tool_calls == []

    def test_tool_calls_become_toolcall_objects(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "read_sensor",
                                    "arguments": '{"sensor": "s-fig-01"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        response = greenhouse.parse_completion(payload)
        assert [c.name for c in response.tool_calls] == ["read_sensor"]
        assert response.tool_calls[0].arguments == {"sensor": "s-fig-01"}

    def test_unparsable_arguments_degrade_to_an_empty_mapping(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "finish", "arguments": "{not json"}}
                        ]
                    }
                }
            ]
        }
        response = greenhouse.parse_completion(payload)
        assert response.tool_calls[0].arguments == {}


# ── the app's own tool surface: no shell, no filesystem beyond its own home ───


class TestToolSurface:
    def test_the_three_tools_are_domain_tools(self, tmp_path: Path) -> None:
        assert set(greenhouse.TOOL_SCHEMA_NAMES) == {"read_sensor", "log_care", "finish"}

    def test_an_unknown_tool_is_a_self_correcting_step(self, tmp_path: Path) -> None:
        tools = greenhouse.Greenhouse(tmp_path)
        with pytest.raises(embodiment.UnknownToolError):
            tools.execute("rm", {"path": "/"})

    def test_log_care_writes_only_into_the_demo_home(self, tmp_path: Path) -> None:
        tools = greenhouse.Greenhouse(tmp_path)
        tools.execute("log_care", {"plant": "Marlow", "action": "watered", "note": "22%"})
        written = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert written == [tmp_path / "journal.jsonl"]

    def test_the_sensor_reading_is_deterministic(self, tmp_path: Path) -> None:
        tools = greenhouse.Greenhouse(tmp_path)
        first = tools.execute("read_sensor", {"sensor": CARRIED_FACT}).result
        second = tools.execute("read_sensor", {"sensor": CARRIED_FACT}).result
        assert first == second

    def test_an_unknown_sensor_is_a_tool_error_not_a_crash(self, tmp_path: Path) -> None:
        tools = greenhouse.Greenhouse(tmp_path)
        with pytest.raises(embodiment.ToolError):
            tools.execute("read_sensor", {"sensor": "s-nope-99"})


# ── degradation stays observable to the host (C3) ─────────────────────────────


class TestObservableDegradation:
    def test_the_report_carries_the_continuity_ledger(self, tmp_path: Path) -> None:
        report = _demo(tmp_path / "greenhouse", VISIT_ONE)
        assert report["continuity"]["mode"] in {"full", "partial", "no-continuity"}
        assert _events(report, "mode"), "the continuity mode was never recorded"
        assert _events(report, "remembered"), "the durable write was never recorded"

    def test_coherence_without_an_embedder_degrades_visibly(self, tmp_path: Path) -> None:
        """``--coherence`` is opt-in precisely because it dials an embedder."""
        report = _demo(tmp_path / "greenhouse", VISIT_ONE, "--coherence")
        assessed = _events(report, "assessed") + _events(report, "degraded")
        assert assessed, "an enabled assessment recorded nothing at all"

    def test_coherence_is_off_by_default_so_nothing_is_dialled(self, tmp_path: Path) -> None:
        config = greenhouse.lifecycle_config(tmp_path)
        assert config.assess_action is False
        assert config.assess_completion is False
        assert config.assess_memory is False

    def test_recall_mode_is_offline_by_default(self, tmp_path: Path) -> None:
        """``hybrid`` dials the embedder; the demo's default must not."""
        assert greenhouse.lifecycle_config(tmp_path).recall_mode == "keyword"


# ── text mode stays readable, and streams stay unmixed ───────────────────────


class TestOutputDiscipline:
    def test_results_go_to_stdout_and_presence_to_stderr(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        env.pop("EIDETIC_DATA_DIR", None)
        home = tmp_path / "greenhouse"
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            [sys.executable, str(DEMO), "--home", str(home), VISIT_ONE],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            timeout=180,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Marlow" in proc.stdout
        assert "memory store" in proc.stdout
        # A text run emits no JSON on stdout — the two modes never blend.
        with pytest.raises(json.JSONDecodeError):
            json.loads(proc.stdout)

    def test_json_mode_emits_exactly_one_object(self, tmp_path: Path) -> None:
        report = _demo(tmp_path / "greenhouse", VISIT_ONE)
        assert isinstance(report, dict)
        assert set(report) >= {"store", "task_id", "mind", "continuity", "loop", "pid"}


# ── the module docstring is the demo's own teaching surface ──────────────────


class TestSelfDescribing:
    def test_the_demo_states_the_software_presence_boundary(self) -> None:
        """C2: the name must not be left to imply a robot body."""
        doc = (greenhouse.__doc__ or "").lower()
        assert "not a robot" in doc or "software presence" in doc

    def test_the_demo_names_no_model_it_is_not_running(self, tmp_path: Path) -> None:
        report = _demo(tmp_path / "greenhouse", VISIT_ONE)
        assert greenhouse.CORTEX_MODEL not in json.dumps(report)


def _readme_headings() -> list[str]:
    return re.findall(r"^#{2,3}\s+(.*)$", README.read_text(encoding="utf-8"), re.MULTILINE)


def test_the_readme_has_a_demo_section() -> None:
    assert any("demo" in heading.lower() for heading in _readme_headings())


# ── perception seam: the first real model consumer ──────────────────────────


class TestPerceptionFlag:
    """Hermetic tests for the --perceive wiring (no network)."""

    def test_perceive_flag_is_off_by_default(self) -> None:
        args = greenhouse.build_parser().parse_args(["x"])
        assert args.perceive is False

    def test_perceive_flag_needs_the_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key ⇒ an environment error (exit 2), not a half-configured dial."""
        monkeypatch.delenv(greenhouse.API_KEY_ENV, raising=False)
        args = greenhouse.build_parser().parse_args(
            ["--home", str(tmp_path), "--perceive", VISIT_ONE]
        )
        with pytest.raises(SystemExit) as excinfo:
            greenhouse.visit(args)
        assert excinfo.value.code == 2

    def test_perceive_with_faked_interpret_populates_fields(self, tmp_path: Path) -> None:
        """A faked interpret populates ack/interpretation/confidence/task_type
        while original stays verbatim — the verbatim invariant."""
        from embodiment.perception import perceive

        original = "Check the moisture on Marlow's sensor"

        def fake_interpret(text: str) -> str:
            return json.dumps(
                {
                    "interpretation": "read sensor for plant Marlow",
                    "confidence": 0.9,
                    "task_type": "query",
                    "omissions": ["which sensor"],
                    "ack": "checking Marlow's moisture",
                }
            )

        packet, record = perceive(original, interpret=fake_interpret)

        # Verbatim invariant: original is byte-identical to the caller's input.
        assert packet.original == original
        # The five non-original fields are populated from the model's JSON.
        assert packet.interpretation == "read sensor for plant Marlow"
        assert packet.confidence == 0.9
        assert packet.task_type == "query"
        assert packet.omissions == ["which sensor"]
        assert packet.ack == "checking Marlow's moisture"
        # Record is clean (not degraded).
        assert record.degraded is False

    def test_perceive_with_hostile_model_output_preserves_original(self, tmp_path: Path) -> None:
        """A model that returns a spoofed 'original' key cannot overwrite the packet."""
        from embodiment.perception import perceive

        original = "Water the fig"

        def hostile_interpret(text: str) -> str:
            return json.dumps(
                {
                    "interpretation": "rewritten by model",
                    "confidence": 1.0,
                    "task_type": "task",
                    "omissions": [],
                    "ack": "ok",
                    "original": "completely different text",
                }
            )

        packet, record = perceive(original, interpret=hostile_interpret)
        # The packet's original is STILL the caller's input, not the model's spoof.
        assert packet.original == original
        assert packet.original != "completely different text"

    def test_perceive_with_raising_interpret_degrades(self, tmp_path: Path) -> None:
        """A dead endpoint (simulated by a raising interpret) degrades, never raises."""
        from embodiment.perception import perceive

        original = "Hello greenhouse"

        def broken_interpret(text: str) -> str:
            raise ConnectionRefusedError("simulated dead endpoint")

        packet, record = perceive(original, interpret=broken_interpret)

        # Packet still carries the verbatim original.
        assert packet.original == original
        # All other fields are empty (degraded path).
        assert packet.interpretation == ""
        assert packet.confidence == 0.0
        assert packet.task_type == ""
        assert packet.omissions == []
        assert packet.ack is None
        # Record shows degradation.
        assert record.degraded is True
        assert record.tokens is None

    def test_perceive_without_interpret_returns_clean_packet(self, tmp_path: Path) -> None:
        """No interpret seam: clean packet with only original, no degradation."""
        from embodiment.perception import perceive

        original = "Just a plain request"
        packet, record = perceive(original)

        assert packet.original == original
        assert packet.interpretation == ""
        assert record.degraded is False


# ── live perception rig — skipped unless explicitly pointed at it ─────────────


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{greenhouse.API_KEY_ENV} is not set")
class TestLivePerception:
    """Live perception seam: the verbatim invariant against a real model."""

    def test_live_perception_preserves_verbatim_original(self, tmp_path: Path) -> None:
        """Assert ContextPacket.original is byte-identical to the operator's input."""
        base_url = os.environ.get("EMBODIMENT_DEMO_BASE_URL", greenhouse.DEFAULT_BASE_URL)
        if not _gateway_answers(base_url):
            pytest.skip(f"no gateway answering at {base_url}")

        key = os.environ.get(greenhouse.API_KEY_ENV, "").strip()
        interpret_fn = greenhouse.senses_seam(base_url, greenhouse.SENSES_MODEL, key)

        from embodiment.perception import perceive

        utterance = "Is the fig thirsty today?"
        packet, record = perceive(utterance, interpret=interpret_fn)

        # The verbatim invariant: original is byte-identical to the caller's input.
        assert packet.original == utterance
        # The model DOES answer well; embodiment#15 is that we cannot read it.
        # The 12B fences its JSON (```json ... ```), perceive does not strip the
        # fence, and every model-derived field comes back empty while the record
        # still claims degraded=False. Pinned as xfail rather than deleted so the
        # defect stays visible in the suite and this test starts passing by
        # itself the moment #15 is fixed.
        if packet.interpretation == "":
            pytest.xfail("embodiment#15: fenced JSON parses to nothing, degraded=False")
        assert packet.interpretation != ""
        assert record.degraded is False

    def test_dead_endpoint_degrades_not_raises(self, tmp_path: Path) -> None:
        """A dead endpoint degrades to a degraded record, never raises."""
        from embodiment.perception import perceive

        # Point at a port that nothing listens on.
        dead_url = "http://localhost:59999/v1"
        interpret_fn = greenhouse.senses_seam(dead_url, greenhouse.SENSES_MODEL, "fake-key")

        utterance = "Hello from a dead port"
        packet, record = perceive(utterance, interpret=interpret_fn)

        assert packet.original == utterance
        assert record.degraded is True
        assert record.tokens is None
