/**
 * Effective exclude-from-accounting for taxonomy nodes (Issue #189).
 * NULL on a node inherits parent; missing parent → false (include).
 */

export interface TaxonomyExcludeNode {
  id: string;
  parent_id: string | null;
  exclude_from_accounting: boolean | null;
}

/** Resolve whether a leaf node is excluded from business accounting rollups. */
export function effectiveExclude(
  leaf: TaxonomyExcludeNode | null | undefined,
  parent: TaxonomyExcludeNode | null | undefined,
): boolean {
  if (!leaf) return false;
  if (leaf.exclude_from_accounting === true) return true;
  if (leaf.exclude_from_accounting === false) return false;
  // NULL → inherit parent
  if (parent && leaf.parent_id) {
    if (parent.exclude_from_accounting === true) return true;
    if (parent.exclude_from_accounting === false) return false;
  }
  return false;
}

/** Look up leaf + parent from a flat taxonomy list and resolve exclude. */
export function effectiveExcludeFromMap(
  leafId: string | null | undefined,
  nodes: TaxonomyExcludeNode[],
): boolean {
  if (!leafId) return false;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const leaf = byId.get(leafId);
  if (!leaf) return false;
  const parent = leaf.parent_id ? byId.get(leaf.parent_id) : undefined;
  return effectiveExclude(leaf, parent);
}

export const INTERNAL_TRANSFERS_CATEGORY_ID = "internal_transfers";
export const PAYROLL_LABOR_CATEGORY_ID = "payroll_labor";

/**
 * Whether a ledger row should drop out of Accounting cash-flow / spend charts.
 * Internal is authoritative even when a category rule later stamped a non-internal
 * leaf (e.g. ACH TRANSFER matched a merchant inventory rule).
 */
export function excludedFromAccountingRollup(opts: {
  leafId: string | null | undefined;
  nodes: TaxonomyExcludeNode[];
  isInternal: boolean;
  categoryId?: string | null;
}): boolean {
  if (opts.isInternal) return true;
  const cat = opts.categoryId || null;
  if (cat === INTERNAL_TRANSFERS_CATEGORY_ID) return true;
  if (opts.leafId === INTERNAL_TRANSFERS_CATEGORY_ID) return true;
  return effectiveExcludeFromMap(opts.leafId, opts.nodes);
}
