/**
 * Plaid amount convention: positive = money leaving the account, negative = money in
 * (prod check 2026-11-19: every card-processor settlement is negative, every payroll wage
 * debit positive). Rules store `positive` / `negative`; the UI says Outflow / Inflow.
 */
export type AmountSignValue = "any" | "positive" | "negative";

export const AMOUNT_SIGN_OPTIONS: { value: AmountSignValue; label: string }[] = [
  { value: "any", label: "Any direction" },
  { value: "positive", label: "Outflow (money out)" },
  { value: "negative", label: "Inflow (money in)" },
];

export function amountSignLabel(sign: string | null | undefined): string {
  if (sign === "positive") return "Outflow";
  if (sign === "negative") return "Inflow";
  return "Any direction";
}

/** Default direction for a rule proposed from a transaction. */
export function amountSignFor(amount: number): AmountSignValue {
  return amount > 0 ? "positive" : amount < 0 ? "negative" : "any";
}
