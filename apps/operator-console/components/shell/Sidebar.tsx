import Link from "next/link";
import {
  Home,
  LineChart,
  Clock,
  TrendingUp,
  ShieldCheck,
  Wallet,
  PackageSearch,
  Activity,
} from "lucide-react";

// Mirrors the Figma IA agreed in docs/operator-console/PLAN.md — grouped by
// operator intent, not by Grafana's original folder structure.
const NAV_GROUPS = [
  {
    label: "Overview",
    items: [{ href: "/home", label: "Home", icon: Home }],
  },
  {
    label: "Performance",
    items: [
      { href: "/sales", label: "Sales", icon: LineChart },
      { href: "/labor", label: "Labor", icon: Clock },
      { href: "/forecast", label: "Forecast", icon: TrendingUp },
      { href: "/order-quality", label: "Order Quality", icon: ShieldCheck },
    ],
  },
  {
    label: "People",
    items: [{ href: "/payroll", label: "Payroll & People", icon: Wallet }],
  },
  {
    label: "Inventory",
    items: [{ href: "/inventory", label: "Inventory / Ordering", icon: PackageSearch }],
  },
  {
    label: "System",
    items: [{ href: "/pipeline", label: "Pipeline Health", icon: Activity }],
  },
] as const;

export function Sidebar() {
  return (
    <nav className="hidden w-60 shrink-0 border-r border-sidebar-border bg-sidebar px-3 py-4 md:flex md:flex-col md:gap-6">
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="flex flex-col gap-1">
          <span className="px-2 text-xs font-medium uppercase tracking-wide text-sidebar-foreground/50">
            {group.label}
          </span>
          {group.items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
