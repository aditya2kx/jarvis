"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  submitRestockAction,
  moveRestockDateAction,
  removeRestockDateAction,
} from "@/app/inventory/actions";
import type { RestockAction } from "@/lib/bq/writes";
import { ACTIVE_BASES, buildSampleCsv, type RestockRow } from "@/lib/restock/parse";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";

const ACTION_LABELS: Record<Exclude<RestockAction, "replace-estimated">, string> = {
  "add-order": "Add / update actuals",
  "register-only": "Register date only (estimated)",
  "reset-to-estimated": "Reset to estimated",
  "move-date": "Move date",
  "remove-date": "Remove date",
};

type UiRestockAction = keyof typeof ACTION_LABELS;

const EMPTY_ESTIMATES: Record<string, RestockRow[]> = {};
const EMPTY_SCHEDULED: { delivery_date: string; has_actuals: boolean }[] = [];

function seedRowsFromEstimates(
  deliveryDate: string,
  estimateByDate: Record<string, RestockRow[]>,
): RestockRow[] {
  const fromReco = estimateByDate[deliveryDate];
  const byItem = new Map((fromReco ?? []).map((r) => [r.item, r.quantityTubs]));
  return ACTIVE_BASES.map((item) => ({
    item,
    quantityTubs: byItem.has(item) ? Number(byItem.get(item)) : 0,
  }));
}

