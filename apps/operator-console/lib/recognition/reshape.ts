// Pure guard for Gemini post rewrites (Issue #369): the rewrite must keep every
// ClickUp mention token from the original and be a single post.

const TOKEN_RE = /\[@[^\]]+\]\(#(?:user_mention#\d+|task_user_group_mention#followers_tag)\)/g;

export function mentionTokens(text: string): string[] {
  return [...new Set(text.match(TOKEN_RE) ?? [])];
}

export function acceptReshape(
  original: string,
  rewrite: string,
): { ok: true; text: string } | { ok: false; reason: string } {
  const text = rewrite.replace(/^```[a-z]*\n?|\n?```$/g, "").trim();
  if (text.length < 40) return { ok: false, reason: "empty reply" };
  if (/^\s*(option\s*\d|---\s*$)/im.test(text)) return { ok: false, reason: "returned multiple drafts" };
  const missing = mentionTokens(original).filter((t) => !text.includes(t));
  if (missing.length) return { ok: false, reason: `dropped ${missing.length} @mention(s)` };
  return { ok: true, text };
}

/** Split a post into text and mention segments for the rendered preview. */
export function previewSegments(text: string): { text: string; mention: boolean }[] {
  const out: { text: string; mention: boolean }[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN_RE)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index), mention: false });
    out.push({ text: `@${m[0].slice(2, m[0].indexOf("]"))}`, mention: true });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), mention: false });
  return out;
}
