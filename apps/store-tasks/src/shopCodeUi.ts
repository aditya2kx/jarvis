/** Labels and status for Manage → Sign-in shop-code cards (pure, unit-tested). */

export type ShopCodeRow = {
  set: boolean;
  code: string | null;
  needs_rotate: boolean;
};

export function shopCodeStatus(row: ShopCodeRow): "shown" | "needs_rotate" | "unset" {
  if (row.code) return "shown";
  if (row.needs_rotate) return "needs_rotate";
  return "unset";
}

export function shopCodeButtonLabel(row: ShopCodeRow, customLen: number, busy: boolean): string {
  if (busy) return "Saving…";
  const custom = customLen === 6;
  if (row.set) return custom ? "Change code" : "Issue new code";
  return custom ? "Set code" : "Issue code";
}
