// Pure parsing for the Monthly recognition automation (Issue #369): nomination
// thread titles, results lines, and winner → ClickUp member / payroll roster
// matching. No IO — unit-tested against real #monthly-recognition messages.

export type Award = "MVP" | "High Five";

export type ResultLine = { award: Award; names: string[]; mentionIds: string[] };

export type Member = { userId: string; username: string; email: string | null };

export type MatchHow = "mention" | "exact" | "prefix" | "ambiguous" | "none";

export type MemberMatch = {
  name: string;
  userId: string | null;
  first: string;
  last: string;
  email: string | null;
  how: MatchHow;
};

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];

const MENTION_RE = /\[@([^\]]+)\]\(#user_mention#(\d+)\)/g;

/** Replace ClickUp mention markdown with `@Name`. */
export function stripMentions(content: string): string {
  return content.replace(MENTION_RE, "@$1");
}

/** Mention ids in order of appearance. */
export function mentionIds(content: string): { name: string; id: string }[] {
  return [...content.matchAll(MENTION_RE)].map((m) => ({ name: m[1], id: m[2] }));
}

/**
 * 'YYYY-MM' for a nomination thread top-level ("🧵 September Nominations",
 * "🧵to capture nominations for May", "🧵 October Recognition"), else null.
 * Year comes from the post date; a month name more than 6 months before the
 * post month belongs to the next year (a thread opened in December for January).
 */
export function monthOfThread(content: string, postedAtIso: string): string | null {
  const text = content.toLowerCase();
  if (!/nominat|recognition/.test(text)) return null;
  if (!text.includes("🧵") && !/nominations? for|nominations\b/.test(text)) return null;
  const idx = MONTHS.findIndex((m) => new RegExp(`\\b${m}\\b`).test(text));
  if (idx < 0) return null;
  const posted = new Date(postedAtIso);
  let year = posted.getUTCFullYear();
  if (idx < posted.getUTCMonth() - 6) year += 1;
  if (idx > posted.getUTCMonth() + 6) year -= 1;
  return `${year}-${String(idx + 1).padStart(2, "0")}`;
}

function awardOf(token: string): Award | null {
  const t = token.toLowerCase().replace(/[^a-z ]/g, "").trim();
  if (t === "mvp") return "MVP";
  if (t === "high five" || t === "highfive") return "High Five";
  return null;
}

function splitNames(raw: string): string[] {
  return raw
    .split(/\s*(?:\+|,|&|\band\b)\s*/i)
    .map((s) => s.replace(/^@/, "").replace(/[.!]+$/, "").trim())
    .filter(Boolean);
}

/**
 * Parse a results message. Accepts both directions:
 *   "1. Jacob → MVP" / "2. Dolce → High Five"
 *   "MVP → Linh" / "High Five → Kenya + Dolce"
 * Returns [] when the message is not a results line.
 */
export function parseResults(content: string): ResultLine[] {
  const out = new Map<Award, ResultLine>();
  const lines = content.split(/\n/);
  for (const rawLine of lines) {
    const mentions = mentionIds(rawLine);
    const line = stripMentions(rawLine).replace(/^\s*(?:\d+[.)]|[-*•])\s*/, "").trim();
    const parts = line.split(/\s*(?:→|->|—|:)\s*/);
    if (parts.length < 2) continue;
    let award = awardOf(parts[0]);
    let namesRaw = parts.slice(1).join(" ");
    if (!award) {
      award = awardOf(parts[parts.length - 1]);
      namesRaw = parts.slice(0, -1).join(" ");
    }
    if (!award) continue;
    const names = splitNames(namesRaw);
    if (!names.length) continue;
    const prev = out.get(award) ?? { award, names: [], mentionIds: [] };
    prev.names.push(...names);
    prev.mentionIds.push(...mentions.map((m) => m.id));
    out.set(award, prev);
  }
  return [...out.values()];
}

