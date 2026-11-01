"""tests for embodiment.mesh_client — Gwen's own stdlib IRC client to agentirc.

Every test runs against :class:`FakeIrcd`, an asyncio IRC server on an
ephemeral 127.0.0.1 port in its own thread. Nothing here dials the real mesh
(localhost:6667): the one test that reads the defaults never starts a client.
"""

from __future__ import annotations

import ast
import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from embodiment import mesh_client
from embodiment import mesh_client as mc
from embodiment.mesh_client import (
    MeshClient,
    MeshCloseReport,
    MeshConfig,
    MeshMessage,
    parse_line,
)

WAIT = 5.0


def wait_for(predicate: Callable[[], object], timeout: float = WAIT) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


class FakeIrcd:
    """A scriptable agentirc stand-in: registration, CAP, JOIN, WHO, PING, THREAD."""

    def __init__(self, name: str = "spark") -> None:
        self.name = name
        self.lines: list[str] = []
        self.connections = 0
        self.nick_reply: Optional[str] = None  # "433" / "432" to refuse NICK
        self.ack_caps = True
        self.answer_ping = True
        self.send_welcome = True
        self.who_members: dict[str, list[tuple[str, str]]] = {}  # chan -> [(nick, server)]
        self._writers: list[asyncio.StreamWriter] = []
        self._loop = asyncio.new_event_loop()
        self._server: Optional[asyncio.base_events.Server] = None
        self._port = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(WAIT)

    # ── plumbing ────────────────────────────────────────────────────────────
    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def boot() -> None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            self._port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(boot())
        self._loop.run_forever()

    @property
    def port(self) -> int:
        return self._port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        nick = ""

        def out(line: str) -> None:
            writer.write((line + "\r\n").encode())

        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().rstrip("\r\n")
                self.lines.append(line)
                parts = line.split(" ")
                cmd = parts[0].upper()
                if cmd == "CAP" and len(parts) > 1 and parts[1] == "REQ":
                    verdict = "ACK" if self.ack_caps else "NAK"
                    out(f":{self.name} CAP * {verdict} :message-tags")
                elif cmd == "NICK":
                    nick = parts[1]
                    if self.nick_reply:
                        out(f":{self.name} {self.nick_reply} * {nick} :refused")
                        nick = ""
                elif cmd == "USER" and nick and self.send_welcome:
                    out(f":{self.name} 001 {nick} :Welcome to {self.name} IRC Network")
                elif cmd == "JOIN":
                    out(f":{nick}!gwen@127.0.0.1 JOIN {parts[1]}")
                elif cmd == "WHO":
                    chan = parts[1]
                    for member, server in self.who_members.get(chan, [(nick, self.name)]):
                        out(f":{self.name} 352 {nick} {chan} u 127.0.0.1 {server} {member} H :0 x")
                    out(f":{self.name} 315 {nick} {chan} :End of WHO list")
                elif cmd == "PING" and self.answer_ping:
                    token = line.split(" ", 1)[1] if " " in line else ""
                    out(f":{self.name} PONG {self.name} {token}")
                elif cmd == "QUIT":
                    break
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    def call(self, fn: Callable[[], object]) -> None:
        done = threading.Event()

        def run() -> None:
            try:
                fn()
            finally:
                done.set()

        self._loop.call_soon_threadsafe(run)
        done.wait(WAIT)

    def push(self, line: str) -> None:
        def go() -> None:
            for w in self._writers:
                if not w.is_closing():
                    w.write((line + "\r\n").encode())

        self.call(go)

    def drop_all(self) -> None:
        def go() -> None:
            for w in self._writers:
                w.close()
            self._writers.clear()

        self.call(go)

    def sent(self, prefix: str) -> list[str]:
        return [line for line in list(self.lines) if line.startswith(prefix)]

    def stop(self) -> None:
        def go() -> None:
            if self._server is not None:
                self._server.close()
            for w in self._writers:
                w.close()

        self.call(go)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(WAIT)


@pytest.fixture
def ircd():
    server = FakeIrcd()
    yield server
    server.stop()


