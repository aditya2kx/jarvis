"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  publishWeekAction,
  saveDraftsToAdpAction,
  schedulePushStatusAction,
  type ScheduleWriteRun,
} from "@/app/labor/actions";
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
import { formatClockMin } from "@/lib/labor/coverage-model";
import {
  relevantRows,
  splitAgainstAdp,
  summarizePush,
  type PushRow,
  type PushShift,
} from "@/lib/labor/schedule-push";

const POLL_MS = 5000;
const GRACE_MS = 90 * 1000;

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/** "Oct 12–18", or "Oct 26 – Nov 1" across a month boundary. */
export function weekRangeLabel(weekStart: string): string {
  const [y, m, d] = weekStart.split("-").map(Number);
  const start = new Date(y!, m! - 1, d!);
  const end = new Date(y!, m! - 1, d! + 6);
  const month = (x: Date) => x.toLocaleDateString("en-US", { month: "short" });
  return start.getMonth() === end.getMonth()
    ? `${month(start)} ${start.getDate()}–${end.getDate()}`
    : `${month(start)} ${start.getDate()} – ${month(end)} ${end.getDate()}`;
}

const hoursOf = (shifts: PushShift[]) => shifts.reduce((a, s) => a + (s.endMin - s.startMin) / 60, 0);

