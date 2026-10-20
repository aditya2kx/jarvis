import type { ReactNode } from "react";
import { MobileFilters } from "./MobileFilters";

/**
 * Shared page header: H1 + optional muted subtitle + filters + actions. Matches the
 * pattern Home already used before every other screen hand-rolled its own header `div` —
 * see docs/operator-console/PLAN.md "Consistency gaps".
 *
 * `right` holds the page's filter controls: inline (wrapping) on md+, behind a Filters
 * bottom sheet on phones. `actions` (drawers, sync buttons) render once and stay visible
 * at every width. Pass filters as a fragment of controls, not one wrapper `div`, so the
 * phone sheet can lay them out one per row.
 */
export function PageHeader({
  title,
  subtitle,
  right,
  actions,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  actions?: ReactNode;
}) {
  const controls = right || actions;
  return (
    <div className="flex flex-col gap-1">
      {/* Phones flatten this row (`contents`) so `order` can put the subtitle between the
          title and the controls; md+ keeps title | controls on one row. */}
      <div className="flex flex-col gap-2 max-md:contents sm:flex-row sm:items-center sm:justify-between">
        <h1 className="shrink-0 text-xl font-semibold tracking-tight md:text-2xl">{title}</h1>
        {controls ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-2 max-md:order-2 max-md:mt-2">
            {right ? (
              <>
                <MobileFilters title={title}>{right}</MobileFilters>
                <div className="hidden md:contents">{right}</div>
              </>
            ) : null}
            {actions}
          </div>
        ) : null}
      </div>
      {subtitle ? <p className="text-sm text-muted-foreground max-md:order-1">{subtitle}</p> : null}
    </div>
  );
}
