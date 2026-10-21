"use client";

import { useSyncExternalStore } from "react";

// Tailwind `md` is 48rem; below it the shell is in phone layout (MobileTabBar).
const QUERY = "(max-width: 47.999rem)";

function query(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia(QUERY) : null;
}

function subscribe(onChange: () => void): () => void {
  const mql = query();
  mql?.addEventListener("change", onChange);
  return () => mql?.removeEventListener("change", onChange);
}

/** True below md. Server render and first paint assume desktop, so use it for phone-only
 *  layout tweaks (chart ticks and readout, frozen columns), never to hide data. */
export function useIsPhone(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => query()?.matches ?? false,
    () => false,
  );
}
