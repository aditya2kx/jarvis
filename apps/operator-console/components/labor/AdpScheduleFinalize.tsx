"use client";

import { useCallback, useEffect, useState } from "react";
import { schedulePushStatusAction, sendPublishMessageAction, type ScheduleWriteRun } from "@/app/labor/actions";
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
import { timeText, type ShiftChange } from "@/lib/labor/adp-changes";

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

const shiftsIn = (c: ShiftChange) => (c.kind === "open-add" ? c.count : 1);
const hoursOf = (changes: ShiftChange[]) =>
  changes.reduce((a, c) => a + (shiftsIn(c) * (c.endMin - c.startMin)) / 60, 0);
const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

function ChangeList({ changes }: { changes: ShiftChange[] }) {
  const byDay = new Map<string, ShiftChange[]>();
  for (const c of changes) byDay.set(c.date, [...(byDay.get(c.date) ?? []), c]);
  return (
    <div className="flex flex-col gap-3 px-4">
      {[...byDay].map(([date, list]) => (
        <div key={date} className="flex flex-col gap-1">
          <p className="flex justify-between text-xs font-medium text-muted-foreground">
            <span>{dayLabel(date)}</span>
            <span className="tabular-nums">
              {plural(list.reduce((n, c) => n + shiftsIn(c), 0), "shift")} · {hoursOf(list).toFixed(1)}h
            </span>
          </p>
          <ul className="flex flex-col divide-y rounded-md border">
            {list.map((c) => (
              <li
                key={`${c.kind}-${"employee" in c ? c.employee : "open"}-${c.startMin}-${c.endMin}`}
                className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm"
              >
                <span className="tabular-nums">{timeText(c)}</span>
                <span className="flex min-w-0 items-center gap-2">
                  {c.kind === "open-add" ? (
                    <Badge variant="outline" className="font-normal">
                      {c.count > 1 ? `${c.count} open shifts` : "Open shift"}
                    </Badge>
                  ) : (
                    <span className="truncate font-medium">{"employee" in c ? c.employee : ""}</span>
                  )}
                  {c.kind === "shift-publish" ? (
                    <Badge variant="secondary" className="font-normal">
                      ADP draft
                    </Badge>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * The suggested week (Issue #337) goes to ADP through the one "Changes to ADP" tray (Issue #434):
 * this card only adds its shifts to the tray, and sends the week's ClickUp message.
 */
export function AdpScheduleFinalize({
  weekStart,
  changes,
  onStage,
  enabled,
}: {
  weekStart: string;
  /** Suggested shifts ADP doesn't have yet, and ADP drafts to publish — minus what's already in the tray. */
  changes: ShiftChange[];
  onStage: (changes: ShiftChange[]) => void;
  /** CONSOLE_ADP_SCHEDULE_WRITE: gates the ClickUp message (the tray gates Apply itself). */
  enabled: boolean;
}) {
  const [running, setRunning] = useState<ScheduleWriteRun | null>(null);
  // Right after a click the Cloud Run execution may not be listed yet; keep polling meanwhile.
  const [graceUntil, setGraceUntil] = useState(0);
  const [sentTo, setSentTo] = useState<"dm" | "channel" | null>(null);
  const [sheet, setSheet] = useState<"add" | "message" | null>(null);
  const { isPending, run } = useConsoleAction();

  const refresh = useCallback(async () => {
    // A refused or failed status read (network, read-only preview) must not throw.
    const ack = await schedulePushStatusAction().catch(() => null);
    if (ack?.ok && ack.data) setRunning(ack.data.running);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const polling = running != null || graceUntil > 0;
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => {
      if (graceUntil && Date.now() > graceUntil) setGraceUntil(0);
      void refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [polling, graceUntil, refresh]);

  const messaging = running?.mode === "dm" || (graceUntil > 0 && sentTo != null);
  const toChannel = running?.mode === "dm" ? running.to === "channel" : sentTo === "channel";

  const sendMessage = async (to: "dm" | "channel") => {
    const ack = await run(() => sendPublishMessageAction(weekStart, to), {
      saving: "Starting…",
      queued:
        to === "channel"
          ? "Posting to Shift Coverage in the background."
          : "Sending you the ClickUp DM in the background.",
    });
    if (ack.ok) {
      setSheet(null);
      setSentTo(to);
      setGraceUntil(Date.now() + GRACE_MS);
    }
  };

  const range = weekRangeLabel(weekStart);
  const adds = changes.filter((c) => c.kind !== "shift-publish");
  const publishes = changes.filter((c) => c.kind === "shift-publish");
  const addCount = adds.reduce((n, c) => n + shiftsIn(c), 0);
  const openCount = adds.reduce((n, c) => n + (c.kind === "open-add" ? c.count : 0), 0);
  const lineCount = changes.length;

  return (
    <div
      data-testid="adp-finalize"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 px-3 py-2.5"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h4 className="text-sm font-medium text-foreground">Suggested schedule for {range}</h4>
        <p data-testid="adp-finalize-status" className="text-xs text-muted-foreground">
          {lineCount
            ? [
                addCount
                  ? `${plural(addCount, "suggested shift")} (${addCount - openCount} assigned, ${openCount} open, ${hoursOf(adds).toFixed(1)}h) ${addCount === 1 ? "isn't" : "aren't"} in ADP yet.`
                  : "",
                publishes.length === 1
                  ? "1 shift in ADP is still a draft staff can't see."
                  : publishes.length
                    ? `${publishes.length} shifts in ADP are still drafts staff can't see.`
                    : "",
                "Add them to your changes, then Apply to ADP.",
              ]
                .filter(Boolean)
                .join(" ")
            : "Everything suggested for this week is in ADP or in your changes."}
        </p>
        {messaging ? (
          <p className="text-xs text-muted-foreground">
            {toChannel
              ? "Re-reading open shifts from ADP, then posting to Shift Coverage (about 3 min)."
              : "Re-reading open shifts from ADP, then sending you the ClickUp DM (about 3 min)."}{" "}
            Safe to leave or reload this page.
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={lineCount === 0} onClick={() => setSheet("add")}>
          Review &amp; add to changes
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!enabled || messaging || isPending}
          title={enabled ? "DM yourself, or post it to Shift Coverage" : "ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE)"}
          onClick={() => setSheet("message")}
        >
          {messaging ? (toChannel ? "Posting to Shift Coverage…" : "Sending ClickUp message…") : "Send ClickUp message"}
        </Button>
      </div>

      <Sheet open={sheet != null} onOpenChange={(o) => !o && setSheet(null)}>
        <SheetContent className="w-full max-w-md overflow-y-auto">
          {sheet === "add" ? (
            <>
              <SheetHeader>
                <SheetTitle>Add the {range} suggestions to your changes</SheetTitle>
                <SheetDescription>
                  {`To add: ${[
                    addCount ? `${plural(addCount, "new shift")} (${hoursOf(adds).toFixed(1)}h)` : "",
                    publishes.length ? `${plural(publishes.length, "ADP draft")} to publish` : "",
                  ]
                    .filter(Boolean)
                    .join(", ")}. `}
                  Nothing changes in ADP until you press Apply to ADP; then each shift goes live and staff
                  see it. You can undo any line before that.
                </SheetDescription>
              </SheetHeader>
              <ChangeList changes={changes} />
              <SheetFooter>
                <Button
                  onClick={() => {
                    onStage(changes);
                    setSheet(null);
                  }}
                >
                  Add {plural(lineCount, "line")} to changes
                </Button>
                <SheetClose render={<Button variant="outline">Cancel</Button>} />
              </SheetFooter>
            </>
          ) : sheet === "message" ? (
            <>
              <SheetHeader>
                <SheetTitle>Send the {range} schedule message</SheetTitle>
                <SheetDescription>
                  Re-reads this week&apos;s open shifts from ADP (about 3 min), then sends the note. A DM
                  keeps the &quot;copy and post&quot; header; Shift Coverage starts at @everyone and notifies
                  the team.
                </SheetDescription>
              </SheetHeader>
              <SheetFooter>
                <Button onClick={() => void sendMessage("dm")} disabled={isPending}>
                  {isPending ? "Starting…" : "Send me a DM"}
                </Button>
                <Button variant="outline" onClick={() => void sendMessage("channel")} disabled={isPending}>
                  Post to Shift Coverage
                </Button>
                <SheetClose render={<Button variant="ghost">Cancel</Button>} />
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
