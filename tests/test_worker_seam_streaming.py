"""Task ``t5`` — the SSE transport: metering parity, two-phase bounds, died streams.

Claims ``c34``/``h23``, ``c37``/``h25``, ``c38``/``h26``, ``c41``/``h28``, and
``c13`` through the outer backstop. Every test here is **hermetic**: the seam's
one audited dial (:meth:`WorkerSeam._open`) is replaced with a scripted SSE
double, so no socket is touched. The live rig is not the instrument for any of
these properties — a scripted stream can be made to stall mid-body on demand,
which is exactly the case a healthy rig will not produce.

Four things are proven here, and each of them is a thing the plan named as a way
this change could go wrong:

* **Metering parity** (``h25``). A streamed record and a non-streamed one from
  the *same* underlying turn are compared field by field, not read for
  plausibility. If the transport lost ``finish_reason`` or a token count, the
  rig would be un-measured and nothing else in this repo would notice.
* **Queue wait is never charged as idle** (``h26``). Proven two ways: the idle
  bound is only *installed* after the first chunk arrives, and a stream whose
  first chunk is 900 s late — fifteen times the idle bound — completes normally.
  A fixed idle clock started at ``t = 0`` is the 300 s censoring defect wearing a
  new clock, and it is the specific mistake this task was told not to make.
* **A died stream is distinguishable and is never retried** (``h28``). Provoked
  by a scripted stream that raises mid-body, then asserted on the record's
  fields and on the dial count.
* **The blocking path is unchanged** with ``stream=False`` — no ``stream`` key
  on the wire, one request-scoped clock, the same parse.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import worker_seam as ws  # noqa: E402

# ── the scripted SSE double ───────────────────────────────────────────────────


def sse(frame: Any) -> bytes:
    """One SSE data line, as the wire carries it."""
    if frame == ws._SSE_DONE:
        return b"data: [DONE]\n"
    return b"data: " + json.dumps(frame).encode("utf-8") + b"\n"


def delta_frame(**delta: Any) -> bytes:
    return sse({"id": "cmpl-1", "model": "m", "choices": [{"index": 0, "delta": delta}]})


def finish_frame(reason: str = "stop") -> bytes:
    return sse(
        {
            "id": "cmpl-1",
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
        }
    )


def usage_frame(usage: dict[str, Any]) -> bytes:
    return sse({"id": "cmpl-1", "model": "m", "choices": [], "usage": usage})


#: The canonical usage object this rig sends. Four counts: the three the probe
#: recorded plus the reasoning breakdown ``worker_throughput`` reads.
CANONICAL_USAGE: dict[str, Any] = {
    "prompt_tokens": 44,
    "completion_tokens": 1101,
    "total_tokens": 1145,
    "completion_tokens_details": {"reasoning_tokens": 1099},
}


class FakeStream:
    """A response double: iterable over scripted lines, with a settable read timeout.

    ``set_read_timeout`` is the documented hook :func:`worker_seam.read_timeout_setter`
    looks for first, so the phase switch is observable without reaching into a
    real socket. ``timeouts`` records every value it is given, in order, which is
    how the "installed only after the first chunk" property is asserted.
    """

    def __init__(
        self,
        lines: Iterable[Any],
        *,
        clock: Optional["FakeClock"] = None,
    ) -> None:
        self._lines = list(lines)
        self.timeouts: list[float] = []
        self.closed = False
        self._clock = clock

    def set_read_timeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)

    def __iter__(self) -> Iterator[bytes]:
        for line in self._lines:
            if isinstance(line, BaseException):
                raise line
            if callable(line):
                line()
                continue
            yield line

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.closed = True
        return False


class FakeClock:
    """A monotonic clock a test drives, so a 900 s queue wait costs no wall time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> Callable[[], None]:
        def _tick() -> None:
            self.now += seconds

        return _tick


def stream_seam(lines: Iterable[Any], **overrides: Any) -> tuple[ws.WorkerSeam, dict[str, Any]]:
    """A streaming seam whose one dial returns a scripted response. Records the dial."""
    kwargs: dict[str, Any] = dict(
        base_url="http://x/v1",
        model="m",
        api_key="k",
        role="worker",
        max_tokens=16000,
        sleep=lambda _seconds: None,
    )
    kwargs.update(overrides)
    seam = ws.WorkerSeam(**kwargs)
    seen: dict[str, Any] = {"dials": 0, "timeouts": [], "response": None}

    def _open(request: Any, *, timeout: float) -> Any:
        seen["dials"] += 1
        seen["timeouts"].append(timeout)
        seen["request"] = request
        response = FakeStream(lines)
        seen["response"] = response
        return response

    seam._open = _open  # type: ignore[method-assign]
    return seam, seen


