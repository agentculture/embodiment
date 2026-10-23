"""Synthetic end-to-end acceptance for "Gwen remembers recordings" (plan task ``t19``).

The announcement, clause by clause, and the test that proves each. Everything
here is synthetic: a real files backend in a tmp dir, a real daemon
(:class:`~embodiment.daemon.app.DaemonApp`) with the real drain, spool, memory,
vector cache and embedder client, and fakes only at the gateway seams (the
senses completion, the embedder's HTTP transport, tts, stt). No network, no
device, no record from Gwen's real store; the CLI runs as a subprocess the way
culture-rules' runner does (env ``{PATH, LANG}``, stdin closed, a throwaway cwd,
absolute paths).

Announcement clause -> test:

1. *"The operator talks with Gwen by voice"* ->
   :class:`TestTalkByVoiceAndByText` ``test_a_spoken_turn_recalls_and_speaks_a_cited_fact``.
2. *"... and by text at gwen.culture.dev behind Cloudflare Access"* ->
   ``test_a_typed_turn_cites_both_sources`` (the text turn) and
   :class:`TestTextBehindAccess` (the daemon's own controls behind a real guard
   and the real ``AccessVerifier``, tokens signed with a local RSA key, no
   network). NOT provable here: the live Cloudflare edge, DNS, the real JWKS;
   the guard's own matrix lives in ``tests/test_http_guard.py`` and
   ``tests/test_http_access.py``.
3. *"remembers their Pocket recordings and culture-rules' daily newspaper"* ->
   :class:`TestIngestIsAppliedAndRecalled`
   ``test_both_batches_are_applied_within_two_drain_intervals_then_recalled_and_cited``
   and :class:`TestNothingLoses` ``test_100_interleaved_ingest_and_remember_writes_lose_no_record``.
4. *"culture-rules enqueues distilled facts through an embodiment-owned memory
   ingest verb"* -> :class:`TestOneVerbOneWriter`
   ``test_the_verb_runs_under_the_producers_runner_and_is_idempotent``.
5. *"the daemon is their single writer"* -> :class:`TestOneVerbOneWriter`
   ``test_the_verb_never_writes_the_store_only_the_running_daemon_does``.
6. *"recall finds them semantically through gemma2-embed"* ->
   :class:`TestIngestIsAppliedAndRecalled`
   ``test_recall_is_semantic_through_the_named_role_and_embeds_only_the_query``.
7. *"every recalled fact is cited by its source"* ->
   ``test_both_batches_are_applied_within_two_drain_intervals_then_recalled_and_cited``
   (title/date/mm:ss for a recording, issue date/section/link for the paper,
   in the prompt and on the page) and ``test_a_spoken_turn_recalls_and_speaks_a_cited_fact``.
8. *"pocketchat can retire"* -> NOT provable synthetically: retiring pocketchat
   is pocketsync redirecting its users to gwen.culture.dev, an act in another
   repo that this cycle neither performs nor posts. What this module proves is
   the precondition, that one front answers a recording question from one
   memory with a citation (clauses 1-3 and 7). The statement is pinned by
   :class:`TestWhatIsNotClaimed` so it cannot be quietly dropped.

Success signals proven here (spec): a pocket batch and a newspaper batch applied
by a running daemon within 2 drain intervals, recalled in a text turn and cited
(``test_both_batches_...``); 0 records lost across 100 interleaved ingest and
remember writes (``test_100_interleaved_...``). Honesty condition h46, the
spool keeps no second copy: ``TestForgetLeavesNoCopy``.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess  # nosec B404 - the test runs this repo's own CLI
import sys
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import continuity
from embodiment import memory as mem
from embodiment import vectors
from embodiment.audio.features import FeatureExtractor
from embodiment.bus import Bus
from embodiment.contract import ModelResponse
from embodiment.daemon import app as app_module
from embodiment.daemon.app import AppConfig, AppFactories, DaemonApp
from embodiment.daemon.drain import Drain
from embodiment.daemon.state import DaemonState
from embodiment.http import access
from embodiment.http import guard as guard_module
from embodiment.http import server as server_module
from embodiment.ingest.spool import Spool
from embodiment.memory import RoomMemory
from embodiment.voice import Voice, VoiceConfig
from tests.test_daemon_app import FakeEars, FakeEndpoint, _sync_spawn
from tests.test_http_access import AUD, NOW, OPERATOR, TEAM, claims, jwks, token

pytestmark = pytest.mark.skipif(
    not continuity.eidetic_available(), reason="eidetic is not installed"
)

# Unique, invented sentinels: grepping a state dir for them is meaningful because
# nothing else can produce them.
POCKET_SENTINEL = "zqxotter-survey-moved-to-thursday"
POCKET_TITLE = "synthetic standup"
POCKET_DATE = "2026-09-30"
NEWS_SENTINEL = "zqxharbour-bridge-closes-for-repairs"
NEWS_LINK = "https://example.invalid/bridge"
PUBLIC_HOST = "gwen.example.invalid"
DIM = 128


# ── the fake embedder: deterministic bag-of-words vectors ─────────────────────


def bag_vector(text: str) -> list[float]:
    """Hash each word into one of DIM buckets: shared words mean similar vectors."""
    out = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        out[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0  # nosec B324
    return out


class CountingTransport:
    """The embedder's HTTP transport, faked: records every request, never dials."""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.models: list[str] = []

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float) -> bytes:
        payload = json.loads(body)
        self.inputs.append(payload["input"])
        self.models.append(payload["model"])
        return json.dumps({"data": [{"embedding": bag_vector(payload["input"])}]}).encode()


