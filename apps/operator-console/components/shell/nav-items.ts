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
  KeyRound,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Short label when this page holds a slot in the phone bottom tab bar. */
  tab: string;
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
      { href: "/order-quality", label: "Order Quality", icon: ShieldCheck, tab: "Quality" },
      { href: "/accounting", label: "Accounting", icon: Landmark, tab: "Accounting" },
    ],
  },
  {
    label: "People",
    items: [{ href: "/payroll", label: "Payroll & People", icon: Wallet, tab: "Payroll" }],
  },
  {
    label: "Inventory",
    items: [{ href: "/inventory", label: "Inventory / Ordering", icon: PackageSearch, tab: "Inventory" }],
  },
  {
    label: "System",
    items: [
      { href: "/automations", label: "Automations", icon: Bot, tab: "Automations" },
      { href: "/pipeline", label: "Pipeline Health", icon: Activity, tab: "Pipeline" },
      { href: "/access", label: "Access", icon: KeyRound, tab: "Access" },
    ],
  },
];

export function isNavItemActive(pathname: string | null, href: string): boolean {
  return pathname === href || (pathname?.startsWith(href + "/") ?? false);
}

export const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

/** Groups trimmed to the pages this person may open (`undefined` = every page); empty groups drop. */
export function visibleNavGroups(allowedHrefs: readonly string[] | undefined): NavGroup[] {
  if (!allowedHrefs) return NAV_GROUPS;
  return NAV_GROUPS.map((g) => ({ ...g, items: g.items.filter((i) => allowedHrefs.includes(i.href)) })).filter(
    (g) => g.items.length > 0,
  );
}

/** Phone tab bar before this device has any history (see recentTabs.ts). */
export const DEFAULT_TAB_HREFS = ["/home", "/sales", "/labor", "/payroll"] as const;

export const TAB_ITEMS: NavItem[] = DEFAULT_TAB_HREFS.map((href) => ALL_ITEMS.find((i) => i.href === href)!);

/** The nav item that owns `pathname` (longest matching href), for the phone top-bar title. */
export function activeNavItem(pathname: string | null): NavItem | undefined {
  return ALL_ITEMS.filter((i) => isNavItemActive(pathname, i.href)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
}
