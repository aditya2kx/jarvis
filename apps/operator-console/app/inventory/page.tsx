import { nextDates, orderRecoCombined, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { DataTable } from "@/components/tables/DataTable";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderRecoCombinedRow } from "@/lib/bq/queries";

export const revalidate = 600;

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
    { accessorKey: "Days Left 1", header: "Days left", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Source 1", header: "Source", meta: { format: { kind: "source" } } },
    { accessorKey: "On Hand 2", header: date2 ? `On hand (${date2})` : "On hand — slot 2", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Order Tubs 2", header: "Order tubs", meta: { format: { kind: "number" } } },
    { accessorKey: "After Restock 2", header: "After restock", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Days Left 2", header: "Days left", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Source 2", header: "Source", meta: { format: { kind: "source" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Inventory / Ordering</h1>
        {FEATURES.writeRestock ? (
          <div className="flex items-center gap-2">
            <CapacityEdit currentMaxTubs={maxTubs} />
            <RestockImportDrawer dates={dates} />
          </div>
        ) : null}
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {date1 ? `Next delivery: ${date1}` : "No delivery date registered yet."}
            {date2 ? ` · then ${date2}` : ""}
          </p>
          <DataTable columns={columns} data={rows} pinLeft={["Item", "Current Qty", "Avg per day"]} />
        </>
      )}
    </div>
  );
}
