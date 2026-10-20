"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutGridIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { NAV_GROUPS, TAB_ITEMS, isNavItemActive } from "./nav-items";

const TAB =
  "flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring active:bg-muted";

/**
 * Phone navigation (below md): a fixed bottom tab bar for the four pages an operator
 * checks most, plus More — a bottom sheet with every page from NAV_GROUPS. Thumb-reachable
 * and one tap per page, unlike the old top-left hamburger. The bar pads for the iOS home
 * indicator so it sits above it in Safari and as a home-screen app.
 */
export function MobileTabBar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const onTab = TAB_ITEMS.some((i) => isNavItemActive(pathname, i.href));

  return (
    <nav
      aria-label="Main"
      data-testid="mobile-tab-bar"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg supports-[backdrop-filter]:bg-background/75 md:hidden"
    >
      <div className="mx-auto flex h-[var(--tabbar-h)] max-w-lg items-stretch gap-1 px-2 py-1">
        {TAB_ITEMS.map((item) => {
          const active = isNavItemActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(TAB, active ? "text-primary" : "text-muted-foreground")}
            >
              <item.icon className="size-5" strokeWidth={active ? 2.25 : 1.75} aria-hidden />
              {item.tab}
            </Link>
          );
        })}
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger
            render={
              <button
                type="button"
                className={cn(TAB, !onTab ? "text-primary" : "text-muted-foreground")}
                aria-label="More pages"
              />
            }
          >
            <LayoutGridIcon className="size-5" strokeWidth={!onTab ? 2.25 : 1.75} aria-hidden />
            More
          </SheetTrigger>
          <SheetContent
            side="bottom"
            className="max-h-[85dvh] gap-0 overflow-y-auto rounded-t-2xl px-4 pb-[calc(1rem+env(safe-area-inset-bottom))]"
          >
            <div aria-hidden className="mx-auto mt-2 h-1 w-10 rounded-full bg-muted-foreground/30" />
            <SheetHeader className="px-0 pt-3 pb-2">
              <SheetTitle>All pages</SheetTitle>
            </SheetHeader>
            <div className="flex flex-col gap-5">
              {NAV_GROUPS.map((group) => (
                <div key={group.label} className="flex flex-col gap-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {group.label}
                  </span>
                  <div className="grid grid-cols-2 gap-2">
                    {group.items.map((item) => {
                      const active = isNavItemActive(pathname, item.href);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          onClick={() => setOpen(false)}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "flex min-h-12 items-center gap-2.5 rounded-xl border px-3 text-sm font-medium transition-colors active:scale-[0.98]",
                            active
                              ? "border-primary/50 bg-primary/10 text-foreground"
                              : "border-border bg-card text-foreground hover:bg-muted",
                          )}
                        >
                          <item.icon
                            className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")}
                            aria-hidden
                          />
                          <span className="truncate">{item.label}</span>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </nav>
  );
}
