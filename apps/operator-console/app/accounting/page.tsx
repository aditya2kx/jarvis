import {
  laborByGrain,
  plaidCategoryRules,
  plaidItems,
  plaidTaxonomyNodes,
  plaidTransactions,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, truncateToGrain, wantsCustom } from "@/lib/filters/range";
import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlaidLinkButton } from "@/components/drawers/PlaidLinkButton";
import {
  AccountingLedger,
  type AccountingTxnRow,
} from "@/components/accounting/AccountingLedger";
import { dateSortKey, formatDollars } from "@/lib/format";
import { effectiveExcludeFromMap } from "@/lib/plaid/exclude-accounting";

export const dynamic = "force-dynamic";

export default async function AccountingPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string; grain?: string }>;
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
  const grain = await resolvePageGrain(sp.grain);
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> =
    win.preset === "custom" ? { from: win.start, to: win.end } : {};

  let txns: Awaited<ReturnType<typeof plaidTransactions>> = [];
  let taxonomy: Awaited<ReturnType<typeof plaidTaxonomyNodes>> = [];
  let rules: Awaited<ReturnType<typeof plaidCategoryRules>> = [];
  let squareByIso: Record<string, number> = {};
  let squareTotal: number | null = null;
  let linked = false;
  let institution: string | null = null;
  let lastSynced: string | null = null;
  let error: string | undefined;

  try {
    const [items, transactions, nodes, ruleRows, labor] = await Promise.all([
      plaidItems(DEFAULT_STORE),
      plaidTransactions(win),
      plaidTaxonomyNodes().catch(() => []),
      plaidCategoryRules().catch(() => []),
      laborByGrain(win, grain).catch(() => []),
    ]);
    txns = transactions;
    taxonomy = nodes;
    rules = ruleRows;
    linked = items.length > 0;
    institution = items[0]?.institution_name ?? null;
    lastSynced = items[0]?.last_synced_at ?? null;
    for (const r of labor) {
      const raw = typeof r.date === "string" ? r.date : dateSortKey(r.date);
      const iso = truncateToGrain(raw.slice(0, 10), grain);
      squareByIso[iso] = (squareByIso[iso] ?? 0) + (Number(r.net_sales) || 0);
    }
    squareTotal = labor.length
      ? labor.reduce((s, r) => s + (r.net_sales ?? 0), 0)
      : null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const taxNodes = taxonomy.map((n) => ({
    id: n.id,
    parent_id: n.parent_id,
    exclude_from_accounting: n.exclude_from_accounting ?? null,
  }));

  const txnRows: AccountingTxnRow[] = txns.map((t) => {
    const amount = t.amount ?? 0;
    const mask = t.account_mask?.trim() || "";
    const acctName = t.account_name?.trim() || "";
    const kind =
      t.account_type === "credit"
        ? "Card"
        : t.account_type === "depository"
          ? "Bank"
          : t.account_type || "";
    const accountLast4 = mask
      ? `${kind ? `${kind} ` : ""}•••• ${mask}${acctName ? ` · ${acctName}` : ""}`
      : acctName || "—";
    const isInternal = Boolean(t.is_internal);
    const isOverride = Boolean(t.override_category_id);
    const categoryLabel = t.category_label || "Uncategorized";
    const subcategoryLabel = t.subcategory_label || "—";
    const bankDescription = (t.name || "").trim() || null;
    const merchant = (t.merchant_name || "").trim() || null;
    const counterparty = (t.counterparty_name || "").trim() || null;
    // Prefer merchant for the short title; always keep full bank memo for the table.
    const transactionName = merchant || bankDescription || counterparty || "—";
    const ruleSummary =
      t.rule_id && t.rule_pattern
        ? `#${t.rule_priority ?? "?"} ${t.rule_id}: ${t.rule_operator || "contains"} '${t.rule_pattern}'`
        : t.rule_id
          ? t.rule_id
          : null;
    const leafId =
      t.override_subcategory_id ||
      t.subcategory_id ||
      t.override_category_id ||
      t.category_id;
    const excluded =
      effectiveExcludeFromMap(leafId, taxNodes) ||
      (isInternal && (!leafId || leafId === "internal_transfers"));
    return {
      transaction_id: t.transaction_id,
      date: t.date,
      transaction_name: transactionName,
      bank_description: bankDescription,
      counterparty,
      account_last4: accountLast4,
      spend: amount > 0 ? amount : null,
      earned: amount < 0 ? Math.abs(amount) : null,
      category: categoryLabel,
      category_detail: subcategoryLabel,
      channel: t.payment_channel || "—",
      pending_label: t.pending ? "yes" : "no",
      amount,
      excluded,
      excluded_label: excluded ? "yes" : "no",
      is_internal: isInternal,
      internal_label: isInternal ? "yes" : "no",
      category_id: t.override_category_id || t.category_id,
      subcategory_id: t.override_subcategory_id || t.subcategory_id,
      rule_id: isOverride ? null : t.rule_id,
      is_override: isOverride,
      category_definition: t.category_definition,
      rule_summary: ruleSummary,
    };
  });

  const taxonomyOpts = taxonomy.map((n) => ({
    id: n.id,
    parent_id: n.parent_id,
    label: n.label,
    exclude_from_accounting: n.exclude_from_accounting ?? null,
  }));

  const ruleList = rules.map((r) => ({
    id: r.id,
    priority: r.priority,
    match_operator: r.match_operator,
    match_pattern: r.match_pattern,
    amount_sign: r.amount_sign,
    enabled: r.enabled,
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Accounting"
        subtitle={`Linked bank accounts · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <AggregationSelect
              value={grain}
              basePath="/accounting"
              extraParams={{ range: win.preset, ...dateParams }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/accounting"
              extraParams={{ grain }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/accounting"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
                extraParams={{ grain }}
              />
            ) : null}
          </>
        }
      />

      <div
        role="note"
        className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">Bank feed</span> (checking + card) drives
          money in / out / cash flow and the transaction ledger.{" "}
          <span className="font-medium text-foreground">Square net sales</span> is POS sales from
          BHAGA for the same period — used as the denominator when you toggle charts to{" "}
          <span className="font-medium text-foreground">% of Square net sales</span>. Bank deposits
          and Square sales often diverge (settlement lag, tips, tax, marketplace netting); the %
          view is for spotting those gaps, not a P&amp;L.
        </p>
        {squareTotal != null ? (
          <p className="mt-1 text-xs">
            This period Square net sales: {formatDollars(squareTotal)}
          </p>
        ) : null}
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — expected locally without ADC/BQ; deployed
          behind IAP this reads live. Plaid tables need migration 037+ / 045+ applied.
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
            Categories are Palmetto taxonomy (Copilot rules). Click a category for definition +
            matched rule; Propose rule from the sheet to backfill history. Sync / Reapply keeps
            historical + new txns categorized. Put transfers / Personal under a category with
            Exclude from accounting so Money out is not inflated.
          </p>
        </CardContent>
      </Card>

      <AccountingLedger
        periodLabel={win.label}
        grain={grain}
        rows={txnRows}
        canWrite={FEATURES.writePlaidLink}
        taxonomy={taxonomyOpts}
        rules={ruleList}
        squareNetSalesTotal={squareTotal}
        squareNetSalesByIso={squareByIso}
      />
    </div>
  );
}
