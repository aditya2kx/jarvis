"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check,
  ChevronDown,
  GripVertical,
  History,
  Plus,
  RotateCcw,
  X,
} from "lucide-react";
import { saveScheduleRulesAction } from "@/app/labor/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ALL_DAYS,
  DAY_NAMES,
  dayRuleConflicts,
  daysLabel,
  minToTime,
  moveItem,
  uncoveredWindows,
  timeToMin,
  type DayRule,
  type DeliveryMode,
  type RulesVersion,
  type ScheduleRules,
  type StaffingBasics,
  type StaffRule,
  type StaffRuleKind,
} from "@/lib/labor/schedule-inputs";
import { cn } from "@/lib/utils";

const DELIVERY: { value: DeliveryMode; label: string }[] = [
  { value: "any", label: "Delivery days too" },
  { value: "skip", label: "Except delivery days" },
  { value: "only", label: "Only delivery days" },
];

const chip = (on: boolean) =>
  cn(
    "h-7 rounded-md border text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    on
      ? "border-primary bg-primary/15 text-foreground"
      : "border-border text-muted-foreground hover:bg-muted/50 hover:text-foreground",
  );

type RowRule = Omit<DayRule, "people"> & { people: number | null };

/** One "else" row per uncovered window: what the order-based need covers today, shaped like a rule. */
function elseRows(dayRules: DayRule[], staffing: StaffingBasics): RowRule[] {
  const ids = new Set(dayRules.map((r) => r.id));
  const out = uncoveredWindows(dayRules, staffing);
  return out.flatMap((u) =>
    u.groups.flatMap((g) =>
      g.windows.map(([fromMin, toMin]): RowRule => {
        const delivery: DeliveryMode = u.deliveryDays
          ? "only"
          : out.length > 1
            ? "skip"
            : "any";
        let id = `else-${delivery}-${g.days.join("")}-${fromMin}-${toMin}`;
        while (ids.has(id)) id += "~";
        return { id, days: g.days, delivery, fromMin, toMin, people: null };
      }),
    ),
  );
}

/** Drag (grip) or ↑/↓ on the focused grip to reorder a list of rows. */
function useReorder<T>(items: T[], onReorder: (next: T[]) => void) {
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const end = () => {
    setDrag(null);
    setOver(null);
  };
  const move = (from: number, to: number) => {
    if (to >= 0 && to < items.length && from !== to)
      onReorder(moveItem(items, from, to));
  };
  return {
    isDropTarget: (i: number) => drag != null && over === i && drag !== i,
    handle: (i: number) => ({
      draggable: true,
      onDragStart: (e: React.DragEvent<HTMLElement>) => {
        setDrag(i);
        e.dataTransfer.effectAllowed = "move";
        const row = e.currentTarget.closest("[data-reorder-row]");
        if (row) e.dataTransfer.setDragImage(row, 12, 14);
      },
      onDragEnd: end,
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        move(i, i + (e.key === "ArrowUp" ? -1 : 1));
      },
    }),
    row: (i: number) => ({
      "data-reorder-row": "",
      onDragOver: (e: React.DragEvent) => {
        if (drag == null) return;
        e.preventDefault();
        setOver(i);
      },
      onDrop: (e: React.DragEvent) => {
        if (drag == null) return;
        e.preventDefault();
        move(drag, i);
        end();
      },
    }),
  };
}

function DragHandle({
  label,
  ...props
}: { label: string } & React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      aria-label={label}
      title="Drag to reorder — or focus and press ↑ / ↓"
      className="flex h-7 w-5 shrink-0 cursor-grab items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
      {...props}
    >
      <GripVertical className="size-3.5" />
    </button>
  );
}

