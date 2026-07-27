"use client";

import { useCallback, useSyncExternalStore } from "react";

export const SIDEBAR_STORAGE_KEY = "oc_sidebar_collapsed";
/** Tailwind `lg` breakpoint — auto-collapse below this width when no stored preference. */
export const LG_MIN_PX = 1024;

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** null = no preference stored yet. */
export function readStoredCollapsed(): boolean | null {
  try {
    const raw = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (raw === "1" || raw === "true") return true;
    if (raw === "0" || raw === "false") return false;
    return null;
  } catch {
    return null;
  }
}

export function writeStoredCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // ignore quota / private mode
  }
  emit();
}

export function defaultCollapsedForWidth(widthPx: number): boolean {
  return widthPx < LG_MIN_PX;
}

function resolveCollapsed(): boolean {
  const stored = readStoredCollapsed();
  if (stored !== null) return stored;
  return defaultCollapsedForWidth(window.innerWidth);
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  window.addEventListener("resize", onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("resize", onStoreChange);
  };
}

function getServerSnapshot(): boolean {
  return false;
}

/**
 * Collapsed preference for the md+ icon-rail sidebar.
 * Mirrors ThemeToggle's localStorage key pattern (`oc_theme` → `oc_sidebar_collapsed`).
 * Until the operator toggles, viewport < lg defaults to collapsed.
 */
export function useSidebarCollapsed(): {
  collapsed: boolean;
  setCollapsed: (next: boolean) => void;
  toggle: () => void;
  ready: boolean;
} {
  const collapsed = useSyncExternalStore(
    subscribe,
    resolveCollapsed,
    getServerSnapshot,
  );

  const setCollapsed = useCallback((next: boolean) => {
    writeStoredCollapsed(next);
  }, []);

  const toggle = useCallback(() => {
    writeStoredCollapsed(!resolveCollapsed());
  }, []);

  // Client snapshot always available after hydration via useSyncExternalStore.
  const ready = true;

  return { collapsed, setCollapsed, toggle, ready };
}
