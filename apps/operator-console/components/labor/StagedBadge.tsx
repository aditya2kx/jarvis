import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** A change waiting in the Changes to ADP tray: outlined so it reads as a status, not a button. */
export function StagedBadge({ remove = false, children }: { remove?: boolean; children: ReactNode }) {
  return (
    <Badge variant="outline" className={cn(remove ? "border-destructive/50 text-destructive" : "border-primary/50 text-primary")}>
      {children}
    </Badge>
  );
}
