"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { pfcDetailedHint, pfcPrimaryDefinition } from "@/lib/plaid/pfc-definitions";
import { setPlaidInternalAction } from "@/app/accounting/actions";
import { formatDollars } from "@/lib/format";

export interface AccountingTxnRow {
  transaction_id: string;
  date: string;
  transaction_name: string;
  account_last4: string;
  spend: number | null;
  earned: number | null;
  category: string;
  category_detail: string;
  channel: string;
  pending_label: string;
  amount: number;
  is_internal: boolean;
  internal_label: string;
}

function moneyTotals(rows: AccountingTxnRow[]): { spend: number; earned: number } {
  let spend = 0;
  let earned = 0;
  for (const r of rows) {
    if (r.is_internal) continue;
    if (typeof r.spend === "number") spend += r.spend;
    if (typeof r.earned === "number") earned += r.earned;
  }
  return { spend, earned };
}

function categoryRollup(rows: AccountingTxnRow[]): { category: string; spend: number; count: number }[] {
  const map = new Map<string, { spend: number; count: number }>();
  for (const r of rows) {
    if (r.is_internal) continue;
    if (!(typeof r.spend === "number" && r.spend > 0)) continue;
    const key = r.category || "—";
    const cur = map.get(key) || { spend: 0, count: 0 };
    cur.spend += r.spend;
    cur.count += 1;
    map.set(key, cur);
  }
  return [...map.entries()]
    .map(([category, v]) => ({ category, spend: v.spend, count: v.count }))
    .sort((a, b) => b.spend - a.spend)
    .slice(0, 12);
}

