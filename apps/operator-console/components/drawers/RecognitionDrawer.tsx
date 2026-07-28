"use client";

import { useEffect, useState } from "react";
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
import { EmployeeCombobox } from "@/components/filters/EmployeeCombobox";
import { addRecognitionBonusAction } from "@/app/payroll/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";

// A recognition bonus is a richer, less-frequent write (period + employee +
// dollar amount + reason) — drawer, not inline, per the write-UX hybrid
// pattern. Amount is entered in dollars and converted to integer cents in
// the server action (migration 033's amount_cents invariant).
export function RecognitionDrawer({
  defaultPayPeriod,
  employees,
}: {
  /** `YYYY-MM-DD..YYYY-MM-DD` from the page Period selector. */
  defaultPayPeriod: string;
  employees: string[];
}) {
  const [open, setOpen] = useState(false);
  const [payPeriod, setPayPeriod] = useState(defaultPayPeriod);
  const [employee, setEmployee] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const { isPending, stage, error, run } = useConsoleAction();

  useEffect(() => {
    if (open) setPayPeriod(defaultPayPeriod);
  }, [defaultPayPeriod, open]);

  async function handleSubmit() {
    const n = Number(amount);
    if (!employee.trim() || !payPeriod.trim() || Number.isNaN(n) || n <= 0) {
      setStatus("Employee, pay period, and a positive amount are required.");
      return;
    }
    const ack = await run(
      () => addRecognitionBonusAction(payPeriod.trim(), employee.trim(), n, reason.trim()),
      { saving: "Adding…", done: "Added." },
    );
    if (ack.ok) {
      setOpen(false);
      setEmployee("");
      setAmount("");
      setReason("");
      setStatus(null);
    }
  }

  const feedback = stage || error || status;

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
              placeholder="2026-07-13..2026-07-26"
              value={payPeriod}
              onChange={(e) => setPayPeriod(e.target.value)}
              readOnly
              className="bg-muted/40"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rec-employee">Employee</Label>
            <EmployeeCombobox
              id="rec-employee"
              value={employee}
              options={employees}
              onChange={setEmployee}
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

          {feedback ? (
            <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {feedback}
            </p>
          ) : null}
        </div>

        <SheetFooter>
          <Button onClick={() => void handleSubmit()} disabled={isPending}>
            {isPending ? "Adding…" : "Add bonus"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
