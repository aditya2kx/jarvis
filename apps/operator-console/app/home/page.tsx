import { laborDaily, type LaborDailyRow } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { formatCents, formatDate, formatPct } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const revalidate = 600;

export default async function HomePage() {
  let latest: LaborDailyRow | undefined;
  let error: string | undefined;
  try {
    const rows = await laborDaily(DEFAULT_STORE, 1);
    latest = rows[0];
  } catch (e) {
    // Expected locally without ADC/BQ access; M2 wires the full Home scorecard.
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
      <Card className="max-w-sm">
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Net sales — latest day ({DEFAULT_STORE})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {latest ? (
            <>
              <p className="text-3xl font-semibold">{formatCents(latest.net_sales_cents)}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {formatDate(latest.date)} · labor {formatPct(latest.labor_pct)}
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              vw_model_labor_daily unavailable{error ? `: ${error}` : ""}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
