/**
 * Accounting tables, and the preview sandbox that lets a PR preview exercise every
 * Accounting write without touching prod (operator-console-preview.yml copies these
 * tables to `<name>_<tag>` and sets PLAID_SANDBOX_SUFFIX=_<tag>).
 *
 * Prod never sets PLAID_SANDBOX_SUFFIX; on a preview it only takes effect with
 * CONSOLE_PREVIEW=1, and `assertPreviewWrite` refuses any preview write whose target is
 * not a sandbox table, so a missed call site fails loudly instead of writing prod.
 */
import { isConsolePreview } from "@/lib/config/preview";

export const PLAID_SANDBOX_TABLES = [
  "plaid_transactions",
  "plaid_category_rules",
  "plaid_taxonomy_nodes",
  "plaid_categorize_jobs",
  "plaid_category_rule_events",
] as const;
export type PlaidSandboxTable = (typeof PLAID_SANDBOX_TABLES)[number];

const SUFFIX_RE = /^_[a-z0-9_]{2,40}$/;

/** `_<tag>` when this is a preview with an Accounting sandbox, else null. */
export function plaidSandboxSuffix(): string | null {
  if (!isConsolePreview()) return null;
  const raw = process.env.PLAID_SANDBOX_SUFFIX?.trim() ?? "";
  return SUFFIX_RE.test(raw) ? raw : null;
}

/** Table name to read/write: the sandbox copy on a sandboxed preview, else prod. */
export function plaidTable(name: PlaidSandboxTable): string {
  return `${name}${plaidSandboxSuffix() ?? ""}`;
}

function project(): string {
  return process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
}

function dataset(): string {
  return process.env.BQ_DATASET ?? "bhaga";
}

/** Fully-qualified, backtick-quoted name (same shape as lib/bq/client `fq`). */
export function plaidFq(name: PlaidSandboxTable): string {
  return `\`${project()}.${dataset()}.${plaidTable(name)}\``;
}

const WRITE_TARGET_RE =
  /\b(?:INSERT\s+(?:INTO\s+)?|UPDATE\s+|MERGE\s+(?:INTO\s+)?|DELETE\s+(?:FROM\s+)?|TRUNCATE\s+TABLE\s+|CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?|ALTER\s+TABLE\s+|DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?)`([^`]+)`/gi;
const WRITE_VERB_RE = /\b(INSERT|UPDATE|MERGE|DELETE|TRUNCATE|CREATE|ALTER|DROP|CALL|EXECUTE)\b/i;

/** Every backticked table a statement writes (INSERT / UPDATE / MERGE / DELETE / DDL). */
export function writeTargets(sql: string): string[] {
  return [...sql.matchAll(WRITE_TARGET_RE)].map((m) => m[1]);
}

/**
 * On a preview, throw unless every table `sql` writes is this preview's own sandbox
 * copy (Accounting sandbox or the access-grants sandbox). No-op in prod.
 */
export function assertPreviewWrite(sql: string): void {
  if (!isConsolePreview()) return;
  const allowed = new Set<string>();
  const suffix = plaidSandboxSuffix();
  if (suffix) {
    for (const t of PLAID_SANDBOX_TABLES) allowed.add(`${project()}.${dataset()}.${t}${suffix}`);
  }
  const access = process.env.CONSOLE_ACCESS_TABLE?.trim();
  if (access && access !== "console_access_grants") {
    allowed.add(`${project()}.${dataset()}.${access}`);
  }
  const targets = writeTargets(sql);
  const blocked = targets.filter((t) => !allowed.has(t));
  if (blocked.length || (!targets.length && WRITE_VERB_RE.test(sql.replace(/--.*$/gm, "")))) {
    console.error(
      `preview_write_blocked targets=${blocked.join(",") || "(unparsed)"} sandbox=${suffix ?? "none"}`,
    );
    throw new Error(
      "Preview mode — this write targets prod data and was refused. Nothing was saved.",
    );
  }
}
