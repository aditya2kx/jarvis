"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Ctx = {
  /** Draft shift hours per day. */
  byDay: ReadonlyMap<string, number>;
  /** Draft people added to the floor per day (hours ÷ staffed hours). */
  concurrentByDay: ReadonlyMap<string, number>;
  publish: (hours: ReadonlyMap<string, number>, concurrent: ReadonlyMap<string, number>) => void;
};

const EMPTY: ReadonlyMap<string, number> = new Map();
const SuggestedHoursCtx = createContext<Ctx>({ byDay: EMPTY, concurrentByDay: EMPTY, publish: () => {} });

/**
 * Shares the coverage panel's suggested (draft) shifts per day with the hours
 * and concurrent charts, so all reflect the same rules — including unsaved edits.
 */
export function SuggestedHoursProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState({ byDay: EMPTY, concurrentByDay: EMPTY });
  const publish = useCallback(
    (byDay: ReadonlyMap<string, number>, concurrentByDay: ReadonlyMap<string, number>) =>
      setState({ byDay, concurrentByDay }),
    [],
  );
  return <SuggestedHoursCtx.Provider value={{ ...state, publish }}>{children}</SuggestedHoursCtx.Provider>;
}

export function useSuggestedHours(): Ctx {
  return useContext(SuggestedHoursCtx);
}