function ShiftList({ shifts }: { shifts: PushShift[] }) {
  const byDay = new Map<string, PushShift[]>();
  for (const s of [...shifts].sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin)) {
    byDay.set(s.date, [...(byDay.get(s.date) ?? []), s]);
  }
  return (
    <div className="flex flex-col gap-3 px-4">
      {[...byDay].map(([date, list]) => (
        <div key={date} className="flex flex-col gap-1">
          <p className="flex justify-between text-xs font-medium text-muted-foreground">
            <span>{dayLabel(date)}</span>
            <span className="tabular-nums">
              {list.length} shift{list.length === 1 ? "" : "s"} · {hoursOf(list).toFixed(1)}h
            </span>
          </p>
          <ul className="flex flex-col divide-y rounded-md border">
            {list.map((s) => (
              <li
                key={`${s.employee ?? "open"}-${s.startMin}`}
                className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm"
              >
                <span className="tabular-nums">
                  {formatClockMin(s.startMin)}–{formatClockMin(s.endMin)}
                </span>
                {s.employee ? (
                  <span className="truncate font-medium">{s.employee}</span>
                ) : (
                  <Badge variant="outline" className="font-normal">
                    Open shift
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * "Save to ADP as drafts" then "Publish week" (Issue #337). Each opens a drawer
 * listing exactly what will be written; nothing reaches ADP until its confirm
 * button. Drafts stay invisible to employees until the week is published.
 */
export function AdpScheduleFinalize({
  weekStart,
  shifts,
  enabled,
}: {
  weekStart: string;
  shifts: PushShift[];
  enabled: boolean;
}) {
  const [allRows, setRows] = useState<PushRow[]>([]);
  const [running, setRunning] = useState<ScheduleWriteRun | null>(null);
  const [loadedWeek, setLoadedWeek] = useState<string | null>(null);
  // Right after a click the Cloud Run execution may not be listed yet; keep polling meanwhile.
  const [graceUntil, setGraceUntil] = useState(0);
  const [lastAction, setLastAction] = useState<"save" | "publish" | null>(null);
  const [sheet, setSheet] = useState<"save" | "publish" | null>(null);
  const { isPending, run } = useConsoleAction();

  const refresh = useCallback(async () => {
    const ack = await schedulePushStatusAction(weekStart);
    if (ack.ok && ack.data) {
      setRows(ack.data.rows);
      setRunning(ack.data.running);
    }
  }, [weekStart]);

  useEffect(() => {
    let live = true;
    void schedulePushStatusAction(weekStart).then((ack) => {
      if (!live) return;
      if (ack.ok && ack.data) {
        setRows(ack.data.rows);
        setRunning(ack.data.running);
      }
      setLoadedWeek(weekStart);
    });
    return () => {
      live = false;
    };
  }, [weekStart]);

  const polling = running != null || graceUntil > 0;
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => {
      if (graceUntil && Date.now() > graceUntil) setGraceUntil(0);
      void refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [polling, graceUntil, refresh]);

  const rows = useMemo(() => relevantRows(shifts, allRows), [shifts, allRows]);
  const summary = useMemo(() => summarizePush(rows), [rows]);
  const { toSave, inAdp } = useMemo(() => splitAgainstAdp(shifts, rows), [shifts, rows]);
  const drafted = rows.filter((r) => r.status === "drafted");
  const loaded = loadedWeek === weekStart;
  const busy = polling || !loaded;
  const pushRowsNow = running?.pushId ? rows.filter((r) => r.push_id === running.pushId) : [];
  const otherWeek =
    running != null &&
    (running.mode === "publish" ? running.weekStart !== weekStart : pushRowsNow.length === 0) &&
    graceUntil === 0;
  const saving = !otherWeek && (running?.mode === "drafts" || (graceUntil > 0 && lastAction === "save"));
  const publishing = !otherWeek && (running?.mode === "publish" || (graceUntil > 0 && lastAction === "publish"));
  const savedSoFar = pushRowsNow.filter((r) => r.status !== "queued").length;
  const stalled = !polling && summary.queued > 0;

  const start = (action: "save" | "publish") => {
    setSheet(null);
    setLastAction(action);
    setGraceUntil(Date.now() + GRACE_MS);
  };

  const save = async () => {
    const ack = await run(() => saveDraftsToAdpAction(weekStart, toSave), {
      saving: "Queuing drafts…",
      queued: "Saving drafts to ADP in the background.",
    });
    if (ack.ok) {
      start("save");
      await refresh();
    }
  };

  const publish = async () => {
    const ack = await run(() => publishWeekAction(weekStart), {
      saving: "Starting publish…",
      queued: "Publishing the week in ADP in the background.",
    });
    if (ack.ok) start("publish");
  };

  const disabledTitle = enabled ? undefined : "ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE)";
  const range = weekRangeLabel(weekStart);
  const days = new Set(toSave.map((s) => s.date)).size;
  const open = toSave.filter((s) => !s.employee).length;

  return (
    <div
      data-testid="adp-finalize"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 px-3 py-2.5"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h4 className="text-sm font-medium text-foreground">Send the suggested schedule for {range} to ADP</h4>
        <p data-testid="adp-finalize-status" className="text-xs text-muted-foreground">
          {!loaded
            ? "Checking ADP status…"
            : otherWeek
              ? "ADP is busy saving or publishing another week — this week's buttons unlock when it finishes."
              : saving
                ? `Saving to ADP as drafts — ${savedSoFar} of ${pushRowsNow.length || "…"} done (about 7 s per shift, after ~2 min to sign in to ADP). Safe to leave or reload this page.`
                : publishing
                  ? "Publishing the week in ADP — employees get notified when it finishes. Safe to leave or reload this page."
                  : toSave.length
                    ? `The shifts this page drafted for every day of the week (the “+N draft” on each day above): ${toSave.length} shifts — ${toSave.length - open} assigned, ${open} open — ${hoursOf(toSave).toFixed(1)}h. Not in ADP yet.`
                    : drafted.length
                      ? `All ${drafted.length} suggested shifts are ADP drafts — employees don't see them until you publish.`
                      : "Every suggested shift of this week is already in ADP."}
        </p>
        {stalled ? (
          <p className="text-xs text-rose-600 dark:text-rose-400">
            The save stopped with {summary.queued} shift{summary.queued === 1 ? "" : "s"} not saved. Check ADP
            before saving again.
          </p>
        ) : null}
        {rows.length ? (
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {summary.queued ? (
              <Badge variant="secondary">
                {summary.queued} {stalled ? "not saved" : "waiting"}
              </Badge>
            ) : null}
            {drafted.length ? <Badge variant="outline">{drafted.length} ADP drafts</Badge> : null}
            {summary.published ? <Badge variant="default">{summary.published} published</Badge> : null}
            {summary.failed ? <Badge variant="destructive">{summary.failed} failed</Badge> : null}
          </div>
        ) : null}
        {summary.errors.length ? (
          <ul className="text-[11px] text-rose-600 dark:text-rose-400">
            {[...new Set(summary.errors)].slice(0, 3).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!enabled || busy || isPending || toSave.length === 0}
          title={disabledTitle ?? "Step 1 — employees don't see drafts"}
          onClick={() => setSheet("save")}
        >
          {saving
            ? pushRowsNow.length
              ? `Saving to ADP… ${savedSoFar} of ${pushRowsNow.length}`
              : "Saving to ADP…"
            : "1 · Review & save as ADP drafts"}
        </Button>
        <Button
          size="sm"
          disabled={!enabled || busy || isPending || drafted.length === 0}
          title={disabledTitle ?? (drafted.length ? "Step 2 — notifies employees" : "Save drafts first")}
          onClick={() => setSheet("publish")}
        >
          {publishing ? "Publishing…" : `2 · Publish ${range}`}
        </Button>
      </div>

      <Sheet open={sheet != null} onOpenChange={(o) => !o && setSheet(null)}>
        <SheetContent className="w-full max-w-md overflow-y-auto">
          {sheet === "save" ? (
            <>
              <SheetHeader>
                <SheetTitle>Save week of {range} to ADP as drafts</SheetTitle>
                <SheetDescription>
                  {`Creates these ${toSave.length} suggested shifts (${days} days) in ADP Team Schedule as drafts.`}
                  Employees don&apos;t see drafts until you publish the week. Takes about 7 s per
                  shift; you can keep using the page.
                  {inAdp.length ? ` ${inAdp.length} shifts already in ADP are left alone.` : ""}
                </SheetDescription>
              </SheetHeader>
              <ShiftList shifts={toSave} />
              <SheetFooter>
                <Button onClick={() => void save()} disabled={isPending}>
                  {isPending ? "Queuing…" : `Save ${toSave.length} drafts to ADP`}
                </Button>
                <SheetClose render={<Button variant="outline">Cancel</Button>} />
              </SheetFooter>
            </>
          ) : sheet === "publish" ? (
            <>
              <SheetHeader>
                <SheetTitle>Publish week of {range}</SheetTitle>
                <SheetDescription>
                  Publishes every ADP draft for this week (including any you added in ADP) — employees
                  get notified in ADP Mobile.
                </SheetDescription>
              </SheetHeader>
              <ShiftList
                shifts={drafted.map((r) => ({
                  date: r.date,
                  employee: r.employee,
                  startMin: Number(r.start_min),
                  endMin: Number(r.end_min),
                }))}
              />
              <SheetFooter>
                <Button onClick={() => void publish()} disabled={isPending}>
                  {isPending ? "Starting…" : `Publish ${range}`}
                </Button>
                <SheetClose render={<Button variant="outline">Cancel</Button>} />
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