#: One complete turn, as SSE. Reasoning arrives under ``reasoning`` — the field
#: this rig actually sends, NOT vLLM's documented ``reasoning_content``.
COMPLETE_TURN: list[bytes] = [
    delta_frame(role="assistant", content=""),
    delta_frame(reasoning="Let me "),
    delta_frame(reasoning="think about that."),
    delta_frame(content="The answer "),
    delta_frame(content="is 42."),
    finish_frame("stop"),
    usage_frame(CANONICAL_USAGE),
    sse(ws._SSE_DONE),
]

#: The identical turn as a single non-streamed completion payload.
COMPLETE_TURN_BLOCKING: dict[str, Any] = {
    "id": "cmpl-1",
    "model": "m",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "The answer is 42.",
                "reasoning_content": "Let me think about that.",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": CANONICAL_USAGE,
}


# ── c37 / h25: metering parity ────────────────────────────────────────────────


class TestMeteringParity:
    """A streamed record and a non-streamed record of the same turn, compared.

    Not "the streamed record looks right" — the two are produced from the same
    turn and asserted equal where they must be. ``worker_seam.py:318`` reads
    ``usage`` from the final payload, and losing it under streaming would
    un-measure the rig with nothing in the artifact to say so.
    """

    def _streamed(self) -> ws.WorkerSeam:
        seam, _ = stream_seam(COMPLETE_TURN)
        seam([{"role": "user", "content": "hi"}])
        return seam

    def _blocking(self) -> ws.WorkerSeam:
        seam, _ = stream_seam([], stream=False)
        seam._post = lambda body: COMPLETE_TURN_BLOCKING  # type: ignore[method-assign]
        seam([{"role": "user", "content": "hi"}])
        return seam

    def test_the_wire_asks_for_the_terminal_usage_chunk(self) -> None:
        """``c37``: without ``include_usage`` a stream carries no counts at all."""
        seam, seen = stream_seam(COMPLETE_TURN)
        seam([{"role": "user", "content": "hi"}])
        body = json.loads(seen["request"].data.decode("utf-8"))
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}

    def test_the_two_records_carry_exactly_the_same_fields(self) -> None:
        """``h25``: field-identical, asserted on the key sets themselves."""
        streamed = self._streamed().meter.transcript[0]
        blocking = self._blocking().meter.transcript[0]
        assert set(streamed) == set(blocking)

    def test_finish_reason_and_every_token_count_survive_the_transport(self) -> None:
        streamed = self._streamed().meter.transcript[0]
        blocking = self._blocking().meter.transcript[0]
        for field in ("finish_reason", "prompt_tokens", "completion_tokens"):
            assert streamed[field] == blocking[field], field
        assert streamed["finish_reason"] == "stop"
        assert streamed["prompt_tokens"] == CANONICAL_USAGE["prompt_tokens"]
        assert streamed["completion_tokens"] == CANONICAL_USAGE["completion_tokens"]

    def test_the_usage_object_is_passed_through_verbatim(self) -> None:
        """All four counts, including the ones ``Meter`` does not itself keep.

        ``worker_throughput.ThroughputSeam`` and ``arch_arms.ArchSeam`` keep the
        raw payload precisely to read ``total_tokens`` and the reasoning-token
        breakdown. Reassembling a payload that drops them would break those two
        harnesses silently, since both degrade an absent breakdown to a recorded
        "absent" rather than to an error.
        """
        seam, _ = stream_seam(COMPLETE_TURN)
        payload = seam._post(
            {
                "model": "m",
                "messages": [],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        )
        assert payload["usage"] == CANONICAL_USAGE
        assert payload["usage"]["total_tokens"] == 1145
        assert payload["usage"]["completion_tokens_details"]["reasoning_tokens"] == 1099

    def test_content_and_reasoning_stay_separate_across_the_transport(self) -> None:
        """The ``parse_completion`` invariant, at the layer that could break it.

        Folding a thought into a reply reports a thought as if it were an
        answer. Streaming makes that easy to get wrong: both arrive as deltas on
        the same object, one key apart.
        """
        streamed = self._streamed().meter.transcript[0]
        blocking = self._blocking().meter.transcript[0]
        assert streamed["content"] == blocking["content"] == "The answer is 42."
        assert streamed["reasoning"] == blocking["reasoning"] == "Let me think about that."

    def test_the_reasoning_field_this_rig_actually_sends_is_read(self) -> None:
        """``streaming-probe.md`` §3, as an assertion.

        The first probe run reported **0 reasoning deltas** while 390 chunks
        arrived 0.11 s apart, because it was written against vLLM's documented
        ``delta.reasoning_content`` and this rig sends ``delta.reasoning``. A
        client with that bug reports the model as not streaming its thinking —
        the opposite of the truth, with nothing in the record to flag it.
        """
        result = read([delta_frame(reasoning="thought"), finish_frame(), sse(ws._SSE_DONE)])
        assert result.payload["choices"][0]["message"]["reasoning"] == "thought"

    def test_the_documented_field_name_is_accepted_too(self) -> None:
        """A rig that starts sending the documented name must not silently zero."""
        result = read([delta_frame(reasoning_content="thought"), finish_frame(), sse(ws._SSE_DONE)])
        assert result.payload["choices"][0]["message"]["reasoning"] == "thought"

    def test_the_reassembled_message_carries_only_the_name_the_rig_sends(self) -> None:
        """Checked live, not assumed: a superset is not "field-identical".

        A non-streamed completion on this rig returns ``reasoning`` — the same
        name the deltas use, not vLLM's documented ``reasoning_content``. So the
        reassembled message emits exactly that one key. Every reader in this
        repo accepts either name, so a rig that flips still parses.
        """
        result = read([delta_frame(reasoning="thought"), finish_frame(), sse(ws._SSE_DONE)])
        message = result.payload["choices"][0]["message"]
        assert "reasoning_content" not in message
        assert set(message) == {"role", "content", "reasoning"}

    def test_a_meter_summary_carries_the_streaming_counters(self) -> None:
        payload = self._streamed().meter.to_dict()
        for key in ("stream_deaths", "stream_usage_absent", "degradations", "finish_reasons"):
            assert key in payload
        assert payload["stream_deaths"] == 0
        assert payload["calls"] == 1

    def test_every_record_names_the_transport_that_produced_it(self) -> None:
        """The ``d16`` line, applied forward: latency is not comparable across this."""
        assert self._streamed().meter.transcript[0]["transport"] == ws.TRANSPORT_STREAM
        assert self._blocking().meter.transcript[0]["transport"] == ws.TRANSPORT_BLOCKING

    def test_an_absent_usage_chunk_is_recorded_rather_than_read_as_zero(self) -> None:
        """``c37``'s fallback path: a backend that ignores ``include_usage``.

        The counts genuinely are zero, and a zero that nobody flagged is
        indistinguishable from a call that generated nothing. So it is counted,
        announced, and left on the meter for the artifact.
        """
        seam, _ = stream_seam([delta_frame(content="hi"), finish_frame(), sse(ws._SSE_DONE)])
        seam([{"role": "user", "content": "hi"}])

        assert seam.meter.stream_usage_absent == 1
        codes = {entry["code"] for entry in seam.meter.degradations}
        assert ws.DEGRADED_STREAM_USAGE_ABSENT in codes


# ── tool calls: fragments, reassembled ────────────────────────────────────────


def read(lines: Iterable[Any], **overrides: Any) -> ws.StreamResult:
    """Drive :func:`read_sse` directly, with generous bounds unless overridden.

    The script goes through :class:`FakeStream` rather than straight into
    ``read_sse`` so that a bare exception in the list raises where a socket
    would, and a bare callable ticks the fake clock between chunks — the only
    way to script a 900 s queue wait that costs no wall time.
    """
    kwargs: dict[str, Any] = dict(bounds=ws.StreamBounds.derived())
    kwargs.update(overrides)
    return ws.read_sse(iter(FakeStream(lines)), **kwargs)


class TestToolCallReassembly:
    """Arguments arrive as fragments. Taking the last one loses the call's arguments.

    This is the failure that would be hardest to see: ``parse_completion``
    degrades unparseable arguments to ``{}`` on purpose, so a reader that
    dropped fragments would produce tool calls with the right *name* and no
    arguments, and the loop would dutifully execute them.
    """

    def test_argument_fragments_are_concatenated_in_arrival_order(self) -> None:
        result = read(
            [
                delta_frame(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "add"}}]),
                delta_frame(tool_calls=[{"index": 0, "function": {"arguments": '{"a": 17,'}}]),
                delta_frame(tool_calls=[{"index": 0, "function": {"arguments": ' "b": 25}'}}]),
                finish_frame("tool_calls"),
                sse(ws._SSE_DONE),
            ]
        )
        reply = ws.parse_completion(result.payload)
        assert reply.tool_calls[0].name == "add"
        assert reply.tool_calls[0].arguments == {"a": 17, "b": 25}

    def test_parallel_calls_stay_separate_and_ordered_by_index(self) -> None:
        result = read(
            [
                delta_frame(
                    tool_calls=[
                        {"index": 1, "id": "c2", "function": {"name": "finish", "arguments": "{}"}}
                    ]
                ),
                delta_frame(
                    tool_calls=[
                        {"index": 0, "id": "c1", "function": {"name": "add", "arguments": "{}"}}
                    ]
                ),
                finish_frame("tool_calls"),
                sse(ws._SSE_DONE),
            ]
        )
        reply = ws.parse_completion(result.payload)
        assert [call.name for call in reply.tool_calls] == ["add", "finish"]
        assert [call.id for call in reply.tool_calls] == ["c1", "c2"]


