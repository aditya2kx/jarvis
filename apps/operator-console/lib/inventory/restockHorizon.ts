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

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
}

export const AUTOMATION_BY = "restock-horizon";
export const DEFAULT_HORIZON = 2;
export const MAX_HORIZON = 4;
export const CADENCE_WEEKDAYS = Object.keys(WEEKDAY_PY);

/**
 * Live = scheduled dates from the first open day: today, or tomorrow once
 * today's base closing is in (same rule as vw_order_reco_next_dates).
 * Each delivery weekday is a slot; a slot is not added when scheduled,
 * removed by the operator (cancelled, or the source of a Move), or stood in
 * for by an off-weekday live date — the removed slot beside it (a Move), else
 * the nearest slot.
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

  const removedSlots = new Set((opts.removed ?? []).map((d) => d.slice(0, 10)));
  const taken = new Set([...removedSlots, ...live.filter((d) => weekdayOfIso(d) === want)]);
  const moveSources = new Set<string>();
  for (const d of live) {
    if (weekdayOfIso(d) === want) continue;
    const prev = addDaysIso(d, -((weekdayOfIso(d) - want + 7) % 7));
    const next = addDaysIso(prev, 7);
    const [near, far] = daysBetween(prev, d) <= daysBetween(d, next) ? [prev, next] : [next, prev];
    const source = [near, far].find((s) => removedSlots.has(s) && !moveSources.has(s));
    if (source) {
      moveSources.add(source);
      continue;
    }
    const free = [near, far].find((s) => !taken.has(s));
    if (free) taken.add(free);
  }

  const need = opts.horizon - live.length;
  const added: string[] = [];
  let slot = addDaysIso(start, (want - weekdayOfIso(start) + 7) % 7);
  for (let i = 0; i < 53 && added.length < need; i++) {
    if (!taken.has(slot)) added.push(slot);
    slot = addDaysIso(slot, 7);
  }
  return added;
}

export function parseHorizon(raw: string | null | undefined): number {
  const s = String(raw ?? "").trim();
  return /^[+-]?\d+$/.test(s) ? Number(s) : DEFAULT_HORIZON;
}
