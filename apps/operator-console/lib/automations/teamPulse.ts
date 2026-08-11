/** Pure team-pulse compose helpers (mirror agents/bhaga/scripts/team_pulse.py). */

export const AUTOMATION_ID = "team-pulse";
export const DEFAULT_DAYS = [1, 3, 6]; // Tue Thu Sun (Python weekday Mon=0)
export const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export const DEFAULT_TEMPLATE = `{greeting} Team ! Sharing {pay_cycle}'s leaderboard based of Google Review Bonus.

{leaderboard}

Keep the momentum going...!! One team, one fight.

There would be more such incentives/challenges program rolled out soon.
`;

export type PayCycleContext = {
  periodStart: string;
  periodEnd?: string | null;
  isCurrent: boolean;
};

/** Chicago local hour 0–23 (for greeting). */
export function chicagoHour(now: Date = new Date()): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    hour: "numeric",
    hourCycle: "h23",
  }).formatToParts(now);
  return Number(parts.find((p) => p.type === "hour")?.value ?? "0");
}

/** "Good Morning" / "Good Afternoon" / "Good Evening" in America/Chicago. */
export function timeOfDayGreeting(now: Date = new Date()): string {
  const h = chicagoHour(now);
  if (h < 12) return "Good Morning";
  if (h < 17) return "Good Afternoon";
  return "Good Evening";
}

/**
 * Fill `{greeting}` and rewrite legacy Good Morning/Afternoon/Evening so
 * Preview/Post match Chicago time of day.
 */
export function applyGreetingWording(
  template: string,
  now: Date = new Date(),
): string {
  const greeting = timeOfDayGreeting(now);
  let t = template.includes("{greeting}")
    ? template.split("{greeting}").join(greeting)
    : template;
  t = t.replace(/\bGood (Morning|Afternoon|Evening)\b/gi, greeting);
  return t;
}

/** Human date for message copy (UTC calendar date → "Jul 27"). */
export function formatPayCycleDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(dt);
}

/** "current pay cycle" or "the Jul 27 – Aug 9 pay cycle". */
export function formatPayCycleLabel(ctx: PayCycleContext): string {
  if (ctx.isCurrent) return "current pay cycle";
  const start = formatPayCycleDate(ctx.periodStart);
  if (ctx.periodEnd) {
    return `the ${start} – ${formatPayCycleDate(ctx.periodEnd)} pay cycle`;
  }
  return `the ${start} pay cycle`;
}

/**
 * Fill `{pay_cycle}` and rewrite legacy "current pay cycle['s]" when the
 * selected biweek is not the current one (stored BQ templates).
 */
export function applyPayCycleWording(
  template: string,
  ctx: PayCycleContext,
): string {
  const label = formatPayCycleLabel(ctx);
  let t = template;
  if (t.includes("{pay_cycle}")) {
    t = t.split("{pay_cycle}").join(label);
  }
  if (!ctx.isCurrent) {
    t = t.replace(/\bcurrent pay cycle's\b/gi, `${label}'s`);
    t = t.replace(/\bcurrent pay cycle\b/gi, label);
  }
  return t;
}

export function composeMessage(
  template: string,
  leaderboardMd: string,
  period?: PayCycleContext,
  now: Date = new Date(),
): string {
  let filled = applyGreetingWording(template, now);
  if (period) filled = applyPayCycleWording(filled, period);
  if (filled.includes("{leaderboard}")) {
    return filled.replace("{leaderboard}", leaderboardMd.trim()).trim();
  }
  if (!leaderboardMd.trim()) return filled.trim();
  return `${filled.trim()}\n\n${leaderboardMd.trim()}`;
}

export function displayName(employee: string): string {
  const raw = employee.trim();
  if (raw.includes(", ")) {
    const [last, first] = raw.split(", ", 2);
    return `${first.trim()} ${last.trim()}`.trim();
  }
  return raw;
}

export function formatMoney(amount: number): string {
  if (Math.abs(amount - Math.round(amount)) < 1e-9) return `$${Math.round(amount)}`;
  return `$${amount.toFixed(2)}`;
}

export function formatLeaderboard(
  rows: { employee: string; total_bonus: number | string | null }[],
): string {
  const byAmount = new Map<number, string[]>();
  for (const r of rows) {
    const amt = Number(r.total_bonus ?? 0);
    if (!(amt > 0)) continue;
    const name = displayName(String(r.employee ?? ""));
    if (!name) continue;
    const list = byAmount.get(amt) ?? [];
    list.push(name);
    byAmount.set(amt, list);
  }
  if (byAmount.size === 0) {
    return "_No review bonuses credited in this pay period yet._";
  }
  const amounts = [...byAmount.keys()].sort((a, b) => b - a);
  const lines: string[] = [];
  amounts.forEach((amt, i) => {
    const names = (byAmount.get(amt) ?? []).slice().sort();
    const money = formatMoney(amt);
    const top = i === 0;
    if (names.length === 1) {
      const verb = top ? `leading with ${money}` : `at ${money}`;
      lines.push(`*   **${names[0]}** ${verb}.`);
    } else if (names.length === 2) {
      const verb = top ? `leading with ${money} each` : `at ${money} each`;
      lines.push(`*   **${names[0]}** and **${names[1]}** ${verb}.`);
    } else {
      const head = names.slice(0, -1).map((n) => `**${n}**`).join(", ");
      const verb = top ? `leading with ${money} each` : `at ${money} each`;
      lines.push(`*   ${head}, and **${names[names.length - 1]}** ${verb}.`);
    }
  });
  return lines.join("\n");
}

export function parseDays(raw: unknown): number[] {
  if (Array.isArray(raw)) return raw.map((x) => Number(x));
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) return parsed.map((x) => Number(x));
    } catch {
      /* fall through */
    }
  }
  return [...DEFAULT_DAYS];
}

export function cadenceSummary(
  days: number[],
  hour: number,
  minute: number,
  tz: string,
): string {
  const labels = days
    .filter((d) => d >= 0 && d <= 6)
    .sort((a, b) => a - b)
    .map((d) => DAY_LABELS[d])
    .join(" · ");
  return `${labels} · ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} ${tz}`;
}

export function chicagoTodayIso(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
