"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { assignOpenShiftsAction, openShiftAssignStatusAction } from "@/app/labor/actions";
import { ShiftPersonPicker } from "@/components/labor/ShiftPersonPicker";
import { TRAINING_TEXT_CLASS } from "@/components/labor/StaffTag";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClockMin } from "@/lib/labor/coverage-model";
import { summarizeAssign, type AssignRow } from "@/lib/labor/open-shift-assign";
import type { DraftShift } from "@/lib/labor/shift-draft";
import { cn } from "@/lib/utils";

const OPEN_COLOR = LABOR_CHART_COLORS.openShift;
const POLL_MS = 5000;
const GRACE_MS = 90 * 1000;
const KIND_LABEL = { open: "Open", mid: "Mid", close: "Close" } as const;

/** One ADP open shift on a day; `fill` = who the draft suggests (or the operator picked), null = nobody fits. */
export type DayOpenShift = { slot: string; date: string; startMin: number; endMin: number; hours: number; fill: DraftShift | null };

/** A selected open shift ready to write: the person is whoever the picker shows now. */
export type AssignSelection = { slot: string; date: string; startMin: number; endMin: number; employee: string };

export function shortDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

const kindOf = (s: { startMin: number; endMin: number }) =>
  s.startMin <= 450 ? "open" : s.endMin >= 1200 ? "close" : "mid";

const time = (s: { startMin: number; endMin: number }) =>
  `${formatClockMin(s.startMin)}–${formatClockMin(s.endMin)}`;

