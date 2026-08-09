"use client";

import { useCallback, useRef, useState } from "react";
import type { ActionAck } from "@/lib/actions/types";
import { useActionToast } from "@/lib/actions/ActionToast";

export type ConsoleActionLabels = {
  saving?: string;
  queued?: string;
  done?: string;
};

/**
 * Shared mutating-action shell — pending, stage copy, toast that survives
 * Sheet close, and no double-submit while in flight.
 *
 * Busy uses useState + ref (not async startTransition): React ends a transition
 * at the first await, which re-enabled Post once mid-flight (Issue #233).
 */
export function useConsoleAction() {
  const [isPending, setIsPending] = useState(false);
  const busyRef = useRef(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useActionToast();

  const run = useCallback(
    async <T,>(
      fn: () => Promise<ActionAck<T>>,
      labels?: ConsoleActionLabels,
    ): Promise<ActionAck<T>> => {
      if (busyRef.current) {
        return { ok: false, error: "Another action is still in progress." };
      }
      busyRef.current = true;
      const saving = labels?.saving ?? "Saving…";
      setError(null);
      setStage(saving);
      setIsPending(true);
      try {
        const ack = await fn();
        if (!ack.ok) {
          setError(ack.error);
          setStage(null);
          toast.push(ack.error, "error");
          return ack;
        }
        const queuedMsg =
          ack.queued?.length && labels?.queued
            ? labels.queued
            : ack.queued?.length
              ? `Queued: ${ack.queued.join(", ")}`
              : null;
        const doneMsg = ack.message ?? labels?.done ?? (queuedMsg ? null : "Done.");
        const display = queuedMsg ?? doneMsg ?? "Done.";
        setStage(display);
        toast.push(display, "info");
        return ack;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        setStage(null);
        toast.push(msg, "error");
        return { ok: false, error: msg };
      } finally {
        busyRef.current = false;
        setIsPending(false);
      }
    },
    [toast],
  );

  return { isPending, stage, error, run, setStage, setError };
}
