// Two money shapes coexist in the warehouse (see DOMAIN.md convention note):
// legacy model views (vw_model_labor_daily, vw_model_payroll_period, …) store
// dollars-and-cents floats; new write tables this app owns (recognition_bonuses)
// store integer cents per the EXECUTION.md §5.2 invariant. Never conflate them —
// use the formatter that matches the column's actual unit.
export function formatDollars(dollars: number | null | undefined): string {
  if (dollars == null || Number.isNaN(dollars)) return "—";
  return trueMinus(
    dollars.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }),
  );
}

export function formatCents(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return trueMinus((cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" }));
}

/** Typographic minus (U+2212) matches the width of "+" and reads as a sign, not a dash. */
function trueMinus(s: string): string {
  return s.startsWith("-") ? `\u2212${s.slice(1)}` : s;
}

export function formatPct(fraction: number | null | undefined, digits = 1): string {
  if (fraction == null || Number.isNaN(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatNumber(
  n: number | null | undefined,
  digits = 0,
  options?: { minimumFractionDigits?: number },
): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: options?.minimumFractionDigits ?? 0,
    maximumFractionDigits: digits,
  });
}

/** ADP Time Tracker / Preview hours: always hundredths (47.30, 458.97). */
export function formatHours(hours: number | null | undefined): string {
  return formatNumber(hours, 2, { minimumFractionDigits: 2 });
}

const CHICAGO = "America/Chicago";

// lib/bq/client.ts::q() sanitizes every row before it reaches callers, so by
// the time a date reaches here it's always a plain string/Date — never a
// BigQueryDate class instance.

/** Sortable string key for a BQ date-shaped value. */
export function dateSortKey(value: string | Date | null | undefined): string {
  if (!value) return "";
  return typeof value === "string" ? value : value.toISOString();
}

/** Render a BQ DATE/TIMESTAMP value as America/Chicago — never server-local tz. */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  // BQ DATE columns sanitize to "YYYY-MM-DD". Parse as a calendar date (noon
  // UTC) so America/Chicago formatting does not shift the labeled day back
  // when the string would otherwise be treated as UTC midnight.
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    value = new Date(Date.UTC(y!, m! - 1, d!, 12, 0, 0));
  }
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: CHICAGO,
    month: "short",
    day: "numeric",
  }).format(d);
}

/** BQ TIMESTAMP ("2026-10-20T02:48:52.798661000Z" / "2026-09-27 18:35:40.304467+00") → "Oct 19, 9:48 PM CT". */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = value
    .replace(" ", "T")
    .replace(/\+00$/, "Z")
    .replace(/(\.\d{3})\d+/, "$1");
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return value;
  return (
    new Intl.DateTimeFormat("en-US", {
      timeZone: CHICAGO,
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(d) + " CT"
  );
}
