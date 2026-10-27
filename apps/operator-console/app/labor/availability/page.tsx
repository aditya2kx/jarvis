import {
  adpDirectoryRoster,
  adpHoursScrapedAt,
  adpScheduleScrapedAt,
  adpUnavailability,
  adpUnavailabilityEvents,
  laborScheduledShiftDays,
  type UnavailabilityEventRow,
  type UnavailabilityRow,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { FEATURES } from "@/lib/config/features";
import { isConsolePreview } from "@/lib/config/preview";
import { chicagoTodayIso, shiftCalendarDate } from "@/lib/filters/range";
import { scheduleRulesHistory } from "@/lib/labor/schedule-rules-store";
import { ruleUnavailability } from "@/lib/labor/schedule-inputs";
import { collapseWeekly, type ScheduledShift } from "@/lib/labor/unavailability";
import { pageDenied, sectionPageDenied } from "@/components/access/guards";
import { PageHeader } from "@/components/shell/PageHeader";
import { SyncAdpButton } from "@/components/labor/SyncAdpButton";
import { LaborTabs } from "@/components/labor/LaborTabs";
import { AvailabilityWorkspace } from "@/components/labor/availability/AvailabilityWorkspace";
import { clockedHoursTargetDate } from "@/lib/labor/actual-schedule-windows";

export const dynamic = "force-dynamic";

export default async function LaborAvailabilityPage() {
  const denied = (await pageDenied("labor")) ?? (await sectionPageDenied("labor", "availability"));
  if (denied) return denied;

  const todayIso = chicagoTodayIso();
  let rows: UnavailabilityRow[] = [];
  let shifts: ScheduledShift[] = [];
  let roster: string[] = [];
  let events: UnavailabilityEventRow[] = [];
  let lastReadAt: string | null = null;
  let hoursScrapedAt: string | null = null;
  let error: string | undefined;
  try {
    const [unavail, rules, upcoming, directory, history, readAt, hoursAt] = await Promise.all([
      adpUnavailability(DEFAULT_STORE),
      scheduleRulesHistory(DEFAULT_STORE).catch(() => []),
      laborScheduledShiftDays(
        // Back to the Monday a week ago so the grid's first weeks show their shifts too.
        { start: shiftCalendarDate(todayIso, "day", -13), end: shiftCalendarDate(todayIso, "day", 56), label: "Availability", preset: "custom" },
        { store: DEFAULT_STORE, excludePto: true, allowPast: true },
      ).catch(() => []),
      adpDirectoryRoster(DEFAULT_STORE).catch(() => []),
      // Table lands with migration 092; until then history is simply empty.
      adpUnavailabilityEvents(DEFAULT_STORE).catch(() => []),
      adpScheduleScrapedAt().catch(() => null),
      adpHoursScrapedAt().catch(() => null),
    ]);
    rows = [
      ...unavail,
      ...ruleUnavailability(rules[0]?.rules.staffRules ?? []).map(
        (r): UnavailabilityRow => ({ ...r, expires_at_ct: null, hours_left: null, scraped_at: null }),
      ),
    ];
    shifts = upcoming;
    events = history;
    lastReadAt = readAt;
    hoursScrapedAt = hoursAt;
    // Same roster rule as /labor: anyone scheduled ahead plus every Active hourly person in the
    // ADP Directory; any other Directory status drops even the scheduled.
    const statusOf = new Map(directory.map((r) => [r.employee, r.employment_status?.toLowerCase() ?? null]));
    roster = [
      ...new Set([
        ...upcoming.filter((s) => s.date >= todayIso).map((s) => s.employee),
        ...directory.filter((r) => r.employment_status?.toLowerCase() === "active").map((r) => r.employee),
      ]),
    ].filter((n) => (statusOf.get(n) ?? "active") === "active");
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const pendingCount = collapseWeekly(rows.filter((r) => r.status === "pending")).length;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Labor"
        subtitle="Unavailability · from ADP"
        actions={<SyncAdpButton lastScrapedAt={hoursScrapedAt} targetDate={clockedHoursTargetDate({ todayIso, coverageDay: undefined })} />}
      />
      <LaborTabs active="/labor/availability" pendingRequests={pendingCount} />
      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <AvailabilityWorkspace
          rows={rows}
          shifts={shifts}
          roster={roster}
          events={events}
          lastReadAt={lastReadAt}
          todayIso={todayIso}
          applyBlock={isConsolePreview() ? "preview" : FEATURES.adpUnavailabilityWrite ? null : "off"}
        />
      )}
    </div>
  );
}
