"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Popover } from "@base-ui/react/popover";
import { UploadIcon } from "lucide-react";
import {
  pollPunchGapWriteAction,
  writePunchGapsToAdpAction,
} from "@/app/labor/actions";
import { Button } from "@/components/ui/button";
import { useActionToast } from "@/lib/actions/ActionToast";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { decisionTimes, type PunchGap } from "@/lib/labor/punch-gaps";
import { cn } from "@/lib/utils";

const POLL_MS = 4000;
const TIMEOUT_MS = 25 * 60 * 1000;

type Phase = "idle" | "running" | "done" | "error";

function dayShort(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, d!)).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/**
 * Separate, explicit commit of accepted/edited punches into ADP Timecards
 * (Issue #356). Confirm popover lists exactly what will be written.
 */
export function WriteToAdpButton({ gaps }: { gaps: PunchGap[] }) {
  const router = useRouter();
  const toast = useActionToast();
  const { run, setError } = useConsoleAction();
  const [open, setOpen] = useState(false);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const chosen = gaps.filter((g) => !unchecked.has(g.key));
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState<string | null>(null);
  const idsRef = useRef<string[]>([]);
  const executionRef = useRef<string | null>(null);
  const startedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);
  useEffect(() => () => stop(), [stop]);

  const finish = useCallback(
    (next: Phase, text: string) => {
      stop();
      setPhase(next);
      setStatus(text);
      toast.push(text, next === "error" ? "error" : "info");
      router.refresh();
    },
    [router, stop, toast],
  );

  const poll = useCallback(async () => {
    if (Date.now() - startedRef.current > TIMEOUT_MS) {
      finish(
        "error",
        "Still running on the server after 25 minutes — rows update when it finishes; any that fail show “ADP write failed — retry”.",
      );
      return;
    }
    const ack = await pollPunchGapWriteAction({
      decisionIds: idsRef.current,
      executionName: executionRef.current,
    });
    if (!ack.ok) {
      finish("error", ack.error);
      return;
    }
    const { statuses = [], done, execution } = ack.data ?? {};
    const count = (s: string) => statuses.filter((x) => x.status === s).length;
    if (done) {
      const failed = count("failed");
      const parts = [
        count("applied") && `${count("applied")} written`,
        count("already_resolved") &&
          `${count("already_resolved")} already fixed`,
        failed && `${failed} failed`,
      ].filter(Boolean);
      finish(
        failed ? "error" : "done",
        `ADP: ${parts.join(" · ")}. Clocked hours resyncing.`,
      );
      return;
    }
    if (execution?.failed) {
      finish(
        "error",
        `ADP write job failed: ${execution.message ?? "Cloud Run execution failed"}`,
      );
      return;
    }
    const left = count("applying");
    setStatus(
      `Writing to ADP… ${statuses.length - left} of ${statuses.length} done`,
    );
  }, [finish]);

  const start = useCallback(async () => {
    setOpen(false);
    const ack = await run(
      () =>
        writePunchGapsToAdpAction(
          chosen.map((g) => g.decision!.id!).filter(Boolean),
        ),
      {
        saving: "Starting ADP write…",
        queued: "ADP write started",
        done: "ADP write started",
      },
    );
    if (!ack.ok) {
      setPhase("error");
      setStatus(ack.error);
      return;
    }
    setError(null);
    idsRef.current = ack.data?.decisionIds ?? [];
    executionRef.current = ack.data?.executionName ?? null;
    startedRef.current = Date.now();
    setPhase("running");
    setStatus(ack.data?.message ?? "Writing to ADP…");
    router.refresh();
    timerRef.current = setInterval(() => void poll(), POLL_MS);
  }, [chosen, poll, router, run, setError]);

  const running = phase === "running";
  if (!gaps.length && !running && phase === "idle") return null;
  const n = gaps.length;

  return (
    <div className="flex flex-col items-end gap-1">
      <Popover.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) setUnchecked(new Set());
        }}
      >
        <Popover.Trigger
          render={
            <Button
              type="button"
              size="sm"
              disabled={running || n === 0}
              className="gap-1.5"
            >
              <UploadIcon className="size-3.5" />
              {running ? "Writing to ADP…" : `Write ${n} to ADP`}
            </Button>
          }
        />
        <Popover.Portal>
          <Popover.Positioner
            side="bottom"
            align="end"
            sideOffset={6}
            className="z-50"
          >
            <Popover.Popup
              className={cn(
                "flex w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg outline-none",
                "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
              )}
            >
              <div className="border-b border-border px-3 py-2.5">
                <p className="text-sm font-medium">
                  Write {chosen.length} of {n} punch fix{n === 1 ? "" : "es"} to
                  ADP?
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Fills the blank Out Time on open entries, or adds the entry on
                  scheduled days with no punch, in ADP Timecards with a comment
                  naming you. Days already fixed in ADP are skipped.
                </p>
              </div>
              <ul className="max-h-56 overflow-y-auto px-3 py-2 text-xs">
                {gaps.map((g) => (
                  <li key={g.key}>
                    <label className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 hover:bg-muted/60">
                      <input
                        type="checkbox"
                        className="size-3.5 accent-primary"
                        checked={!unchecked.has(g.key)}
                        onChange={(e) =>
                          setUnchecked((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.delete(g.key);
                            else next.add(g.key);
                            return next;
                          })
                        }
                      />
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-medium">{g.employee}</span>
                        <span className="text-muted-foreground">
                          {" "}
                          · {dayShort(g.date)}
                        </span>
                      </span>
                      <span className="shrink-0 tabular-nums">
                        {decisionTimes(g.decision!)}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <div className="flex justify-end gap-2 border-t border-border px-3 py-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={chosen.length === 0}
                  onClick={() => void start()}
                >
                  Write {chosen.length} to ADP
                </Button>
              </div>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
      {phase !== "idle" && status ? (
        <p
          role="status"
          aria-live="polite"
          className={cn(
            "max-w-xs text-right text-[11px] leading-snug",
            phase === "error" && "text-destructive",
            phase === "done" && "text-emerald-600 dark:text-emerald-400",
            phase === "running" && "text-amber-700 dark:text-amber-400",
          )}
        >
          {status}
        </p>
      ) : null}
    </div>
  );
}
