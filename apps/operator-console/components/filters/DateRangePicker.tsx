"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/**
 * Custom "from"/"to" date inputs for the "Custom…" range preset. Only
 * rendered by a page when `win.preset === "custom"` (see FilterSelect's
 * Period control) — picking "Custom…" first navigates with `range=custom`
 * and no from/to, `resolveRange` falls back to `30d` for that render, and
 * this picker then lets the operator fill in the real bounds. Submits once
 * (not per-keystroke) via a small local form, same server-driven search-param
 * contract as FilterPills/FilterSelect.
 */
export function DateRangePicker({
  basePath,
  from,
  to,
  extraParams = {},
}: {
  basePath: string;
  from: string;
  to: string;
  extraParams?: Record<string, string>;
}) {
  const router = useRouter();
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);

  function apply() {
    if (!draftFrom || !draftTo) return;
    const params = new URLSearchParams({
      ...extraParams,
      range: "custom",
      from: draftFrom,
      to: draftTo,
    });
    router.push(`${basePath}?${params.toString()}`);
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Custom</span>
      <Input
        type="date"
        value={draftFrom}
        max={draftTo || undefined}
        onChange={(e) => setDraftFrom(e.target.value)}
        className="h-8 w-[9.5rem]"
        aria-label="Custom range start date"
      />
      <span className="text-xs text-muted-foreground">to</span>
      <Input
        type="date"
        value={draftTo}
        min={draftFrom || undefined}
        onChange={(e) => setDraftTo(e.target.value)}
        className="h-8 w-[9.5rem]"
        aria-label="Custom range end date"
      />
      <Button type="button" size="sm" variant="secondary" onClick={apply} disabled={!draftFrom || !draftTo}>
        Apply
      </Button>
    </div>
  );
}
