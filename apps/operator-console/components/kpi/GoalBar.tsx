import { cn } from "@/lib/utils";
import type { GoalStatus } from "@/lib/kpi/health";

const STATUS_COLOR: Record<GoalStatus, string> = {
  "on-track": "bg-emerald-500",
  "at-risk": "bg-amber-500",
  "off-track": "bg-red-500",
  "no-goal": "bg-muted-foreground/30",
};

export function GoalBar({ status, pace }: { status: GoalStatus; pace: number | null }) {
  const width = pace == null ? 0 : Math.min(100, Math.max(0, pace * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className={cn("h-full rounded-full transition-all", STATUS_COLOR[status])}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}
