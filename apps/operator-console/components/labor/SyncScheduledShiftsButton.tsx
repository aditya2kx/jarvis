"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  pollScheduledShiftsSyncAction,
  syncScheduledShiftsAction,
} from "@/app/labor/actions";
import { Button } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { cn } from "@/lib/utils";

function formatScraped(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-US", {
    timeZone: "America/Chicago",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type SyncPhase = "idle" | "starting" | "running" | "done" | "error";

const POLL_MS = 4000;
const TIMEOUT_MS = 6 * 60 * 1000;

/** Sync ADP Team Schedule; page stays usable; status chip tracks progress. */
export function SyncScheduledShiftsButton({
  lastScrapedAt,
}: {
  lastScrapedAt: string | null;
}) {
  const router = useRouter();
  const toast = useActionToast();
  const { run, setError } = useConsoleAction();
  const [phase, setPhase] = useState<SyncPhase>("idle");
  const [statusText, setStatusText] = useState<string | null>(null);
  const [scrapedAt, setScrapedAt] = useState<string | null>(lastScrapedAt);
  const baselineRef = useRef<string | null>(null);
  const executionRef = useRef<string | null>(null);
  const startedAtRef = useRef<number>(0);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setScrapedAt(lastScrapedAt);
  }, [lastScrapedAt]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const finishOk = useCallback(
    (newScraped: string | null) => {
      stopPolling();
      setPhase("done");
      setError(null);
      setScrapedAt(newScraped);
      setStatusText(
        `Synced ${formatScraped(newScraped) ?? "just now"} CT — refreshing charts…`,
      );
      toast.push("Scheduled shifts synced", "info");
      router.refresh();
      window.setTimeout(() => {
        setPhase("idle");
        setStatusText(null);
      }, 8000);
    },
    [router, setError, stopPolling, toast],
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
        "Sync timed out after 6 minutes — check ADP login / Cloud Run logs, then try again.",
      );
      return;
    }
    // Poll stays outside `run()` so we don't toast every 4s.
    const ack = await pollScheduledShiftsSyncAction({
      baselineScrapedAt: baselineRef.current,
      executionName: executionRef.current,
    });
    if (!ack.ok) {
      finishErr(ack.error);
      return;
    }
    const { scrapedAt: latest, advanced, execution } = ack.data ?? {};
    if (advanced) {
      finishOk(latest ?? null);
      return;
    }
    if (execution?.failed) {
      finishErr(
        execution.message?.includes("not yet complete")
          ? "Cloud job failed: schedule-only path not deployed yet (completeness gate). Use local console with BYPASS_IAP, or merge/deploy this branch first."
          : `Sync failed: ${execution.message ?? "Cloud Run execution failed"}`,
      );
      return;
    }
    if (execution?.done && execution.succeeded && !advanced) {
      finishErr(
        "Job finished but schedule data did not update — check ADP scrape logs.",
      );
      return;
    }
    setStatusText(
      execution?.done
        ? "Finishing…"
        : "Syncing scheduled shifts… (you can keep using the page)",
    );
  }, [finishErr, finishOk]);

  const startSync = useCallback(async () => {
    if (phase === "starting" || phase === "running") return;
    stopPolling();
    setPhase("starting");
    setStatusText("Starting sync…");
    const ack = await run(() => syncScheduledShiftsAction(), {
      saving: "Starting sync…",
      queued: "Schedule sync queued in the background",
      done: "Schedule sync started in the background",
    });
    if (!ack.ok) {
      setPhase("error");
      setStatusText(ack.error);
      return;
    }
    const data = ack.data;
    baselineRef.current = data?.baselineScrapedAt ?? scrapedAt;
    executionRef.current = data?.executionName ?? null;
    startedAtRef.current = Date.now();
    setPhase("running");
    setStatusText(data?.message ?? "Syncing…");
    void pollOnce();
    pollTimerRef.current = setInterval(() => {
      void pollOnce();
    }, POLL_MS);
  }, [phase, pollOnce, run, scrapedAt, stopPolling]);

  const scrapedLabel = formatScraped(scrapedAt);
  const busy = phase === "starting" || phase === "running";

  return (
    <div className="flex max-w-[16rem] flex-col items-end gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => void startSync()}
      >
        {phase === "starting"
          ? "Starting…"
          : phase === "running"
            ? "Syncing…"
            : "Sync scheduled shifts"}
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
          : scrapedLabel
            ? `Last synced ${scrapedLabel} CT`
            : "Never synced"}
      </p>
    </div>
  );
}
