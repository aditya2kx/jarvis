import { nextDates, orderAssistantTable, orderRecoCombined, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { DaysOfCoverPanel } from "@/components/kpi/DaysOfCoverPanel";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderAssistantRow, OrderRecoCombinedRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

// Shared red(<=4)/amber(<=7)/green bands for "days left" across both the
// DataTable cell color and the DaysOfCoverPanel bar color (mirrors Figma).
const DAYS_LEFT_THRESHOLDS: Thresholds = { warn: 7, bad: 4, direction: "lower-bad" };

// Dual-date Order Assistant (migration 032, Grafana panel 83) — Item/Current
// Qty/Avg per day frozen, one "Source N" badge column pair per registered
// delivery date. Restock writes go through the RestockImportDrawer's server
// action, converging with the /bhaga-cloud restock Slack path.
export default async function InventoryPage() {
  let rows: OrderRecoCombinedRow[] = [];
  let baseRows: OrderAssistantRow[] = [];
  let dates: string[] = [];
  let maxTubs: number | undefined;
  let error: string | undefined;
  try {
    const [reco, nd, config, base] = await Promise.all([
      orderRecoCombined(),
      nextDates(),
      storeConfig(DEFAULT_STORE),
      orderAssistantTable(),
    ]);
    rows = reco;
    baseRows = base;
    dates = nd.map((d) => d.delivery_date);
    const maxTubsRow = config.find((c) => c.key === "order_reco_max_tubs");
    maxTubs = maxTubsRow ? Number(maxTubsRow.value) : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const [date1, date2] = dates;

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
              <RestockImportDrawer dates={dates} />
            </>
          ) : null
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {date1 ? `Next delivery: ${date1}` : "No delivery date registered yet."}
            {date2 ? ` · then ${date2}` : ""}
          </p>
          <DaysOfCoverPanel
            items={rows.map((r) => ({ name: r.Item, daysLeft: r["Days Left 1"] }))}
            warnDays={DAYS_LEFT_THRESHOLDS.warn}
            badDays={DAYS_LEFT_THRESHOLDS.bad}
          />
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
