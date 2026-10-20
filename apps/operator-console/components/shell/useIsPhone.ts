"use client";

import { useSyncExternalStore } from "react";

// Tailwind `md` is 48rem; below it the shell is in phone layout (MobileTabBar).
const QUERY = "(max-width: 47.999rem)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

/** True below md. Server render and first paint assume desktop, so use it for density
 *  tweaks (chart ticks), never to decide whether content exists. */
export function useIsPhone(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
