import os
import stat

from embodiment import mesh_asks as ma

NICK = "spark-gwen"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def book(tmp_path, clock=None):
    return ma.AskBook(tmp_path / ma.ASKS_NAME, clock or Clock())


def test_file_is_0600_and_survives_restart_with_answer(tmp_path):
    clock = Clock()
    b = book(tmp_path, clock)
    b.begin_turn("t1")
    ask = b.open_ask("t1", "conv1", "#room", "who?").ask
    assert stat.S_IMODE(os.stat(tmp_path / ma.ASKS_NAME).st_mode) == 0o600
    assert ask["expires_at"] - ask["posted_at"] == ma.DEFAULT_EXPIRY_S
    b2 = book(tmp_path, clock)  # restart
    got = b2.answer(NICK, "peer", "#room", f"{NICK}: me")
    assert got["conversation_id"] == "conv1"
    assert got["ask_id"] == ask["ask_id"]
    assert b2.all()[0]["answers"][0]["sender"] == "peer"
    assert b2.all()[0]["status"] == ma.ANSWERED


def test_expired_is_shown_never_dropped_and_a_mention_does_not_match(tmp_path):
    clock = Clock()
    b = book(tmp_path, clock)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "#r")
    clock.t += ma.DEFAULT_EXPIRY_S + 1
    # The nick-addressed fallback never reaches an expired ask: a mention is a new turn.
    assert b.answer(NICK, "p", "#r", f"{NICK}: hi") is None
    assert [a["status"] for a in b.all()] == [ma.EXPIRED]


def test_an_answer_in_the_thread_after_expiry_still_lands_marked(tmp_path):
    """Operator, 2026-10-31: a late answer after expiry still lands, marked, via event."""
    clock = Clock()
    b = book(tmp_path, clock)
    b.begin_turn("t1")
    ask = b.open_ask("t1", "c", "#r")
    b.end_turn("t1")
    clock.t += ma.DEFAULT_EXPIRY_S + 1
    thread = next(a["thread"] for a in b.all())
    got = b.answer(NICK, "p", "#r", "here at last", thread=thread)
    assert got is not None
    assert got["conversation_id"] == "c"
    assert got["after_expiry"] is True
    assert got["late"] is True
    assert got["rule"] == ma.RULE_THREAD
    assert ask is not None


def test_two_hour_late_answer_lands_marked_late(tmp_path):
    clock = Clock()
    b = book(tmp_path, clock)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "#r")
    b.end_turn("t1")
    clock.t += 2 * 3600
    got = b.answer(NICK, "p", "#r", f"{NICK}, here")
    assert got["late"] is True
    assert got["ambiguous"] is False


def test_answer_before_turn_end_is_not_late(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "#r")
    assert b.answer(NICK, "p", "#r", f"{NICK}: x")["late"] is False


def test_matching_rule(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "#r")
    assert b.answer(NICK, "p", "#r", "unrelated chatter") is None
    assert b.answer(NICK, "p", "#other", f"{NICK}: x") is None
    assert b.answer(NICK, "p", "#r", f"{NICK}x: no") is None
    assert b.answer(NICK, "p", "#r", f"I think {NICK} should") is not None


def test_dm_matches_dm_ask(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "peer")
    assert b.answer(NICK, "peer", "peer", "no prefix needed")["conversation_id"] == "c"


def test_ambiguous_and_most_recent(tmp_path):
    clock = Clock()
    b = book(tmp_path, clock)
    b.begin_turn("t1")
    b.open_ask("t1", "c1", "#r")
    clock.t += 1
    b.begin_turn("t2")
    b.open_ask("t2", "c2", "#r")
    got = b.answer(NICK, "p", "#r", f"{NICK}: x")
    assert got["conversation_id"] == "c2"
    assert got["ambiguous"] is True


