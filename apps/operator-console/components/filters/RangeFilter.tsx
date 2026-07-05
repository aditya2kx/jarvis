import { FilterPills } from "./FilterPills";

/**
 * Range pill row (Figma: "Range 7d / 30d / 90d") — drives the page's own
 * query day-count via a `range` search param. Thin wrapper over the
 * generic `FilterPills` so every screen gets the same labeled-pill look;
 * kept as its own component since every page already imports it by name.
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
    <FilterPills
      label="Range"
      param="range"
      value={String(value)}
      options={options.map((days) => ({ value: String(days), label: `${days}d` }))}
      basePath={basePath}
      extraParams={extraParams}
    />
  );
}

export function parseRange(value: string | string[] | undefined, fallback = 30): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return [7, 30, 90].includes(n) ? n : fallback;
}
