/**
 * Sticky pin left offsets for DataTable.
 *
 * When every pinned column declares meta.width, offsets are pure arithmetic —
 * never sync into React state (useLayoutEffect + setState was looping under
 * Accounting's 5 pinned cols + row updates → "Maximum update depth exceeded").
 */

export type PinColumnLike = {
  accessorKey?: string;
  id?: string;
  meta?: { width?: number };
};

/** Cumulative left offset (px) per pinned column id, or null if any width missing. */
export function computeMetaPinOffsets(
  columns: PinColumnLike[],
  pinLeft: readonly string[],
): Record<string, number> | null {
  if (!pinLeft.length) return null;
  const next: Record<string, number> = {};
  let acc = 0;
  for (const id of pinLeft) {
    const col = columns.find((c) => c.accessorKey === id || c.id === id);
    const w = col?.meta?.width;
    if (typeof w !== "number" || !(w > 0)) return null;
    next[id] = acc;
    acc += w;
  }
  return next;
}

export function pinOffsetsEqual(
  a: Record<string, number>,
  b: Record<string, number>,
): boolean {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((k) => a[k] === b[k]);
}

/** Build measured offsets from header widths (DOM fallback). */
export function accumulateDomPinOffsets(
  heads: Iterable<{ colId: string; width: number }>,
): Record<string, number> {
  let acc = 0;
  const next: Record<string, number> = {};
  for (const h of heads) {
    next[h.colId] = acc;
    acc += h.width;
  }
  return next;
}