def echoing_complete(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
    """A fake senses model that answers by quoting whatever memory the prompt carried."""
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    quoted = [
        line[len(mem.QUOTE) :]
        for line in system.splitlines()
        if line.startswith(mem.QUOTE) and "synthetic" in line.lower()
    ]
    echoing_complete.prompts.append(system)  # type: ignore[attr-defined]
    reply = ("From memory: " + " / ".join(quoted)) if quoted else "Nothing in memory."
    return ModelResponse(content=reply, prompt_tokens=3, completion_tokens=4)


echoing_complete.prompts = []  # type: ignore[attr-defined]


# ── the rig: the real daemon over a real store, fakes at the gateway ──────────


class Rig:
    def __init__(self, tmp_path: Path, *, backfill: bool = True) -> None:
        self.tmp = tmp_path
        self.state_dir = tmp_path / "state"
        self.state = DaemonState(self.state_dir)
        self.store_dir = self.state_dir / "memory"
        self.cache_dir = self.state_dir / app_module.VECTOR_DIRNAME
        self.cache_dir.mkdir(mode=0o700, parents=True)
        self.transport = CountingTransport()
        self.embedder = vectors.EmbedderClient(
            "http://gateway.invalid", "", model=vectors.EMBED_ROLE, transport=self.transport
        )
        self.cache = mem.vector_cache_for(self.cache_dir)
        self.memory = RoomMemory(
            self.store_dir,
            scope="gwen",
            added_by="gwen",
            ingest_scope=mem.INGEST_SCOPE,
            embedder=self.embedder,
            vector_cache=self.cache,
            similarity_threshold=0.3,
        )
        self.drain = Drain(Spool.for_state_dir(self.state_dir), self.memory)
        self.bus = Bus()
        self.endpoint = FakeEndpoint(name="host")
        self.subscription = self.bus.subscribe(include_speech=True)
        self.tts_calls: list[str] = []
        self.app = DaemonApp(
            config=AppConfig(poll_interval_s=0.01, follow_up_threshold=0.3),
            state=self.state,
            bus=self.bus,
            memory=self.memory,
            complete=echoing_complete,
            factories=AppFactories(
                ears=lambda rate: FakeEars(),
                endpoint=lambda: self.endpoint,
                voice=self._voice,
            ),
            lanes=app_module.AppLanes(
                recall_spawn=_sync_spawn,
                drain=self.drain,
                embedder=self.embedder,
                vector_cache=self.cache,
                tts=lambda sentence, config: self.tts_calls.append(sentence) or b"\x01\x00" * 240,
            ),
        )
        self.app.attach_ear("host", self.endpoint)
        # The REAL background backfill (it is what gives a drained record its
        # vector), on a short idle cadence so a test need not wait 30 s. Started
        # here so the app's own start() finds it running.
        if backfill:
            self.memory.start_backfill(idle_interval=0.05, busy_interval=0.02)

    def wait_vectors(self, *texts: str, timeout: float = 20.0) -> None:
        """Wait for the running backfill to give every text a cached vector."""
        deadline = time.monotonic() + timeout
        while any(self.cache.get(t) is None for t in texts):
            assert time.monotonic() < deadline, "the backfill never cached the vectors"
            time.sleep(0.01)

    def _voice(self, endpoint: Any) -> Voice:
        return Voice(
            endpoint=endpoint,
            config=VoiceConfig(gateway_url="http://gateway.invalid"),
            bus=self.bus,
            features=FeatureExtractor(),
            synthesize=lambda sentence, config: b"\x00\x00" * 240,
        )

    # -- the producer's side: the real CLI, as a subprocess --------------------

    def cli(self, *args: str) -> dict[str, Any]:
        """Run ``embodiment <args>`` under culture-rules' runner; return its JSON."""
        env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
        cwd = self.tmp / "throwaway-cwd"
        cwd.mkdir(exist_ok=True)
        done = subprocess.run(  # nosec B603 - fixed argv, this repo's own CLI
            [sys.executable, "-m", "embodiment", *args, "--state-dir", str(self.state_dir)]
            + ["--json"],
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, (done.returncode, done.stdout[-400:], done.stderr[-400:])
        return json.loads(done.stdout)

    def ingest(self, records: list[dict[str, Any]], name: str) -> dict[str, Any]:
        path = self.tmp / f"{name}.ndjson"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return self.cli("memory", "ingest", "--file", str(path), "--apply")

    def forget(self, source: str, source_id: str) -> dict[str, Any]:
        return self.cli("memory", "forget", "--source", source, "--source-id", source_id, "--apply")

    # -- the daemon's clock ----------------------------------------------------

    def pending(self) -> int:
        return self.app.status()["ingest"]["inbox"]["pending"]

    def drain_intervals_until(self, done: Any, *, limit: int = 2) -> int:
        """Pump the daemon's own heartbeat until *done()*; return intervals used.

        One interval is one ``pump()`` (which kicks the drain worker) plus waiting
        for that kick's pass to finish — the wait is only so the test reads the
        result after the worker ran, it is not a wall-clock allowance.
        """
        for interval in range(1, limit + 1):
            passes_before = self.app.status()["ingest"]["worker"]["passes"]
            self.app.pump()
            deadline = time.monotonic() + 20.0
            while self.app.status()["ingest"]["worker"]["passes"] <= passes_before:
                assert time.monotonic() < deadline, "the drain worker never finished a pass"
                time.sleep(0.005)
            if done():
                return interval
        raise AssertionError(f"not applied within {limit} drain intervals")

    def store_records(self, scope: str) -> list[dict[str, Any]]:
        return mem._enumerate_scope(
            data_dir=self.store_dir, scope=scope, visibility="private", backend="files"
        )

    def close(self) -> None:
        for call in (
            lambda: self.app.close(deadline=3.0),
            lambda: self.memory.close(deadline=1.0),
            lambda: self.bus.close(deadline=1.0),
        ):
            try:
                call()
            except Exception:  # noqa: BLE001, S110 - best-effort teardown
                pass

    def events(self, kind: str) -> list[Any]:
        self._seen = getattr(self, "_seen", []) + self.subscription.drain()
        return [e for e in self._seen if e.kind == kind]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    built = Rig(tmp_path)
    built.app.start()
    yield built
    built.close()


def pocket(i: int, text: Optional[str] = None, timestamp: str = "01:05") -> dict[str, Any]:
    return {
        "id": f"pocket:synrec{i}:fact:{i}",
        "type": "fact",
        "text": text or f"synthetic fact {i} about herons {POCKET_SENTINEL}-{i}",
        "metadata": {
            "source": "pocket",
            "recording_id": f"synrec{i}",
            "title": POCKET_TITLE,
            "date": POCKET_DATE,
            "timestamp": timestamp,
        },
    }


def newspaper(i: int = 0, text: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": f"newspaper:2026-10-01:item:{i}",
        "type": "item",
        "text": text or f"synthetic harbour bridge closes for repairs {NEWS_SENTINEL}",
        "metadata": {
            "source": "newspaper",
            "issue_date": "2026-10-01",
            "section": "city",
            "url": NEWS_LINK,
        },
    }


OTTER = "synthetic otter survey moved to thursday zqxotter survey thursday"
BRIDGE = "synthetic harbour bridge closes for repairs zqxharbour bridge repairs"
QUESTION = "what about the otter survey thursday and the harbour bridge repairs"


def seed_both(rig: Rig) -> int:
    """Enqueue one pocket and one newspaper batch through the CLI; drain; return intervals."""
    rig.ingest([pocket(1, OTTER)], "pocket-batch")
    rig.ingest([newspaper(0, BRIDGE)], "paper-batch")
    assert rig.pending() == 2, "both batches must be waiting before the daemon is pumped"
    intervals = rig.drain_intervals_until(lambda: rig.pending() == 0)
    # Applied is not yet recallable by meaning: the vector arrives with the running
    # backfill (see TestTheVectorIsFilledByTheWrite for the gap this exposes).
    rig.wait_vectors(OTTER, BRIDGE)
    return intervals


# ── clauses 3, 6, 7: applied, semantic, cited ─────────────────────────────────


class TestIngestIsAppliedAndRecalled:
    def test_both_batches_are_applied_within_two_drain_intervals_then_recalled_and_cited(
        self, rig: Rig
    ) -> None:
        # Nothing is in the store until the daemon drains (the verbs never write it).
        assert rig.store_records(mem.INGEST_SCOPE) == []

        intervals = seed_both(rig)

        assert intervals <= 2
        stored = rig.store_records(mem.INGEST_SCOPE)
        assert len(stored) == 2
        assert {r["metadata"]["source"] for r in stored} == {"pocket", "newspaper"}
        assert {r["added_by"] for r in stored} == {"culture-rules"}, "stamped by the verb"

        played = len(rig.endpoint.played)
        answer = rig.app.controls().chat(QUESTION)

        assert answer["ok"] is True, answer
        # The fake model only knows what the prompt carried: its echo is the proof
        # that both facts reached the prompt through recall.
        assert "otter survey moved to thursday" in answer["reply"]
        assert "harbour bridge closes for repairs" in answer["reply"]
        by_source = {s["source"]: s for s in answer["sources"]}
        assert set(by_source) == {"pocket", "newspaper"}
        rec = by_source["pocket"]
        assert (rec["title"], rec["date"], rec["start"]) == (POCKET_TITLE, POCKET_DATE, 65)
        paper = by_source["newspaper"]
        assert (paper["date"], paper["link"]) == ("2026-10-01", NEWS_LINK)
        # ...and the same provenance reached the prompt, as quoted data (c-render funnel).
        prompt = echoing_complete.prompts[-1]
        assert mem.BEGIN_MARK in prompt
        assert POCKET_TITLE in prompt and POCKET_DATE in prompt and "01:05" in prompt
        assert "2026-10-01" in prompt and "city" in prompt and NEWS_LINK in prompt
        assert len(rig.endpoint.played) == played, "a typed turn never reaches the speaker"

    def test_recall_is_semantic_through_the_named_role_and_embeds_only_the_query(
        self, rig: Rig
    ) -> None:
        seed_both(rig)
        assert set(rig.transport.models) == {vectors.EMBED_ROLE} == {"gemma2-embed"}
        assert (
            rig.cache.get(OTTER) is not None and rig.cache.get(BRIDGE) is not None
        ), "the drain fills the vector cache when it writes"

        before = len(rig.transport.inputs)
        answer = rig.app.controls().chat(QUESTION)

        assert answer["ok"] is True and len(answer["sources"]) == 2
        assert rig.transport.inputs[before:] == [QUESTION], "a recall embeds exactly the query"
        recall = rig.app.status()["recall"]
        assert recall["embedder"]["role"] == "gemma2-embed"
        assert rig.memory.status()["last_recall_mode"] == "semantic"

    def test_an_unrelated_question_recalls_nothing(self, rig: Rig) -> None:
        seed_both(rig)
        answer = rig.app.controls().chat("completely different subject matter entirely")
        assert answer["ok"] is True
        assert answer["sources"] == []


class TestTalkByVoiceAndByText:
    def test_a_spoken_turn_recalls_and_speaks_a_cited_fact(self, rig: Rig) -> None:
        seed_both(rig)
        rig.events("reply")
        rig.app.run_turn(QUESTION)

        replies = [e.data["text"] for e in rig.events("reply")]
        assert replies and "otter survey moved to thursday" in replies[0]
        assert rig.endpoint.played, "the voice turn was spoken through the host endpoint"
        assert POCKET_TITLE in echoing_complete.prompts[-1]
        assert NEWS_LINK in echoing_complete.prompts[-1]

    def test_a_typed_turn_cites_both_sources(self, rig: Rig) -> None:
        seed_both(rig)
        played = len(rig.endpoint.played)
        answer = rig.app.controls().chat(QUESTION)
        assert answer["ok"] is True
        assert {s["source"] for s in answer["sources"]} == {"pocket", "newspaper"}
        assert len(rig.endpoint.played) == played


# ── clause 2: text behind Access ──────────────────────────────────────────────


class TestTextBehindAccess:
    """The daemon's real chat control behind a real guard and the real verifier.

    Not provable synthetically: Cloudflare itself (the edge adding the
    assertion, the tunnel, DNS) and the real team JWKS.
    """

    def _serve(self, rig: Rig) -> server_module.DashboardServer:
        verifier = access.AccessVerifier(
            TEAM,
            AUD,
            frozenset({OPERATOR}),
            fetch_jwks=lambda: jwks(),
            clock=lambda: float(NOW),
        )
        origin = f"https://{PUBLIC_HOST}"
        guard = guard_module.Guard(
            guard_module.GuardConfig(
                install_secret="synthetic-install-secret",
                allowed_origins=frozenset({origin}),
                public_hostname=PUBLIC_HOST,
            ),
            assertion_verifier=verifier,
        )
        server = server_module.DashboardServer(
            config=server_module.ServerConfig(port=0, wall_clock=lambda: float(NOW)),
            guard=guard,
            bus=rig.bus,
            controls=rig.app.controls(),
        )
        server.start()
        return server

    def _post(self, server: Any, **headers: str) -> tuple[int, dict[str, Any]]:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
        hdrs = {"Host": PUBLIC_HOST, "Content-Type": "application/json", **headers}
        conn.request("POST", "/api/chat", body=json.dumps({"message": QUESTION}), headers=hdrs)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, {}

    def test_an_operator_assertion_reaches_a_cited_answer_without_the_install_secret(
        self, rig: Rig
    ) -> None:
        seed_both(rig)
        server = self._serve(rig)
        try:
            origin = f"https://{PUBLIC_HOST}"
            status, body = self._post(
                server, Origin=origin, **{"Cf-Access-Jwt-Assertion": token(claims())}
            )
            assert status == 200, (status, body)
            result = body.get("result", body)
            assert {s["source"] for s in result["sources"]} == {"pocket", "newspaper"}

            # No assertion: refused, and the public Host never falls back to nothing.
            status, _ = self._post(server, Origin=origin)
            assert status in (401, 403)
            # Another identity's validly-signed token: refused.
            other = token(claims(email="intruder@example.com"))
            status, _ = self._post(server, Origin=origin, **{"Cf-Access-Jwt-Assertion": other})
            assert status in (401, 403)
            # A cross-site POST carrying a valid assertion: refused (CSRF rule).
            status, _ = self._post(
                server,
                Origin="https://evil.example",
                **{"Cf-Access-Jwt-Assertion": token(claims())},
            )
            assert status in (401, 403)
        finally:
            server.shutdown(2.0)


# ── clauses 4 and 5: one verb, one writer ─────────────────────────────────────


class TestOneVerbOneWriter:
    def test_the_verb_runs_under_the_producers_runner_and_is_idempotent(self, rig: Rig) -> None:
        # rig.cli() IS the producer's runner: env {PATH, LANG}, stdin closed, a
        # throwaway cwd, absolute --file / --state-dir, exit 0 asserted inside.
        batch = [pocket(1, OTTER), pocket(2)]
        first = rig.ingest(batch, "same-file")
        assert first  # a JSON result, not a traceback
        rig.drain_intervals_until(lambda: rig.pending() == 0)
        scope_files = sorted(
            p for p in rig.store_dir.rglob("*") if p.is_file() and mem.INGEST_SCOPE in str(p)
        )
        snapshot = {p: p.read_bytes() for p in scope_files}
        assert len(rig.store_records(mem.INGEST_SCOPE)) == 2

        rig.ingest(batch, "same-file")  # the engine retried the step
        rig.drain_intervals_until(lambda: rig.pending() == 0)

        assert len(rig.store_records(mem.INGEST_SCOPE)) == 2, "re-enqueue duplicated records"
        assert {p: p.read_bytes() for p in scope_files} == snapshot, "the store changed"

    def test_the_verb_never_writes_the_store_only_the_running_daemon_does(self, rig: Rig) -> None:
        rig.ingest([pocket(1, OTTER)], "pocket-batch")

        inbox_files = sorted((rig.state_dir / "ingest" / "inbox").glob("*.json"))
        assert len(inbox_files) == 1, "the verb enqueued one batch into the daemon's inbox"
        assert rig.store_records(mem.INGEST_SCOPE) == []
        assert not [p for p in rig.store_dir.rglob("*") if p.is_file()], "the verb wrote the store"
        modes = {p: p.stat().st_mode & 0o777 for p in inbox_files}
        assert set(modes.values()) == {0o600}
        assert {p.parent.stat().st_mode & 0o777 for p in inbox_files} == {0o700}

        rig.drain_intervals_until(lambda: rig.pending() == 0)
        assert len(rig.store_records(mem.INGEST_SCOPE)) == 1
        assert list((rig.state_dir / "ingest" / "inbox").glob("*.json")) == []
        # Only the drain worker (one thread) ever writes the ingest scope: the
        # daemon's own remember tool writes the gwen scope and nothing else.
        assert rig.app.status()["ingest"]["worker"]["failures"] == 0


# ── criterion 3: nothing lost ─────────────────────────────────────────────────


class TestNothingLoses:
    def test_100_interleaved_ingest_and_remember_writes_lose_no_record(self, rig: Rig) -> None:
        per_batch, batches = 20, 5  # 100 ingested records
        for b in range(batches):
            records = [pocket(b * per_batch + k) for k in range(per_batch)]
            rig.ingest(records, f"interleave-{b}")
            for k in range(per_batch):  # 100 remember writes, interleaved with the drain
                n = b * per_batch + k
                assert rig.app._remember_tool(fact=f"synthetic remembered note {n}").startswith(
                    "ok"
                )
            rig.app.pump()

        deadline = time.monotonic() + 60.0
        while rig.pending() and time.monotonic() < deadline:
            rig.app.pump()
            time.sleep(0.02)
        assert rig.pending() == 0

        ingested = rig.store_records(mem.INGEST_SCOPE)
        notes = [r for r in rig.store_records("gwen") if "synthetic remembered note" in r["text"]]
        assert len(ingested) == per_batch * batches == 100
        assert len(notes) == per_batch * batches == 100
        assert {r["text"] for r in notes} == {f"synthetic remembered note {n}" for n in range(100)}
        assert {r["metadata"]["recording_id"] for r in ingested} == {
            f"synrec{n}" for n in range(100)
        }
        failed = [p for p in (rig.state_dir / "inbox").rglob("*") if "failed" in str(p)]
        assert [p for p in failed if p.is_file()] == []


# ── criterion 4 / h46: forget leaves no second copy ───────────────────────────


class TestForgetLeavesNoCopy:
    """After ingest then forget of a recording, only the archived store record keeps its text.

    Scope note: a conversation that quoted a fact is the conversation's own
    transcript, not the spool's copy; the turn here asks only about the surviving
    recording, so the scan can be strict.
    """

    def test_no_file_but_the_archived_store_record_holds_the_forgotten_text_or_vector(
        self, rig: Rig
    ) -> None:
        gone_text = f"synthetic gone fact {POCKET_SENTINEL}-gone about badgers"
        kept_text = f"synthetic second fact {POCKET_SENTINEL}-kept about badgers"
        second_fact_of_recording_7 = pocket(7, kept_text)
        second_fact_of_recording_7["id"] = "pocket:synrec7:fact:70"
        rig.ingest(
            [
                pocket(7, gone_text),
                second_fact_of_recording_7,
            ],
            "doomed",
        )
        rig.ingest([pocket(9, f"synthetic other fact {POCKET_SENTINEL}-other about swans")], "kept")
        rig.drain_intervals_until(lambda: rig.pending() == 0)
        other_text = f"synthetic other fact {POCKET_SENTINEL}-other about swans"
        rig.wait_vectors(gone_text, kept_text, other_text)
        # Sanity: before the forget, the sentinel is in the store AND its vector is cached.
        assert any(gone_text == r["text"] for r in rig.store_records(mem.INGEST_SCOPE))
        assert rig.cache.get(other_text) is not None
        gone_vector = bag_vector(gone_text)
        assert rig.cache.get(gone_text) == gone_vector

        # A turn about the surviving recording only (so no transcript quotes the doomed one).
        answer = rig.app.controls().chat("other swans")
        assert answer["ok"] is True

        rig.forget("pocket", "synrec7")
        rig.drain_intervals_until(lambda: rig.pending() == 0)
        rig.app.close(deadline=3.0)

        # 1. The store: both of recording 7's records are archived (hidden), the other lives.
        everything = rig.store_records(mem.INGEST_SCOPE)
        archived = {r["text"] for r in everything if r.get("lifecycle") == "archived"}
        live = {r["text"] for r in everything if r.get("lifecycle") != "archived"}
        assert archived == {gone_text, kept_text}
        assert len(live) == 1 and f"{POCKET_SENTINEL}-other" in next(iter(live))

        # 2. The vector cache dropped the forgotten entries and kept the survivor's.
        fresh = mem.vector_cache_for(rig.cache_dir)
        assert fresh.get(gone_text) is None
        assert fresh.get(kept_text) is None
        cache_blob = (rig.cache_dir / vectors.CACHE_FILENAME).read_text(encoding="utf-8")
        entries = json.loads(cache_blob)["entries"]
        assert all("synrec7" not in str(e.get("record_id")) for e in entries.values())
        assert gone_vector not in [e["vec"] for e in entries.values()]
        assert any("synrec9" in str(e.get("record_id")) for e in entries.values())
        assert (rig.cache_dir / vectors.CACHE_FILENAME).stat().st_mode & 0o777 == 0o600

        # 3. Grep the whole state dir: the sentinel survives only in the store's archived record.
        needle = f"{POCKET_SENTINEL}-gone".encode()
        holders = [p for p in rig.state_dir.rglob("*") if p.is_file() and needle in p.read_bytes()]
        outside_store = [p for p in holders if rig.store_dir not in p.parents]
        assert outside_store == [], f"a second copy of forgotten text: {outside_store}"
        for p in holders:
            assert rig.store_dir in p.parents
            assert b"archived" in p.read_bytes(), f"{p} holds the text but is not archived"
        # The spool never held a second copy of ANY memory text, forgotten or not.
        spooled = [p for p in (rig.state_dir / "ingest").rglob("*") if p.is_file()]
        assert spooled, "expected done/ receipts"
        for p in spooled:
            assert POCKET_SENTINEL.encode() not in p.read_bytes(), f"{p} keeps memory text"


# ── a gap the acceptance run exposed ──────────────────────────────────────────


class TestTheVectorIsFilledByTheWrite:
    """The spec: vectors are "filled when the drain writes and when remember writes".

    What is shipped: only the background backfill (``RoomMemory.backfill_tick``,
    30 s idle cadence by default) caches a vector; ``ingest_records`` and the
    remember path never do. Until the next tick a freshly drained or remembered
    record is skipped by semantic recall (``uncached_skipped``) — a fact the
    operator was just told was applied is invisible to recall for up to the
    backfill's idle interval. Recorded as strict xfails, not fixed here (this
    module owns tests only).
    """

    @pytest.fixture
    def slow_rig(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        built = Rig(tmp_path, backfill=False)
        built.app.start()
        yield built
        built.close()

    @pytest.mark.xfail(
        strict=True,
        reason="ingest_records never caches the record's vector; only the backfill tick does",
    )
    def test_a_drained_record_has_its_vector_before_any_backfill_tick(self, slow_rig: Rig) -> None:
        slow_rig.ingest([pocket(1, OTTER)], "pocket-batch")
        slow_rig.drain_intervals_until(lambda: slow_rig.pending() == 0)
        assert slow_rig.cache.get(OTTER) is not None

    @pytest.mark.xfail(
        strict=True,
        reason="the remember path never caches the record's vector; only the backfill tick does",
    )
    def test_a_remembered_record_has_its_vector_before_any_backfill_tick(
        self, slow_rig: Rig
    ) -> None:
        assert slow_rig.app._remember_tool(fact="synthetic remembered otter note").startswith("ok")
        assert slow_rig.cache.get("synthetic remembered otter note") is not None


# ── what is not claimed ───────────────────────────────────────────────────────


class TestWhatIsNotClaimed:
    def test_the_module_docstring_states_what_cannot_be_proven_synthetically(self) -> None:
        doc = __doc__ or ""
        assert "pocketchat can retire" in doc and "NOT provable" in doc
        assert "Cloudflare edge" in doc
        for clause in range(1, 9):
            assert f"\n{clause}. " in doc, f"clause {clause} is not mapped"
