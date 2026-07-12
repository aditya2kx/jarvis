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
import { addRecognitionBonusAction } from "@/app/payroll/actions";

// A recognition bonus is a richer, less-frequent write (period + employee +
// dollar amount + reason) — drawer, not inline, per the write-UX hybrid
// pattern. Amount is entered in dollars and converted to integer cents in
// the server action (migration 033's amount_cents invariant).
export function RecognitionDrawer({ defaultPayPeriod }: { defaultPayPeriod: string }) {
  const [open, setOpen] = useState(false);
  const [payPeriod, setPayPeriod] = useState(defaultPayPeriod);
  const [employee, setEmployee] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleSubmit() {
    const n = Number(amount);
    if (!employee.trim() || !payPeriod.trim() || Number.isNaN(n) || n <= 0) {
      setStatus("Employee, pay period, and a positive amount are required.");
      return;
    }
    startTransition(async () => {
      try {
        await addRecognitionBonusAction(payPeriod.trim(), employee.trim(), n, reason.trim());
        setStatus("Added.");
        setOpen(false);
        setEmployee("");
        setAmount("");
        setReason("");
      } catch (e) {
        setStatus(`Failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button variant="outline" size="sm">Add recognition bonus…</Button>} />
      <SheetContent className="w-full max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Recognition bonus</SheetTitle>
          <SheetDescription>
            Manual bonus, separate from the automated Google-review bonus below —
            reconciled against the ADP bonus earnings line.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rec-period">Pay period</Label>
            <Input
              id="rec-period"
              placeholder="2026-07-01..2026-07-15"
              value={payPeriod}
              onChange={(e) => setPayPeriod(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rec-employee">Employee</Label>
            <Input
              id="rec-employee"
              placeholder="Last, First"
              value={employee}
              onChange={(e) => setEmployee(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rec-amount">Amount ($)</Label>
            <Input
              id="rec-amount"
              type="number"
              min={0}
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rec-reason">Reason</Label>
            <Input id="rec-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>

          {status ? <p className="text-sm text-muted-foreground">{status}</p> : null}
        </div>

        <SheetFooter>
          <Button onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Adding…" : "Add bonus"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
