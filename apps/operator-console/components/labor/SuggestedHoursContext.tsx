"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Ctx = {
  /** Draft shift hours per day. */
  byDay: ReadonlyMap<string, number>;
  /** Draft people added to the floor per day (hours ÷ staffed hours). */
  concurrentByDay: ReadonlyMap<string, number>;
  /** Draft hours per day per suggested person ("" = unassigned). */
  byDayPerson: ReadonlyMap<string, ReadonlyMap<string, number>>;
  publish: (
    hours: ReadonlyMap<string, number>,
    concurrent: ReadonlyMap<string, number>,
    perPerson: ReadonlyMap<string, ReadonlyMap<string, number>>,
  ) => void;
};

const EMPTY: ReadonlyMap<string, number> = new Map();
const EMPTY_PP: ReadonlyMap<string, ReadonlyMap<string, number>> = new Map();
const SuggestedHoursCtx = createContext<Ctx>({
  byDay: EMPTY,
  concurrentByDay: EMPTY,
  byDayPerson: EMPTY_PP,
  publish: () => {},
});

/**
 * Shares the coverage panel's suggested (draft) shifts per day with the hours
 * and concurrent charts, so all reflect the same rules — including unsaved edits.
 */
export function SuggestedHoursProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState({ byDay: EMPTY, concurrentByDay: EMPTY, byDayPerson: EMPTY_PP });
  const publish = useCallback<Ctx["publish"]>(
    (byDay, concurrentByDay, byDayPerson) => setState({ byDay, concurrentByDay, byDayPerson }),
    [],
  );
  return <SuggestedHoursCtx.Provider value={{ ...state, publish }}>{children}</SuggestedHoursCtx.Provider>;
}

export function useSuggestedHours(): Ctx {
  return useContext(SuggestedHoursCtx);
}
