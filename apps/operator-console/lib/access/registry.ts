/**
 * Every page and section the Access page can grant. Section ids are stable keys stored
 * in `console_access_grants.grant_json` — rename the label freely, never the id.
 * `href` must match the page's entry in components/shell/nav-items.ts.
 */
export const ACCESS_PAGES = [
  {
    id: "home",
    label: "Home",
    href: "/home",
    sections: [
      { id: "scorecard", label: "Health summary" },
      { id: "goals", label: "Edit goals" },
    ],
  },
  {
    id: "sales",
    label: "Sales",
    href: "/sales",
    sections: [
      { id: "charts", label: "Sales charts" },
      { id: "detail", label: "Detail table" },
    ],
  },
  {
    id: "labor",
    label: "Labor",
    href: "/labor",
    sections: [
      { id: "labor-hours", label: "Labor hours" },
      { id: "labor-wages", label: "Labor wages" },
      { id: "concurrent", label: "On the floor" },
      { id: "coverage", label: "Coverage & shift draft" },
      { id: "availability", label: "ADP availability" },
      { id: "hours-per-person", label: "Hours per person" },
      { id: "open-shifts", label: "Open shifts" },
      { id: "one-person", label: "One person" },
      { id: "punch-gaps", label: "Punch gaps" },
      { id: "solo", label: "Solo vs team hours" },
    ],
  },
  {
    id: "order-quality",
    label: "Order Quality",
    href: "/order-quality",
    sections: [
      { id: "prep-chart", label: "Prep time chart" },
      { id: "percentiles", label: "Percentile detail" },
      { id: "orders", label: "Order KDS times" },
    ],
  },
  {
    id: "accounting",
    label: "Accounting",
    href: "/accounting",
    sections: [
      { id: "bank-link", label: "Bank link" },
      { id: "ledger", label: "Bank feed & categories" },
    ],
  },
  {
    id: "payroll",
    label: "Payroll & People",
    href: "/payroll",
    sections: [
      { id: "summary", label: "Pay period totals" },
      { id: "per-employee", label: "Per-employee table" },
      { id: "adp-draft", label: "ADP payroll draft" },
      { id: "tip-exemptions", label: "Tip exemptions" },
      { id: "perks", label: "Reimbursements" },
      { id: "recognition", label: "Recognition bonuses" },
      { id: "review-bonuses", label: "Google review bonuses" },
    ],
  },
  {
    id: "inventory",
    label: "Inventory / Ordering",
    href: "/inventory",
    sections: [
      { id: "order-reco", label: "Order recommendations & restock" },
      { id: "ordered-actuals", label: "Ordered tubs (actuals)" },
      { id: "usage-by-day", label: "Base usage by day" },
    ],
  },
  {
    id: "automations",
    label: "Automations",
    href: "/automations",
    sections: [
      { id: "team-pulse", label: "Team pulse" },
      { id: "unavailability-reminder", label: "Unavailability reminder" },
      { id: "monthly-recognition", label: "Monthly recognition" },
    ],
  },
  {
    id: "pipeline",
    label: "Pipeline Health",
    href: "/pipeline",
    sections: [
      { id: "summary", label: "Run summary" },
      { id: "source-freshness", label: "Source freshness" },
      { id: "run-history", label: "Run history" },
    ],
  },
] as const;

export type AccessPage = (typeof ACCESS_PAGES)[number];
export type PageId = AccessPage["id"];
export type SectionOf<P extends PageId> = Extract<AccessPage, { id: P }>["sections"][number]["id"];

export function accessPage(id: string): AccessPage | undefined {
  return ACCESS_PAGES.find((p) => p.id === id);
}

export function sectionIds(page: PageId): string[] {
  return accessPage(page)!.sections.map((s) => s.id);
}

