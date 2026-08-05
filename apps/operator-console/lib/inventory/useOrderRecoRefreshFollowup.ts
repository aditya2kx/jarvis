"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { pollOrderRecoRefreshAction } from "@/app/inventory/actions";
import { useActionToast } from "@/lib/actions/ActionToast";

const RECO_POLL_MS = 3000;
const RECO_TIMEOUT_MS = 3 * 60 * 1000;

export type OrderRecoFollowupInput = {
  queued?: string[] | null;
  baselineRefreshedAt?: string | null;
};

/**
 * After any inventory write that enqueues async order-reco: immediate
 * router.refresh(), then poll refreshed_at and refresh again when it advances
 * (Capacity / Restock / usage-day Apply).
 */
export function useOrderRecoRefreshFollowup(opts?: {
  pendingBanner?: string;
  doneToast?: string;
  timeoutBanner?: string;
}) {
  const router = useRouter();
  const toast = useActionToast();
  const [banner, setBanner] = useState<string | null>(null);
  const baselineRef = useRef<string | null>(null);
  const startedAtRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pendingBanner =
    opts?.pendingBanner ??
    "Order recommendation refreshing — Order tubs / Avg/day update when ready.";
  const doneToast = opts?.doneToast ?? "Order recommendation updated";
  const timeoutBanner =
    opts?.timeoutBanner ??
    "Recommendation still refreshing — reload the page in a minute if numbers look stale.";

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const finishOk = useCallback(() => {
    stopPolling();
    setBanner(null);
    toast.push(doneToast, "info");
    router.refresh();
  }, [doneToast, router, stopPolling, toast]);

  const finishTimeout = useCallback(() => {
    stopPolling();
    setBanner(timeoutBanner);
    toast.push("Order recommendation still refreshing — try reload shortly", "error");
  }, [stopPolling, timeoutBanner, toast]);

  const pollOnce = useCallback(async () => {
    if (Date.now() - startedAtRef.current > RECO_TIMEOUT_MS) {
      finishTimeout();
      return;
    }
    const ack = await pollOrderRecoRefreshAction({
      baselineRefreshedAt: baselineRef.current,
    });
    if (!ack.ok) {
      stopPolling();
      setBanner(null);
      toast.push(ack.error, "error");
      return;
    }
    if (ack.data?.advanced) {
      finishOk();
    }
  }, [finishOk, finishTimeout, stopPolling, toast]);

  const followOrderReco = useCallback(
    (input: OrderRecoFollowupInput) => {
      // Config / override rows are live; tables need a refresh either way.
      router.refresh();
      if (!input.queued?.length) {
        return;
      }
      stopPolling();
      baselineRef.current = input.baselineRefreshedAt ?? null;
      startedAtRef.current = Date.now();
      setBanner(pendingBanner);
      void pollOnce();
      pollTimerRef.current = setInterval(() => {
        void pollOnce();
      }, RECO_POLL_MS);
    },
    [pendingBanner, pollOnce, router, stopPolling],
  );

  return { banner, followOrderReco, stopPolling };
}
