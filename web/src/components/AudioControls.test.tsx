import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AudioControls, MAX_CLIP_SECONDS, RECORD_LIMIT_MS, SpeakButton } from "./AudioControls";

const resp = (status: number, body: unknown, extra: Partial<Response> = {}) =>
  ({ ok: status < 400, status, json: async () => body, blob: async () => new Blob(["RIFFxxxx"], { type: "audio/wav" }), ...extra }) as Response;

let played: string[] = [];
let revoked: string[] = [];
let endedHandlers: Array<() => void> = [];
beforeEach(() => {
  played = []; revoked = []; endedHandlers = [];
  URL.createObjectURL = vi.fn(() => "blob:synthetic-1");
  URL.revokeObjectURL = vi.fn((u: string) => { revoked.push(u); });
  class FakeAudio {
    src: string;
    onended: (() => void) | null = null;
    constructor(src: string) { this.src = src; }
    play() { played.push(this.src); endedHandlers.push(() => this.onended?.()); return Promise.resolve(); }
    pause() {}
  }
  vi.stubGlobal("Audio", FakeAudio);
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("SpeakButton", () => {
  it("posts the message text to /api/speak and plays the returned audio, then revokes the url", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, null));
    render(<SpeakButton text="שלום עולם" secret="k" clientOptions={{ fetchFn }} />);
    await userEvent.click(screen.getByRole("button", { name: /speak/i }));
    await waitFor(() => expect(played).toEqual(["blob:synthetic-1"]));
    expect(fetchFn.mock.calls[0][0]).toBe("/api/speak");
    expect(JSON.parse(String(fetchFn.mock.calls[0][1]?.body))).toEqual({ text: "שלום עולם" });
    endedHandlers[0]();
    expect(revoked).toEqual(["blob:synthetic-1"]);
  });
  it("shows a visible alert when synthesis is refused or playback is blocked", async () => {
    render(<SpeakButton text="x" secret="" clientOptions={{ fetchFn: async () => resp(502, { error: { code: "speak-tts-failed" } }) }} />);
    await userEvent.click(screen.getByRole("button", { name: /speak/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("speak-tts-failed");
  });
  it("reports playback failure", async () => {
    vi.stubGlobal("Audio", class { onended = null; play() { return Promise.reject(new Error("blocked")); } pause() {} });
    render(<SpeakButton text="x" secret="" clientOptions={{ fetchFn: async () => resp(200, null) }} />);
    await userEvent.click(screen.getByRole("button", { name: /speak/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("playback-failed");
  });
});

describe("AudioControls upload", () => {
  it("posts an uploaded file raw to /api/clip and hands the transcript and reply to onReply", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, { ok: true, transcript: "heard", reply: "answer", code: "" }));
    const onReply = vi.fn();
    render(<AudioControls secret="" onReply={onReply} clientOptions={{ fetchFn }} />);
    const file = new File(["abc"], "clip.ogg", { type: "audio/ogg" });
    await userEvent.upload(screen.getByLabelText(/upload audio/i), file);
    await waitFor(() => expect(onReply).toHaveBeenCalledWith({ transcript: "heard", reply: "answer" }));
    expect(fetchFn.mock.calls[0][0]).toBe("/api/clip");
    expect(fetchFn.mock.calls[0][1]?.body).toBe(file);
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>)["Content-Type"]).toBe("audio/ogg");
  });
  it("surfaces a 415 refusal as an alert and does not call onReply", async () => {
    const onReply = vi.fn();
    render(<AudioControls secret="" onReply={onReply} clientOptions={{ fetchFn: async () => resp(415, { ok: false, code: "refused-unsupported-format" }) }} />);
    await userEvent.upload(screen.getByLabelText(/upload audio/i), new File(["a"], "c.mp4", { type: "audio/mp4" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("refused-unsupported-format");
    expect(onReply).not.toHaveBeenCalled();
  });
});

describe("AudioControls record", () => {
  function stubRecorder() {
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] }));
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    class FakeRecorder {
      mimeType = "audio/webm;codecs=opus";
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() {}
      stop() { this.ondataavailable?.({ data: new Blob(["opus"], { type: this.mimeType }) }); this.onstop?.(); }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    return { stopTrack, getUserMedia };
  }
  it("records, stops, posts the clip as recorded, and releases the microphone", async () => {
    const { stopTrack } = stubRecorder();
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, { ok: true, transcript: "t", reply: "r" }));
    const onReply = vi.fn();
    render(<AudioControls secret="k" onReply={onReply} clientOptions={{ fetchFn }} />);
    await userEvent.click(screen.getByRole("button", { name: /record/i }));
    await userEvent.click(await screen.findByRole("button", { name: /stop/i }));
    await waitFor(() => expect(onReply).toHaveBeenCalledWith({ transcript: "t", reply: "r" }));
    const init = fetchFn.mock.calls[0][1];
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("audio/webm;codecs=opus");
    expect(stopTrack).toHaveBeenCalled();
  });
  it("stops by itself before the server's clip cap, so a forgotten recording still lands", async () => {
    const { stopTrack } = stubRecorder();
    const held: Array<() => void> = [];
    const cleared: unknown[] = [];
    const realSet = globalThis.setTimeout;
    const realClear = globalThis.clearTimeout;
    vi.stubGlobal("setTimeout", ((fn: () => void, ms?: number) => {
      if (ms === RECORD_LIMIT_MS) { held.push(fn); return 4242 as unknown as ReturnType<typeof setTimeout>; }
      return realSet(fn, ms);
    }) as typeof setTimeout);
    vi.stubGlobal("clearTimeout", ((id?: ReturnType<typeof setTimeout>) => { cleared.push(id); realClear(id); }) as typeof clearTimeout);
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, { ok: true, transcript: "t", reply: "r" }));
    const onReply = vi.fn();
    render(<AudioControls secret="k" onReply={onReply} clientOptions={{ fetchFn }} />);
    await userEvent.click(screen.getByRole("button", { name: /record/i }));
    await screen.findByRole("button", { name: /stop/i });
    expect(MAX_CLIP_SECONDS).toBe(60);
    expect(RECORD_LIMIT_MS).toBeLessThan(MAX_CLIP_SECONDS * 1000);
    expect(held).toHaveLength(1);
    act(() => held[0]());
    await waitFor(() => expect(onReply).toHaveBeenCalledWith({ transcript: "t", reply: "r" }));
    expect(stopTrack).toHaveBeenCalled();
    expect(cleared).toContain(4242);
  });
  it("alerts when the microphone is denied or unavailable", async () => {
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: async () => { throw new Error("denied"); } }, configurable: true });
    vi.stubGlobal("MediaRecorder", class {});
    render(<AudioControls secret="" onReply={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /record/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("microphone-unavailable");
  });
  it("alerts when MediaRecorder does not exist", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    render(<AudioControls secret="" onReply={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /record/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("recording-unsupported");
  });
});

describe("speech-to-speech button", () => {
  it("is disabled with its reason shown while no role is advertised", () => {
    render(<AudioControls secret="" onReply={vi.fn()} />);
    const btn = screen.getByRole("button", { name: /speech-to-speech/i });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/no speech-to-speech role is advertised/i)).toBeInTheDocument();
  });
  it("stays disabled even with a role: turning it on is not in this cycle", () => {
    render(<AudioControls secret="" onReply={vi.fn()} s2sRole="s2s-demo" />);
    expect(screen.getByRole("button", { name: /speech-to-speech/i })).toBeDisabled();
    expect(screen.getByText(/s2s-demo/)).toHaveAttribute("dir", "auto");
    expect(screen.getByText(/not enabled in this release/i)).toBeInTheDocument();
  });
});