# ── c38 / h26: two-phase bounds ───────────────────────────────────────────────


class TestTwoPhaseBounds:
    """Queue wait is never charged as idle. The specific mistake this task forbids.

    A request queued behind ``--max-num-seqs=2`` legitimately receives nothing
    until it is scheduled. An idle clock applied from ``t = 0`` would kill it —
    and would kill it the way the 300 s constant killed long completions: by
    removing the evidence, so that the distribution of what got through looks
    healthy.
    """

    def test_the_idle_bound_is_installed_only_after_the_first_chunk(self) -> None:
        """Structural: the clock that could charge queue wait does not yet exist."""
        seam, seen = stream_seam(COMPLETE_TURN)
        seam([{"role": "user", "content": "hi"}])

        response = seen["response"]
        # The dial's own timeout is the queue bound...
        assert seen["timeouts"][0] == pytest.approx(ws.STREAM_FIRST_CHUNK_TIMEOUT, rel=1e-3)
        # ...and the idle bound is installed exactly once, at the first chunk.
        assert response.timeouts == [ws.STREAM_IDLE_TIMEOUT]

    def test_a_queued_request_that_waits_far_past_the_idle_bound_still_completes(self) -> None:
        """``h26``, end to end, on a clock the test drives.

        900 s of silence before the first chunk — fifteen times the idle bound —
        then a normal turn. Under a single-phase clock this is a dead request
        and a censored measurement.
        """
        clock = FakeClock()
        lines: list[Any] = [clock.advance(900.0), *COMPLETE_TURN]
        result = read(lines, now=clock, started=0.0)

        assert result.died is False
        assert result.first_chunk_seconds == pytest.approx(900.0)
        assert result.payload["choices"][0]["finish_reason"] == "stop"
        assert result.payload["usage"] == CANONICAL_USAGE

    def test_a_stall_before_the_first_chunk_is_a_connection_failure_not_a_death(self) -> None:
        """The body never started, so lobes' no-retry contract does not bind yet.

        It raises, which puts it on the seam's existing retry ladder — and it
        must NOT produce a ``stream_died`` record, because nothing died: a
        request that was never scheduled has no partial turn to preserve.
        """
        stall = TimeoutError("timed out")
        with pytest.raises(TimeoutError):
            read([stall])

    def test_a_stall_before_the_first_chunk_is_retried_by_the_seam(self) -> None:
        seam, seen = stream_seam([TimeoutError("timed out")])
        with pytest.raises(ws.WorkerTransportError):
            seam([{"role": "user", "content": "hi"}])

        assert seen["dials"] == ws.MAX_TRANSPORT_RETRIES + 1
        assert seam.meter.stream_deaths == 0
        assert seam.meter.failures == 1

    def test_the_queue_bound_is_measured_from_the_call_not_the_attempt(self) -> None:
        """A reconnect is still the same wait in the same queue.

        Per-attempt queue bounds would multiply by the retry ladder and push a
        dead endpoint past the fan-out deadline, where it would be recorded as
        ``fanout-unit-absent`` — a straggler — rather than as a dead transport.
        """
        seam, seen = stream_seam([TimeoutError("timed out")])
        with pytest.raises(ws.WorkerTransportError):
            seam([{"role": "user", "content": "hi"}])

        assert seen["timeouts"] == sorted(seen["timeouts"], reverse=True)
        assert seen["timeouts"][-1] < seen["timeouts"][0]

    def test_an_attempt_after_the_queue_bound_has_elapsed_is_refused(self) -> None:
        seam, seen = stream_seam(COMPLETE_TURN)
        seam._call_started = -(ws.STREAM_FIRST_CHUNK_TIMEOUT + 1.0)
        with pytest.raises(TimeoutError):
            seam._post({"model": "m", "messages": [], "stream": True})
        assert seen["dials"] == 0

    def test_the_total_backstop_stops_a_stream_that_never_ends(self) -> None:
        """A dribble just inside the idle bound trips neither phase clock."""
        clock = FakeClock()
        lines: list[Any] = []
        for _ in range(200):
            lines.extend([clock.advance(30.0), delta_frame(content="x")])
        result = read(lines, now=clock, started=0.0)

        assert result.died is True
        assert "total bound" in result.death_reason

    def test_a_response_with_no_socket_records_that_the_idle_bound_is_unarmed(self) -> None:
        """C3: the degradation is visible, never a bound that silently is not there."""

        class NoSocket:
            def __iter__(self) -> Iterator[bytes]:
                return iter(COMPLETE_TURN)

            def __enter__(self) -> "NoSocket":
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

        seam, _ = stream_seam([])
        seam._open = lambda request, *, timeout: NoSocket()  # type: ignore[method-assign]
        seam([{"role": "user", "content": "hi"}])

        codes = {entry["code"] for entry in seam.meter.degradations}
        assert ws.DEGRADED_STREAM_IDLE_UNENFORCEABLE in codes


