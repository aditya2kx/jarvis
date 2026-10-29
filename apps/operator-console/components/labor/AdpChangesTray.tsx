"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Undo2Icon } from "lucide-react";
import { adpChangesStatusAction, applyAdpChangesAction } from "@/app/labor/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import {
  adpChangeKey,
  changePeople,
  summarizeApply,
  trayLines,
  type AdpChange,
  type ApplyRow,
  type TrayLine,
} from "@/lib/labor/adp-changes";
import type { ApplyBlock } from "@/lib/labor/availability-changes";

const POLL_MS = 5000;
const GRACE_MS = 90 * 1000;

type Inflight = { batchId: string; at: number; sent: { id: string; key: string }[] };
const inflightKey = (store: string) => `labor-adp-apply:v1:${store}`;

function readInflight(store: string): Inflight | null {
  try {
    const v = JSON.parse(window.localStorage.getItem(inflightKey(store)) ?? "null") as Inflight | null;
    return v && typeof v.batchId === "string" && Array.isArray(v.sent) ? v : null;
  } catch {
    return null;
  }
}

function writeInflight(store: string, v: Inflight | null): void {
  try {
    if (v) window.localStorage.setItem(inflightKey(store), JSON.stringify(v));
    else window.localStorage.removeItem(inflightKey(store));
  } catch {
    // Storage blocked: a reload mid-run then just refreshes from ADP when it ends.
  }
}

const AREA_TITLE: Record<AdpChange["area"], string> = { shift: "Shifts", unavailability: "Unavailability" };

function blockText(area: AdpChange["area"], block: ApplyBlock): string | null {
  if (block === "preview") return "Read-only preview: in prod Apply runs one ADP session for every change here.";
  if (block === "off") {
    return area === "shift"
      ? "ADP shift changes are switched off (CONSOLE_ADP_SCHEDULE_WRITE)."
      : "ADP unavailability changes are switched off (CONSOLE_ADP_UNAVAIL_WRITE).";
  }
  return null;
}