export function AccountingLedger({
  netSales,
  periodLabel,
  rows: initialRows,
  canWrite,
}: {
  netSales: number | null;
  periodLabel: string;
  rows: AccountingTxnRow[];
  canWrite: boolean;
}) {
  const [rows, setRows] = useState(initialRows);
  const [hideInternal, setHideInternal] = useState(true);
  const [filtered, setFiltered] = useState<AccountingTxnRow[]>(initialRows);
  const [explain, setExplain] = useState<{
    primary: string;
    detailed: string;
  } | null>(null);
  const [pending, startTransition] = useTransition();

  // Keep local rows in sync when the server re-renders with a new period.
  useEffect(() => {
    setRows(initialRows);
    setFiltered(initialRows);
  }, [initialRows]);

  const tableData = useMemo(
    () => (hideInternal ? rows.filter((r) => !r.is_internal) : rows),
    [rows, hideInternal],
  );

  const onFilteredRowsChange = useCallback((next: AccountingTxnRow[]) => {
    setFiltered(next);
  }, []);

  const kpis = useMemo(() => moneyTotals(filtered), [filtered]);
  const cats = useMemo(() => categoryRollup(filtered), [filtered]);
  const internalHidden = rows.filter((r) => r.is_internal).length;

  function toggleInternal(txnId: string, next: boolean) {
    setRows((prev) =>
      prev.map((r) =>
        r.transaction_id === txnId
          ? { ...r, is_internal: next, internal_label: next ? "yes" : "no" }
          : r,
      ),
    );
    startTransition(async () => {
      try {
        await setPlaidInternalAction(txnId, next);
      } catch (e) {
        // Revert optimistic flip on failure.
        setRows((prev) =>
          prev.map((r) =>
            r.transaction_id === txnId
              ? { ...r, is_internal: !next, internal_label: !next ? "yes" : "no" }
              : r,
          ),
        );
        console.error(e);
      }
    });
  }

  const toggleInternalCb = useCallback(toggleInternal, []);

  const columns: ColumnDef<AccountingTxnRow>[] = useMemo(
    () => [
      {
        accessorKey: "date",
        header: "Date",
        meta: { format: { kind: "date" }, filterable: true, width: 88 },
      },
      {
        accessorKey: "account_last4",
        header: "Account",
        meta: { filterable: true, width: 120 },
      },
      {
        accessorKey: "spend",
        header: "Spend",
        meta: { format: { kind: "dollars" }, filterable: true, width: 100 },
      },
      {
        accessorKey: "earned",
        header: "Earned",
        meta: { format: { kind: "dollars" }, filterable: true, width: 100 },
      },
      {
        accessorKey: "transaction_name",
        header: "Transaction",
        meta: { filterable: true, wrap: true, maxWidth: 280, width: 240 },
      },
      {
        accessorKey: "category",
        header: "Category",
        meta: { filterable: true, wrap: true, maxWidth: 160, width: 140 },
        cell: ({ row }) => {
          const code = row.original.category;
          if (!code || code === "—") return "—";
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() =>
                setExplain({
                  primary: code,
                  detailed: row.original.category_detail,
                })
              }
            >
              {code}
            </button>
          );
        },
      },
      {
        accessorKey: "category_detail",
        header: "Detail",
        meta: { filterable: true, wrap: true, maxWidth: 180, width: 160 },
        cell: ({ row }) => {
          const code = row.original.category_detail;
          if (!code || code === "—") return "—";
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() =>
                setExplain({
                  primary: row.original.category,
                  detailed: code,
                })
              }
            >
              {code}
            </button>
          );
        },
      },
      {
        accessorKey: "channel",
        header: "Channel",
        meta: { filterable: true, width: 88 },
      },
      {
        accessorKey: "pending_label",
        header: "Pending",
        meta: { filterable: true, width: 80 },
      },
      {
        accessorKey: "internal_label",
        header: "Internal",
        meta: { filterable: true, width: 100 },
        cell: ({ row }) => {
          const on = row.original.is_internal;
          if (!canWrite) return on ? "yes" : "no";
          return (
            <Button
              type="button"
              size="sm"
              variant={on ? "secondary" : "outline"}
              className="h-7 px-2 text-xs"
              disabled={pending}
              onClick={() => toggleInternalCb(row.original.transaction_id, !on)}
            >
              {on ? "Internal" : "Mark"}
            </Button>
          );
        },
      },
    ],
    [canWrite, pending, toggleInternalCb],
  );

  const def = explain ? pfcPrimaryDefinition(explain.primary) : null;
  const detailHint = explain ? pfcDetailedHint(explain.detailed) : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Money in (Square net sales)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">
              {netSales == null
                ? "—"
                : netSales.toLocaleString("en-US", { style: "currency", currency: "USD" })}
            </p>
            <p className="text-xs text-muted-foreground">{periodLabel}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Money out (Plaid spend)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">
              {kpis.spend.toLocaleString("en-US", { style: "currency", currency: "USD" })}
            </p>
            <p className="text-xs text-muted-foreground">
              Follows table filters · excludes internal · {periodLabel}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Money in (Plaid earned)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">
              {kpis.earned.toLocaleString("en-US", { style: "currency", currency: "USD" })}
            </p>
            <p className="text-xs text-muted-foreground">
              Follows table filters · excludes internal · {periodLabel}
            </p>
          </CardContent>
        </Card>
      </div>

      {cats.length ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Spend by category (filtered)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-1 text-sm sm:grid-cols-2">
              {cats.map((c) => (
                <li key={c.category} className="flex justify-between gap-3">
                  <button
                    type="button"
                    className="truncate text-left underline decoration-dotted underline-offset-2"
                    onClick={() => setExplain({ primary: c.category, detailed: "—" })}
                  >
                    {c.category}
                  </button>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {formatDollars(c.spend)} · {c.count}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Transactions
          </CardTitle>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={hideInternal}
              onChange={(e) => setHideInternal(e.target.checked)}
            />
            Hide internal
            {internalHidden ? ` (${internalHidden})` : ""}
          </label>
        </CardHeader>
        <CardContent>
          {tableData.length ? (
            <DataTable
              columns={columns}
              data={tableData}
              enableColumnFilters
              onFilteredRowsChange={onFilteredRowsChange}
              initialSorting={[{ id: "date", desc: true }]}
              pinLeft={["date", "account_last4", "spend", "earned"]}
              rowHighlight={{
                accessorKey: "internal_label",
                equals: "yes",
                className: "opacity-60",
              }}
            />
          ) : (
            <p className="text-sm text-muted-foreground">No transactions in this period.</p>
          )}
        </CardContent>
      </Card>

      <Sheet open={!!explain} onOpenChange={(open) => !open && setExplain(null)}>
        <SheetContent side="right" className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{def?.title || "Category"}</SheetTitle>
            <SheetDescription>
              Plaid Personal Finance Category (PFC v2) — interim until custom taxonomy (#160).
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-3 px-4 pb-4">
            <p className="text-sm">{def?.summary}</p>
            {def?.opsNote ? (
              <p className="text-sm text-muted-foreground">{def.opsNote}</p>
            ) : null}
            {explain?.detailed && explain.detailed !== "—" ? (
              <p className="text-xs text-muted-foreground">
                Detail code: <span className="font-mono">{explain.detailed}</span>
              </p>
            ) : null}
            {detailHint ? <p className="text-sm">{detailHint}</p> : null}
            <p className="text-xs text-muted-foreground">
              Primary: <span className="font-mono">{explain?.primary}</span>
            </p>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