def make_config(ircd: FakeIrcd, **overrides) -> MeshConfig:
    base = dict(
        enabled=True,
        nick="spark-gwen",
        host="127.0.0.1",
        port=ircd.port,
        channels=("#general",),
        connect_deadline=1.0,
        register_deadline=1.0,
        backoff_initial=0.05,
        backoff_max=0.2,
        ping_interval=5.0,
        ping_timeout=5.0,
    )
    base.update(overrides)
    return MeshConfig(**base)


@pytest.fixture
def started():
    clients: list[MeshClient] = []

    def start(config: MeshConfig, on_message=None) -> MeshClient:
        client = MeshClient(config, on_message=on_message)
        clients.append(client)
        client.start()
        return client

    yield start
    for client in clients:
        client.close(2.0)


# ── config ──────────────────────────────────────────────────────────────────


def test_defaults_from_empty_env():
    config = MeshConfig.from_env({})
    assert config.enabled is True
    assert config.nick == "spark-gwen"
    assert (config.host, config.port) == ("localhost", 6667)
    assert config.channels == ("#general",)
    assert config.error == ""


def test_from_env_reads_the_shared_names():
    config = MeshConfig.from_env(
        {
            "EMBODIMENT_MESH": "off",
            "EMBODIMENT_MESH_NICK": "thor-gwen",
            "EMBODIMENT_MESH_SERVER": "10.0.0.2:6697",
            "EMBODIMENT_MESH_CHANNELS": " #a, #b ,#a,notachannel,",
        }
    )
    assert config.enabled is False
    assert config.nick == "thor-gwen"
    assert (config.host, config.port) == ("10.0.0.2", 6697)
    assert config.channels == ("#a", "#b")


@pytest.mark.parametrize("value", ["localhost", "localhost:0", "localhost:99999", "h:x", ":1"])
def test_bad_server_is_a_recorded_error_not_a_raise(value):
    config = MeshConfig.from_env({"EMBODIMENT_MESH_SERVER": value})
    assert config.error == "mesh-config-invalid"


def test_config_has_no_model_input():
    """The nick comes from config; nothing in the config names a model."""
    fields = set(MeshConfig.__dataclass_fields__)
    assert not any("model" in name for name in fields)


def test_derived_bounds():
    config = MeshConfig(backoff_max=7.0, connect_deadline=2.0, register_deadline=3.0)
    assert config.reconnect_bound == pytest.approx(12.0)
    config = MeshConfig(ping_interval=10.0, ping_timeout=4.0)
    assert config.liveness_bound == pytest.approx(14.0)


def test_backoff_doubles_and_is_capped():
    config = MeshConfig(backoff_initial=0.5, backoff_max=3.0)
    assert [config.backoff(n) for n in range(6)] == [0.5, 1.0, 2.0, 3.0, 3.0, 3.0]


# ── wire parsing ────────────────────────────────────────────────────────────


def test_parse_line_with_tags_prefix_and_trailing():
    msg = parse_line(
        r"@msgid=abc;time=2026-10-31T00:00:00Z;agentirc.io/thread=ask-1;x=a\sb\:c"
        " :spark-ori!ori@h PRIVMSG #general :hello there"
    )
    assert msg.tags == {
        "msgid": "abc",
        "time": "2026-10-31T00:00:00Z",
        "agentirc.io/thread": "ask-1",
        "x": "a b;c",
    }
    assert msg.prefix == "spark-ori!ori@h"
    assert msg.nick == "spark-ori"
    assert msg.command == "PRIVMSG"
    assert msg.params == ["#general", "hello there"]


def test_parse_line_never_raises_on_garbage():
    for raw in ["", " ", "@", ":", "@a=b", ":x", "\x00\x01"]:
        msg = parse_line(raw)
        assert msg is None or isinstance(msg.command, str)


# ── connect, register, join ─────────────────────────────────────────────────


