"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { WhyItem } from "@/lib/recognition/context";
import { channelPost, dollars, mention } from "@/lib/recognition/draft";
import type { Member } from "@/lib/recognition/parse";
import { previewSegments } from "@/lib/recognition/reshape";
import type { GiftCardRow } from "@/lib/recognition/store";
import type { RecognitionView, WinnerView } from "@/lib/recognition/view";
import {
  approveRecognitionAction,
  dmRecognitionDraftAction,
  markGiftCardsIssuedAction,
  previewRecognitionAction,
  reshapeRecognitionPostAction,
  sendTestGiftCardAction,
  syncRecognitionSourcesAction,
} from "./actions";

type Props = {
  view: RecognitionView;
  members: Member[];
  giftCardsEnabled: boolean;
  squareProblem: string | null;
  senderEmail: string | null;
  leadClickupUserId: string | null;
};

type Row = {
  key: string;
  award: WinnerView["award"];
  resultName: string;
  userId: string | null;
  displayName: string;
  spoken: string;
  first: string;
  last: string;
  email: string;
  cards: number;
  cardDollars: number;
  bonusDollars: number;
  blurb: string;
  blurbSource: "gemini" | "template" | "edited";
};

const AUTO_SYNC_MS = 60 * 60_000;
const SOURCE_LABEL: Record<WhyItem["source"], string> = {
  nomination: "Nomination",
  running: "#running",
  coverage: "Shift coverage",
};

function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const min = Math.round(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

function shortDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", month: "short", day: "numeric" }).format(
    new Date(iso),
  );
}

function initialRows(view: RecognitionView, members: Member[]): Row[] {
  return view.winners.map((w) => {
    const ledger = view.giftCards.filter((g) => g.clickup_user_id === w.match.userId);
    const member = members.find((m) => m.userId === w.match.userId);
    return {
      key: w.key,
      award: w.award,
      resultName: w.resultName,
      userId: w.match.userId,
      displayName: member?.username || w.resultName,
      spoken: w.resultName.trim().split(/\s+/)[0],
      first: ledger[0]?.recipient_first ?? w.match.first,
      last: ledger[0]?.recipient_last ?? w.match.last,
      email: ledger[0]?.recipient_email ?? w.match.email ?? "",
      cards: ledger.length || w.prize.cards,
      cardDollars: (ledger[0]?.amount_cents ?? w.prize.cardCents) / 100,
      bonusDollars: w.prize.bonusDollars,
      blurb: w.blurb,
      blurbSource: "template",
    };
  });
}

function statusBadge(rows: GiftCardRow[]) {
  if (!rows.length) return <Badge variant="outline">Not issued</Badge>;
  if (rows.every((r) => r.status === "external")) return <Badge>Issued by you</Badge>;
  if (rows.some((r) => r.status === "failed")) return <Badge variant="destructive">Failed</Badge>;
  if (rows.every((r) => r.status === "emailed")) return <Badge>Emailed</Badge>;
  if (rows.some((r) => r.error)) return <Badge variant="destructive">Email failed</Badge>;
  return <Badge variant="secondary">{rows[0].status}</Badge>;
}

