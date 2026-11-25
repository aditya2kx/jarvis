"use client";

import { useEffect } from "react";

/** Gaps larger than this are something else (an on-screen keyboard, split view), not the bug. */
const MAX_GAP_PX = 120;

const clamp = (gap: number) => (gap > 0.5 && gap <= MAX_GAP_PX ? Math.round(gap) : 0);

function probe(style: Partial<CSSStyleDeclaration>): DOMRect {
  const el = document.createElement("div");
  Object.assign(el.style, { position: "fixed", left: "0", width: "0", visibility: "hidden", pointerEvents: "none" }, style);
  document.body.appendChild(el);
  const rect = el.getBoundingClientRect();
  el.remove();
  return rect;
}

/**
 * iOS 26 home-screen web apps sometimes lay the page out shorter than the screen by the
 * status-bar inset, so `100dvh` and `bottom: 0` stop above the bottom edge — and sometimes
 * not, in the same app. `innerHeight` doesn't say which, so this measures where those two
 * actually end and publishes each shortfall: `--vp-gap` for fixed `bottom: 0` (tab bar,
 * sheets, toasts) and `--dvh-gap` for `100dvh` (the shell). Both are 0 outside the app.
 */
export function ViewportGap() {
  useEffect(() => {
    const standalone =
      (navigator as Navigator & { standalone?: boolean }).standalone === true ||
      window.matchMedia("(display-mode: standalone)").matches;
    if (!standalone) return;
    const root = document.documentElement;
    const update = () => {
      // iOS keeps screen.width/height in portrait terms whatever the orientation.
      const landscape = window.matchMedia("(orientation: landscape)").matches;
      const screenH = landscape ? Math.min(screen.width, screen.height) : Math.max(screen.width, screen.height);
      const fixedBottom = probe({ bottom: "0", height: "0" }).top;
      const dvh = probe({ top: "0", height: "100dvh" }).height;
      root.style.setProperty("--vp-gap", `${clamp(screenH - fixedBottom)}px`);
      root.style.setProperty("--dvh-gap", `${clamp(screenH - dvh)}px`);
    };
    update();
    // iOS can settle the viewport after load without a resize event.
    const timers = [300, 1000, 2500].map((ms) => window.setTimeout(update, ms));
    const events = ["resize", "orientationchange", "pageshow"] as const;
    events.forEach((e) => window.addEventListener(e, update));
    document.addEventListener("visibilitychange", update);
    return () => {
      timers.forEach(clearTimeout);
      events.forEach((e) => window.removeEventListener(e, update));
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return null;
}
