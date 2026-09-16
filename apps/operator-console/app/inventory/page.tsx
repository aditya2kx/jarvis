import {
  estimatedScheduleDates,
  inventoryStockLevels,
  nextDates,
  orderRecoRefreshedAt,
  orderRecoSlots,
  restockActuals,
  scheduledRestockDates,
  storeConfig,
  usageDayAudit,
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
  stockOnlyRows,
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
import { PageHeader } from "@/components/shell/PageHeader";
import { RestockImportDrawer } from "@/components/drawers/RestockImportDrawer";
import { CapacityEdit } from "@/components/drawers/CapacityEdit";
import { UsageDayAuditTable } from "@/components/inventory/UsageDayAuditTable";
import { OrderRecoTable } from "@/components/inventory/OrderRecoTable";
import { OrderedTubsActualsTable } from "@/components/inventory/OrderedTubsActualsTable";
import { InventoryRecoFreshness } from "@/components/inventory/InventoryRecoFreshness";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";

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
  /** Showing stock/burn only, because no delivery date is registered. */
  let stockOnly = false;
  try {
    // Prod: enqueue Cloud Run when stale. Local BYPASS_IAP: refresh inline so
    // Inventory columns match schedule without waiting on a job.
    const syncLocal = Boolean(process.env.BYPASS_IAP_EMAIL?.trim());
    recoBaseline = await orderRecoRefreshedAt(DEFAULT_STORE);
    const ensure = await ensureOrderRecoFresh(
      DEFAULT_STORE,
      FEATURES.asyncOrderReco && !syncLocal
        ? {
            enqueue: async () => {
              await triggerOrderRecoRefresh(DEFAULT_STORE);
            },
          }
        : {},
    );
    recoQueued = ensure.status === "queued";
    const [slotRows, nd, config, estimated, scheduled, audit, actuals] =
      await Promise.all([
        orderRecoSlots(),
        nextDates(),
        storeConfig(DEFAULT_STORE),
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
    auditRows = audit;
    estimatedDates = estimated.map((d) => normalizeDeliveryDate(d.delivery_date)).filter(Boolean);
    scheduledDates = scheduled.map((d) => ({
      delivery_date: normalizeDeliveryDate(d.delivery_date),
      has_actuals: Boolean(d.has_actuals),
    }));
    // No delivery date on the books: the ordering columns are undefined, but
    // stock and burn rate are not. Fall back to them rather than rendering an
    // empty table — a blank page looks like "nothing to do" at exactly the
    // moment a base may be days from running out.
    if (dates.length === 0) {
      rows = stockOnlyRows(await inventoryStockLevels(DEFAULT_STORE));
      stockOnly = rows.length > 0;
    }
    estimateByDate = buildEstimateByDate(paintRows);
    const maxTubsRow = config.find((c) => c.key === "order_reco_max_tubs");
    maxTubs = maxTubsRow ? Number(maxTubsRow.value) : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  // Say what is missing AND what still holds. The bare "No delivery date
  // registered yet." over an empty table read as "no data", when in fact
  // current stock and burn rate were known the whole time.
  const nextDeliveryLabel =
    liveDates.length > 0
      ? `Next delivery: ${liveDates.join(" · then ")}`
      : stockOnly
        ? "No delivery date registered yet — showing current stock and burn rate. " +
          "Register a delivery date to get order quantities."
        : "No delivery date registered yet.";

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
          <p className="text-sm text-muted-foreground">{nextDeliveryLabel}</p>
          {stockOnly ? (
            <p className="text-xs text-muted-foreground">
              Days left = Current Qty ÷ Avg/day, from the latest closing counts — how long
              this base lasts with no restocking. Order quantities need a delivery date to
              count back from, so those columns appear once one is registered.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Days left = Current Qty ÷ Avg/day — how long this base lasts with no restocking
              at all. Days left after is the same figure counted from a delivery, assuming
              that order arrives. Order weight (lbs) = Order tubs × per-tub weight (Açaí 18 lbs;
              other bases 20 lbs; Blade is direct-delivery / not weighed). TOTAL includes +50
              lbs per pallet (40 tubs/pallet) — same as Grafana Order Assistant. Click an
              Order tubs cell (or the pencil in the header) to edit that delivery: Estimated
              dates pin Manual values; Actuals dates update uploaded Actuals. Apply once
              recomputes the recommendation.
            </p>
          )}
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
              omitted. Reco and usage-by-day are not Period-filtered. Last 7 / 30 days
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
