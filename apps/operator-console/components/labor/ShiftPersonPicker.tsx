"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClockMin } from "@/lib/labor/coverage-model";
import type { DraftShift, ShiftOption } from "@/lib/labor/shift-draft";

const DRAFT_COLOR = LABOR_CHART_COLORS.draftShift;

/** Select value for "leave it open" (Select treats null as no selection). */
const OPEN = "__open__";

const fmtH = (h: number) => `${Number(h.toFixed(1))}h`;

function optionMeta(o: ShiftOption, shift: DraftShift): string {
  const parts: string[] = [];
  if (o.startMin !== shift.startMin || o.endMin !== shift.endMin) {
    parts.push(`${formatClockMin(o.startMin)}–${formatClockMin(o.endMin)} · ${fmtH(o.hours)}`);
  }
  if (o.weekHours != null && o.cap != null) {
    parts.push(`${fmtH(o.weekHours)} of ${fmtH(o.cap)}${o.target != null ? " target" : ""} this week`);
  }
  if (o.trimmed) parts.push("shortened to availability");
  return parts.join(" · ");
}

/**
 * Who takes a drafted shift: the suggestion, anyone else who fits (their own times and week so far),
 * or leave it open. Choosing re-drafts the week around the pick.
 */
export function ShiftPersonPicker({
  shift,
  onPick,
}: {
  shift: DraftShift;
  /** A person, null = leave open, undefined = back to the suggestion. */
  onPick: (employee: string | null | undefined) => void;
}) {
  const options = shift.options ?? [];
  const people = options.filter((o) => o.employee != null);
  const canLeaveOpen = options.some((o) => o.employee == null);
  const others = options.length - 1;
  const value = shift.employee ?? OPEN;
  const time = `${formatClockMin(shift.startMin)}–${formatClockMin(shift.endMin)}`;

  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <Select
        value={value}
        onValueChange={(v) => {
          if (v == null || v === value) return;
          onPick(v === OPEN ? null : String(v));
        }}
      >
        <SelectTrigger
          size="sm"
          aria-label={`Who takes the ${time} shift`}
          className="h-9 max-w-full min-w-0 gap-2 sm:h-7"
          style={shift.picked ? { borderColor: `${DRAFT_COLOR}99` } : undefined}
        >
          <SelectValue>
            {(v: string) => (
              <span className="flex min-w-0 items-center gap-2">
                <span className={v === OPEN ? "truncate text-muted-foreground" : "truncate font-medium"}>
                  {v === OPEN ? "Leave open" : v}
                </span>
                {!shift.picked && others > 0 ? (
                  <span className="shrink-0 text-xs font-normal text-muted-foreground">
                    <span className="sm:hidden">+{others}</span>
                    <span className="hidden sm:inline">
                      {others} {others === 1 ? "other option" : "other options"}
                    </span>
                  </span>
                ) : null}
              </span>
            )}
          </SelectValue>
        </SelectTrigger>
        <SelectContent
          align="start"
          alignItemWithTrigger={false}
          className="w-auto min-w-72 max-w-[min(26rem,calc(100vw-2rem))]"
        >
          <SelectGroup>
            <SelectLabel>Who takes {time}</SelectLabel>
            {people.map((o, i) => (
              <SelectItem key={o.employee} value={o.employee!} className="py-1.5">
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate font-medium">{o.employee}</span>
                    {i === 0 && !shift.picked ? (
                      <Badge variant="secondary" className="h-4 px-1.5 text-[11px] font-normal">
                        Suggested
                      </Badge>
                    ) : null}
                  </span>
                  {optionMeta(o, shift) ? (
                    <span className="truncate text-xs text-muted-foreground">{optionMeta(o, shift)}</span>
                  ) : null}
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
          {canLeaveOpen ? (
            <>
              <SelectSeparator />
              <SelectItem value={OPEN} className="py-1.5">
                <span className="flex flex-col gap-0.5">
                  <span className="font-medium">Leave open</span>
                  <span className="text-xs text-muted-foreground">Posts as an open shift</span>
                </span>
              </SelectItem>
            </>
          ) : null}
        </SelectContent>
      </Select>
      {shift.picked ? (
        <>
          <Badge
            variant="outline"
            className="h-5 px-1.5 text-[11px] font-normal"
            style={{ borderColor: DRAFT_COLOR, color: DRAFT_COLOR }}
          >
            Your pick
          </Badge>
          <Button variant="link" size="xs" className="h-6 px-0 text-xs" onClick={() => onPick(undefined)}>
            Reset
          </Button>
        </>
      ) : null}
    </span>
  );
}
