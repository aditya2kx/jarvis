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
