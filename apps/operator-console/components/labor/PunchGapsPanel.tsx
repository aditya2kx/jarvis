"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  Loader2Icon,
  ChevronsUpDownIcon,
  PencilIcon,
  RotateCcwIcon,
  XIcon,
} from "lucide-react";
import {
  acceptPunchGapsAction,
  decidePunchGapAction,
  dismissPunchGapsAction,
} from "@/app/labor/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MultiSelectFilter } from "@/components/tables/DataTable";
import { WriteToAdpButton } from "@/components/labor/WriteToAdpButton";
import { FEATURES } from "@/lib/config/features";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import {
  KIND_LABEL,
  filterPunchGaps,
  decisionTimes,
  formatClock,
  isOpenGap,
  isWrittenToAdp,
  hoursIfClosedAt,
  openClockIn,
  punchGapOptions,
  sortPunchGaps,
  summarizePunchGaps,
  timelineBounds,
  validateDecision,
  writableToAdp,
  type PunchGap,
  type PunchGapColumn,
  type PunchGapFilters,
  type PunchGapKind,
  type PunchGapSort,
} from "@/lib/labor/punch-gaps";
import { cn } from "@/lib/utils";
import { PunchGapTimeline, PunchGapTimelineAxis } from "./PunchGapTimeline";

const KIND_BADGE: Record<PunchGapKind, string> = {
  missing_out: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  missing_out_after_break:
    "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  in_progress: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  no_entry: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
  missing_in: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
};

const LONG_SHIFT_HOURS = 10;

const RULE_LABEL: Record<string, string> = {
  close_at_scheduled_end: "scheduled end",
  close_at_shop_close: "shop close",
  pay_scheduled_hours: "full scheduled hours",
  scheduled_shift: "scheduled shift",
};

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function shortName(employee: string): string {
  const [last, first] = employee.split(",").map((s) => s.trim());
  return first ? `${first.split(" ")[0]} ${last![0]}.` : employee;
}

function rangeText(a: string | null, b: string | null): string {
  return `${formatClock(a)}–${b ? formatClock(b) : "?"}`;
}

function DecisionBadge({ gap }: { gap: PunchGap }) {
  const d = gap.decision!;
  if (d.action === "reject") {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        Dismissed
      </Badge>
    );
  }
  const verb = d.action === "accept" ? "Accepted" : "Edited";
  const adp: Record<string, { text: string; tone: string }> = {
    recorded: { text: "Not yet in ADP", tone: "text-muted-foreground" },
    pending_write: { text: "Not yet in ADP", tone: "text-muted-foreground" },
    applying: {
      text: "Writing to ADP…",
      tone: "text-amber-700 dark:text-amber-400",
    },
    applied: {
      text: "Written to ADP ✓",
      tone: "text-emerald-700 dark:text-emerald-400",
    },
    not_in_hours: {
      text: "In ADP — not in hours yet",
      tone: "text-amber-700 dark:text-amber-400",
    },
    already_resolved: {
      text: "Already fixed in ADP",
      tone: "text-muted-foreground",
    },
    failed: { text: "ADP write failed — retry", tone: "text-destructive" },
  };
  const line = adp[d.status] ?? {
    text: d.status,
    tone: "text-muted-foreground",
  };
  return (
    <span className="flex flex-col items-end gap-0.5">
      <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
        {verb}
      </Badge>
      <span className="whitespace-nowrap text-[11px] tabular-nums text-foreground/80">
        {decisionTimes(d)}
      </span>
      <span
        className={cn("text-[11px]", line.tone)}
        title={d.error ?? undefined}
      >
        {line.text}
      </span>
    </span>
  );
}

