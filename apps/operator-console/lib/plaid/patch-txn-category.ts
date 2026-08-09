/** Minimal txn shape for optimistic category patches (keep free of RSC/client imports). */
export type TxnCategoryPatchable = {
  category_id: string | null;
  subcategory_id: string | null;
  category: string;
  category_detail: string;
  is_override: boolean;
  rule_id: string | null;
  rule_summary: string | null;
  is_internal: boolean;
  internal_label: string;
  excluded: boolean;
  excluded_label: string;
};

/** Optimistic ledger patch after category override / rule apply. */
export function patchTxnCategory<T extends TxnCategoryPatchable>(
  row: T,
  input: {
    categoryId: string | null;
    subcategoryId: string | null;
    categoryLabel: string;
    subcategoryLabel: string;
    excluded: boolean;
    ruleId?: string | null;
    ruleSummary?: string | null;
    isOverride: boolean;
  },
): T {
  const internal = input.categoryId === "internal_transfers";
  return {
    ...row,
    category_id: input.categoryId,
    subcategory_id: input.subcategoryId,
    category: input.categoryLabel,
    category_detail: input.subcategoryLabel,
    is_override: input.isOverride,
    rule_id: input.isOverride ? null : (input.ruleId ?? row.rule_id),
    rule_summary: input.isOverride ? null : (input.ruleSummary ?? row.rule_summary),
    is_internal: internal ? true : row.is_internal,
    internal_label: internal ? "yes" : row.internal_label,
    excluded: input.excluded || internal,
    excluded_label: input.excluded || internal ? "yes" : "no",
  };
}
