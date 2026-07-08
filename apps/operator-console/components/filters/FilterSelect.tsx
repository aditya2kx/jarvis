"use client";

import { useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FilterOption } from "./FilterPills";

/**
 * Labeled dropdown filter — same server-driven-search-param contract as
 * `FilterPills` (label/param/value/options/basePath/extraParams), but reads
 * as a compact `Select` instead of a pill row. Convention (audited across
 * every filter on the console): >=5 options or a dynamic set (e.g. Source,
 * Period) -> `FilterSelect`; <=4 fixed options (e.g. On-time, View) stay
 * `FilterPills`. A 9-option pill row doesn't fit at 390px; a dropdown does.
 */
export function FilterSelect({
  label,
  param,
  value,
  options,
  basePath,
  extraParams = {},
}: {
  label: string;
  param: string;
  value: string;
  options: FilterOption[];
  basePath: string;
  extraParams?: Record<string, string>;
}) {
  const router = useRouter();

  function onChange(next: string | null) {
    if (next == null) return;
    const params = new URLSearchParams({ ...extraParams, [param]: next });
    router.push(`${basePath}?${params.toString()}`);
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger size="sm" className="min-w-32">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
