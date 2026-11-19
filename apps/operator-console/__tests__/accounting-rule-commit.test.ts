import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const s = vi.hoisted(() => ({
  mutate: vi.fn(async (_sql: string, _p?: Record<string, unknown>, _t?: unknown) => {}),
  q: vi.fn(async (_sql: string, _p?: unknown): Promise<unknown[]> => [{ n: 0 }]),
  rules: [] as Record<string, unknown>[],
  busy: null as string | null,
}));
vi.mock("@/lib/bq/client", () => ({ mutate: s.mutate, q: s.q, intParam: (n: number) => n }));
vi.mock("next/cache", () => ({ revalidatePath: () => {} }));
vi.mock("@/lib/access/server", () => ({ actionDenied: async () => null }));
vi.mock("@/lib/auth/identity", () => ({ operatorEmail: async () => "cfo@example.com", DEFAULT_STORE: "palmetto" }));
vi.mock("@/lib/config/features", () => ({ FEATURES: { writePlaidLink: true } }));
vi.mock("@/lib/plaid/client", () => ({}));
vi.mock("@/lib/bq/writes", () => ({ dedupePlaidTransactions: async () => 0 }));
vi.mock("@/lib/bq/queries", () => ({}));
vi.mock("@/lib/plaid/categorize-jobs", () => ({
  busyMessage: async () => s.busy,
  runCategorizeJob: vi.fn(),
}));
vi.mock("@/lib/plaid/reapply-categories", async (orig) => ({
  ...(await orig<typeof import("@/lib/plaid/reapply-categories")>()),
  loadRules: async () => s.rules,
}));

import { commitRuleFromTxnAction } from "@/app/accounting/actions";
import { commitRuleScript, duplicateRuleIds, findIdenticalRule } from "@/lib/plaid/rule-commit";

/** Synthetic pattern / masks; shape of the three duplicate rules found in prod. */
const WAGE = {
  match_pattern: "PAYCO WAGE",
  match_operator: "regex",
  amount_sign: "positive",
  account_mask: null,
  from_mask: "1111",
  to_mask: null,
  category_id: "payroll",
  subcategory_id: "payroll__wages",
};
const dupes = [
  { ...WAGE, id: "op_payco_wage_a", priority: 880, enabled: true },
  { ...WAGE, id: "op_payco_wage_b", priority: 890, enabled: true },
  { ...WAGE, id: "op_payco_wage_c", priority: 900, enabled: true },
];
const draft = {
  match_pattern: "PAYCO WAGE",
  match_operator: "regex",
  amount_sign: "positive",
  from_mask: "1111",
  category_id: "payroll",
  subcategory_id: "payroll__wages",
};
const conflict = new Error("Could not serialize access to table x:bhaga.plaid_transactions due to concurrent update");

describe("rule identity", () => {
  it("flags later identical rules as duplicates of the first by priority", () => {
    expect([...duplicateRuleIds(dupes).entries()]).toEqual([
      ["op_payco_wage_b", "op_payco_wage_a"],
      ["op_payco_wage_c", "op_payco_wage_a"],
    ]);
  });

  it("a different target or direction is a different rule", () => {
    expect(findIdenticalRule(dupes, { ...WAGE, subcategory_id: null })).toBeNull();
    expect(findIdenticalRule(dupes, { ...WAGE, amount_sign: "negative" })).toBeNull();
    expect(findIdenticalRule(dupes, { ...WAGE, from_mask: "x1111" })?.id).toBe("op_payco_wage_a");
  });

  it("disabled rules are not reused", () => {
    expect(findIdenticalRule(dupes.map((r) => ({ ...r, enabled: false })), WAGE)).toBeNull();
  });

  it("commit script is one transaction that rolls back on any error", () => {
    const sql = commitRuleScript({ rules: "`r`", events: "`e`", txns: "`t`" });
    const begin = sql.indexOf("BEGIN TRANSACTION");
    expect(begin).toBeGreaterThan(-1);
    expect(sql.indexOf("INSERT INTO `r`")).toBeGreaterThan(begin);
    expect(sql.indexOf("INSERT INTO `e`")).toBeGreaterThan(begin);
    expect(sql.indexOf("UPDATE `t`")).toBeGreaterThan(sql.indexOf("INSERT INTO `r`"));
    expect(sql.indexOf("COMMIT TRANSACTION")).toBeGreaterThan(sql.indexOf("UPDATE `t`"));
    expect(sql).toMatch(/EXCEPTION WHEN ERROR THEN\s+ROLLBACK TRANSACTION;\s+RAISE/);
  });
});

