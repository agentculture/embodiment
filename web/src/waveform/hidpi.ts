// waveform/hidpi.ts
//
// Task t18 round 2 (coordinator's live-Chrome review, item 2): size the
// canvas BACKING STORE from `clientWidth * devicePixelRatio` rather than
// letting the browser stretch a 1:1 backing store over a larger CSS box --
// on a hi-DPI display that blurs every line the oscilloscope draws. The CSS
// size itself stays exactly what the stylesheet already gives `.waveform`
// (`width: 100%; height: 160px` / `220px` above the 640px breakpoint,
// `web/src/styles/app.css`) -- this module never touches `canvas.style`.
//
// Split the same way `model.ts`/`draw.ts` are: the ARITHMETIC (this file,
// pure, no DOM) is what a test pins; wiring it to a real `<canvas>`, a real
// `ResizeObserver` and a real `devicePixelRatio` is `Waveform.tsx`'s job,
// through the same kind of injectable seam `contextFactory`/`raf` already
// use (jsdom has neither a canvas backend NOR `ResizeObserver` at all, so
// every DOM-touching default here has to degrade gracefully under test).

export interface CssSize {
  width: number;
  height: number;
}

export interface BackingSize {
  width: number;
  height: number;
}

/**
 * The canvas backing-store size for a given CSS box size and device pixel
 * ratio. Rounded (a canvas's `width`/`height` are integers; a fractional
 * value truncates silently otherwise) and floored at 1 in each dimension --
 * a canvas with a 0-pixel backing store is a valid DOM state (e.g. a panel
 * not yet laid out, exactly what `clientWidth`/`clientHeight` report under
 * jsdom, which never runs layout at all) and must not throw or divide by
 * zero anywhere downstream.
 */
export function computeBackingSize(cssSize: CssSize, devicePixelRatio: number): BackingSize {
  const dpr = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  const width = Number.isFinite(cssSize.width) ? cssSize.width : 0;
  const height = Number.isFinite(cssSize.height) ? cssSize.height : 0;
  return {
    width: Math.max(1, Math.round(width * dpr)),
    height: Math.max(1, Math.round(height * dpr)),
  };
}

/** The narrow `ResizeObserver` surface `Waveform.tsx` needs -- injectable
 *  (jsdom has no `ResizeObserver` global at all, unlike `canvas`, which at
 *  least exists and merely lacks a 2D backend). */
export interface ResizeObserverLike {
  observe(target: Element): void;
  disconnect(): void;
}

export type ResizeObserverFactory = (callback: () => void) => ResizeObserverLike;

const NOOP_RESIZE_OBSERVER: ResizeObserverLike = {
  observe: () => {},
  disconnect: () => {},
};

/**
 * The default factory: a real `ResizeObserver` when the global exists,
 * otherwise a no-op that never throws and never calls back -- the same
 * "degrade, never raise" contract every other public entry point in this
 * app follows (lesson 3). A caller that needs resize-driven behavior under
 * an environment without `ResizeObserver` (jsdom; some older browsers) has
 * to fall back to the size computed once on mount, which is exactly what
 * happens here: `observe()` is a no-op, so the initial `applySize()` call
 * `Waveform.tsx` makes before ever calling this factory is the only sizing
 * that occurs.
 */
export function defaultResizeObserverFactory(callback: () => void): ResizeObserverLike {
  if (typeof ResizeObserver === "undefined") return NOOP_RESIZE_OBSERVER;
  try {
    return new ResizeObserver(() => callback());
  } catch {
    return NOOP_RESIZE_OBSERVER;
  }
}

/** Reads the live `window.devicePixelRatio`, defaulting to 1 outside a
 *  browser (SSR, a test that didn't inject a value) or when the value
 *  itself is missing/non-finite. */
export function readDevicePixelRatio(): number {
  try {
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio : 1;
    return Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  } catch {
    return 1;
  }
}
