/**
 * Pure planner for restock schedule horizon top-up.
 * Keep in lock-step with core/restock_horizon.py::plan_horizon_additions.
 */

const WEEKDAY_PY: Record<string, number> = {
  monday: 0,
  tuesday: 1,
  wednesday: 2,
  thursday: 3,
  friday: 4,
  saturday: 5,
  sunday: 6,
};

function addDaysIso(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

/** Match Python date.weekday(): Mon=0 … Sun=6. */
function weekdayOfIso(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  const js = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // Sun=0
  return js === 0 ? 6 : js - 1;
}

/** ISO-week identity via that week's Monday (same grouping as date.isocalendar()[:2]). */
function weekKey(iso: string): string {
  return addDaysIso(iso, -weekdayOfIso(iso));
}

export const AUTOMATION_BY = "restock-horizon";
export const DEFAULT_HORIZON = 2;

/**
 * Live = scheduled dates from the first open day: today, or tomorrow once
 * today's base closing is in (same rule as vw_order_reco_next_dates). Never
 * proposes a date in a week that already has a live date, nor one the
 * operator removed.
 */
export function planHorizonAdditions(opts: {
  todayIso: string;
  weekday: string | null | undefined;
  horizon: number;
  scheduled: string[];
  todayClosed?: boolean;
  removed?: string[];
}): string[] {
  const raw = (opts.weekday ?? "").trim().toLowerCase();
  const want = WEEKDAY_PY[raw];
  if (want === undefined || opts.horizon < 1) return [];

  const today = opts.todayIso.slice(0, 10);
  const start = opts.todayClosed ? addDaysIso(today, 1) : today;
  const live = [
    ...new Set(opts.scheduled.map((d) => d.slice(0, 10)).filter((d) => d >= start)),
  ].sort();
  if (live.length >= opts.horizon) return [];

  const skip = new Set((opts.removed ?? []).map((d) => d.slice(0, 10)));
  const occupied = new Set(live.map(weekKey));
  const need = opts.horizon - live.length;
  const added: string[] = [];
  let cursor = start;
  for (let i = 0; i <= 370 && added.length < need; i++) {
    if (weekdayOfIso(cursor) === want && !skip.has(cursor)) {
      const week = weekKey(cursor);
      if (!occupied.has(week)) {
        added.push(cursor);
        occupied.add(week);
      }
    }
    cursor = addDaysIso(cursor, 1);
  }
  return added;
}

export function parseHorizon(raw: string | null | undefined): number {
  if (raw == null || !String(raw).trim()) return DEFAULT_HORIZON;
  const n = Number.parseInt(String(raw).trim(), 10);
  return Number.isFinite(n) ? n : DEFAULT_HORIZON;
}