def test_registers_as_configured_nick_and_joins(ircd, started):
    client = started(make_config(ircd, channels=("#general", "#gwen")))
    wait_for(lambda: client.status()["state"] == "registered")
    wait_for(lambda: set(client.status()["joined"]) == {"#general", "#gwen"})
    assert ircd.sent("NICK") == ["NICK spark-gwen"]
    assert ircd.sent("CAP REQ") == ["CAP REQ :message-tags"]
    assert ircd.sent("USER")
    assert "JOIN #general" in ircd.lines
    assert "JOIN #gwen" in ircd.lines
    assert client.status()["message_tags"] is True


def test_mesh_off_never_connects(ircd, started):
    client = started(make_config(ircd, enabled=False))
    time.sleep(0.2)
    assert ircd.connections == 0
    assert client.status()["state"] == "off"
    assert client.send("#general", "hi") is False


def test_invalid_config_stays_off_and_records(ircd, started):
    client = started(make_config(ircd, error="mesh-config-invalid"))
    time.sleep(0.1)
    assert ircd.connections == 0
    assert [d.code for d in client.degradations] == ["mesh-config-invalid"]


@pytest.mark.parametrize("nick", ["", "system-gwen", "bad nick", "x\r\nQUIT"])
def test_invalid_nick_stays_off(ircd, started, nick):
    client = started(make_config(ircd, nick=nick))
    time.sleep(0.1)
    assert ircd.connections == 0
    assert [d.code for d in client.degradations] == ["mesh-nick-invalid"]


# ── nick refusals ───────────────────────────────────────────────────────────


def test_nick_in_use_records_sends_nothing_and_stays_off(ircd, started):
    ircd.nick_reply = "433"
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "nick-taken")
    before = len(ircd.lines)
    assert client.send("#general", "hello") is False
    assert client.create_thread("#general", "ask-1", "q") is False
    time.sleep(0.4)  # several backoff_max windows: no reconnect
    assert ircd.connections == 1
    assert len(ircd.lines) == before
    assert not ircd.sent("PRIVMSG")
    assert not ircd.sent("JOIN")
    assert not ircd.sent("QUIT")
    assert [d.code for d in client.degradations] == ["mesh-nick-taken"]


def test_erroneous_nick_records_and_stays_off(ircd, started):
    ircd.nick_reply = "432"
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "nick-refused")
    time.sleep(0.3)
    assert ircd.connections == 1
    assert [d.code for d in client.degradations] == ["mesh-nick-refused"]


# ── send ────────────────────────────────────────────────────────────────────


