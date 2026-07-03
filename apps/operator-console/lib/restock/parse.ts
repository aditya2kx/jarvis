// Mirrors cloud/webhook/handler.py::_parse_restock_csv and _ACTIVE_BASES
// exactly — the app upload path and the /bhaga-cloud Slack CSV path must
// accept the same input and produce the same rows.
export const ACTIVE_BASES = ["Açaí", "Coconut", "Tropical", "Mango", "Pitaya", "Matcha", "Ube", "Pog"] as const;
export type ActiveBase = (typeof ACTIVE_BASES)[number];

export interface RestockRow {
  item: string;
  quantityTubs: number;
}

export interface ParseResult {
  rows: RestockRow[];
  errors: string[];
}

function isActiveBase(value: string): value is ActiveBase {
  return (ACTIVE_BASES as readonly string[]).includes(value);
}

/**
 * Parse a (base, quantity) CSV. Header row is optional — a row whose first
 * cell isn't a known base AND whose second cell isn't a number is silently
 * treated as a header, so both "base,quantity\nAçaí,12" and plain "Açaí,12"
 * work. De-dups by base, last occurrence wins.
 */
export function parseRestockCsv(text: string): ParseResult {
  const rows: [string, number][] = [];
  const errors: string[] = [];
  const lines = text.split(/\r\n|\r|\n/);

  lines.forEach((line, i) => {
    const raw = line.split(",");
    if (raw.every((c) => c.trim() === "")) return;
    if (raw.length < 2) {
      errors.push(`row ${i + 1}: expected 'base,quantity', got ${JSON.stringify(raw)}`);
      return;
    }
    const base = raw[0].trim();
    const qtyStr = raw[1].trim();
    if (i === 0 && !isActiveBase(base) && Number.isNaN(Number(qtyStr))) {
      return; // header row — skip
    }
    if (!isActiveBase(base)) {
      errors.push(`row ${i + 1}: unknown base ${JSON.stringify(base)} (expected one of ${ACTIVE_BASES.join(", ")})`);
      return;
    }
    const qty = Number(qtyStr);
    if (qtyStr === "" || Number.isNaN(qty)) {
      errors.push(`row ${i + 1}: quantity ${JSON.stringify(qtyStr)} is not a number`);
      return;
    }
    if (qty < 0) {
      errors.push(`row ${i + 1}: quantity for ${base} must be >= 0, got ${qty}`);
      return;
    }
    rows.push([base, qty]);
  });

  const deduped = new Map(rows);
  return {
    rows: Array.from(deduped, ([item, quantityTubs]) => ({ item, quantityTubs })),
    errors,
  };
}

/** Validate LLM-parsed image rows against the known bases before they're editable. */
export function validateParsedRows(
  candidates: { item: string; quantity_tubs: number; confidence?: number }[],
): ParseResult {
  const rows: RestockRow[] = [];
  const errors: string[] = [];
  for (const c of candidates) {
    if (!isActiveBase(c.item)) {
      errors.push(`unrecognized item ${JSON.stringify(c.item)} (expected one of ${ACTIVE_BASES.join(", ")})`);
      continue;
    }
    if (typeof c.quantity_tubs !== "number" || Number.isNaN(c.quantity_tubs) || c.quantity_tubs < 0) {
      errors.push(`${c.item}: invalid quantity ${JSON.stringify(c.quantity_tubs)}`);
      continue;
    }
    rows.push({ item: c.item, quantityTubs: c.quantity_tubs });
  }
  return { rows, errors };
}
