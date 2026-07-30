"""The muse's workspace: it reaches nothing, it carries no secrets, it never raises.

Task t13 wires ``headspace-cli`` — as a base dependency and as the muse's second
thinking tool, in one change, because a pin without an importer would fail
``tests/test_zero_deps.py`` and would violate honesty condition ``h2`` (*"the
dependency is never added speculatively ahead of the code that needs it"*).

Four things have to be true, and none of them is self-evident from reading
:mod:`embodiment.workspace`, so all four are pinned here:

1. **The sandbox reaches nothing.** No repository path, no eidetic or coherence
   store, no network. Held four ways: no ``policy`` is ever constructed or
   passed (so headspace's closed default is the only reachable posture), no
   host-path verb is ever called, the tool schema has no path parameter, and —
   under ``EMBODIMENT_LIVE_RIG=1`` — a real workspace is opened and the reach is
   *attempted* and observed to fail. This is the load-bearing control of the
   whole muse-tools lane (issue #21), so the always-on half runs on
   ``provider="fake"`` and needs no daemon.
2. **No secrets, and no way to add them** (claim ``c34``). Proved three ways —
   there is no parameter to set, no call site that could forward one, and the
   real constructed invocation carries none — because "we did not pass one" is a
   fact about today's code and "there is nothing to pass" is a fact about the
   seam.
3. **A missing engine degrades, never raises.** The muse gets readable text with
   an install hint and keeps thinking; the host gets a recorded transition
   (constraint ``C3``).
4. **It behaves like a muse tool.** Bench-shaped, top-level only, results capped,
   an unoffered name refused rather than guessed at.

The always-on tests use ``provider="fake"`` — headspace's in-memory backend,
which is a real implementation of its provider seam rather than a stub — plus a
recording double for the invocation-shape assertions. ``HEADSPACE_HOME`` is
redirected into ``tmp_path`` everywhere, so no test writes to a developer's real
store.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess  # nosec B404 - fixed argv, no shell, reads the host's route table
from pathlib import Path
from typing import Any, Optional

import headspace.api
import pytest

from embodiment import workspace as workspace_module
from embodiment.contract import ModelResponse, ToolCall
from embodiment.muse import DEGRADED_TOOL, MARKER_DONE, MuseControls, MuseLoop, MuseToolBench
from embodiment.presence_engine import BoundaryContext
from embodiment.workspace import (
    DEGRADED_DESTROY_FAILED,
    DEGRADED_ENGINE_UNAVAILABLE,
    DEGRADED_RUN_FAILED,
    DEGRADED_UNREADABLE_RESULT,
    ENGINE_HINT,
    NO_REACH,
    PROVIDER_FAKE,
    WORKSPACE_CODES,
    WORKSPACE_LANE,
    WORKSPACE_PROTOCOL,
    WORKSPACE_TOOL_NAME,
    WORKSPACE_TOOL_NAMES,
    WORKSPACE_TOOLS,
    MuseWorkspace,
    WorkspaceCounts,
    WorkspaceDegradation,
)

MODULE_PATH = Path(workspace_module.__file__).resolve()

#: Ports the executed probe used, and what each one is on the reference rig.
#: 8001 is the lobes gateway, 7687 neo4j, 27017 mongo — the three things a
#: workspace must not be able to reach.
PROBED_PORTS = (8001, 7687, 27017)

#: The errno a connect from a network-less namespace reports.
ENETUNREACH = 101


# ── fixtures and doubles ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every headspace call in this file writes into ``tmp_path``, never ``~``.

    ``headspace.api`` opens a fresh store rooted at ``$HEADSPACE_HOME`` on every
    call, so redirecting the variable is enough — and it has to be done for the
    whole file, because a test that quietly wrote into a developer's real store
    would be a side effect this suite has no business having.
    """
    monkeypatch.setenv("HEADSPACE_HOME", str(tmp_path / "headspace"))


