import { describe, expect, it } from "vitest";
import { SSEFrameParser } from "./sseFraming";

describe("SSEFrameParser", () => {
  it("parses one complete event: + data: frame", () => {
    const parser = new SSEFrameParser();
    const events = parser.push('event: state\ndata: {"a":1}\n\n');
    expect(events).toEqual([{ event: "state", data: '{"a":1}', id: undefined }]);
  });

  it("parses several frames in one push", () => {
    const parser = new SSEFrameParser();
    const events = parser.push(
      "event: state\ndata: {\"a\":1}\n\n" + "event: mic\ndata: {\"hot\":true}\n\n",
    );
    expect(events).toHaveLength(2);
    expect(events[0].event).toBe("state");
    expect(events[1].event).toBe("mic");
  });

  it("joins multiple data: lines with \\n, per the SSE spec", () => {
    const parser = new SSEFrameParser();
    const events = parser.push("event: reply\ndata: line one\ndata: line two\n\n");
    expect(events[0].data).toBe("line one\nline two");
  });

  it("captures the id: field", () => {
    const parser = new SSEFrameParser();
    const events = parser.push("event: heartbeat\nid: 42\ndata: {}\n\n");
    expect(events[0].id).toBe("42");
  });

  it("ignores comment lines (leading colon)", () => {
    const parser = new SSEFrameParser();
    const events = parser.push(": keep-alive\nevent: state\ndata: {}\n\n");
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("state");
  });

  it("ignores a retry: field (this app manages its own backoff)", () => {
    const parser = new SSEFrameParser();
    const events = parser.push("retry: 3000\nevent: state\ndata: {}\n\n");
    expect(events).toHaveLength(1);
  });

  it("handles a frame split across multiple push() calls (chunk boundary mid-line)", () => {
    const parser = new SSEFrameParser();
    expect(parser.push("event: sta")).toEqual([]);
    expect(parser.push("te\ndata: {\"a\":")).toEqual([]);
    const events = parser.push("1}\n\n");
    expect(events).toEqual([{ event: "state", data: '{"a":1}', id: undefined }]);
  });

  it("handles a chunk boundary landing exactly on the blank-line terminator", () => {
    const parser = new SSEFrameParser();
    expect(parser.push("event: state\ndata: {}\n")).toEqual([]);
    const events = parser.push("\n");
    expect(events).toHaveLength(1);
  });

  it("handles CRLF line endings", () => {
    const parser = new SSEFrameParser();
    const events = parser.push('event: state\r\ndata: {"a":1}\r\n\r\n');
    expect(events).toEqual([{ event: "state", data: '{"a":1}', id: undefined }]);
  });

  it("handles a CRLF terminator split exactly at the \\r|\\n boundary across two pushes", () => {
    const parser = new SSEFrameParser();
    expect(parser.push('event: state\r\ndata: {}\r')).toEqual([]);
    const events = parser.push("\n\r\n");
    expect(events).toHaveLength(1);
  });

  it("defaults data: with no leading space stripped only when exactly one space follows the colon", () => {
    const parser = new SSEFrameParser();
    // "data:  two spaces" -- only ONE leading space is stripped per spec,
    // so the second space is part of the value.
    const events = parser.push("event: state\ndata:  two spaces\n\n");
    expect(events[0].data).toBe(" two spaces");
  });

  it("tolerates a field with no colon at all (a field name with an empty value)", () => {
    const parser = new SSEFrameParser();
    const events = parser.push("event: state\ndata\n\n");
    expect(events[0].data).toBe("");
  });

  it("never dispatches a frame with no data: line at all", () => {
    const parser = new SSEFrameParser();
    const events = parser.push("event: state\n\n");
    expect(events).toEqual([]);
  });

  it("does not carry state across a dispatched frame into the next one", () => {
    const parser = new SSEFrameParser();
    const events = parser.push(
      "event: state\ndata: {\"a\":1}\n\n" + "data: {\"b\":2}\n\n", // second frame has no event: -> defaults to \"message\"
    );
    expect(events).toHaveLength(2);
    expect(events[1].event).toBe("message");
    expect(events[1].data).toBe('{"b":2}');
  });

  it("survives an attack: a NUL byte inside id: is ignored per spec, huge data:, and the module's own delimiters inside data", () => {
    const huge = "x".repeat(20_000);
    const parser = new SSEFrameParser();
    const events = parser.push(
      `event: transcript\nid: bad\u0000id\ndata: ${huge}event: fake\\ndata: injected\n\n`,
    );
    expect(events).toHaveLength(1);
    expect(events[0].id).toBeUndefined(); // NUL-bearing id is ignored, not stored
    expect(events[0].data).toContain(huge);
  });

  it("handles many frames in a single huge push without losing any", () => {
    const parser = new SSEFrameParser();
    let text = "";
    for (let i = 0; i < 2000; i += 1) {
      text += `event: features\nid: ${i}\ndata: {"i":${i}}\n\n`;
    }
    const events = parser.push(text);
    expect(events).toHaveLength(2000);
    expect(events[0].id).toBe("0");
    expect(events[1999].id).toBe("1999");
  });
});
