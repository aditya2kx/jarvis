"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DataTable } from "@/components/tables/DataTable";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { AutomationPostRow, AutomationRow } from "@/lib/bq/queries";
import { cadenceSummary, DAY_LABELS, parseDays } from "@/lib/automations/teamPulse";
import {
  REMINDER_DEFAULT_DAYS,
  REMINDER_DEFAULT_FOLLOWUP,
  REMINDER_DEFAULT_HOUR,
  REMINDER_DEFAULT_TEMPLATE,
  reminderKind,
} from "@/lib/automations/unavailabilityReminder";
import {
  previewReminderAction,
  saveReminderConfigAction,
  sendReminderOnceAction,
} from "./actions";

type Props = {
  initial: AutomationRow | null;
  posts: AutomationPostRow[];
  todayIso: string;
};

const textareaClass =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function ReminderEditor({ initial, posts, todayIso }: Props) {
  const { run, isPending, stage, error } = useConsoleAction();
  const [enabled, setEnabled] = useState(() => {
    const v = initial?.enabled as unknown;
    return initial ? v === true || v === "true" || v === 1 : true;
  });
  const [days, setDays] = useState<number[]>(() =>
    initial ? parseDays(initial.days_of_week) : REMINDER_DEFAULT_DAYS,
  );
  const [hour, setHour] = useState(initial?.hour_local ?? REMINDER_DEFAULT_HOUR);
  const [minute, setMinute] = useState(initial?.minute_local ?? 0);
  const [template, setTemplate] = useState(initial?.template || REMINDER_DEFAULT_TEMPLATE);
  const [followup, setFollowup] = useState(
    initial?.followup_template || REMINDER_DEFAULT_FOLLOWUP,
  );
  const [previewDate, setPreviewDate] = useState(todayIso);
  const [preview, setPreview] = useState<{ kind: string; content: string } | null>(null);

  const cadence = useMemo(
    () => cadenceSummary(days, hour, minute, "America/Chicago"),
    [days, hour, minute],
  );
  const firstDay = days.length ? DAY_LABELS[Math.min(...days)] : "—";
  const input = {
    enabled,
    days_of_week: days,
    hour_local: hour,
    minute_local: minute,
    template,
    followup_template: followup,
  };

  function toggleDay(d: number) {
    setDays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort()));
    setPreview(null);
  }

  async function onSave() {
    await run(() => saveReminderConfigAction(input), { saving: "Saving…", done: "Saved." });
  }

  async function onPreview() {
    const ack = await run(() => previewReminderAction(previewDate, input), {
      saving: "Composing…",
      done: "Preview ready.",
    });
    if (ack.ok && ack.data) setPreview(ack.data);
  }

  async function onSendNow() {
    await run(() => sendReminderOnceAction(), { saving: "Sending…", done: "Sent to your DM." });
  }

  const postColumns: ColumnDef<AutomationPostRow>[] = [
    { accessorKey: "post_date_ct", header: "Date (CT)" },
    {
      accessorKey: "content",
      header: "Message",
      cell: ({ getValue }) => {
        const v = String(getValue() ?? "").split("---\n\n").pop() ?? "";
        return v.length > 120 ? `${v.slice(0, 120)}…` : v;
      },
    },
    { accessorKey: "trigger", header: "Trigger" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Link
          href="/automations"
          className="rounded hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Automations
        </Link>
        <span>/</span>
        <span className="text-foreground">Unavailability reminder</span>
        <Badge variant={enabled ? "default" : "secondary"}>{enabled ? "Enabled" : "Off"}</Badge>
        <span>{cadence}</span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Schedule</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-primary"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>
              <span className="font-medium">Automation on</span>
              <span className="block text-xs text-muted-foreground">
                Off = no scheduled DMs. Preview and DM me now still work.
              </span>
            </span>
          </label>

          <div>
            <Label className="mb-2 block">Days</Label>
            <div className="flex flex-wrap gap-2">
              {DAY_LABELS.map((label, idx) => {
                const on = days.includes(idx);
                return (
                  <Button
                    key={label}
                    type="button"
                    size="sm"
                    variant={on ? "default" : "outline"}
                    className="min-h-11 min-w-11"
                    aria-pressed={on}
                    onClick={() => toggleDay(idx)}
                  >
                    {label}
                  </Button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:max-w-xs">
            <div>
              <Label htmlFor="ur-hour">Hour (CT)</Label>
              <Input
                id="ur-hour"
                type="number"
                min={0}
                max={23}
                className="min-h-11"
                value={hour}
                onChange={(e) => setHour(Number(e.target.value))}
              />
            </div>
            <div>
              <Label htmlFor="ur-minute">Minute</Label>
              <Input
                id="ur-minute"
                type="number"
                min={0}
                max={59}
                step={15}
                className="min-h-11"
                value={minute}
                onChange={(e) => setMinute(Number(e.target.value))}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Checked every 15 minutes; sends one DM to you per day, at or after this time.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Messages</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ur-template">Reminder · {firstDay}</Label>
            <p className="text-xs text-muted-foreground">
              Sent on the first selected day.{" "}
              <code className="rounded bg-muted px-1">{"{target_week}"}</code> is the week after
              next (e.g. Oct 19 - Oct 25);{" "}
              <code className="rounded bg-muted px-1">{"{publish_day}"}</code> is this week’s
              Friday (e.g. Friday(Oct 9)).
            </p>
            <textarea
              id="ur-template"
              className={`${textareaClass} min-h-28`}
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ur-followup">Follow-up · other selected days</Label>
            <p className="text-xs text-muted-foreground">
              A reply for the reminder’s thread. Same placeholders work here.
            </p>
            <textarea
              id="ur-followup"
              className={`${textareaClass} min-h-16`}
              value={followup}
              onChange={(e) => setFollowup(e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-end gap-2">
        <Button type="button" className="min-h-11" disabled={isPending} onClick={() => void onSave()}>
          Save
        </Button>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="ur-preview-date" className="text-xs text-muted-foreground">
            Preview as of
          </Label>
          <Input
            id="ur-preview-date"
            type="date"
            className="min-h-11 w-40"
            value={previewDate}
            onChange={(e) => {
              setPreviewDate(e.target.value);
              setPreview(null);
            }}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={isPending || !previewDate}
          onClick={() => void onPreview()}
        >
          Preview {previewDate ? (reminderKind(previewDate, days) === "reminder" ? "reminder" : "follow-up") : ""}
        </Button>
        <Button
          type="button"
          variant="secondary"
          className="min-h-11"
          disabled={isPending}
          onClick={() => void onSendNow()}
        >
          DM me now
        </Button>
      </div>
      <p className="-mt-2 text-xs text-muted-foreground">
        DM me now sends today’s saved message and counts as today’s send.
      </p>
      {(stage || error) && (
        <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
          {error ?? stage}
        </p>
      )}

      {preview && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="text-base">Preview</CardTitle>
            <Badge variant="secondary">{preview.kind === "reminder" ? "Reminder" : "Follow-up"}</Badge>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-sm">{preview.content}</pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sent history</CardTitle>
        </CardHeader>
        <CardContent>
          {posts.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing sent yet.</p>
          ) : (
            <DataTable columns={postColumns} data={posts} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
