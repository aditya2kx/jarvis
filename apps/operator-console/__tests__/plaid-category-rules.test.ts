import { describe, expect, it } from "vitest";
import {
  effectiveCategory,
  evaluateRules,
  type CategoryRule,
} from "@/lib/plaid/category-rules";

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
  it("prefers lower priority (ADP over Amazon in same string)", () => {
    const match = evaluateRules(
      { name: "ADP WAGE PAY Amazon", merchant_name: null, amount: 100 },
      [
        r({ id: "amazon", priority: 270, match_pattern: "Amazon", category_id: "inv" }),
        r({ id: "adp", priority: 100, match_pattern: "Adp Wage", category_id: "pay" }),
      ],
    );
    expect(match?.rule_id).toBe("adp");
  });

  it("ADP beats AK Juicy blob in IND NAME", () => {
    const match = evaluateRules(
      {
        name: "ORIG CO NAME:ADP WAGE PAY IND NAME:AK JUICY BOWLS LLC PAL",
        merchant_name: null,
        amount: 5000,
      },
      [
        r({
          id: "inv",
          priority: 200,
          match_pattern: "AK Juicy Bowls",
          category_id: "inv",
        }),
        r({
          id: "payroll_adp_wages",
          priority: 100,
          match_pattern: "Adp Wage Pay",
          category_id: "payroll_labor",
          amount_sign: "positive",
        }),
      ],
    );
    expect(match?.rule_id).toBe("payroll_adp_wages");
  });

  it("Amazon refund uses negative rule", () => {
    const match = evaluateRules(
      { name: "AMAZON", merchant_name: "Amazon", amount: -12 },
      [
        r({
          id: "refund_amazon",
          priority: 50,
          match_pattern: "Amazon",
          amount_sign: "negative",
          category_id: "contra",
        }),
        r({
          id: "inventory_amazon",
          priority: 270,
          match_pattern: "Amazon",
          amount_sign: "positive",
          category_id: "inv",
        }),
      ],
    );
    expect(match?.rule_id).toBe("refund_amazon");
  });

  it("override beats rule", () => {
    const match = evaluateRules(
      { name: "Amazon", merchant_name: null, amount: 10 },
      [r({ id: "a", priority: 1, match_pattern: "Amazon", category_id: "inv" })],
    );
    const eff = effectiveCategory(
      {
        name: "Amazon",
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
});
