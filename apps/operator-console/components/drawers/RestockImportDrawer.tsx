"use client";

import { useState, useTransition } from "react";
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
import { submitRestockAction, replaceEstimatedRestockDateAction } from "@/app/inventory/actions";
import type { RestockAction } from "@/lib/bq/writes";
import { buildSampleCsv, type RestockRow } from "@/lib/restock/parse";

const ACTION_LABELS: Record<RestockAction, string> = {
  "add-order": "Add order (actuals)",
  "register-only": "Register date only (estimated)",
  "reset-to-estimated": "Reset to estimated",
  "replace-estimated": "Replace estimated date",
};

// Nothing writes to BQ until the operator reviews the parsed rows and hits
// Submit — mirrors the Slack restock modal's confirm step (EXECUTION.md §M3).
export function RestockImportDrawer({
  dates,
  estimatedDates = [],
  defaultAction = "add-order",
}: {
  dates: string[];
  estimatedDates?: string[];
  /** Test/default override — production always leaves this at add-order. */
  defaultAction?: RestockAction;
}) {
  const [open, setOpen] = useState(false);
  const [deliveryDate, setDeliveryDate] = useState(dates[0] ?? "");
  const [action, setAction] = useState<RestockAction>(defaultAction);
  const [fromDate, setFromDate] = useState(estimatedDates[0] ?? "");
  const [toDate, setToDate] = useState("");
  const [rows, setRows] = useState<RestockRow[]>([]);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const isReplace = action === "replace-estimated";

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
    setRows(body.rows ?? []);
    setParseErrors(body.errors ?? []);
    setStatus(body.rows?.length ? `Parsed ${body.rows.length} row(s) — review below.` : "No valid rows parsed.");
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

  function handleSubmit() {
    if (isReplace) {
      if (!fromDate || !toDate) {
        setStatus("Pick both the current estimated date and the new delivery date.");
        return;
      }
      if (!estimatedDates.length) {
        setStatus("No estimated dates to replace.");
        return;
      }
      startTransition(async () => {
        try {
          await replaceEstimatedRestockDateAction(fromDate, toDate);
          setStatus("Submitted.");
          setOpen(false);
        } catch (e) {
          setStatus(`Submit failed: ${e instanceof Error ? e.message : String(e)}`);
        }
      });
      return;
    }

    if (!deliveryDate) {
      setStatus("Pick a delivery date first.");
      return;
    }
    startTransition(async () => {
      try {
        await submitRestockAction(deliveryDate, action, rows);
        setStatus("Submitted.");
        setOpen(false);
        setRows([]);
      } catch (e) {
        setStatus(`Submit failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button size="sm">Restock…</Button>} />
      <SheetContent className="w-full max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Restock</SheetTitle>
          <SheetDescription>
            Register a delivery date, replace an estimated date, or upload a CSV/photo of the
            order — nothing writes until you submit.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4">
          <div className="flex flex-col gap-1.5">
            <Label>Action</Label>
            <Select
              value={action}
              onValueChange={(v) => {
                const next = v as RestockAction;
                setAction(next);
                setStatus(null);
                if (next === "replace-estimated" && !fromDate && estimatedDates[0]) {
                  setFromDate(estimatedDates[0]);
                }
              }}
            >
              <SelectTrigger>
                <SelectValue>
                  {(value: string | null) =>
                    value ? (ACTION_LABELS[value as RestockAction] ?? value) : "Select action"
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {Object.entries(ACTION_LABELS).map(([value, label]) => (
                  <SelectItem
                    key={value}
                    value={value}
                    disabled={value === "replace-estimated" && estimatedDates.length === 0}
                  >
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isReplace ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label>Current estimated date</Label>
                {estimatedDates.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No estimated dates to replace.</p>
                ) : (
                  <Select value={fromDate} onValueChange={(v) => setFromDate(v ?? "")}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select date" />
                    </SelectTrigger>
                    <SelectContent>
                      {estimatedDates.map((d) => (
                        <SelectItem key={d} value={d}>
                          {d}
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
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                />
              </div>
            </>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="delivery-date">Delivery date</Label>
              <Input
                id="delivery-date"
                type="date"
                value={deliveryDate}
                onChange={(e) => setDeliveryDate(e.target.value)}
              />
            </div>
          )}

          {action === "add-order" && (
            <div className="flex flex-col gap-1.5">
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
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleFile(f);
                }}
              />
            </div>
          )}

          {status ? <p className="text-sm text-muted-foreground">{status}</p> : null}

          {parseErrors.length ? (
            <ul className="list-disc pl-4 text-sm text-destructive">
              {parseErrors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}

          {rows.length ? (
            <div className="flex flex-col gap-2">
              <Label>Review before submitting</Label>
              {rows.map((r) => (
                <div key={r.item} className="flex items-center justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm">{r.item}</span>
                  <Input
                    type="number"
                    min={0}
                    step="1"
                    className="w-24"
                    value={r.quantityTubs}
                    onChange={(e) => updateQty(r.item, Number(e.target.value))}
                  />
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <SheetFooter>
          <Button onClick={handleSubmit} disabled={isPending || (isReplace && estimatedDates.length === 0)}>
            {isPending ? "Submitting…" : "Submit"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
