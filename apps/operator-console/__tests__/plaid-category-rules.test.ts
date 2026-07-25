import { describe, expect, it } from "vitest";
import {
  effectiveCategory,
  evaluateRules,
  type CategoryRule,
} from "@/lib/plaid/category-rules";

/** Synthetic merchants only — never real store / brand / personal names. */
function r(partial: Partial<CategoryRule> & Pick<CategoryRule, "id" | "match_pattern">): CategoryRule {
  return {
    priority: 100,
    match_field: "name_or_merchant",
    match_operator: "contains",
    amount_sign: "any",
    category_id: "cat",
    subcategory_id: "sub",
    enabled: true,
    ...partial,
  };
}

describe("evaluateRules", () => {
  it("prefers lower priority (payroll over marketplace in same string)", () => {
    const match = evaluateRules(
      { name: "PAYCO WAGE PAY MarketCo", merchant_name: null, amount: 100 },
      [
        r({ id: "market", priority: 270, match_pattern: "MarketCo", category_id: "inv" }),
        r({ id: "payroll", priority: 100, match_pattern: "PayCo Wage", category_id: "pay" }),
      ],
    );
    expect(match?.rule_id).toBe("payroll");
  });

  it("payroll beats entity blob in IND NAME", () => {
    const match = evaluateRules(
      {
        name: "ORIG CO NAME:PAYCO WAGE PAY IND NAME:ACME ENTITY LLC PAL",
        merchant_name: null,
        amount: 5000,
      },
      [
        r({
          id: "inv",
          priority: 200,
          match_pattern: "Acme Entity Llc",
          category_id: "inv",
        }),
        r({
          id: "payroll_wages",
          priority: 100,
          match_pattern: "Payco Wage Pay",
          category_id: "payroll_labor",
          amount_sign: "positive",
        }),
      ],
    );
    expect(match?.rule_id).toBe("payroll_wages");
  });

  it("refund uses negative amount rule", () => {
    const match = evaluateRules(
      { name: "MARKETCO", merchant_name: "MarketCo", amount: -12 },
      [
        r({
          id: "refund_market",
          priority: 50,
          match_pattern: "MarketCo",
          amount_sign: "negative",
          category_id: "contra",
        }),
        r({
          id: "inventory_market",
          priority: 270,
          match_pattern: "MarketCo",
          amount_sign: "positive",
          category_id: "inv",
        }),
      ],
    );
    expect(match?.rule_id).toBe("refund_market");
  });

  it("override beats rule", () => {
    const match = evaluateRules(
      { name: "MarketCo", merchant_name: null, amount: 10 },
      [r({ id: "a", priority: 1, match_pattern: "MarketCo", category_id: "inv" })],
    );
    const eff = effectiveCategory(
      {
        name: "MarketCo",
        merchant_name: null,
        amount: 10,
        override_category_id: "opex",
        override_subcategory_id: "supplies",
      },
      match,
    );
    expect(eff.source).toBe("override");
    expect(eff.category_id).toBe("opex");
  });

  it("account_mask constrains match", () => {
    const rules = [
      r({
        id: "card_only",
        priority: 10,
        match_pattern: "TollCo",
        category_id: "personal",
        account_mask: "1234",
      }),
    ];
    expect(
      evaluateRules(
        { name: "TollCo", merchant_name: null, amount: 5, account_mask: "9999" },
        rules,
      ),
    ).toBeNull();
    expect(
      evaluateRules(
        { name: "TollCo", merchant_name: null, amount: 5, account_mask: "1234" },
        rules,
      )?.rule_id,
    ).toBe("card_only");
  });
});
