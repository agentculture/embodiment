import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConnectionStatus } from "./ConnectionStatus";

describe("ConnectionStatus", () => {
  it.each([
    ["connecting", "connecting"],
    ["connected", "connected"],
    ["disconnected", "disconnected"],
    ["unauthorized", "not authorised"],
  ] as const)("labels %s as %s", (status, label) => {
    render(<ConnectionStatus status={status} />);
    expect(screen.getByRole("status")).toHaveAttribute("data-status", status);
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });
});
