import { baseRunway, estimatedScheduleDates, nextDates, orderAssistantTable, orderRecoCombined, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import type { ColumnDef } from "@tanstack/react-table";
import type { BaseRunwayRow, OrderAssistantRow, OrderRecoCombinedRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

// Shared red(<=4)/amber(<=7)/green bands for "days left" across runway +
// dual-date reco + analytics tables.
const DAYS_LEFT_THRESHOLDS: Thresholds = { warn: 7, bad: 4, direction: "lower-bad" };

// Dual-date Order Assistant (migration 032, Grafana panel 83) — Item/Current
// Qty/Avg per day frozen, one "Source N" badge column pair per registered
// delivery date. Restock writes go through the RestockImportDrawer's server
// action, converging with the /bhaga-cloud restock Slack path for the three
// shared actions; Replace estimated date is console-only.
//
// Base runway (migration 036, Issue #164) sits above: burn-down days left
// from today; Restock 1/2 match Next delivery slots; Actuals-only Status 1/2.
export default async function InventoryPage() {
  let rows: OrderRecoCombinedRow[] = [];
  let runwayRows: BaseRunwayRow[] = [];
  let baseRows: OrderAssistantRow[] = [];
  let dates: string[] = [];
  let estimatedDates: string[] = [];
  let maxTubs: number | undefined;
  let error: string | undefined;
  try {
    const [reco, nd, config, base, runway, estimated] = await Promise.all([
      orderRecoCombined(),
      nextDates(),
      storeConfig(DEFAULT_STORE),
      orderAssistantTable(),
      baseRunway(),
      estimatedScheduleDates(DEFAULT_STORE),
    ]);
    rows = reco;
    runwayRows = runway;
    baseRows = base;
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
    { accessorKey: "Restock 1", header: date1 ? `Restock 1 (${date1})` : "Restock 1", meta: { format: { kind: "date" } } },
    { accessorKey: "Qty 1", header: "Qty 1", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Status 1", header: "Status 1", meta: { format: { kind: "status" } } },
    { accessorKey: "Stockout 2", header: "Stockout 2", meta: { format: { kind: "date" } } },
    { accessorKey: "Restock 2", header: date2 ? `Restock 2 (${date2})` : "Restock 2", meta: { format: { kind: "date" } } },
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

  // Base Inventory Analytics (Grafana panel 80, single-date usage/day math
  // that vw_order_reco_combined's dual-date table builds on top of).
  const baseColumns: ColumnDef<OrderAssistantRow>[] = [
    { accessorKey: "Item", header: "Item" },
    { accessorKey: "Current Qty", header: "Current Qty", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Reported", header: "Reported" },
    { accessorKey: "Last Restock", header: "Last restock" },
    { accessorKey: "Usage 7d", header: "Usage (7d)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Avg per day", header: "Avg/day", meta: { format: { kind: "number", digits: 2 } } },
    {
      accessorKey: "Days Left",
      header: "Days left",
      meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
    },
    { accessorKey: "Days Considered", header: "Days considered" },
    { accessorKey: "Exclusions", header: "Exclusions" },
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
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Base runway</h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Days left and Stockout 1 are burn-down from today (ignore future restocks).
              Restock 1/2 match the Next delivery dates below (schedule Estimated or Actuals).
              Qty and Status use uploaded Actuals only — Estimated dates appear but cannot make
              Fine alone. Stockout 2 assumes slot-1 Actuals qty arrived on Restock 1. Status is
              Fine when that slot&apos;s Actuals restock arrives on or before its stockout date;
              otherwise Risky. Rows highlight when Status 1 or Status 2 is Risky.
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
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Base inventory analytics</h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Single-date usage/day math (reported qty, 7-day usage, avg/day, days left) that the
              dual-date recommendation table above is built on. &quot;Days considered&quot; excludes
              days with a restock or a reporting gap so usage isn&apos;t double-counted; excluded
              days are listed per item under &quot;Exclusions&quot;.
            </p>
            <DataTable columns={baseColumns} data={baseRows} pinLeft={["Item", "Current Qty"]} />
          </div>
        </>
      )}
    </div>
  );
}
