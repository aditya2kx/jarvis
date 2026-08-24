"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  pollPayrollDraftAction,
  runPayrollDraftAction,
} from "@/app/payroll/actions";
import { Button } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { cn } from "@/lib/utils";

type DraftPhase = "idle" | "starting" | "running" | "done" | "error";

const POLL_MS = 5000;
const TIMEOUT_MS = 15 * 60 * 1000;

/** Start ADP RUN payroll, fill Preview, then Delete. Never Approve. */
export function PayrollDraftButton({
  periodStart,
  periodEnd,
}: {
  periodStart: string;
  periodEnd: string;
}) {
  const toast = useActionToast();
  const { run, setError } = useConsoleAction();
  const [phase, setPhase] = useState<DraftPhase>("idle");
  const [statusText, setStatusText] = useState<string | null>(null);
  const executionRef = useRef<string | null>(null);
  const startedAtRef = useRef<number>(0);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const finishOk = useCallback(() => {
    stopPolling();
    setPhase("done");
    setError(null);
    setStatusText("Preview filled and deleted — check Cloud Run logs for COMPARE");
    toast.push("ADP Preview ran and was deleted", "info");
    window.setTimeout(() => {
      setPhase("idle");
      setStatusText(null);
    }, 10000);
  }, [setError, stopPolling, toast]);

  const finishErr = useCallback(
    (msg: string) => {
      stopPolling();
      setPhase("error");
      setError(msg);
      setStatusText(msg);
      toast.push(msg, "error");
    },
    [setError, stopPolling, toast],
  );

  const pollOnce = useCallback(async () => {
    if (Date.now() - startedAtRef.current > TIMEOUT_MS) {
      finishErr(
        "ADP Preview timed out after 15 minutes — check Cloud Run logs, then try again.",
      );
      return;
    }
    const name = executionRef.current;
    if (!name) {
      finishErr("Missing Cloud Run execution name");
      return;
    }
    const ack = await pollPayrollDraftAction({ executionName: name });
    if (!ack.ok) {
      finishErr(ack.error);
      return;
    }
    const execution = ack.data;
    if (execution?.failed) {
      finishErr(execution.message ?? "ADP Preview job failed");
      return;
    }
    if (execution?.done && execution.succeeded) {
      finishOk();
      return;
    }
    if (execution?.done && !execution.succeeded) {
      finishErr(execution.message ?? "ADP Preview job finished without success");
      return;
    }
    setStatusText("Filling ADP Preview… then Delete (never Approve)");
  }, [finishErr, finishOk]);

  const startDraft = useCallback(async () => {
    if (phase === "starting" || phase === "running") return;
    stopPolling();
    setPhase("starting");
    setStatusText("Starting ADP Preview…");
    const ack = await run(
      () => runPayrollDraftAction(periodStart, periodEnd),
      {
        saving: "Starting ADP Preview…",
        queued: "ADP Preview queued in the background",
        done: "ADP Preview started",
      },
    );
    if (!ack.ok) {
      setPhase("error");
      setStatusText(ack.error);
      return;
    }
    executionRef.current = ack.data?.executionName ?? null;
    if (!executionRef.current) {
      finishErr("Cloud Run did not return an execution name");
      return;
    }
    startedAtRef.current = Date.now();
    setPhase("running");
    setStatusText(ack.message ?? "Running…");
    void pollOnce();
    pollTimerRef.current = setInterval(() => {
      void pollOnce();
    }, POLL_MS);
  }, [finishErr, phase, periodEnd, periodStart, pollOnce, run, stopPolling]);

  const busy = phase === "starting" || phase === "running";

  return (
    <div className="flex max-w-[18rem] flex-col items-end gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => void startDraft()}
      >
        {phase === "starting"
          ? "Starting…"
          : phase === "running"
            ? "Running Preview…"
            : "Run ADP Preview (delete after)"}
      </Button>
      <p
        className={cn(
          "text-right text-[11px] leading-snug",
          phase === "error" && "text-destructive",
          phase === "done" && "text-emerald-600 dark:text-emerald-400",
          phase === "running" && "text-amber-700 dark:text-amber-400",
          (phase === "idle" || phase === "starting") && "text-muted-foreground",
        )}
        role="status"
        aria-live="polite"
      >
        {statusText
          ? statusText
          : "Unpaid cycle only · never Approve"}
      </p>
    </div>
  );
}
