"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isJobLive, type CategorizeJob } from "@/lib/plaid/categorize-job-status";

const POLL_MS = 1500;

/**
 * Latest Accounting categorize job, polled from GET /api/accounting/categorize-job while
 * one is live (any tab) or while this tab has asked to watch (it just clicked Reapply).
 */
export function useCategorizeJob(initial: CategorizeJob | null) {
  const [job, setJob] = useState<CategorizeJob | null>(initial);
  const [watching, setWatching] = useState(false);
  const stopAt = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/accounting/categorize-job", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as { ok: boolean; job: CategorizeJob | null };
      if (body.ok) setJob(body.job);
    } catch {
      // A missed poll is retried on the next tick.
    }
  }, []);

  const live = isJobLive(job);

  useEffect(() => {
    if (!live && !watching) return;
    const id = setInterval(() => {
      void refresh();
      if (watching && !live && Date.now() > stopAt.current) setWatching(false);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, watching, refresh]);

  /** Poll for a while even before the new job row appears. */
  const watch = useCallback(() => {
    stopAt.current = Date.now() + 10_000;
    setWatching(true);
    void refresh();
  }, [refresh]);

  return { job, live, watch, refresh, setJob };
}
