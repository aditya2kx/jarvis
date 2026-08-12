"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/tables/DataTable";
import type { RestockActualsPivotedRow } from "@/lib/inventory/restockActuals";

export function OrderedTubsActualsTable({
  rows,
  columns,
}: {
  rows: RestockActualsPivotedRow[];
  columns: ColumnDef<RestockActualsPivotedRow>[];
}) {
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No uploaded Actuals in this Period.
      </p>
    );
  }

  return <DataTable columns={columns} data={rows} pinLeft={["date"]} />;
}
