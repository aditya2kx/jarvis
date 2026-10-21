/**
 * Read-only preview mode (`CONSOLE_PREVIEW=1`): renders prod data but every
 * server action is refused in middleware.ts and render-time self-heals skip
 * their writes. Set only on preview / review deploys, never on `operator-console`.
 */
export function isConsolePreview(): boolean {
  return process.env.CONSOLE_PREVIEW === "1";
}

export const PREVIEW_ACTION_REFUSAL = "Preview mode — changes are disabled. Nothing was saved.";

/**
 * A preview whose access grants live in their own sandbox table (`CONSOLE_ACCESS_TABLE`,
 * set by operator-console-preview.yml) may save access there — the one write a preview allows.
 */
export function accessSandboxWritable(): boolean {
  const table = process.env.CONSOLE_ACCESS_TABLE?.trim();
  return Boolean(table) && table !== "console_access_grants";
}
