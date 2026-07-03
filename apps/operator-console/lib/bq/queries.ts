import "server-only";
import { fq, q } from "./client";

export interface LaborDailyRow {
  date: string;
  store: string;
  net_sales_cents?: number;
  labor_cost_cents?: number;
  labor_pct?: number;
  [key: string]: unknown;
}

// M1 proves the data path with one real view; M2 adds the rest
// (laborWeekly, salesItemDaily, forecast, forecastAccuracy, orderQualityDaily,
// kdsBySource, payrollPeriod, reviewBonusDetail, pipelineRuns, sourcePulls,
// storeConfig — see docs/operator-console/EXECUTION.md §4 M2).
export function laborDaily(store: string, days = 30): Promise<LaborDailyRow[]> {
  return q<LaborDailyRow>(
    `SELECT * FROM ${fq("vw_model_labor_daily")} WHERE store=@store
     AND date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date DESC`,
    { store, days },
  );
}
