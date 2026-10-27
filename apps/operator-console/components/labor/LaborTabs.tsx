import Link from "next/link";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/labor", label: "Insights" },
  { href: "/labor/availability", label: "Availability" },
] as const;

/** Sub-pages of Labor; the sidebar keeps one Labor entry for all of them. */
export function LaborTabs({ active, pendingRequests }: { active: (typeof TABS)[number]["href"]; pendingRequests?: number }) {
  return (
    <nav aria-label="Labor pages" className="-mt-1 flex gap-1 overflow-x-auto border-b border-border [scrollbar-width:none]">
      {TABS.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          aria-current={active === t.href ? "page" : undefined}
          className={cn(
            "-mb-px flex h-9 items-center gap-1.5 border-b-2 px-3 text-sm font-medium whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:h-11",
            active === t.href ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
          {t.href === "/labor/availability" && pendingRequests ? (
            <span className="rounded-full bg-amber-500/15 px-1.5 text-[11px] font-semibold text-amber-700 tabular-nums dark:text-amber-300">
              {pendingRequests}
            </span>
          ) : null}
        </Link>
      ))}
    </nav>
  );
}
