/** Shared by DataTable — text substring or multi-select exact match. */
export function filterTextOrMulti(
  rowValue: unknown,
  filterValue: unknown,
): boolean {
  if (Array.isArray(filterValue)) {
    if (!filterValue.length) return true;
    const cell = rowValue == null || rowValue === "" ? "" : String(rowValue);
    return filterValue.map(String).includes(cell);
  }
  const needle = String(filterValue ?? "")
    .trim()
    .toLowerCase();
  if (!needle) return true;
  if (rowValue == null || rowValue === "") return false;
  return String(rowValue).toLowerCase().includes(needle);
}

export type ColumnFilterLike = { id: string; value: unknown };

/**
 * Faceted option values for `forCol`: unique cell values among rows that still
 * match every OTHER column filter. Selected values are always retained so a
 * choice does not vanish from the popover mid-edit.
 */
export function facetedMultiOptions(
  rows: Record<string, unknown>[],
  forCol: string,
  filters: ColumnFilterLike[],
  selected?: unknown,
): string[] {
  const vals = new Set<string>();
  for (const row of rows) {
    let ok = true;
    for (const f of filters) {
      if (f.id === forCol) continue;
      if (!filterTextOrMulti(row[f.id], f.value)) {
        ok = false;
        break;
      }
    }
    if (!ok) continue;
    const v = row[forCol];
    vals.add(v == null || v === "" ? "" : String(v));
  }
  if (Array.isArray(selected)) {
    for (const s of selected) vals.add(String(s));
  }
  return [...vals].sort((a, b) => a.localeCompare(b));
}
