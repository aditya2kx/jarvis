"use client";

import type { TaxonomyOption } from "@/components/accounting/AccountingLedger";

/**
 * Category + subcategory override for one transaction. Picking a category never guesses
 * a subcategory: the old picker auto-assigned the parent's first child (by sort order),
 * which is how team meals became "ADP wage pay" and parking became "Frozen freight".
 */
export function TxnCategoryPicker({
  taxonomy,
  categoryId,
  subcategoryId,
  disabled,
  onChange,
}: {
  taxonomy: TaxonomyOption[];
  categoryId: string | null;
  subcategoryId: string | null;
  disabled?: boolean;
  onChange: (categoryId: string | null, subcategoryId: string | null) => void;
}) {
  const parents = taxonomy.filter((t) => !t.parent_id);
  const children = categoryId ? taxonomy.filter((t) => t.parent_id === categoryId) : [];
  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
        Category
        <select
          aria-label="Category"
          className="rounded border bg-background px-2 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={categoryId || ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value || null, null)}
        >
          <option value="">(clear override / use rules)</option>
          {parents.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.exclude_from_accounting === true ? " (excluded)" : ""}
            </option>
          ))}
        </select>
      </label>
      {categoryId ? (
        <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
          Subcategory
          <select
            aria-label="Subcategory"
            className="rounded border bg-background px-2 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={subcategoryId || ""}
            disabled={disabled}
            onChange={(e) => onChange(categoryId, e.target.value || null)}
          >
            <option value="">None — category only</option>
            {children.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}
