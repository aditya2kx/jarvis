import { Children, Fragment, isValidElement, type ReactNode } from "react";
import { MobileFilters } from "./MobileFilters";

/**
 * Shared page header: H1 + optional muted subtitle + filters + actions. Matches the
 * pattern Home already used before every other screen hand-rolled its own header `div` —
 * see docs/operator-console/PLAN.md "Consistency gaps".
 *
 * `right` holds the page's filter controls: inline (wrapping) on md+, behind a Filters
 * bottom sheet on phones (a lone filter stays inline there too). `actions` (drawers, sync buttons) render once and stay visible
 * at every width. Pass filters as a fragment of controls, not one wrapper `div`, so the
 * phone sheet can lay them out one per row.
 */
/** Rendered controls inside `right`, looking through fragments (null / false don't count). */
function controlCount(node: ReactNode): number {
  let n = 0;
  Children.forEach(node, (child) => {
    if (isValidElement<{ children?: ReactNode }>(child) && child.type === Fragment) {
      n += controlCount(child.props.children);
    } else if (child != null && typeof child !== "boolean") {
      n += 1;
    }
  });
  return n;
}

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
          <div
            data-testid="page-header-controls"
            className={
              "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-2 " +
              // Phones: one swipeable row of equal-height pills (Filters first, then actions).
              "max-md:order-2 max-md:-mx-4 max-md:mt-2 max-md:flex-nowrap max-md:overflow-x-auto max-md:px-4 max-md:pb-1 max-md:[scrollbar-width:none] " +
              "max-md:[&_[data-slot=button]]:h-10 max-md:[&_[data-slot=button]]:shrink-0 max-md:[&_[data-slot=button]]:min-h-10 max-md:[&_[data-slot=button]]:rounded-full max-md:[&_[data-slot=button]]:px-4"
            }
          >
            {right && controlCount(right) > 1 ? (
              <>
                <MobileFilters title={title}>{right}</MobileFilters>
                <div className="hidden md:contents">{right}</div>
              </>
            ) : (
              right
            )}
            {actions}
          </div>
        ) : null}
      </div>
      {subtitle ? <p className="text-sm text-muted-foreground max-md:order-1">{subtitle}</p> : null}
    </div>
  );
}
