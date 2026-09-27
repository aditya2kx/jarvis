"use client";

import { useState } from "react";
import { ChevronDown, Plus, X } from "lucide-react";
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
  minToTime,
  timeToMin,
  type DayRule,
  type DayScope,
  type ScheduleRules,
  type StaffRule,
  type StaffRuleKind,
} from "@/lib/labor/schedule-inputs";
import { cn } from "@/lib/utils";

const SCOPES: { value: DayScope; label: string }[] = [
  { value: "weekdays", label: "Weekdays (Mon–Fri)" },
  { value: "weekends", label: "Weekends (Sat–Sun)" },
  { value: "all", label: "Every day" },
  { value: "delivery", label: "Frozen delivery days" },
  ...["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, i) => ({
    value: String(i) as DayScope,
    label: `Every ${label}`,
  })),
];

const KINDS: { value: StaffRuleKind; label: string; unit: string }[] = [
  { value: "target_week_hours", label: "About … hours / week", unit: "h / week" },
  { value: "max_shifts_per_period", label: "At most … shifts / pay period", unit: "shifts / pay period" },
];

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function Section({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/80 bg-card/60 p-3">
      <div>
        <h4 className="text-xs font-medium text-foreground">{title}</h4>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
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
  return (
    <Input
      type="time"
      value={minToTime(value)}
      onChange={(e) => {
        const v = timeToMin(e.target.value);
        if (v != null) onChange(v);
      }}
      className="h-7 w-28 text-xs"
      aria-label={label}
    />
  );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button size="icon-xs" variant="ghost" aria-label={label} onClick={onClick}>
      <X />
    </Button>
  );
}

export function ScheduleInputsPanel({
  rules,
  onChange,
  employees,
  deliveryDates,
  goalHoursWeek,
}: {
  rules: ScheduleRules;
  onChange: (next: ScheduleRules) => void;
  employees: string[];
  /** Upcoming frozen deliveries from the Inventory restock schedule (read-only here). */
  deliveryDates: string[];
  goalHoursWeek?: number;
}) {
  const [open, setOpen] = useState(false);
  const setDayRule = (id: string, patch: Partial<DayRule>) =>
    onChange({ ...rules, dayRules: rules.dayRules.map((r) => (r.id === id ? { ...r, ...patch } : r)) });
  const setStaffRule = (id: string, patch: Partial<StaffRule>) =>
    onChange({ ...rules, staffRules: rules.staffRules.map((r) => (r.id === id ? { ...r, ...patch } : r)) });

  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const summary = [
    goalHoursWeek != null ? `${goalHoursWeek}h/week goal` : null,
    plural(rules.staffRules.length, "staff rule"),
    plural(rules.dayRules.length, "day rule"),
    plural(deliveryDates.length, "upcoming delivery", "upcoming deliveries"),
  ].filter(Boolean);

  return (
    <div data-testid="schedule-inputs" className="rounded-lg border border-border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex items-center gap-2 text-sm font-medium text-foreground">
            Scheduling rules
            <Badge variant="outline" className="h-4 px-1.5 text-[10px] font-normal">
              Saved in this browser
            </Badge>
          </span>
          <span className="truncate text-xs text-muted-foreground">{summary.join(" · ")}</span>
        </span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
        />
      </button>

      {open ? (
        <div className="grid gap-3 border-t border-border p-3 md:grid-cols-2">
          <Section
            title="Weekly hours goal"
            hint="The draft never adds order-driven shifts past this. Set it in Weekly hours goal above."
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
              <p className="text-xs text-muted-foreground">No upcoming deliveries scheduled.</p>
            )}
          </Section>

          <div className="md:col-span-2">
            <Section
              title="Staff rules"
              hint="Hour targets get first pick of draft shifts until they reach the target (never past it). Pay periods are biweekly."
            >
              <div className="flex flex-col gap-1.5">
                {rules.staffRules.map((r) => (
                  <div key={r.id} className="flex flex-wrap items-center gap-2">
                    <Select
                      value={r.employee || null}
                      onValueChange={(v) => v && setStaffRule(r.id, { employee: String(v) })}
                    >
                      <SelectTrigger className="h-7 w-48 text-xs" aria-label="Employee">
                        <SelectValue placeholder="Pick a person" />
                      </SelectTrigger>
                      <SelectContent>
                        {employees.map((name) => (
                          <SelectItem key={name} value={name}>
                            {name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
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
                    <Input
                      type="number"
                      min={0}
                      value={r.value}
                      onChange={(e) => setStaffRule(r.id, { value: Math.max(0, Number(e.target.value)) })}
                      className="h-7 w-16 text-xs tabular-nums"
                      aria-label="Value"
                    />
                    <span className="text-xs text-muted-foreground">
                      {KINDS.find((k) => k.value === r.kind)?.unit}
                    </span>
                    <RemoveButton
                      label="Remove staff rule"
                      onClick={() =>
                        onChange({ ...rules, staffRules: rules.staffRules.filter((x) => x.id !== r.id) })
                      }
                    />
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
                        { id: `${Date.now()}`, employee: "", kind: "target_week_hours", value: 40 },
                      ],
                    })
                  }
                >
                  <Plus /> Add staff rule
                </Button>
              </div>
            </Section>
          </div>

          <div className="md:col-span-2">
            <Section
              title="Day & time rules"
              hint="Exactly how many people you want in a window. Replaces the labor floor and order-based need there; later rules win."
            >
              <div className="flex flex-col gap-1.5">
                {rules.dayRules.map((r) => (
                  <div key={r.id} className="flex flex-wrap items-center gap-2">
                    <Select
                      value={r.scope}
                      onValueChange={(v) => v && setDayRule(r.id, { scope: v as DayScope })}
                    >
                      <SelectTrigger className="h-7 w-48 text-xs" aria-label="Days">
                        <SelectValue>{(v: DayScope) => SCOPES.find((s) => s.value === v)?.label}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {SCOPES.map((s) => (
                          <SelectItem key={s.value} value={s.value}>
                            {s.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {r.scope !== "delivery" ? (
                      <button
                        type="button"
                        aria-pressed={r.exceptDelivery}
                        onClick={() => setDayRule(r.id, { exceptDelivery: !r.exceptDelivery })}
                        className={cn(
                          "h-7 rounded-md border px-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          r.exceptDelivery
                            ? "border-primary bg-primary/15 text-foreground"
                            : "border-border text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                        )}
                      >
                        except delivery days
                      </button>
                    ) : null}
                    <TimeInput value={r.fromMin} onChange={(v) => setDayRule(r.id, { fromMin: v })} label="From" />
                    <span className="text-xs text-muted-foreground">to</span>
                    <TimeInput value={r.toMin} onChange={(v) => setDayRule(r.id, { toMin: v })} label="To" />
                    <Input
                      type="number"
                      min={0}
                      value={r.people}
                      onChange={(e) => setDayRule(r.id, { people: Math.max(0, Number(e.target.value)) })}
                      className="h-7 w-16 text-xs tabular-nums"
                      aria-label="People"
                    />
                    <span className="text-xs text-muted-foreground">people</span>
                    <RemoveButton
                      label="Remove day rule"
                      onClick={() => onChange({ ...rules, dayRules: rules.dayRules.filter((x) => x.id !== r.id) })}
                    />
                  </div>
                ))}
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
                          scope: "all",
                          exceptDelivery: false,
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
        </div>
      ) : null}
    </div>
  );
}