export function RecognitionEditor({
  view,
  members,
  giftCardsEnabled,
  squareProblem,
  senderEmail,
  leadClickupUserId,
}: Props) {
  const router = useRouter();
  const { run, isPending, stage, error } = useConsoleAction();
  const [rows, setRows] = useState<Row[]>(() => initialRows(view, members));
  const [emailedLine, setEmailedLine] = useState(true);
  const [postOverride, setPostOverride] = useState<string | null>(null);
  const [editingPost, setEditingPost] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [confirmIssue, setConfirmIssue] = useState(false);
  const [confirmMark, setConfirmMark] = useState(false);
  const autoSynced = useRef(false);
  const autoDrafted = useRef(false);

  const ledgerByUser = useMemo(() => {
    const map = new Map<string, GiftCardRow[]>();
    for (const g of view.giftCards) map.set(g.clickup_user_id, [...(map.get(g.clickup_user_id) ?? []), g]);
    return map;
  }, [view.giftCards]);

  const leadMention = useMemo(() => {
    const lead = members.find((m) => m.userId === leadClickupUserId);
    return lead ? mention({ clickupUserId: lead.userId, displayName: lead.username }) : null;
  }, [members, leadClickupUserId]);

  const generatedPost = useMemo(
    () =>
      channelPost({
        awardMonth: view.awardMonth,
        winners: rows.map((r) => ({
          award: r.award,
          clickupUserId: r.userId,
          displayName: r.displayName,
          first: r.spoken,
          blurb: r.blurb,
        })),
        giftCardsEmailed: emailedLine,
        leadMention,
      }),
    [rows, emailedLine, leadMention, view.awardMonth],
  );
  const post = postOverride ?? generatedPost;

  const totalCents = rows.reduce((s, r) => s + r.cards * Math.round(r.cardDollars * 100), 0);
  const totalCards = rows.reduce((s, r) => s + r.cards, 0);
  const missing = rows.filter((r) => !r.userId || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email));
  const anyFailed = view.giftCards.some((g) => g.status === "failed" || g.error);
  const allDone = rows.length > 0 && rows.every((r) => {
    const l = r.userId ? ledgerByUser.get(r.userId) ?? [] : [];
    return l.length > 0 && l.every((g) => g.status === "emailed" || g.status === "external");
  });
  const noneRecorded = rows.every((r) => !(r.userId && ledgerByUser.get(r.userId)?.length));
  const issueBlocked = !giftCardsEnabled
    ? "Gift cards are off — set CONSOLE_RECOGNITION_GIFT_CARDS=1"
    : squareProblem
      ? squareProblem
      : !senderEmail
        ? "No Gmail sender grant — run scripts/gmail_sender_grant.py"
        : missing.length
          ? `Fill in ClickUp user + email for ${missing.map((m) => m.resultName).join(", ")}`
          : null;

  const lastSyncAt = view.lastSync?.finished_at ?? view.lastSync?.started_at ?? null;

  useEffect(() => {
    if (autoSynced.current) return;
    autoSynced.current = true;
    const stale = !lastSyncAt || Date.now() - Date.parse(lastSyncAt) > AUTO_SYNC_MS;
    if (stale && view.lastSync?.status !== "running") {
      void run(() => syncRecognitionSourcesAction("auto"), { saving: "Syncing ClickUp…" }).then((ack) => {
        if (ack.ok) router.refresh();
      });
    }
  }, [lastSyncAt, view.lastSync?.status, run, router]);

  function patch(key: string, p: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...p } : r)));
  }

  function pickMember(key: string, userId: string) {
    const m = members.find((x) => x.userId === userId);
    if (!m) return;
    const [first, ...rest] = m.username.split(/\s+/);
    patch(key, { userId, displayName: m.username, first, last: rest.join(" "), email: m.email ?? "" });
  }

  async function onSync() {
    const ack = await run(() => syncRecognitionSourcesAction("console"), { saving: "Syncing ClickUp…" });
    if (ack.ok) router.refresh();
  }

  // Called directly (not via `run`) so it can overlap the auto-sync without tripping single-flight.
  const draftBlurbs = useCallback(async () => {
    setDrafting(true);
    try {
      const ack = await previewRecognitionAction(view.awardMonth);
      if (ack.ok && ack.data) {
        const blurbs = ack.data.blurbs;
        setRows((prev) =>
          prev.map((r) =>
            blurbs[r.key] && r.blurbSource !== "edited"
              ? { ...r, blurb: blurbs[r.key].text, blurbSource: blurbs[r.key].source }
              : r,
          ),
        );
      }
    } finally {
      setDrafting(false);
    }
  }, [view.awardMonth]);

  useEffect(() => {
    if (autoDrafted.current || !view.winners.length) return;
    autoDrafted.current = true;
    void draftBlurbs();
  }, [draftBlurbs, view.winners.length]);

  async function onReshape() {
    const ack = await run(
      () => reshapeRecognitionPostAction({ awardMonth: view.awardMonth, post, instruction }),
      { saving: "Reshaping with Gemini…" },
    );
    if (ack.ok && ack.data) {
      setPostOverride(ack.data.post);
      setEditingPost(false);
    }
  }

  async function onMarkIssued() {
    setConfirmMark(false);
    await run(
      () =>
        markGiftCardsIssuedAction({
          awardMonth: view.awardMonth,
          recipients: rows.map((r) => ({
            clickupUserId: r.userId ?? "",
            award: r.award,
            displayName: r.displayName,
            first: r.first,
            last: r.last,
            email: r.email,
            cards: r.cards,
            cardCents: Math.round(r.cardDollars * 100),
          })),
        }),
      { saving: "Recording…" },
    );
    router.refresh();
  }

  function summaryLines(): string[] {
    return rows.map(
      (r) =>
        `${r.award} — ${r.first} ${r.last} <${r.email || "no email"}>: ${r.cards} × ${dollars(Math.round(r.cardDollars * 100))} gift cards; ${dollars(r.bonusDollars * 100)} bonus via payroll`,
    );
  }

  async function onDm() {
    await run(
      () => dmRecognitionDraftAction({ awardMonth: view.awardMonth, post, giftCardSummary: summaryLines() }),
      { saving: "Sending draft DM…" },
    );
  }

  async function onIssue(resume: boolean) {
    setConfirmIssue(false);
    const ack = await run(
      () =>
        approveRecognitionAction({
          awardMonth: view.awardMonth,
          resume,
          recipients: rows.map((r) => ({
            clickupUserId: r.userId ?? "",
            award: r.award,
            displayName: r.displayName,
            first: r.first,
            last: r.last,
            email: r.email,
            cards: r.cards,
            cardCents: Math.round(r.cardDollars * 100),
          })),
        }),
      { saving: "Creating gift cards in Square…" },
    );
    if (ack.ok) setEmailedLine(true);
    router.refresh();
  }

  async function onTest() {
    await run(() => sendTestGiftCardAction(100), { saving: "Creating $1 test card…" });
    router.refresh();
  }

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
        <span className="text-foreground">Monthly recognition</span>
        <Badge variant="outline" className="font-normal">
          {view.label}
        </Badge>
        <span className="ml-auto flex items-center gap-2">
          <span className="text-xs">
            ClickUp synced {ago(lastSyncAt)}
            {view.lastSync?.status === "failed" ? " · last sync failed" : ""}
          </span>
          <Button type="button" size="sm" variant="outline" className="min-h-9" disabled={isPending} onClick={() => void onSync()}>
            Sync now
          </Button>
        </span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sources</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5 text-sm">
          {view.thread ? (
            <p>
              Nomination thread{" "}
              <a className="text-primary underline-offset-4 hover:underline" href={view.thread.url} target="_blank" rel="noreferrer">
                opened {shortDate(view.thread.postedAt)}
              </a>{" "}
              <span className="text-muted-foreground">· {view.thread.replies} replies</span>
            </p>
          ) : (
            <p className="text-muted-foreground">No “{view.label.split(" ")[0]} Nominations” thread in #monthly-recognition yet.</p>
          )}
          {view.resultsUrl ? (
            <p>
              Results{" "}
              <a className="text-primary underline-offset-4 hover:underline" href={view.resultsUrl} target="_blank" rel="noreferrer">
                posted {view.resultsAt ? shortDate(view.resultsAt) : ""}
              </a>{" "}
              <Badge variant="secondary" className="font-normal">
                {view.resultsSource === "thread-reply" ? "thread reply" : view.resultsSource === "channel" ? "channel post" : "weekly update"}
              </Badge>
            </p>
          ) : (
            <p className="text-muted-foreground">
              No results line yet — post e.g. “MVP → Name / High Five → Name” in the thread, then Sync now.
            </p>
          )}
        </CardContent>
      </Card>

      {rows.map((r) => {
        const w = view.winners.find((x) => x.key === r.key)!;
        const ledger = r.userId ? ledgerByUser.get(r.userId) ?? [] : [];
        const s = w.why.shiftsInMonth;
        const after = w.why.shiftsAfterMonth;
        const peopleItems = w.why.items.filter((it) => it.author !== "Automation");
        const locked = ledger.length > 0;
        return (
          <Card key={r.key}>
            <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
              <div className="flex flex-col gap-1">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Badge variant={r.award === "MVP" ? "default" : "secondary"}>{r.award === "MVP" ? "🏆 MVP" : "🙌 High Five"}</Badge>
                  {r.displayName}
                </CardTitle>
                <p className="text-xs text-muted-foreground">
                  Results said “{r.resultName}” · ClickUp match: {w.match.how}
                  {w.rosterName ? ` · payroll ${w.rosterName}` : ""}
                </p>
              </div>
              {statusBadge(ledger)}
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {w.why.flags.length ? (
                <ul className="flex flex-col gap-1 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200">
                  {w.why.flags.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              ) : null}

              {!r.userId ? (
                <div className="flex flex-col gap-1.5">
                  <Label>ClickUp member</Label>
                  <Select value={r.userId ?? ""} onValueChange={(v) => v && pickMember(r.key, v)}>
                    <SelectTrigger className="min-h-11 w-full max-w-md">
                      <SelectValue placeholder="Choose who this is…" />
                    </SelectTrigger>
                    <SelectContent>
                      {members
                        .filter((m) => m.username)
                        .sort((a, b) => a.username.localeCompare(b.username))
                        .map((m) => (
                          <SelectItem key={m.userId} value={m.userId}>
                            {m.username}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <div>
                  <Label htmlFor={`${r.key}-first`}>First name</Label>
                  <Input id={`${r.key}-first`} className="min-h-11" disabled={locked} value={r.first} onChange={(e) => patch(r.key, { first: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor={`${r.key}-last`}>Last name</Label>
                  <Input id={`${r.key}-last`} className="min-h-11" disabled={locked} value={r.last} onChange={(e) => patch(r.key, { last: e.target.value })} />
                </div>
                <div className="lg:col-span-1 sm:col-span-2">
                  <Label htmlFor={`${r.key}-email`}>Email</Label>
                  <Input id={`${r.key}-email`} type="email" className="min-h-11" disabled={locked} value={r.email} onChange={(e) => patch(r.key, { email: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor={`${r.key}-cards`}>Gift cards</Label>
                  <Input id={`${r.key}-cards`} type="number" min={1} max={4} className="min-h-11" disabled={locked} value={r.cards} onChange={(e) => patch(r.key, { cards: Number(e.target.value) })} />
                </div>
                <div>
                  <Label htmlFor={`${r.key}-amt`}>Each ($)</Label>
                  <Input id={`${r.key}-amt`} type="number" min={1} max={50} step={1} className="min-h-11" disabled={locked} value={r.cardDollars} onChange={(e) => patch(r.key, { cardDollars: Number(e.target.value) })} />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {r.cards} × {dollars(Math.round(r.cardDollars * 100))} = {dollars(r.cards * Math.round(r.cardDollars * 100))} in gift cards ·{" "}
                {dollars(r.bonusDollars * 100)} bonus goes through payroll (not issued here)
                {ledger.some((g) => g.gan_last4)
                  ? ` · cards ${ledger.filter((g) => g.gan_last4).map((g) => `•••${g.gan_last4}`).join(", ")}`
                  : ""}
              </p>

              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <Label htmlFor={`${r.key}-blurb`}>Why (goes in the post)</Label>
                  <Badge variant="outline" className="font-normal">
                    {drafting && r.blurbSource === "template"
                      ? "Drafting…"
                      : { gemini: "Drafted from ClickUp", template: "Template", edited: "Edited" }[r.blurbSource]}
                  </Badge>
                </div>
                <textarea
                  id={`${r.key}-blurb`}
                  className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={r.blurb}
                  onChange={(e) => {
                    patch(r.key, { blurb: e.target.value, blurbSource: "edited" });
                    setPostOverride(null);
                  }}
                />
              </div>

              <details className="group rounded-md border bg-muted/20">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-sm font-medium hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span>
                    Context from ClickUp · {peopleItems.length} messages
                  </span>
                  <span className="text-xs text-muted-foreground group-open:hidden">Show</span>
                  <span className="hidden text-xs text-muted-foreground group-open:inline">Hide</span>
                </summary>
                <div className="flex flex-col gap-3 border-t px-3 py-3">
                  <p className="text-xs text-muted-foreground">
                    {s ? `${view.label.split(" ")[0]}: ${s.days} shift days · ${s.hours} h · ${s.opening} opens · ${s.closing} closes` : "No shifts in the award month"}
                    {after && after.days ? ` · after month: ${after.days} days, ${after.closing} closes (${after.first_date} → ${after.last_date})` : ""}
                  </p>
                  {peopleItems.length ? (
                    <ul className="flex flex-col divide-y">
                      {peopleItems.map((it) => (
                        <li key={it.url + it.at} className="flex flex-col gap-1 py-2 text-sm">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <Badge variant={it.source === "nomination" ? "default" : "outline"} className="font-normal">
                              {SOURCE_LABEL[it.source]}
                            </Badge>
                            <span>{shortDate(it.at)}</span>
                            <span>· {it.author}</span>
                            <a className="ml-auto text-primary underline-offset-4 hover:underline" href={it.url} target="_blank" rel="noreferrer">
                              Open
                            </a>
                          </div>
                          <p className="line-clamp-4 whitespace-pre-wrap">{it.text}</p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">No messages mention them in this window.</p>
                  )}
                </div>
              </details>
            </CardContent>
          </Card>
        );
      })}

      {rows.length ? (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
              <CardTitle className="text-base">Team post · DM&apos;d to you to forward</CardTitle>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="min-h-9"
                aria-pressed={editingPost}
                onClick={() => setEditingPost((v) => !v)}
              >
                {editingPost ? "Preview" : "Edit text"}
              </Button>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <label className="flex min-h-11 items-center gap-2 text-sm">
                <input type="checkbox" className="size-4 accent-primary" checked={emailedLine} onChange={(e) => { setEmailedLine(e.target.checked); setPostOverride(null); }} />
                <span>Say gift cards were emailed (otherwise “reach out to your shift lead to collect”)</span>
              </label>
              {editingPost ? (
                <>
                  <textarea
                    aria-label="Team post"
                    className="min-h-72 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-[13px] leading-relaxed shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    value={post}
                    onChange={(e) => setPostOverride(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    <code>[@Name](#user_mention#…)</code> is ClickUp&apos;s mention markup — it shows up as a clickable @mention in ClickUp. Keep it intact.
                  </p>
                </>
              ) : (
                <div className="whitespace-pre-wrap rounded-md border bg-muted/20 px-4 py-3 text-sm leading-relaxed">
                  {previewSegments(post).map((s, i) =>
                    s.mention ? (
                      <span key={i} className="rounded bg-primary/10 px-1 font-medium text-primary">
                        {s.text}
                      </span>
                    ) : (
                      <span key={i}>{s.text}</span>
                    ),
                  )}
                </div>
              )}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="flex flex-1 flex-col gap-1.5">
                  <Label htmlFor="reshape-instruction">Reshape with a prompt</Label>
                  <Input
                    id="reshape-instruction"
                    className="min-h-11"
                    placeholder="e.g. shorter and punchier, more emojis, thank the closing crew"
                    value={instruction}
                    maxLength={1000}
                    onChange={(e) => setInstruction(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && instruction.trim() && !isPending) void onReshape();
                    }}
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="min-h-11"
                  disabled={isPending || drafting || !instruction.trim()}
                  onClick={() => void onReshape()}
                >
                  Reshape
                </Button>
              </div>
              <div className="flex flex-wrap gap-3 text-xs">
                {postOverride !== null ? (
                  <button type="button" className="text-primary underline-offset-4 hover:underline" onClick={() => setPostOverride(null)}>
                    Reset to generated
                  </button>
                ) : null}
                <button
                  type="button"
                  className="text-primary underline-offset-4 hover:underline disabled:opacity-50"
                  disabled={drafting}
                  onClick={() => void draftBlurbs()}
                >
                  {drafting ? "Drafting “why” from ClickUp…" : "Redraft “why” from ClickUp"}
                </button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Gift cards</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 pr-3 font-medium">Recipient</th>
                      <th className="py-2 pr-3 font-medium">Award</th>
                      <th className="py-2 pr-3 font-medium">Email</th>
                      <th className="py-2 pr-3 text-right font-medium">Cards</th>
                      <th className="py-2 pr-3 text-right font-medium">Total</th>
                      <th className="py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.key} className="border-b last:border-0">
                        <td className="py-2 pr-3">{`${r.first} ${r.last}`.trim()}</td>
                        <td className="py-2 pr-3">{r.award}</td>
                        <td className="py-2 pr-3 text-muted-foreground">{r.email || "—"}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {r.cards} × {dollars(Math.round(r.cardDollars * 100))}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">{dollars(r.cards * Math.round(r.cardDollars * 100))}</td>
                        <td className="py-2">{statusBadge(r.userId ? ledgerByUser.get(r.userId) ?? [] : [])}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="font-medium">
                      <td className="pt-2" colSpan={3}>
                        Total
                      </td>
                      <td className="pt-2 pr-3 text-right tabular-nums">{totalCards}</td>
                      <td className="pt-2 pr-3 text-right tabular-nums">{dollars(totalCents)}</td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
              <p className="text-xs text-muted-foreground">
                Digital cards at the Austin Square location, emailed from {senderEmail ?? "— (no sender grant)"}. Square documents a
                2.5% load fee on US gift cards, deducted from payouts. The recap lands in your ClickUp DM.
              </p>
              {issueBlocked ? <p className="text-xs text-destructive">{issueBlocked}</p> : null}

              {confirmMark ? (
                <div className="flex flex-col gap-2 rounded-md border bg-muted/30 p-3">
                  <p className="text-sm">
                    Record {totalCards} cards ({dollars(totalCents)}) for {rows.map((r) => r.first).join(", ")} as bought and sent
                    by you? Nothing is created or emailed — this only stops the console from issuing them again.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" className="min-h-11" disabled={isPending} onClick={() => void onMarkIssued()}>
                      Confirm
                    </Button>
                    <Button type="button" variant="outline" className="min-h-11" onClick={() => setConfirmMark(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : confirmIssue ? (
                <div className="flex flex-col gap-2 rounded-md border border-primary/40 bg-primary/5 p-3">
                  <p className="text-sm">
                    Create and email <span className="font-medium">{totalCards} gift cards ({dollars(totalCents)})</span> to{" "}
                    {rows.map((r) => r.first).join(", ")}? This moves real money in Square.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" className="min-h-11" disabled={isPending} onClick={() => void onIssue(false)}>
                      Confirm &amp; issue
                    </Button>
                    <Button type="button" variant="outline" className="min-h-11" onClick={() => setConfirmIssue(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    className="min-h-11"
                    disabled={isPending || Boolean(issueBlocked) || allDone}
                    onClick={() => setConfirmIssue(true)}
                  >
                    {allDone ? "All gift cards issued" : "Approve & issue gift cards"}
                  </Button>
                  {noneRecorded ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11"
                      disabled={isPending || rows.some((r) => !r.userId)}
                      onClick={() => setConfirmMark(true)}
                    >
                      I already issued these
                    </Button>
                  ) : null}
                  {anyFailed ? (
                    <Button type="button" variant="outline" className="min-h-11" disabled={isPending || Boolean(issueBlocked)} onClick={() => void onIssue(true)}>
                      Retry failed
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    className="min-h-11"
                    disabled={isPending || !giftCardsEnabled || Boolean(squareProblem) || !senderEmail}
                    onClick={() => void onTest()}
                  >
                    Send me a $1 test card
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" className="min-h-11" disabled={isPending || drafting} onClick={() => void onDm()}>
              DM me the post to forward
            </Button>
            <span className="text-xs text-muted-foreground">
              Two ClickUp DMs: the gift-card summary, then the post on its own so you can forward it.
            </span>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No winners for {view.label} yet.</p>
      )}

      {(stage || error) && (
        <p className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>{error ?? stage}</p>
      )}
    </div>
  );
}
