// hooks/useVisualViewport.ts
//
// The app follows the visual viewport: --app-h and --app-top (styles/app.css .app) come
// from window.visualViewport, so on a phone the dock rides above the keyboard (iOS keeps
// the layout viewport when the keyboard opens and shrinks only the visual one; operator's
// phone, 2026-10-24).
//
// d5 (operator's phone, 2026-10-26): after a refresh the header was scrolled away and the
// dock floated above empty space. A refresh can take its first reading before the
// browser's toolbars settle, with no viewport event after, and a restored scroll offset
// moves the fixed app off the screen. So the viewport is read again after load, on
// pageshow, orientation change and window resize, and a few times in the first second;
// the top never goes above the screen; and the document, which never needs to scroll,
// is kept at the top unless a field has focus (then the keyboard owns the viewport).

import { useEffect } from "react";

/** When after mount the viewport is read again, in ms: the toolbars settle within ~1 s. */
const SETTLE_MS = [100, 400, 1000];

function typing(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el instanceof HTMLElement && el.isContentEditable);
}

export function useVisualViewport(): void {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const root = document.documentElement;
    try {
      history.scrollRestoration = "manual";
    } catch {
      /* not settable here */
    }
    const sync = () => {
      if (!typing() && window.scrollY > 0) window.scrollTo(0, 0);
      if (!vv) return;
      root.style.setProperty("--app-h", `${Math.round(vv.height)}px`);
      root.style.setProperty("--app-top", `${Math.max(0, Math.round(vv.offsetTop))}px`);
    };
    sync();
    const timers = SETTLE_MS.map((ms) => window.setTimeout(sync, ms));
    const windowEvents = ["pageshow", "load", "orientationchange", "resize"];
    for (const name of windowEvents) window.addEventListener(name, sync);
    vv?.addEventListener("resize", sync);
    vv?.addEventListener("scroll", sync);
    return () => {
      timers.forEach((t) => window.clearTimeout(t));
      for (const name of windowEvents) window.removeEventListener(name, sync);
      vv?.removeEventListener("resize", sync);
      vv?.removeEventListener("scroll", sync);
    };
  }, []);
}