// Nothing writes to BQ until the operator reviews the rows and hits Submit —
// mirrors the Slack restock modal's confirm step (EXECUTION.md §M3).
export function RestockImportDrawer({
  dates,
  scheduledDates = EMPTY_SCHEDULED,
  estimateByDate = EMPTY_ESTIMATES,
  defaultAction = "add-order",
}: {
  dates: string[];
  /** Future schedule dates (Estimated or Actuals) for Move / Remove. */
  scheduledDates?: { delivery_date: string; has_actuals: boolean }[];
  /** Per delivery date → Order Tubs from reco (prefills Add actuals form). */
  estimateByDate?: Record<string, RestockRow[]>;
  /** Test/default override — production always leaves this at add-order. */
  defaultAction?: UiRestockAction;
}) {
  const scheduledList = useMemo(
    () =>
      scheduledDates.map((d) => ({
        delivery_date: String(d.delivery_date).slice(0, 10),
        has_actuals: Boolean(d.has_actuals),
      })),
    [scheduledDates],
  );

  const [open, setOpen] = useState(false);
  const [deliveryDate, setDeliveryDate] = useState(dates[0] ?? "");
  const [action, setAction] = useState<UiRestockAction>(defaultAction);
  const [fromDate, setFromDate] = useState(scheduledList[0]?.delivery_date ?? "");
  const [toDate, setToDate] = useState("");
  const [removeDate, setRemoveDate] = useState(scheduledList[0]?.delivery_date ?? "");
  const [removeConfirmed, setRemoveConfirmed] = useState(false);
  const [rows, setRows] = useState<RestockRow[]>(() =>
    seedRowsFromEstimates(dates[0] ?? "", estimateByDate),
  );
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const { isPending, stage, error, run } = useConsoleAction();
  const { banner: recoBanner, followOrderReco } = useOrderRecoRefreshFollowup({
    doneToast: "Restock applied — Order tubs updated",
  });

  const isMove = action === "move-date";
  const isRemove = action === "remove-date";
  const isAddOrder = action === "add-order";

  useEffect(() => {
    if (!open || !isAddOrder) return;
    setRows(seedRowsFromEstimates(deliveryDate, estimateByDate));
    setParseErrors([]);
    setShowImport(false);
  }, [open, isAddOrder, deliveryDate, estimateByDate]);

  async function handleFile(file: File) {
    setStatus("Parsing…");
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/parse-restock", { method: "POST", body: fd });
    const body = await res.json();
    if (!res.ok) {
      setStatus(`Parse failed: ${body.error ?? res.statusText}`);
      return;
    }
    const parsed: RestockRow[] = body.rows ?? [];
    const byItem = new Map(parsed.map((r) => [r.item, r.quantityTubs]));
    setRows(
      ACTIVE_BASES.map((item) => ({
        item,
        quantityTubs: byItem.has(item) ? Number(byItem.get(item)) : 0,
      })),
    );
    setParseErrors(body.errors ?? []);
    setStatus(parsed.length ? `Imported ${parsed.length} row(s) — review below.` : "No valid rows parsed.");
  }

  function downloadSampleCsv() {
    const blob = new Blob([buildSampleCsv()], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "restock-sample.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  function updateQty(item: string, quantityTubs: number) {
    setRows((prev) => prev.map((r) => (r.item === item ? { ...r, quantityTubs } : r)));
  }

  async function handleSubmit() {
    if (isMove) {
      if (!fromDate || !toDate) {
        setStatus("Pick both the current date and the new delivery date.");
        return;
      }
      if (!scheduledList.length) {
        setStatus("No scheduled dates to move.");
        return;
      }
      const ack = await run(() => moveRestockDateAction(fromDate, toDate), {
        saving: "Moving…",
        done: "Moved.",
        queued: "Moved — recommendation refreshing…",
      });
      if (ack.ok) {
        setOpen(false);
        followOrderReco({
          queued: ack.queued,
          baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
        });
      }
      return;
    }

    if (isRemove) {
      if (!removeDate) {
        setStatus("Pick a delivery date to remove.");
        return;
      }
      if (!removeConfirmed) {
        setStatus("Confirm removal before submitting.");
        return;
      }
      const ack = await run(() => removeRestockDateAction(removeDate), {
        saving: "Removing…",
        done: "Removed.",
        queued: "Removed — recommendation refreshing…",
      });
      if (ack.ok) {
        setOpen(false);
        setRemoveConfirmed(false);
        followOrderReco({
          queued: ack.queued,
          baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
        });
      }
      return;
    }

    if (!deliveryDate) {
      setStatus("Pick a delivery date first.");
      return;
    }
    if (isAddOrder) {
      for (const r of rows) {
        if (!Number.isInteger(r.quantityTubs) || r.quantityTubs < 0) {
          setStatus(`Enter a non-negative integer for ${r.item}.`);
          return;
        }
      }
    }
    const ack = await run(
      () => submitRestockAction(deliveryDate, action as RestockAction, rows),
      {
        saving: "Submitting…",
        done: "Submitted.",
        queued: "Submitted — recommendation refreshing…",
      },
    );
    if (ack.ok) {
      setOpen(false);
      followOrderReco({
        queued: ack.queued,
        baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
      });
    }
  }

  const feedback = stage || error || status || recoBanner;
  const submitDisabled =
    isPending ||
    (isMove && scheduledList.length === 0) ||
    (isRemove && (scheduledList.length === 0 || !removeConfirmed));

  return (
    <>
      {recoBanner && !open ? (
        <p className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          {recoBanner}
        </p>
      ) : null}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger render={<Button size="sm">Restock…</Button>} />
        <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Restock</SheetTitle>
            <SheetDescription>
              Add or update Actuals from estimates, move a wrong date, or remove a cancelled
              delivery — nothing writes until you submit.
            </SheetDescription>
          </SheetHeader>

          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
            <div className="flex flex-col gap-1.5">
              <Label>Action</Label>
              <Select
                value={action}
                onValueChange={(v) => {
                  const next = v as UiRestockAction;
                  setAction(next);
                  setStatus(null);
                  setRemoveConfirmed(false);
                  if (next === "move-date" && !fromDate && scheduledList[0]) {
                    setFromDate(scheduledList[0].delivery_date);
                  }
                  if (next === "remove-date" && !removeDate && scheduledList[0]) {
                    setRemoveDate(scheduledList[0].delivery_date);
                  }
                }}
              >
                <SelectTrigger className="h-10">
                  <SelectValue>
                    {(value: string | null) =>
                      value
                        ? (ACTION_LABELS[value as UiRestockAction] ?? value)
                        : "Select action"
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ACTION_LABELS).map(([value, label]) => (
                    <SelectItem
                      key={value}
                      value={value}
                      disabled={
                        (value === "move-date" || value === "remove-date") &&
                        scheduledList.length === 0
                      }
                    >
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {isMove ? (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label>Current date</Label>
                  {scheduledList.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No scheduled dates to move.</p>
                  ) : (
                    <Select value={fromDate} onValueChange={(v) => setFromDate(v ?? "")}>
                      <SelectTrigger className="h-10">
                        <SelectValue placeholder="Select date" />
                      </SelectTrigger>
                      <SelectContent>
                        {scheduledList.map((d) => (
                          <SelectItem key={d.delivery_date} value={d.delivery_date}>
                            {d.delivery_date}
                            {d.has_actuals ? " · Actuals" : " · Estimated"}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="to-date">New delivery date</Label>
                  <Input
                    id="to-date"
                    type="date"
                    className="h-10"
                    value={toDate}
                    onChange={(e) => setToDate(e.target.value)}
                  />
                </div>
              </>
            ) : isRemove ? (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label>Date to remove</Label>
                  {scheduledList.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No scheduled dates to remove.</p>
                  ) : (
                    <Select
                      value={removeDate}
                      onValueChange={(v) => {
                        setRemoveDate(v ?? "");
                        setRemoveConfirmed(false);
                      }}
                    >
                      <SelectTrigger className="h-10">
                        <SelectValue placeholder="Select date" />
                      </SelectTrigger>
                      <SelectContent>
                        {scheduledList.map((d) => (
                          <SelectItem key={d.delivery_date} value={d.delivery_date}>
                            {d.delivery_date}
                            {d.has_actuals ? " · Actuals" : " · Estimated"}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1 size-4 accent-foreground"
                    checked={removeConfirmed}
                    onChange={(e) => setRemoveConfirmed(e.target.checked)}
                  />
                  <span>
                    Remove this date from the schedule
                    {scheduledList.find((d) => d.delivery_date === removeDate)?.has_actuals
                      ? " and delete its Actuals"
                      : ""}
                    . This cannot be undone from here.
                  </span>
                </label>
              </>
            ) : (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="delivery-date">Delivery date</Label>
                <Input
                  id="delivery-date"
                  type="date"
                  className="h-10"
                  value={deliveryDate}
                  onChange={(e) => setDeliveryDate(e.target.value)}
                />
              </div>
            )}

            {isAddOrder ? (
              <>
                <div className="flex flex-col gap-2">
                  <Label>Actuals (prefilled from estimates)</Label>
                  <p className="text-xs text-muted-foreground">
                    Edit only quantities that differ from the estimate, then Submit.
                  </p>
                  {rows.map((r) => (
                    <div
                      key={r.item}
                      className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm">{r.item}</span>
                      <Input
                        type="number"
                        min={0}
                        step="1"
                        className="h-10 w-24"
                        aria-label={`${r.item} tubs`}
                        value={r.quantityTubs}
                        onChange={(e) => updateQty(r.item, Number(e.target.value))}
                      />
                    </div>
                  ))}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-10 w-fit"
                    onClick={() => setShowImport((v) => !v)}
                  >
                    {showImport ? "Hide import" : "Import CSV / photo…"}
                  </Button>
                  {showImport ? (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="restock-file">Order CSV or photo</Label>
                        <Button type="button" variant="outline" size="sm" onClick={downloadSampleCsv}>
                          Download sample CSV
                        </Button>
                      </div>
                      <Input
                        id="restock-file"
                        type="file"
                        accept=".csv,text/csv,image/*"
                        className="h-10"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) void handleFile(f);
                        }}
                      />
                    </>
                  ) : null}
                </div>
              </>
            ) : null}

            {feedback ? (
              <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
                {feedback}
              </p>
            ) : null}

            {parseErrors.length ? (
              <ul className="list-disc pl-4 text-sm text-destructive">
                {parseErrors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <SheetFooter className="gap-2 border-t border-border/60 pt-3">
            <Button
              onClick={() => void handleSubmit()}
              disabled={submitDisabled}
              className="h-10"
            >
              {isPending ? "Submitting…" : isRemove ? "Remove" : isMove ? "Move" : "Submit"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}
