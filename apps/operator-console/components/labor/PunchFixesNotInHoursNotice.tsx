import type { PunchFixNotInHoursRow } from "@/lib/bq/queries";
import { formatDate } from "@/lib/format";
import { decisionTimes } from "@/lib/labor/punch-gaps";

/**
 * Notice copy, split out so it can be tested without rendering. Null when every
 * punch fix written to ADP is already in hours.
 */
export function punchFixesNotInHoursMessage(
  rows: PunchFixNotInHoursRow[],
): { headline: string; items: string[] } | null {
  if (!rows.length) return null;
  const n = rows.length;
  return {
    headline:
      `${n} punch ${n === 1 ? "fix" : "fixes"} saved in ADP ${n === 1 ? "is" : "are"} ` +
      `not in hours yet — hours, labor and payroll exclude ${n === 1 ? "it" : "them"} ` +
      `until Sync ADP pulls ${n === 1 ? "it" : "them"} in.`,
    items: rows.map(
      (r) =>
        `${r.employee} · ${formatDate(r.date)} · ${decisionTimes({
          inTime: r.in_time,
          outTime: r.out_time,
        })}`,
    ),
  };
}

/**
 * Shown on /labor and /payroll (Issue #358): a punch fix the Punches panel calls
 * written must not silently be missing from the hours both pages total.
 */
export function PunchFixesNotInHoursNotice({ rows }: { rows: PunchFixNotInHoursRow[] }) {
  const msg = punchFixesNotInHoursMessage(rows);
  if (!msg) return null;
  return (
    <div
      role="status"
      data-testid="punch-fixes-not-in-hours"
      className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-200"
    >
      <p className="font-medium">{msg.headline}</p>
      <ul className="mt-1 list-disc pl-5 opacity-90">
        {msg.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
