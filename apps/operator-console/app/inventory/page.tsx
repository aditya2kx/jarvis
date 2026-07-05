import { nextDates, orderRecoCombined, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { DaysOfCoverPanel } from "@/components/kpi/DaysOfCoverPanel";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderRecoCombinedRow } from "@/lib/bq/queries";

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
  let dates: string[] = [];
  let maxTubs: number | undefined;
  let error: string | undefined;
  try {
    const [reco, nd, config] = await Promise.all([
      orderRecoCombined(),
      nextDates(),
      storeConfig(DEFAULT_STORE),
    ]);
    rows = reco;
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
        </>
      )}
    </div>
  );
}
