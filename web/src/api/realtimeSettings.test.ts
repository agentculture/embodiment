import { describe, expect, it } from "vitest";
import { DEFAULT_REALTIME_WS_URL, readRealtimeSettings } from "./realtimeSettings";

describe("readRealtimeSettings", () => {
  it("falls back to the setting (or default) when status is absent", () => {
    expect(readRealtimeSettings(null)).toEqual({
      wsUrl: DEFAULT_REALTIME_WS_URL,
      enabled: false,
      source: "setting",
    });
  });

  it("uses the explicit setting when status carries no field", () => {
    const resolved = readRealtimeSettings({}, { wsUrl: "ws://example:9", enabled: true });
    expect(resolved).toEqual({ wsUrl: "ws://example:9", enabled: true, source: "setting" });
  });

  it("prefers the status field over the setting when present", () => {
    const resolved = readRealtimeSettings(
      { realtime_ws_url: "ws://from-status:1", realtime_ear_enabled: true },
      { wsUrl: "ws://from-setting:2", enabled: false },
    );
    expect(resolved).toEqual({ wsUrl: "ws://from-status:1", enabled: true, source: "status" });
  });

  it("ignores a malformed status field (wrong type) and falls back", () => {
    const resolved = readRealtimeSettings({ realtime_ws_url: 12345 });
    expect(resolved.source).toBe("setting");
  });

  it("never throws on a status object with unexpected shapes", () => {
    expect(() => readRealtimeSettings({ realtime_ws_url: null })).not.toThrow();
    expect(() => readRealtimeSettings(undefined)).not.toThrow();
  });
});
