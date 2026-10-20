import {
  Home,
  LineChart,
  Clock,
  ShieldCheck,
  Wallet,
  PackageSearch,
  Activity,
  Landmark,
  Bot,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Short label for the phone bottom tab bar; items with one get a tab, the rest live in More. */
  tab?: string;
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
    items: [{ href: "/home", label: "Home", icon: Home, tab: "Home" }],
  },
  {
    label: "Performance",
    items: [
      { href: "/sales", label: "Sales", icon: LineChart, tab: "Sales" },
      { href: "/labor", label: "Labor", icon: Clock, tab: "Labor" },
      { href: "/order-quality", label: "Order Quality", icon: ShieldCheck },
      { href: "/accounting", label: "Accounting", icon: Landmark },
    ],
  },
  {
    label: "People",
    items: [{ href: "/payroll", label: "Payroll & People", icon: Wallet, tab: "Payroll" }],
  },
  {
    label: "Inventory",
    items: [{ href: "/inventory", label: "Inventory / Ordering", icon: PackageSearch }],
  },
  {
    label: "System",
    items: [
      { href: "/automations", label: "Automations", icon: Bot },
      { href: "/pipeline", label: "Pipeline Health", icon: Activity },
    ],
  },
];

export function isNavItemActive(pathname: string | null, href: string): boolean {
  return pathname === href || (pathname?.startsWith(href + "/") ?? false);
}

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

export const TAB_ITEMS: NavItem[] = ALL_ITEMS.filter((i) => i.tab);

/** The nav item that owns `pathname` (longest matching href), for the phone top-bar title. */
export function activeNavItem(pathname: string | null): NavItem | undefined {
  return ALL_ITEMS.filter((i) => isNavItemActive(pathname, i.href)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
}
