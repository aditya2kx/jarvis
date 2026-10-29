"use client";

import { ChevronRightIcon, PlusIcon, Undo2Icon } from "lucide-react";
import { ShiftPersonPicker } from "@/components/labor/ShiftPersonPicker";
import { StagedBadge } from "@/components/labor/StagedBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClockMin } from "@/lib/labor/coverage-model";
import { shiftKey, timeText, type ShiftChange } from "@/lib/labor/adp-changes";
import type { DraftShift } from "@/lib/labor/shift-draft";
import { cn } from "@/lib/utils";

const OPEN_COLOR = LABOR_CHART_COLORS.openShift;
const KIND_LABEL = { open: "Open", mid: "Mid", close: "Close" } as const;

/** One ADP open shift on a day; `fill` = who the draft suggests (or the operator picked), null = nobody fits. */
export type DayOpenShift = { slot: string; date: string; startMin: number; endMin: number; hours: number; fill: DraftShift | null };

/** A selected open shift ready to assign: the person is whoever the picker shows now. */
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

/** "Will delete" / "Will change to 9 AM–1 PM" for an open shift with a staged change. */
function stagedText(c: ShiftChange): string {
  return c.kind === "open-edit" ? `Will change to ${timeText(c.to)}` : c.kind === "open-delete" ? "Will delete" : "";
}

/**
 * The selected day's ADP open shifts: tick the ones to fill and choose who takes each, change
 * their times, delete them, or add more. Everything waits in the Changes to ADP tray.
 */
export function OpenShiftDayList({
  dayLabel,
  shifts,
  adds,
  selected,
  staged,
  showSuggestions,
  onToggle,
  onPick,
  onAdd,
  onOpen,
  onUndo,
}: {
  dayLabel: string;
  shifts: DayOpenShift[];
  /** Open shifts staged to be added on this day. */
  adds: Extract<ShiftChange, { kind: "open-add" }>[];
  selected: ReadonlySet<string>;
  /** Staged edit / delete for an open shift, by `shiftKey`. */
  staged: (key: string) => ShiftChange | undefined;
  /** Off: pickers show "Choose who" instead of the draft's suggestion (a shift already ticked keeps its person). */
  showSuggestions: boolean;
  onToggle: (slots: string[], on: boolean) => void;
  /** A person, or undefined = back to the suggestion. */
  onPick: (slot: string, employee: string | undefined) => void;
  onAdd: () => void;
  /** The open shift's sheet: assign, change its times or delete it on one screen. */
  onOpen: (shift: DayOpenShift) => void;
  onUndo: (key: string) => void;
}) {
  const add = (
    <Button size="xs" variant="outline" onClick={onAdd}>
      <PlusIcon /> Add open shift
    </Button>
  );
  if (!shifts.length && !adds.length) {
    return (
      <div
        data-testid="open-shift-assign"
        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground"
      >
        <span>No open shifts in ADP — {dayLabel}</span>
        {add}
      </div>
    );
  }
  const whoOf = (s: DayOpenShift) =>
    s.fill?.employee && (showSuggestions || s.fill.picked || selected.has(s.slot)) ? s.fill.employee : null;
  const choosable = shifts.filter((s) => whoOf(s) && !staged(`open|${s.slot}`));
  const allOn = choosable.length > 0 && choosable.every((s) => selected.has(s.slot));
  return (
    <div
      data-testid="open-shift-assign"
      className="flex flex-col gap-2 rounded-lg border px-3 py-3"
      style={{ borderColor: `${OPEN_COLOR}55`, backgroundColor: `${OPEN_COLOR}0a` }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="text-sm font-medium text-foreground">
            {shifts.length ? "Open shifts in ADP" : "Open shifts"} — {dayLabel}
          </h4>
          <p className="text-xs text-muted-foreground">
            {shifts.length
              ? "Tick the ones to fill and choose who takes each. Your changes carry across days and go to ADP together."
              : "None in ADP yet. These go to ADP with your other changes."}
          </p>
        </div>
        <span className="flex items-center gap-2">
          {choosable.length > 1 ? (
            <Button
              variant="link"
              size="xs"
              className="h-6 px-0 text-xs"
              onClick={() => onToggle(choosable.map((s) => s.slot), !allOn)}
            >
              {allOn ? "Clear this day" : "Select all"}
            </Button>
          ) : null}
          {add}
        </span>
      </div>
      <ul className="flex flex-col divide-y rounded-md border bg-background">
        {shifts.map((s) => {
          const change = staged(shiftKey({ kind: "open-delete", slot: s.slot, date: s.date, startMin: s.startMin, endMin: s.endMin }));
          const on = selected.has(s.slot) && !change;
          const who = whoOf(s);
          return (
            <li
              key={s.slot}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 transition-colors",
                (on || change) && "bg-primary/5",
              )}
            >
              <SelectBox
                checked={on}
                disabled={!who || change != null}
                label={`Select the ${time(s)} open shift`}
                onChange={(next) => onToggle([s.slot], next)}
              />
              <span className="flex min-w-36 items-center gap-2">
                <Badge variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">
                  {KIND_LABEL[kindOf(s)]}
                </Badge>
                <span className={cn("text-sm tabular-nums", change?.kind === "open-delete" && "line-through opacity-60")}>{time(s)}</span>
                <span className="text-xs tabular-nums text-muted-foreground">{s.hours.toFixed(1)}h</span>
              </span>
              <span className="min-w-0 flex-1 basis-48">
                {change ? (
                  <span className="flex items-center gap-1.5">
                    <StagedBadge remove={change.kind === "open-delete"}>{stagedText(change)}</StagedBadge>
                    <Button size="icon-sm" variant="ghost" aria-label="Undo" onClick={() => onUndo(shiftKey(change))}>
                      <Undo2Icon />
                    </Button>
                  </span>
                ) : s.fill?.employee ? (
                  <ShiftPersonPicker
                    shift={s.fill}
                    hideSuggestion={!who}
                    onPick={(employee) => onPick(s.slot, employee ?? undefined)}
                  />
                ) : (
                  <span className="text-xs text-muted-foreground">Nobody fits your rules</span>
                )}
              </span>
              {change ? null : (
                <Button size="xs" variant="outline" aria-label={`Change the ${time(s)} open shift`} onClick={() => onOpen(s)}>
                  Change
                  <ChevronRightIcon aria-hidden data-affordance="opens" />
                </Button>
              )}
            </li>
          );
        })}
        {adds.map((c) => (
          <li key={shiftKey(c)} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 bg-primary/5 px-3 py-2">
            <span className="size-4 shrink-0" aria-hidden />
            <span className="flex min-w-36 items-center gap-2">
              <Badge variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">
                {KIND_LABEL[kindOf(c)]}
              </Badge>
              <span className="text-sm tabular-nums">{time(c)}</span>
              <span className="text-xs tabular-nums text-muted-foreground">{((c.endMin - c.startMin) / 60).toFixed(1)}h</span>
            </span>
            <span className="flex min-w-0 flex-1 basis-48 items-center gap-1.5">
              <StagedBadge>{c.count > 1 ? `Will add ${c.count}` : "Will add"}</StagedBadge>
              <Button size="icon-sm" variant="ghost" aria-label={`Undo adding the ${time(c)} open shift`} onClick={() => onUndo(shiftKey(c))}>
                <Undo2Icon />
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
