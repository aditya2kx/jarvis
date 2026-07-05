import {
  Home,
  LineChart,
  Clock,
  TrendingUp,
  ShieldCheck,
  Wallet,
  PackageSearch,
  Activity,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

// Mirrors the Figma IA agreed in docs/operator-console/PLAN.md — grouped by
// operator intent, not by Grafana's original folder structure. Single
// source of truth for both the desktop Sidebar and the mobile Sheet nav
// (see components/shell/MobileNav.tsx) — never duplicate this list.
export const NAV_GROUPS: NavGroup[] = [
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
];

export function isNavItemActive(pathname: string | null, href: string): boolean {
  return pathname === href || (pathname?.startsWith(href + "/") ?? false);
}
