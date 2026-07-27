// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("next/navigation", () => ({
  usePathname: () => "/home",
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
    "aria-label"?: string;
    "aria-current"?: string;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const { Sidebar } = await import("@/components/shell/Sidebar");
const { SIDEBAR_STORAGE_KEY } = await import("@/components/shell/useSidebarCollapsed");

function renderSidebar() {
  return render(
    <TooltipProvider>
      <Sidebar />
    </TooltipProvider>,
  );
}

describe("Sidebar icon rail", () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    store.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 1280,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows labels when expanded and collapses to icon-only", async () => {
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText("Home")).toBeInTheDocument();
    });
    expect(screen.getByText("Overview")).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.queryByText("Overview")).not.toBeInTheDocument();
      expect(screen.queryByText("Home")).not.toBeInTheDocument();
    });

    const expand = screen.getByRole("button", { name: "Expand sidebar" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    expect(store.get(SIDEBAR_STORAGE_KEY)).toBe("1");
  });

  it("defaults collapsed below lg when no stored preference", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 900,
    });
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
    });
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sales" })).toBeInTheDocument();
  });
});
