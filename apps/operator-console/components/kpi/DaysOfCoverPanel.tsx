import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Mirrors the DataTable "Days left" threshold bands (see
// app/inventory/page.tsx DAYS_LEFT_THRESHOLDS) so the bar color always
// agrees with the table cell color for the same item.
const MAX_DAYS = 14; // bar-width cap; days-left rarely exceeds ~2 weeks of cover

function barColor(days: number, warnDays: number, badDays: number): string {
  if (days <= badDays) return "bg-red-500";
  if (days <= warnDays) return "bg-amber-500";
  return "bg-emerald-500";
}

export function DaysOfCoverPanel({
  items,
  warnDays,
  badDays,
}: {
  items: { name: string; daysLeft: number | null }[];
  warnDays: number;
  badDays: number;
}) {
  const rows = items.filter((i) => i.daysLeft != null) as { name: string; daysLeft: number }[];
  if (!rows.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Days of cover</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {rows.map((item) => {
          const widthPct = Math.min(100, (item.daysLeft / MAX_DAYS) * 100);
          return (
            <div key={item.name} className="flex items-center gap-3 text-sm">
              <span className="w-40 shrink-0 truncate sm:w-48">{item.name}</span>
              <div className="relative flex-1 rounded-sm bg-secondary">
                <div
                  className={cn("h-4 rounded-sm", barColor(item.daysLeft, warnDays, badDays))}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
              <span className="w-12 shrink-0 text-right text-xs text-muted-foreground">
                {item.daysLeft.toFixed(1)}d
              </span>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
