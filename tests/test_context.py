"""Tests for embodiment.context — context-window management primitives.

Ported from colleague ``1.52.1``'s ``tests/test_context_window.py`` and
``tests/test_context_media.py`` (task t3), merged into one file as directed by
the task brief (one ``tests/test_context.py`` for the whole module). Imports
were rewritten from ``colleague.context`` / ``colleague.media`` to
``embodiment.context`` / ``embodiment.media``; behaviour and assertions are
otherwise unchanged.

``TestMediaRejectionClassifier`` below additionally carries the one
self-contained unit test for ``is_media_rejection`` extracted from colleague's
``tests/test_media_delivery.py`` — the rest of that file exercises
``colleague.loop`` (the bounded tool loop, out of scope for this task) and is
not ported here.
"""

from __future__ import annotations

from embodiment.context import (
    count_tokens_chars,
    media_aware_count,
    window_messages,
)
from embodiment.media import IMAGE_TOKEN_ESTIMATE

# ---------------------------------------------------------------------------
# Helpers to build message fixtures
# ---------------------------------------------------------------------------


def sys_msg(text: str = "You are a coding agent.") -> dict:
    return {"role": "system", "content": text}


def user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant_tool_calls_msg(call_id: str, fn_name: str, fn_args: str = "{}") -> dict:
    """An assistant turn that requests a tool call."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": fn_name, "arguments": fn_args},
            }
        ],
    }


def tool_result_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def assistant_text_msg(text: str) -> dict:
    """An assistant turn with no tool calls (just text)."""
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------------------
# Validity checker (used as an assertion helper)
# ---------------------------------------------------------------------------


def is_openai_valid(messages: list[dict]) -> tuple[bool, str]:
    """Return (True, '') if OpenAI validity holds, else (False, reason)."""
    # Collect all tool_call_ids from assistant tool_calls messages
    declared_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                declared_ids.add(tc["id"])

    # Every tool message must have a declared parent
    for m in messages:
        if m.get("role") == "tool":
            tid = m.get("tool_call_id", "")
            if tid not in declared_ids:
                return False, f"orphan tool message tool_call_id={tid!r}"

    # Every assistant tool_calls turn must have ALL its replies present
    # Group by call_id; each id must appear exactly once as a tool message
    tool_ids_present: set[str] = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc["id"] not in tool_ids_present:
                    return False, f"assistant tool_calls id={tc['id']!r} missing tool reply"

    return True, ""


# ===========================================================================
# Tests for count_tokens_chars
# ===========================================================================


class TestCountTokensChars:
    def test_empty_list_returns_one(self):
        # minimum 1 when there is any text ... actually spec says minimum 1
        # when there IS any text. Empty list = 0 chars → 0 // 4 = 0.
        # Spec: "minimum 1 when there is any text" — empty list has no text.
        result = count_tokens_chars([])
        assert result == 0

    def test_counts_content_chars(self):
        # 400 chars of content → 400 // 4 = 100
        msgs = [{"role": "user", "content": "a" * 400}]
        assert count_tokens_chars(msgs) == 100

    def test_minimum_one_when_any_text(self):
        # 1 char → 1 // 4 = 0 but minimum 1
        msgs = [{"role": "user", "content": "x"}]
        assert count_tokens_chars(msgs) == 1

    def test_counts_tool_calls_name_and_arguments(self):
        fn_name = "read_file"  # 9 chars
        fn_args = '{"path": "foo.py"}'  # 18 chars
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": fn_name, "arguments": fn_args},
                    }
                ],
            }
        ]
        expected = (len(fn_name) + len(fn_args)) // 4
        assert count_tokens_chars(msgs) == max(1, expected)

    def test_sums_all_messages(self):
        msgs = [
            {"role": "system", "content": "a" * 80},
            {"role": "user", "content": "b" * 80},
            {"role": "tool", "tool_call_id": "c1", "content": "c" * 80},
        ]
        assert count_tokens_chars(msgs) == 240 // 4

    def test_missing_content_key_treated_as_zero(self):
        # assistant tool_calls message may have empty / missing content
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [],
            }
        ]
        assert count_tokens_chars(msgs) == 0


# ===========================================================================
# Tests for window_messages
# ===========================================================================


class TestWindowMessagesUnderBudget:
    def test_under_budget_returns_same_list(self):
        """When already under budget the list is returned unchanged."""
        msgs = [
            sys_msg(),
            user_msg("do the task"),
            assistant_tool_calls_msg("c1", "read_file"),
            tool_result_msg("c1", "content"),
        ]
        result = window_messages(msgs, budget_tokens=10_000)
        # Same elements in same order
        assert result == msgs

    def test_under_budget_no_placeholder(self):
        msgs = [sys_msg(), user_msg("task")]
        result = window_messages(msgs, budget_tokens=10_000)
        assert not any("[earlier steps elided" in (m.get("content") or "") for m in result)


class TestWindowMessagesOverBudget:
    def _long_history(self, n_pairs: int = 8) -> list[dict]:
        """Build: system, user(task), n_pairs*(assistant+tool), assistant(final)."""
        msgs: list[dict] = [sys_msg(), user_msg("do the task")]
        for i in range(n_pairs):
            call_id = f"call_{i}"
            msgs.append(assistant_tool_calls_msg(call_id, "read_file", f'{{"path":"f{i}.py"}}'))
            msgs.append(tool_result_msg(call_id, "x" * 200))  # 200 chars each
        msgs.append(assistant_text_msg("done"))
        return msgs

    def test_system_and_first_user_always_preserved(self):
        msgs = self._long_history(10)
        result = window_messages(msgs, budget_tokens=50)
        assert result[0]["role"] == "system"
        first_user = next(m for m in result if m["role"] == "user")
        assert first_user["content"] == "do the task"

    def test_placeholder_present_exactly_once(self):
        msgs = self._long_history(10)
        result = window_messages(msgs, budget_tokens=50)
        placeholders = [m for m in result if "[earlier steps elided" in (m.get("content") or "")]
        assert len(placeholders) == 1

    def test_result_is_openai_valid(self):
        """No orphan tool messages; every tool_calls id has a matching tool reply."""
        msgs = self._long_history(10)
        result = window_messages(msgs, budget_tokens=80)
        ok, reason = is_openai_valid(result)
        assert ok, reason

    def test_most_recent_messages_retained(self):
        """The tail of the history (the most recent turns) is kept."""
        msgs = self._long_history(6)
        result = window_messages(msgs, budget_tokens=300)
        # The last assistant message ("done") should survive
        last_assistant = [m for m in result if m.get("role") == "assistant"][-1]
        assert last_assistant.get("content") == "done"

    def test_dropped_pairs_are_matched_units(self):
        """No assistant tool_calls without its tool reply, and no orphan tool."""
        msgs = self._long_history(8)
        # Very tight budget forces significant dropping
        result = window_messages(msgs, budget_tokens=60)
        ok, reason = is_openai_valid(result)
        assert ok, reason

    def test_placeholder_positioned_after_head(self):
        """Placeholder comes after system+first_user, before the retained tail."""
        msgs = self._long_history(8)
        result = window_messages(msgs, budget_tokens=80)
        head_indices = [i for i, m in enumerate(result) if m["role"] in ("system",)]
        ph_indices = [
            i for i, m in enumerate(result) if "[earlier steps elided" in (m.get("content") or "")
        ]
        if ph_indices:
            # placeholder must come after the system message
            assert ph_indices[0] > head_indices[0]
            # placeholder must come before the last message
            assert ph_indices[0] < len(result) - 1


class TestWindowMessagesCallCount:
    def test_count_tokens_calls_bounded(self):
        """count_tokens must be called at most a small constant number of times."""
        call_count = 0

        def counting_counter(msgs):
            nonlocal call_count
            call_count += 1
            # Use char-based estimate so it actually triggers trimming
            total = 0
            for m in msgs:
                total += len(m.get("content") or "")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    total += len(fn.get("name") or "") + len(fn.get("arguments") or "")
            return max(1, total // 4) if total else 0

        # Build a long history so trimming is definitely needed
        msgs = [sys_msg(), user_msg("task")]
        for i in range(20):
            cid = f"call_{i}"
            msgs.append(assistant_tool_calls_msg(cid, "read_file", f'{{"path":"f{i}.py"}}'))
            msgs.append(tool_result_msg(cid, "r" * 300))
        msgs.append(assistant_text_msg("finished"))

        window_messages(msgs, budget_tokens=50, count_tokens=counting_counter)
        # Must be small constant — spec says e.g. <= 4
        assert call_count <= 4, f"count_tokens called {call_count} times (expected <= 4)"

    def test_custom_count_tokens_used(self):
        """When count_tokens is passed it must be used instead of chars heuristic."""
        used = []

        def always_under(msgs):
            used.append(True)
            return 1  # always reports 1 token → always under budget

        msgs = [sys_msg(), user_msg("task")]
        result = window_messages(msgs, budget_tokens=10, count_tokens=always_under)
        assert result == msgs  # under budget → unchanged
        assert len(used) >= 1


class TestWindowMessagesEdgeCases:
    def test_only_head_and_one_turn_still_over_budget_returns_minimal(self):
        """When nothing droppable exists return minimal valid list."""
        msgs = [
            sys_msg("s" * 1000),
            user_msg("u" * 1000),
        ]
        result = window_messages(msgs, budget_tokens=1)
        # Must include at least system and first user
        assert result[0]["role"] == "system"
        assert any(m["role"] == "user" for m in result)

    def test_assistant_text_turn_can_be_dropped_as_standalone(self):
        """A plain assistant text turn (no tool_calls) is droppable on its own."""
        msgs = [
            sys_msg(),
            user_msg("task"),
            assistant_text_msg("intermediate thought " * 50),  # big, old
            assistant_tool_calls_msg("c1", "read_file"),
            tool_result_msg("c1", "ok"),
        ]
        result = window_messages(msgs, budget_tokens=30)
        ok, reason = is_openai_valid(result)
        assert ok, reason

    def test_no_placeholder_when_nothing_dropped(self):
        msgs = [sys_msg(), user_msg("small")]
        result = window_messages(msgs, budget_tokens=10_000)
        assert not any("[earlier steps elided" in (m.get("content") or "") for m in result)


# ===========================================================================
# Tests for is_context_overflow
# ===========================================================================


class TestIsContextOverflow:
    def test_maximum_context_length(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("This model's maximum context length is 4096 tokens")

    def test_context_window(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("The context window has been exceeded")

    def test_too_many_tokens(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("Error: too many tokens in the prompt")

    def test_reduce_the_length(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("Please reduce the length of the messages")

    def test_context_length_exceeded(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("context_length_exceeded error code returned")

    def test_longer_than_the_maximum(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("Your input is longer than the maximum allowed")

    def test_case_insensitive(self):
        from embodiment.context import is_context_overflow

        assert is_context_overflow("MAXIMUM CONTEXT LENGTH exceeded")
        assert is_context_overflow("Context Window Full")

    def test_unrelated_text_returns_false(self):
        from embodiment.context import is_context_overflow

        assert not is_context_overflow("File not found")
        assert not is_context_overflow("rate limit exceeded")
        assert not is_context_overflow("")

    def test_none_like_empty_returns_false(self):
        from embodiment.context import is_context_overflow

        assert not is_context_overflow("")

    def test_stdlib_only(self):
        """The module must import only stdlib — no third-party deps."""
        import sys

        # Snapshot + remove the cached module so the re-import is fresh, then
        # RESTORE the original module objects afterward: leaving a re-imported
        # embodiment.context in sys.modules poisons identity assertions in
        # tests that later run on the same xdist worker.
        saved = {key: sys.modules[key] for key in list(sys.modules) if "embodiment.context" in key}
        for key in saved:
            del sys.modules[key]

        try:
            import embodiment.context  # noqa: F401

            # Verify the module only imports stdlib modules (no third-party)
            # We check by ensuring tiktoken / transformers / etc. are NOT imported
            third_party = {"tiktoken", "transformers", "openai", "anthropic"}
            loaded = set(sys.modules.keys())
            bad = third_party & loaded
            # Allow any that were already loaded before this test
            assert not bad, f"Third-party modules loaded: {bad}"
        finally:
            for key, mod in saved.items():
                sys.modules[key] = mod


# ===========================================================================
# Tests for is_request_timeout
# ===========================================================================


class TestIsRequestTimeout:
    def test_timed_out(self):
        from embodiment.context import is_request_timeout

        assert is_request_timeout("timed out")

    def test_full_timeout_message(self):
        from embodiment.context import is_request_timeout

        assert is_request_timeout(
            "request to http://localhost:8001/v1/chat/completions timed out after 120s"
        )

    def test_case_insensitive(self):
        from embodiment.context import is_request_timeout

        assert is_request_timeout("Read Timed Out")

    def test_unrelated_text_returns_false(self):
        from embodiment.context import is_request_timeout

        assert not is_request_timeout("vLLM endpoint unreachable: Connection refused")

    def test_empty_returns_false(self):
        from embodiment.context import is_request_timeout

        assert not is_request_timeout("")

    def test_disjoint_from_overflow(self):
        from embodiment.context import is_request_timeout

        assert not is_request_timeout("maximum context length exceeded")


# ===========================================================================
# Tests for classify_degradable
# ===========================================================================


class TestClassifyDegradable:
    def test_overflow(self):
        from embodiment.context import classify_degradable

        assert classify_degradable("maximum context length exceeded") == "overflow"

    def test_timeout(self):
        from embodiment.context import classify_degradable

        assert classify_degradable("timed out") == "timeout"

    def test_neither(self):
        from embodiment.context import classify_degradable

        assert classify_degradable("Connection refused") is None

    def test_empty(self):
        from embodiment.context import classify_degradable

        assert classify_degradable("") is None


# ===========================================================================
# Media-rejection classifier (extracted from colleague's
# tests/test_media_delivery.py — the rest of that file exercises
# colleague.loop, out of scope here)
# ===========================================================================

# Verbatim from the live probe 2026-07-02 against the served text-only 27B.
_LIVE_400 = (
    'HTTP Error 400: Bad Request: {"error":{"message":"At most 0 image(s) '
    'may be provided in one prompt. (parameter=image)"}}'
)


class TestMediaRejectionClassifier:
    def test_media_rejection_classifier(self):
        from embodiment.context import is_media_rejection

        assert is_media_rejection(_LIVE_400)
        assert is_media_rejection("the model does not support image input")
        assert not is_media_rejection("maximum context length exceeded")
        assert not is_media_rejection("request timed out")


# ===========================================================================
# Part-aware budget accounting + part-safe windowing (t6, spec c13/h11),
# ported from colleague's tests/test_context_media.py
# ===========================================================================

_IMG_PART = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def _parts_message(text: str = "look at this") -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}, dict(_IMG_PART)]}


def _history(n_turns: int = 6, with_mid_parts: bool = True) -> list[dict]:
    msgs = [
        {"role": "system", "content": "sys"},
        _parts_message("the task text"),
    ]
    for i in range(n_turns):
        msgs.append({"role": "assistant", "content": f"thinking {i} " + "x" * 400})
        if with_mid_parts and i == 1:
            msgs.append(_parts_message("[view_media] loaded image img.png"))
    msgs.append({"role": "assistant", "content": "latest turn"})
    return msgs


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_char_fallback_charges_the_media_estimate() -> None:
    text = "a" * 400
    plain = [{"role": "user", "content": text}]
    with_media = [{"role": "user", "content": [{"type": "text", "text": text}, dict(_IMG_PART)]}]
    base = count_tokens_chars(plain)
    counted = count_tokens_chars(with_media)
    # The image part contributes ~IMAGE_TOKEN_ESTIMATE tokens, never zero and
    # never len(list)-style nonsense.
    assert counted - base >= IMAGE_TOKEN_ESTIMATE - 1
    assert counted - base <= IMAGE_TOKEN_ESTIMATE + 1


def test_char_fallback_string_only_unchanged() -> None:
    msgs = [{"role": "user", "content": "abcd" * 25}]
    assert count_tokens_chars(msgs) == 25


def test_media_aware_count_flattens_for_the_exact_counter() -> None:
    seen: list[list[dict]] = []

    def exact(msgs: list[dict]) -> int:
        seen.append(msgs)
        assert all(isinstance(m.get("content"), str) for m in msgs)
        return 100

    msgs = [_parts_message()]
    total = media_aware_count(msgs, exact)
    assert seen, "the exact counter must be consulted"
    assert total == 100 + IMAGE_TOKEN_ESTIMATE


def test_media_aware_count_passthrough_without_media() -> None:
    calls: list[list[dict]] = []

    def exact(msgs: list[dict]) -> int:
        calls.append(msgs)
        return 42

    msgs = [{"role": "user", "content": "plain"}]
    assert media_aware_count(msgs, exact) == 42
    # No flattening copy for a string-only history: the exact counter sees the
    # original list object (zero-overhead passthrough).
    assert calls[0] is msgs


def test_media_aware_count_none_counter_falls_back_to_chars() -> None:
    msgs = [_parts_message("t" * 40)]
    assert media_aware_count(msgs, None) == count_tokens_chars(msgs)


# ---------------------------------------------------------------------------
# Windowing: parts survive whole or drop whole — never sliced
# ---------------------------------------------------------------------------


def _assert_no_partial_parts(msgs: list[dict], original_parts: list[list[dict]]) -> None:
    for m in msgs:
        content = m.get("content")
        if isinstance(content, list):
            assert content in original_parts, "a parts list must survive intact"


def test_windowing_keeps_head_parts_message_intact() -> None:
    msgs = _history()
    original = [m["content"] for m in msgs if isinstance(m.get("content"), list)]
    out = window_messages(msgs, budget_tokens=350)
    assert out[1]["content"] == msgs[1]["content"], "head (first user) always survives"
    _assert_no_partial_parts(out, original)
    assert any("elided" in str(m.get("content")) for m in out), "placeholder present"


def test_windowing_drops_mid_history_parts_message_whole() -> None:
    msgs = _history()
    out = window_messages(msgs, budget_tokens=300)
    mid_parts = [
        m
        for m in out[2:]
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    # tight budget: the mid-history view_media fold drops whole (the head
    # attachment message is exempt — always preserved)
    assert not mid_parts
    for m in out:
        content = m.get("content")
        if isinstance(content, list):
            types = [p.get("type") for p in content]
            assert types == ["text", "image_url"], "never a truncated parts list"


def test_windowing_string_only_history_unchanged_when_under_budget() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "done"},
    ]
    assert window_messages(msgs, budget_tokens=10_000) is msgs