class RecordingApi:
    """A stand-in for ``headspace.api`` that records the exact invocation.

    This is how "no secrets reach a job" stops being a promise: the assertion is
    made against the ``(args, kwargs)`` this module actually constructed, not
    against a reading of its source.
    """

    def __init__(
        self,
        *,
        create_error: Optional[Exception] = None,
        run_error: Optional[Exception] = None,
        destroy_error: Optional[Exception] = None,
        package: Any = None,
        workspace_id: str = "ws-1",
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._create_error = create_error
        self._run_error = run_error
        self._destroy_error = destroy_error
        self._package = package
        self._workspace_id = workspace_id

    def _record(self, verb: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append((verb, args, dict(kwargs)))

    def kwargs_for(self, verb: str) -> list[dict[str, Any]]:
        return [kwargs for name, _args, kwargs in self.calls if name == verb]

    def create(self, *args: Any, **kwargs: Any) -> Any:
        self._record("create", args, kwargs)
        if self._create_error is not None:
            raise self._create_error
        return _Package(provenance=_Provenance(workspace_id=self._workspace_id))

    def run(self, *args: Any, **kwargs: Any) -> Any:
        self._record("run", args, kwargs)
        if self._run_error is not None:
            raise self._run_error
        return self._package if self._package is not None else _Package(status="success")

    def destroy(self, *args: Any, **kwargs: Any) -> Any:
        self._record("destroy", args, kwargs)
        if self._destroy_error is not None:
            raise self._destroy_error
        return _Package(status="success")


class _Provenance:
    def __init__(self, workspace_id: str = "") -> None:
        self.workspace_id = workspace_id


class _Evidence:
    def __init__(self, label: str = "captured output", excerpt: str = "") -> None:
        self.label = label
        self.excerpt = excerpt


class _Package:
    """A result package shaped like headspace's, built without importing its type."""

    def __init__(
        self,
        *,
        status: str = "success",
        outcome_summary: str = "",
        key_findings: Optional[list[str]] = None,
        evidence: Optional[list[_Evidence]] = None,
        warnings: Optional[list[str]] = None,
        provenance: Optional[_Provenance] = None,
    ) -> None:
        self.status = status
        self.outcome_summary = outcome_summary
        self.key_findings = key_findings or []
        self.evidence = evidence or []
        self.warnings = warnings or []
        self.provenance = provenance or _Provenance()


def _resp(content: str = "", *calls: ToolCall) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


def _call(name: str, call_id: str = "c1", **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=dict(arguments))


def _boundary() -> BoundaryContext:
    return BoundaryContext(kind="cadence-tick", step_count=0, reason="every-n")


class Scripted:
    """The tools-off seam: replays fixed turns."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        item = self.responses[index] if self.responses else _resp(MARKER_DONE)
        return item


class ScriptedTools:
    """The tool-carrying seam: records the schema it was handed, per turn."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.schemas: list[list[dict[str, Any]]] = []

    def __call__(
        self, messages: list[dict[str, Any]], schema: list[dict[str, Any]]
    ) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        self.schemas.append([dict(tool) for tool in schema])
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index] if self.responses else _resp(MARKER_DONE)


def _drive(
    workspace: MuseWorkspace, *replies: Any, depth: Any = 0
) -> tuple[Any, ScriptedTools, Scripted]:
    """One thinking session with *workspace* wired. Returns (outcome, tools, floor)."""
    tools = ScriptedTools(*replies)
    floor = Scripted(_resp(MARKER_DONE))
    loop = MuseLoop(
        floor,
        controls=MuseControls(max_turns=8, max_tool_rounds=8),
        tools=workspace.bench(tools),
        depth=depth,
    )
    return loop.think(_boundary()), tools, floor


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))


def _headspace_calls() -> list[ast.Call]:
    """Every call in the module whose callee names a ``headspace.api`` verb."""
    verbs = {"create", "run", "put", "export", "destroy"}
    found: list[ast.Call] = []
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in verbs:
            # ``self._api.<verb>(...)`` — the receiver is the injected seam.
            if isinstance(func.value, ast.Attribute) and func.value.attr == "_api":
                found.append(node)
    return found


# ── 1. the sandbox reaches nothing ────────────────────────────────────────────


