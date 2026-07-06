import { FilterSelect } from "./FilterSelect";
import { GRAINS, type Grain } from "@/lib/filters/range";

/**
 * Thin `FilterSelect` wrapper for the daily/weekly/monthly grain picker
 * shared by every Performance screen (Issue #132 follow-up). Always a
 * dropdown (3 options today, but consistent with the >=5-option `Source`
 * convention documented in FilterSelect — this one's a fixed companion to
 * the Period picker, not a pill row, so the two read as a pair).
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
