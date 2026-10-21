import { StoreFilter } from "./StoreFilter";
import { MobilePageTitle } from "./MobilePageTitle";
import { ThemeToggle } from "./ThemeToggle";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { pipelineRuns } from "@/lib/bq/queries";
import { cn } from "@/lib/utils";
import { runTone } from "@/lib/run-status";
import { isConsolePreview } from "@/lib/config/preview";
import { Badge } from "@/components/ui/badge";
import { Eye } from "lucide-react";
import { ViewAsButton } from "@/components/access/ViewAsMenu";

async function PipelineDot() {
  let status: string | undefined;
  try {
    status = (await pipelineRuns())[0]?.status;
  } catch {
    status = undefined; // honest "unknown" without local ADC — never fake a color
  }
  const color = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    bad: "bg-red-500",
    unknown: "bg-muted-foreground/40",
  }[runTone(status)];
  return (
    <span
      title={`Last pipeline run: ${status ?? "unknown"}`}
      className={cn("size-2 rounded-full", color)}
    />
  );
}

/** `canViewAs`: an owner not already viewing as someone gets the View as… picker. */
export function Topbar({ operatorEmail, canViewAs }: { operatorEmail?: string; canViewAs?: boolean }) {
  return (
    <header className="shrink-0 border-b border-border bg-background pt-[env(safe-area-inset-top)]">
      <div className="flex h-14 items-center justify-between gap-2 px-4">
        <div className="flex min-w-0 items-center gap-2">
          <PipelineDot />
          <MobilePageTitle />
          <span className="hidden truncate text-sm font-semibold tracking-tight md:inline">
            Palmetto · Texas — Operator Console
          </span>
          {isConsolePreview() ? (
            <Badge
              variant="outline"
              title="Read-only preview: prod data, every save is disabled"
              aria-label="Preview"
              className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
            >
              <Eye aria-hidden />
              <span className="hidden sm:inline">Preview</span>
            </Badge>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground sm:gap-3">
          {canViewAs ? <ViewAsButton /> : null}
          <ThemeToggle />
          <StoreFilter store={DEFAULT_STORE} />
          {operatorEmail ? <span className="hidden sm:inline">{operatorEmail}</span> : null}
        </div>
      </div>
    </header>
  );
}