class TestNoPolicyIsEverConstructed:
    """The first no-reach mechanism, and the one everything else rests on.

    headspace's ``Policy`` defaults are the closed ones — network disabled, no
    host paths, small budgets. This module never builds one and never passes
    ``policy=``, so the closed default is not *chosen* here; it is the only
    posture reachable from here. A widening would require code that does not
    exist rather than a configuration value someone could set.
    """

    def test_create_is_invoked_with_no_policy(self) -> None:
        api = RecordingApi()
        MuseWorkspace(api=api).execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert api.kwargs_for("create") == [{"provider": "docker"}]

    def test_create_is_invoked_with_no_profile_either(self) -> None:
        """The pinned default image is the only image, for the same reason."""
        api = RecordingApi()
        MuseWorkspace(api=api).execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "profile" not in api.kwargs_for("create")[0]

    def test_a_named_workspace_id_is_the_only_other_thing_create_can_carry(self) -> None:
        api = RecordingApi(workspace_id="ws-named")
        MuseWorkspace(api=api, workspace_id="ws-named").execute(
            WORKSPACE_TOOL_NAME, {"command": ["true"]}
        )

        assert api.kwargs_for("create") == [{"provider": "docker", "workspace_id": "ws-named"}]

    def test_no_call_site_in_the_module_passes_a_policy(self) -> None:
        for node in _headspace_calls():
            names = {keyword.arg for keyword in node.keywords}
            assert "policy" not in names, ast.dump(node)

    def test_the_module_never_names_a_policy_type(self) -> None:
        """``headspace.core.policy`` is private and stays unimported."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {name for name in imported if name.startswith("headspace.core")}, imported
        assert "headspace.api" in imported


class TestTheClosedDefaultIsWhatTheEngineRecords:
    """...and the default really is closed, read back off a real create.

    Driven through the actual ``headspace.api`` on the in-memory backend, so
    this is headspace's own recorded posture rather than this repo's belief
    about it. If a future headspace release flipped a default, this fails.
    """

    @staticmethod
    def _create_package() -> Any:
        return headspace.api.create(provider=PROVIDER_FAKE)

    def test_the_recorded_network_posture_is_disabled(self) -> None:
        package = self._create_package()

        assert "network: disabled" in package.key_findings

    def test_the_recorded_policy_summary_disables_the_network(self) -> None:
        package = self._create_package()

        assert "network=disabled" in package.provenance.policy_summary

    def test_the_recorded_policy_summary_grants_no_host_filesystem(self) -> None:
        package = self._create_package()

        assert "filesystem=()" in package.provenance.policy_summary


class TestNoBytesCrossInOrOut:
    """The second mechanism: the two host-path verbs are never called.

    ``headspace.api`` declares ``put`` (host path in) and ``export`` (host path
    out). Wiring either would hand this lane a way to name a location on the
    host — the repository, an eidetic store — so neither appears at a call site,
    and this walks the AST rather than trusting the reading.
    """

    def test_put_and_export_are_never_called(self) -> None:
        called = {node.func.attr for node in _headspace_calls()}  # type: ignore[union-attr]

        assert called <= {"create", "run", "destroy"}, called

    def test_the_three_verbs_that_are_called_are_the_lifecycle_and_the_tool(self) -> None:
        called = {node.func.attr for node in _headspace_calls()}  # type: ignore[union-attr]

        assert called == {"create", "run", "destroy"}


class TestTheSchemaOffersNoReach:
    """The third mechanism: there is nothing on the wire a model could fill in.

    One tool, one parameter, and that parameter is an argv. No path, no mount,
    no environment, no url — so the muse cannot name a host location even in
    principle, and a widening of the reach would have to be a schema change
    somebody reviewed.
    """

    @staticmethod
    def _properties() -> dict[str, Any]:
        return WORKSPACE_TOOLS[0]["function"]["parameters"]["properties"]

    def test_there_is_exactly_one_tool(self) -> None:
        assert len(WORKSPACE_TOOLS) == 1
        assert WORKSPACE_TOOL_NAMES == (WORKSPACE_TOOL_NAME,)

    def test_it_takes_exactly_one_parameter(self) -> None:
        assert list(self._properties()) == ["command"]

    def test_that_parameter_is_an_argv_of_strings(self) -> None:
        command = self._properties()["command"]
        assert command["type"] == "array"
        assert command["items"] == {"type": "string"}

    @pytest.mark.parametrize(
        "forbidden",
        ["path", "paths", "mount", "volume", "env", "environment", "secret", "secrets", "url"],
    )
    def test_no_reach_shaped_property_exists(self, forbidden: str) -> None:
        assert forbidden not in self._properties()

    def test_the_tool_name_does_not_collide_with_the_muse_pad(self) -> None:
        """Arm C wires the pad and the workspace together; one bench, one name each."""
        from embodiment.muse_pad import MUSE_PAD_TOOL_NAMES

        assert not set(WORKSPACE_TOOL_NAMES) & set(MUSE_PAD_TOOL_NAMES)


class TestTheProtocolStatesTheBoundary:
    """A boundary the muse is not told about is a boundary it will test by accident."""

    def test_every_no_reach_item_reaches_the_protocol_text(self) -> None:
        for item in NO_REACH:
            assert item in WORKSPACE_PROTOCOL

    def test_the_protocol_names_the_repository_the_stores_and_the_network(self) -> None:
        lowered = WORKSPACE_PROTOCOL.lower()
        for word in ("repository", "eidetic", "network"):
            assert word in lowered

    def test_the_protocol_says_no_secrets_reach_the_job(self) -> None:
        assert "no secrets" in WORKSPACE_PROTOCOL.lower()


class TestTheLiveSandboxProbe:
    """The executed probe, ported: open a real workspace and *attempt* the reach.

    Everything above is structural — it proves nothing was *asked for*. This is
    the half that proves the isolation actually holds, so it needs a real engine
    and is gated exactly as this repo's other live tests are
    (``EMBODIMENT_LIVE_RIG=1``). What it asserts is what the hand-run probe
    observed: only the loopback interface exists inside the workspace, and a
    connect to the docker-bridge gateway on the lobes, neo4j and mongo ports
    fails with ``[Errno 101] Network is unreachable``.

    Both addresses are **discovered**, never hardcoded: ``ip route`` on the
    host, reading the docker bridge's own address off its ``dev docker0`` line
    and the host's gateway off ``default via <addr>`` — field 3, the shell
    one-liner the probe used, spelled without a shell. Hardcoding ``172.17.0.1``
    would make the test a fact about one machine's bridge numbering.

    Strictly, a namespace with no interface but ``lo`` fails *every* non-loopback
    connect identically, so the addresses chosen do not change the verdict. They
    are chosen anyway because these are the addresses that genuinely host the
    lobes gateway, neo4j and mongo on the reference rig: a future workspace that
    somehow acquired a route would fail this test against the real services
    rather than against an address nothing listens on.
    """

    LIVE = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"

    @staticmethod
    def _routes() -> str:
        """The host's route table, or ``""`` when ``ip`` is unavailable."""
        try:
            proc = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
                ["ip", "route"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout

    @classmethod
    def _probe_targets(cls) -> tuple[str, ...]:
        """The docker bridge address and the default gateway, in that order."""
        found: list[str] = []
        for line in cls._routes().splitlines():
            fields = line.split()
            if not fields:
                continue
            # `172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1`
            if "dev" in fields and "docker0" in fields and "src" in fields:
                found.append(fields[fields.index("src") + 1])
            # `default via 192.168.1.1 dev … ` — awk's $3.
            elif fields[0] == "default" and "via" in fields:
                found.append(fields[fields.index("via") + 1])
        # Preserve order, drop duplicates: several default routes are ordinary
        # on a host with two wireless interfaces.
        return tuple(dict.fromkeys(found))

    @staticmethod
    def _probe_source(targets: tuple[str, ...], repo_root: str) -> str:
        """What runs inside the workspace. Prints one labelled line per finding."""
        return (
            "import os, socket\n"
            "print('interfaces=' + ','.join(sorted(os.listdir('/sys/class/net'))))\n"
            f"for host in {list(targets)!r}:\n"
            f"    for port in {list(PROBED_PORTS)!r}:\n"
            "        try:\n"
            "            socket.create_connection((host, port), timeout=2).close()\n"
            "            print('reached=%s:%d' % (host, port))\n"
            "        except OSError as err:\n"
            "            print('refused=%s:%d errno=%s' % (host, port, err.errno))\n"
            f"print('repo=' + str(os.path.exists({repo_root!r})))\n"
            f"print('store=' + str(os.path.exists({repo_root + '/.eidetic'!r})))\n"
        )

    @pytest.mark.skipif(not LIVE, reason="set EMBODIMENT_LIVE_RIG=1 to test the real engine")
    def test_a_live_workspace_reaches_neither_the_network_nor_the_repo(self) -> None:
        targets = self._probe_targets()
        if not targets:
            pytest.skip("no bridge or default route on this host; nothing to fail against")

        repo_root = str(Path(__file__).resolve().parents[1])
        workspace = MuseWorkspace()
        try:
            result = workspace.execute(
                WORKSPACE_TOOL_NAME,
                {"command": ["python3", "-c", self._probe_source(targets, repo_root)]},
            )
        finally:
            workspace.destroy()

        assert "interfaces=lo" in result, result
        for host in targets:
            for port in PROBED_PORTS:
                assert f"refused={host}:{port} errno={ENETUNREACH}" in result, result
                assert f"reached={host}:{port}" not in result, result
        assert "repo=False" in result, result
        assert "store=False" in result, result
        assert workspace.degradations == ()


# ── 2. no secrets, and no way to add them (claim c34) ─────────────────────────


class TestNoSecretsParameterExists:
    """Proof one of three: there is nothing to set.

    headspace's environment channel is real and works — ``run(environment=...)``,
    ``--env``, ``--env-file`` — and it is operator tooling. The muse tool seam
    does not expose it, so the leak path (job prints env -> captured output ->
    counsel text -> a PUBLIC eidetic record, committed inside a git repo) cannot
    be opened by configuration. It has no switch.
    """

    #: Parameter names that would open the channel, in any spelling a caller
    #: might plausibly reach for.
    FORBIDDEN = ("env", "environment", "env_file", "envfile", "secret", "secrets", "credentials")

    @staticmethod
    def _public_callables() -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        for name in workspace_module.__all__:
            value = getattr(workspace_module, name)
            if inspect.isclass(value):
                found.append((f"{name}.__init__", value.__init__))
                found.extend(
                    (f"{name}.{attr}", member)
                    for attr, member in vars(value).items()
                    if not attr.startswith("_") and inspect.isfunction(member)
                )
            elif inspect.isfunction(value):
                found.append((name, value))
        return found

    def test_no_public_callable_takes_a_secrets_parameter(self) -> None:
        for label, member in self._public_callables():
            parameters = set(inspect.signature(member).parameters)
            assert not parameters & set(self.FORBIDDEN), f"{label}: {sorted(parameters)}"

    def test_no_public_shape_carries_a_secrets_field(self) -> None:
        for shape in (WorkspaceCounts, WorkspaceDegradation):
            fields = set(getattr(shape, "__dataclass_fields__", {}))
            assert not fields & set(self.FORBIDDEN), sorted(fields)

    def test_the_scan_would_catch_one(self) -> None:
        """The guard is only worth something if it fails on a real leak."""

        def leaky(*, environment: dict[str, str]) -> None:  # pragma: no cover - shape only
            return None

        assert set(inspect.signature(leaky).parameters) & set(self.FORBIDDEN)


class TestNoCallSiteCouldForwardOne:
    """Proof two of three: there is nowhere to pass it.

    Even a private attribute someone added later would need a call site to reach
    the engine. Every ``headspace.api`` invocation in the module is walked and
    its keywords are held to a closed allow-list, so a new keyword is a test
    failure rather than a review's responsibility to notice.
    """

    ALLOWED = {"provider", "workspace_id"}

    def test_every_invocation_carries_only_allow_listed_keywords(self) -> None:
        for node in _headspace_calls():
            names = {keyword.arg for keyword in node.keywords}
            assert names <= self.ALLOWED, ast.dump(node)

    def test_no_invocation_uses_a_star_star_kwargs_expansion(self) -> None:
        """``**whatever`` would route straight around the allow-list above."""
        for node in _headspace_calls():
            assert all(keyword.arg is not None for keyword in node.keywords), ast.dump(node)

    def test_the_module_never_names_headspaces_environment_type(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "JobEnvironment" not in names
        assert "InputRequest" not in names


class TestTheConstructedInvocationCarriesNone:
    """Proof three of three: the real invocation, recorded and inspected.

    Driven through the public seam — a whole thinking session with the bench
    wired — so what is asserted is what a live drive would send, not what a
    unit-level poke would.
    """

    def test_a_full_session_sends_no_environment_and_no_inputs(self) -> None:
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)
        _drive(
            workspace,
            _resp("checking", _call(WORKSPACE_TOOL_NAME, command=["python3", "-c", "print(76)"])),
            _resp("GUIDANCE: it is 76 " + MARKER_DONE),
        )

        assert api.calls, "the session never reached the engine"
        for _verb, _args, kwargs in api.calls:
            assert "environment" not in kwargs
            assert "inputs" not in kwargs
            assert "declares" not in kwargs

    def test_run_carries_the_argv_and_the_provider_and_nothing_else(self) -> None:
        api = RecordingApi(workspace_id="ws-7")
        workspace = MuseWorkspace(api=api)
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", "print(1)"]})

        verb, args, kwargs = api.calls[-1]
        assert verb == "run"
        assert args == ("ws-7", ["python3", "-c", "print(1)"])
        assert kwargs == {"provider": "docker"}

    def test_a_real_fake_backed_run_still_forwards_no_environment(self) -> None:
        """The same claim against the genuine ``headspace.api``, not a double.

        ``FakeProvider`` records the env a job actually observed on its own
        workspace record; the seam's default is an empty mapping, and this
        module never overrides it.
        """
        workspace = MuseWorkspace(provider=PROVIDER_FAKE)
        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", "pass"]})

        assert workspace.workspace_id
        assert "status: success" in result


# ── 3. a missing engine degrades, never raises ────────────────────────────────


class TestAMissingEngineDegradesObservably:
    """Constraint C3 on the path a host will actually hit first.

    A machine with no container daemon is the ordinary case for a developer
    running the suite, and for any host that adopts this package without one. It
    must not raise into the muse's thinking, it must not pretend to have run
    anything, and it must not degrade silently.
    """

    @staticmethod
    def _dead_engine() -> RecordingApi:
        return RecordingApi(
            create_error=OSError("could not reach the execution engine: no such file /var/run")
        )

    def test_a_dead_engine_returns_text_rather_than_raising(self) -> None:
        workspace = MuseWorkspace(api=self._dead_engine())

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert isinstance(result, str)
        assert "no workspace is available" in result

    def test_the_text_carries_the_install_hint(self) -> None:
        workspace = MuseWorkspace(api=self._dead_engine())

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert ENGINE_HINT in result

    def test_the_transition_is_recorded(self) -> None:
        workspace = MuseWorkspace(api=self._dead_engine())
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert [record.code for record in workspace.degradations] == [DEGRADED_ENGINE_UNAVAILABLE]
        assert workspace.degradations[0].stage == "create"
        assert "no such file" in workspace.degradations[0].reason

    def test_the_record_names_the_lane_when_serialised(self) -> None:
        workspace = MuseWorkspace(api=self._dead_engine())
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert workspace.degradations[0].to_dict()["lane"] == WORKSPACE_LANE

    def test_nothing_was_run(self) -> None:
        api = self._dead_engine()
        MuseWorkspace(api=api).execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert api.kwargs_for("run") == []

    def test_a_missing_engine_sdk_reads_just_as_legibly(self) -> None:
        """An ``ImportError`` has no ``.message``; the render must not depend on one."""
        workspace = MuseWorkspace(api=RecordingApi(create_error=ImportError("No module docker")))

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "No module docker" in result
        assert workspace.degradations[0].code == DEGRADED_ENGINE_UNAVAILABLE

    def test_a_structured_refusal_carries_its_remediation_through(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi(create_error=_CliErrorish()))

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "start the daemon" in result

    def test_the_session_keeps_thinking_through_a_dead_engine(self) -> None:
        workspace = MuseWorkspace(api=self._dead_engine())
        outcome, _tools, _floor = _drive(
            workspace,
            _resp("checking", _call(WORKSPACE_TOOL_NAME, command=["true"])),
            _resp("GUIDANCE: reasoning it out instead " + MARKER_DONE),
        )

        assert outcome.insights
        assert not [d for d in outcome.degradations if d.code == DEGRADED_TOOL]


class _CliErrorish(Exception):
    """A ``headspace.cli._errors.CliError``-shaped failure, built without importing it."""

    def __init__(self) -> None:
        super().__init__("the engine is unreachable")
        self.message = "the engine is unreachable"
        self.remediation = "start the daemon"


class TestTheOtherDegradationsAreReachedThroughThePublicSeam:
    """Every code in the vocabulary has a provoker that drives ``execute``.

    Task t3's rule: a provoker that reaches a private attribute proves the
    constant exists, not that anything produces it. All four of these go through
    the public seam.
    """

    def test_a_broken_engine_mid_run_is_recorded(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi(run_error=OSError("engine died")))

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "could not be run" in result
        assert workspace.degradations[-1].code == DEGRADED_RUN_FAILED
        assert workspace.degradations[-1].workspace_id == "ws-1"

    def test_a_create_that_reports_no_workspace_id_is_recorded(self) -> None:
        """A provisioned-but-unnamed workspace is unusable, and says so.

        Nothing could be run against it — ``run`` takes an id — so treating the
        empty id as success would leave the muse waiting on a command that was
        never sent.
        """
        api = RecordingApi(workspace_id="")
        workspace = MuseWorkspace(api=api)

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "no workspace is available" in result
        assert workspace.degradations[-1].code == DEGRADED_ENGINE_UNAVAILABLE
        assert "no workspace id" in workspace.degradations[-1].reason
        assert api.kwargs_for("run") == []

    def test_an_unreadable_result_is_recorded(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi(package=object()))

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "could not be read" in result
        assert workspace.degradations[-1].code == DEGRADED_UNREADABLE_RESULT

    def test_a_failed_teardown_is_recorded(self) -> None:
        api = RecordingApi(destroy_error=OSError("volume busy"))
        workspace = MuseWorkspace(api=api)
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert workspace.destroy() is False
        assert workspace.degradations[-1].code == DEGRADED_DESTROY_FAILED
        assert workspace.degradations[-1].workspace_id == "ws-1"

    def test_every_declared_code_was_provoked_here(self) -> None:
        """Exhaustiveness, so a code added later cannot ship without a producer."""
        provoked = {
            DEGRADED_ENGINE_UNAVAILABLE,
            DEGRADED_RUN_FAILED,
            DEGRADED_UNREADABLE_RESULT,
            DEGRADED_DESTROY_FAILED,
        }
        assert set(WORKSPACE_CODES) == provoked

    def test_a_failing_command_is_a_result_not_a_degradation(self) -> None:
        """The distinction the vocabulary exists to keep.

        A command that ran and exited non-zero is evidence the muse should read,
        not embodiment breaking. Recording it would cry wolf on the ordinary
        case.
        """
        api = RecordingApi(package=_Package(status="failure", outcome_summary="job x exited 1"))
        workspace = MuseWorkspace(api=api)

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["false"]})

        assert "status: failure" in result
        assert workspace.degradations == ()
        assert workspace.counts().statuses == {"failure": 1}


