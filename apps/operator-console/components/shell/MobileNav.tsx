"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MenuIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { NAV_GROUPS, isNavItemActive } from "./nav-items";

// Sidebar is `hidden md:flex` (desktop-only) — below md there was previously
// no way to navigate at all. This renders the same NAV_GROUPS in a Sheet,
// shown only on narrow screens (`md:hidden` on the trigger), and closes
// itself on navigation so the operator doesn't have to close it by hand.
export function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label="Open navigation">
            <MenuIcon />
          </Button>
        }
      />
      <SheetContent side="left" className="w-64 overflow-y-auto px-3 py-4">
        <SheetHeader className="p-0">
          <SheetTitle className="px-2">Palmetto, Texas — Operator Console</SheetTitle>
        </SheetHeader>
        <div className="flex flex-col gap-6">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="flex flex-col gap-1">
              <span className="px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {group.label}
              </span>
              {group.items.map((item) => {
                const active = isNavItemActive(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setOpen(false)}
                    className={cn(
                      "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                      active
                        ? "bg-sidebar-primary text-sidebar-primary-foreground"
                        : "text-foreground hover:bg-muted",
                    )}
                  >
                    <item.icon className="size-4" />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}
