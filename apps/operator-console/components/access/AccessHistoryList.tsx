import { formatDateTime } from "@/lib/format";
import { grantSummary, type GrantVersion } from "@/lib/access/policy";

/** Append-only change log: every save is kept, newest first. */
export function AccessHistoryList({ entries, showEmail = true }: { entries: GrantVersion[]; showEmail?: boolean }) {
  return (
    <ol className="flex flex-col divide-y rounded-lg border" data-testid="access-history">
      {entries.map((h) => (
        <li key={`${h.email}-${h.version}`} className="flex flex-col gap-0.5 px-3 py-2 text-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="min-w-0 truncate font-medium">
              {showEmail ? h.email : h.status === "revoked" ? "Access removed" : `Version ${h.version}`}
            </span>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatDateTime(h.createdAt)}</span>
          </div>
          <span className="text-xs text-muted-foreground">
            {h.status === "revoked" ? "No access" : grantSummary(h.grant)} · by {h.createdBy}
            {h.note ? ` · “${h.note}”` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
