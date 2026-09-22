import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  INSTALL_SECRET_COOKIE_NAME,
  INSTALL_SECRET_SESSION_KEY,
  buildInstallSecretCookie,
  loadInstallSecretFromSession,
  saveInstallSecretToSession,
  setInstallSecretCookie,
} from "./secret";

describe("buildInstallSecretCookie", () => {
  it("is exactly this shape on http, with no Secure attribute", () => {
    const cookie = buildInstallSecretCookie("abc123", { protocol: "http:" });
    expect(cookie).toBe("embodiment_secret=abc123; Path=/; SameSite=Strict");
  });

  it("adds Secure on https", () => {
    const cookie = buildInstallSecretCookie("abc123", { protocol: "https:" });
    expect(cookie).toBe("embodiment_secret=abc123; Path=/; SameSite=Strict; Secure");
  });

  it("uses the exported cookie name constant", () => {
    expect(buildInstallSecretCookie("x", { protocol: "http:" })).toContain(
      `${INSTALL_SECRET_COOKIE_NAME}=x`,
    );
  });
});

describe("setInstallSecretCookie", () => {
  it("writes the exact cookie string through the injected writer, once", () => {
    const writer = vi.fn();
    setInstallSecretCookie("s3cr3t", { protocol: "http:", cookieWriter: writer });
    expect(writer).toHaveBeenCalledTimes(1);
    expect(writer).toHaveBeenCalledWith("embodiment_secret=s3cr3t; Path=/; SameSite=Strict");
  });

  it("never throws even when the writer throws", () => {
    const writer = vi.fn(() => {
      throw new Error("cookies disabled");
    });
    expect(() => setInstallSecretCookie("s3cr3t", { cookieWriter: writer })).not.toThrow();
  });
});

describe("session persistence (sessionStorage ONLY, never localStorage)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("round-trips a secret through sessionStorage", () => {
    saveInstallSecretToSession("s3cr3t");
    expect(loadInstallSecretFromSession()).toBe("s3cr3t");
  });

  it("never writes to localStorage", () => {
    saveInstallSecretToSession("s3cr3t");
    expect(localStorage.getItem(INSTALL_SECRET_SESSION_KEY)).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("returns the empty string when nothing was saved", () => {
    expect(loadInstallSecretFromSession()).toBe("");
  });

  it("degrades to the empty string, never throws, when sessionStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(() => loadInstallSecretFromSession()).not.toThrow();
    expect(loadInstallSecretFromSession()).toBe("");
    spy.mockRestore();
  });

  it("degrades silently, never throws, when sessionStorage.setItem throws (quota/private mode)", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError", "QuotaExceededError");
    });
    expect(() => saveInstallSecretToSession("s3cr3t")).not.toThrow();
    spy.mockRestore();
  });
});
