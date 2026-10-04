"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApproveUnavailabilityButton } from "@/components/labor/ApproveUnavailabilityButton";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import type { LocalMultiSelection } from "@/lib/tables/localMultiFilter";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { minToTime } from "@/lib/labor/schedule-inputs";
import {
  approvedByPerson,
  collapseWeekly,
  scheduleConflicts,
  type PatternLine,
  type ScheduledShift,
} from "@/lib/labor/unavailability";
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

function hoursText(r: { all_day: boolean; from_time: string | null; to_time: string | null }): string {
  return r.all_day || !r.from_time || !r.to_time
    ? "All day"
    : `${clock(hhmm(r.from_time))}–${clock(hhmm(r.to_time))}`;
}

/** [0,1,2,3,4,6] → "Mon–Fri, Sun". */
function weekdaysText(days: number[]): string {
  const parts: string[] = [];
  for (let i = 0; i < days.length; ) {
    let j = i;
    while (j + 1 < days.length && days[j + 1] === days[j]! + 1) j++;
    parts.push(j - i >= 2 ? `${WEEKDAYS[days[i]!]}–${WEEKDAYS[days[j]!]}` : days.slice(i, j + 1).map((d) => WEEKDAYS[d]).join(", "));
    i = j + 1;
  }
  return parts.join(", ");
}

/** "Every Sat · 6:00 AM–10:00 AM · until Nov 1" / "Sun, Oct 4 · All day" — ADP's card, one line. */
function when(r: UnavailabilityRow): string {
  if (r.repeat_weekday != null) {
    return `Every ${WEEKDAYS[r.repeat_weekday]} · ${hoursText(r)} · ${shortDate(r.first_date)}${
      r.repeat_until ? ` – ${shortDate(r.repeat_until)}` : " onward"
    }`;
  }
  return `${dayLabel(r.first_date)} · ${hoursText(r)}`;
}

