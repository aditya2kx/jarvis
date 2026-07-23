import { describe, expect, it } from "vitest";
import { suggestInternal, type LinkedAccountHint, type TxnInternalHint } from "@/lib/plaid/internal";
import { pfcPrimaryDefinition, pfcDetailedHint } from "@/lib/plaid/pfc-definitions";

const linked: LinkedAccountHint[] = [
  { account_id: "chk", mask: "8933", type: "depository" },
  { account_id: "card", mask: "6029", type: "credit" },
];

function txn(partial: Partial<TxnInternalHint> & Pick<TxnInternalHint, "transaction_id">): TxnInternalHint {
  return {
    account_id: "chk",
    name: null,
    merchant_name: null,
    amount: 0,
    date: "2026-07-02",
    pfc_primary: null,
    pfc_detailed: null,
    ...partial,
  };
}

describe("suggestInternal", () => {
  it("flags payment to own card by mask", () => {
    const t = txn({
      transaction_id: "a",
      name: "Payment to Chase card ending in 6029 07/02",
      amount: 36710.71,
      pfc_detailed: "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    });
    expect(suggestInternal(t, linked, [t])).toBe(true);
  });

  it("flags Payment Thank You on credit (inflow)", () => {
    const t = txn({
      transaction_id: "b",
      account_id: "card",
      name: "Payment Thank You-Mobile",
      amount: -36710.71,
    });
    expect(suggestInternal(t, linked, [t])).toBe(true);
  });

  it("flags credit-card payment when opposite leg exists", () => {
    const out = txn({
      transaction_id: "out",
      name: "CC PAY",
      amount: 100,
      pfc_detailed: "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
      pfc_primary: "LOAN_PAYMENTS",
    });
    const inn = txn({
      transaction_id: "in",
      account_id: "card",
      name: "Thank You",
      amount: -100,
      date: "2026-07-02",
    });
    expect(suggestInternal(out, linked, [out, inn])).toBe(true);
  });

  it("does not flag ordinary vendor spend", () => {
    const t = txn({
      transaction_id: "v",
      name: "COSTCO WHOLESALE",
      amount: 220.5,
      pfc_primary: "GENERAL_MERCHANDISE",
    });
    expect(suggestInternal(t, linked, [t])).toBe(false);
  });
});

describe("pfc definitions", () => {
  it("returns known primary", () => {
    expect(pfcPrimaryDefinition("LOAN_PAYMENTS").title).toBe("Loan payments");
  });

  it("returns detailed hint for card payment", () => {
    expect(pfcDetailedHint("LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")).toMatch(/credit-card/i);
  });
});
