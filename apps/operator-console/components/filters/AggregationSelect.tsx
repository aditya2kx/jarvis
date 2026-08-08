import { FilterSelect } from "./FilterSelect";
import { GRAINS, GRAINS_WITHOUT_HOUR, type Grain } from "@/lib/filters/range";
import type { FilterOption } from "./FilterPills";

/**
 * Thin `FilterSelect` wrapper for the Aggregation grain picker shared by every
 * Performance screen (Issue #132 follow-up; Entire period added Issue #225;
 * Hour of day added Issue #227 — Sales + Labor via `options={GRAINS}`).
 * Always a dropdown (companion to Period — not a pill row).
 *
 * Default options omit Hour (Accounting / Order Quality). Pass `GRAINS` on
 * Sales and Labor so Hour appears there.
 */
export function AggregationSelect({
  value,
  basePath,
  extraParams = {},
  options = GRAINS_WITHOUT_HOUR,
}: {
  value: Grain;
  basePath: string;
  extraParams?: Record<string, string>;
  options?: FilterOption[];
}) {
  const opts = options.length ? options : GRAINS;
  return (
    <FilterSelect
      label="Aggregation"
      param="grain"
      value={value}
      options={opts}
      basePath={basePath}
      extraParams={extraParams}
    />
  );
}