function GapActions({ gap, onDone }: { gap: PunchGap; onDone: () => void }) {
  const { isPending, error, run, setError } = useConsoleAction();
  const [mode, setMode] = useState<"idle" | "edit">("idle");
  const [changing, setChanging] = useState(false);
  const clockIn = openClockIn(gap);
  const [inTime, setInTime] = useState(
    gap.suggestedIn ?? gap.scheduled[0]?.[0] ?? "",
  );
  const [outTime, setOutTime] = useState(
    gap.suggestedOut ?? gap.scheduled[gap.scheduled.length - 1]?.[1] ?? "",
  );
  const isNoEntry = gap.kind === "no_entry";
  const reviewOnly = gap.kind === "in_progress" || gap.kind === "missing_in";

  function submit(action: "accept" | "edit" | "reject") {
    const input = {
      date: gap.date,
      employee: gap.employee,
      action,
      inTime: isNoEntry ? inTime : null,
      outTime: action === "edit" ? outTime : null,
    };
    const problem = validateDecision(gap, input);
    if (problem) {
      setError(problem);
      return;
    }
    void run(() => decidePunchGapAction(input), { saving: "Saving…" }).then(
      (ack) => {
        if (ack.ok) {
          setMode("idle");
          setChanging(false);
          onDone();
        }
      },
    );
  }

  if (gap.decision && !changing) {
    const locked = isWrittenToAdp(gap.decision.status);
    return (
      <div className="flex items-start justify-end gap-1">
        <DecisionBadge gap={gap} />
        {locked ? null : (
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Change decision for ${gap.employee} on ${gap.date}`}
            onClick={() => setChanging(true)}
          >
            <RotateCcwIcon />
          </Button>
        )}
      </div>
    );
  }

  if (gap.kind === "ok") {
    return <span className="block text-right text-xs text-muted-foreground/60">—</span>;
  }

  if (reviewOnly) {
    return (
      <span className="block text-right text-xs text-muted-foreground">
        {gap.kind === "in_progress" ? "Re-checks after close" : "Fix in ADP"}
      </span>
    );
  }

  if (mode === "edit") {
    const preview = isNoEntry
      ? hoursIfClosedAt(inTime, outTime)
      : hoursIfClosedAt(clockIn, outTime);
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="flex items-center gap-1">
          {isNoEntry ? (
            <Input
              type="time"
              aria-label="Clock-in time"
              value={inTime}
              onChange={(e) => {
                setInTime(e.target.value);
                setError(null);
              }}
              className="h-7 w-[6.5rem] text-xs"
            />
          ) : null}
          <Input
            autoFocus
            type="time"
            aria-label="Clock-out time"
            value={outTime}
            onChange={(e) => {
              setOutTime(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit("edit");
              if (e.key === "Escape") setMode("idle");
            }}
            className="h-7 w-[6.5rem] text-xs"
          />
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={isPending}
            onClick={() => submit("edit")}
            aria-label="Save clock-out"
          >
            <CheckIcon />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={isPending}
            onClick={() => setMode("idle")}
            aria-label="Cancel edit"
          >
            <XIcon />
          </Button>
        </div>
        <span
          className={cn(
            "text-[11px]",
            error ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {error ??
            (preview != null ? `${preview.toFixed(2)} h` : "Pick a time")}
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center justify-end gap-1">
        {gap.suggestedOut && (!isNoEntry || gap.suggestedIn) ? (
          <Button
            size="xs"
            disabled={isPending}
            onClick={() => submit("accept")}
          >
            <CheckIcon data-icon="inline-start" />
            Accept
          </Button>
        ) : null}
        <Button
          size="xs"
          variant="outline"
          disabled={isPending}
          onClick={() => setMode("edit")}
        >
          <PencilIcon data-icon="inline-start" />
          Edit
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={isPending}
          onClick={() => submit("reject")}
          className="text-muted-foreground"
        >
          Dismiss
        </Button>
        {changing ? (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Keep current decision"
            onClick={() => setChanging(false)}
          >
            <XIcon />
          </Button>
        ) : null}
      </div>
      {error ? (
        <span className="text-[11px] text-destructive">{error}</span>
      ) : null}
    </div>
  );
}

function SuggestionCell({ gap }: { gap: PunchGap }) {
  if (gap.kind === "ok") {
    const worked = gap.entries.reduce((s, e) => s + e.hours, 0);
    return (
      <span className="text-xs tabular-nums text-muted-foreground">
        {worked.toFixed(2)} h worked
      </span>
    );
  }
  if (gap.kind === "no_entry" && !gap.suggestedIn) {
    return (
      <span className="text-xs text-muted-foreground">
        No punch to close — only pay if they worked
      </span>
    );
  }
  if (gap.kind === "in_progress") {
    return (
      <span className="text-xs text-muted-foreground">
        Clocked in {formatClock(openClockIn(gap))}
      </span>
    );
  }
  if (!gap.suggestedOut) {
    return (
      <span className="text-xs text-muted-foreground">
        No schedule to anchor on
      </span>
    );
  }
  return (
    <span className="flex flex-col">
      <span className="text-sm font-medium tabular-nums">
        {gap.kind === "no_entry"
          ? `${formatClock(gap.suggestedIn)} – ${formatClock(gap.suggestedOut)}`
          : `Out ${formatClock(gap.suggestedOut)}`}
      </span>
      <span className="text-[11px] text-muted-foreground">
        +{gap.suggestedHours?.toFixed(2)} h ·{" "}
        {RULE_LABEL[gap.rule ?? ""] ?? gap.rule}
      </span>
      {(gap.suggestedHours ?? 0) >= LONG_SHIFT_HOURS ? (
        <span className="text-[11px] text-amber-700 dark:text-amber-300">
          Long day — check the schedule
        </span>
      ) : null}
    </span>
  );
}

function ContextLine({ gap }: { gap: PunchGap }) {
  const sched = gap.scheduled.length
    ? gap.scheduled.map(([a, b]) => rangeText(a, b)).join(", ")
    : "not scheduled";
  const punched = gap.entries.map((e) => rangeText(e.in, e.out)).join(", ");
  const others = gap.coworkers
    .map((c) => `${shortName(c.employee)} ${rangeText(c.in_time, c.out_time)}`)
    .join(" · ");
  return (
    <div className="flex flex-col gap-0.5 text-[11px] leading-tight text-muted-foreground">
      <span>
        <span className="text-foreground/80">Sched</span> {sched} ·{" "}
        {punched ? (
          <>
            <span className="text-foreground/80">Punched</span> {punched}
          </>
        ) : (
          "No punches"
        )}
      </span>
      {others ? (
        <span className="max-w-[22rem] truncate" title={others}>
          <span className="text-foreground/80">Also on</span> {others}
        </span>
      ) : null}
    </div>
  );
}

/** Rows a bulk action may touch: not on shift and not already written / being written to ADP. */
function selectable(gap: PunchGap): boolean {
  if (gap.kind === "in_progress" || gap.kind === "ok") return false;
  return !isWrittenToAdp(gap.decision?.status);
}

function RowCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  label,
  onChange,
}: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      className="size-3.5 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-40"
      checked={checked}
      disabled={disabled}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

function BulkActions({
  chosen,
  onDone,
  onClear,
}: {
  chosen: PunchGap[];
  onDone: () => void;
  onClear: () => void;
}) {
  const { isPending, run } = useConsoleAction();
  const [confirming, setConfirming] = useState<"accept" | "dismiss" | null>(
    null,
  );
  if (!chosen.length) {
    return (
      <p className="text-xs text-muted-foreground">
        Tick rows to accept or dismiss several at once.
      </p>
    );
  }
  const acceptable = chosen.filter(
    (g) => validateDecision(g, { action: "accept" }) === null,
  );
  const hours = acceptable.reduce((t, g) => t + (g.suggestedHours ?? 0), 0);
  const items = (gaps: PunchGap[]) =>
    gaps.map((g) => ({ date: g.date, employee: g.employee }));
  const finish = (ok: boolean) => {
    setConfirming(null);
    if (ok) onDone();
  };

  if (confirming) {
    const n = confirming === "accept" ? acceptable.length : chosen.length;
    return (
      <span className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">
          {confirming === "accept"
            ? `Accept ${n} suggestion${n === 1 ? "" : "s"} (+${hours.toFixed(2)} h)? Nothing goes to ADP yet.`
            : `Dismiss ${n} row${n === 1 ? "" : "s"}? They leave the Open list and nothing goes to ADP.`}
        </span>
        <Button
          type="button"
          size="xs"
          variant={confirming === "dismiss" ? "destructive" : "default"}
          disabled={isPending}
          onClick={() =>
            void (
              confirming === "accept"
                ? run(() => acceptPunchGapsAction(items(acceptable)), {
                    saving: `Accepting ${n}…`,
                  })
                : run(() => dismissPunchGapsAction(items(chosen)), {
                    saving: `Dismissing ${n}…`,
                  })
            ).then((ack) => finish(ack.ok))
          }
        >
          {isPending
            ? confirming === "accept"
              ? "Accepting…"
              : "Dismissing…"
            : confirming === "accept"
              ? "Accept"
              : "Dismiss"}
        </Button>
        <Button
          type="button"
          size="xs"
          variant="ghost"
          disabled={isPending}
          onClick={() => setConfirming(null)}
        >
          Cancel
        </Button>
      </span>
    );
  }

  const skipped = chosen.length - acceptable.length;
  return (
    <span className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="font-medium tabular-nums">{chosen.length} selected</span>
      <Button
        type="button"
        size="xs"
        className="gap-1"
        disabled={!acceptable.length}
        title={
          skipped
            ? `${skipped} selected row${skipped === 1 ? " has" : "s have"} no suggestion to accept`
            : undefined
        }
        onClick={() => setConfirming("accept")}
      >
        <CheckIcon />
        Accept {acceptable.length}
      </Button>
      <Button
        type="button"
        size="xs"
        variant="outline"
        className="gap-1"
        onClick={() => setConfirming("dismiss")}
      >
        <XIcon />
        Dismiss {chosen.length}
      </Button>
      <Button type="button" size="xs" variant="ghost" onClick={onClear}>
        Clear
      </Button>
    </span>
  );
}

/**
 * Server-side write in progress (rows `applying` in BQ). Lives off the data,
 * not the click, so it survives a refresh or a new tab; re-reads every 10 s.
 */
function AdpWriteStatus({ gaps }: { gaps: PunchGap[] }) {
  const router = useRouter();
  const writing = gaps.filter((g) => g.decision?.status === "applying");
  const active = writing.length > 0;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), 10_000);
    return () => clearInterval(t);
  }, [active, router]);
  if (!active) return null;
  const n = writing.length;
  return (
    <div
      role="status"
      className="flex items-center gap-2 rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-800 dark:text-sky-200"
    >
      <Loader2Icon className="size-3.5 shrink-0 animate-spin" />
      <span>
        Writing {n} punch fix{n === 1 ? "" : "es"} to ADP on the server — each
        row turns <span className="font-medium">Written to ADP ✓</span> as it
        lands. Safe to close this tab; this page checks every 10 s.
      </span>
    </div>
  );
}

function ColumnHeader({
  column,
  label,
  sort,
  onSort,
  filter,
}: {
  column: PunchGapColumn;
  label: string;
  sort: PunchGapSort;
  onSort: (column: PunchGapColumn) => void;
  filter?: ReactNode;
}) {
  const active = sort.column === column;
  const Icon = !active
    ? ChevronsUpDownIcon
    : sort.desc
      ? ArrowDownIcon
      : ArrowUpIcon;
  return (
    <div className="flex flex-col gap-1.5 py-1">
      <button
        type="button"
        onClick={() => onSort(column)}
        aria-label={`Sort by ${label}`}
        className={cn(
          "flex items-center gap-1 rounded-sm text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active && "text-foreground",
        )}
      >
        {label}
        <Icon className={cn("size-3", !active && "text-muted-foreground/50")} />
      </button>
      {filter}
    </div>
  );
}

export function PunchGapsPanel({
  gaps,
  periodLabel,
}: {
  gaps: PunchGap[];
  periodLabel: string;
}) {
  const router = useRouter();
  const [view, setView] = useState<"open" | "all">(() =>
    gaps.some(isOpenGap) ? "open" : "all",
  );
  const summary = useMemo(() => summarizePunchGaps(gaps), [gaps]);
  const bounds = useMemo(() => timelineBounds(gaps), [gaps]);
  const [sort, setSort] = useState<PunchGapSort>({
    column: "day",
    desc: true,
  });
  const [filters, setFilters] = useState<PunchGapFilters>({});
  const inView = useMemo(
    () => (view === "open" ? gaps.filter(isOpenGap) : gaps),
    [view, gaps],
  );
  const shown = useMemo(
    () => sortPunchGaps(filterPunchGaps(inView, filters), sort),
    [inView, filters, sort],
  );
  const filterActive = Object.values(filters).some((v) => v && v.length > 0);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const selectableShown = shown.filter(selectable);
  const chosen = selectableShown.filter((g) => selected.has(g.key));
  const allChosen =
    chosen.length > 0 && chosen.length === selectableShown.length;
  const toggle = (keys: string[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const k of keys) {
        if (on) next.add(k);
        else next.delete(k);
      }
      return next;
    });
  const ariaSort = (column: PunchGapColumn) =>
    sort.column === column
      ? sort.desc
        ? "descending"
        : "ascending"
      : undefined;
  const onSort = (column: PunchGapColumn) =>
    setSort((s) =>
      s.column === column ? { column, desc: !s.desc } : { column, desc: false },
    );
  const filterFor = (
    column: keyof PunchGapFilters,
    label: string,
    format?: (v: string) => string,
  ) => (
    <MultiSelectFilter
      columnId={`punch-${column}`}
      label={label}
      options={punchGapOptions(inView, filters, column)}
      value={filters[column] ?? []}
      onChange={(next) => setFilters((f) => ({ ...f, [column]: next }))}
      formatOption={format}
    />
  );

  return (
    <Card data-testid="labor-punch-gaps">
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Punches — {periodLabel}
          </CardTitle>
          <div className="flex items-center gap-2">
            {FEATURES.punchFixWriteback ? (
              <WriteToAdpButton gaps={writableToAdp(gaps)} />
            ) : null}
            <div className="flex items-center gap-1 rounded-lg border border-border p-0.5">
              {(
                [
                  ["open", `Needs review · ${gaps.filter(isOpenGap).length}`],
                  ["all", `All · ${gaps.length}`],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setView(value)}
                  aria-pressed={view === value}
                  className={cn(
                    "h-6 rounded-md px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    view === value
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <CardDescription>
          Every clocked day in the Period, from ADP. Filter Issue to
          &ldquo;No issue&rdquo; for the clean days. Issues come from ADP&apos;s
          Timecards page, which still shows an open entry the timecard export
          drops — those hours are missing from every chart above until fixed.
          A missing clock-out is closed at the end of the scheduled shift, or
          later if a late clock-in would otherwise pay less than the scheduled
          hours; a completed punch is never changed. A scheduled day with no
          punch suggests the scheduled shift — dismiss it if they didn&apos;t
          work.
          {summary.undecided > 0 && summary.suggestedHours > 0 ? (
            <>
              {" "}
              Accepting every suggestion adds{" "}
              <span className="font-medium text-foreground">
                {summary.suggestedHours.toFixed(2)} h
              </span>
              .
            </>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {inView.length ? (
          <div className="flex flex-col gap-2">
            <AdpWriteStatus gaps={gaps} />
            <div className="flex min-h-7 flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                Showing {shown.length} of {inView.length}
                {filterActive ? (
                  <>
                    {" "}
                    (filtered) ·{" "}
                    <button
                      type="button"
                      className="underline-offset-2 hover:text-foreground hover:underline"
                      onClick={() => setFilters({})}
                    >
                      Clear filters
                    </button>
                  </>
                ) : null}
              </p>
              <BulkActions
                chosen={chosen}
                onClear={() => setSelected(new Set())}
                onDone={() => {
                  setSelected(new Set());
                  router.refresh();
                }}
              />
            </div>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-8 pt-2.5 align-top">
                    <RowCheckbox
                      label="Select all rows shown"
                      checked={allChosen}
                      indeterminate={chosen.length > 0 && !allChosen}
                      disabled={!selectableShown.length}
                      onChange={(on) =>
                        toggle(
                          selectableShown.map((g) => g.key),
                          on,
                        )
                      }
                    />
                  </TableHead>
                  <TableHead
                    aria-sort={ariaSort("day")}
                    className="w-[7rem] align-top"
                  >
                    <ColumnHeader
                      column="day"
                      label="Day"
                      sort={sort}
                      onSort={onSort}
                      filter={filterFor("day", "Day", dayLabel)}
                    />
                  </TableHead>
                  <TableHead
                    aria-sort={ariaSort("employee")}
                    className="w-[10rem] align-top"
                  >
                    <ColumnHeader
                      column="employee"
                      label="Employee"
                      sort={sort}
                      onSort={onSort}
                      filter={filterFor("employee", "Employee")}
                    />
                  </TableHead>
                  <TableHead
                    aria-sort={ariaSort("issue")}
                    className="w-[8.5rem] align-top"
                  >
                    <ColumnHeader
                      column="issue"
                      label="Issue"
                      sort={sort}
                      onSort={onSort}
                      filter={filterFor("issue", "Issue")}
                    />
                  </TableHead>
                  <TableHead className="min-w-[15rem] align-bottom">
                    <PunchGapTimelineAxis bounds={bounds} />
                  </TableHead>
                  <TableHead
                    aria-sort={ariaSort("suggestion")}
                    className="w-[8rem] align-top"
                  >
                    <ColumnHeader
                      column="suggestion"
                      label="Suggestion"
                      sort={sort}
                      onSort={onSort}
                    />
                  </TableHead>
                  <TableHead
                    aria-sort={ariaSort("decision")}
                    className="w-[1%] min-w-[9rem] align-top"
                  >
                    <ColumnHeader
                      column="decision"
                      label="Decision"
                      sort={sort}
                      onSort={onSort}
                      filter={filterFor("decision", "Decision")}
                    />
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((gap) => (
                  <TableRow
                    key={gap.key}
                    data-state={selected.has(gap.key) ? "selected" : undefined}
                    className="align-top"
                  >
                    <TableCell className="w-8">
                      <RowCheckbox
                        label={`Select ${gap.employee} on ${gap.date}`}
                        checked={selected.has(gap.key)}
                        disabled={!selectable(gap)}
                        onChange={(on) => toggle([gap.key], on)}
                      />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm tabular-nums">
                      {dayLabel(gap.date)}
                    </TableCell>
                    <TableCell className="text-sm font-medium">
                      {gap.employee}
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-wrap gap-1">
                        <Badge className={KIND_BADGE[gap.kind]}>
                          {KIND_LABEL[gap.kind]}
                        </Badge>
                        {gap.kind === "missing_out_after_break" ? (
                          <Badge
                            variant="outline"
                            className="text-muted-foreground"
                          >
                            after break
                          </Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <div className="flex flex-col gap-1.5">
                        <PunchGapTimeline gap={gap} bounds={bounds} />
                        <ContextLine gap={gap} />
                      </div>
                    </TableCell>
                    <TableCell>
                      <SuggestionCell gap={gap} />
                    </TableCell>
                    <TableCell className="text-right">
                      <GapActions gap={gap} onDone={() => router.refresh()} />
                    </TableCell>
                  </TableRow>
                ))}
                {!shown.length ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell
                      colSpan={7}
                      className="py-6 text-center text-sm text-muted-foreground"
                    >
                      No rows match these filters.{" "}
                      <button
                        type="button"
                        className="underline-offset-2 hover:text-foreground hover:underline"
                        onClick={() => setFilters({})}
                      >
                        Clear filters
                      </button>
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {gaps.length
              ? "Nothing needs review — every issue in this Period has a decision. Switch to All for every punch."
              : "No punches in this Period yet."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
