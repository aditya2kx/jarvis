import { getSystemHealth, type SystemHealth } from "@/lib/bhaga/health";
import { cn } from "@/lib/utils";

type Tone = "danger" | "warn" | "muted";

const TONE: Record<Tone, string> = {
  danger: "border-red-500/30 bg-red-500/10 text-red-900 dark:text-red-200",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-200",
  muted: "border-border bg-muted/50 text-muted-foreground",
};

function ageLabel(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

/**
 * The banner copy, split out so it can be tested without rendering.
 * Returns null when the system is healthy — a healthy system shows no banner.
 */
export function healthMessage(
  h: SystemHealth,
): { tone: Tone; headline: string; detail: string } | null {
  if (h.error) {
    // Warn, not muted: "we could not tell" must not read like "nothing to see".
    // `halted` is false here only because it is unknown, and the figures below
    // may be stale for a reason this page cannot see.
    return {
      tone: "warn",
      headline: "Pipeline health unknown",
      detail:
        `Could not read the breaker or the data window, so the figures below ` +
        `may be stale: ${h.error}`,
    };
  }
  if (h.halted) {
    const scope =
      h.haltScope === "all"
        ? "The nightly run is stopped."
        : "Model rebuilds are stopped; raw data is still being collected.";
    return {
      tone: "danger",
      headline: "Pipeline halted",
      detail:
        `${scope} Tripped ${h.haltSince ?? "at an unknown time"}: ` +
        `${h.haltReason ?? "no reason recorded"}.`,
    };
  }
  if (h.stale && h.dataWindowEnd && h.dataAgeDays !== null) {
    return {
      tone: "warn",
      headline: `Data is ${ageLabel(h.dataAgeDays)}`,
      detail:
        `Every figure below stops at ${h.dataWindowEnd}. The nightly refresh has ` +
        `not advanced the window since then.`,
    };
  }
  return null;
}

/**
 * Global, server-derived health banner.
 *
 * Rendered from the root layout so the state of the pipeline is visible on every
 * page, to every viewer, without anyone having to know to go look for it. On
 * 2026-09-07 the console rendered six-day-old numbers as though they were
 * current, because nothing on any page asked whether the pipeline was alive.
 */
export async function HealthBanner() {
  const health = await getSystemHealth();
  const msg = healthMessage(health);
  if (!msg) return null;

  return (
    <div
      role="status"
      className={cn(
        "shrink-0 border-b px-4 py-2 text-sm md:px-6",
        TONE[msg.tone],
      )}
    >
      <span className="font-semibold">{msg.headline}</span>
      <span className="ml-2 opacity-90">{msg.detail}</span>
    </div>
  );
}
