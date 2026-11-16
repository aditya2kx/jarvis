"use client";

import { useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { endEmployeePerkAction } from "@/app/payroll/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { formatCents } from "@/lib/format";
import { labelPerkId } from "@/lib/payroll/perkLabels";
import { WARN_BADGE_CLASS } from "@/lib/run-status";
import { cn } from "@/lib/utils";
import type { PeriodPerkRow } from "@/lib/bq/queries";

export type PerkManage = {
  /** Selected unpaid pay period the perk stops from. */
  periodStart: string;
  periodLabel: string;
};

/** A perk on the /payroll Perks cell that opens its sheet: details + Stop / Remove. */
export function PerkChip({
  perk,
  manage,
  showAmount,
}: {
  perk: PeriodPerkRow;
  manage: PerkManage;
  showAmount: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const { isPending, stage, error, run } = useConsoleAction();
  const recurring = perk.pay_period === "";
  const label = labelPerkId(perk.perk_id);
  const warn = recurring && perk.terminated;
  const verb = recurring ? "Stop perk" : "Remove reimbursement";

  async function end() {
    const ack = await run(
      () => endEmployeePerkAction(perk.employee, perk.perk_id, perk.pay_period, manage.periodStart),
      { saving: recurring ? "Stopping…" : "Removing…", done: recurring ? "Stopped." : "Removed." },
    );
    if (ack.ok) setOpen(false);
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setConfirming(false);
      }}
    >
      <SheetTrigger
        render={
          <button
            type="button"
            aria-label={`${label} for ${perk.employee} — details and remove`}
            className="rounded-4xl focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
        }
      >
        <Badge
          variant="outline"
          className={cn(
            "h-6 cursor-pointer pr-1.5 font-normal hover:bg-muted max-md:h-8 max-md:px-2.5",
            warn && WARN_BADGE_CLASS,
          )}
        >
          {showAmount ? `${label} ${formatCents(perk.amount_cents)}` : label}
          <ChevronRightIcon aria-hidden className="text-muted-foreground" />
        </Badge>
      </SheetTrigger>
      <SheetContent className="w-full max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>
            {label} · {perk.employee}
          </SheetTitle>
          <SheetDescription>
            {formatCents(perk.amount_cents)} ·{" "}
            {recurring ? "every pay period" : `this pay period only (${manage.periodLabel})`}
          </SheetDescription>
        </SheetHeader>

        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 px-4 text-sm">
          {perk.note ? (
            <>
              <dt className="text-muted-foreground">Note</dt>
              <dd>{perk.note}</dd>
            </>
          ) : null}
          {perk.updated_by ? (
            <>
              <dt className="text-muted-foreground">Added by</dt>
              <dd className="break-all">{perk.updated_by}</dd>
            </>
          ) : null}
        </dl>

        {perk.terminated ? (
          <p className="mx-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
            Terminated in ADP.{" "}
            {recurring
              ? "This perk keeps paying every period they are on payroll until you stop it."
              : "Keep it only if it belongs on their final paycheck."}
          </p>
        ) : null}

        <SheetFooter className="gap-3 border-t">
          {confirming ? (
            <p className="text-sm text-muted-foreground" role="status">
              {recurring
                ? `Stops from ${manage.periodLabel} on. Earlier pay periods keep it.`
                : `Comes off ${manage.periodLabel}.`}{" "}
              The record is kept.
            </p>
          ) : null}
          {stage || error ? (
            <p className={cn("text-sm", error ? "text-destructive" : "text-muted-foreground")} role="status">
              {error ?? stage}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {confirming ? (
              <>
                <Button
                  variant="destructive"
                  className="min-h-11 sm:min-h-9"
                  disabled={isPending}
                  onClick={() => void end()}
                  data-testid="confirm-end-perk"
                >
                  {isPending ? (recurring ? "Stopping…" : "Removing…") : verb}
                </Button>
                <Button
                  variant="ghost"
                  className="min-h-11 sm:min-h-9"
                  disabled={isPending}
                  onClick={() => setConfirming(false)}
                >
                  Keep
                </Button>
              </>
            ) : (
              <Button
                variant="ghost"
                className="min-h-11 text-destructive hover:bg-destructive/10 hover:text-destructive sm:min-h-9"
                onClick={() => setConfirming(true)}
              >
                {verb}…
              </Button>
            )}
            <Button
              variant="outline"
              className="ml-auto min-h-11 sm:min-h-9"
              onClick={() => setOpen(false)}
            >
              Close
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
