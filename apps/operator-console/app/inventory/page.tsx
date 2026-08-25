import {
  baseRunway,
  estimatedScheduleDates,
  nextDates,
  orderRecoRefreshedAt,
  orderRecoSlots,
  restockActuals,
  scheduledRestockDates,
  storeConfig,
  usageDayAudit,
  type BaseRunwayRow,
  type UsageDayAuditRow,
} from "@/lib/bq/queries";
import { ensureOrderRecoFresh } from "@/lib/bq/writes";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { storeDisplayName } from "@/lib/config/stores";
import { triggerOrderRecoRefresh } from "@/lib/bhaga/recompute";
import {
  normalizeDeliveryDate,
  pivotOrderRecoSlots,
  rowsForPaintGeneration,
  selectPaintGeneration,
  type OrderRecoPivotedRow,
} from "@/lib/inventory/orderRecoPivot";
import {
  pivotRestockActuals,
  restockActualsColumns,
  type RestockActualsPivotedRow,
} from "@/lib/inventory/restockActuals";
import { RANGE_PRESETS, wantsCustom } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { ACTIVE_BASES, type RestockRow } from "@/lib/restock/parse";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import { UsageDayAuditTable } from "@/components/inventory/UsageDayAuditTable";
import { OrderRecoTable } from "@/components/inventory/OrderRecoTable";
import { OrderedTubsActualsTable } from "@/components/inventory/OrderedTubsActualsTable";
import { InventoryRecoFreshness } from "@/components/inventory/InventoryRecoFreshness";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import type { ColumnDef } from "@tanstack/react-table";

function buildEstimateByDate(
  slotRows: { Item: string; delivery_date: string; "Order Tubs": number | null }[],
): Record<string, RestockRow[]> {
  const byDate = new Map<string, Map<string, number>>();
  for (const r of slotRows) {
    if (r.Item === "TOTAL" || r.Item === "Blade") continue;
    const d = normalizeDeliveryDate(r.delivery_date);
    if (!d) continue;
    if (!byDate.has(d)) byDate.set(d, new Map());
    byDate.get(d)!.set(r.Item, Number(r["Order Tubs"] ?? 0));
  }
  const out: Record<string, RestockRow[]> = {};
  for (const [d, items] of byDate) {
    out[d] = ACTIVE_BASES.map((item) => ({
      item,
      quantityTubs: items.has(item) ? Number(items.get(item)) : 0,
    }));
  }
  return out;
}

export const dynamic = "force-dynamic";

const DAYS_LEFT_THRESHOLDS: Thresholds = { warn: 7, bad: 4, direction: "lower-bad" };

