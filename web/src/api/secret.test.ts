import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  INSTALL_SECRET_SESSION_KEY,
  loadInstallSecretFromSession,
  saveInstallSecretToSession,
} from "./secret";

// Round 4: the cookie path (buildInstallSecretCookie / setInstallSecretCookie)
// is GONE -- see this module's own docstring. The live finding (Tailscale,
// off-loopback/off-https) showed the cookie credential gets refused by t16's
// guard there; the credential now travels only as an Authorization header,
// tested in hooks/useEventStream.test.tsx and api/control.test.ts. This
// file now covers only session persistence.

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

  it("never writes to document.cookie", () => {
    const before = document.cookie;
    saveInstallSecretToSession("s3cr3t");
    expect(document.cookie).toBe(before);
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
