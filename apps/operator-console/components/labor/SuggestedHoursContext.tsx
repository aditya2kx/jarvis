"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

type Ctx = {
  byDay: ReadonlyMap<string, number>;
  setByDay: (m: ReadonlyMap<string, number>) => void;
};

const EMPTY: ReadonlyMap<string, number> = new Map();
const SuggestedHoursCtx = createContext<Ctx>({ byDay: EMPTY, setByDay: () => {} });

/**
 * Shares the coverage panel's suggested (draft) shift hours per day with the
 * hours chart, so both reflect the same rules — including unsaved edits.
 */
export function SuggestedHoursProvider({ children }: { children: ReactNode }) {
  const [byDay, setByDay] = useState<ReadonlyMap<string, number>>(EMPTY);
  return (
    <SuggestedHoursCtx.Provider value={{ byDay, setByDay }}>{children}</SuggestedHoursCtx.Provider>
  );
}

export function useSuggestedHours(): Ctx {
  return useContext(SuggestedHoursCtx);
}
