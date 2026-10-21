"use client";

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { ALL_ITEMS, DEFAULT_TAB_HREFS, activeNavItem, type NavItem } from "./nav-items";

export interface TabSlot {
  href: string;
  usedAt: number;
}

const KEY = "operator-console.recent-tabs.v1";
const listeners = new Set<() => void>();

/** Defaults rank Home highest, so the first new page replaces Payroll, then Labor, … */
export const DEFAULT_SLOTS: TabSlot[] = DEFAULT_TAB_HREFS.map((href, i) => ({ href, usedAt: -(i + 1) }));

/**
 * Most-recently-used pages, with stable positions: revisiting a page in the bar only
 * refreshes its recency, and a page not in the bar takes over the least recently used
 * slot in place — so the tabs a thumb has learned don't shuffle on every tap.
 */
export function recordVisit(slots: TabSlot[], href: string, now: number): TabSlot[] {
  const hit = slots.findIndex((s) => s.href === href);
  if (hit >= 0) return slots.map((s, i) => (i === hit ? { href, usedAt: now } : s));
  let lru = 0;
  slots.forEach((s, i) => {
    if (s.usedAt < slots[lru]!.usedAt) lru = i;
  });
  return slots.map((s, i) => (i === lru ? { href, usedAt: now } : s));
}

export function parseSlots(raw: string | null): TabSlot[] {
  if (!raw) return DEFAULT_SLOTS;
  try {
    const v: unknown = JSON.parse(raw);
    if (!Array.isArray(v) || v.length !== DEFAULT_SLOTS.length) return DEFAULT_SLOTS;
    const slots = v.filter(
      (s): s is TabSlot =>
        typeof s?.href === "string" && typeof s?.usedAt === "number" && ALL_ITEMS.some((i) => i.href === s.href),
    );
    const unique = new Set(slots.map((s) => s.href)).size === slots.length;
    return slots.length === DEFAULT_SLOTS.length && unique ? slots : DEFAULT_SLOTS;
  } catch {
    return DEFAULT_SLOTS;
  }
}

function read(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Tab bar items for this device, updated as `pathname` changes. Server render = defaults. */
export function useRecentTabs(pathname: string | null): NavItem[] {
  const raw = useSyncExternalStore(subscribe, read, () => "");
  const slots = useMemo(() => parseSlots(raw), [raw]);

  const visit = useCallback((href: string) => {
    const next = JSON.stringify(recordVisit(parseSlots(read()), href, Date.now()));
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      return;
    }
    listeners.forEach((l) => l());
  }, []);

  const current = activeNavItem(pathname)?.href;
  useEffect(() => {
    if (current) visit(current);
  }, [current, visit]);

  return slots.map((s) => ALL_ITEMS.find((i) => i.href === s.href)!);
}
