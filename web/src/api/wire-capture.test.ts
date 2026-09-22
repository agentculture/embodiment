import { describe, expect, it } from "vitest";
import { parseEnvelopeFrame } from "./events";

// The exact bytes captured from a real probe SSE server
// (scratchpad/sse_probe_server.py) serving this task's built dist and the
// committed fixtures verbatim, via `curl -sN http://127.0.0.1:.../api/events`.
// Pinned here as a literal so this test never depends on a live server.
const CAPTURED_STATE_FRAME =
  '{"v": 1, "kind": "state", "ts": "2026-09-22T12:00:00.000Z", "seq": 1, "source": "app://embodiment", "data": {"component": "daemon", "status": "up"}}';
const CAPTURED_DEGRADATION_FRAME =
  '{"v": 1, "kind": "degradation", "ts": "2026-09-22T12:00:05.000Z", "seq": 7, "source": "app://embodiment", "data": {"source": "continuity", "code": "eidetic-unavailable", "reason": "ConnectionError: connect failed"}}';
const CAPTURED_FEATURES_FRAME =
  '{"v": 1, "kind": "features", "ts": "2026-09-22T00:00:00Z", "source": "app://embodiment", "data": {"direction": "out", "env": "AOTOxcrd+OvTxsfW8PTZyAAcMjs2IwgVLTo5KhAMJzg=", "level_db": -19.99999761581421, "noise_floor_db": -60.0, "zero_crossing_hz": 200.0}, "seq": 8}';

describe("wire capture from the real probe SSE server (round 2 verification)", () => {
  it("parses a captured real state frame with a non-empty status", () => {
    const { envelope } = parseEnvelopeFrame(CAPTURED_STATE_FRAME);
    expect(envelope?.data.status).toBe("up");
  });

  it("parses a captured real degradation frame with a non-empty code/reason", () => {
    const { envelope } = parseEnvelopeFrame(CAPTURED_DEGRADATION_FRAME);
    expect(envelope?.data.code).toBe("eidetic-unavailable");
    expect(envelope?.data.reason).toBe("ConnectionError: connect failed");
  });

  it("parses a captured real features frame with a non-empty env", () => {
    const { envelope } = parseEnvelopeFrame(CAPTURED_FEATURES_FRAME);
    expect(typeof envelope?.data.env).toBe("string");
    expect((envelope?.data.env as string).length).toBeGreaterThan(0);
  });
});
