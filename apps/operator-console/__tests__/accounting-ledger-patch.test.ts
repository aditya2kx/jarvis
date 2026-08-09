import { describe, expect, it } from "vitest";
import { patchTxnCategory } from "@/lib/plaid/patch-txn-category";
import { computeMetaPinOffsets } from "@/lib/tables/pin-offsets";

function baseRow() {
  return {
    transaction_id: "txn_1",
    category_id: null as string | null,
    subcategory_id: null as string | null,
    category: "Uncategorized",
    category_detail: "—",
    is_override: false,
    rule_id: null as string | null,
    rule_summary: null as string | null,
    is_internal: false,
    internal_label: "no",
    excluded: false,
    excluded_label: "no",
  };
}

describe("patchTxnCategory", () => {
  it("marks internal_transfers as excluded + is_internal", () => {
    const next = patchTxnCategory(baseRow(), {
      categoryId: "internal_transfers",
      subcategoryId: null,
      categoryLabel: "Internal transfers",
      subcategoryLabel: "—",
      excluded: true,
      isOverride: true,
    });
    expect(next.is_internal).toBe(true);
    expect(next.internal_label).toBe("yes");
    expect(next.excluded).toBe(true);
    expect(next.excluded_label).toBe("yes");
    expect(next.is_override).toBe(true);
    expect(next.rule_id).toBeNull();
  });

  it("forces excluded when taxonomy exclude is false but category is internal", () => {
    const next = patchTxnCategory(baseRow(), {
      categoryId: "internal_transfers",
      subcategoryId: null,
      categoryLabel: "Internal transfers",
      subcategoryLabel: "—",
      excluded: false,
      isOverride: true,
    });
    expect(next.excluded).toBe(true);
  });

  it("clears override without inventing internal", () => {
    const next = patchTxnCategory(
      {
        ...baseRow(),
        category_id: "opex",
        is_override: true,
        rule_id: null,
      },
      {
        categoryId: null,
        subcategoryId: null,
        categoryLabel: "Uncategorized",
        subcategoryLabel: "—",
        excluded: false,
        isOverride: false,
        ruleId: "r1",
        ruleSummary: "rule",
      },
    );
    expect(next.is_override).toBe(false);
    expect(next.category_id).toBeNull();
    expect(next.rule_id).toBe("r1");
    expect(next.is_internal).toBe(false);
  });

  it("personal override excludes without setting is_internal", () => {
    const next = patchTxnCategory(baseRow(), {
      categoryId: "personal",
      subcategoryId: null,
      categoryLabel: "Personal",
      subcategoryLabel: "—",
      excluded: true,
      isOverride: true,
    });
    expect(next.excluded).toBe(true);
    expect(next.is_internal).toBe(false);
  });
});

describe("Accounting pinLeft + column widths", () => {
  it("every pinned column has meta.width so DataTable never DOM-measures", () => {
    const pinLeft = ["date", "from_account", "to_account", "spend", "earned"];
    const cols = [
      { accessorKey: "date", meta: { width: 88 } },
      { accessorKey: "from_account", meta: { width: 140 } },
      { accessorKey: "to_account", meta: { width: 140 } },
      { accessorKey: "spend", meta: { width: 100 } },
      { accessorKey: "earned", meta: { width: 100 } },
    ];
    expect(computeMetaPinOffsets(cols, pinLeft)).not.toBeNull();
  });
});
