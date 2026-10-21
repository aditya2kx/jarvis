"use client";

import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { setPageGrant, sectionsGranted, type Grant } from "@/lib/access/policy";
import type { AccessPage } from "@/lib/access/registry";
import { PageIcon } from "./visuals";

/** One page in the person drawer: tick the page for all of it, or open it and pick sections. */
export function PageGrantRow({
  page,
  grant,
  onChange,
  disabled,
}: {
  page: AccessPage;
  grant: Grant;
  onChange: (next: Grant) => void;
  disabled?: boolean;
}) {
  const granted = sectionsGranted(grant, page.id);
  const total = page.sections.length;
  const all = granted === total;
  const some = granted > 0 && !all;
  const [open, setOpen] = useState(some);
  const g = grant.pages[page.id];
  const has = (id: string) => grant.full || g === "all" || (Array.isArray(g) && g.includes(id));

  function toggleSection(id: string, on: boolean) {
    const current = page.sections.map((s) => s.id).filter(has);
    const next = on ? [...current, id] : current.filter((s) => s !== id);
    onChange(setPageGrant(grant, page.id, next));
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border transition-colors",
        granted > 0 ? "border-primary/25 bg-primary/[0.03]" : "bg-card",
      )}
      data-testid={`page-grant-${page.id}`}
    >
      <div className="flex min-h-12 items-center gap-3 pr-1.5 pl-3">
        <Checkbox
          id={`pg-${page.id}`}
          checked={all}
          indeterminate={some}
          disabled={disabled}
          onCheckedChange={(on) => onChange(setPageGrant(grant, page.id, on ? "all" : null))}
          aria-label={`${page.label}: whole page`}
        />
        <label htmlFor={`pg-${page.id}`} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-sm font-medium">
          <PageIcon page={page.id} className={cn("size-4 shrink-0", granted ? "text-primary" : "text-muted-foreground")} />
          <span className="truncate">{page.label}</span>
        </label>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs tabular-nums",
            all ? "bg-primary/10 text-primary" : some ? "bg-muted text-foreground" : "text-muted-foreground",
          )}
        >
          {all ? "Whole page" : some ? `${granted} of ${total}` : "Off"}
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Choose"} ${page.label} sections`}
          className="flex size-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDownIcon className={cn("size-4 transition-transform duration-200", open && "rotate-180")} />
        </button>
      </div>
      {open ? (
        <div className="border-t bg-muted/30 px-3 pt-2 pb-2.5">
          <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
            <span>Sections</span>
            <span className="flex gap-1">
              <button
                type="button"
                disabled={disabled || all}
                onClick={() => onChange(setPageGrant(grant, page.id, "all"))}
                className="rounded px-1.5 py-0.5 font-medium transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              >
                All
              </button>
              <button
                type="button"
                disabled={disabled || granted === 0}
                onClick={() => onChange(setPageGrant(grant, page.id, null))}
                className="rounded px-1.5 py-0.5 font-medium transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              >
                None
              </button>
            </span>
          </div>
          <div className="grid gap-x-4 sm:grid-cols-2">
            {page.sections.map((s) => (
              <label
                key={s.id}
                className={cn(
                  "flex min-h-9 cursor-pointer items-center gap-2.5 rounded-md px-1 text-sm transition-colors hover:text-foreground",
                  has(s.id) ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <Checkbox checked={has(s.id)} disabled={disabled} onCheckedChange={(on) => toggleSection(s.id, on)} />
                {s.label}
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
