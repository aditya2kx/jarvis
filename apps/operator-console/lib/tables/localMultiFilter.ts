/** null = all selected (no filter). */
export type LocalMultiSelection = string[] | null;

export function facetedOptions(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

/** Row passes when every active dimension includes its value (null dim = all). */
export function rowMatchesLocalFilters(
  row: Record<string, string>,
  filters: Record<string, LocalMultiSelection>,
): boolean {
  for (const [key, selected] of Object.entries(filters)) {
    if (selected == null) continue;
    if (selected.length === 0) return false;
    if (!selected.includes(row[key] ?? "")) return false;
  }
  return true;
}