def test_send_privmsg_from_another_thread(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    results: list[bool] = []
    worker = threading.Thread(target=lambda: results.append(client.send("#general", "שלום")))
    worker.start()
    worker.join(WAIT)
    assert results == [True]
    wait_for(lambda: ircd.sent("PRIVMSG #general"))
    assert ircd.sent("PRIVMSG #general") == ["PRIVMSG #general :שלום"]
    # The server can see the line before the client's loop counts it (CI, PR #119):
    # wait for the counter rather than read it once.
    wait_for(lambda: client.status()["counts"]["sent"] >= 1)
    assert client.status()["counts"]["sent"] == 1  # still shows a double count


def test_send_splits_lines_and_never_injects(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    assert client.send("#general", "one\r\nQUIT :x\n\ntwo\x00")
    wait_for(lambda: len(ircd.sent("PRIVMSG")) == 3)
    time.sleep(0.05)
    assert ircd.sent("PRIVMSG") == [
        "PRIVMSG #general :one",
        "PRIVMSG #general :QUIT :x",
        "PRIVMSG #general :two",
    ]
    assert not ircd.sent("QUIT")


def test_send_chunks_long_text_under_the_server_line_cap(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    text = "א" * 9000  # 18000 bytes
    assert client.send("#general", text)
    wait_for(lambda: "".join(line.split(" :", 1)[1] for line in ircd.sent("PRIVMSG")) == text)
    for line in ircd.sent("PRIVMSG"):
        assert len((line + "\r\n").encode()) <= mesh_client.MAX_LINE_BYTES


def test_send_refuses_unlisted_channel_and_bad_target(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    assert client.send("#secret", "x") is False
    assert client.send("bad target", "x") is False
    assert client.send("#general", "   ") is False
    assert client.send("spark-ori", "dm") is True
    wait_for(lambda: ircd.sent("PRIVMSG spark-ori"))
    assert not ircd.sent("PRIVMSG #secret")


def test_send_before_registration_is_refused(ircd, started):
    ircd.send_welcome = False
    client = started(make_config(ircd, register_deadline=5.0))
    wait_for(lambda: ircd.sent("USER"))
    assert client.send("#general", "early") is False


# ── threads ─────────────────────────────────────────────────────────────────


def test_create_and_reply_thread(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    assert client.create_thread("#general", "ask-1", "who knows?\nsecond line")
    assert client.reply_thread("#general", "ask-1", "an answer")
    wait_for(lambda: len(ircd.sent("THREAD")) == 3)
    assert ircd.sent("THREAD") == [
        "THREAD CREATE #general ask-1 :who knows?",
        "THREAD REPLY #general ask-1 :second line",
        "THREAD REPLY #general ask-1 :an answer",
    ]


@pytest.mark.parametrize("name", ["", "-x", "x-", "a b", "a" * 33, "ask_1"])
def test_thread_name_must_match_agentirc_regex(ircd, started, name):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    assert client.create_thread("#general", name, "q") is False
    assert client.reply_thread("#general", name, "q") is False


def test_thread_refuses_unlisted_channel(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    assert client.create_thread("#secret", "ask-1", "q") is False


# ── inbound ─────────────────────────────────────────────────────────────────


def _inbound(ircd, started, **cfg):
    got: list[MeshMessage] = []
    client = started(make_config(ircd, **cfg), on_message=got.append)
    wait_for(lambda: client.status()["state"] == "registered")
    wait_for(lambda: "#general" in client.status()["joined"])
    return client, got


def test_inbound_channel_message_and_mention(ircd, started):
    client, got = _inbound(ircd, started)
    ircd.push(":spark-ori!ori@h PRIVMSG #general :just chatting")
    ircd.push("@msgid=m2;time=t2 :spark-ori!ori@h PRIVMSG #general :spark-gwen: are you there?")
    ircd.push(":spark-ori!ori@h PRIVMSG #general :hey @Spark-Gwen look")
    ircd.push(":spark-ori!ori@h PRIVMSG #general :spark-gwenny is someone else")
    wait_for(lambda: len(got) == 4)
    assert [m.mention for m in got] == [False, True, True, False]
    assert got[0].sender == "spark-ori"
    assert got[0].channel == "#general"
    assert got[0].text == "just chatting"
    assert got[0].direct is False
    assert (got[1].msgid, got[1].server_time) == ("m2", "t2")
    # Counted after the callback returns, so it can trail ``got`` by a moment.
    wait_for(lambda: client.status()["counts"]["delivered"] >= 4)
    assert client.status()["counts"]["delivered"] == 4  # still shows a double count


def test_inbound_direct_message(ircd, started):
    _, got = _inbound(ircd, started)
    ircd.push(":thor-claude!c@h PRIVMSG spark-gwen :private hello")
    wait_for(lambda: len(got) == 1)
    assert got[0].direct is True
    assert got[0].mention is True
    assert got[0].channel is None
    assert got[0].target == "spark-gwen"


def test_inbound_thread_tag_and_prefix_stripped(ircd, started):
    _, got = _inbound(ircd, started)
    ircd.push(
        "@msgid=m9;agentirc.io/thread=ask-1 :thor-claude!c@h PRIVMSG #general "
        ":[thread:ask-1] the answer"
    )
    wait_for(lambda: len(got) == 1)
    assert got[0].thread == "ask-1"
    assert got[0].msgid == "m9"
    assert got[0].text == "the answer"


def test_inbound_ignores_unlisted_channel_and_self(ircd, started):
    _, got = _inbound(ircd, started)
    ircd.push(":spark-ori!ori@h PRIVMSG #elsewhere :not for us")
    ircd.push(":spark-gwen!g@h PRIVMSG #general :my own echo")
    ircd.push(":spark-ori!ori@h PRIVMSG #general :marker")
    wait_for(lambda: len(got) == 1)
    time.sleep(0.05)
    assert [m.text for m in got] == ["marker"]


def test_callback_failure_is_recorded_without_text(ircd, started):
    def boom(_msg: MeshMessage) -> None:
        raise RuntimeError("secret words the user said")

    client = started(make_config(ircd), on_message=boom)
    wait_for(lambda: "#general" in client.status()["joined"])
    ircd.push(":spark-ori!ori@h PRIVMSG #general :secret words the user said")
    wait_for(lambda: any(d.code == "mesh-callback-failed" for d in client.degradations))
    assert client.status()["state"] == "registered"
    for d in client.degradations:
        assert "secret" not in d.reason
    assert "secret" not in repr(client.status())


def test_answers_server_ping(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    ircd.push("PING :spark")
    wait_for(lambda: ircd.sent("PONG"))
    assert ircd.sent("PONG") == ["PONG :spark"]


# ── servers visible ─────────────────────────────────────────────────────────


def test_servers_visible_from_welcome_and_who(ircd, started):
    ircd.who_members = {
        "#general": [("spark-gwen", "spark"), ("thor-claude", "thor"), ("orin-x", "orin")]
    }
    client = started(make_config(ircd))
    wait_for(lambda: client.servers() == ("orin", "spark", "thor"))
    assert client.status()["servers"] == ["orin", "spark", "thor"]
    assert client.status()["server_name"] == "spark"


def test_servers_refresh_on_join_and_quit(ircd, started):
    ircd.who_members = {"#general": [("spark-gwen", "spark")]}
    client = started(make_config(ircd))
    wait_for(lambda: client.servers() == ("spark",))
    ircd.who_members = {"#general": [("spark-gwen", "spark"), ("thor-claude", "thor")]}
    ircd.push(":thor-claude!c@h JOIN #general")
    wait_for(lambda: client.servers() == ("spark", "thor"))
    ircd.who_members = {"#general": [("spark-gwen", "spark")]}
    ircd.push(":thor-claude!c@h QUIT :gone")
    wait_for(lambda: client.servers() == ("spark",))


def test_servers_empty_when_off(ircd):
    client = MeshClient(make_config(ircd, enabled=False))
    assert client.servers() == ()


# ── drop + reconnect ────────────────────────────────────────────────────────


def test_dropped_connection_records_and_reconnects(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    ircd.drop_all()
    wait_for(lambda: any(d.code == "mesh-connection-dropped" for d in client.degradations))
    wait_for(lambda: ircd.connections == 2 and client.status()["state"] == "registered")
    assert client.status()["counts"]["reconnects"] == 1
    assert ircd.sent("NICK") == ["NICK spark-gwen", "NICK spark-gwen"]
    wait_for(lambda: ircd.lines.count("JOIN #general") == 2)


def test_connect_refused_backs_off_within_bound(ircd, started):
    port = ircd.port
    ircd.stop()
    config = make_config(ircd, port=port, backoff_initial=0.02, backoff_max=0.05)
    client = started(config)
    wait_for(lambda: client.status()["counts"]["connect_attempts"] >= 4)
    codes = [d.code for d in client.degradations]
    # one record per failure streak, not per attempt
    assert codes == ["mesh-connect-failed"]
    assert client.status()["state"] in ("backoff", "connecting")


def test_silent_server_trips_liveness_and_reconnects(ircd, started):
    ircd.answer_ping = False
    client = started(make_config(ircd, ping_interval=0.1, ping_timeout=0.1))
    wait_for(lambda: client.status()["state"] == "registered")
    wait_for(lambda: ircd.sent("PING"))
    wait_for(lambda: any(d.code == "mesh-connection-dropped" for d in client.degradations))
    wait_for(lambda: ircd.connections >= 2)


def test_register_timeout_records_and_retries(ircd, started):
    ircd.send_welcome = False
    client = started(make_config(ircd, register_deadline=0.1))
    wait_for(lambda: any(d.code == "mesh-register-timeout" for d in client.degradations))
    wait_for(lambda: ircd.connections >= 2)


# ── close ───────────────────────────────────────────────────────────────────


def test_close_sends_quit_and_reports_graceful(ircd):
    client = MeshClient(make_config(ircd))
    client.start()
    wait_for(lambda: client.status()["state"] == "registered")
    report = client.close(2.0)
    assert isinstance(report, MeshCloseReport)
    assert report.graceful
    assert client.status()["state"] == "closed"
    wait_for(lambda: ircd.sent("QUIT"))
    assert client.send("#general", "after close") is False


def test_close_never_raises_unstarted_twice_and_zero_deadline(ircd):
    client = MeshClient(make_config(ircd))
    assert client.close(0.0).graceful
    assert client.close(None).graceful
    started_client = MeshClient(make_config(ircd))
    started_client.start()
    started_client.close(0.0)
    started_client.close(1.0)


def test_close_while_backing_off_is_prompt(ircd):
    port = ircd.port
    ircd.stop()
    client = MeshClient(make_config(ircd, port=port, backoff_initial=30.0, backoff_max=30.0))
    client.start()
    wait_for(lambda: client.status()["state"] == "backoff")
    began = time.monotonic()
    report = client.close(2.0)
    assert time.monotonic() - began < 2.0
    assert report.graceful


def test_start_is_idempotent(ircd):
    client = MeshClient(make_config(ircd))
    client.start()
    client.start()
    wait_for(lambda: client.status()["state"] == "registered")
    time.sleep(0.1)
    assert ircd.connections == 1
    client.close(2.0)


# ── structure ───────────────────────────────────────────────────────────────


def test_stdlib_only_and_no_subprocess():
    source = Path(mesh_client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    allowed = {
        "__future__",
        "asyncio",
        "collections",
        "contextlib",  # PresenceGauge.busy (#121)
        "dataclasses",
        "datetime",  # PRESENCE "since" (#121)
        "json",  # the PRESENCE payload (#121)
        "os",
        "re",
        "threading",
        "time",
        "typing",
        "embodiment",
    }
    assert imported <= allowed, imported - allowed
    assert "subprocess" not in imported
    assert "culture" not in imported


def test_a_create_line_filled_to_the_budget_still_fits_the_server_bound(ircd, started):
    """Review of #117: chunks were sized for 'THREAD REPLY', one byte shorter than the
    'THREAD CREATE' head the first line gets, so it could reach 8193 bytes."""
    client = started(make_config(ircd))
    wait_for(lambda: client.status()["state"] == "registered")
    reply_head = "THREAD REPLY #general ask-1 :"
    text = "x" * (mc.MAX_LINE_BYTES - len(reply_head) - 2)  # fills the REPLY budget exactly
    assert client.create_thread("#general", "ask-1", text)
    wait_for(lambda: len(ircd.sent("THREAD")) >= 1)
    lines = ircd.sent("THREAD")
    assert lines[0].startswith("THREAD CREATE #general ask-1 :")
    assert all(len(line.encode()) + 2 <= mc.MAX_LINE_BYTES for line in lines)
    assert "".join(line.split(" :", 1)[1] for line in lines) == text


# ── a line in flight is counted once, whatever ends the pump (review of #119) ──


class _Writer:
    def __init__(self, drain: Any) -> None:
        self.written: list[bytes] = []
        self._drain = drain

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        await self._drain()


def _pump_once(client: MeshClient, drain: Any, *, cancel: bool = False) -> None:
    async def run() -> None:
        outbox: "asyncio.Queue[str]" = asyncio.Queue()
        outbox.put_nowait("PRIVMSG #general :hello")
        task = asyncio.ensure_future(client._pump(outbox, _Writer(drain)))
        if cancel:
            await asyncio.sleep(0.05)
            task.cancel()
        with pytest.raises((ConnectionResetError, asyncio.CancelledError)):
            await task

    asyncio.run(run())


def test_a_line_whose_drain_fails_is_a_dropped_send_not_lost(ircd: Any) -> None:
    client = MeshClient(make_config(ircd))

    async def broken() -> None:
        raise ConnectionResetError("peer gone")

    _pump_once(client, broken)
    counts = client.status()["counts"]
    assert counts["sent"] == 0
    assert counts["dropped_sends"] == 1


def test_a_line_whose_drain_is_cancelled_is_a_dropped_send_not_lost(ircd: Any) -> None:
    client = MeshClient(make_config(ircd))

    async def forever() -> None:
        await asyncio.sleep(3600)

    _pump_once(client, forever, cancel=True)
    counts = client.status()["counts"]
    assert counts["sent"] == 0
    assert counts["dropped_sends"] == 1


# ── presence (embodiment#121, culture protocol/extensions/presence.md) ──────


def _presences(ircd: FakeIrcd) -> list[dict[str, Any]]:
    import json

    return [json.loads(line.split(" :", 1)[1]) for line in ircd.sent("PRESENCE ")]


def test_registration_sets_mode_a_and_publishes_idle(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: ircd.sent("PRESENCE "))
    assert ircd.sent("MODE ") == ["MODE spark-gwen +A"]
    first = _presences(ircd)[0]
    assert first["state"] == "idle"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", first["since"])
    assert client.status()["presence"]["state"] == "idle"


def test_mode_and_presence_come_before_join(ircd, started):
    started(make_config(ircd))
    wait_for(lambda: ircd.sent("JOIN "))
    commands = [line.split(" ", 1)[0] for line in ircd.lines]
    assert commands.index("MODE") < commands.index("JOIN")
    assert commands.index("PRESENCE") < commands.index("JOIN")


def test_a_state_change_is_published_once(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.state == "registered" and ircd.sent("PRESENCE "))
    assert client.set_presence("thinking") is True
    assert client.set_presence("thinking") is True  # no change: nothing sent
    wait_for(lambda: len(ircd.sent("PRESENCE ")) == 2)
    time.sleep(0.1)
    assert [p["state"] for p in _presences(ircd)] == ["idle", "thinking"]
    wait_for(lambda: client.status()["counts"]["presence_sent"] == 2)


def test_unknown_state_is_refused(ircd, started):
    client = started(make_config(ircd))
    assert client.set_presence("asleep") is False
    assert client.status()["presence"]["state"] == "idle"


def test_busy_state_heartbeats_with_its_own_since(ircd, started):
    client = started(make_config(ircd, presence_heartbeat=0.1))
    wait_for(lambda: client.state == "registered")
    client.set_presence("thinking")
    wait_for(lambda: len(ircd.sent("PRESENCE ")) >= 4)
    beats = [p for p in _presences(ircd) if p["state"] == "thinking"]
    assert len({p["since"] for p in beats}) == 1


def test_idle_is_not_heartbeated(ircd, started):
    client = started(make_config(ircd, presence_heartbeat=0.05))
    wait_for(lambda: client.state == "registered" and ircd.sent("PRESENCE "))
    time.sleep(0.3)
    assert len(ircd.sent("PRESENCE ")) == 1


def test_state_set_before_registration_is_the_first_published(ircd, started):
    client = MeshClient(make_config(ircd))
    client.set_presence("thinking")
    client.start()
    try:
        wait_for(lambda: ircd.sent("PRESENCE "))
        assert _presences(ircd)[0]["state"] == "thinking"
    finally:
        client.close(2.0)


def test_graceful_close_sends_draining_before_quit(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.state == "registered" and ircd.sent("PRESENCE "))
    client.close(2.0)
    wait_for(lambda: ircd.sent("QUIT"))
    lines = list(ircd.lines)
    drain = next(i for i, line in enumerate(lines) if '"draining"' in line)
    assert drain < lines.index(next(line for line in lines if line.startswith("QUIT")))


def test_a_server_without_presence_is_recorded_once(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.state == "registered")
    ircd.push(":spark 421 spark-gwen PRESENCE :Unknown command")
    ircd.push(":spark 421 spark-gwen PRESENCE :Unknown command")
    ircd.push(":spark 501 spark-gwen :Unknown MODE flag")
    wait_for(lambda: any(d.code == "mesh-mode-refused" for d in client.degradations))
    codes = [d.code for d in client.degradations]
    assert codes.count("mesh-presence-unsupported") == 1
    assert client.state == "registered"


def test_a_421_for_another_command_is_not_a_presence_record(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.state == "registered")
    ircd.push(":spark 421 spark-gwen FOO :Unknown command")
    ircd.push(":spark 501 spark-gwen :Unknown MODE flag")
    wait_for(lambda: any(d.code == "mesh-mode-refused" for d in client.degradations))
    assert all(d.code != "mesh-presence-unsupported" for d in client.degradations)


class _Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.states: list[str] = []
        self.fail = fail

    def set_presence(self, state: str) -> bool:
        self.states.append(state)
        if self.fail:
            raise RuntimeError("boom")
        return True


def test_gauge_reports_edges_only_across_overlapping_calls():
    gauge = mc.PresenceGauge()
    rec = _Recorder()
    gauge.attach(rec)
    with gauge.busy():
        with gauge.busy():
            assert gauge.inflight == 2
        assert gauge.inflight == 1
    assert gauge.inflight == 0
    assert rec.states == ["idle", "thinking", "idle"]


def test_gauge_tells_a_late_client_the_current_state():
    gauge = mc.PresenceGauge()
    rec = _Recorder()
    with gauge.busy():
        gauge.attach(rec)
    assert rec.states == ["thinking", "idle"]


def test_gauge_without_a_client_is_a_no_op():
    gauge = mc.PresenceGauge()
    with gauge.busy():
        pass
    assert gauge.inflight == 0


def test_gauge_counts_a_raising_client_and_never_raises():
    gauge = mc.PresenceGauge()
    rec = _Recorder()
    gauge.attach(rec)
    rec.fail = True
    with gauge.busy():
        pass
    assert gauge.failures == 2
    assert gauge.inflight == 0


def test_gauge_releases_on_an_exception_inside():
    gauge = mc.PresenceGauge()
    rec = _Recorder()
    gauge.attach(rec)
    with pytest.raises(ValueError):
        with gauge.busy():
            raise ValueError("completion failed")
    assert rec.states[-1] == "idle"
    assert gauge.inflight == 0


def test_presence_reader_counts_lines_sent(ircd, started):
    client = started(make_config(ircd))
    wait_for(lambda: client.presence()["sent"] == 1)
    assert client.presence()["state"] == "idle"


def test_queued_presence_lines_are_not_dropped_sends():
    queue: asyncio.Queue[str] = asyncio.Queue()
    for line in ("PRESENCE :{}", "PRIVMSG #general :hi", "THREAD CREATE #g t :x", "PRESENCE :{}"):
        queue.put_nowait(line)
    assert mc._unsent_messages(queue) == 2
    assert queue.empty()


def test_after_a_421_the_connection_publishes_no_more_presence(ircd, started):
    client = started(make_config(ircd, presence_heartbeat=0.05))
    wait_for(lambda: client.state == "registered" and ircd.sent("PRESENCE "))
    ircd.push(":spark 421 spark-gwen PRESENCE :Unknown command")
    wait_for(lambda: any(d.code == "mesh-presence-unsupported" for d in client.degradations))
    before = len(ircd.sent("PRESENCE "))
    client.set_presence("thinking")
    time.sleep(0.3)
    assert len(ircd.sent("PRESENCE ")) == before
    client.close(2.0)
    wait_for(lambda: ircd.sent("QUIT"))
    assert len(ircd.sent("PRESENCE ")) == before  # no draining either


def test_gauge_tells_the_client_while_holding_its_lock():
    gauge = mc.PresenceGauge()
    held: list[bool] = []

    class Client:
        def set_presence(self, state: str) -> bool:
            held.append(gauge._lock.locked())
            return True

    gauge.attach(Client())
    with gauge.busy():
        pass
    assert held == [True, True, True]


def test_gauge_edges_from_many_threads_end_on_the_true_state():
    gauge = mc.PresenceGauge()
    rec = _Recorder()
    gauge.attach(rec)

    def work() -> None:
        for _ in range(200):
            with gauge.busy():
                pass

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert gauge.inflight == 0
    assert rec.states[-1] == "idle"
    # strictly alternating: every edge was told in the order it happened
    assert all(a != b for a, b in zip(rec.states[1:], rec.states[2:]))
