"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Popover } from "@base-ui/react/popover";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  normalizeSourceSelection,
  serializeSources,
} from "@/lib/filters/sources";

/**
 * Page-level multi-select filter driven by URL search params (same contract
 * as FilterSelect / FilterPills). Empty selection or all options selected
 * collapses to "all" (param omitted) so unfiltered totals stay the default.
 */
export function FilterMultiSelect({
  label,
  param,
  selected,
  options,
  basePath,
  extraParams = {},
}: {
  label: string;
  param: string;
  /** Currently selected values; empty array means "all". */
  selected: string[];
  options: string[];
  basePath: string;
  extraParams?: Record<string, string>;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selectedSet = new Set(selected);
  const active = selected.length > 0;
  const displayLabel = (() => {
    if (!active) return "All";
    if (selected.length === 1) return selected[0];
    return `${selected.length} selected`;
  })();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((opt) => opt.toLowerCase().includes(q));
  }, [options, query]);
  const showSearch = options.length > 6;

  function push(nextSelected: string[]) {
    const normalized = normalizeSourceSelection(nextSelected, options);
    const params = new URLSearchParams(extraParams);
    const serialized = serializeSources(normalized);
    if (serialized) params.set(param, serialized);
    else params.delete(param);
    const qs = params.toString();
    router.push(qs ? `${basePath}?${qs}` : basePath);
  }

  function toggle(opt: string) {
    const next = new Set(selectedSet);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    push([...next]);
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <Popover.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setQuery("");
        }}
      >
        <Popover.Trigger
          nativeButton
          type="button"
          aria-label={`Filter ${label}`}
          className={cn(
            "flex h-8 min-w-32 max-w-56 items-center justify-between gap-1 rounded-md border px-2 text-left text-xs font-normal outline-none transition-colors",
            "focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40",
            active
              ? "border-primary/50 bg-primary/10 text-foreground"
              : "border-border bg-background text-muted-foreground hover:bg-muted/60 hover:text-foreground",
          )}
        >
          <span className="truncate">{displayLabel}</span>
          <ChevronDownIcon className="size-3.5 shrink-0 opacity-60" />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="bottom" align="start" sideOffset={4} className="z-50">
            <Popover.Popup
              className={cn(
                "flex w-72 max-w-[min(18rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg outline-none",
                "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
              )}
            >
              <div className="flex items-center justify-between gap-2 border-b border-border px-2.5 py-2">
                <span className="text-xs font-medium text-muted-foreground">{label}</span>
                <div className="flex items-center gap-2 text-[11px]">
                  <button
                    type="button"
                    className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    onClick={() => push([...options])}
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-40"
                    disabled={!active}
                    onClick={() => push([])}
                  >
                    Clear
                  </button>
                </div>
              </div>
              {showSearch ? (
                <div className="border-b border-border p-2">
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={`Search ${label.toLowerCase()}…`}
                    className="h-8 px-2 text-xs"
                    aria-label={`Search ${label}`}
                    autoFocus
                  />
                </div>
              ) : null}
              <ul className="max-h-64 overflow-y-auto p-1" role="listbox" aria-multiselectable>
                {filtered.length === 0 ? (
                  <li className="px-2 py-3 text-center text-xs text-muted-foreground">No matches</li>
                ) : (
                  filtered.map((opt) => {
                    const checked = !active || selectedSet.has(opt);
                    return (
                      <li key={opt}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={checked}
                          className={cn(
                            "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted",
                            checked && "bg-muted/70",
                          )}
                          onClick={() => {
                            if (!active) {
                              // Currently "all" — first click narrows to everything except this
                              // would be surprising. Instead: start from all, toggle off this one.
                              push(options.filter((o) => o !== opt));
                              return;
                            }
                            toggle(opt);
                          }}
                        >
                          <span
                            className={cn(
                              "mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
                              checked
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-border",
                            )}
                            aria-hidden
                          >
                            {checked ? <CheckIcon className="size-2.5" strokeWidth={3} /> : null}
                          </span>
                          <span className="min-w-0 flex-1 whitespace-normal break-words leading-snug">
                            {opt}
                          </span>
                        </button>
                      </li>
                    );
                  })
                )}
              </ul>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
