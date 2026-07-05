import type { ReactNode } from "react";

/**
 * Shared page header: H1 + optional muted subtitle + a right-hand slot for
 * filters/actions. Matches the pattern Home already used before every other
 * screen hand-rolled its own header `div` — see docs/operator-console/PLAN.md
 * "Consistency gaps". The right slot wraps on narrow screens so multiple
 * filter-pill groups don't force horizontal overflow (see M5 responsive work).
 */
export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-baseline sm:justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {right ? <div className="flex flex-wrap items-center gap-2">{right}</div> : null}
      </div>
      {subtitle ? <p className="text-sm text-muted-foreground">{subtitle}</p> : null}
    </div>
  );
}
