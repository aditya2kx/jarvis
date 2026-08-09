"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  setTxnCategoryOverrideAction,
  previewRuleMatchesAction,
  commitRuleFromTxnAction,
  revertRuleEvidenceAction,
  setTaxonomyExcludeAction,
  upsertTaxonomyNodeAction,
  type RuleMatchPreview,
} from "@/app/accounting/actions";
import { formatDollars } from "@/lib/format";
import { formatBucket, grainDisplayLabel, truncateToGrain, type Grain } from "@/lib/filters/range";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { ACCOUNTING_COLORS, expenseCategoryColor } from "@/lib/charts/palette";
import { cn } from "@/lib/utils";
import { effectiveExclude } from "@/lib/plaid/exclude-accounting";
import { patchTxnCategory } from "@/lib/plaid/patch-txn-category";
import {
  AccountingRulesDrawer,
  type RuleListItem,
} from "@/components/accounting/AccountingRulesDrawer";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { okAck } from "@/lib/actions/types";

export interface AccountingTxnRow {
  transaction_id: string;
  date: string;
  transaction_name: string;
  /** Full Plaid/bank memo (ACH ORIG CO NAME / TRACE / IND NAME, etc.). */
  bank_description: string | null;
  counterparty: string | null;
  account_last4: string;
  /** Directional from account (linked or counterparty). */
  from_account: string;
  /** Directional to account (linked or counterparty). */
  to_account: string;
  from_mask: string | null;
  to_mask: string | null;
  spend: number | null;
  earned: number | null;
  category: string;
  category_detail: string;
  channel: string;
  pending_label: string;
  amount: number;
  excluded: boolean;
  excluded_label: string;
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
  exclude_from_accounting: boolean | null;
}

const TOP_CATEGORY_SERIES = 8;

export type ChartUnit = "dollars" | "pct_net_sales";

