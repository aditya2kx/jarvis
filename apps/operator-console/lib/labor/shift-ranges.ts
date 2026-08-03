/**
 * Parse ADP Team Schedule shift range strings ("1:30 PM - 8:30 PM") into
 * decimal hours and minute-of-day bounds for concurrent math.
 */

const SHIFT_RANGE_RE =
  /(\d{1,2}):(\d{2})\s*(AM|PM)\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)/i;

function toMinutes(h: number, m: number, ampm: string): number {
  let hh = h % 12;
  if (ampm.toUpperCase() === "PM") hh += 12;
  return hh * 60 + m;
}

export type ParsedShiftRange = {
  startMin: number;
  endMin: number;
  hours: number;
};

/** Parse one ADP range string; overnight wraps past midnight. */
export function parseShiftRange(s: string | null | undefined): ParsedShiftRange | null {
  if (!s) return null;
  const m = SHIFT_RANGE_RE.exec(String(s));
  if (!m) return null;
  let start = toMinutes(Number(m[1]), Number(m[2]), m[3]!);
  let end = toMinutes(Number(m[4]), Number(m[5]), m[6]!);
  if (end < start) end += 24 * 60;
  return {
    startMin: start,
    endMin: end,
    hours: Number(((end - start) / 60).toFixed(2)),
  };
}

export function parseShiftRangesJson(raw: string | null | undefined): ParsedShiftRange[] {
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return [];
    return arr
      .map((x) => parseShiftRange(typeof x === "string" ? x : null))
      .filter((x): x is ParsedShiftRange => x != null);
  } catch {
    return [];
  }
}

/**
 * Day concurrent = Σ hours ÷ (first start → last end) in hours.
 * Returns null when span is missing/zero.
 */
export function concurrentFromRanges(
  hours: number,
  ranges: ParsedShiftRange[],
): number | null {
  if (!(hours > 0) || ranges.length === 0) return null;
  const start = Math.min(...ranges.map((r) => r.startMin));
  const end = Math.max(...ranges.map((r) => r.endMin));
  const spanHrs = (end - start) / 60;
  if (!(spanHrs > 0)) return null;
  return Number((hours / spanHrs).toFixed(2));
}
