import { laborDaily, plaidItems, plaidTransactions } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, wantsCustom } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlaidLinkButton } from "@/components/drawers/PlaidLinkButton";
import {
  AccountingLedger,
  type AccountingTxnRow,
} from "@/components/accounting/AccountingLedger";

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
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const showCustomPicker = wantsCustom(sp.range);

  let netSales: number | null = null;
  let txns: Awaited<ReturnType<typeof plaidTransactions>> = [];
  let linked = false;
  let institution: string | null = null;
  let lastSynced: string | null = null;
  let error: string | undefined;

  try {
    const [labor, items, transactions] = await Promise.all([
      laborDaily(win),
      plaidItems(DEFAULT_STORE),
      plaidTransactions(win),
    ]);
    netSales = labor.length ? labor.reduce((s, r) => s + (r.net_sales ?? 0), 0) : null;
    txns = transactions;
    linked = items.length > 0;
    institution = items[0]?.institution_name ?? null;
    lastSynced = items[0]?.last_synced_at ?? null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  // Precompute display fields — DataTable/ledger is a client component and
  // cannot receive accessorFn from this server page (Next RSC serialization).
  // Plaid amount: positive = money out (spend), negative = money in (earned).
  const txnRows: AccountingTxnRow[] = txns.map((t) => {
    const amount = t.amount ?? 0;
    const mask = t.account_mask?.trim() || "";
    const kind =
      t.account_type === "credit"
        ? "Card"
        : t.account_type === "depository"
          ? "Bank"
          : t.account_type || "";
    const accountLast4 = mask
      ? `${kind ? `${kind} ` : ""}•••• ${mask}`
      : "—";
    const isInternal = Boolean(t.is_internal);
    return {
      transaction_id: t.transaction_id,
      date: t.date,
      transaction_name: t.merchant_name || t.name || "—",
      account_last4: accountLast4,
      spend: amount > 0 ? amount : null,
      earned: amount < 0 ? Math.abs(amount) : null,
      category: t.pfc_primary || "—",
      category_detail: t.pfc_detailed || "—",
      channel: t.payment_channel || "—",
      pending_label: t.pending ? "yes" : "no",
      amount,
      is_internal: isInternal,
      internal_label: isInternal ? "yes" : "no",
    };
  });

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
          behind IAP this reads live. Plaid tables need migration 037+ applied.
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
            Categories are Plaid PFC v2 (interim). Click a category for the definition. Mark
            checking↔card payments as Internal so Money out is not double-counted. Filter any
            column — Plaid KPI cards follow the table.
          </p>
        </CardContent>
      </Card>

      <AccountingLedger
        netSales={netSales}
        periodLabel={win.label}
        rows={txnRows}
        canWrite={FEATURES.writePlaidLink}
      />
    </div>
  );
}