# ── c41 / h28: the died stream ────────────────────────────────────────────────


#: A stream that starts normally and then has its body cut. The provocation: the
#: iterator raises mid-body, which is what a socket does when a relay dies.
DIED_MID_STREAM: list[Any] = [
    delta_frame(role="assistant", content=""),
    delta_frame(reasoning="I was thinking "),
    delta_frame(reasoning="about the problem when"),
    delta_frame(content="Partial answ"),
    ConnectionResetError("connection reset by peer"),
]


class TestDiedStream:
    """``h28``: a provoked mid-stream death, distinguishable by field value alone.

    This is issue #37's defect one layer up. ``ModelResponse`` carries no
    ``finish_reason``, so a truncated turn and a deliberate one reached the loop
    as the same object and 6.0% of completions were silently cut. A died stream
    is the same shape: a short body that reads exactly like a model with little
    to say. It gets an explicit field.
    """

    def _died(self) -> ws.WorkerSeam:
        seam, self._seen = stream_seam(DIED_MID_STREAM)
        seam([{"role": "user", "content": "hi"}])
        return seam

    def test_the_record_says_so_in_a_field_a_reader_can_test(self) -> None:
        record = self._died().meter.transcript[0]
        assert record["stream_died"] is True

    def test_a_completed_turn_carries_the_same_field_set_to_false(self) -> None:
        """The field is on EVERY record. An absence can never mean anything."""
        seam, _ = stream_seam(COMPLETE_TURN)
        seam([{"role": "user", "content": "hi"}])
        assert seam.meter.transcript[0]["stream_died"] is False

    def test_the_finish_reason_is_not_one_a_model_can_produce(self) -> None:
        seam = self._died()
        assert seam.meter.transcript[0]["finish_reason"] == ws.FINISH_STREAM_DIED
        assert seam.meter.finish_reasons == {ws.FINISH_STREAM_DIED: 1}
        assert seam.meter.stream_deaths == 1

    def test_partial_content_and_reasoning_are_kept(self) -> None:
        """The reason no-retry is honest: what arrived is real and is not thrown away."""
        record = self._died().meter.transcript[0]
        assert record["content"] == "Partial answ"
        assert record["reasoning"] == "I was thinking about the problem when"

    def test_it_is_never_retried(self) -> None:
        """lobes' no-retry-once-streaming contract, held structurally.

        ``_post`` returns rather than raising, so control never re-enters the
        attempt loop. There is no flag to get wrong and no ordering to preserve:
        a second dial is simply not reachable from here.
        """
        self._died()
        assert self._seen["dials"] == 1

    def test_the_death_is_announced_and_recorded_as_a_degradation(self, capsys: Any) -> None:
        seam = self._died()
        codes = {entry["code"] for entry in seam.meter.degradations}
        assert ws.DEGRADED_STREAM_DIED in codes
        assert ws.DEGRADED_STREAM_DIED in capsys.readouterr().err

    def test_a_body_that_ends_without_a_terminal_frame_is_also_a_death(self) -> None:
        """No exception, no ``[DONE]``, no ``finish_reason`` — a server that hung up."""
        result = read([delta_frame(content="half an ans")])
        assert result.died is True
        assert result.payload["stream_died"] is True
        assert result.payload["choices"][0]["message"]["content"] == "half an ans"

    def test_a_completed_turn_missing_only_its_done_marker_is_not_a_death(self) -> None:
        """A ``finish_reason`` arrived, so the generation finished. Metering is the gap."""
        result = read([delta_frame(content="whole"), finish_frame("stop")])
        assert result.died is False
        assert result.payload["choices"][0]["finish_reason"] == "stop"

    def test_a_truncated_turn_and_a_died_stream_are_different_objects(self) -> None:
        """The distinction the whole claim is about, asserted as a comparison."""
        truncated, _ = stream_seam(
            [delta_frame(content="ran out of budget"), finish_frame("length"), usage_frame({})]
        )
        truncated([{"role": "user", "content": "hi"}])
        died = self._died()

        assert truncated.meter.transcript[0]["stream_died"] is False
        assert truncated.meter.transcript[0]["finish_reason"] == ws.FINISH_TRUNCATED
        assert truncated.meter.truncated == 1
        assert died.meter.transcript[0]["stream_died"] is True
        assert died.meter.truncated == 0


