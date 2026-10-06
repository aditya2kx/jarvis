// Award-month math for Monthly recognition (Issue #369). Pure, America/Chicago
// calendar dates passed in as YYYY-MM-DD strings.

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Default recognition month for a payroll cycle: the month before period_end's month. */
export function awardMonthForPeriod(periodEndIso: string): string {
  const [y, m] = periodEndIso.split("-").map(Number);
  const prevY = m === 1 ? y - 1 : y;
  const prevM = m === 1 ? 12 : m - 1;
  return `${prevY}-${String(prevM).padStart(2, "0")}`;
}

export function isAwardMonth(value: string | undefined | null): value is string {
  return Boolean(value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value));
}

/** "2026-09" → "September". */
export function monthName(awardMonth: string): string {
  return MONTH_NAMES[Number(awardMonth.slice(5, 7)) - 1] ?? awardMonth;
}

/** "2026-09" → "September 2026". */
export function monthLabel(awardMonth: string): string {
  return `${monthName(awardMonth)} ${awardMonth.slice(0, 4)}`;
}

/** First and last calendar day of an award month (YYYY-MM-DD). */
export function monthBounds(awardMonth: string): { start: string; end: string } {
  const [y, m] = awardMonth.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return {
    start: `${awardMonth}-01`,
    end: `${awardMonth}-${String(last).padStart(2, "0")}`,
  };
}

/** Recent award months (newest first) ending at `latest`, for the month override select. */
export function recentAwardMonths(latest: string, count = 6): string[] {
  const out: string[] = [];
  let [y, m] = latest.split("-").map(Number);
  for (let i = 0; i < count; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m -= 1;
    if (m === 0) {
      m = 12;
      y -= 1;
    }
  }
  return out;
}
