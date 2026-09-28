"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { AlertTriangleIcon, CheckCircle2Icon, Loader2Icon, RotateCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { orderRecoStatusAction, retryOrderRecoAction } from "@/app/inventory/actions";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import {
  awaitedRunLanded,
  describeChanges,
  hasCommittedSince,
  relativeAgo,
  secondsSince,
  typicalSeconds,
  type OrderRecoStatus,
} from "@/lib/inventory/orderRecoStatus";
import { cn } from "@/lib/utils";

const ACTIVE_POLL_MS = 2_000;
/** Idle polling still notices refreshes started elsewhere (Slack, nightly). */
const IDLE_POLL_MS = 20_000;
const AWAIT_TIMEOUT_MS = 3 * 60 * 1000;

type Ctx = {
  status: OrderRecoStatus;
  /** An edit from this tab is waiting for its refresh to commit. */
  awaiting: boolean;
  timedOut: boolean;
  lastChange: string;
  follow: (runId?: string | null) => void;
  retry: () => Promise<void>;
  retrying: boolean;
};

const OrderRecoStatusCtx = createContext<Ctx | null>(null);

export function useOrderRecoStatus(): Ctx | null {
  return useContext(OrderRecoStatusCtx);
}

/**
 * Page-level owner of the order-reco refresh lifecycle (Issue #350). Drawers
 * close right after Apply, so the poll must not live inside them: this
 * provider survives, repaints the table when a newer generation commits, and
 * tells the operator what changed.
 */
export function OrderRecoStatusProvider({
  initialStatus,
  children,
}: {
  initialStatus: OrderRecoStatus;
  children: ReactNode;
}) {
  const router = useRouter();
  const toast = useActionToast();
  const [status, setStatus] = useState(initialStatus);
  const [awaitingSince, setAwaitingSince] = useState<number | null>(() =>
    initialStatus.state === "running" ? Date.now() : null,
  );
  const [timedOut, setTimedOut] = useState(false);
  const [lastChange, setLastChange] = useState("");
  const baselineRef = useRef<string | null>(initialStatus.lastCommittedAt);
  const awaitRunIdRef = useRef<string | null>(
    initialStatus.state === "running" ? initialStatus.runId : null,
  );
  const inFlight = useRef(false);

  const onCommitted = useCallback(
    async (next: OrderRecoStatus, announce: boolean) => {
      // Flip to "Up to date" only once the summary is in hand, so both paint together.
      const ack = await orderRecoStatusAction({ withChanges: true });
      const summary = ack.ok ? describeChanges(ack.data?.changes ?? []) : "";
      baselineRef.current = next.lastCommittedAt;
      awaitRunIdRef.current = null;
      setStatus(next);
      setLastChange(summary);
      setAwaitingSince(null);
      setTimedOut(false);
      router.refresh();
      if (announce) {
        toast.push(summary ? `Order recommendation updated · ${summary}` : "Order recommendation updated", "info");
      }
    },
    [router, toast],
  );

  const poll = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const awaitRunId = awaitingSince != null ? awaitRunIdRef.current : null;
      const ack = await orderRecoStatusAction({ awaitRunId });
      if (!ack.ok || !ack.data) return;
      const next = ack.data.status;
      if (awaitingSince == null) {
        // A refresh started elsewhere (another tab, Slack, nightly) — repaint quietly.
        if (next.state !== "running" && hasCommittedSince(next, baselineRef.current)) {
          await onCommitted(next, false);
        } else {
          setStatus(next);
        }
        return;
      }
      if (awaitedRunLanded(next, awaitRunId, baselineRef.current)) {
        await onCommitted(next, true);
        return;
      }
      setStatus(next);
      const failed = awaitRunId ? next.awaitedStatus === "failed" : next.state === "failed";
      if (failed) {
        awaitRunIdRef.current = null;
        setAwaitingSince(null);
        toast.push("Order recommendation refresh failed — Retry from the banner", "error");
      } else if (Date.now() - awaitingSince > AWAIT_TIMEOUT_MS) {
        setTimedOut(true);
      }
    } finally {
      inFlight.current = false;
    }
  }, [awaitingSince, onCommitted, toast]);

  const active = awaitingSince != null || status.state === "running";
  useEffect(() => {
    const id = window.setInterval(() => void poll(), active ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    return () => window.clearInterval(id);
  }, [active, poll]);

  const follow = useCallback((runId?: string | null) => {
    awaitRunIdRef.current = runId ?? null;
    setAwaitingSince(Date.now());
    setTimedOut(false);
  }, []);

  const { run, isPending: retrying } = useConsoleAction();
  const retry = useCallback(async () => {
    const ack = await run(() => retryOrderRecoAction(), {
      saving: "Requesting refresh…",
      queued: "Order recommendation refresh started",
    });
    if (ack.ok) follow(ack.data?.runId ?? null);
  }, [follow, run]);

  const value = useMemo(
    () => ({ status, awaiting: awaitingSince != null, timedOut, lastChange, follow, retry, retrying }),
    [status, awaitingSince, timedOut, lastChange, follow, retry, retrying],
  );
  return <OrderRecoStatusCtx.Provider value={value}>{children}</OrderRecoStatusCtx.Provider>;
}

