import type { ReactNode } from "react";
import { ChevronDownIcon, InfoIcon } from "lucide-react";

/**
 * Explanatory page note as a tap-to-expand row at every width, so a paragraph of chart
 * legend never sits between the page header and the first chart.
 */
export function InfoNote({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group rounded-lg border border-border bg-muted/30 text-sm text-muted-foreground">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-3 transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:min-h-9 [&::-webkit-details-marker]:hidden">
        <InfoIcon className="size-4 shrink-0" aria-hidden />
        <span className="flex-1 font-medium text-foreground/90">{summary}</span>
        <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <div className="max-w-prose px-3 pb-3 leading-relaxed md:max-w-4xl">{children}</div>
    </details>
  );
}
