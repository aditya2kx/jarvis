"use client";

import { useId, useSyncExternalStore, type ReactNode } from "react";
import { ChevronDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const storageKey = (id: string) => `oc_section_collapsed:${id}`;
const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

function readCollapsed(id: string): boolean {
  try {
    return localStorage.getItem(storageKey(id)) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(id: string, collapsed: boolean): void {
  try {
    localStorage.setItem(storageKey(id), collapsed ? "1" : "0");
  } catch {
    // ignore quota / private mode
  }
  for (const l of listeners) l();
}

/**
 * Page section with a clickable header that hides its content. Collapsed content stays
 * mounted (only `hidden`) so sections that publish shared state — e.g. the coverage
 * panel's drafts — keep feeding the rest of the page. State persists per `id`.
 */
export function CollapsibleSection({
  id,
  title,
  meta,
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  const collapsed = useSyncExternalStore(subscribe, () => readCollapsed(id), () => false);
  const bodyId = useId();
  const toggle = () => writeCollapsed(id, !collapsed);

  return (
    <section data-testid={`section-${id}`} className="flex flex-col gap-2">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={!collapsed}
        aria-controls={bodyId}
        className="group flex w-full items-center gap-2 rounded-md border-b border-border py-1.5 text-left outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronDownIcon
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform group-hover:text-foreground",
            collapsed && "-rotate-90",
          )}
          aria-hidden
        />
        <span className="text-sm font-semibold text-foreground">{title}</span>
        {collapsed && meta ? <span className="truncate text-xs text-muted-foreground">{meta}</span> : null}
        <span className="ml-auto text-xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          {collapsed ? "Show" : "Hide"}
        </span>
      </button>
      <div id={bodyId} hidden={collapsed} className="flex flex-col gap-4">
        {children}
      </div>
    </section>
  );
}
