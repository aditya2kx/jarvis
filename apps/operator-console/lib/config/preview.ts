/**
 * Read-only preview mode (`CONSOLE_PREVIEW=1`): renders prod data but every
 * server action is refused in middleware.ts and render-time self-heals skip
 * their writes. Set only on preview / review deploys, never on `operator-console`.
 */
export function isConsolePreview(): boolean {
  return process.env.CONSOLE_PREVIEW === "1";
}

export const PREVIEW_ACTION_REFUSAL = "Preview mode — changes are disabled. Nothing was saved.";