export default async function InventoryPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> =
    win.preset === "custom" ? { from: win.start, to: win.end } : {};

  let rows: OrderRecoPivotedRow[] = [];
  let runwayRows: BaseRunwayRow[] = [];
  let auditRows: UsageDayAuditRow[] = [];
  let actualsRows: RestockActualsPivotedRow[] = [];
  let dates: string[] = [];
  let liveDates: string[] = [];
  let estimatedDates: string[] = [];
  let scheduledDates: { delivery_date: string; has_actuals: boolean }[] = [];
  let estimateByDate: Record<string, RestockRow[]> = {};
  let maxTubs: number | undefined;
  let error: string | undefined;
  let recoQueued = false;
  let recoBaseline: string | null = null;
  let recoPending = false;
  try {
    // Prod: enqueue Cloud Run when stale. Local BYPASS_IAP: refresh inline so
    // Inventory columns match schedule without waiting on a job.
    const syncLocal = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());
    recoBaseline = await orderRecoRefreshedAt(DEFAULT_STORE);
    const ensure = await ensureOrderRecoFresh(
      DEFAULT_STORE,
      FEATURES.asyncOrderReco && !syncLocal
        ? { enqueue: () => triggerOrderRecoRefresh(DEFAULT_STORE) }
        : {},
    );
    recoQueued = ensure.status === "queued";
    const [slotRows, nd, config, runway, estimated, scheduled, audit, actuals] =
      await Promise.all([
        orderRecoSlots(),
        nextDates(),
        storeConfig(DEFAULT_STORE),
        baseRunway(),
        estimatedScheduleDates(DEFAULT_STORE),
        scheduledRestockDates(DEFAULT_STORE),
        usageDayAudit(DEFAULT_STORE),
        restockActuals(DEFAULT_STORE, win),
      ]);
    actualsRows = pivotRestockActuals(actuals);
    liveDates = nd.map((d) => normalizeDeliveryDate(d.delivery_date)).filter(Boolean);
    const paint = selectPaintGeneration(liveDates, slotRows);
    dates = paint.readyDates;
    recoPending = paint.pending || recoQueued;
    const paintRows = rowsForPaintGeneration(slotRows, paint);
    rows = pivotOrderRecoSlots(dates, paintRows);
    runwayRows = runway;
    auditRows = audit;
    estimatedDates = estimated.map((d) => normalizeDeliveryDate(d.delivery_date)).filter(Boolean);
    scheduledDates = scheduled.map((d) => ({
      delivery_date: normalizeDeliveryDate(d.delivery_date),
      has_actuals: Boolean(d.has_actuals),
    }));
    estimateByDate = buildEstimateByDate(paintRows);
    const maxTubsRow = config.find((c) => c.key === "order_reco_max_tubs");
    maxTubs = maxTubsRow ? Number(maxTubsRow.value) : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const runwayColumns: ColumnDef<BaseRunwayRow>[] = [
    { accessorKey: "Base", header: "Base" },
    { accessorKey: "Stock", header: "Stock", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Vel per day", header: "Vel/day", meta: { format: { kind: "number", digits: 2 } } },
    {
      accessorKey: "Days left",
      header: "Days left",
      meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
    },
    { accessorKey: "Stockout 1", header: "Stockout 1", meta: { format: { kind: "date" } } },
    { accessorKey: "Restock 1", header: "Restock 1", meta: { format: { kind: "date" } } },
    { accessorKey: "Qty 1", header: "Qty 1", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Status 1", header: "Status 1", meta: { format: { kind: "status" } } },
    { accessorKey: "Stockout 2", header: "Stockout 2", meta: { format: { kind: "date" } } },
    { accessorKey: "Restock 2", header: "Restock 2", meta: { format: { kind: "date" } } },
    { accessorKey: "Qty 2", header: "Qty 2", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "Status 2", header: "Status 2", meta: { format: { kind: "status" } } },
  ];

  const nextDeliveryLabel =
    liveDates.length === 0
      ? "No delivery date registered yet."
      : `Next delivery: ${liveDates.join(" · then ")}`;

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PageHeader
        title="Inventory / Ordering"
        subtitle={`Order Assistant recommendations · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/inventory"
              extraParams={dateParams}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/inventory"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
              />
            ) : null}
            {FEATURES.writeRestock ? (
              <>
                <CapacityEdit currentMaxTubs={maxTubs} />
                <RestockImportDrawer
                  dates={liveDates.length ? liveDates : dates}
                  scheduledDates={scheduledDates}
                  estimateByDate={estimateByDate}
                />
              </>
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          {recoPending ? (
            <InventoryRecoFreshness
              pending={recoPending}
              baselineRefreshedAt={recoBaseline}
            />
          ) : null}
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Base runway</h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Days left and Stockout 1 are burn-down from today (ignore future restocks).
              Restock 1/2 show uploaded Actuals only (up to two future Actuals dates per base) —
              estimated schedule dates do not appear here. Stockout 2 assumes Restock 1 Actuals
              qty arrived on that date. Status is Risky when that slot&apos;s restock is empty or
              stockout is before the restock date; Fine when restock arrives on or before stockout.
              Rows highlight when Status 1 or Status 2 is Risky.
            </p>
            <DataTable
              columns={runwayColumns}
              data={runwayRows}
              pinLeft={["Base"]}
              initialSorting={[{ id: "Days left", desc: false }]}
              rowHighlight={[
                { accessorKey: "Status 1", equals: "Risky", className: "bg-destructive/5" },
                { accessorKey: "Status 2", equals: "Risky", className: "bg-destructive/5" },
              ]}
            />
          </div>

          <p className="text-sm text-muted-foreground">{nextDeliveryLabel}</p>
          <p className="text-xs text-muted-foreground">
            Order weight (lbs) = Order tubs × per-tub weight (Açaí 18 lbs; other bases 20 lbs;
            Blade is direct-delivery / not weighed). TOTAL includes +50 lbs per pallet (40
            tubs/pallet) — same as Grafana Order Assistant. Click an Order tubs cell (or the
            pencil in the header) to edit that delivery: Estimated dates pin Manual values;
            Actuals dates update uploaded Actuals. Apply once recomputes the recommendation.
          </p>
          <OrderRecoTable
            dates={dates}
            estimatedDates={estimatedDates}
            rows={rows}
            maxTubs={maxTubs}
            writable={FEATURES.writeRestock}
          />

          <div data-testid="ordered-tubs-actuals">
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Ordered tubs (Actuals)
            </h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Uploaded order quantities for delivery dates in {win.label} ({win.start} –{" "}
              {win.end}). Period is the header control (same as Sales / Labor). Estimates are
              omitted. Reco, runway, and usage-by-day are not Period-filtered. Last 7 / 30 days
              end today, so a future delivery needs This month or Custom.
            </p>
            <OrderedTubsActualsTable rows={actualsRows} columns={restockActualsColumns()} />
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Base usage by day (last 30 days)
            </h2>
            <p className="mb-2 text-xs text-muted-foreground">
              One row per closing date; one column per base (qty + in-avg / exclude tag). Table
              scrolls (~10 rows tall); Date stays frozen. Click a row to open the day editor —
              set force include/exclude per base, then Apply (nothing writes until then).
            </p>
            <UsageDayAuditTable
              rows={auditRows}
              writable={FEATURES.writeInventoryDayOverrides}
            />
          </div>
        </>
      )}
    </div>
  );
}
