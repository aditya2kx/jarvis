"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  publishWeekAction,
  saveDraftsToAdpAction,
  schedulePushStatusAction,
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
  splitAgainstAdp,
  summarizePush,
  type PushRow,
  type PushShift,
} from "@/lib/labor/schedule-push";

const POLL_MS = 5000;
const POLL_LIMIT_MS = 20 * 60 * 1000;

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function ShiftList({ shifts }: { shifts: PushShift[] }) {
  const byDay = new Map<string, PushShift[]>();
  for (const s of [...shifts].sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin)) {
    byDay.set(s.date, [...(byDay.get(s.date) ?? []), s]);
  }
  return (
    <div className="flex flex-col gap-3 px-4">
      {[...byDay].map(([date, list]) => (
        <div key={date} className="flex flex-col gap-1">
          <p className="text-xs font-medium text-muted-foreground">{dayLabel(date)}</p>
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
  const [rows, setRows] = useState<PushRow[]>([]);
  const [pollUntil, setPollUntil] = useState(0);
  const [sheet, setSheet] = useState<"save" | "publish" | null>(null);
  const { isPending, run } = useConsoleAction();

  const refresh = useCallback(async () => {
    const ack = await schedulePushStatusAction(weekStart);
    if (ack.ok && ack.data) setRows(ack.data.rows);
  }, [weekStart]);

  useEffect(() => {
    let live = true;
    void schedulePushStatusAction(weekStart).then((ack) => {
      if (live && ack.ok && ack.data) setRows(ack.data.rows);
    });
    return () => {
      live = false;
    };
  }, [weekStart]);

  const summary = useMemo(() => summarizePush(rows), [rows]);
  const { toSave, inAdp } = useMemo(() => splitAgainstAdp(shifts, rows), [shifts, rows]);
  const drafted = rows.filter((r) => r.status === "drafted");
  const busy = summary.queued > 0 || pollUntil > 0;

  useEffect(() => {
    if (!pollUntil) return;
    const id = setInterval(() => {
      if (Date.now() > pollUntil) setPollUntil(0);
      else void refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [pollUntil, refresh]);

  // Stop polling once the job has settled: nothing queued and, after publish, the
  // drafts either became published or were stamped with a new publish error.
  const [publishBaseline, setPublishBaseline] = useState<string | null>(null);
  const publishing = publishBaseline != null;
  const draftStamp = drafted.map((r) => `${r.row_key}@${r.updated_at}`).join(",");
  const settled =
    summary.queued === 0 && (!publishing || drafted.length === 0 || draftStamp !== publishBaseline);
  const [prevSettled, setPrevSettled] = useState(settled);
  if (settled !== prevSettled) {
    setPrevSettled(settled);
    if (settled && pollUntil) {
      setPollUntil(0);
      setPublishBaseline(null);
    }
  }

  const save = async () => {
    const ack = await run(() => saveDraftsToAdpAction(weekStart, toSave), {
      saving: "Queuing drafts…",
      queued: "Saving drafts to ADP in the background.",
    });
    if (ack.ok) {
      setSheet(null);
      await refresh();
      setPollUntil(Date.now() + POLL_LIMIT_MS);
    }
  };

  const publish = async () => {
    const ack = await run(() => publishWeekAction(weekStart), {
      saving: "Starting publish…",
      queued: "Publishing the week in ADP in the background.",
    });
    if (ack.ok) {
      setSheet(null);
      setPublishBaseline(draftStamp);
      setPollUntil(Date.now() + POLL_LIMIT_MS);
    }
  };

  const disabledTitle = enabled ? undefined : "ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE)";

  return (
    <div data-testid="adp-finalize" className="flex flex-col items-end gap-1.5">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!enabled || busy || isPending || toSave.length === 0}
          title={disabledTitle}
          onClick={() => setSheet("save")}
        >
          {summary.queued > 0 ? "Saving to ADP…" : `Save to ADP as drafts${toSave.length ? ` (${toSave.length})` : ""}`}
        </Button>
        <Button
          size="sm"
          disabled={!enabled || busy || isPending || drafted.length === 0}
          title={disabledTitle}
          onClick={() => setSheet("publish")}
        >
          {publishing ? "Publishing…" : "Publish week"}
        </Button>
      </div>
      {rows.length ? (
        <div className="flex flex-wrap justify-end gap-1.5 text-[11px]">
          {summary.queued ? <Badge variant="secondary">{summary.queued} saving</Badge> : null}
          {drafted.length ? <Badge variant="outline">{drafted.length} ADP drafts</Badge> : null}
          {summary.published ? <Badge variant="default">{summary.published} published</Badge> : null}
          {summary.failed ? <Badge variant="destructive">{summary.failed} failed</Badge> : null}
        </div>
      ) : null}
      {summary.errors.length ? (
        <ul className="max-w-sm text-right text-[11px] text-rose-600 dark:text-rose-400">
          {[...new Set(summary.errors)].slice(0, 3).map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}

      <Sheet open={sheet != null} onOpenChange={(o) => !o && setSheet(null)}>
        <SheetContent className="w-full max-w-md overflow-y-auto">
          {sheet === "save" ? (
            <>
              <SheetHeader>
                <SheetTitle>Save {toSave.length} shifts to ADP as drafts</SheetTitle>
                <SheetDescription>
                  Creates these in ADP Team Schedule as drafts for the week of {dayLabel(weekStart)}.
                  Employees don&apos;t see drafts until you publish the week. Takes about 20–40 s per
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
                <SheetTitle>Publish the week of {dayLabel(weekStart)}</SheetTitle>
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
                  {isPending ? "Starting…" : "Publish week"}
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
