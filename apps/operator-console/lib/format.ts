// Two money shapes coexist in the warehouse (see DOMAIN.md convention note):
// legacy model views (vw_model_labor_daily, vw_model_payroll_period, …) store
// dollars-and-cents floats; new write tables this app owns (recognition_bonuses)
// store integer cents per the EXECUTION.md §5.2 invariant. Never conflate them —
// use the formatter that matches the column's actual unit.
export function formatDollars(dollars: number | null | undefined): string {
  if (dollars == null || Number.isNaN(dollars)) return "—";
  return dollars.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

export function formatCents(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return (cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

export function formatPct(fraction: number | null | undefined, digits = 1): string {
  if (fraction == null || Number.isNaN(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatNumber(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
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
