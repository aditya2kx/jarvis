/**
 * Pure gate for Gemini team-pulse rewrites (Issue #233).
 * Rejects multi-draft responses (--- separators / repeated leaderboard).
 */

export function acceptVariedCopy(
  text: string,
  leaderboardMd: string,
): { text: string; varied: boolean } {
  const lb = leaderboardMd.trim();
  const t = text.trim();
  if (!t || !lb) return { text: "", varied: false };
  const occurrences = t.split(lb).length - 1;
  if (occurrences !== 1) return { text: "", varied: false };
  if (/(^|\n)\s*---\s*(\n|$)/.test(t)) return { text: "", varied: false };
  return { text: t, varied: true };
}
