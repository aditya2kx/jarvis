// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const a = vi.hoisted(() => ({
  commit: vi.fn(),
  preview: vi.fn(),
  override: vi.fn(),
  reapply: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
  unstable_isUnrecognizedActionError: () => false,
}));
vi.mock("@/app/accounting/actions", () => ({
  reapplyPlaidCategoriesAction: a.reapply,
  setTxnCategoryOverrideAction: a.override,
  previewRuleMatchesAction: a.preview,
  commitRuleFromTxnAction: a.commit,
  revertRuleEvidenceAction: vi.fn(),
  setTaxonomyExcludeAction: vi.fn(),
  upsertTaxonomyNodeAction: vi.fn(),
  dryRunRuleAction: vi.fn(),
  setCategoryRuleEnabledAction: vi.fn(),
  setTaxonomyNodeEnabledAction: vi.fn(),
}));
vi.mock("@/components/charts/BarChartCard", () => ({ BarChartCard: () => null }));

import { AccountingLedger, type AccountingTxnRow } from "@/components/accounting/AccountingLedger";
import { ActionToastProvider } from "@/lib/actions/ActionToast";

afterEach(cleanup);

const TAX = [
  { id: "ops", parent_id: null, label: "Store ops", exclude_from_accounting: null },
  { id: "ops__freight", parent_id: "ops", label: "Freight", exclude_from_accounting: null },
  { id: "ops__insurance", parent_id: "ops", label: "Insurance", exclude_from_accounting: null },
];
const row = (id: string, name: string, cat: string | null, override = false): AccountingTxnRow => ({
  transaction_id: id,
  date: "2026-11-13",
  transaction_name: name,
  bank_description: name,
  counterparty: null,
  account_last4: "Bank •••• 1111",
  from_account: "Bank •••• 1111",
  to_account: name,
  from_mask: "1111",
  to_mask: null,
  spend: 100,
  earned: null,
  category: cat ? "Store ops" : "Uncategorized",
  category_detail: "—",
  channel: "online",
  pending_label: "no",
  amount: 100,
  excluded: false,
  excluded_label: "no",
  is_internal: false,
  internal_label: "no",
  category_id: cat,
  subcategory_id: null,
  rule_id: null,
  is_override: override,
  category_definition: null,
  rule_summary: null,
});

function ledger(rows: AccountingTxnRow[]) {
  return (
    <ActionToastProvider>
      <AccountingLedger
        periodLabel="t"
        grain="day"
        rows={rows}
        canWrite
        taxonomy={TAX}
        rules={[]}
        squareNetSalesTotal={null}
        squareNetSalesByIso={{}}
        latestJob={null}
      />
    </ActionToastProvider>
  );
}

describe("AccountingLedger propose-rule commit", () => {
  it("zero-backfill commit succeeds and the ledger survives the revalidated props", async () => {
    a.preview.mockResolvedValue({ ok: true, data: [{ transaction_id: "t1", date: "2026-11-13", name: "INSURECO", amount: 100, has_override: true }] });
    a.commit.mockResolvedValue({
      ok: true,
      message: "Rule committed — it will apply to future transactions.",
      data: { ruleId: "op_insureco_x", applied: 0, skipped_override: 0, reused: false, shadowed: {} },
    });
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...e) => void errors.push(e));
    const view = render(ledger([row("t1", "INSURECO", "ops", true)]));
    fireEvent.click(screen.getAllByRole("button", { name: "INSURECO" })[0]);
    const sheet = await screen.findByRole("dialog");
    fireEvent.change(within(sheet).getByPlaceholderText("Name regex (optional)"), { target: { value: "INSURECO" } });
    await act(async () => fireEvent.click(within(sheet).getByRole("button", { name: "Find matches" })));
    expect(within(sheet).getByText(/1 match\(es\) · 0 selected/)).toBeInTheDocument();
    const commit = within(sheet).getByRole("button", { name: "Commit rule (future transactions only)" });
    expect(commit).toBeEnabled();
    await act(async () => fireEvent.click(commit));
    expect(a.commit).toHaveBeenCalledWith(expect.objectContaining({ selectedTxnIds: [], applyFuture: true }));
    // Server revalidation hands the ledger a fresh rows array.
    await act(async () => view.rerender(ledger([row("t1", "INSURECO", "ops", true)])));
    expect(within(screen.getByRole("dialog")).getByText(/Rule op_insureco_x: Rule committed/)).toBeInTheDocument();
    spy.mockRestore();
    expect(errors.filter((e) => String(e).includes("Maximum update depth"))).toEqual([]);
  });
});
