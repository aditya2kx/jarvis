import { StoreFilter } from "./StoreFilter";
import { DEFAULT_STORE } from "@/lib/auth/identity";

// Pipeline-health status is wired to vw_pipeline_runs / vw_source_pulls in M2
// (see docs/operator-console/EXECUTION.md §4 M2); "unknown" is the honest
// default until that read path exists.
export function Topbar({ operatorEmail }: { operatorEmail?: string }) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background px-4">
      <span className="text-sm font-semibold tracking-tight">
        Palmetto · Texas — Operator Console
      </span>
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <StoreFilter store={DEFAULT_STORE} />
        {operatorEmail ? <span className="hidden sm:inline">{operatorEmail}</span> : null}
      </div>
    </header>
  );
}