function Lines({ lines, onUndo }: { lines: TrayLine[]; onUndo?: (keys: string[]) => void }) {
  const areas = (["shift", "unavailability"] as const).filter((a) => lines.some((l) => l.area === a));
  return (
    <div className="flex flex-col gap-2">
      {areas.map((area) => (
        <section key={area} className="flex flex-col">
          {areas.length > 1 ? <h5 className="pt-1 text-xs font-medium text-muted-foreground">{AREA_TITLE[area]}</h5> : null}
          <ul className="flex flex-col divide-y divide-border">
            {lines
              .filter((l) => l.area === area)
              .map((l) => (
                <li key={l.key} className="flex items-center gap-2 py-1.5 text-sm">
                  <Badge variant={l.destructive ? "destructive" : "outline"} className="w-[4.5rem] shrink-0 justify-center">
                    {l.label}
                  </Badge>
                  <span className="min-w-0 flex-1 break-words text-muted-foreground">
                    {l.text}
                  </span>
                  {onUndo ? (
                    <Button size="icon-sm" variant="ghost" aria-label={`Undo ${l.label.toLowerCase()} ${l.text}`} onClick={() => onUndo(l.keys)}>
                      <Undo2Icon />
                    </Button>
                  ) : null}
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/**
 * Sticky "Changes to ADP" tray (Issues #427, #434): every staged shift and unavailability change,
 * reviewed in one drawer and applied in one ADP run. Nothing shows as done until ADP is read back.
 */
export function AdpChangesTray({
  store,
  changes,
  blocks,
  notice,
  onUndo,
  onDiscard,
  onApplied,
  onDismissNotice,
}: {
  store: string;
  changes: AdpChange[];
  blocks: Record<AdpChange["area"], ApplyBlock>;
  /** A line about changes dropped because ADP moved on (e.g. an open shift someone claimed). */
  notice?: string | null;
  onUndo: (keys: string[]) => void;
  onDiscard: () => void;
  /** Queue keys ADP confirmed (applied, or already so) — leave the tray. */
  onApplied: (keys: string[]) => void;
  onDismissNotice?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [inflight, setInflight] = useState<Inflight | null>(null);
  const [rows, setRows] = useState<ApplyRow[]>([]);
  const [graceUntil, setGraceUntil] = useState(0);
  const [result, setResult] = useState<ApplyRow[] | null>(null);
  const { isPending, run } = useConsoleAction();
  const router = useRouter();

  // A reloaded page picks up an Apply still running (or one that ended while away).
  useEffect(() => {
    let live = true;
    void adpChangesStatusAction(null)
      .catch(() => null)
      .then((ack) => {
        if (!live) return;
        const stored = readInflight(store);
        const running = ack?.ok ? ack.data?.running : null;
        if (stored) {
          setInflight(stored);
          setGraceUntil((stored.at || 0) + GRACE_MS);
        } else if (running) {
          setInflight({ batchId: running, at: Date.now(), sent: [] });
          setGraceUntil(Date.now() + GRACE_MS);
        }
      });
    return () => {
      live = false;
    };
  }, [store]);

  const settle = useCallback(
    (final: ApplyRow[], sent: Inflight["sent"]) => {
      const done = new Set(final.filter((r) => r.status === "applied" || r.status === "skipped").map((r) => r.change_id));
      const keys = sent.filter((s) => done.has(s.id)).map((s) => s.key);
      if (keys.length) onApplied(keys);
      setResult(final);
      setInflight(null);
      setGraceUntil(0);
      writeInflight(store, null);
      router.refresh();
    },
    [onApplied, router, store],
  );

  // The run can take a while to show as running; until the grace ends, queued rows mean "not yet".
  const poll = useCallback(async () => {
    if (!inflight) return;
    const ack = await adpChangesStatusAction(inflight.batchId).catch(() => null);
    if (!ack?.ok || !ack.data) return;
    const live = ack.data.running === inflight.batchId;
    setRows(ack.data.rows);
    const queued = ack.data.rows.some((r) => r.status === "queued");
    if (!live && (Date.now() > graceUntil || (ack.data.rows.length > 0 && !queued))) settle(ack.data.rows, inflight.sent);
  }, [inflight, graceUntil, settle]);

  const polling = inflight != null;
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(id);
  }, [polling, poll]);

  const lines = useMemo(() => trayLines(changes), [changes]);
  const people = useMemo(() => changePeople(changes), [changes]);
  const progress = useMemo(() => summarizeApply(rows), [rows]);
  const outcome = result ? summarizeApply(result) : null;
  const busy = polling || isPending;
  const blocked = (["shift", "unavailability"] as const)
    .filter((area) => changes.some((c) => c.area === area))
    .map((area) => blockText(area, blocks[area]))
    .find((t) => t != null) ?? null;

  const apply = async () => {
    const sending = changes;
    const ack = await run(() => applyAdpChangesAction(sending), {
      saving: "Queuing…",
      queued: "Applying your changes in ADP in the background.",
    });
    if (ack.ok && ack.data) {
      const next = { batchId: ack.data.batchId, at: Date.now(), sent: sending.map((c, i) => ({ id: ack.data!.ids[i]!, key: adpChangeKey(c) })) };
      writeInflight(store, next);
      setInflight(next);
      setRows([]);
      setResult(null);
      setGraceUntil(Date.now() + GRACE_MS);
      setSheet(false);
    }
  };

  if (!changes.length && !busy && !outcome && !notice) return null;
  const done = progress.applied + progress.skipped + progress.failed;
  const total = rows.length || inflight?.sent.length || 0;

  return (
    <div
      data-testid="adp-changes-tray"
      className="sticky bottom-3 z-20 flex flex-col gap-2 rounded-xl border border-border bg-popover/95 p-3 shadow-lg backdrop-blur max-md:bottom-[calc(4.5rem+env(safe-area-inset-bottom))]"
    >
      <div className="flex flex-wrap items-center gap-2">
        {busy ? (
          <p className="text-sm text-foreground" aria-live="polite">
            Applying in ADP — {done} of {total || "…"} done. Safe to leave or reload this page.
          </p>
        ) : changes.length ? (
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-baseline gap-2 text-left">
            <span className="text-sm font-medium hover:underline">
              {changes.length} change{changes.length === 1 ? "" : "s"} to ADP
              {people ? ` · ${people} ${people === 1 ? "person" : "people"}` : ""}
            </span>
            <span className="text-xs text-muted-foreground">{open ? "Hide" : "Review"}</span>
          </button>
        ) : null}
        {!busy && changes.length ? (
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onDiscard}>
              Discard
            </Button>
            <Button size="sm" disabled={blocked != null} title={blocked ?? undefined} onClick={() => setSheet(true)}>
              Apply to ADP
            </Button>
          </div>
        ) : null}
      </div>
      {!busy && changes.length ? (
        <p className="text-xs text-muted-foreground">
          {blocked ?? "Apply runs one ADP session for all of them, then re-reads ADP to confirm each."}
        </p>
      ) : null}
      {notice ? (
        <p className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
          {notice}
          {onDismissNotice ? (
            <Button variant="link" size="xs" className="h-6 px-1 text-xs" onClick={onDismissNotice}>
              Dismiss
            </Button>
          ) : null}
        </p>
      ) : null}
      {outcome && !busy ? (
        <div className="flex flex-col gap-1 text-xs">
          <p className="flex flex-wrap items-center gap-1.5">
            {outcome.applied + outcome.skipped ? <Badge variant="default">{outcome.applied + outcome.skipped} done in ADP</Badge> : null}
            {outcome.failed ? <Badge variant="destructive">{outcome.failed} not done — still in your changes</Badge> : null}
            {outcome.queued ? <Badge variant="secondary">{outcome.queued} not attempted — still in your changes</Badge> : null}
            <Button variant="link" size="xs" className="h-6 px-1 text-xs" onClick={() => setResult(null)}>
              Dismiss
            </Button>
          </p>
          {outcome.errors.length ? (
            <ul className="text-rose-600 dark:text-rose-400">
              {outcome.errors.slice(0, 4).map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {open && !busy && changes.length ? (
        <div className="border-t border-border pt-1">
          <Lines lines={lines} onUndo={onUndo} />
        </div>
      ) : null}

      <Sheet open={sheet} onOpenChange={(o) => !o && setSheet(false)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>
              Apply {changes.length} change{changes.length === 1 ? "" : "s"} to ADP
            </SheetTitle>
            <SheetDescription>
              One ADP session makes every change below, then reads ADP back to confirm each. New and edited shifts are
              published, so the person sees them in ADP Mobile. ADP can&apos;t hand an assigned shift to someone else, so
              Reassign deletes it and adds the same hours for the new person. About 2 min to sign in, then ~20 s per
              change — you can keep using the page.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4">
            <Lines lines={lines} />
          </div>
          <SheetFooter>
            <Button onClick={() => void apply()} disabled={isPending || blocked != null}>
              {isPending ? "Queuing…" : `Apply ${changes.length} to ADP`}
            </Button>
            <SheetClose render={<Button variant="outline">Cancel</Button>} />
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
