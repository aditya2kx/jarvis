// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import { EmployeeCombobox } from "@/components/filters/EmployeeCombobox";

describe("LocalMultiSelect", () => {
  const options = ["A", "B", "C", "D", "E", "F", "G"]; // >6 → search shown

  it("all → uncheck one → emits all-but-one", () => {
    const onChange = vi.fn();
    render(
      <LocalMultiSelect label="Date" selected={null} options={options} onChange={onChange} />,
    );
    fireEvent.click(screen.getByLabelText("Filter Date"));
    const list = screen.getByRole("listbox");
    fireEvent.click(within(list).getByRole("option", { name: /^A$/i }));
    expect(onChange).toHaveBeenCalledWith(["B", "C", "D", "E", "F", "G"]);
  });
});

describe("EmployeeCombobox", () => {
  it("type → filter → select emits value", () => {
    const onChange = vi.fn();
    render(
      <EmployeeCombobox
        value=""
        options={["Lee, Sam", "Garcia, Jacob", "Guerrero, Amy"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Employee"));
    fireEvent.change(screen.getByLabelText("Search employees"), {
      target: { value: "guerr" },
    });
    fireEvent.click(screen.getByRole("option", { name: /Guerrero, Amy/i }));
    expect(onChange).toHaveBeenCalledWith("Guerrero, Amy");
  });
});