def test_reply_to_msgid_wins(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c1", "#r", msgid="m1")
    b.begin_turn("t2")
    b.open_ask("t2", "c2", "#r", msgid="m2")
    got = b.answer(NICK, "p", "#r", "no address", reply_to="m1")
    assert got["conversation_id"] == "c1"
    assert got["ambiguous"] is False


def test_one_ask_per_turn(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    assert b.open_ask("t1", "c", "#r").ok
    r = b.open_ask("t1", "c", "#r")
    assert not r.ok
    assert r.refusal == ma.REFUSE_TURN_LIMIT
    assert b.refusals[-1]["code"] == ma.REFUSE_TURN_LIMIT


def test_mesh_started_turn_cannot_ask(tmp_path):
    b = book(tmp_path)
    assert b.begin_turn("m1", mesh_started=True).ok
    assert b.open_ask("m1", "c", "#r").refusal == ma.REFUSE_MESH_TURN


def test_open_cap(tmp_path):
    b = book(tmp_path)
    for i in range(ma.MAX_OPEN_ASKS):
        b.begin_turn(f"t{i}")
        assert b.open_ask(f"t{i}", "c", "#r").ok
    b.begin_turn("x")
    assert b.open_ask("x", "c", "#r").refusal == ma.REFUSE_OPEN_CAP


def test_mesh_turn_rate_cap_and_window(tmp_path):
    clock = Clock()
    b = book(tmp_path, clock)
    for i in range(ma.MAX_MESH_TURNS):
        assert b.begin_turn(f"m{i}", mesh_started=True).ok
    assert b.begin_turn("mx", mesh_started=True).refusal == ma.REFUSE_RATE
    clock.t += ma.MESH_TURN_WINDOW_S + 1
    assert b.begin_turn("my", mesh_started=True).ok


def test_peer_answering_with_questions_yields_one_post_per_operator_turn(tmp_path):
    b = book(tmp_path)
    posts = 0
    b.begin_turn("op1")
    if b.open_ask("op1", "c", "#r").ok:
        posts += 1
    for i in range(5):  # peer answers every ask with a question
        got = b.answer(NICK, "peer", "#r", f"{NICK}: and you?")
        if got is None:
            break
        b.begin_turn(f"m{i}", mesh_started=True)
        if b.open_ask(f"m{i}", got["conversation_id"], "#r").ok:
            posts += 1
        b.end_turn(f"m{i}")
    assert posts == 1


def test_unreadable_file_set_aside_never_raises(tmp_path):
    (tmp_path / ma.ASKS_NAME).write_text("{not json")
    b = book(tmp_path)
    assert b.all() == []
    assert b.unreadable == 1
    assert (tmp_path / (ma.ASKS_NAME + ".unreadable")).exists()


def test_save_failure_is_a_recorded_refusal(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    b = ma.AskBook(f / ma.ASKS_NAME, Clock())
    b.begin_turn("t")
    assert b.open_ask("t", "c", "#r").refusal == ma.REFUSE_SAVE
    assert b.save_failures == 1


def test_thread_name_valid_and_matches_first(tmp_path):
    import re

    b = book(tmp_path)
    b.begin_turn("t1")
    a1 = b.open_ask("t1", "c1", "#r", msgid="m1").ask
    b.begin_turn("t2")
    b.open_ask("t2", "c2", "#r")
    assert re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?", a1["thread"])
    got = b.answer(NICK, "p", "#r", "no address", reply_to="zzz", thread=a1["thread"])
    assert got["conversation_id"] == "c1"
    assert got["rule"] == ma.RULE_THREAD
    assert got["ambiguous"] is False


def test_rule_recorded_for_reply_to_and_fallback(tmp_path):
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c1", "#r", msgid="m1")
    assert b.answer(NICK, "p", "#r", "x", reply_to="m1", thread="nope")["rule"] == ma.RULE_REPLY_TO
    b.begin_turn("t2")
    b.open_ask("t2", "c2", "#r")
    assert b.answer(NICK, "p", "#r", f"{NICK}: x")["rule"] == ma.RULE_FALLBACK


def test_a_failed_post_closes_its_ask_and_frees_the_open_slot(tmp_path):
    """Plan risk r8: post() failed after the ask opened; the ask is closed as failed."""
    clock = Clock()
    b = book(tmp_path, clock)
    opened = []
    for i in range(ma.MAX_OPEN_ASKS):
        b.begin_turn(f"t{i}")
        opened.append(b.open_ask(f"t{i}", "c", "#r").ask)
    b.begin_turn("over")
    assert b.open_ask("over", "c", "#r").refusal == ma.REFUSE_OPEN_CAP
    assert b.fail(opened[0]["ask_id"]) is True
    assert b.fail("no-such-ask") is False
    b.begin_turn("after")
    assert b.open_ask("after", "c", "#r").ok is True  # the failed post held no slot
    kept = {a["ask_id"]: a for a in book(tmp_path, clock).all()}  # survives a restart
    assert kept[opened[0]["ask_id"]]["status"] == ma.FAILED
    # A failed ask is never answered: its thread was never created on the mesh.
    thread = opened[0]["thread"]
    got = b.answer(NICK, "p", "#r", "x", thread=thread)
    assert got is None or got["ask_id"] != opened[0]["ask_id"]


def test_a_server_notice_is_never_an_answer(tmp_path):
    """Review of #117, finding 3: t19 saw 'spark-gwen joined #general' from system-spark.
    A system- sender is the server, never an agent answering Gwen -- not even in-thread."""
    b = book(tmp_path)
    b.begin_turn("t1")
    ask = b.open_ask("t1", "c", "#general").ask
    assert b.answer(NICK, "system-spark", "#general", f"{NICK} joined #general") is None
    assert b.answer(NICK, "SYSTEM-spark", "#general", f"Welcome {NICK}") is None
    assert b.answer(NICK, "system-spark", "#general", "x", thread=ask["thread"]) is None
    assert b.answer(NICK, "spark-ada", "#general", f"{NICK}: it is noon") is not None


def test_a_message_that_matches_nothing_does_not_rewrite_the_file(tmp_path):
    """Review of #117, finding 10: answer() saved mesh-asks.json on every unmatched message."""
    b = book(tmp_path)
    b.begin_turn("t1")
    b.open_ask("t1", "c", "#r")
    path = tmp_path / ma.ASKS_NAME
    before = path.stat().st_mtime_ns
    os.utime(path, ns=(before - 10**9, before - 10**9))
    stamped = path.stat().st_mtime_ns
    assert b.answer(NICK, "p", "#r", "unrelated chatter") is None
    assert path.stat().st_mtime_ns == stamped
