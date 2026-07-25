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
import {
  reapplyPlaidCategoriesAction,
  setPlaidInternalAction,
  setTxnCategoryOverrideAction,
} from "@/app/accounting/actions";
import { formatDollars } from "@/lib/format";
import { formatBucket, truncateToGrain, type Grain } from "@/lib/filters/range";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { ACCOUNTING_COLORS, expenseCategoryColor } from "@/lib/charts/palette";
import { cn } from "@/lib/utils";
import {
  AccountingRulesDrawer,
  type RuleListItem,
} from "@/components/accounting/AccountingRulesDrawer";

export interface AccountingTxnRow {
  transaction_id: string;
  date: string;
  transaction_name: string;
  /** Full Plaid/bank memo (ACH ORIG CO NAME / TRACE / IND NAME, etc.). */
  bank_description: string | null;
  counterparty: string | null;
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
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  is_override: boolean;
  category_definition: string | null;
  rule_summary: string | null;
}

export interface TaxonomyOption {
  id: string;
  parent_id: string | null;
  label: string;
}

const TOP_CATEGORY_SERIES = 8;

export type ChartUnit = "dollars" | "pct_net_sales";

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

function pctOf(numerator: number, denom: number): number {
  if (!denom) return 0;
  return Math.round((numerator / denom) * 1000) / 10;
}

function cashFlowByGrain(
  rows: AccountingTxnRow[],
  grain: Grain,
): { iso: string; date: string; money_in: number; money_out: number; cash_flow: number }[] {
  const map = new Map<string, { money_in: number; money_out: number }>();
  for (const r of rows) {
    if (r.is_internal) continue;
    const key = truncateToGrain(r.date, grain);
    const cur = map.get(key) || { money_in: 0, money_out: 0 };
    if (typeof r.earned === "number") cur.money_in += r.earned;
    if (typeof r.spend === "number") cur.money_out += r.spend;
    map.set(key, cur);
  }
  return [...map.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([iso, v]) => ({
      iso,
      date: formatBucket(iso, grain),
      money_in: Math.round(v.money_in * 100) / 100,
      money_out: Math.round(v.money_out * 100) / 100,
      cash_flow: Math.round((v.money_in - v.money_out) * 100) / 100,
    }));
}

function spendByCategoryGrain(
  rows: AccountingTxnRow[],
  grain: Grain,
): {
  data: { iso: string; date: string; values: Record<string, number> }[];
  series: { key: string; label: string; color: string }[];
} {
  const totals = new Map<string, number>();
  const byBucket = new Map<string, Map<string, number>>();

  for (const r of rows) {
    if (r.is_internal) continue;
    if (!(typeof r.spend === "number" && r.spend > 0)) continue;
    const cat = r.category || "Uncategorized";
    totals.set(cat, (totals.get(cat) || 0) + r.spend);
    const bucket = truncateToGrain(r.date, grain);
    let cats = byBucket.get(bucket);
    if (!cats) {
      cats = new Map();
      byBucket.set(bucket, cats);
    }
    cats.set(cat, (cats.get(cat) || 0) + r.spend);
  }

  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  const top = ranked.slice(0, TOP_CATEGORY_SERIES).map(([c]) => c);
  const topSet = new Set(top);
  const hasOther = ranked.length > top.length;
  const labels = [...top, ...(hasOther ? ["Other"] : [])];
  const series = labels.map((c, i) => ({
    key: c,
    label: c,
    color: expenseCategoryColor(i, c === "Other"),
  }));

  const data = [...byBucket.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([iso, cats]) => {
      const values: Record<string, number> = {};
      for (const s of series) values[s.key] = 0;
      for (const [cat, amt] of cats) {
        const key = topSet.has(cat) ? cat : "Other";
        if (!(key in values)) continue;
        values[key] = Math.round((values[key]! + amt) * 100) / 100;
      }
      return { iso, date: formatBucket(iso, grain), values };
    });

  return { data, series };
}

