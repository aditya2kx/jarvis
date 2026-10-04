import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ScheduleRequestRow, UnavailabilityRow } from "@/lib/bq/queries";
import { minToTime } from "@/lib/labor/schedule-inputs";
import {
  availabilityByPerson,
  scheduleConflicts,
  type ScheduledShift,
} from "@/lib/labor/unavailability";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function dayLabel(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function clock(min: number): string {
  const [h, m] = minToTime(min % (24 * 60)).split(":").map(Number);
  return `${h! % 12 || 12}:${String(m).padStart(2, "0")} ${h! < 12 ? "AM" : "PM"}`;
}

function when(r: UnavailabilityRow): string {
  const hours = r.all_day || !r.from_time || !r.to_time
    ? "all day"
    : `${clock(Number(r.from_time.slice(0, 2)) * 60 + Number(r.from_time.slice(3)))}–${clock(
        Number(r.to_time.slice(0, 2)) * 60 + Number(r.to_time.slice(3)),
      )}`;
  if (r.repeat_weekday != null) {
    return `Every ${WEEKDAYS[r.repeat_weekday]}, ${hours}, ${dayLabel(r.first_date)} → ${
      r.repeat_until ? dayLabel(r.repeat_until) : "no end"
    }`;
  }
  return `${dayLabel(r.first_date)}, ${hours}`;
}

function expiryLabel(r: UnavailabilityRow): { text: string; urgent: boolean } | null {
  if (r.hours_left == null || !r.expires_at_ct) return null;
  const h = r.hours_left;
  if (h <= 0) return { text: "expired — never reaches the schedule", urgent: true };
  const text = h < 48 ? `expires in ${Math.max(1, Math.round(h))}h` : `expires ${dayLabel(r.expires_at_ct)}`;
  return { text, urgent: h < 48 };
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
  requests,
  shifts,
}: {
  rows: UnavailabilityRow[];
  requests: ScheduleRequestRow[];
  /** Upcoming ADP scheduled shifts (today onward). */
  shifts: ScheduledShift[];
}) {
  const pending = rows.filter((r) => r.status === "pending");
  const conflicts = scheduleConflicts(rows, shifts);
  const people = availabilityByPerson(rows, shifts, conflicts);
  const withEntries = people.filter((p) => p.pending + p.approved > 0).length;
  const scheduled = people.filter((p) => p.upcomingShifts > 0);
  const otherRequests = requests.filter((r) => !/unavailab/i.test(r.request_type) && r.pending > 0);
  const scrapedAt = rows[0]?.scraped_at ?? requests[0]?.scraped_at;

  return (
    <Card data-testid="adp-availability">
      <CardHeader>
        <CardTitle>Availability in ADP</CardTitle>
        <CardDescription>
          Unavailability staff enter in ADP Mobile (More → Schedule → My Unavailability). The draft
          schedule works around pending and approved entries. ADP only shows an entry on the schedule
          after you approve it in Team Schedule → Pending requests. Last read {lastRead(scrapedAt)} ·
          refreshed by Sync ADP and every nightly run.
        </CardDescription>
        <CardAction className="flex flex-wrap justify-end gap-1.5">
          <Badge variant={pending.length ? "default" : "secondary"}>{pending.length} awaiting approval</Badge>
          <Badge variant={conflicts.length ? "destructive" : "secondary"}>
            {conflicts.length} schedule conflict{conflicts.length === 1 ? "" : "s"}
          </Badge>
          <Badge variant="outline">
            {withEntries} of {scheduled.length} scheduled people have entries
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {pending.length ? (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-foreground">Awaiting your approval in ADP</h4>
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {pending.map((r) => {
                const exp = expiryLabel(r);
                return (
                  <li key={`${r.employee}-${r.first_date}-${r.from_time}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
                    <span className="font-medium text-foreground">{r.employee}</span>
                    <span className="text-muted-foreground">{when(r)}</span>
                    {exp ? (
                      <span className={cn("ml-auto", exp.urgent ? "font-medium text-destructive" : "text-muted-foreground")}>
                        {exp.text}
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {conflicts.length ? (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-foreground">Scheduled while unavailable</h4>
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-destructive/30">
              {conflicts.map((c) => (
                <li key={`${c.date}-${c.employee}-${c.shift.startMin}`} className="flex flex-wrap gap-x-3 px-3 py-2 text-xs">
                  <span className="font-medium text-foreground">{c.employee}</span>
                  <span className="text-muted-foreground">
                    {dayLabel(c.date)} · scheduled {clock(c.shift.startMin)}–{clock(c.shift.endMin)} · unavailable{" "}
                    {c.block.toMin - c.block.fromMin >= 24 * 60 ? "all day" : `${clock(c.block.fromMin)}–${clock(c.block.toMin)}`}
                    {c.block.status === "pending" ? " (pending)" : ""}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {otherRequests.length ? (
          <p className="text-xs text-muted-foreground">
            Other pending ADP requests:{" "}
            {otherRequests.map((r) => `${r.pending} ${r.request_type.toLowerCase()}`).join(" · ")}
          </p>
        ) : null}

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Person</TableHead>
              <TableHead className="text-right">Upcoming shifts</TableHead>
              <TableHead className="text-right">Pending</TableHead>
              <TableHead className="text-right">Approved</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {people.map((p) => (
              <TableRow key={p.employee}>
                <TableCell className="font-medium">{p.employee}</TableCell>
                <TableCell className="text-right tabular-nums">{p.upcomingShifts}</TableCell>
                <TableCell className="text-right tabular-nums">{p.pending || "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{p.approved || "—"}</TableCell>
                <TableCell>
                  {p.conflicts ? (
                    <Badge variant="destructive">{p.conflicts} conflict{p.conflicts === 1 ? "" : "s"}</Badge>
                  ) : p.pending ? (
                    <Badge>Needs approval</Badge>
                  ) : p.approved ? (
                    <Badge variant="secondary">On file</Badge>
                  ) : (
                    <span className="text-xs text-muted-foreground">Nothing entered</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!people.length ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-xs text-muted-foreground">
                  No upcoming shifts or unavailability in ADP yet — run Sync ADP.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