function DayRuleRow({
  rule: r,
  label,
  ghost,
  autoHint,
  onPatch,
  onRemove,
  handle,
  rowProps,
  dropTarget,
  children,
}: {
  handle?: React.ReactNode;
  rowProps?: Record<string, unknown>;
  dropTarget?: boolean;
  rule: RowRule;
  label: string;
  ghost: boolean;
  autoHint: string;
  onPatch: (patch: Partial<DayRule>) => void;
  onRemove?: () => void;
  children?: React.ReactNode;
}) {
  const clashing = Boolean(
    children && (Array.isArray(children) ? children.length : true),
  );
  return (
    <div
      {...rowProps}
      className={cn(
        "flex flex-col gap-1 rounded-md transition-shadow",
        clashing && "border border-destructive/50 bg-destructive/5 px-2 py-1.5",
        ghost && "border border-dashed border-border px-2 py-1.5",
        dropTarget && "ring-2 ring-primary/50",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {handle ?? <span className="w-5 shrink-0" aria-hidden />}
        <span
          className={cn(
            "w-8 text-right text-xs tabular-nums",
            ghost ? "font-medium text-foreground" : "text-muted-foreground",
          )}
        >
          {label}
        </span>
        <DayPicker days={r.days} onChange={(days) => onPatch({ days })} />
        <Select
          value={r.delivery}
          onValueChange={(v) => v && onPatch({ delivery: v as DeliveryMode })}
        >
          <SelectTrigger
            className="h-7 w-44 text-xs"
            aria-label="Delivery days"
          >
            <SelectValue>
              {(v: DeliveryMode) => DELIVERY.find((d) => d.value === v)?.label}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {DELIVERY.map((d) => (
              <SelectItem key={d.value} value={d.value}>
                {d.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <TimeInput
          value={r.fromMin}
          onChange={(v) => onPatch({ fromMin: v })}
          label="From"
        />
        <span className="text-xs text-muted-foreground">to</span>
        <TimeInput
          value={r.toMin}
          onChange={(v) => onPatch({ toMin: v })}
          label="To"
        />
        <Input
          type="number"
          min={0}
          value={r.people ?? ""}
          placeholder="auto"
          title={r.people == null ? autoHint : undefined}
          onChange={(e) =>
            e.target.value !== "" &&
            onPatch({ people: Math.max(0, Number(e.target.value)) })
          }
          className="h-7 w-16 text-xs tabular-nums"
          aria-label="People"
        />
        <span className="text-xs text-muted-foreground">people</span>
        {onRemove ? (
          <RemoveButton label="Remove day rule" onClick={onRemove} />
        ) : (
          <span className="w-7" aria-hidden />
        )}
      </div>
      {children}
    </div>
  );
}

/** Toggle any set of weekdays; the last selected day can't be turned off. */
function DayPicker({
  days,
  onChange,
}: {
  days: number[];
  onChange: (days: number[]) => void;
}) {
  const label = daysLabel(days);
  return (
    <div
      className="flex items-center gap-1"
      role="group"
      aria-label={`Days: ${label}`}
    >
      {DAY_NAMES.map((name, d) => {
        const on = days.includes(d);
        return (
          <button
            key={name}
            type="button"
            aria-pressed={on}
            aria-label={name}
            title={
              on && days.length === 1 ? "A rule needs at least one day" : name
            }
            onClick={() => {
              if (on && days.length === 1) return;
              onChange(on ? days.filter((x) => x !== d) : [...days, d].sort());
            }}
            className={cn(chip(on), "w-9 px-0 tabular-nums")}
          >
            {name.slice(0, 2)}
          </button>
        );
      })}
    </div>
  );
}

const KINDS: { value: StaffRuleKind; label: string; unit: string; initial: number }[] = [
  { value: "target_week_hours", label: "About … hours / week", unit: "h / week", initial: 30 },
  { value: "max_day_hours", label: "At most … hours / day", unit: "h / day · longest shift for this person", initial: 9 },
  { value: "min_shifts_per_week", label: "At least … shifts / week", unit: "shifts / week · replaces Everyone", initial: 2 },
  { value: "max_shifts_per_period", label: "At most … shifts / pay period", unit: "shifts / pay period", initial: 1 },
  { value: "last_day", label: "Last working day", unit: "not drafted after this day", initial: 0 },
];
const kindOrder = (k: StaffRuleKind) => KINDS.findIndex((x) => x.value === k);
const valueMax = (k: StaffRuleKind) => (k === "max_day_hours" ? 16 : k === "min_shifts_per_week" ? 7 : 80);

/** Staff rules per person (A–Z, rule types in a fixed order); each unassigned rule on its own. */
function staffGroups(rules: StaffRule[]): [string, StaffRule[]][] {
  const named = new Map<string, StaffRule[]>();
  const blank: [string, StaffRule[]][] = [];
  for (const r of rules) {
    if (!r.employee) blank.push(["", [r]]);
    else named.set(r.employee, [...(named.get(r.employee) ?? []), r]);
  }
  return [
    ...[...named.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([n, list]): [string, StaffRule[]] => [n, list.sort((x, y) => kindOrder(x.kind) - kindOrder(y.kind))]),
    ...blank,
  ];
}

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function savedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    timeZone: "America/Chicago",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function whoSaved(email: string): string {
  return email.split("@")[0] ?? email;
}

function clock(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${((h + 11) % 12) + 1}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`;
}

function staffingSummary(s: StaffingBasics): string {
  return `${s.ordersPerPerson} orders/person · min ${s.minPeople} · ${clock(s.openMin)}–${clock(s.closeMin)} · ${s.minShiftMin / 60}–${s.maxShiftMin / 60}h shifts · ≤${s.maxWeekHours}h/week each${s.minWeekShifts ? ` · ≥${s.minWeekShifts} shifts/week each` : ""}${s.shiftTimes === "need" ? " · follow the need" : ""}${s.unpaidMealMin ? ` · ${s.unpaidMealMin}m unpaid meal over ${s.mealAfterMin / 60}h` : ""}`;
}

function rulesSummary(v: RulesVersion): string {
  const n = (k: number, one: string) => `${k} ${one}${k === 1 ? "" : "s"}`;
  const staffing = v.savedStaffing
    ? staffingSummary(v.rules.staffing)
    : "before staffing basics";
  return `${staffing} · ${n(v.rules.staffRules.length, "staff rule")} · ${n(v.rules.dayRules.length, "day rule")}`;
}

function NumberField({
  label,
  unit,
  value,
  step = 1,
  min,
  onChange,
}: {
  label: string;
  unit: string;
  value: number;
  step?: number;
  min: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5">
        <Input
          type="number"
          min={min}
          step={step}
          value={value}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v) && v >= min) onChange(v);
          }}
          className="h-7 w-16 text-xs tabular-nums"
        />
        <span className="text-xs text-muted-foreground">{unit}</span>
      </span>
    </label>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/80 bg-card/60 p-3">
      <div>
        <h4 className="text-xs font-medium text-foreground">{title}</h4>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {children}
    </div>
  );
}

function TimeInput({
  value,
  onChange,
  label,
}: {
  value: number;
  onChange: (min: number) => void;
  label: string;
}) {
  // While focused, mirror what the browser reports: mid-entry it reports "",
  // and forcing the saved time back would wipe the half-typed segments.
  const [typing, setTyping] = useState<string | null>(null);
  return (
    <Input
      type="time"
      value={typing ?? minToTime(value)}
      onFocus={() => setTyping(minToTime(value))}
      onBlur={() => setTyping(null)}
      onChange={(e) => {
        setTyping(e.target.value);
        const v = timeToMin(e.target.value);
        if (v != null) onChange(v);
      }}
      className="h-7 w-28 text-xs"
      aria-label={label}
    />
  );
}

function RemoveButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <Button size="icon-xs" variant="ghost" aria-label={label} onClick={onClick}>
      <X />
    </Button>
  );
}

export type WeekForecast = { weekStart: string; before: number | null; after: number };

function weekLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** "Wk of Oct 12  ~~243~~ → 221h  +11 vs goal" per future week — edits preview before saving. */
function HoursImpact({
  forecast,
  goal,
  dirty,
}: {
  forecast: WeekForecast[];
  goal?: number;
  dirty: boolean;
}) {
  if (!forecast.length) return null;
  const r = (n: number) => Math.round(n);
  const noEffect = dirty && forecast.every((f) => f.before == null || r(f.before) === r(f.after));
  return (
    <div className="flex flex-col gap-1">
    <div data-testid="hours-impact" className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
      <span className="text-muted-foreground">
        {dirty ? "Drafted hours — saved → with your edits" : "Drafted hours per week"}
        {goal != null ? ` (goal ${goal}h)` : ""}
      </span>
      {forecast.map((f) => {
        const changed = dirty && f.before != null && r(f.before) !== r(f.after);
        const delta = f.before != null ? r(f.after) - r(f.before) : 0;
        const vsGoal = goal != null ? r(f.after) - goal : null;
        return (
          <span key={f.weekStart} className="inline-flex items-baseline gap-1 tabular-nums">
            <span className="text-muted-foreground">Wk of {weekLabel(f.weekStart)}</span>
            {changed ? (
              <>
                <span className="text-muted-foreground line-through">{r(f.before!)}</span>
                <span className="text-muted-foreground">→</span>
              </>
            ) : null}
            <span className="font-medium text-foreground">{r(f.after)}h</span>
            {changed ? (
              <span className={delta < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}>
                ({delta > 0 ? "+" : "−"}
                {Math.abs(delta)})
              </span>
            ) : null}
            {vsGoal != null ? (
              <span
                className={cn(
                  "rounded px-1 text-[11px]",
                  Math.abs(vsGoal) <= 2
                    ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                    : vsGoal > 0
                      ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {Math.abs(vsGoal) <= 2 ? "at goal" : vsGoal > 0 ? `${vsGoal}h over` : `${-vsGoal}h under`}
              </span>
            ) : null}
          </span>
        );
      })}
    </div>
    {noEffect ? (
      <p data-testid="hours-impact-none" className="text-xs text-muted-foreground">
        These edits don&apos;t change drafted hours yet — outside a rule, typical orders already need that many
        people, or nobody available can take the extra time.
      </p>
    ) : null}
    </div>
  );
}

export function ScheduleInputsPanel({
  rules,
  onChange,
  employees,
  deliveryDates,
  goalHoursWeek,
  dirty,
  onDiscard,
  savedVersion,
  history,
  forecast = [],
}: {
  rules: ScheduleRules;
  onChange: (next: ScheduleRules) => void;
  employees: string[];
  /** Upcoming frozen deliveries from the Inventory restock schedule (read-only here). */
  deliveryDates: string[];
  goalHoursWeek?: number;
  /** Rules differ from the saved version (edits preview in the draft until saved). */
  dirty: boolean;
  onDiscard: () => void;
  savedVersion: number;
  /** Saved versions, newest (live) first. */
  history: RulesVersion[];
  /** Drafted hours per future week: saved rules (before) vs these rules (after). */
  forecast?: WeekForecast[];
}) {
  const router = useRouter();
  const { run, isPending } = useConsoleAction();
  const [open, setOpen] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [note, setNote] = useState("");
  const live = history[0];
  const save = async () => {
    const ack = await run(
      () => saveScheduleRulesAction(rules, savedVersion, note),
      { saving: "Saving rules…" },
    );
    if (ack.ok) {
      setNote("");
      router.refresh();
    }
  };
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const conflicts = dayRuleConflicts(rules.dayRules);
  const ghosts = elseRows(rules.dayRules, rules.staffing);
  const dayOrder = useReorder(rules.dayRules, (dayRules) =>
    onChange({ ...rules, dayRules }),
  );
  const setDayRule = (id: string, patch: Partial<DayRule>) =>
    onChange({
      ...rules,
      dayRules: rules.dayRules.map((r) =>
        r.id === id ? { ...r, ...patch } : r,
      ),
    });
  const setStaffRule = (id: string, patch: Partial<StaffRule>) =>
    onChange({
      ...rules,
      staffRules: rules.staffRules.map((r) =>
        r.id === id ? { ...r, ...patch } : r,
      ),
    });
  const setStaffing = (patch: Partial<StaffingBasics>) =>
    onChange({ ...rules, staffing: { ...rules.staffing, ...patch } });

  const plural = (n: number, one: string, many = `${one}s`) =>
    `${n} ${n === 1 ? one : many}`;
  const summary = [
    `${rules.staffing.ordersPerPerson} orders/person`,
    goalHoursWeek != null ? `${goalHoursWeek}h/week cap` : null,
    plural(rules.staffRules.length, "staff rule"),
    plural(rules.dayRules.length, "day rule"),
    plural(deliveryDates.length, "upcoming delivery", "upcoming deliveries"),
  ].filter(Boolean);

  return (
    <div
      data-testid="schedule-inputs"
      className="rounded-lg border border-border"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            Scheduling rules
            {dirty ? (
              <Badge className="h-4 border-amber-500/40 bg-amber-500/15 px-1.5 text-[11px] font-normal text-amber-700 dark:text-amber-300">
                Unsaved changes
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="h-4 px-1.5 text-[11px] font-normal"
              >
                {live
                  ? `v${live.version} · ${whoSaved(live.createdBy)}, ${savedAt(live.createdAt)}`
                  : "Defaults — not saved yet"}
              </Badge>
            )}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {summary.join(" · ")}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <div className="grid gap-3 border-t border-border p-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <Section
              title="Staffing basics"
              hint="Needed people each hour = typical orders for that weekday and hour (median of the last 8 weeks) ÷ orders per person, rounded up — never below the minimum while staffed."
            >
              <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                <NumberField
                  label="Orders one person handles"
                  unit="/ hour"
                  step={0.5}
                  min={1}
                  value={rules.staffing.ordersPerPerson}
                  onChange={(v) => setStaffing({ ordersPerPerson: v })}
                />
                <NumberField
                  label="Minimum on the floor"
                  unit="people"
                  min={0}
                  value={rules.staffing.minPeople}
                  onChange={(v) => setStaffing({ minPeople: Math.round(v) })}
                />
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">
                    Staffed hours (incl. open/close duties)
                  </span>
                  <span className="flex items-center gap-1.5">
                    <TimeInput
                      value={rules.staffing.openMin}
                      onChange={(v) => setStaffing({ openMin: v })}
                      label="Staffed from"
                    />
                    <span className="text-xs text-muted-foreground">to</span>
                    <TimeInput
                      value={rules.staffing.closeMin}
                      onChange={(v) => setStaffing({ closeMin: v })}
                      label="Staffed until"
                    />
                  </span>
                </div>
                <NumberField
                  label="Shortest shift"
                  unit="hours"
                  step={0.5}
                  min={1}
                  value={rules.staffing.minShiftMin / 60}
                  onChange={(v) =>
                    setStaffing({ minShiftMin: Math.round(v * 60) })
                  }
                />
                <NumberField
                  label="Longest shift"
                  unit="hours"
                  step={0.5}
                  min={rules.staffing.minShiftMin / 60}
                  value={rules.staffing.maxShiftMin / 60}
                  onChange={(v) =>
                    setStaffing({ maxShiftMin: Math.round(v * 60) })
                  }
                />
                <NumberField
                  label="Handover overlap"
                  unit="min · on top of longest"
                  step={15}
                  min={0}
                  value={rules.staffing.handoverOverlapMin}
                  onChange={(v) =>
                    setStaffing({ handoverOverlapMin: Math.min(180, Math.round(v / 15) * 15) })
                  }
                />
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">Shift times</span>
                  <div
                    role="group"
                    aria-label="Shift times"
                    className="flex items-center gap-1 rounded-lg border border-border p-0.5"
                  >
                    {(
                      [
                        ["history", "Usual handover"],
                        ["need", "Follow the need"],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={rules.staffing.shiftTimes === value}
                        onClick={() => setStaffing({ shiftTimes: value })}
                        className={cn(
                          "h-6 rounded-md px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          rules.staffing.shiftTimes === value
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
              <p className="text-xs text-muted-foreground">
                {rules.staffing.shiftTimes === "need"
                  ? `Each shift starts when someone is first needed and runs until the need ends; stretches longer than ${rules.staffing.maxShiftMin / 60}h split into equal shifts.`
                  : "Openers, mids and closers fit the usual ADP blocks (handover about 2 PM), trimmed to when someone is needed. Longest shift applies to Follow the need."}
                {rules.staffing.handoverOverlapMin > 0
                  ? ` When someone leaves as someone else arrives, the newcomer starts ${rules.staffing.handoverOverlapMin} min earlier so a late arrival never leaves the floor short.`
                  : ""}
              </p>
            </Section>
          </div>

          <Section
            title="Weekly hours cap"
            hint="A ceiling, not a target: the draft never adds order-driven shifts past it. Set it in Weekly hours goal above."
          >
            <p className="text-sm font-medium tabular-nums text-foreground">
              {goalHoursWeek != null ? `${goalHoursWeek}h / week` : "Not set"}
            </p>
          </Section>

          <Section
            title="Frozen deliveries"
            hint="From the Inventory restock schedule — change them on the Inventory page. Day rules can target or skip these days."
          >
            {deliveryDates.length ? (
              <div className="flex flex-wrap gap-1.5">
                {deliveryDates.map((d) => (
                  <Badge key={d} variant="secondary" className="font-normal">
                    {dayLabel(d)}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No upcoming deliveries scheduled.
              </p>
            )}
          </Section>

          <div className="md:col-span-2">
            <Section
              title="Staff rules"
              hint="Coverage comes first: every needed shift goes to someone if anyone can take it. Who gets it: an hour target first (until it is reached — never past it, and it replaces the cap), then anyone below the weekly minimum shifts, then regulars (most recent hours) up to the weekly cap. “At most … hours / day” sets that person's longest shift, longer or shorter than the store's. Pay periods are biweekly. A last working day stops someone being drafted or suggested after it."
            >
              <div className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-2" data-testid="staff-rule-everyone">
                  <span className="flex h-7 w-48 items-center rounded-md border border-dashed border-border px-2.5 text-xs font-medium">
                    Everyone
                  </span>
                  <span className="flex h-7 w-56 items-center px-1 text-xs text-muted-foreground">
                    At most … hours / week
                  </span>
                  <Input
                    type="number"
                    min={1}
                    max={80}
                    value={rules.staffing.maxWeekHours}
                    onChange={(e) =>
                      setStaffing({
                        maxWeekHours: Math.min(80, Math.max(1, Math.round(Number(e.target.value) || 1))),
                      })
                    }
                    className="h-7 w-16 text-xs tabular-nums"
                    aria-label="Most hours per person per week"
                  />
                  <span className="text-xs text-muted-foreground">
                    h / week · ADP scheduled + draft, unless a person&apos;s hour target below says otherwise
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2" data-testid="staff-rule-everyone-min">
                  <span className="flex h-7 w-48 items-center rounded-md border border-dashed border-border px-2.5 text-xs font-medium">
                    Everyone
                  </span>
                  <span className="flex h-7 w-56 items-center px-1 text-xs text-muted-foreground">
                    At least … shifts / week
                  </span>
                  <Input
                    type="number"
                    min={0}
                    max={7}
                    value={rules.staffing.minWeekShifts}
                    onChange={(e) =>
                      setStaffing({
                        minWeekShifts: Math.min(7, Math.max(0, Math.round(Number(e.target.value) || 0))),
                      })
                    }
                    className="h-7 w-16 text-xs tabular-nums"
                    aria-label="Fewest shifts per person per week"
                  />
                  <span className="text-xs text-muted-foreground">
                    shifts / week · before regulars take more, when they&apos;re available · 0 = off
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2" data-testid="staff-rule-everyone-meal">
                  <span className="flex h-7 w-48 items-center rounded-md border border-dashed border-border px-2.5 text-xs font-medium">
                    Everyone
                  </span>
                  <span className="flex h-7 w-56 items-center px-1 text-xs text-muted-foreground">
                    Unpaid meal … min
                  </span>
                  <Input
                    type="number"
                    min={0}
                    max={90}
                    step={5}
                    value={rules.staffing.unpaidMealMin}
                    onChange={(e) =>
                      setStaffing({
                        unpaidMealMin: Math.min(90, Math.max(0, Math.round(Number(e.target.value) || 0))),
                      })
                    }
                    className="h-7 w-16 text-xs tabular-nums"
                    aria-label="Unpaid meal minutes"
                  />
                  <span className="text-xs text-muted-foreground">min on shifts longer than</span>
                  <Input
                    type="number"
                    min={0}
                    max={12}
                    step={0.5}
                    value={rules.staffing.mealAfterMin / 60}
                    onChange={(e) =>
                      setStaffing({
                        mealAfterMin: Math.min(720, Math.max(0, Math.round((Number(e.target.value) || 0) * 60))),
                      })
                    }
                    className="h-7 w-16 text-xs tabular-nums"
                    aria-label="Meal applies to shifts longer than (hours)"
                  />
                  <span className="text-xs text-muted-foreground">
                    h · as in ADP, so drafted hours are paid hours
                  </span>
                </div>
                {staffGroups(rules.staffRules).map(([name, list]) => (
                  <div
                    key={name || list[0]!.id}
                    data-testid="staff-rules-person"
                    className="flex flex-col gap-1.5 rounded-md border border-border/70 bg-muted/20 px-2.5 py-2"
                  >
                    <div className="flex items-center gap-2">
                      {name ? (
                        <span className="text-sm font-medium text-foreground">{name}</span>
                      ) : (
                        <Select
                          value={null}
                          onValueChange={(v) => v && setStaffRule(list[0]!.id, { employee: String(v) })}
                        >
                          <SelectTrigger className="h-7 w-48 text-xs" aria-label="Employee">
                            <SelectValue placeholder="Pick a person" />
                          </SelectTrigger>
                          <SelectContent>
                            {employees.map((n) => (
                              <SelectItem key={n} value={n}>
                                {n}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                      {name && list.length < KINDS.length ? (
                        <Button
                          size="xs"
                          variant="ghost"
                          className="ml-auto text-muted-foreground"
                          onClick={() => {
                            const k = KINDS.find((x) => !list.some((r) => r.kind === x.value))!;
                            onChange({
                              ...rules,
                              staffRules: [
                                ...rules.staffRules,
                                {
                                  id: `${Date.now()}`,
                                  employee: name,
                                  kind: k.value,
                                  value: k.initial,
                                  ...(k.value === "last_day" ? { date: new Date().toLocaleDateString("en-CA", { timeZone: "America/Chicago" }) } : {}),
                                },
                              ],
                            });
                          }}
                        >
                          <Plus /> Add rule
                        </Button>
                      ) : null}
                    </div>
                    {list.map((r) => {
                      const kind = KINDS.find((k) => k.value === r.kind);
                      return (
                        <div key={r.id} className="flex flex-wrap items-center gap-2">
                          <Select
                            value={r.kind}
                            onValueChange={(v) => v && setStaffRule(r.id, { kind: v as StaffRuleKind })}
                          >
                            <SelectTrigger className="h-7 w-56 text-xs" aria-label="Rule">
                              <SelectValue>{(v: StaffRuleKind) => KINDS.find((k) => k.value === v)?.label}</SelectValue>
                            </SelectTrigger>
                            <SelectContent>
                              {KINDS.map((k) => (
                                <SelectItem key={k.value} value={k.value}>
                                  {k.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {r.kind === "last_day" ? (
                            <Input
                              type="date"
                              value={r.date ?? ""}
                              onChange={(e) => setStaffRule(r.id, { date: e.target.value })}
                              className="h-7 w-36 text-xs tabular-nums"
                              aria-label="Last working day"
                            />
                          ) : (
                            <Input
                              type="number"
                              min={0}
                              max={valueMax(r.kind)}
                              step={r.kind === "max_day_hours" ? 0.5 : 1}
                              value={r.value}
                              onChange={(e) =>
                                setStaffRule(r.id, {
                                  value: Math.min(valueMax(r.kind), Math.max(0, Number(e.target.value))),
                                })
                              }
                              className="h-7 w-16 text-xs tabular-nums"
                              aria-label="Value"
                            />
                          )}
                          <span className="text-xs text-muted-foreground">{kind?.unit}</span>
                          <RemoveButton
                            label="Remove staff rule"
                            onClick={() =>
                              onChange({ ...rules, staffRules: rules.staffRules.filter((x) => x.id !== r.id) })
                            }
                          />
                        </div>
                      );
                    })}
                  </div>
                ))}
                <Button
                  size="xs"
                  variant="outline"
                  className="self-start"
                  onClick={() =>
                    onChange({
                      ...rules,
                      staffRules: [
                        ...rules.staffRules,
                        { id: `${Date.now()}`, employee: "", kind: "target_week_hours", value: 30 },
                      ],
                    })
                  }
                >
                  <Plus /> Add person
                </Button>
              </div>
            </Section>
          </div>

          <div className="md:col-span-2">
            <Section
              title="Day & time rules"
              hint="Exactly how many people you want in a window — e.g. opening or closing duties. Each day and time can have only one rule. Else rows are the staffed time no rule covers: people is auto (from typical orders) until you change anything in the row."
            >
              <div className="flex flex-col gap-1.5">
                {[
                  ...rules.dayRules.map((r) => ({
                    rule: r as RowRule,
                    ghost: false,
                  })),
                  ...ghosts.map((g) => ({ rule: g, ghost: true })),
                ].map(({ rule: r, ghost }) => {
                  const clashes = ghost
                    ? []
                    : conflicts.filter((c) => c.a === r.id || c.b === r.id);
                  const idx = rules.dayRules.findIndex((x) => x.id === r.id);
                  const n = idx + 1;
                  return (
                    <DayRuleRow
                      key={r.id}
                      rule={r}
                      label={ghost ? "Else" : `${n}.`}
                      ghost={ghost}
                      handle={
                        ghost ? undefined : (
                          <DragHandle
                            label={`Move rule ${n}`}
                            {...dayOrder.handle(idx)}
                          />
                        )
                      }
                      rowProps={ghost ? undefined : dayOrder.row(idx)}
                      dropTarget={!ghost && dayOrder.isDropTarget(idx)}
                      autoHint={`Auto: typical orders ÷ ${rules.staffing.ordersPerPerson} per person, at least ${rules.staffing.minPeople}. Change anything here to make it a rule.`}
                      onPatch={(patch) =>
                        ghost
                          ? onChange({
                              ...rules,
                              dayRules: [
                                ...rules.dayRules,
                                {
                                  id: r.id,
                                  days: r.days,
                                  delivery: r.delivery,
                                  fromMin: r.fromMin,
                                  toMin: r.toMin,
                                  people: rules.staffing.minPeople,
                                  ...patch,
                                },
                              ],
                            })
                          : setDayRule(r.id, patch)
                      }
                      onRemove={
                        ghost
                          ? undefined
                          : () =>
                              onChange({
                                ...rules,
                                dayRules: rules.dayRules.filter(
                                  (x) => x.id !== r.id,
                                ),
                              })
                      }
                    >
                      {clashes.map((c) => {
                        const other =
                          rules.dayRules.findIndex(
                            (x) => x.id === (c.a === r.id ? c.b : c.a),
                          ) + 1;
                        return (
                          <p
                            key={`${c.a}-${c.b}`}
                            className="pl-12 text-xs text-destructive"
                          >
                            Overlaps rule {other} on {daysLabel(c.days)},{" "}
                            {clock(c.fromMin)}–{clock(c.toMin)} — change the
                            days or times so only one rule applies.
                          </p>
                        );
                      })}
                    </DayRuleRow>
                  );
                })}
                <Button
                  size="xs"
                  variant="outline"
                  className="self-start"
                  onClick={() =>
                    onChange({
                      ...rules,
                      dayRules: [
                        ...rules.dayRules,
                        {
                          id: `${Date.now()}`,
                          days: [...ALL_DAYS],
                          delivery: "any",
                          fromMin: 12 * 60,
                          toMin: 14 * 60,
                          people: 3,
                        },
                      ],
                    })
                  }
                >
                  <Plus /> Add day rule
                </Button>
              </div>
            </Section>
          </div>

          <div className="sticky bottom-0 z-10 -mx-3 -mb-3 flex flex-col gap-2 rounded-b-lg border-t border-border bg-card px-3 py-2.5 md:col-span-2">
          <HoursImpact forecast={forecast} goal={goalHoursWeek} dirty={dirty} />
          <div className="flex flex-wrap items-center gap-2">
            {dirty ? (
              <>
                <Input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="What changed? (optional)"
                  maxLength={200}
                  className="h-7 min-w-48 flex-1 text-xs"
                  aria-label="Change note"
                />
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={onDiscard}
                  disabled={isPending}
                >
                  Discard
                </Button>
                {conflicts.length ? (
                  <span className="text-xs text-destructive">
                    Fix overlapping day rules to save
                  </span>
                ) : null}
                <Button
                  size="xs"
                  onClick={save}
                  disabled={isPending || conflicts.length > 0}
                >
                  {isPending ? "Saving…" : "Save rules"}
                </Button>
              </>
            ) : (
              <span className="flex flex-1 items-center gap-1.5 text-xs text-muted-foreground" data-testid="rules-saved">
                {live ? (
                  <>
                    <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
                    Saved as v{live.version} · {savedAt(live.createdAt)}. Draft shifts are rebuilt
                    from v{live.version} on every load, in any browser.
                  </>
                ) : (
                  "Edits preview in the draft right away; save to keep them for every future week."
                )}
              </span>
            )}
            <Button
              size="xs"
              variant="outline"
              aria-expanded={showHistory}
              onClick={() => setShowHistory((v) => !v)}
              disabled={!history.length}
            >
              <History /> History ({history.length})
            </Button>
          </div>
          </div>

          {showHistory && history.length ? (
            <ol className="flex flex-col divide-y divide-border rounded-lg border border-border md:col-span-2">
              {history.map((v, i) => (
                <li
                  key={v.version}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs"
                >
                  <span className="font-medium tabular-nums text-foreground">
                    v{v.version}
                  </span>
                  {i === 0 ? (
                    <Badge
                      variant="secondary"
                      className="h-4 px-1.5 text-[11px]"
                    >
                      Live
                    </Badge>
                  ) : null}
                  <span className="text-muted-foreground">
                    {whoSaved(v.createdBy)} · {savedAt(v.createdAt)}
                  </span>
                  <span className="text-muted-foreground">
                    {rulesSummary(v)}
                  </span>
                  {v.note ? (
                    <span className="italic text-foreground">“{v.note}”</span>
                  ) : null}
                  {i > 0 ? (
                    <Button
                      size="xs"
                      variant="ghost"
                      className="ml-auto"
                      onClick={() => onChange(v.rules)}
                      title="Load this version into the editor; save to make it live again"
                    >
                      <RotateCcw /> Restore
                    </Button>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
