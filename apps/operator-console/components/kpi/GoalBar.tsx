import { cn } from "@/lib/utils";
import type { GoalStatus } from "@/lib/kpi/health";

const STATUS_COLOR: Record<GoalStatus, string> = {
  "on-track": "bg-emerald-500",
  "at-risk": "bg-amber-500",
  "off-track": "bg-red-500",
  "no-goal": "bg-muted-foreground/30",
};

/**
 * Fill = actual on a 0…max(actual,goal) scale; vertical tick = goal.
 * Falls back to pace-only fill when actual/goal are missing.
 */
export function GoalBar({
  status,
  pace,
  actual,
  goal,
}: {
  status: GoalStatus;
  pace: number | null;
  actual?: number | null;
  goal?: number | null;
}) {
  const hasScale =
    actual != null && goal != null && (Math.abs(actual) > 0 || Math.abs(goal) > 0);
  let fillPct: number;
  let goalPct: number | null = null;
  if (hasScale) {
    const max = Math.max(Math.abs(actual!), Math.abs(goal!), 1e-9);
    fillPct = Math.min(100, (Math.abs(actual!) / max) * 100);
    goalPct = Math.min(100, (Math.abs(goal!) / max) * 100);
  } else {
    fillPct = pace == null ? 0 : Math.min(100, Math.max(0, pace * 100));
  }

  return (
    <div className="relative h-1.5 w-full overflow-visible rounded-full bg-muted">
      <div className="absolute inset-0 overflow-hidden rounded-full">
        <div
          className={cn("h-full rounded-full transition-all", STATUS_COLOR[status])}
          style={{ width: `${fillPct}%` }}
        />
      </div>
      {goalPct != null ? (
        <div
          aria-hidden
          title="Goal"
          className="absolute top-1/2 z-10 h-3.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-background bg-foreground shadow-sm"
          style={{ left: `${goalPct}%` }}
        />
      ) : null}
    </div>
  );
}
