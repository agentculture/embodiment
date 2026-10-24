// The room log shows spoken turns only: typed turns arrive with `typed: true`
// and the chat already shows them (live duplicate seen 2026-10-24).
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { EventEnvelope } from "../api/events";

const ev = (seq: number, kind: "transcript" | "reply", data: Record<string, unknown>) =>
  ({ v: 1, kind, ts: "t", seq, source: "app://embodiment", data }) as EventEnvelope<"transcript" | "reply">;

describe("Transcript", () => {
  it("skips typed turns and labels spoken ones", () => {
    render(
      <Transcript
        entries={[
          ev(1, "transcript", { role: "user", text: "typed hello", typed: true }),
          ev(2, "reply", { text: "typed answer", typed: true }),
          ev(3, "transcript", { role: "user", text: "מה שלומך" }),
          ev(4, "reply", { text: "טוב, תודה" }),
        ]}
      />,
    );
    expect(screen.queryByText("typed hello")).toBeNull();
    expect(screen.queryByText("typed answer")).toBeNull();
    expect(screen.getByText("מה שלומך")).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Gwen, spoken")).toBeInTheDocument();
  });

  it("is empty when every turn was typed", () => {
    render(<Transcript entries={[ev(1, "transcript", { role: "user", text: "x", typed: true })]} />);
    expect(screen.getByText("No speech yet.")).toBeInTheDocument();
  });
});
