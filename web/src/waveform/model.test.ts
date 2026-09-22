// waveform/model.test.ts
//
// Task t18 acceptance criterion 1 (verbatim): "with no microphone permission
// and no audio element, the assistant trace moves from fixture feature
// events and goes flat, not frozen, when the stream stops." Proved here
// entirely against the pure model (no DOM, no canvas, no mic, no audio
// element anywhere in this file or its imports) -- exactly the "no
// microphone permission and no audio element" precondition, satisfied by
// construction rather than by mocking absence.

import { describe, expect, it } from "vitest";
import {
  DEFAULT_IDLE_AFTER_MS,
  oscilloscopeModel,
  sampleFromFeatures,
  traceFrame,
} from "./model";
import type { EventEnvelope, FeaturesData } from "../api/events";

function encodeEnvelope(mins: number[], maxs: number[]): string {
  const bytes = new Uint8Array([...mins, ...maxs].map((v) => v & 0xff));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function featuresEvent(
  overrides: Partial<FeaturesData> = {},
  seq = 1,
): EventEnvelope<"features"> {
  const mins = Array.from({ length: 16 }, () => -40);
  const maxs = Array.from({ length: 16 }, () => 40);
  return {
    v: 1,
    kind: "features",
    ts: "2026-09-22T12:00:00.000Z",
    seq,
    source: "app://embodiment",
    data: {
      direction: "out",
      env: encodeEnvelope(mins, maxs),
      level_db: -20,
      noise_floor_db: -60,
      zero_crossing_hz: 200,
      ...overrides,
    },
  };
}

describe("sampleFromFeatures", () => {
  it("decodes a real fixture-shaped features event", () => {
    const sample = sampleFromFeatures(featuresEvent(), 1_000);
    expect(sample).not.toBeNull();
    expect(sample?.bars).toHaveLength(16);
    expect(sample?.direction).toBe("out");
    expect(sample?.receivedAtMs).toBe(1_000);
  });

  it("returns null (never throws) for an undecodable envelope", () => {
    const sample = sampleFromFeatures(featuresEvent({ env: "not valid base64!!!" }), 1_000);
    expect(sample).toBeNull();
  });

  it("returns null for a missing env field", () => {
    const bad = featuresEvent();
    delete bad.data.env;
    expect(sampleFromFeatures(bad, 1_000)).toBeNull();
  });
});

describe("traceFrame: idle is its own state, never a frozen last frame", () => {
  it("is idle with no sample at all", () => {
    const frame = traceFrame(null, 1_000, 500);
    expect(frame.isIdle).toBe(true);
    expect(frame.bars).toEqual([]);
  });

  it("is live immediately after a sample arrives", () => {
    const sample = sampleFromFeatures(featuresEvent(), 1_000);
    const frame = traceFrame(sample, 1_050, 500);
    expect(frame.isIdle).toBe(false);
    expect(frame.bars).toHaveLength(16);
  });

  it("goes flat (idle) once idleAfterMs has passed since the sample, with no new event", () => {
    const sample = sampleFromFeatures(featuresEvent(), 1_000);
    // Exactly at the boundary and beyond -- still idle.
    expect(traceFrame(sample, 1_500, 500).isIdle).toBe(true);
    expect(traceFrame(sample, 1_500, 500).bars).toEqual([]);
    expect(traceFrame(sample, 9_999, 500).isIdle).toBe(true);
  });

  it("stays live right up to the boundary", () => {
    const sample = sampleFromFeatures(featuresEvent(), 1_000);
    expect(traceFrame(sample, 1_499, 500).isIdle).toBe(false);
  });

  it("uses the real default idle window when none is passed", () => {
    const sample = sampleFromFeatures(featuresEvent(), 0);
    expect(traceFrame(sample, DEFAULT_IDLE_AFTER_MS - 1).isIdle).toBe(false);
    expect(traceFrame(sample, DEFAULT_IDLE_AFTER_MS + 1).isIdle).toBe(true);
  });
});

describe("the assistant trace moves from fixture feature events, and goes flat -- not frozen -- when the stream stops", () => {
  it("produces DIFFERENT bar frames across a series of distinct fixture events (motion)", () => {
    const eventA = featuresEvent({
      env: encodeEnvelope(
        Array.from({ length: 16 }, () => -10),
        Array.from({ length: 16 }, () => 10),
      ),
    });
    const eventB = featuresEvent(
      {
        env: encodeEnvelope(
          Array.from({ length: 16 }, () => -100),
          Array.from({ length: 16 }, () => 100),
        ),
      },
      2,
    );

    const sampleA = sampleFromFeatures(eventA, 1_000);
    const frameA = traceFrame(sampleA, 1_010, 500);

    const sampleB = sampleFromFeatures(eventB, 1_020);
    const frameB = traceFrame(sampleB, 1_030, 500);

    expect(frameA.isIdle).toBe(false);
    expect(frameB.isIdle).toBe(false);
    // Genuine motion: the bars actually differ between the two fixture
    // events, not merely two live frames that happen to render the same.
    expect(frameA.bars).not.toEqual(frameB.bars);
  });

  it("goes flat -- not a frozen repeat of the last live frame -- once the stream stops sending events", () => {
    const event = featuresEvent();
    const sample = sampleFromFeatures(event, 1_000);
    const lastLiveFrame = traceFrame(sample, 1_100, 500);
    expect(lastLiveFrame.isIdle).toBe(false);
    expect(lastLiveFrame.bars.length).toBeGreaterThan(0);

    // No new sample arrives (the stream stopped); time keeps advancing past
    // the idle window. The resulting frame must be the flat idle shape, not
    // a repeat of `lastLiveFrame`.
    const afterStop = traceFrame(sample, 1_100 + 501, 500);
    expect(afterStop.isIdle).toBe(true);
    expect(afterStop.bars).toEqual([]);
    expect(afterStop.bars).not.toEqual(lastLiveFrame.bars);
  });

  it("keeps the listener (in) trace independently idle/live from the assistant (out) trace", () => {
    const outSample = sampleFromFeatures(featuresEvent({ direction: "out" }), 1_000);
    const inSample = sampleFromFeatures(featuresEvent({ direction: "in" }, 2), 1_000);

    // Only the assistant trace's stream stops; the listener trace keeps
    // getting fresh samples.
    const modelJustAfterOutStops = oscilloscopeModel(outSample, inSample, 1_100, 500);
    expect(modelJustAfterOutStops.out.isIdle).toBe(false);
    expect(modelJustAfterOutStops.in.isIdle).toBe(false);

    const laterInSample = sampleFromFeatures(featuresEvent({ direction: "in" }, 3), 1_400);
    const modelWhileOutIdleInLive = oscilloscopeModel(outSample, laterInSample, 1_600, 500);
    expect(modelWhileOutIdleInLive.out.isIdle).toBe(true); // 600ms since outSample -- flat
    expect(modelWhileOutIdleInLive.in.isIdle).toBe(false); // 200ms since laterInSample -- live
  });
});
