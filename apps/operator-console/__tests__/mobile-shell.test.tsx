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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

  it("highlights More on a page without its own tab", () => {
    pathname = "/inventory";
    render(<MobileTabBar />);
    expect(screen.getByRole("button", { name: "More pages" })).toHaveClass("text-primary");
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

describe("DataTable phone cards", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
  });
  it("renders the first column as the card title and skips blank fields", () => {
    render(<DataTable columns={columns} data={rows} pinLeft={["employee"]} />);
    const cards = within(screen.getByTestId("data-table-cards")).getAllByRole("listitem");
    expect(cards).toHaveLength(20);
    expect(within(cards[0]).getByText("Person 00")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Trainer")).toBeInTheDocument();
    expect(within(cards[1]).queryByText("Note")).not.toBeInTheDocument();
    expect(within(cards[1]).getByText("$101.00")).toBeInTheDocument();
  });

  it("pages in 20s", () => {
    render(<DataTable columns={columns} data={rows} />);
    fireEvent.click(screen.getByRole("button", { name: /Show 5 more/ }));
    expect(within(screen.getByTestId("data-table-cards")).getAllByRole("listitem")).toHaveLength(25);
  });

  it("switches to the table layout", () => {
    render(<DataTable columns={columns} data={rows} />);
    fireEvent.click(screen.getByRole("button", { name: "Table layout" }));
    expect(screen.queryByTestId("data-table-cards")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Table layout" })).toHaveAttribute("aria-pressed", "true");
  });
});
