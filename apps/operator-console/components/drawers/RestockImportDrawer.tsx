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
import { submitRestockAction } from "@/app/inventory/actions";
import type { RestockAction } from "@/lib/bq/writes";
import type { RestockRow } from "@/lib/restock/parse";

const ACTION_LABELS: Record<RestockAction, string> = {
  "add-order": "Add order (actuals)",
  "register-only": "Register date only (estimated)",
  "reset-to-estimated": "Reset to estimated",
};

// Nothing writes to BQ until the operator reviews the parsed rows and hits
// Submit — mirrors the Slack restock modal's confirm step (EXECUTION.md §M3).
export function RestockImportDrawer({ dates }: { dates: string[] }) {
  const [open, setOpen] = useState(false);
  const [deliveryDate, setDeliveryDate] = useState(dates[0] ?? "");
  const [action, setAction] = useState<RestockAction>("add-order");
  const [rows, setRows] = useState<RestockRow[]>([]);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

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

  function updateQty(item: string, quantityTubs: number) {
    setRows((prev) => prev.map((r) => (r.item === item ? { ...r, quantityTubs } : r)));
  }

  function handleSubmit() {
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
            Register a delivery date, then optionally upload a CSV or photo of the order —
            nothing writes until you submit.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="delivery-date">Delivery date</Label>
            <Input
              id="delivery-date"
              type="date"
              value={deliveryDate}
              onChange={(e) => setDeliveryDate(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Action</Label>
            <Select value={action} onValueChange={(v) => setAction(v as RestockAction)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(ACTION_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {action === "add-order" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="restock-file">Order CSV or photo</Label>
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
          <Button onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Submitting…" : "Submit"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
