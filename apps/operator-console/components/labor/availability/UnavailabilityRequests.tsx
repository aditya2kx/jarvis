"use client";

import { useState } from "react";
import { HistoryIcon, PlusIcon, Undo2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { UnavailabilityEventRow, UnavailabilityRow } from "@/lib/bq/queries";
import { changeKey, KIND_LABEL, stagedFor, unstage, type ApplyBlock, type StagedChange } from "@/lib/labor/availability-changes";
import { scheduleConflicts, type ScheduledShift } from "@/lib/labor/unavailability";
import { centralTime, dayLabel, expiryText, whenText } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

/** Amber = waiting on the operator; shared with the pending blocks on the coverage lanes. */
export const REQUEST_COLOR = "#d97706";

const EVENT_LABEL: Record<string, string> = {
  requested: "Requested",
  approved: "Approved",
  denied: "Denied",
  added: "Added",
  edited: "Edited",
  deleted: "Deleted",
  expired: "Expired unanswered",
  removed: "Removed in ADP",
};

/** Pending ADP requests across every day, soonest to expire first, with Approve / Deny staged in place. */
export function UnavailabilityRequests({
  pending,
  upcoming,
  queue,
  canManage,
  lastReadAt,
  onStage,
  onQueue,
  onOpen,
  onAdd,
  onHistory,
}: {
  pending: UnavailabilityRow[];
  /** Scheduled ADP shifts from today on, for the clash line. */
  upcoming: ScheduledShift[];
  queue: StagedChange[];
  canManage: boolean;
  lastReadAt: string | null;
  onStage: (c: StagedChange) => void;
  onQueue: (fn: (q: StagedChange[]) => StagedChange[]) => void;
  /** Show the request's day in the chart. */
  onOpen: (row: UnavailabilityRow) => void;
  onAdd: () => void;
  onHistory: () => void;
}) {
  const actions = canManage ? (
    <span className="flex items-center gap-1">
      <Button size="xs" variant="ghost" onClick={onHistory}>
        <HistoryIcon /> History
      </Button>
      <Button size="xs" variant="ghost" onClick={onAdd}>
        <PlusIcon /> Add unavailability
      </Button>
    </span>
  ) : null;

  if (!pending.length) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground">
        <span>No unavailability requests waiting in ADP · read {centralTime(lastReadAt)}</span>
        {actions}
      </div>
    );
  }

  return (
    <div
      data-testid="unavailability-requests"
      className="flex flex-col gap-2 rounded-lg border px-3 py-3"
      style={{ borderColor: `${REQUEST_COLOR}55`, backgroundColor: `${REQUEST_COLOR}0a` }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="text-sm font-medium text-foreground">Unavailability requests · {pending.length}</h4>
          <p className="text-xs text-muted-foreground">
            Waiting on you in ADP · read {centralTime(lastReadAt)}. Tap a request to see its day below.
          </p>
        </div>
        {actions}
      </div>
      <ul className="flex flex-col divide-y rounded-md border bg-background">
        {pending.map((r) => {
          const exp = expiryText(r);
          const staged = stagedFor(queue, r.row_key);
          const clashes = scheduleConflicts([r], upcoming);
          const summary = `${r.employee} · ${whenText(r)}`;
          return (
            <li
              key={`${r.row_key}-${r.repeat_until ?? ""}`}
              className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 text-sm transition-colors", staged && "bg-primary/5")}
            >
              <button
                type="button"
                onClick={() => onOpen(r)}
                className="flex min-w-0 flex-1 basis-48 flex-col rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="truncate font-medium text-foreground hover:underline">{r.employee}</span>
                <span className="truncate text-xs text-muted-foreground">{whenText(r)}</span>
                {clashes.length ? (
                  <span className="truncate text-xs text-destructive">
                    Clashes with {clashes.length === 1 ? "a scheduled shift" : `${clashes.length} scheduled shifts`} ·{" "}
                    {clashes.map((c) => dayLabel(c.date)).join(", ")}
                  </span>
                ) : null}
              </button>
              {exp ? (
                <span className={cn("text-xs tabular-nums", exp.urgent ? "font-medium text-destructive" : "text-muted-foreground")}>
                  {exp.text}
                </span>
              ) : null}
              {!canManage ? null : staged ? (
                <span className="flex items-center gap-1.5">
                  <Badge variant={staged.kind === "deny" ? "destructive" : "default"}>Will {staged.kind}</Badge>
                  <Button size="icon-xs" variant="ghost" aria-label="Undo" onClick={() => onQueue((q) => unstage(q, changeKey(staged)))}>
                    <Undo2Icon />
                  </Button>
                </span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <Button size="xs" variant="outline" onClick={() => onStage({ kind: "deny", rowKey: r.row_key, employee: r.employee, summary })}>
                    Deny
                  </Button>
                  <Button size="xs" onClick={() => onStage({ kind: "approve", rowKey: r.row_key, employee: r.employee, summary })}>
                    Approve
                  </Button>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** ADP keeps no unavailability history, so this is the console's own append-only log. */
export function UnavailabilityHistorySheet({
  open,
  events,
  onClose,
}: {
  open: boolean;
  events: UnavailabilityEventRow[];
  onClose: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Unavailability history</SheetTitle>
          <SheetDescription>Every change made here, and anything changed in ADP as the next read finds it.</SheetDescription>
        </SheetHeader>
        {events.length ? (
          <ul className="flex flex-col divide-y divide-border px-4 pb-4">
            {events.map((e, i) => (
              <li key={`${e.event_at}-${i}`} className="flex flex-col gap-0.5 py-2.5 text-sm">
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-foreground">{e.employee}</span>
                  <Badge variant={e.event === "denied" || e.event === "deleted" ? "destructive" : e.event === "expired" ? "outline" : "secondary"}>
                    {EVENT_LABEL[e.event] ?? e.event}
                  </Badge>
                </span>
                <span className="text-muted-foreground">{e.summary}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {centralTime(e.event_at)} · {e.source === "console" ? (e.actor ?? "Console") : "Seen in ADP"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mx-4 rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
            No history yet. It starts with the first change applied from here.
          </p>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** Staged changes, applied to ADP in one run; nothing shows as done until ADP is read back. */
export function UnavailabilityApplyTray({
  queue,
  applyBlock,
  onQueue,
}: {
  queue: StagedChange[];
  applyBlock: ApplyBlock;
  onQueue: (fn: (q: StagedChange[]) => StagedChange[]) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!queue.length) return null;
  const blocked =
    applyBlock === "preview"
      ? "Read-only preview: in prod this runs one ADP job for every change below."
      : applyBlock === "off"
        ? "ADP unavailability writes are switched off (CONSOLE_ADP_UNAVAIL_WRITE)."
        : null;
  return (
    <div
      data-testid="availability-apply-tray"
      className="sticky bottom-3 z-20 flex flex-col gap-2 rounded-xl border border-border bg-popover/95 p-3 shadow-lg backdrop-blur max-md:bottom-[calc(4.5rem+env(safe-area-inset-bottom))]"
    >
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="text-sm font-medium hover:underline">
          {queue.length} unavailability change{queue.length === 1 ? "" : "s"} for ADP
        </button>
        <span className="text-xs text-muted-foreground">{open ? "Hide" : "Review"}</span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => onQueue(() => [])}>
            Discard
          </Button>
          <Button size="sm" disabled={blocked != null} title={blocked ?? undefined}>
            Apply to ADP
          </Button>
        </div>
      </div>
      {blocked ? <p className="text-xs text-muted-foreground">{blocked}</p> : null}
      {open ? (
        <ul className="flex flex-col divide-y divide-border border-t border-border">
          {queue.map((c) => (
            <li key={changeKey(c)} className="flex items-center gap-2 py-1.5 text-sm">
              <Badge variant={c.kind === "deny" || c.kind === "delete" ? "destructive" : "outline"} className="w-16 justify-center">
                {KIND_LABEL[c.kind]}
              </Badge>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{c.summary}</span>
              <Button size="icon-xs" variant="ghost" aria-label="Undo" onClick={() => onQueue((q) => unstage(q, changeKey(c)))}>
                <Undo2Icon />
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
