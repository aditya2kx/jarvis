import "server-only";

import { DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickup";
import { RECOGNITION } from "@/lib/config/stores";
import { buildWhyPanel, messageUrl, type ChatRow, type WhyPanel } from "@/lib/recognition/context";
import { DEFAULT_PRIZES, fallbackBlurb, type Prize } from "@/lib/recognition/draft";
import { monthBounds, monthLabel } from "@/lib/recognition/month";
import { matchMember, matchRoster, type Award, type MemberMatch } from "@/lib/recognition/parse";
import { resolveMonth, type ResultsSource } from "@/lib/recognition/resolve";
import {
  chatRows,
  giftCardsForMonth,
  latestSyncRun,
  members as loadMembers,
  rosterNames,
  shiftAggs,
  type GiftCardRow,
  type SyncRun,
} from "@/lib/recognition/store";

// Assembles everything the Monthly recognition page needs for one award month
// from BQ only (no live ClickUp calls on page load).

export type WinnerView = {
  key: string;
  award: Award;
  resultName: string;
  match: MemberMatch;
  rosterName: string | null;
  rosterHow: "exact" | "last-name" | "none";
  why: WhyPanel;
  prize: Prize;
  blurb: string;
};

export type RecognitionView = {
  awardMonth: string;
  label: string;
  thread: { url: string; postedAt: string; replies: number } | null;
  resultsSource: ResultsSource | null;
  resultsUrl: string | null;
  resultsAt: string | null;
  winners: WinnerView[];
  lastSync: SyncRun | null;
  giftCards: GiftCardRow[];
};

/** The name the team uses in copy — the results-line name ("Linh"), not the ClickUp first name ("Linhchi"). */
export function spokenName(resultName: string): string {
  return resultName.trim().split(/\s+/)[0] ?? resultName;
}

const DAY_MS = 86_400_000;
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export async function loadRecognitionView(store: string, awardMonth: string): Promise<RecognitionView> {
  const cfg = RECOGNITION[store];
  if (!cfg) throw new Error(`No recognition config for store ${store}`);
  const { start, end } = monthBounds(awardMonth);
  const monthStartMs = Date.parse(`${start}T05:00:00Z`);
  const monthEndMs = Date.parse(`${end}T05:00:00Z`) + DAY_MS;
  const nowIso = new Date().toISOString();

  const [recRows, auxRows, memberList, roster, lastSync, giftCards] = await Promise.all([
    chatRows([cfg.recognitionChannelId], new Date(monthStartMs - 75 * DAY_MS).toISOString(), nowIso),
    chatRows(
      [cfg.runningChannelId, cfg.coverageChannelId],
      new Date(monthStartMs).toISOString(),
      new Date(monthEndMs + 21 * DAY_MS).toISOString(),
    ),
    loadMembers(DEFAULT_WORKSPACE_ID),
    rosterNames(),
    latestSyncRun(store),
    giftCardsForMonth(store, awardMonth),
  ]);

  const resolved = resolveMonth({
    awardMonth,
    recognitionChannelId: cfg.recognitionChannelId,
    recognitionRows: recRows,
    runningRows: auxRows.filter((r) => r.channel_id === cfg.runningChannelId),
  });

  const resultsAtMs = resolved.resultsMessage ? Date.parse(resolved.resultsMessage.posted_at) : null;
  const windowEndMs = Math.max(resultsAtMs ?? monthEndMs + 7 * DAY_MS, monthEndMs);
  const afterStart = isoDay(monthEndMs + DAY_MS / 2);
  const afterEnd = isoDay(windowEndMs);
  const [inMonth, afterMonth] = await Promise.all([
    shiftAggs(start, end),
    afterEnd >= afterStart ? shiftAggs(afterStart, afterEnd) : Promise.resolve({}),
  ]);

  const memberNames = Object.fromEntries(memberList.map((m) => [m.userId, m.username]));
  const auxWithRole = auxRows.map((r: ChatRow) => ({
    ...r,
    role: r.channel_id === cfg.coverageChannelId ? ("coverage" as const) : ("running" as const),
  }));

  const winners: WinnerView[] = [];
  for (const line of resolved.results) {
    line.names.forEach((name, i) => {
      const match = matchMember(name, line.mentionIds[i] ?? null, memberList);
      const roster_ = matchRoster(match.first, match.last, roster);
      const winner = { award: line.award, resultName: name, match, rosterName: roster_.canonical, rosterHow: roster_.how };
      const why = buildWhyPanel({
        winner,
        nominationReplies: resolved.replies,
        auxMessages: auxWithRole,
        memberNames,
        windowStartIso: new Date(monthStartMs).toISOString(),
        windowEndIso: new Date(windowEndMs).toISOString(),
        shiftsInMonth: roster_.canonical ? (inMonth as Record<string, WhyPanel["shiftsInMonth"]>)[roster_.canonical] ?? null : null,
        shiftsAfterMonth: roster_.canonical ? (afterMonth as Record<string, WhyPanel["shiftsAfterMonth"]>)[roster_.canonical] ?? null : null,
        resultsMessageIds: resolved.resultsMessage ? [resolved.resultsMessage.message_id] : [],
      });
      winners.push({
        key: `${line.award}:${match.userId ?? name}`,
        ...winner,
        why,
        prize: DEFAULT_PRIZES[line.award],
        blurb: fallbackBlurb(
          spokenName(name),
          why.items.filter((x) => x.source === "nomination").map((x) => x.text),
        ),
      });
    });
  }

  return {
    awardMonth,
    label: monthLabel(awardMonth),
    thread: resolved.thread
      ? { url: messageUrl(resolved.thread), postedAt: resolved.thread.posted_at, replies: resolved.replies.length }
      : null,
    resultsSource: resolved.resultsSource,
    resultsUrl: resolved.resultsMessage ? messageUrl(resolved.resultsMessage) : null,
    resultsAt: resolved.resultsMessage?.posted_at ?? null,
    winners,
    lastSync,
    giftCards,
  };
}
