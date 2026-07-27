"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PanelLeftCloseIcon, PanelLeftIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { NAV_GROUPS, isNavItemActive } from "./nav-items";
import { useSidebarCollapsed } from "./useSidebarCollapsed";

export function Sidebar() {
  const pathname = usePathname();
  const { collapsed, toggle } = useSidebarCollapsed();

  return (
    <nav
      data-collapsed={collapsed ? "true" : "false"}
      aria-label="Main"
      className={cn(
        "hidden shrink-0 border-r border-sidebar-border bg-sidebar py-4 md:flex md:flex-col md:gap-4",
        "transition-[width,padding] duration-200 ease-out",
        collapsed ? "w-14 px-2" : "w-60 px-3",
      )}
    >
      <div className={cn("flex", collapsed ? "justify-center" : "justify-end")}>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftIcon className="size-4" />
          ) : (
            <PanelLeftCloseIcon className="size-4" />
          )}
        </Button>
      </div>

      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="flex flex-col gap-1">
          {!collapsed ? (
            <span className="px-2 text-xs font-medium uppercase tracking-wide text-sidebar-foreground/50">
              {group.label}
            </span>
          ) : null}
          {group.items.map((item) => {
            const active = isNavItemActive(pathname, item.href);
            const linkClass = cn(
              "flex items-center rounded-md text-sm transition-colors",
              collapsed ? "justify-center px-0 py-2" : "gap-2 px-2 py-1.5",
              active
                ? "bg-sidebar-primary text-sidebar-primary-foreground"
                : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            );
            const linkBody = (
              <>
                <item.icon className="size-4 shrink-0" />
                {!collapsed ? <span className="truncate">{item.label}</span> : null}
              </>
            );

            if (!collapsed) {
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={linkClass}
                  aria-current={active ? "page" : undefined}
                >
                  {linkBody}
                </Link>
              );
            }

            return (
              <Tooltip key={item.href}>
                <TooltipTrigger
                  render={
                    <Link
                      href={item.href}
                      className={linkClass}
                      aria-label={item.label}
                      aria-current={active ? "page" : undefined}
                    />
                  }
                >
                  {linkBody}
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={8}>
                  {item.label}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