# ── c34: streaming is the default, and off is byte-identical ──────────────────


class TestTheDefaultAndTheEscapeHatch:
    """Deviation ``d3``: default on for cortex/worker dials, off still reachable."""

    def test_a_seam_streams_unless_told_otherwise(self) -> None:
        seam = ws.WorkerSeam(base_url="http://x/v1", model="m", api_key="k", max_tokens=100)
        assert ws.DEFAULT_STREAM is True
        assert seam.stream is True
        assert seam.transport == ws.TRANSPORT_STREAM

    def test_streaming_off_puts_no_stream_key_on_the_wire(self) -> None:
        """ "Byte-identical with the flag off" — checked on the bytes."""
        seam, seen = stream_seam([], stream=False)
        sent: dict[str, Any] = {}

        def _post(body: dict[str, Any]) -> dict[str, Any]:
            sent.update(body)
            return COMPLETE_TURN_BLOCKING

        seam._post = _post  # type: ignore[method-assign]
        seam([{"role": "user", "content": "hi"}])

        assert "stream" not in sent
        assert "stream_options" not in sent
        assert seam.transport == ws.TRANSPORT_BLOCKING

    def test_the_blocking_transport_uses_the_request_scoped_clock(self) -> None:
        seam, seen = stream_seam([], stream=False)

        class _Blocking:
            def __enter__(self_inner: Any) -> Any:
                return self_inner

            def __exit__(self_inner: Any, *exc: Any) -> bool:
                return False

            def read(self_inner: Any) -> bytes:
                return json.dumps(COMPLETE_TURN_BLOCKING).encode("utf-8")

        def _open(request: Any, *, timeout: float) -> Any:
            seen["timeouts"].append(timeout)
            return _Blocking()

        seam._open = _open  # type: ignore[method-assign]
        seam([{"role": "user", "content": "hi"}])

        assert seen["timeouts"] == [ws.REQUEST_TIMEOUT]

    def test_the_cli_exposes_no_stream_and_defaults_to_streaming(self) -> None:
        parser = ws.build_parser()
        assert parser.parse_args([]).stream is True
        assert parser.parse_args(["--no-stream"]).stream is False

    def test_a_transport_error_on_a_streamed_dial_still_retries(self) -> None:
        """Nothing about streaming changes what happens before the body starts."""
        seam, seen = stream_seam([])
        refused = urllib.error.URLError("connection refused")
        seam._open = _raising(refused, seen)  # type: ignore[method-assign]

        with pytest.raises(ws.WorkerTransportError):
            seam([{"role": "user", "content": "hi"}])
        assert seen["dials"] == ws.MAX_TRANSPORT_RETRIES + 1
        assert seam.meter.stream_deaths == 0


def _raising(error: Exception, seen: dict[str, Any]) -> Callable[..., Any]:
    def _open(request: Any, *, timeout: float) -> Any:
        seen["dials"] += 1
        raise error

    return _open