export function AccountingLedger({
  periodLabel,
  grain,
  rows: initialRows,
  canWrite,
  taxonomy,
  rules,
  squareNetSalesTotal,
  squareNetSalesByIso,
}: {
  periodLabel: string;
  grain: Grain;
  rows: AccountingTxnRow[];
  canWrite: boolean;
  taxonomy: TaxonomyOption[];
  rules: RuleListItem[];
  /** Square POS net sales for the period (BHAGA labor daily). */
  squareNetSalesTotal: number | null;
  /** Square net sales keyed by the same grain bucket ISO as bank charts. */
  squareNetSalesByIso: Record<string, number>;
}) {
  const [rows, setRows] = useState(initialRows);
  const [hideInternal, setHideInternal] = useState(true);
  const [filtered, setFiltered] = useState<AccountingTxnRow[]>(initialRows);
  const [explain, setExplain] = useState<AccountingTxnRow | null>(null);
  const [pending, startTransition] = useTransition();
  const [reapplyMsg, setReapplyMsg] = useState<string | null>(null);
  const [chartUnit, setChartUnit] = useState<ChartUnit>("dollars");

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

  // Charts/totals must read `is_internal` from `rows` (optimistic + post-revalidate
  // source of truth). `filtered` alone can lag with a stale copy after Mark Internal,
  // so KPIs kept counting the transfer. Gate by filtered txn ids so column filters
  // still apply; always drop internals from rollups.
  const rollupRows = useMemo(() => {
    const visibleIds = new Set(filtered.map((r) => r.transaction_id));
    return rows.filter((r) => !r.is_internal && visibleIds.has(r.transaction_id));
  }, [rows, filtered]);

  const kpis = useMemo(() => moneyTotals(rollupRows), [rollupRows]);
  const cashFlow = kpis.earned - kpis.spend;
  const cashChartBase = useMemo(() => cashFlowByGrain(rollupRows, grain), [rollupRows, grain]);
  const categoryChartBase = useMemo(
    () => spendByCategoryGrain(rollupRows, grain),
    [rollupRows, grain],
  );
  const asPct = chartUnit === "pct_net_sales";

  const cashChart = useMemo(() => {
    return cashChartBase.map((row) => {
      const denom = squareNetSalesByIso[row.iso] ?? 0;
      if (!asPct) {
        return {
          date: row.date,
          money_in: row.money_in,
          money_out: row.money_out,
          cash_flow: row.cash_flow,
        };
      }
      return {
        date: row.date,
        money_in: pctOf(row.money_in, denom),
        money_out: pctOf(row.money_out, denom),
        cash_flow: pctOf(row.cash_flow, denom),
      };
    });
  }, [cashChartBase, squareNetSalesByIso, asPct]);

  const categoryChart = useMemo(() => {
    const series = categoryChartBase.series;
    const data = categoryChartBase.data.map((row) => {
      const denom = squareNetSalesByIso[row.iso] ?? 0;
      const out: Record<string, unknown> = { date: row.date };
      for (const s of series) {
        const dollars = row.values[s.key] ?? 0;
        out[s.key] = asPct ? pctOf(dollars, denom) : dollars;
      }
      return out;
    });
    return { data, series };
  }, [categoryChartBase, squareNetSalesByIso, asPct]);

  const grainLabel = grain === "day" ? "day" : grain === "week" ? "week" : "month";
  const internalHidden = rows.filter((r) => r.is_internal).length;
  const unitToggle = (
    <div className="flex items-center gap-1 rounded-md bg-secondary p-0.5">
      {(
        [
          { value: "dollars" as const, label: "$" },
          { value: "pct_net_sales" as const, label: "% of Square net sales" },
        ] as const
      ).map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={cn(
            "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
            chartUnit === opt.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
          onClick={() => setChartUnit(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );

  const parents = useMemo(
    () => taxonomy.filter((t) => !t.parent_id),
    [taxonomy],
  );

  function toggleInternal(txnId: string, next: boolean) {
    const patch = (r: AccountingTxnRow): AccountingTxnRow =>
      r.transaction_id === txnId
        ? { ...r, is_internal: next, internal_label: next ? "yes" : "no" }
        : r;

    setRows((prev) => prev.map(patch));
    // Keep filtered in sync so rollups don't wait on DataTable's next emit.
    setFiltered((prev) => {
      if (next && hideInternal) {
        return prev.filter((r) => r.transaction_id !== txnId);
      }
      return prev.map(patch);
    });

    startTransition(async () => {
      try {
        await setPlaidInternalAction(txnId, next);
      } catch (e) {
        const revert = (r: AccountingTxnRow): AccountingTxnRow =>
          r.transaction_id === txnId
            ? { ...r, is_internal: !next, internal_label: !next ? "yes" : "no" }
            : r;
        setRows((prev) => prev.map(revert));
        setFiltered((prev) => prev.map(revert));
        console.error(e);
      }
    });
  }

  const toggleInternalCb = useCallback(toggleInternal, [hideInternal]);

  function applyOverride(txnId: string, categoryId: string | null, subcategoryId: string | null) {
    startTransition(async () => {
      try {
        await setTxnCategoryOverrideAction(txnId, categoryId, subcategoryId);
        const parent = taxonomy.find((t) => t.id === categoryId);
        const child = taxonomy.find((t) => t.id === subcategoryId);
        setRows((prev) =>
          prev.map((r) =>
            r.transaction_id === txnId
              ? {
                  ...r,
                  category_id: categoryId,
                  subcategory_id: subcategoryId,
                  category: parent?.label || (categoryId ? r.category : "Uncategorized"),
                  category_detail: child?.label || "—",
                  is_override: !!categoryId,
                  rule_id: categoryId ? null : r.rule_id,
                  rule_summary: categoryId ? null : r.rule_summary,
                }
              : r,
          ),
        );
        setExplain((prev) =>
          prev && prev.transaction_id === txnId
            ? {
                ...prev,
                category: parent?.label || prev.category,
                category_detail: child?.label || "—",
                is_override: !!categoryId,
              }
            : prev,
        );
      } catch (e) {
        console.error(e);
      }
    });
  }

  function runReapply() {
    startTransition(async () => {
      try {
        const r = await reapplyPlaidCategoriesAction();
        setReapplyMsg(`Reapplied: updated ${r.updated}, unchanged ${r.unchanged}`);
      } catch (e) {
        setReapplyMsg(e instanceof Error ? e.message : String(e));
      }
    });
  }

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
        meta: { filterable: true, wrap: true, maxWidth: 200, width: 160 },
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
        meta: { filterable: true, wrap: true, maxWidth: 420, width: 320 },
        cell: ({ row }) => {
          const title = row.original.transaction_name;
          const memo = row.original.bank_description;
          const showMemo = memo && memo !== title;
          const party = row.original.counterparty;
          const showParty = party && party !== title && (!memo || !memo.includes(party));
          return (
            <div className="flex flex-col gap-0.5 py-0.5">
              <span>{title}</span>
              {showParty ? (
                <span className="text-xs text-muted-foreground">Counterparty: {party}</span>
              ) : null}
              {showMemo ? (
                <span className="text-xs text-muted-foreground break-words whitespace-normal">
                  {memo}
                </span>
              ) : null}
            </div>
          );
        },
      },
      {
        accessorKey: "category",
        header: "Category",
        meta: { filterable: true, wrap: true, maxWidth: 180, width: 160 },
        cell: ({ row }) => {
          const code = row.original.category;
          if (!code || code === "—") return "Uncategorized";
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() => setExplain(row.original)}
            >
              {code}
              {row.original.is_override ? " *" : ""}
            </button>
          );
        },
      },
      {
        accessorKey: "category_detail",
        header: "Subcategory",
        meta: { filterable: true, wrap: true, maxWidth: 180, width: 160 },
        cell: ({ row }) => {
          const code = row.original.category_detail;
          if (!code || code === "—") return "—";
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() => setExplain(row.original)}
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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span>
            <span className="text-muted-foreground">Money in (bank) · </span>
            <span className="font-medium tabular-nums">{formatDollars(kpis.earned)}</span>
          </span>
          <span>
            <span className="text-muted-foreground">Money out (bank) · </span>
            <span className="font-medium tabular-nums">{formatDollars(kpis.spend)}</span>
          </span>
          <span>
            <span className="text-muted-foreground">Cash flow (bank) · </span>
            <span className="font-medium tabular-nums">{formatDollars(cashFlow)}</span>
          </span>
          <span>
            <span className="text-muted-foreground">Square net sales · </span>
            <span className="font-medium tabular-nums">
              {squareNetSalesTotal == null ? "—" : formatDollars(squareNetSalesTotal)}
            </span>
          </span>
          <span className="text-xs text-muted-foreground self-center">
            Bank totals exclude internal · {periodLabel}
          </span>
        </div>
        {unitToggle}
      </div>

      {cashChart.length ? (
        <BarChartCard
          title={`Cash flow by ${grainLabel}`}
          subtitle={
            asPct
              ? `Bank money in / out / cash flow as % of Square net sales for the same ${grainLabel} · green=in, red=out, sky/fuchsia=net`
              : "Green = money in · red = money out · sky/fuchsia = cash flow (by sign) · excludes internal"
          }
          data={cashChart}
          xKey="date"
          height={280}
          valueFormat={asPct ? "percent" : "dollars"}
          signedValueColors={{
            dataKey: "cash_flow",
            positive: ACCOUNTING_COLORS.cashFlowGain,
            negative: ACCOUNTING_COLORS.cashFlowLoss,
          }}
          series={[
            {
              key: "money_in",
              label: "Money in",
              color: ACCOUNTING_COLORS.moneyIn,
              stackId: "inout",
            },
            {
              key: "money_out",
              label: "Money out",
              color: ACCOUNTING_COLORS.moneyOut,
              stackId: "inout",
            },
            {
              key: "cash_flow",
              label: "Cash flow",
              color: ACCOUNTING_COLORS.cashFlowGain,
            },
          ]}
        />
      ) : null}

      {categoryChart.data.length && categoryChart.series.length ? (
        <BarChartCard
          title={`Spend by category by ${grainLabel}`}
          subtitle={
            asPct
              ? `Each spend category as % of Square net sales for the same ${grainLabel} · warm colors = money leaving (can sum over 100%)`
              : "Spend categories (warm palette = money leaving) · follows table filters · excludes internal"
          }
          data={categoryChart.data}
          xKey="date"
          stacked
          height={300}
          valueFormat={asPct ? "percent" : "dollars"}
          series={categoryChart.series}
        />
      ) : null}

      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Transactions
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            {canWrite ? (
              <>
                <AccountingRulesDrawer
                  canWrite={canWrite}
                  ruleCount={rules.length}
                  taxonomy={taxonomy}
                  rules={rules}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={runReapply}
                >
                  Reapply rules
                </Button>
              </>
            ) : null}
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={hideInternal}
                onChange={(e) => setHideInternal(e.target.checked)}
              />
              Hide internal
              {internalHidden ? ` (${internalHidden})` : ""}
            </label>
          </div>
        </CardHeader>
        <CardContent>
          {reapplyMsg ? (
            <p className="mb-2 text-xs text-muted-foreground">{reapplyMsg}</p>
          ) : null}
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
            <SheetTitle>{explain?.category || "Category"}</SheetTitle>
            <SheetDescription>
              Palmetto management taxonomy
              {explain?.is_override ? " · operator override" : ""}
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-3 px-4 pb-4">
            {explain ? (
              <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
                <p className="font-medium">{explain.transaction_name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{explain.account_last4}</p>
                {explain.counterparty ? (
                  <p className="mt-1 text-xs">
                    Counterparty: <span className="font-medium">{explain.counterparty}</span>
                  </p>
                ) : null}
                {explain.bank_description ? (
                  <p className="mt-2 text-xs break-words text-muted-foreground">
                    {explain.bank_description}
                  </p>
                ) : null}
              </div>
            ) : null}
            {explain?.category_detail && explain.category_detail !== "—" ? (
              <p className="text-sm">
                Subcategory: <span className="font-medium">{explain.category_detail}</span>
              </p>
            ) : null}
            {explain?.category_definition ? (
              <p className="text-sm text-muted-foreground">{explain.category_definition}</p>
            ) : (
              <p className="text-sm text-muted-foreground">
                No definition yet — edit taxonomy in Rules admin.
              </p>
            )}
            {explain?.rule_summary ? (
              <p className="text-sm">
                Matched rule: <span className="font-mono text-xs">{explain.rule_summary}</span>
              </p>
            ) : explain?.is_override ? (
              <p className="text-sm text-muted-foreground">Override — no rule applied.</p>
            ) : (
              <p className="text-sm text-muted-foreground">Uncategorized — no rule matched.</p>
            )}
            {canWrite && explain ? (
              <div className="flex flex-col gap-2 border-t pt-3">
                <p className="text-xs font-medium text-muted-foreground">Override category</p>
                <select
                  className="rounded border bg-background px-2 py-1.5 text-sm"
                  value={explain.category_id || ""}
                  disabled={pending}
                  onChange={(e) => {
                    const catId = e.target.value || null;
                    const firstChild =
                      taxonomy.find((t) => t.parent_id === catId)?.id ?? null;
                    applyOverride(explain.transaction_id, catId, firstChild);
                  }}
                >
                  <option value="">(clear override / use rules)</option>
                  {parents.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
                {explain.category_id ? (
                  <select
                    className="rounded border bg-background px-2 py-1.5 text-sm"
                    value={explain.subcategory_id || ""}
                    disabled={pending}
                    onChange={(e) => {
                      applyOverride(
                        explain.transaction_id,
                        explain.category_id,
                        e.target.value || null,
                      );
                    }}
                  >
                    <option value="">(no subcategory)</option>
                    {taxonomy
                      .filter((t) => t.parent_id === explain.category_id)
                      .map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.label}
                        </option>
                      ))}
                  </select>
                ) : null}
                {explain.is_override ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={pending}
                    onClick={() => applyOverride(explain.transaction_id, null, null)}
                  >
                    Clear override
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