describe("commitRuleFromTxnAction", () => {
  beforeEach(() => {
    s.mutate.mockReset();
    s.q.mockReset();
    s.q.mockResolvedValue([{ n: 0 }]);
    s.rules = [];
    s.busy = null;
  });

  it("refuses while a Reapply is running and writes nothing", async () => {
    s.busy = "Reapplying rules — 400 of 1,012 rows written. Committing a rule is paused…";
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: ["t1"], applyFuture: true });
    expect(ack.ok).toBe(false);
    expect(s.mutate).not.toHaveBeenCalled();
  });

  it("rule + backfill go out as one statement; a conflict retries it whole", async () => {
    s.mutate.mockRejectedValueOnce(conflict);
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: ["t1", "t2", "t1"], applyFuture: true });
    expect(ack.ok).toBe(true);
    // Both attempts are the full transaction script — no separate rule INSERT that could survive a failure.
    expect(s.mutate).toHaveBeenCalledTimes(2);
    for (const [sql, params] of s.mutate.mock.calls) {
      expect(sql).toContain("BEGIN TRANSACTION");
      expect(params).toMatchObject({ create: true, ids: ["t1", "t2"], enabled: true });
    }
    if (ack.ok) expect(ack.data).toMatchObject({ applied: 2, reused: false });
  }, 15_000);

  it("a failed commit surfaces the error and inserts nothing outside the transaction", async () => {
    s.mutate.mockRejectedValue(new Error("Syntax error"));
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: ["t1"], applyFuture: true });
    expect(ack.ok).toBe(false);
    expect(s.mutate).toHaveBeenCalledTimes(1);
    expect(s.mutate.mock.calls[0][0]).toContain("ROLLBACK TRANSACTION");
  });

  it("reuses an identical enabled rule instead of inserting a duplicate", async () => {
    s.rules = [dupes[0]];
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: ["t1"], applyFuture: true });
    expect(ack.ok).toBe(true);
    expect(s.mutate.mock.calls[0][1]).toMatchObject({ create: false, id: "op_payco_wage_a" });
    if (ack.ok) expect(ack.data?.reused).toBe(true);
  });

  it("Apply to future allows a rule with zero backfill rows", async () => {
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: [], applyFuture: true });
    expect(ack.ok).toBe(true);
    expect(s.mutate.mock.calls[0][1]).toMatchObject({ create: true, ids: [] });
    if (ack.ok) expect(ack.message).toMatch(/future transactions/);
  });

  it("warns when a higher-priority rule will take the rows back on Reapply", async () => {
    s.rules = [{ ...WAGE, id: "corpus_wages", priority: 100, enabled: true, match_operator: "contains", from_mask: null, match_field: "name_or_merchant" }];
    s.q.mockImplementation(async (sql: string) =>
      sql.includes("COUNT(*)")
        ? [{ n: 0 }]
        : [{ transaction_id: "t1", date: "2026-09-10", name: "PAYCO WAGE PAY", merchant_name: null, amount: 100, account_mask: "1111", counterparty_name: null, category_id: null, subcategory_id: null, rule_id: null, override_category_id: null, is_internal: false }],
    );
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: ["t1"], applyFuture: true });
    expect(ack.ok).toBe(true);
    if (ack.ok) {
      expect(ack.data?.shadowed).toEqual({ corpus_wages: 1 });
      expect(ack.message).toMatch(/higher-priority corpus_wages/);
    }
  });

  it("zero rows and Apply to future off is refused (nothing to do)", async () => {
    const ack = await commitRuleFromTxnAction({ draft, selectedTxnIds: [], applyFuture: false });
    expect(ack.ok).toBe(false);
    expect(s.mutate).not.toHaveBeenCalled();
  });
});
