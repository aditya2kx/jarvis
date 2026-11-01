"use client";

import { useCallback, useEffect, useState } from "react";
import { schedulePushStatusAction, sendPublishMessageAction, type ScheduleWriteRun } from "@/app/labor/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
const graceDeadline = () => Date.now() + GRACE_MS;

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
const listLabel = (weeks: string[]) =>
  weeks.length > 1
    ? `${weeks.slice(0, -1).map(weekRangeLabel).join(", ")} and ${weekRangeLabel(weeks.at(-1)!)}`
    : weeks.map(weekRangeLabel).join("");

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
            {list.map((c, i) => (
              <li
                key={`${c.kind}-${"employee" in c ? c.employee : "open"}-${c.startMin}-${c.endMin}-${i}`}
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

/** One bordered, ≥44px row per week; a row with nothing to pick is shown but can't be checked. */
function WeekPicker({
  weeks,
  picked,
  onToggle,
  detail,
}: {
  weeks: string[];
  picked: Set<string>;
  onToggle: (week: string, on: boolean) => void;
  detail: (week: string) => { text: string; disabled?: boolean };
}) {
  return (
    <fieldset className="flex flex-col gap-1.5 px-4">
      <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Weeks</legend>
      {weeks.map((w) => {
        const { text, disabled } = detail(w);
        return (
          <label
            key={w}
            data-testid={`week-${w}`}
            className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted/50 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/50 has-[[data-disabled]]:cursor-not-allowed has-[[data-disabled]]:opacity-60"
          >
            <Checkbox
              checked={picked.has(w) && !disabled}
              disabled={disabled}
              onCheckedChange={(on) => onToggle(w, on === true)}
              aria-label={`Week of ${weekRangeLabel(w)}`}
            />
            <span className="font-medium">{weekRangeLabel(w)}</span>
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">{text}</span>
          </label>
        );
      })}
    </fieldset>
  );
}

export type SuggestedWeek = {
  weekStart: string;
  /** Suggested shifts ADP doesn't have yet, and ADP drafts to publish — minus what's already in the tray. */
  changes: ShiftChange[];
};

/**
 * The suggested weeks (Issue #337) go to ADP through the one "Changes to ADP" tray (Issue #434):
 * this card only adds their shifts to the tray, and sends one ClickUp message for the weeks picked.
 */
export function AdpScheduleFinalize({
  weekStart,
  weeks,
  onStage,
  enabled,
}: {
  /** The week of the selected day; the card's status line is about it. */
  weekStart: string;
  /** Every upcoming week with a suggested schedule, ascending (includes `weekStart`). */
  weeks: SuggestedWeek[];
  onStage: (changes: ShiftChange[]) => void;
  /** CONSOLE_ADP_SCHEDULE_WRITE: gates the ClickUp message (the tray gates Apply itself). */
  enabled: boolean;
}) {
  const [running, setRunning] = useState<ScheduleWriteRun | null>(null);
  // Right after a click the Cloud Run execution may not be listed yet; keep polling meanwhile.
  const [graceUntil, setGraceUntil] = useState(0);
  const [sentTo, setSentTo] = useState<"dm" | "channel" | null>(null);
  const [sheet, setSheet] = useState<"add" | "message" | null>(null);
  // One week selection for both sheets: the weeks just added are the weeks to announce.
  const [picked, setPicked] = useState<Set<string>>(() => new Set([weekStart]));
  const [pickedFor, setPickedFor] = useState(weekStart);
  if (pickedFor !== weekStart) {
    setPickedFor(weekStart);
    if (picked.size <= 1) setPicked(new Set([weekStart]));
  }
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

  const byWeek = new Map(weeks.map((w) => [w.weekStart, w.changes]));
  const allWeeks = weeks.map((w) => w.weekStart);
  const changes = byWeek.get(weekStart) ?? [];
  const otherWeeksWithChanges = weeks.filter((w) => w.weekStart !== weekStart && w.changes.length).length;
  const addable = allWeeks.filter((w) => byWeek.get(w)?.length);
  const addWeeks = addable.filter((w) => picked.has(w));
  const addChanges = addWeeks.flatMap((w) => byWeek.get(w) ?? []);
  const messageWeeks = allWeeks.filter((w) => picked.has(w));

  const toggle = (week: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(week);
      else next.delete(week);
      return next;
    });

  const openAdd = () => {
    // Nothing picked can be added (e.g. this week is all in ADP): start from the first week that has suggestions.
    if (!addable.some((w) => picked.has(w)) && addable.length) setPicked(new Set([addable[0]!]));
    setSheet("add");
  };

  const sendMessage = async (to: "dm" | "channel") => {
    const ack = await run(() => sendPublishMessageAction(messageWeeks, to), {
      saving: "Starting…",
      queued:
        to === "channel"
          ? "Posting to Shift Coverage in the background."
          : "Sending your draft to #jarvis-drafts in the background.",
    });
    if (ack.ok) {
      setSheet(null);
      setSentTo(to);
      setGraceUntil(graceDeadline());
    }
  };

  const range = weekRangeLabel(weekStart);
  const adds = changes.filter((c) => c.kind !== "shift-publish");
  const publishes = changes.filter((c) => c.kind === "shift-publish");
  const addCount = adds.reduce((n, c) => n + shiftsIn(c), 0);
  const openCount = adds.reduce((n, c) => n + (c.kind === "open-add" ? c.count : 0), 0);
  const lineCount = changes.length;

  const sheetAdds = addChanges.filter((c) => c.kind !== "shift-publish");
  const sheetAddCount = sheetAdds.reduce((n, c) => n + shiftsIn(c), 0);
  const sheetPublishes = addChanges.length - sheetAdds.length;

  return (
    <div
      data-testid="adp-finalize"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 px-3 py-2.5"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h4 className="text-sm font-medium text-foreground">Suggested schedule for {range}</h4>
        <p data-testid="adp-finalize-status" className="text-xs text-muted-foreground">
          {[
            lineCount
              ? [
                  addCount
                    ? `${plural(addCount, "suggested shift")} (${hoursOf(adds).toFixed(1)}h${openCount ? `, ${openCount} open` : ""}) ${addCount === 1 ? "isn't" : "aren't"} in ADP yet.`
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
              : "Everything suggested for this week is in ADP or in your changes.",
            otherWeeksWithChanges
              ? `${plural(otherWeeksWithChanges, "other week")} ${otherWeeksWithChanges === 1 ? "has" : "have"} suggestions too.`
              : "",
          ]
            .filter(Boolean)
            .join(" ")}
        </p>
        {messaging ? (
          <p className="text-xs text-muted-foreground">
            {toChannel
              ? "Re-reading open shifts from ADP, then posting to Shift Coverage (about 3 min)."
              : "Re-reading open shifts from ADP, then sending your draft to #jarvis-drafts (about 3 min)."}{" "}
            Safe to leave or reload this page.
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={addable.length === 0} onClick={openAdd}>
          Review &amp; add to changes
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!enabled || messaging || isPending}
          title={enabled ? "A draft for you, or a post to Shift Coverage" : "ADP schedule writes are off (CONSOLE_ADP_SCHEDULE_WRITE)"}
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
                <SheetTitle>Suggested shifts</SheetTitle>
                <SheetDescription>
                  {addChanges.length
                    ? `To add: ${[
                        sheetAddCount ? `${plural(sheetAddCount, "new shift")} (${hoursOf(sheetAdds).toFixed(1)}h)` : "",
                        sheetPublishes ? `${plural(sheetPublishes, "ADP draft")} to publish` : "",
                      ]
                        .filter(Boolean)
                        .join(", ")}. `
                    : "Pick the weeks to add. "}
                  Nothing changes in ADP until you press Apply to ADP; then each shift goes live and staff
                  see it. You can undo any line before that.
                </SheetDescription>
              </SheetHeader>
              <WeekPicker
                weeks={allWeeks}
                picked={picked}
                onToggle={toggle}
                detail={(w) => {
                  const list = byWeek.get(w) ?? [];
                  const n = list.reduce((a, c) => a + shiftsIn(c), 0);
                  return list.length
                    ? { text: `${plural(n, "shift")} · ${hoursOf(list).toFixed(1)}h` }
                    : { text: "In ADP or your changes", disabled: true };
                }}
              />
              <ChangeList changes={addChanges} />
              <SheetFooter>
                <Button
                  disabled={addChanges.length === 0}
                  onClick={() => {
                    onStage(addChanges);
                    setPicked(new Set(addWeeks));
                    setSheet(null);
                  }}
                >
                  Add {plural(addChanges.length, "line")} to changes
                  {addWeeks.length > 1 ? ` · ${addWeeks.length} weeks` : ""}
                </Button>
                <SheetClose render={<Button variant="outline">Cancel</Button>} />
              </SheetFooter>
            </>
          ) : sheet === "message" ? (
            <>
              <SheetHeader>
                <SheetTitle>Send the schedule message</SheetTitle>
                <SheetDescription>
                  One note for the weeks you pick: how many open shifts are left, and one line tagging each
                  person with no unavailability marked in a picked week. It re-reads open shifts from ADP (about
                  3 min), then types the note in ClickUp so the @mentions are real. Your draft goes to
                  #jarvis-drafts with a &quot;copy and post&quot; header; Shift Coverage starts at @everyone
                  and notifies the team.
                </SheetDescription>
              </SheetHeader>
              <WeekPicker weeks={allWeeks} picked={picked} onToggle={toggle} detail={() => ({ text: "" })} />
              <p className="px-4 text-xs text-muted-foreground">
                {messageWeeks.length ? `Covers ${listLabel(messageWeeks)}.` : "Pick at least one week."}
              </p>
              <SheetFooter>
                <Button onClick={() => void sendMessage("dm")} disabled={isPending || !messageWeeks.length}>
                  {isPending ? "Starting…" : "Send me a draft"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void sendMessage("channel")}
                  disabled={isPending || !messageWeeks.length}
                >
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
