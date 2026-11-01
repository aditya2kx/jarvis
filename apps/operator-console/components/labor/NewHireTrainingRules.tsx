"use client";

import { X } from "lucide-react";
import { StaffTagBadge } from "@/components/labor/StaffTag";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { progressLabel, TRAINING_FROM, type TrainingStatus } from "@/lib/labor/new-hire-training";
import type { TrainingRules } from "@/lib/labor/schedule-inputs";
import { WARN_BADGE_CLASS } from "@/lib/run-status";
import { cn } from "@/lib/utils";

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function Field({
  label,
  unit,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  unit: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5">
        <Input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) onChange(clamp(v, min, max));
          }}
          className="h-9 w-16 text-xs tabular-nums sm:h-7"
        />
        <span className="text-xs text-muted-foreground">{unit}</span>
      </span>
    </label>
  );
}

const shortDate = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { month: "short", day: "numeric" });
};

/**
 * New-hire training (Issue #450): who counts as experienced, how many supervised openers / closers a
 * new hire needs, and where each new hire stands. Lives inside Scheduling rules, saved with them.
 */
export function NewHireTrainingRules({
  training: t,
  employees,
  trainees,
  onChange,
}: {
  training: TrainingRules;
  employees: string[];
  trainees: TrainingStatus[];
  onChange: (patch: Partial<TrainingRules>) => void;
}) {
  const addable = employees.filter((n) => !t.experienced.includes(n));
  const on = t.experienced.length > 0;
  const people = [...trainees].sort((a, b) => Number(a.cleared) - Number(b.cleared) || b.since.localeCompare(a.since));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <Field
          label="Supervised openers"
          unit="shifts"
          value={t.openShifts}
          min={0}
          max={10}
          onChange={(v) => onChange({ openShifts: Math.round(v) })}
        />
        <Field
          label="Supervised closers"
          unit="shifts"
          value={t.closeShifts}
          min={0}
          max={10}
          onChange={(v) => onChange({ closeShifts: Math.round(v) })}
        />
        <Field
          label="Together for at least"
          unit="hours"
          value={t.overlapMin / 60}
          min={0.25}
          max={12}
          step={0.25}
          onChange={(v) => onChange({ overlapMin: Math.round((v * 60) / 15) * 15 })}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-xs text-muted-foreground">Experienced staff</span>
        <div className="flex flex-wrap items-center gap-1.5" data-testid="experienced-staff">
          {t.experienced.map((name) => (
            <Badge key={name} variant="secondary" className="h-7 gap-1 pr-1 font-normal">
              {name}
              <button
                type="button"
                aria-label={`Remove ${name} from experienced staff`}
                onClick={() => onChange({ experienced: t.experienced.filter((n) => n !== name) })}
                className="flex size-5 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
          {addable.length ? (
            <Select
              value={null}
              onValueChange={(v) => v && onChange({ experienced: [...t.experienced, String(v)].sort() })}
            >
              <SelectTrigger className="h-9 w-48 text-xs sm:h-7" aria-label="Add experienced staff">
                <SelectValue placeholder="Add a person" />
              </SelectTrigger>
              <SelectContent>
                {addable.map((n) => (
                  <SelectItem key={n} value={n}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
        </div>
        {!on ? (
          <p className="text-xs text-muted-foreground">
            Add experienced staff to turn this on — until then new hires are drafted like anyone else.
          </p>
        ) : null}
      </div>

      {on ? (
        people.length ? (
          <ul className="flex flex-col divide-y rounded-md border bg-background" data-testid="new-hire-progress">
            {people.map((p) => (
              <li key={p.employee} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
                <span className="text-sm font-medium text-foreground">{p.employee}</span>
                {p.cleared ? (
                  <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal text-muted-foreground">
                    {p.marked ? "Marked trained" : `Trained · ${progressLabel(p)}`}
                  </Badge>
                ) : (
                  <StaffTagBadge employee={p.employee} />
                )}
                <span className="text-muted-foreground">
                  {p.hireDate ? `hired ${shortDate(p.hireDate)}` : `first ADP shift ${shortDate(p.since)} (no hire date yet)`}
                  {` · ${p.worked} ${p.worked === 1 ? "shift" : "shifts"} worked`}
                </span>
                {p.unsupervised.length ? (
                  <Badge
                    variant="outline"
                    className={cn("h-4 px-1.5 text-[11px] font-normal", WARN_BADGE_CLASS)}
                    title={`Already in ADP without experienced staff on long enough: ${p.unsupervised.map(shortDate).join(", ")}. Move them, or put experienced staff on with them.`}
                  >
                    {p.unsupervised.length} ADP {p.unsupervised.length === 1 ? "shift" : "shifts"} ahead without experienced
                    staff
                  </Badge>
                ) : null}
                {p.marked || !p.cleared ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="ml-auto text-xs text-muted-foreground hover:text-foreground"
                    onClick={() =>
                      onChange({
                        trained: p.marked
                          ? t.trained.filter((n) => n !== p.employee)
                          : [...t.trained, p.employee].sort((a, b) => a.localeCompare(b)),
                      })
                    }
                  >
                    {p.marked ? "Undo" : "Mark trained"}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No new hires since {shortDate(TRAINING_FROM)}.</p>
        )
      ) : null}
    </div>
  );
}
