// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Input } from "@/components/ui/input";

afterEach(cleanup);

describe("Input phone keypad", () => {
  it("uses the digit pad for non-negative whole numbers", () => {
    render(<Input aria-label="n" type="number" min={0} />);
    expect(screen.getByLabelText("n")).toHaveAttribute("inputmode", "numeric");
  });

  it("adds the decimal point for fractional steps", () => {
    render(<Input aria-label="n" type="number" min={0} step={0.5} />);
    expect(screen.getByLabelText("n")).toHaveAttribute("inputmode", "decimal");
  });

  it("keeps the full keyboard when a value may be negative", () => {
    render(<Input aria-label="n" type="number" />);
    expect(screen.getByLabelText("n")).not.toHaveAttribute("inputmode");
  });

  it("respects an explicit inputMode and leaves text fields alone", () => {
    render(
      <>
        <Input aria-label="a" type="number" min={0} inputMode="decimal" />
        <Input aria-label="b" />
      </>,
    );
    expect(screen.getByLabelText("a")).toHaveAttribute("inputmode", "decimal");
    expect(screen.getByLabelText("b")).not.toHaveAttribute("inputmode");
  });
});