class TestDestroyIsSafeToCallAnyTime:
    """A close path that raises loses whatever it was closing."""

    def test_destroying_an_unprovisioned_workspace_is_a_no_op(self) -> None:
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)

        assert workspace.destroy() is False
        assert api.calls == []
        assert workspace.degradations == ()

    def test_destroying_a_provisioned_workspace_reports_true(self) -> None:
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert workspace.destroy() is True
        assert api.calls[-1] == ("destroy", ("ws-1",), {"provider": "docker"})
        assert workspace.workspace_id == ""


# ── 4. it behaves like a muse tool ────────────────────────────────────────────


class TestTheBench:
    """The seam t10 landed and t12 first wired, satisfied a second time."""

    def test_bench_carries_the_schema_the_module_declares(self) -> None:
        bench = MuseWorkspace(api=RecordingApi()).bench(ScriptedTools())

        assert isinstance(bench, MuseToolBench)
        assert bench.schema is WORKSPACE_TOOLS

    def test_the_schema_reaches_the_wire_on_a_top_level_session(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi())
        _outcome, tools, _floor = _drive(workspace, _resp("GUIDANCE: done " + MARKER_DONE))

        assert tools.schemas
        assert tools.schemas[0][0]["function"]["name"] == WORKSPACE_TOOL_NAME

    def test_a_subagent_depth_muse_is_handed_nothing(self) -> None:
        """Top-level only. The withholding is muse.py's to record, not this lane's."""
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)
        _outcome, tools, floor = _drive(
            workspace,
            _resp("checking", _call(WORKSPACE_TOOL_NAME, command=["true"])),
            depth=1,
        )

        assert tools.calls == []
        assert floor.calls
        assert api.calls == []

    def test_a_tool_the_lane_does_not_offer_is_refused(self) -> None:
        from embodiment.loop import UnknownToolError

        workspace = MuseWorkspace(api=RecordingApi())
        with pytest.raises(UnknownToolError):
            workspace.execute("shell", {"command": ["rm", "-rf", "/"]})

        assert workspace.counts().off_protocol_calls == 1

    def test_a_refused_name_degrades_the_session_and_it_keeps_thinking(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi())
        outcome, _tools, _floor = _drive(
            workspace,
            _resp("reaching", _call("shell", command=["ls"])),
            _resp("GUIDANCE: no shell here " + MARKER_DONE),
        )

        assert [d.code for d in outcome.degradations] == [DEGRADED_TOOL]
        assert outcome.insights


