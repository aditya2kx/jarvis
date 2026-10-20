"use client";

import { useState, type ReactNode } from "react";
import { SlidersHorizontalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

/**
 * Phone home for a page's filter controls (below md). A page header with five-plus
 * filters used to stack them above the fold at 390px; here they live in a bottom sheet,
 * one control per row. Filters keep their URL contract, so the sheet stays open while
 * the page re-renders behind it and the operator can change several before Done.
 */
export function MobileFilters({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button
            variant="outline"
            className="h-10 gap-2 rounded-full px-4 text-sm md:hidden"
            data-testid="mobile-filters-trigger"
          />
        }
      >
        <SlidersHorizontalIcon className="size-4" aria-hidden />
        Filters
      </SheetTrigger>
      <SheetContent
        side="bottom"
        className="max-h-[85dvh] gap-0 rounded-t-2xl pb-[env(safe-area-inset-bottom)]"
      >
        <div aria-hidden className="mx-auto mt-2 h-1 w-10 rounded-full bg-muted-foreground/30" />
        <SheetHeader className="px-4 pt-3 pb-1">
          <SheetTitle>{title} filters</SheetTitle>
        </SheetHeader>
        <div
          data-testid="mobile-filters"
          className="flex flex-col overflow-y-auto px-4 [&>*]:flex [&>*]:min-h-14 [&>*]:w-full [&>*]:flex-wrap [&>*]:items-center [&>*]:justify-between [&>*]:gap-x-3 [&>*]:gap-y-2 [&>*]:border-b [&>*]:border-border [&>*]:py-2.5 [&>*:last-child]:border-b-0"
        >
          {children}
        </div>
        <SheetFooter className="border-t border-border px-4 pt-3">
          <Button className="h-11 w-full text-base" onClick={() => setOpen(false)}>
            Done
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
