import { orderAssistantTable } from "@/lib/bq/queries";
import { DataTable } from "@/components/tables/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderAssistantRow } from "@/lib/bq/queries";

export const revalidate = 600;

// M2 read-only cut of the Order Assistant table. M3 (docs/operator-console/
// EXECUTION.md §4) replaces this with the dual-date vw_order_reco_combined
// view (frozen Item/Current Qty/Avg per day columns, Estimated/Actuals
// badges) plus the restock write drawer.
export default async function InventoryPage() {
  let rows: OrderAssistantRow[] = [];
  let error: string | undefined;
  try {
    rows = await orderAssistantTable();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const columns: ColumnDef<OrderAssistantRow>[] = [
    { accessorKey: "Item", header: "Item" },
    { accessorKey: "Current Qty", header: "Current Qty", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Reported", header: "Reported" },
    { accessorKey: "Last Restock", header: "Last Restock", meta: { format: { kind: "date" } } },
    { accessorKey: "Usage 7d", header: "Usage 7d", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Avg per day", header: "Avg/day", meta: { format: { kind: "number", digits: 2 } } },
    { accessorKey: "Days Left", header: "Days Left", meta: { format: { kind: "number", digits: 1 } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Inventory / Ordering</h1>
        <span className="text-sm text-muted-foreground">
          Dual-date order recommendation + restock upload ship in M3
        </span>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <DataTable columns={columns} data={rows} pinLeft={["Item", "Current Qty", "Avg per day"]} />
      )}
    </div>
  );
}
