"use client";

import { useCallback, useState, useTransition } from "react";
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
 */
export function useConsoleAction() {
  const [isPending, startTransition] = useTransition();
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useActionToast();

  const run = useCallback(
    <T,>(fn: () => Promise<ActionAck<T>>, labels?: ConsoleActionLabels): Promise<ActionAck<T>> => {
      const saving = labels?.saving ?? "Saving…";
      setError(null);
      setStage(saving);

      return new Promise((resolve) => {
        startTransition(async () => {
          try {
            const ack = await fn();
            if (!ack.ok) {
              setError(ack.error);
              setStage(null);
              toast.push(ack.error, "error");
              resolve(ack);
              return;
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
            resolve(ack);
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            setError(msg);
            setStage(null);
            toast.push(msg, "error");
            resolve({ ok: false, error: msg });
          }
        });
      });
    },
    [toast],
  );

  return { isPending, stage, error, run, setStage, setError };
}
