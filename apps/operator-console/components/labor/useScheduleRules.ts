"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";
import { DEFAULT_RULES, type ScheduleRules } from "@/lib/labor/schedule-inputs";

const KEY = "labor.scheduleRules.v1";
const EVENT = "labor-schedule-rules";

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(EVENT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(EVENT, cb);
  };
}

/** Scheduling rules persisted in this browser until they get a server-side store. */
export function useScheduleRules(): [ScheduleRules, (next: ScheduleRules) => void] {
  const raw = useSyncExternalStore(
    subscribe,
    () => window.localStorage.getItem(KEY),
    () => null,
  );
  const rules = useMemo<ScheduleRules>(() => {
    if (!raw) return DEFAULT_RULES;
    try {
      const parsed = JSON.parse(raw) as Partial<ScheduleRules>;
      return {
        dayRules: parsed.dayRules ?? DEFAULT_RULES.dayRules,
        staffRules: parsed.staffRules ?? DEFAULT_RULES.staffRules,
      };
    } catch {
      return DEFAULT_RULES;
    }
  }, [raw]);
  const setRules = useCallback((next: ScheduleRules) => {
    window.localStorage.setItem(KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [rules, setRules];
}
