// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { TxnCategoryPicker } from "@/components/accounting/TxnCategoryPicker";
import { CategorizeJobStatus } from "@/components/accounting/CategorizeJobStatus";
import { AMOUNT_SIGN_OPTIONS, amountSignFor, amountSignLabel } from "@/lib/plaid/amount-sign";
import {
  isJobLive,
  isJobStalled,
  jobPercent,
  jobProgressLabel,
  parseBqTimestamp,
  type CategorizeJob,
} from "@/lib/plaid/categorize-job-status";

afterEach(cleanup);

// Same ordering as prod taxonomy: the first child is what the old picker guessed.
const TAX = [
  { id: "logistics", parent_id: null, label: "Logistics / facilities / store ops", exclude_from_accounting: null },
  { id: "logistics__frozen_freight", parent_id: "logistics", label: "Frozen freight", exclude_from_accounting: null },
  { id: "logistics__parking", parent_id: "logistics", label: "Parking / tolls", exclude_from_accounting: null },
  { id: "payroll", parent_id: null, label: "Payroll / labor", exclude_from_accounting: null },
  { id: "payroll__wages", parent_id: "payroll", label: "Wage pay", exclude_from_accounting: null },
];

describe("TxnCategoryPicker", () => {
  it("picking a category assigns no subcategory (no first-child guess)", () => {
    const onChange = vi.fn();
    render(<TxnCategoryPicker taxonomy={TAX} categoryId={null} subcategoryId={null} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "logistics" } });
    expect(onChange).toHaveBeenCalledWith("logistics", null);
  });

  it("switching category drops the old subcategory", () => {
    const onChange = vi.fn();
    render(
      <TxnCategoryPicker taxonomy={TAX} categoryId="logistics" subcategoryId="logistics__parking" onChange={onChange} />,
    );
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "payroll" } });
    expect(onChange).toHaveBeenCalledWith("payroll", null);
  });

  it("subcategory picker lists only this category's children plus 'None', and overrides", () => {
    const onChange = vi.fn();
    render(<TxnCategoryPicker taxonomy={TAX} categoryId="logistics" subcategoryId={null} onChange={onChange} />);
    const sub = screen.getByLabelText("Subcategory");
    const labels = within(sub).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual(["None — category only", "Frozen freight", "Parking / tolls"]);
    expect(sub).toHaveValue("");
    fireEvent.change(sub, { target: { value: "logistics__parking" } });
    expect(onChange).toHaveBeenCalledWith("logistics", "logistics__parking");
  });
});

describe("amount sign labels", () => {
  it("positive is Outflow (money out), negative is Inflow (money in)", () => {
    expect(AMOUNT_SIGN_OPTIONS.map((o) => [o.value, o.label])).toEqual([
      ["any", "Any direction"],
      ["positive", "Outflow (money out)"],
      ["negative", "Inflow (money in)"],
    ]);
    expect(amountSignLabel("positive")).toBe("Outflow");
    expect(amountSignLabel("negative")).toBe("Inflow");
    expect(amountSignFor(1234.56)).toBe("positive");
    expect(amountSignFor(-812.4)).toBe("negative");
  });
});

const NOW = Date.parse("2026-11-19T12:00:00Z");
const job = (p: Partial<CategorizeJob>): CategorizeJob => ({
  job_id: "j1",
  kind: "reapply",
  status: "running",
  rule_id: null,
  total: 1012,
  done: 400,
  updated: null,
  unchanged: 1300,
  skipped_override: 64,
  message: null,
  started_by: "cfo@example.com",
  started_at: "2026-11-19 11:59:30+00",
  heartbeat_at: "2026-11-19 11:59:55+00",
  finished_at: null,
  ...p,
});

describe("categorize job status", () => {
  it("live job shows a completion count and percent", () => {
    const j = job({});
    expect(isJobLive(j, NOW)).toBe(true);
    expect(jobProgressLabel(j, NOW)).toBe("Reapplying rules — 400 of 1,012 rows written");
    expect(jobPercent(j)).toBe(40);
  });

  it("before planning finishes there is no total yet", () => {
    expect(jobProgressLabel(job({ total: null, done: null }), NOW)).toMatch(/checking every transaction/);
    expect(jobPercent(job({ total: null }))).toBeNull();
  });

  it("a job whose heartbeat stopped is reported as stopped, not running forever", () => {
    const j = job({ heartbeat_at: "2026-11-19 11:40:00+00" });
    expect(isJobLive(j, NOW)).toBe(false);
    expect(isJobStalled(j, NOW)).toBe(true);
    expect(jobProgressLabel(j, NOW)).toMatch(/stopped at 400 of 1,012/);
  });

  it("done job reports the final counts", () => {
    const j = job({ status: "done", done: 1012, updated: 1012, finished_at: "2026-11-19 12:00:20+00" });
    expect(jobProgressLabel(j, NOW)).toBe("Rules reapplied — 1,012 changed, 1,300 unchanged (64 overrides kept)");
    expect(jobPercent(j)).toBe(100);
  });

  it("renders badge + progress bar while running", () => {
    render(<CategorizeJobStatus job={job({ heartbeat_at: new Date().toISOString() })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Running");
    expect(screen.getByRole("status")).toHaveTextContent("400 of 1,012 rows written");
  });
});

describe("parseBqTimestamp", () => {
  it("parses BigQuery TIMESTAMP strings with micros and a +00 offset", () => {
    expect(parseBqTimestamp("2026-11-18 22:22:22.388396+00")).toBe(Date.parse("2026-11-18T22:22:22.388Z"));
    expect(parseBqTimestamp("2026-11-19T12:00:00Z")).toBe(NOW);
    expect(Number.isNaN(parseBqTimestamp(null))).toBe(true);
  });
});
