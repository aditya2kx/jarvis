import { StoreFilter } from "./StoreFilter";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { pipelineRuns } from "@/lib/bq/queries";
import { cn } from "@/lib/utils";

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
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background px-4">
      <div className="flex items-center gap-2">
        <PipelineDot />
        <span className="text-sm font-semibold tracking-tight">
          Palmetto · Texas — Operator Console
        </span>
      </div>
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <StoreFilter store={DEFAULT_STORE} />
        {operatorEmail ? <span className="hidden sm:inline">{operatorEmail}</span> : null}
      </div>
    </header>
  );
}
