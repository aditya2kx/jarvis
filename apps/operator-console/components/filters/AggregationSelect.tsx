import { FilterSelect } from "./FilterSelect";
import { GRAINS, type Grain } from "@/lib/filters/range";

/**
 * Thin `FilterSelect` wrapper for the Aggregation grain picker shared by every
 * Performance screen (Issue #132 follow-up; Entire period added Issue #225).
 * Always a dropdown (companion to Period — not a pill row).
 */
export function AggregationSelect({
  value,
  basePath,
  extraParams = {},
}: {
  value: Grain;
  basePath: string;
  extraParams?: Record<string, string>;
}) {
  return (
    <FilterSelect
      label="Aggregation"
      param="grain"
      value={value}
      options={GRAINS}
      basePath={basePath}
      extraParams={extraParams}
    />
  );
}