function SelectBox({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      className="size-4 shrink-0 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-40"
      checked={checked}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

/** The selected day's ADP open shifts: tick the ones to fill and choose who takes each. */
export function OpenShiftDayList({
  dayLabel,
  shifts,
  selected,
  enabled,
  onToggle,
  onPick,
}: {
  dayLabel: string;
  shifts: DayOpenShift[];
  selected: ReadonlySet<string>;
  /** FEATURES.adpScheduleWrite. */
  enabled: boolean;
  onToggle: (slots: string[], on: boolean) => void;
  /** A person, or undefined = back to the suggestion. */
  onPick: (slot: string, employee: string | undefined) => void;
}) {
  const choosable = shifts.filter((s) => s.fill?.employee);
  const allOn = choosable.length > 0 && choosable.every((s) => selected.has(s.slot));
  return (
    <div
      data-testid="open-shift-assign"
      className="flex flex-col gap-2 rounded-lg border px-3 py-3"
      style={{ borderColor: `${OPEN_COLOR}55`, backgroundColor: `${OPEN_COLOR}0a` }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="text-sm font-medium text-foreground">Open shifts in ADP — {dayLabel}</h4>
          <p className="text-xs text-muted-foreground">
            Tick the ones to fill and choose who takes each. Your selection carries across days — write them all to
            ADP at once.
          </p>
        </div>
        {enabled && choosable.length > 1 ? (
          <Button
            variant="link"
            size="xs"
            className="h-6 px-0 text-xs"
            onClick={() => onToggle(choosable.map((s) => s.slot), !allOn)}
          >
            {allOn ? "Clear this day" : "Select all"}
          </Button>
        ) : null}
      </div>
      <ul className="flex flex-col divide-y rounded-md border bg-background">
        {shifts.map((s) => {
          const on = selected.has(s.slot);
          const who = s.fill?.employee ?? null;
          return (
            <li
              key={s.slot}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 transition-colors",
                on && "bg-primary/5",
              )}
            >
              <SelectBox
                checked={on}
                disabled={!enabled || !who}
                label={`Select the ${time(s)} open shift`}
                onChange={(next) => onToggle([s.slot], next)}
              />
              <span className="flex min-w-36 items-center gap-2">
                <Badge variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">
                  {KIND_LABEL[kindOf(s)]}
                </Badge>
                <span className="text-sm tabular-nums">{time(s)}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{s.hours.toFixed(1)}h</span>
              </span>
              <span className="min-w-0 flex-1 basis-48">
                {s.fill && who ? (
                  <ShiftPersonPicker shift={s.fill} onPick={(employee) => onPick(s.slot, employee ?? undefined)} />
                ) : (
                  <span className="flex flex-col gap-0.5 text-xs">
                    <span className="text-muted-foreground">Nobody fits your rules — assign in ADP</span>
                    {s.fill?.blocked?.map((b) => (
                      <span key={b.employee} className={TRAINING_TEXT_CLASS}>
                        {b.employee} · {b.reason}
                      </span>
                    ))}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {!enabled ? (
        <p className="text-xs text-muted-foreground">ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE).</p>
      ) : null}
    </div>
  );
}

function SelectionList({ items }: { items: AssignSelection[] }) {
  const byDay = new Map<string, AssignSelection[]>();
  for (const s of [...items].sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin)) {
    byDay.set(s.date, [...(byDay.get(s.date) ?? []), s]);
  }
  return (
    <div className="flex flex-col gap-3 px-4">
      {[...byDay].map(([date, list]) => (
        <div key={date} className="flex flex-col gap-1">
          <p className="text-xs font-medium text-muted-foreground">{shortDay(date)}</p>
          <ul className="flex flex-col divide-y rounded-md border">
            {list.map((s) => (
              <li key={s.slot} className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm">
                <span className="tabular-nums">{time(s)}</span>
                <span className="truncate font-medium">{s.employee}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * Sticky bar for the open shifts selected across days, then one drawer that lists
 * them and starts the ADP job (Issue #412). Nothing reaches ADP until its confirm.
 */
export function OpenShiftAssignBar({
  items,
  dropped,
  enabled,
  onClear,
  onAssigned,
}: {
  items: AssignSelection[];
  /** Selected shifts ADP no longer lists open (claimed or assigned elsewhere) that were dropped. */
  dropped: number;
  enabled: boolean;
  onClear: () => void;
  /** Slots ADP confirmed assigned — leave the selection. */
  onAssigned: (slots: string[]) => void;
}) {
  const [assignId, setAssignId] = useState<string | null>(null);
  const [rows, setRows] = useState<AssignRow[]>([]);
  const [graceUntil, setGraceUntil] = useState(0);
  const [sheet, setSheet] = useState(false);
  const [result, setResult] = useState<AssignRow[] | null>(null);
  const { isPending, run } = useConsoleAction();
  const router = useRouter();
  const [sent, setSent] = useState<AssignSelection[]>([]);

  // A reloaded page resumes a run still going on Cloud Run.
  useEffect(() => {
    let live = true;
    void openShiftAssignStatusAction(null)
      .catch(() => null)
      .then((ack) => {
        if (!live || !ack?.ok || !ack.data?.running) return;
        setAssignId(ack.data.running);
        setRows(ack.data.rows);
      });
    return () => {
      live = false;
    };
  }, []);

  const settle = useCallback(
    (final: AssignRow[]) => {
      const done = final.filter((r) => r.status === "assigned" || r.status === "skipped");
      const slots = (sent.length ? sent : items)
        .filter((s) =>
          done.some(
            (r) =>
              r.date === s.date &&
              Number(r.start_min) === s.startMin &&
              Number(r.end_min) === s.endMin &&
              r.employee === s.employee,
          ),
        )
        .map((s) => s.slot);
      if (slots.length) onAssigned(slots);
      setResult(final);
      setAssignId(null);
      setGraceUntil(0);
      router.refresh();
    },
    [sent, items, onAssigned, router],
  );

  // The job can take a while to show as running after it's started; until the grace ends, queued rows mean "not yet".
  const poll = useCallback(async () => {
    if (!assignId) return;
    const ack = await openShiftAssignStatusAction(assignId).catch(() => null);
    if (!ack?.ok || !ack.data) return;
    const live = ack.data.running === assignId;
    setRows(ack.data.rows);
    const queued = ack.data.rows.some((r) => r.status === "queued");
    if (!live && (Date.now() > graceUntil || (ack.data.rows.length > 0 && !queued))) settle(ack.data.rows);
  }, [assignId, graceUntil, settle]);

  const polling = assignId != null;
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(id);
  }, [polling, poll]);

  const summary = useMemo(() => summarizeAssign(rows), [rows]);

  const write = async () => {
    const chosen = items;
    const ack = await run(
      () =>
        assignOpenShiftsAction(
          chosen.map(({ date, startMin, endMin, employee }) => ({ date, startMin, endMin, employee })),
        ),
      { saving: "Queuing…", queued: "Assigning open shifts in ADP in the background." },
    );
    if (ack.ok && ack.data) {
      setSent(chosen);
      setSheet(false);
      setResult(null);
      setRows([]);
      setAssignId(ack.data.assignId);
      setGraceUntil(Date.now() + GRACE_MS);
    }
  };

  const busy = polling || isPending;
  const outcome = result ? summarizeAssign(result) : null;
  if (!items.length && !busy && !outcome && !dropped) return null;

  const days = [...new Set(items.map((i) => i.date))].sort();
  const done = summary.assigned + summary.skipped + summary.failed;

  return (
    <div
      data-testid="open-shift-assign-bar"
      className="sticky bottom-3 z-20 flex flex-col gap-1.5 rounded-lg border bg-card/95 px-3 py-2.5 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-card/80"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm" aria-live="polite">
          {busy ? (
            <span className="text-foreground">
              Assigning in ADP — {done} of {rows.length || sent.length || "…"} done. Safe to leave or
              reload this page.
            </span>
          ) : items.length ? (
            <>
              <span className="font-medium text-foreground">
                {items.length} open shift{items.length === 1 ? "" : "s"} selected
              </span>
              <span className="text-muted-foreground"> · {days.map(shortDay).join(", ")}</span>
            </>
          ) : (
            <span className="text-muted-foreground">No open shifts selected.</span>
          )}
        </p>
        <div className="flex items-center gap-2">
          {(items.length > 0 || dropped > 0) && !busy ? (
            <Button size="sm" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          ) : null}
          <Button
            size="sm"
            disabled={!enabled || busy || items.length === 0}
            title={enabled ? undefined : "ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE)"}
            onClick={() => setSheet(true)}
          >
            {busy ? "Writing to ADP…" : "Write to ADP"}
          </Button>
        </div>
      </div>
      {dropped > 0 ? (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {dropped} selected shift{dropped === 1 ? " is" : "s are"} no longer open in ADP and{" "}
          {dropped === 1 ? "was" : "were"} removed from your selection.
        </p>
      ) : null}
      {outcome && !busy ? (
        <div className="flex flex-col gap-1 text-xs">
          <p className="flex flex-wrap items-center gap-1.5">
            {outcome.assigned + outcome.skipped ? (
              <Badge variant="default">{outcome.assigned + outcome.skipped} assigned in ADP</Badge>
            ) : null}
            {outcome.failed ? <Badge variant="destructive">{outcome.failed} not assigned</Badge> : null}
            {outcome.queued ? <Badge variant="secondary">{outcome.queued} not attempted — try again</Badge> : null}
            <Button variant="link" size="xs" className="h-6 px-1 text-xs" onClick={() => setResult(null)}>
              Dismiss
            </Button>
          </p>
          {outcome.errors.length ? (
            <ul className="text-rose-600 dark:text-rose-400">
              {outcome.errors.slice(0, 4).map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <Sheet open={sheet} onOpenChange={(o) => !o && setSheet(false)}>
        <SheetContent className="w-full max-w-md overflow-y-auto">
          <SheetHeader>
            <SheetTitle>
              Assign {items.length} open shift{items.length === 1 ? "" : "s"} in ADP
            </SheetTitle>
            <SheetDescription>
              Gives each of these ADP open shifts to the person shown ({days.length} day
              {days.length === 1 ? "" : "s"}). They&apos;re already published, so each person sees their shift in ADP
              once it&apos;s assigned. ADP only accepts people it lists as eligible for a shift — anyone else is
              reported back and that shift stays open. About 2 min to sign in to ADP, then ~20 s per shift; you can
              keep using the page. The page reloads from ADP when it&apos;s done.
            </SheetDescription>
          </SheetHeader>
          <SelectionList items={items} />
          <SheetFooter>
            <Button onClick={() => void write()} disabled={isPending}>
              {isPending ? "Queuing…" : `Assign ${items.length} in ADP`}
            </Button>
            <SheetClose render={<Button variant="outline">Cancel</Button>} />
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
