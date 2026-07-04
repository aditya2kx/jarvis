import Link from "next/link";
import { cn } from "@/lib/utils";

const PILL = "rounded-md px-2.5 py-1 text-xs font-medium transition-colors";

/**
 * Range pill row (Figma: "Range 7d / 30d / 90d") — drives the page's own
 * query day-count via a `range` search param, re-rendered server-side (no
 * client fetch). `basePath` must be the page's own route so the link
 * preserves any other search params via `extraParams`.
 */
export function RangeFilter({
  basePath,
  value,
  options = [7, 30, 90],
  extraParams = {},
}: {
  basePath: string;
  value: number;
  options?: number[];
  extraParams?: Record<string, string>;
}) {
  return (
    <div className="flex items-center gap-1 rounded-md bg-secondary p-0.5">
      {options.map((days) => {
        const params = new URLSearchParams({ ...extraParams, range: String(days) });
        const active = days === value;
        return (
          <Link
            key={days}
            href={`${basePath}?${params.toString()}`}
            className={cn(
              PILL,
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {days}d
          </Link>
        );
      })}
    </div>
  );
}

export function parseRange(value: string | string[] | undefined, fallback = 30): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return [7, 30, 90].includes(n) ? n : fallback;
}
