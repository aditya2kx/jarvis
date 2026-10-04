"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import type { OpenShift } from "@/lib/labor/open-shifts-insight";

type Ctx = {
  /** Draft shift hours per day. */
  byDay: ReadonlyMap<string, number>;
  /** Draft people added to the floor per day (hours ÷ staffed hours). */
  concurrentByDay: ReadonlyMap<string, number>;
  /** Draft hours per day per suggested person ("" = unassigned). */
  byDayPerson: ReadonlyMap<string, ReadonlyMap<string, number>>;
  /** Upcoming shifts nobody is on: ADP open shifts + unassigned drafts. */
  openShifts: readonly OpenShift[];
  /** Weekly hours cap per person (staff rules), for the hiring estimate. */
  capHours: number;
  publishOpen: (openShifts: readonly OpenShift[], capHours: number) => void;
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
  openShifts: [],
  capHours: 40,
  publishOpen: () => {},
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
  const [open, setOpen] = useState<{ openShifts: readonly OpenShift[]; capHours: number }>({
    openShifts: [],
    capHours: 40,
  });
  const publishOpen = useCallback<Ctx["publishOpen"]>(
    (openShifts, capHours) => setOpen({ openShifts, capHours }),
    [],
  );
  return (
    <SuggestedHoursCtx.Provider value={{ ...state, ...open, publish, publishOpen }}>{children}</SuggestedHoursCtx.Provider>
  );
}

export function useSuggestedHours(): Ctx {
  return useContext(SuggestedHoursCtx);
}
