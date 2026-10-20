/** A `partial` run finished, but a non-fatal step failed and the deploy gap scan
 * will replay it: a warning, not a failure. */
export type RunTone = "ok" | "warn" | "bad" | "unknown";

export const OK_BADGE_CLASS =
  "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";

export const WARN_BADGE_CLASS =
  "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400";

export function runTone(status: string | null | undefined): RunTone {
  if (status === "success") return "ok";
  if (status === "partial") return "warn";
  return status ? "bad" : "unknown";
}