class TestAHostileResultDegradesTheReadNotTheRun:
    """The never-raise rule, taken all the way down to the rendering.

    A result package is read duck-typed, which means arbitrary ``__getattr__``
    and ``__str__`` run inside the read. Either can raise, and neither may take
    the thinking session with it — a section that cannot be read is absent, and
    a package with *no* readable section is named as such.
    """

    class _Explosive:
        """Every attribute access raises. Not a shape headspace produces — a shape
        a defensive read must survive anyway."""

        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"attribute {name} explodes")

    class _Unprintable:
        """A status whose ``__str__`` raises, wrapped in an otherwise sane package."""

        def __str__(self) -> str:
            raise RuntimeError("unprintable")

    def test_a_package_whose_attributes_explode_is_named_not_raised(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi(package=self._Explosive()))

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "could not be read" in result
        assert workspace.degradations[-1].code == DEGRADED_UNREADABLE_RESULT

    def test_an_unprintable_field_drops_that_field_and_keeps_the_rest(self) -> None:
        api = RecordingApi(
            package=_Package(status=self._Unprintable(), outcome_summary="the job ran")
        )
        workspace = MuseWorkspace(api=api)

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert result == "the job ran"
        assert workspace.degradations == ()


class TestWhatTheObjectCanAnswerAboutItself:
    """Read-only properties a host and a transcript both reach for."""

    def test_it_reports_the_backend_it_was_asked_for(self) -> None:
        assert MuseWorkspace(api=RecordingApi(), provider=PROVIDER_FAKE).provider == PROVIDER_FAKE

    def test_it_exposes_the_schema_it_puts_on_the_wire(self) -> None:
        assert MuseWorkspace(api=RecordingApi()).schema is WORKSPACE_TOOLS


