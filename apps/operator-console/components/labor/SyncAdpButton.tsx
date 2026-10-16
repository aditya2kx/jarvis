"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  pollAdpSyncAction,
  runningAdpSyncAction,
  syncAdpAction,
} from "@/app/labor/actions";
import { Button } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { decideSyncPoll } from "@/lib/labor/sync-adp-poll";
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

/** One "Sync ADP": clocked hours, schedule (assigned + open), payroll earnings, rates. */
export function SyncAdpButton({
  lastScrapedAt,
  targetDate,
}: {
  lastScrapedAt: string | null;
  targetDate: string;
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
        `Synced ${formatScraped(newScraped) ?? "just now"} CT — refreshing…`,
      );
      toast.push("ADP synced", "info");
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
    const ack = await pollAdpSyncAction({
      baselineScrapedAt: baselineRef.current,
      executionName: executionRef.current,
    });
    if (!ack.ok) {
      finishErr(ack.error);
      return;
    }
    const { scrapedAt: latest, advanced, execution } = ack.data ?? {};
    // Clocked hours land mid-job. With an execution to watch, only its
    // completion ends the sync — a fixed 20-minute clock does not.
    const decision = decideSyncPoll({
      elapsedMs: Date.now() - startedAtRef.current,
      hasExecution: Boolean(executionRef.current),
      advanced: Boolean(advanced),
      execution,
    });
    if (decision.kind === "ok") {
      finishOk(latest ?? null);
      return;
    }
    if (decision.kind === "error") {
      finishErr(decision.message);
      return;
    }
    setStatusText(decision.status);
  }, [finishErr, finishOk]);

  const watch = useCallback(
    (executionName: string | null, baseline: string | null, message: string) => {
      baselineRef.current = baseline;
      executionRef.current = executionName;
      startedAtRef.current = Date.now();
      setPhase("running");
      setStatusText(message);
      void pollOnce();
      pollTimerRef.current = setInterval(() => {
        void pollOnce();
      }, POLL_MS);
    },
    [pollOnce],
  );

  // A sync started before a reload keeps running on Cloud Run — pick its status back up.
  useEffect(() => {
    let cancelled = false;
    void runningAdpSyncAction().catch(() => null).then((ack) => {
      if (cancelled || !ack?.ok || !ack.data || pollTimerRef.current) return;
      watch(ack.data.executionName, ack.data.baselineScrapedAt, "Syncing ADP… (started earlier)");
    });
    return () => {
      cancelled = true;
    };
  }, [watch]);

  const startSync = useCallback(async () => {
    if (phase === "starting" || phase === "running") return;
    stopPolling();
    setPhase("starting");
    setStatusText("Starting sync…");
    const ack = await run(() => syncAdpAction(targetDate), {
      saving: "Starting sync…",
      queued: "ADP sync queued in the background",
      done: "ADP sync started in the background",
    });
    if (!ack.ok) {
      setPhase("error");
      setStatusText(ack.error);
      return;
    }
    const data = ack.data;
    watch(data?.executionName ?? null, data?.baselineScrapedAt ?? scrapedAt, data?.message ?? "Syncing…");
  }, [phase, run, scrapedAt, stopPolling, targetDate, watch]);

  const scrapedLabel = formatScraped(scrapedAt);
  const busy = phase === "starting" || phase === "running";
  const idleHint = scrapedLabel
    ? `Last synced ${scrapedLabel} CT`
    : "Never synced";

  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        title={phase === "idle" ? idleHint : undefined}
        onClick={() => void startSync()}
      >
        {phase === "starting"
          ? "Starting…"
          : phase === "running"
            ? "Syncing…"
            : "Sync ADP"}
      </Button>
      {phase !== "idle" && statusText ? (
      <p
        className={cn(
          "text-[11px] leading-snug",
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
