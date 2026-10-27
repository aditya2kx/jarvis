"use client";

import { useMemo, useState } from "react";
import { PlusIcon, Undo2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import { EntrySheet, type SheetTarget } from "@/components/labor/availability/EntrySheet";
import { TeamWeekGrid } from "@/components/labor/availability/TeamWeekGrid";
import type { UnavailabilityEventRow, UnavailabilityRow } from "@/lib/bq/queries";
import type { LocalMultiSelection } from "@/lib/tables/localMultiFilter";
import { addDays, mondayOf } from "@/lib/labor/availability-week";
import { changeKey, KIND_LABEL, stage, stagedFor, unstage, type StagedChange } from "@/lib/labor/availability-changes";
import { approvedByPerson, collapseWeekly, scheduleConflicts, type ScheduledShift } from "@/lib/labor/unavailability";
import { centralTime, dayLabel, expiryText, initials, lineText, shortDate, whenText } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

type View = "requests" | "week" | "people" | "history";

/** Why Apply is unavailable, or null when it can run. */
export type ApplyBlock = "preview" | "off" | null;

const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");
const ROW = "flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5 text-sm transition-colors hover:bg-muted/40";

function Avatar({ name }: { name: string }) {
  return (
    <span aria-hidden className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground">
      {initials(name)}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">{children}</p>;
}

function Stat({ value, label, tone }: { value: string | number; label: string; tone?: "warn" | "danger" }) {
  return (
    <div className="flex min-w-28 flex-col gap-0.5 rounded-lg border border-border px-3 py-2">
      <span className={cn("text-lg font-semibold tabular-nums", tone === "danger" && Number(value) > 0 && "text-destructive", tone === "warn" && Number(value) > 0 && "text-amber-600 dark:text-amber-400")}>
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

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

export function AvailabilityWorkspace({
  rows,
  shifts,
  roster,
  events,
  lastReadAt,
  todayIso,
  applyBlock,
}: {
  /** ADP pending + approved rows, plus "Unavailable on dates" scheduling-rule rows (row_key "rule|…"). */
  rows: UnavailabilityRow[];
  /** Upcoming ADP scheduled shifts — clashes and the week grid. */
  shifts: ScheduledShift[];
  /** Current team: drives the grid rows, the People filter and "Not marked". */
  roster: string[];
  events: UnavailabilityEventRow[];
  lastReadAt: string | null;
  todayIso: string;
  applyBlock: ApplyBlock;
}) {
  const pending = useMemo(
    () =>
      collapseWeekly(rows.filter((r) => r.status === "pending")).sort(
        (a, b) => (a.hours_left ?? 1e9) - (b.hours_left ?? 1e9) || a.first_date.localeCompare(b.first_date),
      ),
    [rows],
  );
  const people = useMemo(() => approvedByPerson(rows, todayIso), [rows, todayIso]);
  const marked = new Set([...pending.map((r) => r.employee), ...people.map((p) => p.employee)]);
  const everyone = [...new Set([...roster, ...marked])].sort((a, b) => a.localeCompare(b));
  const unmarked = everyone.filter((n) => !marked.has(n));
  const upcoming = shifts.filter((s) => s.date >= todayIso);
  const clashes = scheduleConflicts(rows.filter((r) => !fromRule(r)), upcoming);
  const urgent = pending.filter((r) => (r.hours_left ?? 1e9) < 48).length;

  const [view, setView] = useState<View>(pending.length ? "requests" : "week");
  const [selected, setSelected] = useState<LocalMultiSelection>(null);
  const [monday, setMonday] = useState(() => mondayOf(todayIso));
  const [queue, setQueue] = useState<StagedChange[]>([]);
  const [target, setTarget] = useState<SheetTarget | null>(null);
  const picked = (name: string) => selected == null || selected.includes(name);

  const readThrough = rows.reduce((m, r) => (r.status === "approved" && r.first_date > m ? r.first_date : m), "");
  const lastWeekFrom = readThrough ? addDays(readThrough, -6) : "";
  const nextWeek = addDays(todayIso, 7);
  const onStage = (c: StagedChange) => setQueue((q) => stage(q, c));

  const views: [View, string][] = [
    ["requests", `Requests · ${pending.length}`],
    ["week", "Team week"],
    ["people", "By person"],
    ["history", "History"],
  ];

  return (
    <div className="flex flex-col gap-4" data-testid="availability-workspace">
      <div className="flex flex-wrap gap-2">
        <Stat value={pending.length} label="Need a decision" tone="warn" />
        <Stat value={urgent} label="Expire within 48h" tone="danger" />
        <Stat value={clashes.length} label="Clash with a shift" tone="danger" />
        <div className="flex min-w-28 flex-col justify-center gap-0.5 px-1 py-2">
          <span className="text-xs text-muted-foreground">Last ADP read</span>
          <span className="text-sm font-medium tabular-nums">{centralTime(lastReadAt)}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex max-w-full items-center gap-1 overflow-x-auto rounded-lg border border-border p-0.5 [scrollbar-width:none]" role="tablist" aria-label="Availability view">
          {views.map(([v, label]) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={cn(
                "h-7 rounded-md px-2.5 text-xs font-medium whitespace-nowrap tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:h-9",
                view === v ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <LocalMultiSelect label="People" selected={selected} options={everyone} onChange={setSelected} />
          <Button size="sm" variant="outline" onClick={() => setTarget({ mode: "add", employee: null, date: todayIso })}>
            <PlusIcon /> Add unavailability
          </Button>
        </div>
      </div>

      {view === "requests" ? (
        pending.filter((r) => picked(r.employee)).length ? (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {pending
              .filter((r) => picked(r.employee))
              .map((r) => {
                const exp = expiryText(r);
                const staged = stagedFor(queue, r.row_key);
                const text = whenText(r);
                const rowClashes = scheduleConflicts([r], upcoming);
                const summary = `${r.employee} · ${text}`;
                return (
                  <li key={`${r.row_key}-${r.repeat_until ?? ""}`} className={cn(ROW, staged && "bg-primary/[0.03]")}>
                    <Avatar name={r.employee} />
                    <button type="button" onClick={() => setTarget({ mode: "entry", row: r, date: r.first_date })} className="flex min-w-0 flex-1 flex-col text-left">
                      <span className="truncate font-medium text-foreground">{r.employee}</span>
                      <span className="truncate text-muted-foreground">{text}</span>
                      {rowClashes.length ? (
                        <span className="truncate text-xs text-destructive">
                          Clashes with {rowClashes.length === 1 ? "a scheduled shift" : `${rowClashes.length} scheduled shifts`} · {rowClashes.map((c) => dayLabel(c.date)).join(", ")}
                        </span>
                      ) : null}
                    </button>
                    {exp ? <span className={cn("text-xs tabular-nums", exp.urgent ? "font-medium text-destructive" : "text-muted-foreground")}>{exp.text}</span> : null}
                    {staged ? (
                      <span className="flex items-center gap-1.5">
                        <Badge variant={staged.kind === "deny" ? "destructive" : "default"}>Will {staged.kind}</Badge>
                        <Button size="icon-xs" variant="ghost" aria-label="Undo" onClick={() => setQueue((q) => unstage(q, changeKey(staged)))}>
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
        ) : (
          <Empty>Nothing waiting for a decision.</Empty>
        )
      ) : null}

      {view === "week" ? (
        <TeamWeekGrid
          rows={rows}
          shifts={shifts}
          people={everyone.filter(picked)}
          monday={monday}
          todayIso={todayIso}
          queue={queue}
          onWeek={setMonday}
          onOpenEntry={(row, date) => setTarget({ mode: "entry", row, date })}
          onAdd={(employee, date) => setTarget({ mode: "add", employee, date })}
        />
      ) : null}

      {view === "people" ? (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {people
            .filter((p) => picked(p.employee))
            .map((p) => (
              <li key={p.employee} className={cn(ROW, "items-start")}>
                <Avatar name={p.employee} />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate font-medium text-foreground">{p.employee}</span>
                  {p.lines.map((l) => (
                    <button
                      key={`${l.weekdays.join("")}-${l.date}-${l.from_time}`}
                      type="button"
                      onClick={() => setTarget({ mode: "entry", row: l.rows[0]!, date: l.date })}
                      className="w-fit text-left text-muted-foreground tabular-nums hover:text-foreground hover:underline"
                    >
                      {lineText(l, lastWeekFrom, nextWeek)}
                      {l.rows.every(fromRule) ? " · scheduling rule" : ""}
                    </button>
                  ))}
                </span>
                {p.lines.every((l) => l.rows.every(fromRule)) ? <Badge variant="outline">Scheduling rule</Badge> : <Badge variant="secondary">Approved</Badge>}
              </li>
            ))}
          {unmarked.filter(picked).map((name) => (
            <li key={`none-${name}`} className={ROW}>
              <Avatar name={name} />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-medium text-foreground">{name}</span>
                <span className="truncate text-muted-foreground">Available any time · nothing marked in ADP</span>
              </span>
              <Button size="xs" variant="ghost" onClick={() => setTarget({ mode: "add", employee: name, date: todayIso })}>
                <PlusIcon /> Add
              </Button>
            </li>
          ))}
        </ul>
      ) : null}

      {view === "history" ? (
        events.filter((e) => picked(e.employee)).length ? (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {events
              .filter((e) => picked(e.employee))
              .map((e, i) => (
                <li key={`${e.event_at}-${i}`} className={ROW}>
                  <span className="w-32 shrink-0 text-xs text-muted-foreground tabular-nums">{centralTime(e.event_at)}</span>
                  <Avatar name={e.employee} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-foreground">{e.employee}</span>
                    <span className="truncate text-muted-foreground">{e.summary}</span>
                  </span>
                  <Badge variant={e.event === "denied" || e.event === "deleted" ? "destructive" : e.event === "expired" ? "outline" : "secondary"}>
                    {EVENT_LABEL[e.event] ?? e.event}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{e.source === "console" ? `${e.actor ?? "Console"}` : "Seen in ADP"}</span>
                </li>
              ))}
          </ul>
        ) : (
          <Empty>
            No history yet. ADP keeps none of its own, so the console records every change from here on: yours as you apply them, and
            anything changed in ADP as the next read finds it.
          </Empty>
        )
      ) : null}

      <p className="text-xs text-muted-foreground">
        ADP is the source of truth: changes are staged here, applied in ADP in one run, and only shown as done once a fresh read of ADP
        confirms them. Last read {centralTime(lastReadAt)}
        {readThrough ? ` through ${shortDate(readThrough)}` : ""}.
      </p>

      <ApplyTray queue={queue} applyBlock={applyBlock} onUndo={(k) => setQueue((q) => unstage(q, k))} onDiscard={() => setQueue([])} />

      <EntrySheet target={target} roster={everyone} onClose={() => setTarget(null)} onStage={onStage} />
    </div>
  );
}

function ApplyTray({
  queue,
  applyBlock,
  onUndo,
  onDiscard,
}: {
  queue: StagedChange[];
  applyBlock: ApplyBlock;
  onUndo: (key: string) => void;
  onDiscard: () => void;
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
          {queue.length} change{queue.length === 1 ? "" : "s"} ready for ADP
        </button>
        <span className="text-xs text-muted-foreground">{open ? "Hide" : "Review"}</span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={onDiscard}>
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
              <Button size="icon-xs" variant="ghost" aria-label="Undo" onClick={() => onUndo(changeKey(c))}>
                <Undo2Icon />
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
