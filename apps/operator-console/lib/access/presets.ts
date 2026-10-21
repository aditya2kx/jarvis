import { FULL_ACCESS, normalizeGrant, type Grant } from "./policy";

export interface AccessPreset {
  id: string;
  label: string;
  hint: string;
  grant: Grant;
}

/** Starting points in the person drawer; the owner can tweak any of them section by section. */
export const ACCESS_PRESETS: AccessPreset[] = [
  {
    id: "everything",
    label: "Everything",
    hint: "Every page and section, including ones added later",
    grant: FULL_ACCESS,
  },
  {
    id: "store-manager",
    label: "Store manager",
    hint: "Runs the store — no payroll, accounting or wages",
    grant: normalizeGrant({
      pages: {
        home: "all",
        sales: "all",
        labor: ["labor-hours", "concurrent", "coverage", "availability", "hours-per-person", "open-shifts", "one-person", "punch-gaps", "solo"],
        "order-quality": "all",
        inventory: "all",
        automations: "all",
        pipeline: "all",
      },
    }),
  },
  {
    id: "shift-lead",
    label: "Shift lead",
    hint: "Scheduling, open shifts and ordering",
    grant: normalizeGrant({
      pages: {
        labor: ["concurrent", "coverage", "availability", "open-shifts"],
        "order-quality": "all",
        inventory: "all",
      },
    }),
  },
  {
    id: "inventory",
    label: "Inventory only",
    hint: "Order recommendations, restock and usage",
    grant: normalizeGrant({ pages: { inventory: "all" } }),
  },
];

function sameGrant(a: Grant, b: Grant): boolean {
  return JSON.stringify(normalizeGrant(a)) === JSON.stringify(normalizeGrant(b));
}

/** The preset this grant is exactly, or null when it has been customized. */
export function matchPreset(grant: Grant): AccessPreset | null {
  return ACCESS_PRESETS.find((p) => sameGrant(p.grant, grant)) ?? null;
}
