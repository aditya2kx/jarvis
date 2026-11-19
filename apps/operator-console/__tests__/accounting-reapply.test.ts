import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const bq = vi.hoisted(() => ({
  mutate: vi.fn(async (_sql: string, _p?: unknown, _t?: unknown) => {}),
  q: vi.fn(async (_sql: string, _p?: unknown): Promise<unknown[]> => []),
}));
vi.mock("@/lib/bq/client", () => ({ mutate: bq.mutate, q: bq.q, intParam: (n: number) => n }));

import {
  applyCategoryUpdates,
  planCategoryUpdates,
  type CategorizeTxnRow,
  type CategoryUpdate,
} from "@/lib/plaid/reapply-categories";
import type { CategoryRule } from "@/lib/plaid/category-rules";
import { isSerializationConflict, withSerializeRetry } from "@/lib/plaid/bq-retry";

/** Synthetic merchants / masks only. */
const rule = (p: Partial<CategoryRule> & Pick<CategoryRule, "id" | "match_pattern">): CategoryRule => ({
  priority: 100,
  match_field: "name_or_merchant",
  match_operator: "contains",
  amount_sign: "any",
  category_id: "cat",
  subcategory_id: null,
  enabled: true,
  ...p,
});
const txn = (id: string, name: string, amount: number, extra: Partial<CategorizeTxnRow> = {}): CategorizeTxnRow => ({
  transaction_id: id,
  date: "2026-09-01",
  name,
  merchant_name: null,
  amount,
  account_mask: "1111",
  counterparty_name: null,
  category_id: null,
  subcategory_id: null,
  rule_id: null,
  override_category_id: null,
  is_internal: false,
  ...extra,
});

const RULES = [
  rule({ id: "deposit", priority: 10, match_pattern: "SQUARECO", amount_sign: "negative", category_id: "revenue", subcategory_id: "revenue__card" }),
  rule({ id: "wages", priority: 100, match_pattern: "PAYCO WAGE", amount_sign: "positive", category_id: "payroll", subcategory_id: "payroll__wages" }),
];

describe("planCategoryUpdates (set-based Reapply)", () => {
  it("categorizes never-categorized rows an existing rule matches", () => {
    const plan = planCategoryUpdates(
      [
        txn("a", "ORIG CO NAME:SQUARECO SETTLE", -812.4),
        txn("b", "ORIG CO NAME:PAYCO WAGE PAY", 7704.81),
        txn("c", "SOME CAFE", 12.29),
      ],
      RULES,
    );
    expect(plan.updates.map((u) => [u.transaction_id, u.rule_id, u.subcategory_id])).toEqual([
      ["a", "deposit", "revenue__card"],
      ["b", "wages", "payroll__wages"],
    ]);
    expect(plan.unchanged).toBe(1);
  });

  it("direction matters: an inflow never matches an Outflow (positive) rule", () => {
    const plan = planCategoryUpdates([txn("r", "PAYCO WAGE REVERSAL", -50)], RULES);
    expect(plan.updates).toEqual([]);
  });

  it("never touches an override and skips rows already correct", () => {
    const plan = planCategoryUpdates(
      [
        txn("o", "PAYCO WAGE PAY", 10, { override_category_id: "personal" }),
        txn("k", "PAYCO WAGE PAY", 10, { category_id: "payroll", subcategory_id: "payroll__wages", rule_id: "wages" }),
      ],
      RULES,
    );
    expect(plan.updates).toEqual([]);
    expect(plan.skipped_override).toBe(1);
    expect(plan.unchanged).toBe(2);
  });

  it("clears a stale category when no rule matches any more (revert path)", () => {
    const plan = planCategoryUpdates([txn("s", "OLD VENDOR", 5, { category_id: "x", rule_id: "gone" })], RULES);
    expect(plan.updates).toEqual([
      { transaction_id: "s", category_id: null, subcategory_id: null, rule_id: null, is_internal: false },
    ]);
  });
});

describe("applyCategoryUpdates", () => {
  beforeEach(() => bq.mutate.mockReset());

  const updates = (n: number): CategoryUpdate[] =>
    Array.from({ length: n }, (_, i) => ({
      transaction_id: `t${i}`,
      category_id: "cat",
      subcategory_id: null,
      rule_id: "r",
      is_internal: false,
    }));

  it("writes chunks with one MERGE each and reports progress after each", async () => {
    const seen: number[] = [];
    await applyCategoryUpdates(updates(1012), async (d) => void seen.push(d), 400);
    expect(bq.mutate).toHaveBeenCalledTimes(3);
    expect(seen).toEqual([400, 800, 1012]);
    const [sql, params, types] = bq.mutate.mock.calls[0];
    expect(sql).toMatch(/MERGE .*plaid_transactions/s);
    expect(sql).toContain("T.override_category_id IS NULL");
    expect((params as { rows: unknown[] }).rows).toHaveLength(400);
    expect(types).toEqual({ rows: [expect.objectContaining({ subcategory_id: "STRING", is_internal: "BOOL" })] });
  });

  it("no writes when nothing changed", async () => {
    await applyCategoryUpdates([], undefined);
    expect(bq.mutate).not.toHaveBeenCalled();
  });
});

describe("withSerializeRetry", () => {
  const conflict = new Error(
    "Could not serialize access to table jarvis-bhaga-prod:bhaga.plaid_transactions due to concurrent update",
  );

  it("recognizes the BigQuery concurrent-update error", () => {
    expect(isSerializationConflict(conflict)).toBe(true);
    expect(isSerializationConflict(new Error("Syntax error"))).toBe(false);
  });

  it("retries a conflict, then succeeds", async () => {
    const sleep = vi.fn(async () => {});
    let n = 0;
    const out = await withSerializeRetry(
      async () => {
        n += 1;
        if (n < 3) throw conflict;
        return "ok";
      },
      { label: "t", sleep },
    );
    expect(out).toBe("ok");
    expect(sleep).toHaveBeenCalledTimes(2);
  });

  it("does not retry other errors and gives up after the last delay", async () => {
    const sleep = vi.fn(async () => {});
    await expect(withSerializeRetry(async () => { throw new Error("bad"); }, { label: "t", sleep })).rejects.toThrow("bad");
    expect(sleep).not.toHaveBeenCalled();
    await expect(withSerializeRetry(async () => { throw conflict; }, { label: "t", sleep, delaysMs: [1, 1] })).rejects.toThrow(/serialize/);
    expect(sleep).toHaveBeenCalledTimes(2);
  });
});
