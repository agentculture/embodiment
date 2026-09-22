// api/realtimeSettings.ts
//
// Where the browser ear (`audio/browserEar.ts`) gets the WebSocket URL for
// t14's `RemoteEndpoint` (`embodiment/audio/remote.py`) and whether the
// browser mic is enabled at all. This task's integrator notes: "The
// WebSocket URL and whether the browser ear is enabled come from a settings
// object; the daemon (t15) will expose the endpoint address in `/api/status`
// later, so read it from there if present, else from a setting."
//
// t15 (the daemon composing `/api/status`) does not exist yet in this
// worktree's base (see this task's final report) -- `readRealtimeSettings`
// is written against the CONTRACT the integrator note describes, not against
// a live `/api/status` response this task has ever seen. When t15 ships a
// real field, whatever key it actually uses must be reconciled here; the
// name below (`realtime_ws_url`) is this task's own choice, not one
// t15 confirmed.

/** The status-response field name this module reads, if present -- t15's
 *  contract, per this task's integrator note (not yet observed against a
 *  real /api/status; see this file's header). */
export const STATUS_REALTIME_WS_URL_FIELD = "realtime_ws_url";
export const STATUS_REALTIME_EAR_ENABLED_FIELD = "realtime_ear_enabled";

export interface RealtimeEarSetting {
  /** Explicit fallback used when `/api/status` carries no
   *  `realtime_ws_url` field (t15 not yet deployed, or the field not yet
   *  shipped). Defaults to loopback at `RemoteEndpointConfig`'s own default
   *  port (`embodiment/audio/remote.py`, 8765) -- a judgement call, not
   *  measured against any real deployment, exactly like that default's own
   *  docstring says of itself. */
  wsUrl?: string;
  enabled?: boolean;
}

export interface ResolvedRealtimeEarSettings {
  wsUrl: string;
  enabled: boolean;
  /** Which source won: the live status response, or the fallback setting.
   *  Exposed so a caller can show which one is in effect (C3: a mode must
   *  be observable, not merely correct under the hood). */
  source: "status" | "setting";
}

export const DEFAULT_REALTIME_WS_URL = "ws://127.0.0.1:8765";

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/**
 * Resolve the browser ear's WebSocket URL and enabled flag: `/api/status`
 * (if it carries the field) wins over the explicit setting, which wins over
 * this module's own default. Never throws: an absent or malformed `status`
 * object degrades to the setting/default, exactly like every other public
 * function in this app is required to (lesson 3).
 */
export function readRealtimeSettings(
  status: Record<string, unknown> | null | undefined,
  setting: RealtimeEarSetting = {},
): ResolvedRealtimeEarSettings {
  const statusWsUrl = status ? readString(status[STATUS_REALTIME_WS_URL_FIELD]) : null;
  const statusEnabled = status ? readBoolean(status[STATUS_REALTIME_EAR_ENABLED_FIELD]) : null;

  if (statusWsUrl !== null) {
    return {
      wsUrl: statusWsUrl,
      enabled: statusEnabled ?? setting.enabled ?? false,
      source: "status",
    };
  }

  return {
    wsUrl: setting.wsUrl ?? DEFAULT_REALTIME_WS_URL,
    enabled: setting.enabled ?? false,
    source: "setting",
  };
}
