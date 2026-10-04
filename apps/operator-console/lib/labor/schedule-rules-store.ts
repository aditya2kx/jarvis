import "server-only";
import { fq, intParam, mutate, q } from "@/lib/bq/client";
import {
  DEFAULT_RULES,
  parseScheduleRules,
  type RulesVersion,
  type ScheduleRules,
} from "@/lib/labor/schedule-inputs";

type Row = { version: number; rules_json: string; note: string | null; created_by: string; created_at: string };

function toVersion(r: Row): RulesVersion {
  return {
    version: Number(r.version),
    rules: parseScheduleRules(r.rules_json),
    savedStaffing: JSON.parse(r.rules_json)?.staffing != null,
    note: r.note,
    createdBy: r.created_by,
    createdAt: String(r.created_at),
  };
}

/** Every saved version for the store, newest first (index 0 is live). */
export async function scheduleRulesHistory(store: string, limit = 50): Promise<RulesVersion[]> {
  const rows = await q<Row>(
    `SELECT version, rules_json, note, created_by, created_at
     FROM ${fq("labor_schedule_rules")}
     WHERE store = @store
     ORDER BY version DESC
     LIMIT @limit`,
    { store, limit: intParam(limit) },
  );
  return rows.map(toVersion);
}

/** Live rules: the newest version, or DEFAULT_RULES (version 0) before the first save. */
export async function currentScheduleRules(store: string): Promise<{ version: number; rules: ScheduleRules }> {
  const [latest] = await scheduleRulesHistory(store, 1);
  return latest ?? { version: 0, rules: DEFAULT_RULES };
}

/**
 * Append a new version. `baseVersion` is the version the operator edited; a
 * mismatch means someone saved in between, so refuse rather than silently
 * overwrite their change.
 */
export async function saveScheduleRules(
  store: string,
  rules: unknown,
  baseVersion: number,
  by: string,
  note?: string,
): Promise<number> {
  const parsed = parseScheduleRules(rules);
  const { version } = await currentScheduleRules(store);
  if (version !== baseVersion) {
    throw new Error(`Rules changed since you loaded them (now version ${version}). Reload and re-apply your edit.`);
  }
  const next = version + 1;
  await mutate(
    `INSERT INTO ${fq("labor_schedule_rules")} (store, version, rules_json, note, created_by, created_at)
     VALUES (@store, @version, @rules, @note, @by, CURRENT_TIMESTAMP())`,
    { store, version: intParam(next), rules: JSON.stringify(parsed), note: note ?? null, by },
    { note: "STRING" },
  );
  return next;
}
