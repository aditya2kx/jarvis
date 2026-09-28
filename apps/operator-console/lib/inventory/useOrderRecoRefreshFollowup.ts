"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { useOrderRecoStatus } from "@/components/inventory/OrderRecoStatus";

export type OrderRecoFollowupInput = {
  queued?: string[] | null;
  /** Action ack data; carries `runId` of the refresh this write started. */
  data?: unknown;
};

function runIdOf(data: unknown): string | null {
  if (!data || typeof data !== "object" || !("runId" in data)) return null;
  const id = (data as { runId?: unknown }).runId;
  return typeof id === "string" && id ? id : null;
}

/**
 * After an inventory write that started an order-reco refresh: repaint now
 * (the edited inputs), then let the page-level OrderRecoStatusProvider follow
 * the refresh — it outlives the drawer that triggered it (Issue #350).
 */
export function useOrderRecoRefreshFollowup(opts?: { pendingBanner?: string }) {
  const router = useRouter();
  const ctx = useOrderRecoStatus();
  const follow = ctx?.follow;

  const followOrderReco = useCallback(
    (input: OrderRecoFollowupInput, o?: { skipImmediateRefresh?: boolean }) => {
      if (!o?.skipImmediateRefresh) router.refresh();
      if (input.queued?.length) follow?.(runIdOf(input.data));
    },
    [follow, router],
  );

  const pending = Boolean(ctx && (ctx.awaiting || ctx.status.state === "running"));
  const banner = pending
    ? `${opts?.pendingBanner ?? "Order recommendation updating — numbers repaint when it finishes."} Safe to keep editing — the latest edit wins.`
    : null;

  return { banner, followOrderReco, stopPolling: () => {} };
}