function moneyTotals(rows: AccountingTxnRow[]): { spend: number; earned: number } {
  let spend = 0;
  let earned = 0;
  for (const r of rows) {
    if (r.excluded) continue;
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
    if (r.excluded) continue;
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

type SpendChartGrain = "category" | "subcategory";

function spendSeriesLabel(row: AccountingTxnRow, grain: SpendChartGrain): string {
  const cat = row.category || "Uncategorized";
  if (grain === "category") return cat;
  const sub = row.category_detail;
  if (!sub || sub === "—") return `${cat} · (no subcategory)`;
  return `${cat} · ${sub}`;
}

function spendByCategoryGrain(
  rows: AccountingTxnRow[],
  grain: Grain,
  seriesGrain: SpendChartGrain = "category",
): {
  data: { iso: string; date: string; values: Record<string, number> }[];
  series: { key: string; label: string; color: string }[];
} {
  const totals = new Map<string, number>();
  const byBucket = new Map<string, Map<string, number>>();

  for (const r of rows) {
    if (r.excluded) continue;
    if (!(typeof r.spend === "number" && r.spend > 0)) continue;
    const cat = spendSeriesLabel(r, seriesGrain);
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

function resetRuleState(row: AccountingTxnRow) {
  return {
    pattern:
      row.transaction_name && row.transaction_name !== "—" ? row.transaction_name : "",
    amountSign: row.amount > 0 ? "positive" : "negative",
    fromMask: row.from_mask || "",
    toMask: row.to_mask || "",
    matches: [] as RuleMatchPreview[],
    selectedIds: new Set<string>(),
    committedRuleId: null as string | null,
    msg: null as string | null,
    applyFuture: true,
  };
}

/** Stable pin list — inline arrays re-create metaPinOffsets every render. */
const ACCOUNTING_PIN_LEFT = [
  "date",
  "from_account",
  "to_account",
  "spend",
  "earned",
] as const;

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
  const [rows, setRows] = useState(() => {
    const seen = new Set<string>();
    return initialRows.filter((r) => {
      if (seen.has(r.transaction_id)) return false;
      seen.add(r.transaction_id);
      return true;
    });
  });
  const [filtered, setFiltered] = useState(() => {
    const seen = new Set<string>();
    return initialRows.filter((r) => {
      if (seen.has(r.transaction_id)) return false;
      seen.add(r.transaction_id);
      return true;
    });
  });
  const [taxonomyState, setTaxonomy] = useState(taxonomy);
  const [explain, setExplain] = useState<AccountingTxnRow | null>(null);
  const { isPending: pending, stage, error, run } = useConsoleAction();
  const [reapplyMsg, setReapplyMsg] = useState<string | null>(null);
  const [chartUnit, setChartUnit] = useState<ChartUnit>("dollars");
  const [spendChartGrain, setSpendChartGrain] = useState<SpendChartGrain>("category");

  const [rulePattern, setRulePattern] = useState("");
  const [ruleAmountSign, setRuleAmountSign] = useState("positive");
  const [ruleFromMask, setRuleFromMask] = useState("");
  const [ruleToMask, setRuleToMask] = useState("");
  const [ruleMatches, setRuleMatches] = useState<RuleMatchPreview[]>([]);
  const [selectedTxnIds, setSelectedTxnIds] = useState<Set<string>>(new Set());
  const [applyFuture, setApplyFuture] = useState(true);
  const [committedRuleId, setCommittedRuleId] = useState<string | null>(null);
  const [ruleMsg, setRuleMsg] = useState<string | null>(null);

  useEffect(() => {
    const seen = new Set<string>();
    const unique = initialRows.filter((r) => {
      if (seen.has(r.transaction_id)) return false;
      seen.add(r.transaction_id);
      return true;
    });
    setRows(unique);
    setFiltered(unique);
  }, [initialRows]);

  useEffect(() => {
    setTaxonomy(taxonomy);
  }, [taxonomy]);

  const tableData = useMemo(() => {
    const seen = new Set<string>();
    return rows.filter((r) => {
      if (seen.has(r.transaction_id)) return false;
      seen.add(r.transaction_id);
      return true;
    });
  }, [rows]);

  const onFilteredRowsChange = useCallback((next: AccountingTxnRow[]) => {
    setFiltered((prev) => {
      if (
        prev.length === next.length &&
        prev.every((r, i) => r.transaction_id === next[i]?.transaction_id && r === next[i])
      ) {
        return prev;
      }
      return next;
    });
  }, []);

  const chartRows = useMemo(() => {
    const visibleIds = new Set(filtered.map((r) => r.transaction_id));
    return tableData.filter((r) => !r.excluded && visibleIds.has(r.transaction_id));
  }, [tableData, filtered]);

  const kpis = useMemo(() => moneyTotals(chartRows), [chartRows]);
  const cashFlow = kpis.earned - kpis.spend;
  const cashChartBase = useMemo(() => cashFlowByGrain(chartRows, grain), [chartRows, grain]);
  const categoryChartBase = useMemo(
    () => spendByCategoryGrain(chartRows, grain, spendChartGrain),
    [chartRows, grain, spendChartGrain],
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

  const grainLabel = grainDisplayLabel(grain);
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
    () => taxonomyState.filter((t) => !t.parent_id),
    [taxonomyState],
  );

  const openExplain = useCallback((row: AccountingTxnRow) => {
    const reset = resetRuleState(row);
    setExplain(row);
    setRulePattern(reset.pattern);
    setRuleAmountSign(reset.amountSign);
    setRuleFromMask(reset.fromMask);
    setRuleToMask(reset.toMask);
    setRuleMatches(reset.matches);
    setSelectedTxnIds(reset.selectedIds);
    setCommittedRuleId(reset.committedRuleId);
    setRuleMsg(reset.msg);
    setApplyFuture(reset.applyFuture);
  }, []);

  const ruleHasCriteria = Boolean(
    rulePattern.trim() || ruleFromMask.replace(/\D/g, "").slice(-4) || ruleToMask.replace(/\D/g, "").slice(-4),
  );

  function resolveExcludedWith(
    nodes: TaxonomyOption[],
    categoryId: string | null,
    subcategoryId: string | null,
  ): boolean {
    const leafId = subcategoryId || categoryId;
    if (!leafId) return false;
    const leaf = nodes.find((t) => t.id === leafId);
    const parent = leaf?.parent_id
      ? nodes.find((t) => t.id === leaf.parent_id)
      : undefined;
    return effectiveExclude(
      leaf
        ? {
            id: leaf.id,
            parent_id: leaf.parent_id,
            exclude_from_accounting: leaf.exclude_from_accounting,
          }
        : null,
      parent
        ? {
            id: parent.id,
            parent_id: parent.parent_id,
            exclude_from_accounting: parent.exclude_from_accounting,
          }
        : null,
    );
  }

  function applyOverride(txnId: string, categoryId: string | null, subcategoryId: string | null) {
    void run(async () => {
      const ack = await setTxnCategoryOverrideAction(txnId, categoryId, subcategoryId);
      if (!ack.ok) return ack;
      const parent = taxonomyState.find((t) => t.id === categoryId);
      const child = taxonomyState.find((t) => t.id === subcategoryId);
      const excluded = resolveExcludedWith(taxonomyState, categoryId, subcategoryId);
      const subcategoryLabel = child?.label || "—";
      setRows((prev) =>
        prev.map((r) =>
          r.transaction_id === txnId
            ? patchTxnCategory(r, {
                categoryId,
                subcategoryId,
                categoryLabel: parent?.label || (categoryId ? r.category : "Uncategorized"),
                subcategoryLabel,
                excluded,
                isOverride: !!categoryId,
              })
            : r,
        ),
      );
      setExplain((prev) =>
        prev && prev.transaction_id === txnId
          ? patchTxnCategory(prev, {
              categoryId,
              subcategoryId,
              categoryLabel: parent?.label || (categoryId ? prev.category : "Uncategorized"),
              subcategoryLabel,
              excluded,
              isOverride: !!categoryId,
            })
          : prev,
      );
      return okAck({ message: "Category updated." });
    });
  }

  function setCategoryExcluded(exclude: boolean) {
    if (!explain?.category_id) return;
    const catId = explain.category_id;
    void run(async () => {
      const ack = await setTaxonomyExcludeAction(catId, exclude);
      if (!ack.ok) return ack;
      const nextTax = taxonomyState.map((t) =>
        t.id === catId ? { ...t, exclude_from_accounting: exclude } : t,
      );
      setTaxonomy(nextTax);
      setRows((prev) =>
        prev.map((r) => {
          const nextExcluded = resolveExcludedWith(nextTax, r.category_id, r.subcategory_id);
          if (r.excluded === nextExcluded) return r;
          return {
            ...r,
            excluded: nextExcluded,
            excluded_label: nextExcluded ? "yes" : "no",
          };
        }),
      );
      setExplain((prev) => {
        if (!prev) return prev;
        const nextExcluded = resolveExcludedWith(
          nextTax,
          prev.category_id,
          prev.subcategory_id,
        );
        return {
          ...prev,
          excluded: nextExcluded,
          excluded_label: nextExcluded ? "yes" : "no",
        };
      });
      const message = exclude
        ? "Category excluded from accounting rollups"
        : "Category included in accounting rollups";
      setRuleMsg(message);
      return okAck({ message });
    });
  }

  function ensurePersonalAndAssign() {
    if (!explain) return;
    const txnId = explain.transaction_id;
    void run(async () => {
      const id = "personal";
      const upsertAck = await upsertTaxonomyNodeAction({
        id,
        parent_id: null,
        slug: "personal",
        label: "Personal",
        enabled: true,
        exclude_from_accounting: true,
      });
      if (!upsertAck.ok) return upsertAck;
      setTaxonomy((prev) => {
        if (prev.some((t) => t.id === id)) {
          return prev.map((t) =>
            t.id === id
              ? { ...t, exclude_from_accounting: true, label: "Personal" }
              : t,
          );
        }
        return [
          ...prev,
          {
            id,
            parent_id: null,
            label: "Personal",
            exclude_from_accounting: true,
          },
        ];
      });
      const overrideAck = await setTxnCategoryOverrideAction(txnId, id, null);
      if (!overrideAck.ok) return overrideAck;
      setRows((prev) =>
        prev.map((r) =>
          r.transaction_id === txnId
            ? {
                ...r,
                category_id: id,
                subcategory_id: null,
                category: "Personal",
                category_detail: "—",
                is_override: true,
                rule_id: null,
                rule_summary: null,
                excluded: true,
                excluded_label: "yes",
              }
            : r,
        ),
      );
      setExplain((prev) =>
        prev && prev.transaction_id === txnId
          ? {
              ...prev,
              category_id: id,
              subcategory_id: null,
              category: "Personal",
              category_detail: "—",
              is_override: true,
              excluded: true,
              excluded_label: "yes",
            }
          : prev,
      );
      setRuleMsg("Assigned to Personal (excluded from accounting)");
      return okAck({ message: "Assigned to Personal (excluded from accounting)" });
    });
  }

  function runReapply() {
    void run(async () => {
      const ack = await reapplyPlaidCategoriesAction();
      if (!ack.ok) return ack;
      const r = ack.data!;
      setReapplyMsg(`Reapplied: updated ${r.updated}, unchanged ${r.unchanged}`);
      return okAck({
        message: `Reapplied: updated ${r.updated}, unchanged ${r.unchanged}`,
      });
    });
  }

  function findRuleMatches() {
    if (!explain?.category_id) {
      setRuleMsg("Select a category first");
      return;
    }
    void run(async () => {
      const ack = await previewRuleMatchesAction({
        match_pattern: rulePattern,
        match_operator: rulePattern.trim() ? "regex" : "contains",
        amount_sign: ruleAmountSign,
        from_mask: ruleFromMask || null,
        to_mask: ruleToMask || null,
        category_id: explain.category_id!,
        subcategory_id: explain.subcategory_id,
      });
      if (!ack.ok) return ack;
      const matches = ack.data!;
      // Dedupe by transaction_id (BQ race can still leave extras until Cloud Run
      // picks up post-sync dedupe — Issue #230). Avoids React duplicate-key on <li>.
      const seen = new Set<string>();
      const unique = matches.filter((m) => {
        if (seen.has(m.transaction_id)) return false;
        seen.add(m.transaction_id);
        return true;
      });
      setRuleMatches(unique);
      setSelectedTxnIds(
        new Set(unique.filter((m) => !m.has_override).map((m) => m.transaction_id)),
      );
      const message = unique.length ? `${unique.length} match(es)` : "No matches";
      setRuleMsg(message);
      setCommittedRuleId(null);
      return okAck({ message });
    });
  }

  function toggleMatch(txnId: string, checked: boolean) {
    setSelectedTxnIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(txnId);
      else next.delete(txnId);
      return next;
    });
  }

  function selectAllMatches() {
    setSelectedTxnIds(
      new Set(ruleMatches.filter((m) => !m.has_override).map((m) => m.transaction_id)),
    );
  }

  function commitRule() {
    if (!explain?.category_id) return;
    const catId = explain.category_id;
    const subId = explain.subcategory_id;
    const selected = new Set(selectedTxnIds);
    void run(async () => {
      const ack = await commitRuleFromTxnAction({
        draft: {
          match_pattern: rulePattern,
          match_operator: rulePattern.trim() ? "regex" : "contains",
          amount_sign: ruleAmountSign,
          from_mask: ruleFromMask || null,
          to_mask: ruleToMask || null,
          category_id: catId,
          subcategory_id: subId,
        },
        selectedTxnIds: [...selected],
        applyFuture,
      });
      if (!ack.ok) return ack;
      const result = ack.data!;
      setCommittedRuleId(result.ruleId);
      const parent = taxonomyState.find((t) => t.id === catId);
      const child = taxonomyState.find((t) => t.id === subId);
      const excluded = resolveExcludedWith(taxonomyState, catId, subId);
      const ruleSummary = `#? ${result.ruleId}: contains '${rulePattern.trim()}'`;
      // Optimistic ledger refresh for applied matches (skip overrides).
      setRows((prev) =>
        prev.map((r) =>
          selected.has(r.transaction_id) && !r.is_override
            ? patchTxnCategory(r, {
                categoryId: catId,
                subcategoryId: subId,
                categoryLabel: parent?.label || r.category,
                subcategoryLabel: child?.label || "—",
                excluded,
                ruleId: result.ruleId,
                ruleSummary,
                isOverride: false,
              })
            : r,
        ),
      );
      const message = `Rule ${result.ruleId}: applied ${result.applied}, skipped override ${result.skipped_override}`;
      setRuleMsg(message);
      return okAck({ message });
    });
  }

  function revertRule() {
    if (!committedRuleId) return;
    void run(async () => {
      const ack = await revertRuleEvidenceAction(committedRuleId);
      if (!ack.ok) return ack;
      const result = ack.data!;
      const message = `Reverted ${committedRuleId}: reapply updated ${result.reapply.updated}, unchanged ${result.reapply.unchanged}`;
      setRuleMsg(message);
      setCommittedRuleId(null);
      return okAck({ message });
    });
  }

  const selectableMatchCount = ruleMatches.filter((m) => !m.has_override).length;

  const getTxnRowId = useCallback((r: AccountingTxnRow) => r.transaction_id, []);

  const columns: ColumnDef<AccountingTxnRow>[] = useMemo(
    () => [
      {
        accessorKey: "date",
        header: "Date",
        meta: { format: { kind: "date" }, filterable: true, width: 88 },
      },
      {
        accessorKey: "from_account",
        header: "From",
        meta: { filterable: true, filterVariant: "multi", wrap: true, maxWidth: 180, width: 140 },
      },
      {
        accessorKey: "to_account",
        header: "To",
        meta: { filterable: true, filterVariant: "multi", wrap: true, maxWidth: 180, width: 140 },
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
            <button
              type="button"
              className="flex w-full flex-col gap-0.5 py-0.5 text-left hover:text-foreground"
              onClick={() => openExplain(row.original)}
            >
              <span className="underline decoration-dotted underline-offset-2">{title}</span>
              {showParty ? (
                <span className="text-xs text-muted-foreground">Counterparty: {party}</span>
              ) : null}
              {showMemo ? (
                <span className="text-xs text-muted-foreground break-words whitespace-normal">
                  {memo}
                </span>
              ) : null}
            </button>
          );
        },
      },
      {
        accessorKey: "category",
        header: "Category",
        meta: { filterable: true, filterVariant: "multi", wrap: true, maxWidth: 180, width: 160 },
        cell: ({ row }) => {
          const code = row.original.category;
          const label = !code || code === "—" ? "Uncategorized" : code;
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() => openExplain(row.original)}
            >
              {label}
              {row.original.is_override ? " *" : ""}
            </button>
          );
        },
      },
      {
        accessorKey: "category_detail",
        header: "Subcategory",
        meta: { filterable: true, filterVariant: "multi", wrap: true, maxWidth: 180, width: 160 },
        cell: ({ row }) => {
          const code = row.original.category_detail;
          const label = !code || code === "—" ? "—" : code;
          return (
            <button
              type="button"
              className="text-left underline decoration-dotted underline-offset-2 hover:text-foreground"
              onClick={() => openExplain(row.original)}
            >
              {label}
            </button>
          );
        },
      },
      {
        accessorKey: "channel",
        header: "Channel",
        meta: { filterable: true, filterVariant: "multi", width: 88 },
      },
      {
        accessorKey: "pending_label",
        header: "Pending",
        meta: { filterable: true, filterVariant: "multi", width: 80 },
      },
      {
        accessorKey: "excluded_label",
        header: "Excluded",
        meta: { filterable: true, filterVariant: "multi", width: 80 },
      },
    ],
    [openExplain],
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
            Bank totals exclude excluded categories · {periodLabel}
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
              : "Green = money in · red = money out · sky/fuchsia = cash flow (by sign) · excludes excluded categories"
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
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium text-muted-foreground">
              Spend by {spendChartGrain === "subcategory" ? "subcategory" : "category"} · follows
              table filters
            </p>
            <div className="flex items-center gap-1 rounded-md bg-secondary p-0.5">
              {(
                [
                  { value: "category" as const, label: "Categories" },
                  { value: "subcategory" as const, label: "Subcategories" },
                ] as const
              ).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
                    spendChartGrain === opt.value
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => setSpendChartGrain(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          <BarChartCard
            title={`Spend by ${spendChartGrain === "subcategory" ? "subcategory" : "category"} by ${grainLabel}`}
            subtitle={
              asPct
                ? `Each ${spendChartGrain === "subcategory" ? "subcategory" : "category"} as % of Square net sales for the same ${grainLabel} · follows ledger filters · warm = money leaving`
                : `Stacked spend · follows ledger filters · excludes excluded categories · top ${TOP_CATEGORY_SERIES} + Other`
            }
            data={categoryChart.data}
            xKey="date"
            stacked
            height={300}
            valueFormat={asPct ? "percent" : "dollars"}
            series={categoryChart.series}
          />
        </div>
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
                    taxonomy={taxonomyState}
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
          </div>
        </CardHeader>
        <CardContent>
          {error || reapplyMsg || stage ? (
            <p className={`mb-2 text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {error || reapplyMsg || stage}
            </p>
          ) : null}
          {tableData.length ? (
            <DataTable
              columns={columns}
              data={tableData}
              enableColumnFilters
              getRowId={getTxnRowId}
              onFilteredRowsChange={onFilteredRowsChange}
              initialSorting={[{ id: "date", desc: true }]}
              pinLeft={ACCOUNTING_PIN_LEFT}
              rowHighlight={{
                accessorKey: "excluded_label",
                equals: "yes",
                className: "opacity-60",
              }}
            />          ) : (
            <p className="text-sm text-muted-foreground">No transactions in this period.</p>
          )}
        </CardContent>
      </Card>

      <Sheet
        open={!!explain}
        onOpenChange={(open) => {
          if (!open) setExplain(null);
        }}
      >
        <SheetContent side="right" className="sm:max-w-md overflow-y-auto">
          <SheetHeader>
            <SheetTitle>
              {explain?.transaction_name || explain?.category || "Transaction"}
            </SheetTitle>
            <SheetDescription>
              Change category, exclude from accounting, or propose a rule to backfill matches
              {explain?.is_override ? " · operator override" : ""}
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-3 px-4 pb-4">
            {explain ? (
              <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
                <p className="font-medium">{explain.transaction_name}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  From: {explain.from_account}
                  <br />
                  To: {explain.to_account}
                </p>
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
                <p className="mt-2 text-xs">
                  Currently:{" "}
                  <span className="font-medium">{explain.category || "Uncategorized"}</span>
                  {explain.category_detail && explain.category_detail !== "—"
                    ? ` · ${explain.category_detail}`
                    : ""}
                  {explain.excluded ? " · excluded from rollups" : ""}
                </p>
              </div>
            ) : null}
            {explain?.category_definition ? (
              <p className="text-sm text-muted-foreground">{explain.category_definition}</p>
            ) : null}
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
                <p className="text-xs font-medium text-muted-foreground">Category</p>
                <select
                  className="rounded border bg-background px-2 py-1.5 text-sm"
                  value={explain.category_id || ""}
                  disabled={pending}
                  onChange={(e) => {
                    const catId = e.target.value || null;
                    const firstChild =
                      taxonomyState.find((t) => t.parent_id === catId)?.id ?? null;
                    applyOverride(explain.transaction_id, catId, firstChild);
                  }}
                >
                  <option value="">(clear override / use rules)</option>
                  {parents.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                      {p.exclude_from_accounting === true ? " (excluded)" : ""}
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
                    {taxonomyState
                      .filter((t) => t.parent_id === explain.category_id)
                      .map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.label}
                        </option>
                      ))}
                  </select>
                ) : null}
                {explain.category_id ? (
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={
                        taxonomyState.find((t) => t.id === explain.category_id)
                          ?.exclude_from_accounting === true
                      }
                      disabled={pending}
                      onChange={(e) => setCategoryExcluded(e.target.checked)}
                    />
                    Exclude this category from accounting rollups
                  </label>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={ensurePersonalAndAssign}
                >
                  Mark as Personal (excluded)
                </Button>
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
            {canWrite && explain ? (
              <div className="flex flex-col gap-2 border-t pt-3">
                <p className="text-xs font-medium text-muted-foreground">Propose rule</p>
                <p className="text-[11px] text-muted-foreground">
                  At least one of: name regex, from last-4, to last-4. Empty fields are ignored.
                </p>
                <input
                  type="text"
                  className="rounded border bg-background px-2 py-1.5 text-sm"
                  placeholder="Name regex (optional)"
                  value={rulePattern}
                  disabled={pending || committedRuleId != null}
                  onChange={(e) => setRulePattern(e.target.value)}
                />
                <div className="flex gap-2">
                  <select
                    className="rounded border bg-background px-2 py-1.5 text-sm"
                    value={ruleAmountSign}
                    disabled={pending || committedRuleId != null}
                    onChange={(e) => setRuleAmountSign(e.target.value)}
                  >
                    <option value="any">Any sign</option>
                    <option value="positive">Positive</option>
                    <option value="negative">Negative</option>
                  </select>
                  <input
                    type="text"
                    className="rounded border bg-background px-2 py-1.5 text-sm w-24"
                    placeholder="From last4"
                    value={ruleFromMask}
                    disabled={pending || committedRuleId != null}
                    onChange={(e) => setRuleFromMask(e.target.value)}
                  />
                  <input
                    type="text"
                    className="rounded border bg-background px-2 py-1.5 text-sm w-24"
                    placeholder="To last4"
                    value={ruleToMask}
                    disabled={pending || committedRuleId != null}
                    onChange={(e) => setRuleToMask(e.target.value)}
                  />
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending || !ruleHasCriteria || committedRuleId != null}
                  onClick={findRuleMatches}
                >
                  Find matches
                </Button>
                {ruleMatches.length ? (
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center justify-between">
                      <p className="text-xs text-muted-foreground">
                        {ruleMatches.length} match(es) · {selectedTxnIds.size} selected
                      </p>
                      {selectableMatchCount ? (
                        <button
                          type="button"
                          className="text-xs text-muted-foreground underline"
                          disabled={pending || committedRuleId != null}
                          onClick={selectAllMatches}
                        >
                          Select all
                        </button>
                      ) : null}
                    </div>
                    <ul className="max-h-48 overflow-y-auto rounded border text-xs">
                      {ruleMatches.map((m) => {
                        const checked = selectedTxnIds.has(m.transaction_id);
                        return (
                          <li
                            key={m.transaction_id}
                            className={cn(
                              "flex items-start gap-2 border-b px-2 py-1.5 last:border-0",
                              m.has_override && "opacity-50",
                            )}
                          >
                            <input
                              type="checkbox"
                              className="mt-0.5"
                              checked={checked}
                              disabled={pending || m.has_override || committedRuleId != null}
                              onChange={(e) => toggleMatch(m.transaction_id, e.target.checked)}
                            />
                            <span className="flex-1">
                              <span className="font-medium">{m.date}</span>
                              {" · "}
                              {m.name || "—"}
                              {" · "}
                              {formatDollars(m.amount)}
                              {m.has_override ? " · override" : ""}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={applyFuture}
                    disabled={pending || committedRuleId != null}
                    onChange={(e) => setApplyFuture(e.target.checked)}
                  />
                  Apply to future transactions
                </label>
                {committedRuleId ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-mono">{committedRuleId}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={pending}
                      onClick={revertRule}
                    >
                      Revert
                    </Button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    disabled={
                      pending ||
                      !explain.category_id ||
                      !ruleHasCriteria ||
                      selectedTxnIds.size === 0
                    }
                    onClick={commitRule}
                  >
                    Commit rule
                  </Button>
                )}
                {error || ruleMsg ? (
                  <p className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
                    {error || ruleMsg}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
