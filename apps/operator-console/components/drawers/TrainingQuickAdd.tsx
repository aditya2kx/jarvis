"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { addTrainingShiftAction } from "@/app/payroll/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";

// Inline quick-add, not a drawer — a training mark is a single frequent
// entry (name + date), matching the hybrid write-UX pattern in PLAN.md.
// The employee name must already be canonical (no alias resolution here,
// unlike the Slack `training set` command) — operators type the name as it
// appears in the per-employee table above.
export function TrainingQuickAdd() {
  const [open, setOpen] = useState(false);
  const [employee, setEmployee] = useState("");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const { isPending, stage, error, run } = useConsoleAction();

  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)}>
        + Training shift
      </Button>
    );
  }

  const feedback = stage || error || status;

  return (
    <div className="flex flex-wrap items-end gap-2 rounded-md border p-3">
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">Employee</label>
        <Input
          className="w-48"
          placeholder="Last, First"
          value={employee}
          onChange={(e) => setEmployee(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">Date</label>
        <Input className="w-36" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">Note (optional)</label>
        <Input className="w-48" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <Button
        size="sm"
        disabled={isPending}
        onClick={() => {
          if (!employee.trim() || !date) {
            setStatus("Employee and date are required.");
            return;
          }
          void run(
            () => addTrainingShiftAction(employee.trim(), date, note.trim()),
            { saving: "Adding…", done: "Added." },
          ).then((ack) => {
            if (ack.ok) {
              setEmployee("");
              setDate("");
              setNote("");
              setOpen(false);
            }
          });
        }}
      >
        {isPending ? "Adding…" : "Add"}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {feedback ? (
        <span className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
          {feedback}
        </span>
      ) : null}
    </div>
  );
}
