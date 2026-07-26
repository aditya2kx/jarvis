"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  FROM_COOKIE,
  PERIOD_COOKIE,
  TO_COOKIE,
  writeFilterCookie,
} from "@/lib/filters/range";

/**
 * Custom "from"/"to" date inputs for the "Custom…" range preset. Rendered by
 * a page as soon as `wantsCustom(sp.range)` is true (see `lib/filters/range`)
 * — picking "Custom…" first navigates with `range=custom` and no from/to;
 * `resolveRange` falls back to a default window for that render (so the page
 * still has something to query), but `wantsCustom` stays true off the raw
 * search param, keeping this picker visible so the operator can fill in the
 * real bounds.
 *
 * `committed` is true once the URL has a valid custom from/to
 * (`win.preset === "custom"`). After Apply, the editor collapses to a compact
 * range chip (Apply goes away) until Edit is clicked.
 *
 * Uses native `<input type="date">` (not Base UI Input) so the OS calendar
 * picker works; click/focus calls `showPicker()` where supported.
 */
export function DateRangePicker({
  basePath,
  from,
  to,
  committed = false,
  extraParams = {},
}: {
  basePath: string;
  from: string;
  to: string;
  /** True when URL already has a valid custom from/to window. */
  committed?: boolean;
  extraParams?: Record<string, string>;
}) {
  const router = useRouter();
  const fromRef = useRef<HTMLInputElement>(null);
  const toRef = useRef<HTMLInputElement>(null);
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);
  const [editing, setEditing] = useState(!committed);

  useEffect(() => {
    setDraftFrom(from);
    setDraftTo(to);
  }, [from, to]);

  useEffect(() => {
    if (!committed) setEditing(true);
    else setEditing(false);
  }, [committed]);

  const dirty = draftFrom !== from || draftTo !== to;
  const canApply = Boolean(draftFrom && draftTo && draftFrom <= draftTo);
  const showEditor = editing || !committed || dirty;

  function openPicker(el: HTMLInputElement | null) {
    if (!el) return;
    el.focus();
    try {
      el.showPicker?.();
    } catch {
      // Ignore — user can still use the native calendar glyph / keyboard.
    }
  }

  function apply() {
    if (!canApply) return;
    writeFilterCookie(PERIOD_COOKIE, "custom");
    writeFilterCookie(FROM_COOKIE, draftFrom);
    writeFilterCookie(TO_COOKIE, draftTo);
    const params = new URLSearchParams({
      ...extraParams,
      range: "custom",
      from: draftFrom,
      to: draftTo,
    });
    router.push(`${basePath}?${params.toString()}`);
    setEditing(false);
  }

  if (!showEditor && committed) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Custom</span>
        <span className="rounded-md border border-border bg-muted/40 px-2 py-1 text-xs tabular-nums">
          {from} → {to}
        </span>
        <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(true)}>
          Edit
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Custom</span>
      <DateField
        inputRef={fromRef}
        value={draftFrom}
        max={draftTo || undefined}
        ariaLabel="Custom range start date"
        onChange={setDraftFrom}
        onOpenPicker={() => openPicker(fromRef.current)}
      />
      <span className="text-xs text-muted-foreground">to</span>
      <DateField
        inputRef={toRef}
        value={draftTo}
        min={draftFrom || undefined}
        ariaLabel="Custom range end date"
        onChange={setDraftTo}
        onOpenPicker={() => openPicker(toRef.current)}
      />
      {(dirty || !committed) && (
        <Button type="button" size="sm" variant="secondary" onClick={apply} disabled={!canApply}>
          Apply
        </Button>
      )}
      {committed && editing && !dirty ? (
        <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
          Done
        </Button>
      ) : null}
    </div>
  );
}

function DateField({
  inputRef,
  value,
  min,
  max,
  ariaLabel,
  onChange,
  onOpenPicker,
}: {
  inputRef: React.RefObject<HTMLInputElement | null>;
  value: string;
  min?: string;
  max?: string;
  ariaLabel: string;
  onChange: (next: string) => void;
  onOpenPicker: () => void;
}) {
  return (
    <input
      ref={inputRef}
      type="date"
      value={value}
      min={min}
      max={max}
      onChange={(e) => onChange(e.target.value)}
      onClick={onOpenPicker}
      className={cn(
        "h-8 w-[11rem] cursor-pointer rounded-lg border border-input bg-transparent px-2.5 py-1",
        "text-sm tabular-nums outline-none transition-colors",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "scheme-light dark:scheme-dark",
        "[&::-webkit-calendar-picker-indicator]:cursor-pointer",
        "[&::-webkit-calendar-picker-indicator]:opacity-100",
        "[&::-webkit-calendar-picker-indicator]:ml-1",
      )}
      aria-label={ariaLabel}
    />
  );
}