/** "Mon–Fri · All day · until Oct 30"; patterns still present in the last week ADP was read are "ongoing". */
function lineText(l: PatternLine<UnavailabilityRow>, lastWeekFrom: string, nextWeek: string): string {
  if (!l.weekdays.length) return `${dayLabel(l.date)} · ${hoursText(l)}`;
  const start = l.date >= nextWeek ? ` · from ${shortDate(l.date)}` : "";
  const end = !l.until ? "" : l.until >= lastWeekFrom ? " · ongoing" : ` · until ${shortDate(l.until)}`;
  return `${weekdaysText(l.weekdays)} · ${hoursText(l)}${start}${end}`;
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

function Avatar({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground"
    >
      {initials(name)}
    </span>
  );
}

function ClashBadge({ rows, shifts }: { rows: UnavailabilityRow[]; shifts: ScheduledShift[] }) {
  const clashes = scheduleConflicts(rows, shifts);
  if (!clashes.length) return null;
  return (
    <Badge
      variant="destructive"
      title={clashes.map((c) => `${dayLabel(c.date)} scheduled ${clock(c.shift.startMin)}–${clock(c.shift.endMin)}`).join("\n")}
    >
      {clashes.length} shift clash{clashes.length === 1 ? "" : "es"}
    </Badge>
  );
}

const ROW = "flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs transition-colors hover:bg-muted/40";

type View = "pending" | "approved" | "none" | "all";

export function AdpAvailabilityCard({
  rows,
  shifts,
  roster,
  approveEnabled,
  lastReadAt,
  todayIso,
}: {
  rows: UnavailabilityRow[];
  /** Upcoming ADP scheduled shifts — only used to flag clashes. */
  shifts: ScheduledShift[];
  /** Current team (people on the upcoming ADP schedule) — backs "Not marked". */
  roster: string[];
  approveEnabled: boolean;
  /** Last Team Schedule read (the same run reads Pending requests). */
  lastReadAt: string | null;
  todayIso: string;
}) {
  const pendingRows = collapseWeekly(rows.filter((r) => r.status === "pending")).sort(
    (a, b) => (a.hours_left ?? 1e9) - (b.hours_left ?? 1e9) || a.first_date.localeCompare(b.first_date),
  );
  const people = approvedByPerson(rows, todayIso);
  const marked = new Set([...pendingRows.map((r) => r.employee), ...people.map((p) => p.employee)]);
  const unmarked = [...new Set(roster)].filter((n) => !marked.has(n)).sort((a, b) => a.localeCompare(b));
  const everyone = [...new Set([...marked, ...unmarked])].sort((a, b) => a.localeCompare(b));
  const readThrough = rows.reduce((m, r) => (r.status === "approved" && r.first_date > m ? r.first_date : m), "");
  const lastWeekFrom = readThrough
    ? new Date(Date.parse(`${readThrough}T12:00:00Z`) - 6 * 86_400_000).toISOString().slice(0, 10)
    : "";
  const nextWeek = new Date(Date.parse(`${todayIso}T12:00:00Z`) + 7 * 86_400_000).toISOString().slice(0, 10);

  const [view, setView] = useState<View>(pendingRows.length ? "pending" : "all");
  const [selected, setSelected] = useState<LocalMultiSelection>(null);
  const picked = (name: string) => selected == null || selected.includes(name);
  const show = (v: Exclude<View, "all">) => view === "all" || view === v;

  const shownPending = show("pending") ? pendingRows.filter((r) => picked(r.employee)) : [];
  const shownApproved = show("approved") ? people.filter((p) => picked(p.employee)) : [];
  const shownUnmarked = show("none") ? unmarked.filter(picked) : [];
  const count = (list: string[]) => list.filter(picked).length;
  const views: [View, string][] = [
    ["pending", `Needs approval · ${count(pendingRows.map((r) => r.employee))}`],
    ["approved", `Approved · ${count(people.map((p) => p.employee))}`],
    ["none", `Not marked · ${count(unmarked)}`],
    ["all", "All"],
  ];

  return (
    <Card data-testid="adp-availability">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Unavailability</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <LocalMultiSelect label="People" selected={selected} options={everyone} onChange={setSelected} />
            <div className="flex items-center gap-1 rounded-lg border border-border p-0.5" role="group" aria-label="Unavailability status">
              {views.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setView(value)}
                  aria-pressed={view === value}
                  className={cn(
                    "h-7 rounded-md px-2 text-xs font-medium tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    view === value ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <CardDescription>
          From ADP · last read {lastRead(lastReadAt)}
          {readThrough ? ` through ${shortDate(readThrough)}` : ""}. Drafts work around pending and approved entries.
          &ldquo;Not marked&rdquo; is anyone on the upcoming schedule with no entry in ADP.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {shownPending.length || shownApproved.length || shownUnmarked.length ? (
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {shownPending.map((r) => {
              const exp = expiry(r);
              const text = when(r);
              return (
                <li key={`${r.row_key}-${r.repeat_until ?? ""}`} className={ROW}>
                  <Avatar name={r.employee} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-foreground">{r.employee}</span>
                    <span className="truncate text-muted-foreground">{text}</span>
                  </span>
                  <ClashBadge rows={[r]} shifts={shifts} />
                  {exp ? (
                    <span className={cn("tabular-nums", exp.urgent ? "font-medium text-destructive" : "text-muted-foreground")}>
                      {exp.text}
                    </span>
                  ) : null}
                  <Badge variant="outline">Pending</Badge>
                  {approveEnabled ? (
                    <ApproveUnavailabilityButton rowKey={r.row_key} employee={r.employee} when={text} />
                  ) : null}
                </li>
              );
            })}
            {shownApproved.map((p) => (
              <li key={p.employee} className={cn(ROW, "items-start")}>
                <Avatar name={p.employee} />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate font-medium text-foreground">{p.employee}</span>
                  {p.lines.map((l) => (
                    <span key={`${l.weekdays.join("")}-${l.date}-${l.from_time}`} className="text-muted-foreground tabular-nums">
                      {lineText(l, lastWeekFrom, nextWeek)}
                    </span>
                  ))}
                </span>
                <ClashBadge rows={p.lines.flatMap((l) => l.rows)} shifts={shifts} />
                <Badge variant="secondary">Approved</Badge>
              </li>
            ))}
            {shownUnmarked.map((name) => (
              <li key={`none-${name}`} className={ROW}>
                <Avatar name={name} />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium text-foreground">{name}</span>
                  <span className="truncate text-muted-foreground">Available any time · nothing marked in ADP</span>
                </span>
                <Badge variant="outline" className="text-muted-foreground">Not marked</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
            {view === "pending" ? "Nothing waiting for approval." : "No one matches these filters."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
