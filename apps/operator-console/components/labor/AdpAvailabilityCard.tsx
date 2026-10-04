import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApproveUnavailabilityButton } from "@/components/labor/ApproveUnavailabilityButton";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { minToTime } from "@/lib/labor/schedule-inputs";
import { collapseWeekly, scheduleConflicts, type ScheduledShift } from "@/lib/labor/unavailability";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function dayLabel(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function clock(min: number): string {
  const [h, m] = minToTime(min % (24 * 60)).split(":").map(Number);
  return `${h! % 12 || 12}:${String(m).padStart(2, "0")} ${h! < 12 ? "AM" : "PM"}`;
}

function hhmm(t: string): number {
  return Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
}

/** "Every Sat · 6:00 AM–10:00 AM · until Nov 1" / "Sun, Oct 4 · All day" — ADP's card, one line. */
function when(r: UnavailabilityRow): string {
  const hours = r.all_day || !r.from_time || !r.to_time
    ? "All day"
    : `${clock(hhmm(r.from_time))}–${clock(hhmm(r.to_time))}`;
  if (r.repeat_weekday != null) {
    return `Every ${WEEKDAYS[r.repeat_weekday]} · ${hours} · ${shortDate(r.first_date)}${
      r.repeat_until ? ` – ${shortDate(r.repeat_until)}` : " onward"
    }`;
  }
  return `${dayLabel(r.first_date)} · ${hours}`;
}

function initials(name: string): string {
  const [last = "", first = ""] = name.split(",").map((s) => s.trim());
  return `${first[0] ?? ""}${last[0] ?? ""}`.toUpperCase() || "?";
}

function expiry(r: UnavailabilityRow): { text: string; urgent: boolean } | null {
  if (r.hours_left == null || !r.expires_at_ct) return null;
  const h = r.hours_left;
  return { text: h < 48 ? `expires in ${Math.max(1, Math.round(h))}h` : `expires ${shortDate(r.expires_at_ct)}`, urgent: h < 48 };
}

function lastRead(iso: string | null | undefined): string {
  if (!iso) return "not read yet";
  // BQ renders TIMESTAMP as "2026-09-27 18:35:40.304467+00".
  const d = new Date(iso.replace(" ", "T").replace(/\+00$/, "Z"));
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-US", { timeZone: "America/Chicago", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " CT";
}

export function AdpAvailabilityCard({
  rows,
  shifts,
  approveEnabled,
  lastReadAt,
}: {
  rows: UnavailabilityRow[];
  /** Upcoming ADP scheduled shifts — only used to flag clashes. */
  shifts: ScheduledShift[];
  approveEnabled: boolean;
  /** Last Team Schedule read (the same run reads Pending requests). */
  lastReadAt: string | null;
}) {
  const entries = collapseWeekly(rows).sort(
    (a, b) =>
      (a.status === "pending" ? 0 : 1) - (b.status === "pending" ? 0 : 1) ||
      (a.status === "pending" ? (a.hours_left ?? 1e9) - (b.hours_left ?? 1e9) : 0) ||
      a.first_date.localeCompare(b.first_date) ||
      a.employee.localeCompare(b.employee),
  );
  const pending = entries.filter((r) => r.status === "pending").length;

  return (
    <Card data-testid="adp-availability">
      <CardHeader>
        <CardTitle>Unavailability</CardTitle>
        <CardDescription>
          From ADP · last read {lastRead(lastReadAt)}. Drafts work around pending and approved entries.
        </CardDescription>
        {pending ? (
          <CardAction>
            <Badge>{pending} pending</Badge>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent>
        {entries.length ? (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {entries.map((r) => {
              const exp = r.status === "pending" ? expiry(r) : null;
              const clashes = scheduleConflicts([r], shifts);
              const text = when(r);
              return (
                <li
                  key={`${r.row_key}-${r.repeat_until ?? ""}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs transition-colors hover:bg-muted/40"
                >
                  <span
                    aria-hidden
                    className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground"
                  >
                    {initials(r.employee)}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-foreground">{r.employee}</span>
                    <span className="truncate text-muted-foreground">{text}</span>
                  </span>
                  {clashes.length ? (
                    <Badge
                      variant="destructive"
                      title={clashes
                        .map((c) => `${dayLabel(c.date)} scheduled ${clock(c.shift.startMin)}–${clock(c.shift.endMin)}`)
                        .join("\n")}
                    >
                      {clashes.length} shift clash{clashes.length === 1 ? "" : "es"}
                    </Badge>
                  ) : null}
                  {exp ? (
                    <span className={cn("tabular-nums", exp.urgent ? "font-medium text-destructive" : "text-muted-foreground")}>
                      {exp.text}
                    </span>
                  ) : null}
                  <Badge variant={r.status === "pending" ? "outline" : "secondary"}>
                    {r.status === "pending" ? "Pending" : "Approved"}
                  </Badge>
                  {r.status === "pending" && approveEnabled ? (
                    <ApproveUnavailabilityButton rowKey={r.row_key} employee={r.employee} when={text} />
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No unavailability in ADP.</p>
        )}
      </CardContent>
    </Card>
  );
}
