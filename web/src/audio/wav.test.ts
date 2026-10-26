import { describe, expect, it } from "vitest";
import { joinWav, readWav, splitHead, writeWav } from "./wav";

const fmt = { rate: 24000, channels: 1, bits: 16 };
const tone = (n: number, v = 7) => new Uint8Array(n * 2).fill(v);

describe("wav (d7)", () => {
  it("writes a WAV it reads back", () => {
    const w = writeWav(fmt, tone(100));
    const r = readWav(w);
    expect(r).toMatchObject(fmt);
    expect(r?.data).toEqual(tone(100));
  });

  it("joins two WAVs with a pause of silence between them", () => {
    const joined = readWav(joinWav(writeWav(fmt, tone(100, 1)), writeWav(fmt, tone(50, 2)), 0.01) as Uint8Array);
    expect(joined?.data.length).toBe((100 + 240 + 50) * 2);
    expect(joined?.data[0]).toBe(1);
    expect(joined?.data[(100 + 10) * 2]).toBe(0);
    expect(joined?.data[(100 + 240) * 2]).toBe(2);
  });

  it("refuses what it cannot join: not a WAV, or two formats", () => {
    expect(joinWav(new Uint8Array([1, 2, 3]), writeWav(fmt, tone(1)), 0)).toBeNull();
    expect(joinWav(writeWav(fmt, tone(1)), writeWav({ ...fmt, rate: 16000 }, tone(1)), 0)).toBeNull();
  });

  it("splits off the first sentence as the daemon splits sentences", () => {
    expect(splitHead("One thing. Then the rest. And more.")).toEqual({ head: "One thing.", rest: "Then the rest. And more." });
    expect(splitHead("שלום רב. ועוד משפט.")).toEqual({ head: "שלום רב.", rest: "ועוד משפט." });
    expect(splitHead("Only one sentence.")).toBeNull();
    expect(splitHead("3.5 percent rose")).toBeNull();
  });
});