/**
 * Parse an inline announcement (e.g. the May 2026 weekly update) where awards
 * are bolded headings followed by mentions: "**MVP** (...) - [@Dolce]…" and
 * "**High Five** (...) [@z browning]… [@Myles]…". Mentions are attributed to the
 * most recent award heading seen before them.
 */
export function parseInlineAnnouncement(content: string): ResultLine[] {
  const out = new Map<Award, ResultLine>();
  const re = /\*\*(MVP|High Five)\*\*|\[@([^\]]+)\]\(#user_mention#(\d+)\)/gi;
  let current: Award | null = null;
  const section = content.split(/\\?\*\*\*?Reminders/i)[0];
  for (const m of section.matchAll(re)) {
    if (m[1]) {
      current = awardOf(m[1]);
      continue;
    }
    if (!current) continue;
    const prev = out.get(current) ?? { award: current, names: [], mentionIds: [] };
    if (!prev.mentionIds.includes(m[3])) {
      prev.names.push(m[2]);
      prev.mentionIds.push(m[3]);
    }
    out.set(current, prev);
  }
  return [...out.values()];
}

function splitUsername(username: string): { first: string; last: string } {
  const parts = username.trim().split(/\s+/);
  if (parts.length <= 1) return { first: parts[0] ?? "", last: "" };
  return { first: parts[0], last: parts.slice(1).join(" ") };
}

/** Resolve a results-line name to a ClickUp workspace member. */
export function matchMember(
  name: string,
  mentionId: string | null,
  members: Member[],
): MemberMatch {
  const fromMember = (m: Member, how: MatchHow): MemberMatch => ({
    name,
    userId: m.userId,
    ...splitUsername(m.username),
    email: m.email,
    how,
  });
  if (mentionId) {
    const m = members.find((x) => x.userId === mentionId);
    if (m) return fromMember(m, "mention");
  }
  const needle = name.trim().toLowerCase();
  const full = members.filter((m) => m.username.trim().toLowerCase() === needle);
  if (full.length === 1) return fromMember(full[0], "exact");
  const firsts = members.filter(
    (m) => splitUsername(m.username).first.toLowerCase() === needle,
  );
  if (firsts.length === 1) return fromMember(firsts[0], "exact");
  if (firsts.length > 1) {
    return { name, userId: null, first: name, last: "", email: null, how: "ambiguous" };
  }
  const prefix = members.filter((m) =>
    splitUsername(m.username).first.toLowerCase().startsWith(needle),
  );
  if (prefix.length === 1) return fromMember(prefix[0], "prefix");
  if (prefix.length > 1) {
    return { name, userId: null, first: name, last: "", email: null, how: "ambiguous" };
  }
  return { name, userId: null, first: name, last: "", email: null, how: "none" };
}

/**
 * Map a ClickUp first/last name to a payroll canonical name ("Last, First M").
 * "last-name" = only the last name matched (e.g. ClickUp "Linhchi Huynh" vs
 * payroll "Huynh, Hillary") — shown so the operator can confirm.
 */
export function matchRoster(
  first: string,
  last: string,
  roster: string[],
): { canonical: string | null; how: "exact" | "last-name" | "none" } {
  const f = first.trim().toLowerCase();
  const l = last.trim().toLowerCase();
  if (!f) return { canonical: null, how: "none" };
  const parsed = roster.map((c) => {
    const [lastPart, firstPart = ""] = c.split(",").map((s) => s.trim().toLowerCase());
    return { c, last: lastPart, first: firstPart.split(/\s+/)[0] ?? "" };
  });
  const exact = parsed.filter((p) => p.first === f && (!l || p.last === l));
  if (exact.length === 1) return { canonical: exact[0].c, how: "exact" };
  if (l) {
    const byLast = parsed.filter((p) => p.last === l);
    if (byLast.length === 1) return { canonical: byLast[0].c, how: "last-name" };
  }
  return { canonical: null, how: "none" };
}
