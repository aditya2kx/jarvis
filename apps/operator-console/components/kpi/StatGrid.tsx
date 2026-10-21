import { cn } from "@/lib/utils";

export interface Stat {
  label: string;
  value: string;
  tone?: "positive" | "negative";
}

const TONE: Record<NonNullable<Stat["tone"]>, string> = {
  positive: "text-emerald-700 dark:text-emerald-400",
  negative: "text-red-700 dark:text-red-400",
};

/** Headline figures as equal tiles: two across on phones, one row on desktop. */
export function StatGrid({ stats, caption }: { stats: Stat[]; caption?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <dl className="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid="stat-grid">
        {stats.map((s) => (
          <div key={s.label} className="flex flex-col gap-0.5 rounded-lg border border-border bg-card px-3 py-2.5">
            <dt className="text-xs text-muted-foreground">{s.label}</dt>
            <dd className={cn("text-lg font-semibold tracking-tight tabular-nums", s.tone && TONE[s.tone])}>{s.value}</dd>
          </div>
        ))}
      </dl>
      {caption ? <p className="text-xs text-muted-foreground">{caption}</p> : null}
    </div>
  );
}
