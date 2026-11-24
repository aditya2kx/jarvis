"use client";

import { useEffect } from "react";

/** Gaps larger than this are something else (an on-screen keyboard, split view), not the bug. */
const MAX_GAP_PX = 120;

/**
 * iOS 26 home-screen web apps report a viewport shorter than the screen by the status-bar
 * inset, so `100dvh` and `bottom: 0` stop that far above the bottom edge. Publishes the
 * shortfall as `--vp-gap` (0 everywhere else) for the shell, tab bar, sheets and toasts.
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
      const gap = screenH - window.innerHeight;
      root.style.setProperty("--vp-gap", `${gap > 0 && gap <= MAX_GAP_PX ? gap : 0}px`);
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);
  return null;
}
