"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Popover } from "@base-ui/react/popover";
import { CheckIcon, Loader2Icon } from "lucide-react";
import {
  approveUnavailabilityAction,
  pollUnavailabilityApproveAction,
} from "@/app/labor/actions";
import { Button } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;
const TIMEOUT_MS = 20 * 60 * 1000;

/** Approve one pending ADP unavailability request (headless job on Cloud Run). */
export function ApproveUnavailabilityButton({
  rowKey,
  employee,
  when,
}: {
  rowKey: string;
  employee: string;
  when: string;
}) {
  const router = useRouter();
  const toast = useActionToast();
  const { run } = useConsoleAction();
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const executionRef = useRef<string | null>(null);
  const startedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);
  useEffect(() => () => stop(), [stop]);

  const finish = useCallback(
    (text: string, error: boolean) => {
      stop();
      setRunning(false);
      toast.push(text, error ? "error" : "info");
      router.refresh();
    },
    [router, stop, toast],
  );

  const poll = useCallback(async () => {
    if (Date.now() - startedRef.current > TIMEOUT_MS) {
      finish("Approval still running after 20 minutes — check ADP before trying again.", true);
      return;
    }
    const ack = await pollUnavailabilityApproveAction({
      rowKey,
      executionName: executionRef.current,
    });
    if (!ack.ok) return finish(ack.error, true);
    const { done, execution } = ack.data ?? {};
    if (done) return finish(`Approved ${employee}'s unavailability in ADP.`, false);
    if (execution?.failed) {
      finish(`ADP approval failed: ${execution.message ?? "Cloud Run execution failed"}`, true);
    } else if (execution?.succeeded) {
      finish("The job finished but ADP still lists the request — check ADP.", true);
    }
  }, [employee, finish, rowKey]);

  const start = useCallback(async () => {
    setOpen(false);
    const ack = await run(() => approveUnavailabilityAction(rowKey), {
      saving: "Starting ADP approval…",
      queued: "ADP approval started",
      done: "ADP approval started",
    });
    if (!ack.ok) return;
    executionRef.current = ack.data?.executionName ?? null;
    startedRef.current = Date.now();
    setRunning(true);
    timerRef.current = setInterval(() => void poll(), POLL_MS);
  }, [poll, rowKey, run]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        render={
          <Button type="button" size="xs" variant="outline" disabled={running} className="gap-1">
            {running ? <Loader2Icon className="size-3 animate-spin" /> : <CheckIcon className="size-3" />}
            {running ? "Approving…" : "Approve"}
          </Button>
        }
      />
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup
            className={cn(
              "flex w-72 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg outline-none",
              "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
            )}
          >
            <div className="px-3 py-2.5">
              <p className="text-sm font-medium">Approve in ADP?</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{employee}</span> · {when}. ADP adds it to
                the schedule and notifies them. Undo only in ADP.
              </p>
            </div>
            <div className="flex justify-end gap-2 border-t border-border px-3 py-2">
              <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="button" size="sm" onClick={() => void start()}>
                Approve
              </Button>
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
