// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ColumnDef } from "@tanstack/react-table";

let pathname = "/sales";
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    className?: string;
    onClick?: () => void;
    "aria-current"?: "page";
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const { MobileTabBar } = await import("@/components/shell/MobileTabBar");
const { PageHeader } = await import("@/components/shell/PageHeader");
const { DataTable } = await import("@/components/tables/DataTable");
const { activeNavItem, TAB_ITEMS } = await import("@/components/shell/nav-items");
const { DEFAULT_SLOTS, parseSlots, recordVisit } = await import("@/components/shell/recentTabs");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  pathname = "/sales";
});

describe("nav items", () => {
  it("puts Home, Sales, Labor and Payroll in the phone tab bar", () => {
    expect(TAB_ITEMS.map((i) => i.tab)).toEqual(["Home", "Sales", "Labor", "Payroll"]);
  });

  it("resolves the phone title from nested routes", () => {
    expect(activeNavItem("/automations/team-pulse")?.label).toBe("Automations");
    expect(activeNavItem("/payroll")?.label).toBe("Payroll & People");
    expect(activeNavItem("/nope")).toBeUndefined();
  });
});

describe("MobileTabBar", () => {
  it("marks the current tab and lists every page under More", async () => {
    render(<MobileTabBar />);
    const bar = screen.getByTestId("mobile-tab-bar");
    expect(within(bar).getByRole("link", { name: "Sales" })).toHaveAttribute("aria-current", "page");
    expect(within(bar).getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");

    fireEvent.click(screen.getByRole("button", { name: "More pages" }));
    await waitFor(() => expect(screen.getByText("All pages")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Pipeline Health" })).toHaveAttribute("href", "/pipeline");
  });

  it("highlights More on a route that isn't a page in the nav", () => {
    pathname = "/nope";
    render(<MobileTabBar />);
    expect(screen.getByRole("button", { name: "More pages" })).toHaveClass("text-primary");
  });

  it("a page opened from More takes the least recently used tab, in place", () => {
    const tabNames = () =>
      within(screen.getByTestId("mobile-tab-bar"))
        .getAllByRole("link")
        .map((a) => a.textContent);
    pathname = "/home";
    const { rerender } = render(<MobileTabBar />);
    expect(tabNames()).toEqual(["Home", "Sales", "Labor", "Payroll"]);
    pathname = "/inventory";
    rerender(<MobileTabBar />);
    expect(tabNames()).toEqual(["Home", "Sales", "Labor", "Inventory"]);
    expect(within(screen.getByTestId("mobile-tab-bar")).getByRole("link", { name: "Inventory" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    pathname = "/sales";
    rerender(<MobileTabBar />);
    pathname = "/automations/team-pulse";
    rerender(<MobileTabBar />);
    expect(tabNames()).toEqual(["Home", "Sales", "Automations", "Inventory"]);
  });
});

describe("recordVisit", () => {
  it("refreshes a tab already in the bar without moving it", () => {
    const next = recordVisit(DEFAULT_SLOTS, "/labor", 10);
    expect(next.map((s) => s.href)).toEqual(["/home", "/sales", "/labor", "/payroll"]);
    expect(recordVisit(next, "/pipeline", 11).map((s) => s.href)).toEqual(["/home", "/sales", "/labor", "/pipeline"]);
  });

  it("falls back to the defaults on corrupt or stale storage", () => {
    expect(parseSlots("not json")).toBe(DEFAULT_SLOTS);
    expect(parseSlots(JSON.stringify([{ href: "/gone", usedAt: 1 }]))).toBe(DEFAULT_SLOTS);
  });
});

describe("PageHeader", () => {
  it("moves two or more filters behind the phone Filters sheet", () => {
    render(
      <PageHeader
        title="Sales"
        right={
          <>
            <span>Period filter</span>
            <span>Source filter</span>
            {null}
          </>
        }
        actions={<button type="button">Sync ADP</button>}
      />,
    );
    expect(screen.getByTestId("mobile-filters-trigger")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sync ADP" })).toHaveLength(1);
  });

  it("keeps a lone filter inline", () => {
    render(<PageHeader title="Home" right={<span>Period filter</span>} />);
    expect(screen.queryByTestId("mobile-filters-trigger")).not.toBeInTheDocument();
    expect(screen.getByText("Period filter")).toBeInTheDocument();
  });
});

interface Row {
  employee: string;
  hours: number;
  tips: number;
  note: string | null;
}

const columns: ColumnDef<Row>[] = [
  { accessorKey: "employee", header: "Employee" },
  { accessorKey: "hours", header: "Hours", meta: { format: { kind: "number", digits: 1 } } },
  { accessorKey: "tips", header: "Tips", meta: { format: { kind: "dollars" } } },
  { accessorKey: "note", header: "Note" },
];

const rows: Row[] = Array.from({ length: 25 }, (_, i) => ({
  employee: `Person ${String(i).padStart(2, "0")}`,
  hours: 10 + i,
  tips: 100 + i,
  note: i === 0 ? "Trainer" : null,
}));

function stubPhone(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) => ({
      matches,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    }),
  );
}

describe("DataTable on phones", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
  });

  it("defaults to the table, freezing only the first pinned column", () => {
    stubPhone(true);
    render(<DataTable columns={columns} data={rows} pinLeft={["employee", "hours"]} />);
    expect(screen.queryByTestId("data-table-cards")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Table layout" })).toHaveAttribute("aria-pressed", "true");
    const frozen = screen.getAllByRole("columnheader").filter((th) => th.hasAttribute("data-pinned"));
    expect(frozen.map((th) => th.dataset.colId)).toEqual(["employee"]);
  });

  it("keeps every pinned column frozen on desktop", () => {
    stubPhone(false);
    render(<DataTable columns={columns} data={rows} pinLeft={["employee", "hours"]} />);
    const frozen = screen.getAllByRole("columnheader").filter((th) => th.hasAttribute("data-pinned"));
    expect(frozen.map((th) => th.dataset.colId)).toEqual(["employee", "hours"]);
  });

  it("grouped columns get a spanning group header above their leaf headers", () => {
    stubPhone(false);
    type R = { item: string; onHand: number; after: number };
    const grouped: ColumnDef<R>[] = [
      { accessorKey: "item", header: "Item" },
      {
        id: "delivery-1",
        header: "Delivery Oct 21",
        columns: [
          { accessorKey: "onHand", header: "On hand" },
          { accessorKey: "after", header: "After restock" },
        ],
      },
    ];
    render(<DataTable columns={grouped} data={[{ item: "Acai", onHand: 2, after: 9 }]} pinLeft={["item"]} />);
    const [groupRow, leafRow] = within(screen.getByTestId("data-table")).getAllByRole("row");
    const group = within(groupRow!).getByText("Delivery Oct 21").closest("th")!;
    expect(group).toHaveAttribute("colspan", "2");
    expect(within(groupRow!).queryByText("Item")).not.toBeInTheDocument();
    expect(within(leafRow!).getByRole("button", { name: "Sort by After restock" })).toBeInTheDocument();
    expect(screen.getAllByText("Item")).toHaveLength(1);
  });

  it("cards: first column is the title, blank fields are skipped", () => {
    render(<DataTable columns={columns} data={rows} pinLeft={["employee"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Cards layout" }));
    const cards = within(screen.getByTestId("data-table-cards")).getAllByRole("listitem");
    expect(cards).toHaveLength(20);
    expect(within(cards[0]).getByText("Person 00")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Trainer")).toBeInTheDocument();
    expect(within(cards[1]).queryByText("Note")).not.toBeInTheDocument();
    expect(within(cards[1]).getByText("$101.00")).toBeInTheDocument();
  });

  it("cards page in 20s", () => {
    render(<DataTable columns={columns} data={rows} />);
    fireEvent.click(screen.getByRole("button", { name: "Cards layout" }));
    fireEvent.click(screen.getByRole("button", { name: /Show 5 more/ }));
    expect(within(screen.getByTestId("data-table-cards")).getAllByRole("listitem")).toHaveLength(25);
  });

  it("long tables render 50 rows, then page in 50s; figures right-align", () => {
    stubPhone(false);
    const many: Row[] = Array.from({ length: 120 }, (_, i) => ({ employee: `P${i}`, hours: i, tips: i, note: null }));
    render(<DataTable columns={columns} data={many} />);
    const bodyRows = () => screen.getAllByRole("row").length - 1;
    expect(bodyRows()).toBe(50);
    expect(screen.getByText("Showing 50 of 120 rows")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 50 more" }));
    fireEvent.click(screen.getByRole("button", { name: "Show 20 more" }));
    expect(bodyRows()).toBe(120);
    expect(screen.queryByRole("button", { name: /more$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Hours/ })).toHaveClass("text-right");
    expect(screen.getByRole("columnheader", { name: /Employee/ })).not.toHaveClass("text-right");
  });
});
