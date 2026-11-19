"use client";

import { Badge } from "@/components/ui/badge";
import {
  isJobLive,
  isJobStalled,
  jobPercent,
  jobProgressLabel,
  type CategorizeJob,
} from "@/lib/plaid/categorize-job-status";
import { cn } from "@/lib/utils";

/** Reapply / Revert progress: state badge, "N of M rows written", and a thin bar. */
export function CategorizeJobStatus({ job, className }: { job: CategorizeJob; className?: string }) {
  const live = isJobLive(job);
  const stalled = isJobStalled(job);
  const pct = jobPercent(job);
  const failed = job.status === "failed" || stalled;
  return (
    <div className={cn("flex flex-col gap-1.5", className)} role="status" aria-live="polite" data-testid="categorize-job-status">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant={failed ? "destructive" : live ? "secondary" : "outline"}>
          {live ? "Running" : failed ? (stalled ? "Stopped" : "Failed") : "Done"}
        </Badge>
        <span className={failed ? "text-destructive" : "text-muted-foreground"}>
          {jobProgressLabel(job)}
        </span>
      </div>
      {live ? (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
          <div
            className={cn(
              "h-full rounded-full bg-primary transition-[width] duration-500",
              pct == null && "w-1/3 animate-pulse",
            )}
            style={pct == null ? undefined : { width: `${Math.max(pct, 2)}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
