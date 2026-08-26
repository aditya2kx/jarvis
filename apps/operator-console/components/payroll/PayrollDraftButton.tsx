"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  pollPayrollDraftAction,
  runPayrollDraftAction,
} from "@/app/payroll/actions";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { adpPayrollChrome, adpPayrollLinkCopy } from "@/lib/payroll/adpLink";
import { previewLine } from "@/lib/payroll/previewDiff";
import { cn } from "@/lib/utils";

type DraftPhase = "idle" | "starting" | "running" | "done" | "error";

const POLL_MS = 5000;
const TIMEOUT_MS = 15 * 60 * 1000;

/** Headless ADP Start→Preview. Status + totals; no Preview URL (session hash). */
export function PayrollDraftButton({
  periodStart,
  periodEnd,
  unpaid = true,
  isCurrent = false,
  submitted = false,
  historicPayrollUrl = null,
  initialHasPreview = false,
  initialPreviewHours = null,
  initialPreviewGross = null,
  consoleHours = 0,
  consoleTotalPay = 0,
  initialStatus = null,
}: {
  periodStart: string;
  periodEnd: string;
  unpaid?: boolean;
  isCurrent?: boolean;
  submitted?: boolean;
  historicPayrollUrl?: string | null;
  initialHasPreview?: boolean;
  initialPreviewHours?: number | null;
  initialPreviewGross?: number | null;
  consoleHours?: number;
  consoleTotalPay?: number;
  initialStatus?: "running" | "ok" | "fail" | null;
}) {
  const toast = useActionToast();
  const { run, setError } = useConsoleAction();
  const [phase, setPhase] = useState<DraftPhase>(
    initialStatus === "running" ? "running" : "idle",
  );
  const [statusText, setStatusText] = useState<string | null>(
    initialStatus === "running" ? "Processing ADP Preview…" : null,
  );
  const [hasPreview, setHasPreview] = useState(initialHasPreview);
  const [previewHours, setPreviewHours] = useState<number | null>(
    initialPreviewHours,
  );
  const [previewGross, setPreviewGross] = useState<number | null>(
    initialPreviewGross,
  );
  const executionRef = useRef<string | null>(null);
  const modeRef = useRef<"local" | "cloud" | null>(null);
  const startedAtRef = useRef<number>(0);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const didAutoPoll = useRef(false);

  const copy = adpPayrollLinkCopy({ unpaid, hasPreview, submitted });
  const busy = phase === "starting" || phase === "running";
  const chrome = adpPayrollChrome({
    isCurrent,
    unpaid,
    hasPreview,
    submitted,
    running: busy,
  });
  const canRun = chrome.showButton;
  const showPreviewDiff = unpaid && hasPreview && !busy && !submitted;
  const hoursLine = previewLine(consoleHours, previewHours, "hours");
  const payLine = previewLine(consoleTotalPay, previewGross, "pay");

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const finishOk = useCallback(
    (hours?: number | null, gross?: number | null) => {
      stopPolling();
      setPhase("done");
      setError(null);
      setHasPreview(true);
      if (hours != null) setPreviewHours(hours);
      if (gross != null) setPreviewGross(gross);
      setStatusText("Preview done — review and submit in ADP");
      toast.push("ADP Preview filled — review and submit in ADP", "info");
    },
    [setError, stopPolling, toast],
  );

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
        "ADP Preview timed out after 15 minutes — check logs, then try again.",
      );
      return;
    }
    const ack = await pollPayrollDraftAction({
      executionName: executionRef.current,
      mode: modeRef.current,
      periodStart,
      periodEnd,
    });
    if (!ack.ok) {
      finishErr(ack.error);
      return;
    }
    const execution = ack.data;
    if (execution?.previewHours != null) setPreviewHours(execution.previewHours);
    if (execution?.previewGross != null) setPreviewGross(execution.previewGross);
    if (execution?.failed) {
      finishErr(execution.message ?? "ADP Preview job failed");
      return;
    }
    if (execution?.done && execution.succeeded) {
      finishOk(execution.previewHours, execution.previewGross);
      return;
    }
    if (execution?.done && !execution.succeeded) {
      finishErr(
        execution.message ?? "ADP Preview job finished without success",
      );
      return;
    }
    setStatusText("Processing ADP Preview…");
  }, [finishErr, finishOk, periodEnd, periodStart]);

  useEffect(() => {
    if (didAutoPoll.current) return;
    if (initialStatus !== "running") return;
    didAutoPoll.current = true;
    startedAtRef.current = Date.now();
    void pollOnce();
    pollTimerRef.current = setInterval(() => {
      void pollOnce();
    }, POLL_MS);
  }, [initialStatus, pollOnce]);

  const startDraft = useCallback(async () => {
    if (!canRun) return;
    if (phase === "starting" || phase === "running") return;
    stopPolling();
    setPhase("starting");
    setStatusText("Starting ADP Preview…");
    const ack = await run(
      () => runPayrollDraftAction(periodStart, periodEnd),
      {
        saving: "Starting ADP Preview…",
        queued: "ADP Preview started",
        done: "ADP Preview started",
      },
    );
    if (!ack.ok) {
      setPhase("error");
      setStatusText(ack.error);
      return;
    }
    executionRef.current = ack.data?.executionName ?? null;
    modeRef.current = ack.data?.mode ?? null;
    if (modeRef.current !== "local" && !executionRef.current) {
      finishErr("Cloud Run did not return an execution name");
      return;
    }
    startedAtRef.current = Date.now();
    setPhase("running");
    setStatusText(ack.message ?? "Processing…");
    void pollOnce();
    pollTimerRef.current = setInterval(() => {
      void pollOnce();
    }, POLL_MS);
  }, [canRun, finishErr, phase, periodEnd, periodStart, pollOnce, run, stopPolling]);

  const showLiveStatus =
    phase === "starting" ||
    phase === "running" ||
    phase === "done" ||
    phase === "error";

  if (!chrome.show) return null;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      {showPreviewDiff ? (
        <Badge variant="outline" className="font-normal">
          {copy.badge}
        </Badge>
      ) : null}
      {chrome.showLink && historicPayrollUrl ? (
        <>
          <Badge variant="secondary" className="font-normal">
            {copy.badge}
          </Badge>
          <a
            className={cn(buttonVariants({ variant: "link", size: "sm" }), "px-1")}
            href={historicPayrollUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {copy.linkText}
          </a>
        </>
      ) : null}
      {chrome.showButton ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          title="Fills ADP Preview in the background and leaves the draft. You submit in ADP if it looks right."
          onClick={() => void startDraft()}
        >
          {phase === "starting"
            ? "Starting…"
            : phase === "running"
              ? "Processing…"
              : "Run ADP Preview"}
        </Button>
      ) : null}
      {showPreviewDiff ? (
        <p className="max-w-[18rem] text-[11px] leading-tight text-muted-foreground sm:max-w-xs">
          {[hoursLine?.label, payLine?.label].filter(Boolean).join(" · ") ||
            "Last Preview captured — hours and total pay compare after the next run"}
        </p>
      ) : null}
      {showLiveStatus && statusText ? (
        <p
          className={cn(
            "max-w-[16rem] text-[11px] leading-tight sm:max-w-xs",
            phase === "error" && "text-destructive",
            phase === "done" && "text-emerald-600 dark:text-emerald-400",
            phase === "running" && "text-amber-700 dark:text-amber-400",
            phase === "starting" && "text-muted-foreground",
          )}
          role="status"
          aria-live="polite"
        >
          {statusText}
        </p>
      ) : null}
    </div>
  );
}
