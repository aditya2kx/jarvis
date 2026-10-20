import type { ReactNode } from "react";
import { ChevronDownIcon, InfoIcon } from "lucide-react";

/**
 * Explanatory page note: always open on md+, a tap-to-expand row on phones so a
 * paragraph of chart legend doesn't push the first chart two screens down.
 */
export function InfoNote({ summary, children }: { summary: string; children: ReactNode }) {
  const box = "rounded-md border border-border bg-muted/40 text-sm text-muted-foreground";
  return (
    <>
      <details className={`group md:hidden ${box}`}>
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 [&::-webkit-details-marker]:hidden">
          <InfoIcon className="size-4 shrink-0" aria-hidden />
          <span className="flex-1 font-medium text-foreground">{summary}</span>
          <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="px-3 pb-3">{children}</div>
      </details>
      <div role="note" className={`hidden px-3 py-2 md:block ${box}`}>
        {children}
      </div>
    </>
  );
}
