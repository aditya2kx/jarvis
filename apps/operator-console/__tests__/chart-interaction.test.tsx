// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SeriesToggles, useHiddenSeries } from "@/components/charts/ChartInteraction";

afterEach(cleanup);

const items = [
  { key: "a", label: "Austin", color: "red" },
  { key: "b", label: "Houston", color: "blue" },
];

function Harness() {
  const { hidden, toggle } = useHiddenSeries();
  return (
    <>
      <SeriesToggles items={items} hidden={hidden} onToggle={toggle} />
      <output>{[...hidden].join(",")}</output>
    </>
  );
}

describe("SeriesToggles", () => {
  it("toggles a series off and back on", () => {
    render(<Harness />);
    const austin = screen.getByRole("button", { name: "Austin" });
    fireEvent.click(austin);
    expect(austin).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("status")).toHaveTextContent("a");
    fireEvent.click(austin);
    expect(austin).toHaveAttribute("aria-pressed", "true");
  });

  it("never lets the last visible series be turned off", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Austin" }));
    expect(screen.getByRole("button", { name: "Houston" })).toBeDisabled();
  });

  it("renders nothing for a single series", () => {
    render(<SeriesToggles items={items.slice(0, 1)} hidden={new Set()} onToggle={() => {}} />);
    expect(screen.queryByTestId("series-toggles")).not.toBeInTheDocument();
  });
});
