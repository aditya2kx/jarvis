"use client";

import { useEffect, useRef } from "react";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";

/**
 * Page-open pending state: poll until paint-ready. Do not router.refresh() on
 * mount (that retriggers RSC ensureOrderRecoFresh / a second job).
 */
export function InventoryRecoFreshness({
  pending,
  baselineRefreshedAt,
}: {
  pending: boolean;
  baselineRefreshedAt: string | null;
}) {
  const { banner, followOrderReco } = useOrderRecoRefreshFollowup({
    pendingBanner: "Order recommendation refreshing — numbers update when ready.",
  });
  const started = useRef(false);

  useEffect(() => {
    if (!pending || started.current) return;
    started.current = true;
    followOrderReco(
      { queued: ["order-reco"], baselineRefreshedAt },
      { skipImmediateRefresh: true },
    );
  }, [pending, baselineRefreshedAt, followOrderReco]);

  const text =
    banner ?? "Order recommendation refreshing — numbers update when ready.";

  return (
    <p
      role="status"
      className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
    >
      {text}
    </p>
  );
}
