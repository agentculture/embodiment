import { describe, expect, it } from "vitest";
import { parseEnvelopeFrame } from "./events";

import stateFixture from "../../../tests/fixtures/events/state.json";

describe("parseEnvelopeFrame — round 2: the wire carries the WHOLE envelope", () => {
  it("accepts a real fixture file's exact JSON text as the envelope", () => {
    const result = parseEnvelopeFrame(JSON.stringify(stateFixture));
    expect(result.rejectionReason).toBeUndefined();
    expect(result.envelope).not.toBeNull();
    expect(result.envelope?.v).toBe(1);
    expect(result.envelope?.kind).toBe("state");
    expect(result.envelope?.data).toEqual(stateFixture.data);
  });

  it("rejects (and names the reason) unparseable JSON", () => {
    const result = parseEnvelopeFrame("{not json");
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("unparseable-json");
  });

  it("rejects a JSON array (not an object)", () => {
    const result = parseEnvelopeFrame("[1,2,3]");
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("not-an-object");
  });

  it("rejects null", () => {
    const result = parseEnvelopeFrame("null");
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("not-an-object");
  });

  it("rejects a top-level primitive", () => {
    const result = parseEnvelopeFrame('"just a string"');
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("not-an-object");
  });

  it("rejects v !== 1", () => {
    const result = parseEnvelopeFrame(JSON.stringify({ ...stateFixture, v: 2 }));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("unsupported-version");
  });

  it("rejects a missing v", () => {
    const { v: _v, ...withoutV } = stateFixture;
    const result = parseEnvelopeFrame(JSON.stringify(withoutV));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("unsupported-version");
  });

  it("rejects data missing entirely", () => {
    const { data: _data, ...withoutData } = stateFixture;
    const result = parseEnvelopeFrame(JSON.stringify(withoutData));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("data-not-object");
  });

  it("rejects data being an array", () => {
    const result = parseEnvelopeFrame(JSON.stringify({ ...stateFixture, data: [1, 2] }));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("data-not-object");
  });

  it("rejects data being null", () => {
    const result = parseEnvelopeFrame(JSON.stringify({ ...stateFixture, data: null }));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("data-not-object");
  });

  it("rejects data being a primitive", () => {
    const result = parseEnvelopeFrame(JSON.stringify({ ...stateFixture, data: "oops" }));
    expect(result.envelope).toBeNull();
    expect(result.rejectionReason).toBe("data-not-object");
  });

  it("passes through kind/ts/seq/source exactly as sent, unvalidated", () => {
    const result = parseEnvelopeFrame(
      JSON.stringify({ ...stateFixture, kind: "not-a-real-kind", ts: 123, seq: "nope", source: 9 }),
    );
    // still accepted: only v and data are validated here (routing comes
    // from which addEventListener fired, not this field)
    expect(result.envelope).not.toBeNull();
    expect(result.envelope?.kind).toBe("not-a-real-kind");
  });
});
