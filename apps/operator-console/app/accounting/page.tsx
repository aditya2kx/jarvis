import { laborDaily, plaidItems, plaidSpendByCategory, plaidTransactions } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, resolveRange, wantsCustom } from "@/lib/filters/range";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/tables/DataTable";
import { PlaidLinkButton } from "@/components/drawers/PlaidLinkButton";
import type { ColumnDef } from "@tanstack/react-table";
import type { PlaidSpendCategoryRow, PlaidTransactionRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

export default async function AccountingPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string }>;
}) {
  if (!FEATURES.accounting) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title="Accounting" subtitle="Feature flag off" />
        <p className="text-sm text-muted-foreground">
          Accounting is disabled via FEATURES.accounting.
        </p>
      </div>
    );
  }

  const sp = await searchParams;
  const win = resolveRange(sp.range, "30d", sp.from, sp.to);
  const showCustomPicker = wantsCustom(sp.range);

  let netSales: number | null = null;
  let spendTotal = 0;
  let categories: PlaidSpendCategoryRow[] = [];
  let txns: PlaidTransactionRow[] = [];
  let linked = false;
  let institution: string | null = null;
  let lastSynced: string | null = null;
  let error: string | undefined;

  try {
    const [labor, items, cats, transactions] = await Promise.all([
      laborDaily(win),
      plaidItems(DEFAULT_STORE),
      plaidSpendByCategory(win),
      plaidTransactions(win),
    ]);
    netSales = labor.length ? labor.reduce((s, r) => s + (r.net_sales ?? 0), 0) : null;
    categories = cats;
    spendTotal = cats.reduce((s, c) => s + (c.spend ?? 0), 0);
    txns = transactions;
    linked = items.length > 0;
    institution = items[0]?.institution_name ?? null;
    lastSynced = items[0]?.last_synced_at ?? null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const catColumns: ColumnDef<PlaidSpendCategoryRow>[] = [
    { accessorKey: "pfc_primary", header: "Plaid category" },
    { accessorKey: "spend", header: "Spend", meta: { format: { kind: "dollars" } } },
    { accessorKey: "txn_count", header: "Txns", meta: { format: { kind: "number" } } },
  ];

  const txnColumns: ColumnDef<PlaidTransactionRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    {
      id: "merchant",
      header: "Merchant / name",
      accessorFn: (r) => r.merchant_name || r.name || "—",
    },
    { accessorKey: "amount", header: "Amount", meta: { format: { kind: "dollars" } } },
    { accessorKey: "pfc_primary", header: "Category" },
    {
      id: "pending",
      header: "Pending",
      accessorFn: (r) => (r.pending ? "yes" : ""),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Accounting"
        subtitle={`Square money in · Plaid money out · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/accounting"
            />
            {showCustomPicker ? (
              <DateRangePicker basePath="/accounting" from={win.start} to={win.end} />
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — expected locally without ADC/BQ; deployed
          behind IAP this reads live. Plaid tables need migration 036 applied.
        </p>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">Bank link</CardTitle>
          {linked ? (
            <span className="text-xs text-muted-foreground">
              {institution || "Linked"}
              {lastSynced ? ` · last sync ${lastSynced}` : ""}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">Not linked</span>
          )}
        </CardHeader>
        <CardContent>
          {FEATURES.writePlaidLink ? <PlaidLinkButton linked={linked} /> : (
            <p className="text-sm text-muted-foreground">Plaid Link writes disabled.</p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Categories are Plaid PFC v2 (interim). Management taxonomy + overrides are a follow-up.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
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
            <p className="text-xs text-muted-foreground">{win.label}</p>
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
              {spendTotal.toLocaleString("en-US", { style: "currency", currency: "USD" })}
            </p>
            <p className="text-xs text-muted-foreground">Outflows only · {win.label}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Spend by Plaid category
          </CardTitle>
        </CardHeader>
        <CardContent>
          {categories.length ? (
            <DataTable columns={catColumns} data={categories} />
          ) : (
            <p className="text-sm text-muted-foreground">No Plaid spend in this period.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Transactions
          </CardTitle>
        </CardHeader>
        <CardContent>
          {txns.length ? (
            <DataTable columns={txnColumns} data={txns} />
          ) : (
            <p className="text-sm text-muted-foreground">No transactions in this period.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