class TestUnusableArgumentsAreCorrected:
    """A bad call to a real tool is not a broken protocol — the muse is told so."""

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"command": []}, {"command": "python3 -c 'print(1)'"}, {"command": ["", " "]}],
    )
    def test_an_unusable_command_is_rejected_without_touching_the_engine(
        self, arguments: dict[str, Any]
    ) -> None:
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)

        result = workspace.execute(WORKSPACE_TOOL_NAME, arguments)

        assert "must be a non-empty list of strings" in result
        assert api.calls == []
        assert workspace.counts().rejected_calls == 1

    def test_a_rejection_is_not_a_degradation(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi())
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": []})

        assert workspace.degradations == ()

    def test_blank_entries_are_dropped_rather_than_forwarded(self) -> None:
        api = RecordingApi()
        MuseWorkspace(api=api).execute(
            WORKSPACE_TOOL_NAME, {"command": ["python3", "", "-c", "pass"]}
        )

        assert api.calls[-1][1][1] == ["python3", "-c", "pass"]


class TestTheResultTheMuseReads:
    """Compact, bounded, and honest about what it dropped."""

    def test_the_captured_output_survives_into_the_text(self) -> None:
        api = RecordingApi(
            package=_Package(
                status="success",
                outcome_summary="job ran",
                evidence=[_Evidence(excerpt="76")],
            )
        )
        result = MuseWorkspace(api=api).execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "captured output:\n76" in result

    def test_findings_and_warnings_are_labelled(self) -> None:
        api = RecordingApi(
            package=_Package(key_findings=["exit status: 0"], warnings=["storage is measured"])
        )
        result = MuseWorkspace(api=api).execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert "findings:\n  - exit status: 0" in result
        assert "warnings:\n  - storage is measured" in result

    def test_an_oversized_result_is_capped_and_says_so(self) -> None:
        api = RecordingApi(package=_Package(evidence=[_Evidence(excerpt="x" * 5000)]))
        workspace = MuseWorkspace(api=api, max_result_chars=200)

        result = workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert len(result) < 400
        assert "truncated" in result

    def test_the_default_cap_matches_the_seams_own(self) -> None:
        """So this module's honest truncation happens first, and the seam's clip never does."""
        assert workspace_module.DEFAULT_MAX_RESULT_CHARS == MuseControls().max_tool_result_chars