/** Re-render every second while a refresh is running so "started 12s ago" stays live. */
function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [enabled]);
  return now;
}

/** Status line above the Order Recommendation table: running / stale / failed / fresh. */
export function OrderRecoStatusBanner() {
  const ctx = useOrderRecoStatus();
  const running = Boolean(ctx && (ctx.awaiting || ctx.status.state === "running"));
  const now = useNow(running);
  if (!ctx) return null;
  const { status, timedOut, lastChange, retry, retrying } = ctx;

  if (running) {
    const elapsed = secondsSince(status.state === "running" ? status.startedAt : null, now);
    return (
      <Line tone="progress" icon={<Loader2Icon className="h-4 w-4 animate-spin" aria-hidden />}>
        <span className="font-medium text-foreground">Updating recommendation</span>
        <span>
          {elapsed != null ? `started ${elapsed}s ago · ` : ""}usually ~{typicalSeconds(status)}s
          {timedOut ? " · taking longer than usual" : ""}
        </span>
        <span className="basis-full text-xs sm:basis-auto">
          Safe to keep editing — the latest edit always wins; numbers repaint when it finishes.
        </span>
      </Line>
    );
  }

  if (status.state === "failed") {
    return (
      <Line tone="error" icon={<AlertTriangleIcon className="h-4 w-4" aria-hidden />}>
        <span className="font-medium">Last refresh failed</span>
        <span className="min-w-0 truncate" title={status.error ?? undefined}>
          Numbers below are from {relativeAgo(status.lastCommittedAt, now) ?? "an earlier run"}.
        </span>
        <Button size="sm" variant="outline" disabled={retrying} onClick={() => void retry()}>
          <RotateCwIcon className={cn("mr-1 h-3.5 w-3.5", retrying && "animate-spin")} aria-hidden />
          Retry
        </Button>
      </Line>
    );
  }

  if (status.state === "stale") {
    return (
      <Line tone="warn" icon={<AlertTriangleIcon className="h-4 w-4" aria-hidden />}>
        <span className="font-medium text-foreground">Inputs changed since these numbers</span>
        <span>were computed.</span>
        <Button size="sm" variant="outline" disabled={retrying} onClick={() => void retry()}>
          <RotateCwIcon className={cn("mr-1 h-3.5 w-3.5", retrying && "animate-spin")} aria-hidden />
          Update now
        </Button>
      </Line>
    );
  }

  const ago = relativeAgo(status.lastCommittedAt, now);
  if (!ago) return null;
  return (
    <Line tone="ok" icon={<CheckCircle2Icon className="h-4 w-4" aria-hidden />}>
      <span>Up to date · updated {ago}</span>
      {lastChange ? <span className="text-foreground">· {lastChange}</span> : null}
    </Line>
  );
}

function Line({
  tone,
  icon,
  children,
}: {
  tone: "progress" | "warn" | "error" | "ok";
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="order-reco-status"
      data-tone={tone}
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-sm",
        tone === "progress" && "border-border bg-muted/40 text-muted-foreground",
        tone === "warn" && "border-amber-300/60 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-100",
        tone === "error" && "border-destructive/40 bg-destructive/5 text-destructive",
        tone === "ok" && "border-transparent px-0 py-0 text-xs text-muted-foreground",
      )}
    >
      <span className={cn("shrink-0", tone === "ok" && "text-emerald-600 dark:text-emerald-400")}>{icon}</span>
      {children}
    </div>
  );
}
