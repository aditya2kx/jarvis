import {
  baseRunway,
  estimatedScheduleDates,
  nextDates,
  orderRecoCombined,
  storeConfig,
  usageDayAudit,
  type BaseRunwayRow,
  type OrderRecoCombinedRow,
  type UsageDayAuditRow,
} from "@/lib/bq/queries";
import { ensureOrderRecoFresh } from "@/lib/bq/writes";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { triggerOrderRecoRefresh } from "@/lib/bhaga/recompute";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import { UsageDayAuditTable } from "@/components/inventory/UsageDayAuditTable";
import type { ColumnDef } from "@tanstack/react-table";

export const dynamic = "force-dynamic";

const DAYS_LEFT_THRESHOLDS: Thresholds = { warn: 7, bad: 4, direction: "lower-bad" };

export default async function InventoryPage() {
  let rows: OrderRecoCombinedRow[] = [];
  let runwayRows: BaseRunwayRow[] = [];
  let auditRows: UsageDayAuditRow[] = [];
  let dates: string[] = [];
  let estimatedDates: string[] = [];
  let maxTubs: number | undefined;
  let error: string | undefined;
  let recoQueued = false;
  try {
    const ensure = await ensureOrderRecoFresh(
      DEFAULT_STORE,
      FEATURES.asyncOrderReco
        ? { enqueue: () => triggerOrderRecoRefresh(DEFAULT_STORE) }
        : {},
    );
    recoQueued = ensure.status === "queued";
    const [reco, nd, config, runway, estimated, audit] = await Promise.all([
      orderRecoCombined(),
      nextDates(),
      storeConfig(DEFAULT_STORE),
      baseRunway(),
      estimatedScheduleDates(DEFAULT_STORE),
      usageDayAudit(DEFAULT_STORE),
    ]);
    rows = reco;
    runwayRows = runway;
    auditRows = audit;
    dates = nd.map((d) => d.delivery_date);
    estimatedDates = estimated.map((d) => d.delivery_date);
    const maxTubsRow = config.find((c) => c.key === "order_reco_max_tubs");
    maxTubs = maxTubsRow ? Number(maxTubsRow.value) : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const [date1, date2] = dates;

  const runwayColumns: ColumnDef<BaseRunwayRow>[] = [
    { accessorKey: "Base", header: "Base" },
    { accessorKey: "Stock", header: "Stock", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Vel per day", header: "Vel/day", meta: { format: { kind: "number", digits: 2 } } },
    {
      accessorKey: "Days left",
      header: "Days left",
      meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
    },
    { accessorKey: "Stockout 1", header: "Stockout 1", meta: { format: { kind: "date" } } },
    { accessorKey: "Restock 1", header: "Restock 1", meta: { format: { kind: "date" } } },
    { accessorKey: "Qty 1", header: "Qty 1", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Status 1", header: "Status 1", meta: { format: { kind: "status" } } },
    { accessorKey: "Stockout 2", header: "Stockout 2", meta: { format: { kind: "date" } } },
    { accessorKey: "Restock 2", header: "Restock 2", meta: { format: { kind: "date" } } },
    { accessorKey: "Qty 2", header: "Qty 2", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Status 2", header: "Status 2", meta: { format: { kind: "status" } } },
  ];

  const columns: ColumnDef<OrderRecoCombinedRow>[] = [
    { accessorKey: "Item", header: "Item" },
    { accessorKey: "Current Qty", header: "Current Qty", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Avg per day", header: "Avg/day", meta: { format: { kind: "number", digits: 2 } } },
    { accessorKey: "On Hand 1", header: date1 ? `On hand (${date1})` : "On hand — slot 1", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Order Tubs 1", header: "Order tubs", meta: { format: { kind: "number" } } },
    { accessorKey: "After Restock 1", header: "After restock", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "Days Left 1",
      header: "Days left",
      meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
    },
    { accessorKey: "Source 1", header: "Source", meta: { format: { kind: "source" } } },
    { accessorKey: "On Hand 2", header: date2 ? `On hand (${date2})` : "On hand — slot 2", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Order Tubs 2", header: "Order tubs", meta: { format: { kind: "number" } } },
    { accessorKey: "After Restock 2", header: "After restock", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "Days Left 2",
      header: "Days left",
      meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
    },
    { accessorKey: "Source 2", header: "Source", meta: { format: { kind: "source" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Inventory / Ordering"
        subtitle={`Order Assistant recommendations · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          FEATURES.writeRestock ? (
            <>
              <CapacityEdit currentMaxTubs={maxTubs} />
              <RestockImportDrawer dates={dates} estimatedDates={estimatedDates} />
            </>
          ) : null
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          {recoQueued ? (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
              Order recommendation refreshing in the background — numbers may update on the next
              reload.
            </p>
          ) : null}
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Base runway</h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Days left and Stockout 1 are burn-down from today (ignore future restocks).
              Restock 1/2 show uploaded Actuals only (up to two future Actuals dates per base) —
              estimated schedule dates do not appear here. Stockout 2 assumes Restock 1 Actuals
              qty arrived on that date. Status is Risky when that slot&apos;s restock is empty or
              stockout is before the restock date; Fine when restock arrives on or before stockout.
              Rows highlight when Status 1 or Status 2 is Risky.
            </p>
            <DataTable
              columns={runwayColumns}
              data={runwayRows}
              pinLeft={["Base"]}
              initialSorting={[{ id: "Days left", desc: false }]}
              rowHighlight={[
                { accessorKey: "Status 1", equals: "Risky", className: "bg-destructive/5" },
                { accessorKey: "Status 2", equals: "Risky", className: "bg-destructive/5" },
              ]}
            />
          </div>

          <p className="text-sm text-muted-foreground">
            {date1 ? `Next delivery: ${date1}` : "No delivery date registered yet."}
            {date2 ? ` · then ${date2}` : ""}
          </p>
          <DataTable columns={columns} data={rows} pinLeft={["Item", "Current Qty", "Avg per day"]} />

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Base usage by day (last 30 days)
            </h2>
            <p className="mb-2 text-xs text-muted-foreground">
              One row per closing date. Included chips are in the usage average; excluded show why
              (restock, gap, zero usage, outlier, …). Tap a chip to force include/exclude that
              (base, day) — sticky until cleared; force-include also feeds the outlier bar so
              similar days may auto-pass later. Preview shows whether tomorrow&apos;s similar Δ
              would still need an override. Changes recompute order recommendations.
            </p>
            <UsageDayAuditTable
              rows={auditRows}
              writable={FEATURES.writeInventoryDayOverrides}
            />
          </div>
        </>
      )}
    </div>
  );
}