class TestCountsAreMeasurementsNotJudgements:
    """t18's dependent variables, readable off a finished session."""

    def test_a_fresh_workspace_reports_zeroes_and_no_id(self) -> None:
        counts = MuseWorkspace(api=RecordingApi()).counts()

        assert counts.lane == WORKSPACE_LANE
        assert counts.created is False
        assert counts.workspace_id == ""
        assert counts.runs == 0

    def test_a_run_is_counted_with_its_status(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi())
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        counts = workspace.counts()
        assert counts.runs == 2
        assert counts.statuses == {"success": 2}
        assert counts.created is True
        assert counts.workspace_id == "ws-1"

    def test_counts_serialise_for_a_committed_transcript(self) -> None:
        workspace = MuseWorkspace(api=RecordingApi())
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        payload = workspace.counts().to_dict()
        assert payload["lane"] == WORKSPACE_LANE
        assert payload["runs"] == 1
        assert payload["statuses"] == {"success": 1}

    def test_the_workspace_is_provisioned_once_across_many_runs(self) -> None:
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)
        for _ in range(3):
            workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert len(api.kwargs_for("create")) == 1

    def test_nothing_is_provisioned_until_a_command_arrives(self) -> None:
        """Constructing the bench on a machine with no engine must cost nothing."""
        api = RecordingApi()
        workspace = MuseWorkspace(api=api)
        workspace.bench(ScriptedTools())

        assert api.calls == []
        assert workspace.workspace_id == ""


class TestAgainstTheRealHeadspaceApi:
    """The in-memory backend, end to end, so the wiring is proved not just described."""

    def test_a_session_provisions_runs_and_reports(self) -> None:
        workspace = MuseWorkspace(provider=PROVIDER_FAKE)
        _outcome, _tools, _floor = _drive(
            workspace,
            _resp("checking", _call(WORKSPACE_TOOL_NAME, command=["python3", "-c", "print(76)"])),
            _resp("GUIDANCE: it is 76 " + MARKER_DONE),
        )

        counts = workspace.counts()
        assert counts.created is True
        assert counts.runs == 1
        assert counts.statuses == {"success": 1}
        assert workspace.degradations == ()

    def test_the_workspace_is_destroyed_on_request(self) -> None:
        workspace = MuseWorkspace(provider=PROVIDER_FAKE)
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        assert workspace.destroy() is True
        assert workspace.workspace_id == ""

    def test_only_the_headspace_store_is_written(self, tmp_path: Path) -> None:
        """A session touches its own store root and nothing else under tmp_path."""
        workspace = MuseWorkspace(provider=PROVIDER_FAKE)
        workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["true"]})

        written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
        assert written <= {"headspace"}
