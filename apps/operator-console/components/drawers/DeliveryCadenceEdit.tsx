"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { setRestockCadenceAction } from "@/app/inventory/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { CADENCE_WEEKDAYS, DEFAULT_HORIZON, MAX_HORIZON } from "@/lib/inventory/restockHorizon";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";

const DAY_OPTIONS = [
  { value: "off", label: "Off (manual dates)" },
  ...CADENCE_WEEKDAYS.map((d) => ({ value: d, label: `${d[0].toUpperCase()}${d.slice(1)}s` })),
];
const COUNT_OPTIONS = Array.from({ length: MAX_HORIZON }, (_, i) => String(i + 1));

function summary(weekday: string, horizon: number): string {
  if (weekday === "off") return "Deliveries: manual";
  return `Deliveries: ${weekday.slice(0, 3).replace(/^./, (c) => c.toUpperCase())} · ${horizon} ahead`;
}

// Inline quick-edit for store_config restock_delivery_weekday / restock_horizon_dates,
// same pattern as CapacityEdit. Saving registers any missing upcoming dates and
// refreshes the recommendation.
export function DeliveryCadenceEdit({
  weekday,
  horizon,
}: {
  weekday?: string | null;
  horizon?: number | null;
}) {
  const current = weekday && CADENCE_WEEKDAYS.includes(weekday) ? weekday : "off";
  const currentCount = horizon ?? DEFAULT_HORIZON;
  const [editing, setEditing] = useState(false);
  const [day, setDay] = useState(current);
  const [count, setCount] = useState(String(currentCount));
  const { isPending, stage, error, run } = useConsoleAction();
  const { followOrderReco } = useOrderRecoRefreshFollowup();

  if (!editing) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setDay(current);
          setCount(String(currentCount));
          setEditing(true);
        }}
      >
        {summary(current, currentCount)}
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <Select value={day} onValueChange={(v) => v && setDay(String(v))}>
          <SelectTrigger size="sm" className="h-9 min-w-36 sm:h-7" aria-label="Delivery weekday">
            <SelectValue>{(v: string) => DAY_OPTIONS.find((o) => o.value === v)?.label}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {DAY_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={count} onValueChange={(v) => v && setCount(String(v))} disabled={day === "off"}>
          <SelectTrigger size="sm" className="h-9 sm:h-7" aria-label="Upcoming dates to keep">
            <SelectValue>{(v: string) => `${v} ahead`}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {COUNT_OPTIONS.map((n) => (
              <SelectItem key={n} value={n}>
                {n} ahead
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          disabled={isPending}
          onClick={() => {
            void run(() => setRestockCadenceAction(day, Number(count)), {
              saving: "Saving…",
              queued: "Delivery schedule saved — recommendation refreshing…",
              done: "Delivery schedule saved.",
            }).then((ack) => {
              if (!ack.ok) return;
              setEditing(false);
              followOrderReco({ queued: ack.queued, data: ack.data });
            });
          }}
        >
          {isPending ? "Saving…" : "Save"}
        </Button>
        <Button variant="ghost" size="sm" disabled={isPending} onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
      {stage || error ? (
        <span className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
          {stage || error}
        </span>
      ) : null}
    </div>
  );
}
