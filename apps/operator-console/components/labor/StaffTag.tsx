"use client";

import { createContext, useContext } from "react";
import { Badge } from "@/components/ui/badge";
import type { StaffTag } from "@/lib/labor/new-hire-training";
import { cn } from "@/lib/utils";

const NO_TAGS: ReadonlyMap<string, StaffTag> = new Map();

/** Experienced / new-hire tags for everyone the coverage panel names (Issue #450). */
export const StaffTagsContext = createContext<ReadonlyMap<string, StaffTag>>(NO_TAGS);

export const useStaffTag = (employee: string | null | undefined) =>
  useContext(StaffTagsContext).get(employee ?? "");

export const TRAINING_TEXT_CLASS = "text-sky-700 dark:text-sky-300";
const TRAINING_BADGE_CLASS = "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300";

/** Pill beside a name in pickers: "Experienced" or "New hire · 1/2 openers · 0/2 closers". */
export function StaffTagBadge({ employee, className }: { employee: string; className?: string }) {
  const tag = useStaffTag(employee);
  if (!tag) return null;
  return tag.kind === "experienced" ? (
    <Badge variant="outline" className={cn("h-4 px-1.5 text-[11px] font-normal text-muted-foreground", className)}>
      Experienced
    </Badge>
  ) : (
    <Badge variant="outline" title={tag.detail} className={cn("h-4 px-1.5 text-[11px] font-normal", TRAINING_BADGE_CLASS, className)}>
      {tag.label}
    </Badge>
  );
}
