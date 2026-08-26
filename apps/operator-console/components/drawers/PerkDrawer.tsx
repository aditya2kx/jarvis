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
import { addEmployeePerkAction } from "@/app/payroll/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";

const PERK_TYPES = [
  { id: "mileage", label: "Mileage", cadence: "once" },
  { id: "gym", label: "Gym", cadence: "biweekly" },
  { id: "food_handler", label: "Food handler cert", cadence: "once" },
  { id: "other", label: "Other reimbursement", cadence: "once" },
] as const;

export function PerkDrawer({
  defaultPayPeriod,
  employees,
}: {
  defaultPayPeriod: string;
  employees: string[];
}) {
  const [open, setOpen] = useState(false);
  const [payPeriod, setPayPeriod] = useState(defaultPayPeriod);
  const [employee, setEmployee] = useState("");
  const [perkId, setPerkId] = useState<string>("mileage");
  const [cadence, setCadence] = useState<"once" | "biweekly">("once");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const { isPending, stage, error, run } = useConsoleAction();

  useEffect(() => {
    if (open) setPayPeriod(defaultPayPeriod);
  }, [defaultPayPeriod, open]);

  function onTypeChange(id: string) {
    setPerkId(id);
    const t = PERK_TYPES.find((p) => p.id === id);
    if (t) setCadence(t.cadence);
  }

  async function handleSubmit() {
    const n = Number(amount);
    if (!employee.trim() || Number.isNaN(n) || n <= 0) {
      setStatus("Employee and a positive amount are required.");
      return;
    }
    if (cadence === "once" && !payPeriod.trim()) {
      setStatus("Pay period is required for one-time reimbursements.");
      return;
    }
    const ack = await run(
      () =>
        addEmployeePerkAction(
          employee.trim(),
          perkId,
          n,
          payPeriod.trim(),
          cadence,
          reason.trim(),
        ),
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
      <SheetTrigger
        render={
          <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8">
            Add reimbursement…
          </Button>
        }
      />
      <SheetContent className="w-full max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Reimbursement / perk</SheetTitle>
          <SheetDescription>
            Lands on ADP as Misc reimbursement. Once = this pay period only
            (mileage, cert). Biweekly = every period (gym).
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-period">Pay period</Label>
            <Input
              id="perk-period"
              value={payPeriod}
              onChange={(e) => setPayPeriod(e.target.value)}
              readOnly
              className="bg-muted/40"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-employee">Employee</Label>
            <EmployeeCombobox
              id="perk-employee"
              value={employee}
              options={employees}
              onChange={setEmployee}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-type">Type</Label>
            <select
              id="perk-type"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={perkId}
              onChange={(e) => onTypeChange(e.target.value)}
            >
              {PERK_TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-cadence">Cadence</Label>
            <select
              id="perk-cadence"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={cadence}
              onChange={(e) =>
                setCadence(e.target.value === "biweekly" ? "biweekly" : "once")
              }
            >
              <option value="once">This period only</option>
              <option value="biweekly">Every pay period</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-amount">Amount ($)</Label>
            <Input
              id="perk-amount"
              type="number"
              min={0}
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="perk-reason">Note (optional)</Label>
            <Input
              id="perk-reason"
              placeholder="Houston trip"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>

          {feedback ? (
            <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {feedback}
            </p>
          ) : null}
        </div>

        <SheetFooter>
          <Button
            onClick={() => void handleSubmit()}
            disabled={isPending}
            className="min-h-11 sm:min-h-9"
          >
            {isPending ? "Adding…" : "Add reimbursement"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
