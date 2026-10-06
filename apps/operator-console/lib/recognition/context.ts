// Pure "Why" panel builder for Monthly recognition (Issue #369): collects the
// nomination replies and auxiliary-channel messages that explain each award.
// Inputs are the BQ copy of ClickUp chat (clickup_chat_messages) + adp_shifts
// aggregates; no IO here.

import { mentionIds, stripMentions, type Award, type MemberMatch } from "./parse";

export const CLICKUP_WORKSPACE = "9017956545";

export type ChannelRole = "recognition" | "running" | "coverage";

export type ChatRow = {
  channel_id: string;
  message_id: string;
  parent_message_id: string | null;
  user_id: string | null;
  posted_at: string; // ISO timestamp
  content: string | null;
};

export type ShiftAgg = {
  days: number;
  hours: number;
  opening: number;
  closing: number;
  first_date: string | null;
  last_date: string | null;
};

export type WhyItem = {
  source: "nomination" | "running" | "coverage";
  at: string;
  author: string;
  text: string;
  url: string;
};

export type WhyPanel = {
  items: WhyItem[];
  checklistShoutouts: number;
  checklistTasks: number;
  shiftNotes: number;
  shiftsInMonth: ShiftAgg | null;
  shiftsAfterMonth: ShiftAgg | null;
  flags: string[];
};

export type Winner = {
  award: Award;
  resultName: string;
  match: MemberMatch;
  rosterName: string | null;
  rosterHow: "exact" | "last-name" | "none";
};

export function messageUrl(row: Pick<ChatRow, "channel_id" | "message_id" | "parent_message_id">): string {
  const thread = row.parent_message_id ?? row.message_id;
  return `https://app.clickup.com/${CLICKUP_WORKSPACE}/chat/r/${row.channel_id}/t/${thread}`;
}

const CHECKLIST_RE = /Checklist:\s*\d+\/\d+/;
const FORM_RE = /^Submitted by:\s*(.+?)\s*\|?\s*$/m;
const MIN_AUTHORED_CHARS = 40;
const MAX_AUX_ITEMS = 30;

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Names that identify the winner in free text (first name + the name used in the results line). */
function nameTokens(w: Winner): string[] {
  const tokens = new Set<string>();
  for (const t of [w.match.first, w.resultName.split(/\s+/)[0]]) {
    if (t && t.length >= 3) tokens.add(t);
  }
  return [...tokens];
}

function mentionsWinner(content: string, w: Winner, tokens: string[]): boolean {
  if (w.match.userId && mentionIds(content).some((m) => m.id === w.match.userId)) return true;
  const plain = stripMentions(content);
  return tokens.some((t) => new RegExp(`\\b${escapeRe(t)}\\b`, "i").test(plain));
}

/** "@Dolce Johnson – 10" shoutout count for this winner in a checklist post, else 0. */
function checklistTasksFor(content: string, w: Winner): number {
  if (!w.match.userId) return 0;
  const re = new RegExp(
    `\\[@[^\\]]+\\]\\(#user_mention#${escapeRe(w.match.userId)}\\)\\s*[–-]\\s*(\\d+)`,
  );
  const m = content.match(re);
  return m ? Number(m[1]) : 0;
}

function cleanText(content: string): string {
  return stripMentions(content)
    .replace(/!\[[^\]]*\]\([^)]+\)/g, "[image]")
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, "$1")
    .replace(/\\([*_~-])/g, "$1")
    .trim();
}

export function buildWhyPanel(input: {
  winner: Winner;
  nominationReplies: ChatRow[];
  auxMessages: (ChatRow & { role: ChannelRole })[];
  memberNames: Record<string, string>;
  windowStartIso: string;
  windowEndIso: string;
  shiftsInMonth: ShiftAgg | null;
  shiftsAfterMonth: ShiftAgg | null;
  resultsMessageIds: string[];
}): WhyPanel {
  const w = input.winner;
  const tokens = nameTokens(w);
  const author = (id: string | null) => (id && input.memberNames[id]) || "Automation";
  const items: WhyItem[] = [];

  for (const r of input.nominationReplies) {
    if (input.resultsMessageIds.includes(r.message_id)) continue;
    const content = r.content ?? "";
    if (!mentionsWinner(content, w, tokens)) continue;
    items.push({
      source: "nomination",
      at: r.posted_at,
      author: author(r.user_id),
      text: cleanText(content),
      url: messageUrl(r),
    });
  }

  let checklistShoutouts = 0;
  let checklistTasks = 0;
  let shiftNotes = 0;
  const aux: WhyItem[] = [];
  for (const r of input.auxMessages) {
    if (r.posted_at < input.windowStartIso || r.posted_at > input.windowEndIso) continue;
    const content = r.content ?? "";
    if (CHECKLIST_RE.test(content)) {
      const n = checklistTasksFor(content, w);
      if (n > 0) {
        checklistShoutouts += 1;
        checklistTasks += n;
      }
      continue;
    }
    const form = content.match(FORM_RE);
    if (form) {
      const submitter = stripMentions(form[1]).trim().toLowerCase();
      const full = `${w.match.first} ${w.match.last}`.trim().toLowerCase();
      if (full && submitter === full) shiftNotes += 1;
      continue;
    }
    const byWinner = Boolean(w.match.userId) && r.user_id === w.match.userId;
    const mentioned = mentionsWinner(content, w, tokens);
    if (!mentioned && !(byWinner && cleanText(content).length >= MIN_AUTHORED_CHARS)) continue;
    aux.push({
      source: r.role === "coverage" ? "coverage" : "running",
      at: r.posted_at,
      author: author(r.user_id),
      text: cleanText(content),
      url: messageUrl(r),
    });
  }
  aux.sort((a, b) => a.at.localeCompare(b.at));
  items.push(...aux.slice(-MAX_AUX_ITEMS));

  const flags: string[] = [];
  if (w.match.how === "prefix") {
    flags.push(`"${w.resultName}" matched ClickUp member "${w.match.first} ${w.match.last}" by prefix — confirm`);
  }
  if (w.match.how === "ambiguous") flags.push(`"${w.resultName}" matches more than one ClickUp member — pick one`);
  if (w.match.how === "none") flags.push(`"${w.resultName}" not found in ClickUp members — enter name and email`);
  if (w.match.userId && !w.match.email) flags.push("No email on the ClickUp member — enter one");
  if (w.rosterHow === "last-name") {
    flags.push(`Payroll name "${w.rosterName}" matched by last name only — confirm same person`);
  }
  if (w.rosterHow === "none") flags.push("Not found on the payroll roster");
  const inMonth = input.shiftsInMonth?.days ?? 0;
  const after = input.shiftsAfterMonth;
  if (inMonth === 0 && after && after.days > 0) {
    flags.push(
      `No shifts in the award month — ${after.days} shift day(s) ${after.first_date} to ${after.last_date} fall after it`,
    );
  }
  if (!items.some((i) => i.source === "nomination")) flags.push("No nomination reply names this winner");

  return {
    items,
    checklistShoutouts,
    checklistTasks,
    shiftNotes,
    shiftsInMonth: input.shiftsInMonth,
    shiftsAfterMonth: input.shiftsAfterMonth,
    flags,
  };
}
