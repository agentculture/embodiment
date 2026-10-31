import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import { addPending, loadPending } from "../api/pendingChats";

/** A fetch that answers POST /api/chat with `post` and every GET /api/chat/{id} from `gets` in turn. */
function server(post: { status: number; body: unknown }, gets: unknown[]) {
  const queue = [...gets];
  return vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const isPost = (init?.method ?? "GET") === "POST";
    const body = isPost ? post.body : (queue.length > 1 ? queue.shift() : queue[0]);
    const status = isPost ? post.status : 200;
    return { ok: status < 400, status, json: async () => body, url: String(url) } as unknown as Response;
  });
}
const TIMEOUT = { status: 503, body: { error: { code: "http-control-timeout" } } };
const PENDING = { state: "pending", result: null };
const done = (reply: string) => ({ state: "done", result: { ok: true, reply, sources: [], turn_serial: 9, code: "", fallback: null } });

async function send(text: string) {
  await userEvent.type(screen.getByLabelText("message"), text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

beforeEach(() => localStorage.clear());

describe("an answer that outlives its request", () => {
  it("waits instead of failing when the route's clock runs out, then shows the late answer", async () => {
    const fetchFn = server(TIMEOUT, [PENDING, PENDING, done("Arr, the late answer.")]);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    await send("slow question");
    expect(await screen.findByText(/Still working on this/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(await screen.findByText(/Arr, the late answer/)).toBeInTheDocument();
    expect(screen.queryByText(/Still working on this/)).toBeNull();
    const id = JSON.parse((fetchFn.mock.calls[0][1] as RequestInit).body as string).request_id;
    expect(fetchFn.mock.calls.some((c) => String(c[0]) === `/api/chat/${id}`)).toBe(true);
    expect(loadPending()).toEqual([]);
  });

  it("recovers a question asked before the page was left", async () => {
    addPending({ id: "req-left000000", question: "asked before leaving", conversation: null, at: Date.now() });
    const fetchFn = server({ status: 200, body: {} }, [done("Here it is.")]);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    expect(screen.getByText("asked before leaving")).toBeInTheDocument();
    expect(await screen.findByText(/Here it is/)).toBeInTheDocument();
    expect(fetchFn.mock.calls[0][0]).toBe("/api/chat/req-left000000");
    expect(loadPending()).toEqual([]);
  });

  it("only recovers questions of its own conversation", async () => {
    addPending({ id: "req-other00000", question: "in another conversation", conversation: "c9", at: Date.now() });
    const fetchFn = server({ status: 200, body: {} }, [done("x")]);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    expect(screen.queryByText("in another conversation")).toBeNull();
    expect(loadPending()).toHaveLength(1);
  });

  it("does not repeat an answer the reopened conversation's history already holds", async () => {
    addPending({ id: "req-old0000000", question: "q old", conversation: "c1", at: Date.now() });
    const fetchFn = server({ status: 200, body: {} }, [done("a old")]);
    render(
      <Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} conversation="c1"
        initialTurns={[{ role: "user", text: "q old" }, { role: "assistant", text: "a old" }]} />,
    );
    expect(screen.getAllByText("q old")).toHaveLength(1);
    expect(loadPending()).toEqual([]);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("says plainly when the answer was lost to a restart", async () => {
    addPending({ id: "req-lost000000", question: "lost one", conversation: null, at: Date.now() });
    render(<Chat secret="k" clientOptions={{ fetchFn: server({ status: 200, body: {} }, [{ state: "unknown", result: null }]) }} pollMs={10} />);
    expect(await screen.findByText(/has no answer for this question/)).toBeInTheDocument();
    expect(loadPending()).toEqual([]);
  });

  it("shows a finished turn's own refusal code", async () => {
    const fetchFn = server(TIMEOUT, [{ state: "done", result: { ok: false, code: "app-text-model-unavailable" } }]);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    await send("q");
    expect(await screen.findByText(/could not answer/)).toHaveTextContent("app-text-model-unavailable");
  });

  it("a POST cut off mid-flight says so, and the next visit recovers its answer", async () => {
    const cut = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    const first = render(<Chat secret="k" clientOptions={{ fetchFn: cut as unknown as typeof fetch }} pollMs={10} />);
    await send("cut off");
    expect(await screen.findByRole("alert")).toHaveTextContent("network-error");
    expect(loadPending().map((p) => p.question)).toEqual(["cut off"]);
    first.unmount();
    const fetchFn = server({ status: 200, body: {} }, [done("Recovered after the cut.")]);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    expect(await screen.findByText(/Recovered after the cut/)).toBeInTheDocument();
    expect(loadPending()).toEqual([]);
  });

  it("a cut-off POST the daemon never received ends with a plain note, not a hang", async () => {
    addPending({ id: "req-never00000", question: "never arrived", conversation: null, at: Date.now() });
    render(<Chat secret="k" clientOptions={{ fetchFn: server({ status: 200, body: {} }, [{ state: "unknown", result: null }]) }} pollMs={10} />);
    expect(await screen.findByText(/has no answer for this question/)).toBeInTheDocument();
  });

  it("a request answered in time leaves nothing pending", async () => {
    const fetchFn = server({ status: 200, body: { reply: "fast", sources: [] } }, []);
    render(<Chat secret="k" clientOptions={{ fetchFn }} pollMs={10} />);
    await send("q");
    await screen.findByText(/fast/);
    expect(loadPending()).toEqual([]);
  });
});
