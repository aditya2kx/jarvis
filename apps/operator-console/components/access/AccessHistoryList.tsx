import { formatDateTime } from "@/lib/format";
import type { GrantVersion } from "@/lib/access/policy";
import { PageChips, PersonAvatar, relativeTime } from "./visuals";

/** Append-only change log as a timeline: every save is kept, newest first. */
export function AccessHistoryList({ entries, showEmail = true }: { entries: GrantVersion[]; showEmail?: boolean }) {
  return (
    <ol className="relative flex flex-col" data-testid="access-history">
      {entries.map((h, i) => (
        <li key={`${h.email}-${h.version}`} className="relative flex gap-3 pb-4 last:pb-0">
          {i < entries.length - 1 ? (
            <span aria-hidden className="absolute top-8 bottom-0 left-[0.875rem] w-px bg-border" />
          ) : null}
          {showEmail ? (
            <PersonAvatar email={h.email} size="sm" muted={h.status === "revoked"} />
          ) : (
            <span aria-hidden className="mt-1.5 ml-2.5 size-2 shrink-0 rounded-full bg-border ring-4 ring-popover" />
          )}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="min-w-0 truncate text-sm font-medium">
                {showEmail ? h.email : `Version ${h.version}`}
              </span>
              <time
                dateTime={h.createdAt}
                title={formatDateTime(h.createdAt)}
                className="shrink-0 text-xs tabular-nums text-muted-foreground"
                suppressHydrationWarning
              >
                {relativeTime(h.createdAt)}
              </time>
            </div>
            {h.status === "revoked" ? (
              <span className="text-xs font-medium text-destructive">Access removed</span>
            ) : (
              <PageChips grant={h.grant} max={3} className="text-xs" />
            )}
            <span className="text-xs text-muted-foreground">
              by {h.createdBy.split("@")[0]}
              {h.note ? ` · “${h.note}”` : ""}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}
