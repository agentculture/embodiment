import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useVisualViewport } from "./useVisualViewport";

// d5 (live t9, the operator's phone 2026-10-26): after a refresh the header was scrolled
// away and the dock floated above empty space. The app follows the visual viewport; these
// pin when it reads it again and that the document itself never stays scrolled.

class FakeViewport extends EventTarget {
  height = 600;
  offsetTop = 0;
}

function Probe() {
  useVisualViewport();
  return null;
}

const root = () => document.documentElement.style;
let vv: FakeViewport;

beforeEach(() => {
  vi.useFakeTimers();
  vv = new FakeViewport();
  Object.defineProperty(window, "visualViewport", { value: vv, configurable: true });
  root().removeProperty("--app-h");
  root().removeProperty("--app-top");
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useVisualViewport (d5)", () => {
  it("reads the viewport at mount", () => {
    render(<Probe />);
    expect(root().getPropertyValue("--app-h")).toBe("600px");
    expect(root().getPropertyValue("--app-top")).toBe("0px");
  });

  it("reads it again once the browser's toolbars settle, with no viewport event", () => {
    render(<Probe />);
    vv.height = 720;
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(root().getPropertyValue("--app-h")).toBe("720px");
  });

  it("reads it again on pageshow, load, orientation change and window resize", () => {
    render(<Probe />);
    for (const [i, name] of ["pageshow", "load", "orientationchange", "resize"].entries()) {
      vv.height = 500 + i;
      act(() => {
        window.dispatchEvent(new Event(name));
      });
      expect(root().getPropertyValue("--app-h")).toBe(`${500 + i}px`);
    }
  });

  it("never places the app above the screen", () => {
    vv.offsetTop = -60;
    render(<Probe />);
    expect(root().getPropertyValue("--app-top")).toBe("0px");
  });

  it("turns off scroll restoration and brings a scrolled document back to the top", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    Object.defineProperty(window, "scrollY", { value: 64, configurable: true });
    render(<Probe />);
    expect(history.scrollRestoration).toBe("manual");
    expect(scrollTo).toHaveBeenCalledWith(0, 0);
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  });

  it("leaves the document alone while a field has focus (the keyboard moves the viewport)", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    Object.defineProperty(window, "scrollY", { value: 64, configurable: true });
    render(<Probe />);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(scrollTo).not.toHaveBeenCalled();
    input.remove();
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  });
});
