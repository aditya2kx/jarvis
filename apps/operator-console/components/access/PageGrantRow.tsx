"use client";

import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { setPageGrant, sectionsGranted, type Grant } from "@/lib/access/policy";
import type { AccessPage } from "@/lib/access/registry";

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
    <div className="rounded-lg border" data-testid={`page-grant-${page.id}`}>
      <div className="flex min-h-11 items-center gap-3 px-3">
        <Checkbox
          id={`pg-${page.id}`}
          checked={all}
          indeterminate={some}
          disabled={disabled}
          onCheckedChange={(on) => onChange(setPageGrant(grant, page.id, on ? "all" : null))}
          aria-label={`${page.label}: whole page`}
        />
        <label htmlFor={`pg-${page.id}`} className="min-w-0 flex-1 cursor-pointer text-sm font-medium">
          {page.label}
        </label>
        <span className="text-xs tabular-nums text-muted-foreground">
          {all ? "Whole page" : some ? `${granted} of ${total}` : "None"}
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Choose"} ${page.label} sections`}
          className="-mr-1 flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDownIcon className={cn("size-4 transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open ? (
        <div className="grid gap-x-4 gap-y-1 border-t bg-muted/30 px-3 py-2 sm:grid-cols-2">
          {page.sections.map((s) => (
            <label
              key={s.id}
              className="flex min-h-9 cursor-pointer items-center gap-2.5 rounded-md px-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <Checkbox
                checked={has(s.id)}
                disabled={disabled}
                onCheckedChange={(on) => toggleSection(s.id, on)}
              />
              {s.label}
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}
