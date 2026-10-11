import { StoreFilter } from "./StoreFilter";
import { MobileNav } from "./MobileNav";
import { ThemeToggle } from "./ThemeToggle";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { pipelineRuns } from "@/lib/bq/queries";
import { cn } from "@/lib/utils";
import { isConsolePreview } from "@/lib/config/preview";
import { Badge } from "@/components/ui/badge";
import { Eye } from "lucide-react";

async function PipelineDot() {
  let status: string | undefined;
  try {
    status = (await pipelineRuns())[0]?.status;
  } catch {
    status = undefined; // honest "unknown" without local ADC — never fake a color
  }
  const color =
    status === "success" ? "bg-emerald-500" : status ? "bg-red-500" : "bg-muted-foreground/40";
  return (
    <span
      title={`Last pipeline run: ${status ?? "unknown"}`}
      className={cn("size-2 rounded-full", color)}
    />
  );
}

export function Topbar({ operatorEmail }: { operatorEmail?: string }) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border bg-background px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MobileNav />
        <PipelineDot />
        <span className="truncate text-sm font-semibold tracking-tight">
          Palmetto · Texas — Operator Console
        </span>
        {isConsolePreview() ? (
          <Badge
            variant="outline"
            title="Read-only preview: prod data, every save is disabled"
            className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
          >
            <Eye aria-hidden />
            Preview
          </Badge>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-3 text-sm text-muted-foreground">
        <ThemeToggle />
        <StoreFilter store={DEFAULT_STORE} />
        {operatorEmail ? <span className="hidden sm:inline">{operatorEmail}</span> : null}
      </div>
    </header>
  );
}
