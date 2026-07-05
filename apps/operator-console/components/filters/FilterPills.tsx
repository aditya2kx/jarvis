import Link from "next/link";
import { cn } from "@/lib/utils";

const PILL = "rounded-md px-2.5 py-1 text-xs font-medium transition-colors";

export interface FilterOption {
  value: string;
  label: string;
}

/**
 * Generic labeled pill-group filter (Figma: "Range 7d/30d/90d", "Metric
 * Orders/Items", "On-time 5m/7m/10m", "Source All/Register/Kiosk/Online",
 * "Period Current/Last", "View Reconciliation/Detail"). Server-driven via
 * `Link` + search params, re-rendered server-side — no client fetch. Wraps
 * onto its own line on narrow screens so multiple filter groups can sit in
 * a page header without overflowing.
 */
export function FilterPills({
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
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1 rounded-md bg-secondary p-0.5">
        {options.map((opt) => {
          const params = new URLSearchParams({ ...extraParams, [param]: opt.value });
          const active = opt.value === value;
          return (
            <Link
              key={opt.value}
              href={`${basePath}?${params.toString()}`}
              className={cn(
                PILL,
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {opt.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
