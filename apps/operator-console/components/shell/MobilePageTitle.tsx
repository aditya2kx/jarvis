"use client";

import { usePathname } from "next/navigation";
import { activeNavItem } from "./nav-items";

/** Phone top-bar title: the current page, since the full brand doesn't fit at 390px. */
export function MobilePageTitle() {
  const item = activeNavItem(usePathname());
  return (
    <span className="truncate text-base font-semibold tracking-tight md:hidden">
      {item?.label ?? "Operator Console"}
    </span>
  );
}
