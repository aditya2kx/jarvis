// Pure: locate a month's nomination thread and its results in the BQ copy of
// ClickUp chat (Issue #369).

import type { ChatRow } from "./context";
import { monthName } from "./month";
import { monthOfThread, parseInlineAnnouncement, parseResults, type ResultLine } from "./parse";

export type ResultsSource = "thread-reply" | "channel" | "announcement";

export type ResolvedMonth = {
  thread: ChatRow | null;
  replies: ChatRow[];
  results: ResultLine[];
  resultsMessage: ChatRow | null;
  resultsSource: ResultsSource | null;
};

function newestFirst(rows: ChatRow[]): ChatRow[] {
  return [...rows].sort((a, b) => b.posted_at.localeCompare(a.posted_at));
}

export function resolveMonth(input: {
  awardMonth: string;
  recognitionChannelId: string;
  recognitionRows: ChatRow[];
  runningRows: ChatRow[];
}): ResolvedMonth {
  const top = input.recognitionRows.filter((r) => !r.parent_message_id);
  const candidates = top.filter(
    (r) => monthOfThread(r.content ?? "", r.posted_at) === input.awardMonth,
  );
  const withReplies = (t: ChatRow) =>
    input.recognitionRows.filter((r) => r.parent_message_id === t.message_id);
  const thread =
    newestFirst(candidates).sort((a, b) => withReplies(b).length - withReplies(a).length)[0] ??
    null;
  const replies = thread
    ? withReplies(thread).sort((a, b) => a.posted_at.localeCompare(b.posted_at))
    : [];

  const empty: ResolvedMonth = {
    thread,
    replies,
    results: [],
    resultsMessage: null,
    resultsSource: null,
  };

  for (const r of newestFirst(replies)) {
    const lines = parseResults(r.content ?? "");
    if (lines.length) {
      return { ...empty, results: lines, resultsMessage: r, resultsSource: "thread-reply" };
    }
  }

  // A top-level results post belongs to the latest thread month that is
  // earlier than the post's own month (August results land in early September,
  // after the September thread has already opened).
  const threadMonths = top
    .map((r) => ({ at: r.posted_at, month: monthOfThread(r.content ?? "", r.posted_at) }))
    .filter((x): x is { at: string; month: string } => x.month !== null);
  for (const r of newestFirst(top)) {
    const lines = parseResults(r.content ?? "");
    if (!lines.length) continue;
    const postMonth = r.posted_at.slice(0, 7);
    const owner = threadMonths
      .filter((x) => x.at < r.posted_at && x.month < postMonth)
      .map((x) => x.month)
      .sort()
      .pop();
    if (owner === input.awardMonth) {
      return { ...empty, results: lines, resultsMessage: r, resultsSource: "channel" };
    }
  }

  const name = monthName(input.awardMonth).toLowerCase();
  for (const r of newestFirst(input.runningRows.filter((x) => !x.parent_message_id))) {
    const text = (r.content ?? "").toLowerCase();
    if (!text.includes(name) || !/\*\*mvp\*\*/.test(text)) continue;
    const lines = parseInlineAnnouncement(r.content ?? "");
    if (lines.length) {
      return { ...empty, results: lines, resultsMessage: r, resultsSource: "announcement" };
    }
  }
  return empty;
}
