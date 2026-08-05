"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/tables/DataTable";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { AutomationPostRow, AutomationRow } from "@/lib/bq/queries";
import type { ClickUpNamedOption } from "@/lib/automations/clickupTypes";
import { DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickupTypes";
import {
  cadenceSummary,
  DAY_LABELS,
  DEFAULT_TEMPLATE,
  parseDays,
} from "@/lib/automations/teamPulse";
import {
  postTeamPulseOnceAction,
  previewTeamPulseAction,
  saveTeamPulseConfigAction,
} from "@/app/automations/actions";
import type { ColumnDef } from "@tanstack/react-table";

type Props = {
  initial: AutomationRow | null;
  posts: AutomationPostRow[];
  channels: ClickUpNamedOption[];
  members: ClickUpNamedOption[];
  clickupError?: string;
};

function labelFor(options: ClickUpNamedOption[], id: string): string {
  return options.find((o) => o.id === id)?.label ?? id;
}

export function TeamPulseEditor({
  initial,
  posts,
  channels,
  members,
  clickupError,
}: Props) {
  const { run, isPending, stage, error } = useConsoleAction();
  const [enabled, setEnabled] = useState(() => {
    const v = initial?.enabled as unknown;
    return v === true || v === "true" || v === 1;
  });
  const [days, setDays] = useState<number[]>(
    () => parseDays(initial?.days_of_week ?? "[1,3,6]"),
  );
  const [hour, setHour] = useState(initial?.hour_local ?? 8);
  const [minute, setMinute] = useState(initial?.minute_local ?? 0);
  const [destination, setDestination] = useState<"dm" | "channel">(
    initial?.destination === "channel" ? "channel" : "dm",
  );
  const [channelId, setChannelId] = useState(
    initial?.channel_id ?? "8cr6661-737",
  );
  const [dmUserId, setDmUserId] = useState(
    initial?.dm_user_id ?? "198109189",
  );
  const [template, setTemplate] = useState(
    initial?.template ?? DEFAULT_TEMPLATE,
  );
  const [preview, setPreview] = useState<string | null>(null);
  const [previewVaried, setPreviewVaried] = useState(false);

  const cadence = useMemo(
    () => cadenceSummary(days, hour, minute, "America/Chicago"),
    [days, hour, minute],
  );

  // Ensure current selection appears even if list failed / id not in catalog.
  const channelOptions = useMemo(() => {
    if (channels.some((c) => c.id === channelId)) return channels;
    if (!channelId) return channels;
    return [{ id: channelId, label: `#${channelId}` }, ...channels];
  }, [channels, channelId]);

  const memberOptions = useMemo(() => {
    if (members.some((m) => m.id === dmUserId)) return members;
    if (!dmUserId) return members;
    return [{ id: dmUserId, label: dmUserId }, ...members];
  }, [members, dmUserId]);

  function toggleDay(d: number) {
    setDays((prev) =>
      prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort(),
    );
  }

  async function onSave() {
    await run(
      () =>
        saveTeamPulseConfigAction({
          enabled,
          days_of_week: days,
          hour_local: hour,
          minute_local: minute,
          timezone: "America/Chicago",
          destination,
          channel_id: channelId,
          dm_user_id: dmUserId,
          workspace_id: DEFAULT_WORKSPACE_ID,
          template,
        }),
      { saving: "Saving…", done: "Saved." },
    );
  }

  async function onPreview() {
    const ack = await run(() => previewTeamPulseAction(), {
      saving: "Composing…",
      done: "Preview ready.",
    });
    if (ack.ok && ack.data?.content) {
      setPreview(ack.data.content);
      setPreviewVaried(Boolean(ack.data.varied));
    }
  }

  async function onPostOnce() {
    await run(() => postTeamPulseOnceAction(), {
      saving: "Posting…",
      done: "Posted.",
    });
  }

  const postColumns: ColumnDef<AutomationPostRow>[] = [
    { accessorKey: "post_date_ct", header: "Date (CT)" },
    {
      accessorKey: "destination",
      header: "Dest",
      cell: ({ row }) => {
        const dest = row.original.destination;
        if (dest === "channel") {
          return labelFor(channelOptions, row.original.channel_id ?? "");
        }
        return "DM";
      },
    },
    {
      accessorKey: "content",
      header: "Message",
      cell: ({ getValue }) => {
        const v = String(getValue() ?? "");
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
        <span className="text-foreground">Team pulse</span>
        <Badge variant={enabled ? "default" : "secondary"}>
          {enabled ? "Enabled" : "Disabled"}
        </Badge>
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
                Off = scheduled morning posts stop. Does not mean “skip if
                unchanged.” Preview / Post once still work when off so you can
                test.
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
              <Label htmlFor="tp-hour">Hour (CT)</Label>
              <Input
                id="tp-hour"
                type="number"
                min={0}
                max={23}
                className="min-h-11"
                value={hour}
                onChange={(e) => setHour(Number(e.target.value))}
              />
            </div>
            <div>
              <Label htmlFor="tp-minute">Minute</Label>
              <Input
                id="tp-minute"
                type="number"
                min={0}
                max={59}
                className="min-h-11"
                value={minute}
                onChange={(e) => setMinute(Number(e.target.value))}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Scheduler fires daily at 08:00 CT; posts only on days selected here.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Where to post</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant={destination === "dm" ? "default" : "outline"}
              className="min-h-11"
              onClick={() => setDestination("dm")}
            >
              DM (test)
            </Button>
            <Button
              type="button"
              variant={destination === "channel" ? "default" : "outline"}
              className="min-h-11"
              onClick={() => setDestination("channel")}
            >
              Group channel
            </Button>
          </div>

          {destination === "dm" ? (
            <div className="flex flex-col gap-1.5">
              <Label>Send DM to</Label>
              <Select
                value={dmUserId}
                onValueChange={(v) => {
                  if (v) setDmUserId(v);
                }}
              >
                <SelectTrigger className="min-h-11 w-full max-w-md">
                  <SelectValue placeholder="Choose a person…">
                    {(value: string | null) =>
                      value ? labelFor(memberOptions, value) : "Choose a person…"
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {memberOptions.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label>Channel</Label>
              <Select
                value={channelId}
                onValueChange={(v) => {
                  if (v) setChannelId(v);
                }}
              >
                <SelectTrigger className="min-h-11 w-full max-w-md">
                  <SelectValue placeholder="Choose a channel…">
                    {(value: string | null) =>
                      value ? labelFor(channelOptions, value) : "Choose a channel…"
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {channelOptions.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {clickupError ? (
            <p className="text-xs text-destructive">
              Couldn’t load ClickUp names: {clickupError}. Dropdowns may show
              ids until CLICKUP_PAT is set.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Keep on DM until the message looks right, then switch to the group
              channel.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Template</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">
            Use <code className="rounded bg-muted px-1">{"{leaderboard}"}</code>{" "}
            for the live ranking (always exact). Greeting + closers
            (“keep the momentum…”, “one team one fight”) are lightly rewritten
            by Gemini each Preview / Post so they stay fresh.
          </p>
          <textarea
            className="min-h-48 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          />
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          className="min-h-11"
          disabled={isPending}
          onClick={() => void onSave()}
        >
          Save
        </Button>
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={isPending}
          onClick={() => void onPreview()}
        >
          Preview
        </Button>
        <Button
          type="button"
          variant="secondary"
          className="min-h-11"
          disabled={isPending}
          onClick={() => void onPostOnce()}
        >
          Post once now
        </Button>
      </div>
      {(stage || error) && (
        <p
          className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}
        >
          {error ?? stage}
        </p>
      )}

      {preview && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="text-base">Preview</CardTitle>
            <Badge variant={previewVaried ? "default" : "secondary"}>
              {previewVaried ? "Gemini varied" : "Template (no vary)"}
            </Badge>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-sm">
              {preview}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Posted history</CardTitle>
        </CardHeader>
        <CardContent>
          {posts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No posts yet.</p>
          ) : (
            <DataTable columns={postColumns} data={posts} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
